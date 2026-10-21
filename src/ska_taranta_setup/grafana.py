"""
Grafana dashboards for an HDB++ archive (TimescaleDB).

Taranta only shows live values, so archived data is viewed in Grafana, which
queries the archive database directly. One dashboard is generated:

* **Explore**: pick devices and attributes (dashboard variables) and plot
  them together, with their latest values;
* **Archiver**: what's archived, when each attribute last changed, and errors;
* one section per device: states, enums and booleans as a state timeline,
  numbers plotted (grouped by unit), text and array events in a table.

HDB++ stores events, not samples, so every series also gets its last value
before the time range and is held up to the end of it: a value that hasn't
changed for a week still shows as a flat line.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from itertools import count
from pathlib import Path

from ska_taranta_setup.archive import ArchivedAttribute, ArchiveSnapshot
from ska_taranta_setup.model import prettify
from ska_taranta_setup.options import ArchiverOptions

DATASOURCE_TYPE = "grafana-postgresql-datasource"
DATASOURCE = {"type": DATASOURCE_TYPE, "uid": "${ds}"}
GRID_WIDTH = 24

#: Tango DevState values and colours.
STATES = [
    ("ON", "green"), ("OFF", "#8e8e8e"), ("CLOSE", "#d8d9da"), ("OPEN", "green"),
    ("INSERT", "#d8d9da"), ("EXTRACT", "green"), ("MOVING", "light-blue"),
    ("STANDBY", "yellow"), ("FAULT", "red"), ("INIT", "#c9b38a"),
    ("RUNNING", "dark-green"), ("ALARM", "orange"), ("DISABLE", "purple"),
    ("UNKNOWN", "#5a5a5a"),
]  # fmt: skip
#: Enum label colours, by the first rule that matches.
LABEL_COLOURS = [
    (r"OK|ON|NORMAL|ONLINE|AUTO|ENABLED?|RUNNING|TRUE|READY|IDLE", "green"),
    (r".*(FAIL|FAULT|ERROR|ALARM|TRIP|ABORT).*", "red"),
    (r".*(DEGRADED|WARN|MAINT).*", "orange"),
    (r"UNKNOWN|NOT_FITTED|NONE", "#5a5a5a"),
    (r"OFF|OFFLINE|DISABLED?|STOPPED|FALSE", "#8e8e8e"),
]
PALETTE = ["blue", "purple", "light-blue", "yellow", "super-light-purple", "#c9b38a"]
#: A boolean that is bad when true.
BAD_WHEN_TRUE = re.compile(r"(?i).*(alarm|fault|error|fail|trip|warn|lost).*")

#: Tango unit -> (Grafana unit, quantity shown as the plot title).
UNITS = {
    "degc": ("celsius", "Temperature"), "°c": ("celsius", "Temperature"),
    "c": ("celsius", "Temperature"), "k": ("kelvin", "Temperature"),
    "%": ("percent", "Percentage"), "v": ("volt", "Voltage"),
    "mv": ("mvolt", "Voltage"), "a": ("amp", "Current"), "ma": ("mamp", "Current"),
    "w": ("watt", "Power"), "kw": ("kwatt", "Power"), "hz": ("hertz", "Frequency"),
    "mhz": ("suffix: MHz", "Frequency"), "pa": ("pressurepa", "Pressure"),
    "kpa": ("pressurekpa", "Pressure"), "bar": ("pressurebar", "Pressure"),
    "mbar": ("pressurembar", "Pressure"), "hpa": ("pressurehpa", "Pressure"),
    "m/s": ("velocityms", "Speed"), "km/h": ("velocitykmh", "Speed"),
    "s": ("s", "Time"), "ms": ("ms", "Time"), "us": ("µs", "Time"),
    "dbm": ("dBm", "Level"), "db": ("dB", "Level"), "rpm": ("rotrpm", "Speed"),
    "deg": ("degree", "Angle"), "m": ("lengthm", "Length"),
    "l/s": ("flowlps", "Flow"), "m3/h": ("flowcms", "Flow"),
    "b": ("bytes", "Size"), "bytes": ("bytes", "Size"),
}  # fmt: skip


def _q(text: str) -> str:
    """A SQL string literal."""
    return "'" + text.replace("'", "''") + "'"


def _in(values: Iterable[str]) -> str:
    return "(" + ", ".join(_q(v) for v in values) + ")"


def _device_filter(device: str) -> str:
    domain, family, member = device.split("/")
    return (
        f"c.domain = {_q(domain)} AND c.family = {_q(family)} "
        f"AND c.member = {_q(member)}"
    )


def _value(attr_table: str) -> str:
    if attr_table.endswith(("_devboolean", "_devstate", "_devenum")):
        return "d.value_r::int"
    return "d.value_r::double precision"


def series_sql(filters: dict[str, str], metric: str) -> str:
    """
    One time series per attribute, for Grafana's time-series format.

    ``filters`` maps each data table to the ``att_conf`` (``c``) condition
    picking attributes from it. Each series also gets its last value before
    the range (at the range start) and its latest value held to the end.
    """
    parts = []
    for table, where in filters.items():
        value = _value(table)
        source = (
            f"FROM {table} d JOIN att_conf c ON c.att_conf_id = d.att_conf_id "
            f"WHERE {where}"
        )
        parts.append(
            f"SELECT d.data_time AS time, {metric} AS metric, {value} AS value "
            f"{source} AND $__timeFilter(d.data_time)"
        )
        for at, bound in (
            ("$__timeFrom()::timestamptz", "d.data_time < $__timeFrom()"),
            ("least($__timeTo()::timestamptz, now())", "d.data_time <= $__timeTo()"),
        ):
            parts.append(
                f"(SELECT DISTINCT ON (c.att_conf_id) {at} AS time, "
                f"{metric} AS metric, {value} AS value {source} AND {bound} "
                "ORDER BY c.att_conf_id, d.data_time DESC)"
            )
    return "SELECT * FROM (\n  " + "\n  UNION ALL ".join(parts) + "\n) s ORDER BY 1"


def events_sql(filters: dict[str, str], metric: str, limit: int = 500) -> str:
    """Text and array events, newest first, with any archiving error."""
    parts = []
    for table, where in filters.items():
        text = (
            "array_to_string(d.value_r, ', ')"
            if table.startswith("att_array_")
            else "d.value_r::text"
        )
        parts.append(
            f"SELECT d.data_time AS time, {metric} AS attribute, {text} AS value, "
            f"{QUALITY} AS quality, coalesce(e.error_desc, '') AS error "
            f"FROM {table} d JOIN att_conf c ON c.att_conf_id = d.att_conf_id "
            "LEFT JOIN att_error_desc e ON e.att_error_desc_id = d.att_error_desc_id "
            f"WHERE {where} AND $__timeFilter(d.data_time)"
        )
    return (
        "SELECT * FROM (\n  "
        + "\n  UNION ALL ".join(parts)
        + f"\n) s ORDER BY 1 DESC LIMIT {limit}"
    )


def _metric_case(attrs: list[ArchivedAttribute], with_device: bool) -> str:
    """SQL naming each attribute's series (by its real, camelCase name)."""
    whens = " ".join(
        f"WHEN {_q(a.trl)} THEN {_q(_series_name(a, with_device))}" for a in attrs
    )
    key = "c.domain || '/' || c.family || '/' || c.member || '/' || c.name"
    return f"CASE {key} {whens} ELSE {key} END"


def _series_name(attr: ArchivedAttribute, with_device: bool) -> str:
    return f"{attr.device}/{attr.display}" if with_device else attr.display


def _by_table(attrs: Iterable[ArchivedAttribute]) -> dict[str, list[str]]:
    tables: dict[str, list[str]] = {}
    for attr in attrs:
        tables.setdefault(attr.table, []).append(attr.name)
    return tables


def _attr_filters(device: str, attrs: list[ArchivedAttribute]) -> dict[str, str]:
    return {
        table: f"{_device_filter(device)} AND c.name IN {_in(names)}"
        for table, names in _by_table(attrs).items()
    }


# --------------------------------------------------------------------------
# Field config
# --------------------------------------------------------------------------


def _label_colour(label: str, index: int) -> str:
    for pattern, colour in LABEL_COLOURS:
        if re.fullmatch(pattern, label, re.IGNORECASE):
            return colour
    return PALETTE[index % len(PALETTE)]


def value_mappings(attr: ArchivedAttribute) -> list[dict]:
    """Grafana value mappings: numbers to state/enum/boolean names."""
    if attr.dtype == "DevState":
        options = {
            str(i): {"text": name, "color": colour, "index": i}
            for i, (name, colour) in enumerate(STATES)
        }
    elif attr.dtype == "DevBoolean":
        bad = bool(BAD_WHEN_TRUE.fullmatch(attr.name))
        options = {
            "0": {"text": "false", "color": "green" if bad else "#8e8e8e", "index": 0},
            "1": {"text": "true", "color": "red" if bad else "green", "index": 1},
        }
    elif attr.enum_labels:
        options = {
            str(i): {"text": label, "color": _label_colour(label, i), "index": i}
            for i, label in enumerate(attr.enum_labels)
        }
    else:
        return []
    return [{"type": "value", "options": options}]


def _overrides(attrs: list[ArchivedAttribute], with_device: bool) -> list[dict]:
    overrides = []
    for attr in attrs:
        mappings = value_mappings(attr)
        if mappings:
            overrides.append(
                {
                    "matcher": {
                        "id": "byName",
                        "options": _series_name(attr, with_device),
                    },
                    "properties": [{"id": "mappings", "value": mappings}],
                }
            )
    return overrides


def grafana_unit(unit: str) -> tuple[str, str]:
    """(Grafana unit, quantity) for a Tango unit."""
    if not unit:
        return "short", "Values"
    return UNITS.get(unit.strip().lower(), (f"suffix: {unit}", unit))


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------


class _Layout:
    """Hands out panel ids and places panels on Grafana's 24-wide grid."""

    def __init__(self) -> None:
        self.ids = count(1)
        self.y = 0
        self.x = 0
        self.row_height = 0

    def place(self, width: int, height: int) -> dict:
        if self.x + width > GRID_WIDTH:
            self.newline()
        pos = {"h": height, "w": width, "x": self.x, "y": self.y}
        self.x += width
        self.row_height = max(self.row_height, height)
        if self.x >= GRID_WIDTH:
            self.newline()
        return pos

    def newline(self) -> None:
        if self.x:
            self.y += self.row_height
        self.x = 0
        self.row_height = 0


def _target(sql: str, fmt: str) -> dict:
    return {
        "refId": "A",
        "datasource": DATASOURCE,
        "editorMode": "code",
        "format": fmt,
        "rawQuery": True,
        "rawSql": sql,
    }


def _panel(layout: _Layout, kind: str, title: str, width: int, height: int) -> dict:
    return {
        "id": next(layout.ids),
        "type": kind,
        "title": title,
        "datasource": DATASOURCE,
        "gridPos": layout.place(width, height),
    }


def timeseries(
    layout: _Layout, title: str, sql: str, unit: str, width: int, height: int
) -> dict:
    """A line plot of event data (stepped: values hold until the next event)."""
    panel = _panel(layout, "timeseries", title, width, height)
    panel["targets"] = [_target(sql, "time_series")]
    panel["fieldConfig"] = {
        "defaults": {
            "unit": unit,
            "custom": {
                "lineInterpolation": "stepAfter",
                "spanNulls": True,
                "showPoints": "never",
                "lineWidth": 2,
                "fillOpacity": 0,
            },
        },
        "overrides": [],
    }
    panel["options"] = {
        "legend": {
            "displayMode": "table",
            "placement": "bottom",
            "calcs": ["lastNotNull", "min", "max"],
        },
        "tooltip": {"mode": "multi", "sort": "none"},
    }
    return panel


def state_timeline(
    layout: _Layout,
    title: str,
    sql: str,
    overrides: list[dict],
    lanes: int,
    width: int = GRID_WIDTH,
) -> dict:
    """States, enums and booleans as coloured bands, one lane each."""
    panel = _panel(layout, "state-timeline", title, width, 3 + lanes)
    panel["targets"] = [_target(sql, "time_series")]
    panel["fieldConfig"] = {
        # spanNulls: a lane only has values when its attribute changed; the
        # nulls between are other lanes' events, not gaps. (No colour mode:
        # "thresholds" would replace the value mappings with "< 80".)
        "defaults": {"custom": {"fillOpacity": 80, "spanNulls": True}},
        "overrides": overrides,
    }
    panel["options"] = {
        "mergeValues": True,
        "showValue": "auto",
        "alignValue": "left",
        "rowHeight": 0.85,
        "legend": {"showLegend": False},
        "tooltip": {"mode": "single"},
    }
    return panel


def table(layout: _Layout, title: str, sql: str, width: int, height: int) -> dict:
    """A table of query results."""
    panel = _panel(layout, "table", title, width, height)
    panel["targets"] = [_target(sql, "table")]
    panel["options"] = {"showHeader": True, "cellHeight": "sm"}
    panel["fieldConfig"] = {"defaults": {}, "overrides": []}
    return panel


def stat(layout: _Layout, title: str, sql: str, width: int, colour: str) -> dict:
    """A single number."""
    panel = _panel(layout, "stat", title, width, 4)
    panel["targets"] = [_target(sql, "table")]
    panel["fieldConfig"] = {
        "defaults": {"color": {"mode": "fixed", "fixedColor": colour}},
        "overrides": [],
    }
    panel["options"] = {"colorMode": "background", "graphMode": "none"}
    return panel


def row(layout: _Layout, title: str, collapsed: bool) -> dict:
    """A section header; when collapsed, it holds its panels."""
    layout.newline()
    panel = {
        "id": next(layout.ids),
        "type": "row",
        "title": title,
        "collapsed": collapsed,
        "gridPos": {"h": 1, "w": GRID_WIDTH, "x": 0, "y": layout.y},
        "panels": [],
    }
    layout.y += 1
    return panel


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------

DEVICE_KEY = "c.domain || '/' || c.family || '/' || c.member"
#: Tango AttrQuality names, from the stored number.
QUALITY = "(ARRAY['VALID', 'INVALID', 'ALARM', 'CHANGING', 'WARNING'])[d.quality + 1]"
SELECTED = f"{DEVICE_KEY} IN ($device) AND c.name IN ($attribute)"


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)] or [[]]


def explore_section(
    layout: _Layout, attrs: list[ArchivedAttribute], opts: ArchiverOptions
) -> list[dict]:
    """Plots of whatever is picked in the Device and Attribute variables."""
    panels = []
    metric = _metric_case(attrs, with_device=True)
    numeric = sorted({a.table for a in attrs if a.is_numeric})
    discrete = [a for a in attrs if a.is_discrete]
    if numeric:
        sql = series_sql({t: SELECTED for t in numeric}, metric)
        panels.append(
            timeseries(layout, "Selected attributes", sql, "short", GRID_WIDTH, 10)
        )
    if discrete:
        sql = series_sql(
            {t: SELECTED for t in sorted({a.table for a in discrete})}, metric
        )
        panels.append(
            state_timeline(
                layout, "Selected states", sql, _overrides(discrete, True), 6
            )
        )
    latest = []
    name = _metric_case(attrs, with_device=False)
    for table_name in sorted({a.table for a in attrs}):
        text = (
            "array_to_string(d.value_r, ', ')"
            if table_name.startswith("att_array_")
            else "d.value_r::text"
        )
        latest.append(
            f"(SELECT DISTINCT ON (c.att_conf_id) {DEVICE_KEY} AS device, "
            f"{name} AS attribute, {text} AS value, {QUALITY} AS quality, "
            "coalesce(e.error_desc, '') AS error, d.data_time AS since "
            f"FROM {table_name} d JOIN att_conf c ON c.att_conf_id = d.att_conf_id "
            "LEFT JOIN att_error_desc e ON e.att_error_desc_id = d.att_error_desc_id "
            f"WHERE {SELECTED} AND d.data_time <= $__timeTo() "
            "ORDER BY c.att_conf_id, d.data_time DESC)"
        )
    sql = "SELECT * FROM (\n  " + "\n  UNION ALL ".join(latest) + "\n) s ORDER BY 1, 2"
    panels.append(table(layout, "Latest values", sql, GRID_WIDTH, opts.panel_height))
    return panels


def archiver_section(
    layout: _Layout, attrs: list[ArchivedAttribute], opts: ArchiverOptions
) -> list[dict]:
    """What's archived: counts, and when each attribute last changed."""
    tables = sorted({a.table for a in attrs})
    where = f"NOT coalesce(c.hide, false) AND {DEVICE_KEY} IN ($device)"
    per_attribute = []
    for table_name in tables:
        per_attribute.append(
            f"SELECT {DEVICE_KEY} AS device, c.name AS attribute, "
            f"{_q(table_name.removeprefix('att_'))} AS type, "
            'last.data_time AS "last event", stats.events, stats.errors '
            "FROM att_conf c "
            f"LEFT JOIN LATERAL (SELECT data_time FROM {table_name} d "
            "WHERE d.att_conf_id = c.att_conf_id "
            "ORDER BY data_time DESC LIMIT 1) last ON true "
            "LEFT JOIN LATERAL (SELECT count(*) AS events, "
            "count(att_error_desc_id) AS errors "
            f"FROM {table_name} d WHERE d.att_conf_id = c.att_conf_id "
            "AND $__timeFilter(d.data_time)) stats ON true "
            f"WHERE {where} AND c.table_name = {_q(table_name)}"
        )
    union = "SELECT * FROM (\n  " + "\n  UNION ALL ".join(per_attribute) + "\n) s"
    width = GRID_WIDTH // 4
    return [
        stat(
            layout,
            "Archived attributes",
            f"SELECT count(*) FROM att_conf c WHERE {where}",
            width,
            "blue",
        ),
        stat(
            layout,
            "Events in range",
            f"SELECT sum(events) FROM ({union}) t",
            width,
            "green",
        ),
        stat(
            layout,
            "Silent in range",
            f"SELECT count(*) FROM ({union}) t WHERE coalesce(events, 0) = 0",
            width,
            "#8e8e8e",
        ),
        stat(
            layout,
            "Errors in range",
            f"SELECT sum(errors) FROM ({union}) t",
            width,
            "red",
        ),
        table(
            layout,
            "Archived attributes (least recently changed first)",
            union + ' ORDER BY "last event" ASC NULLS FIRST',
            GRID_WIDTH,
            10,
        ),
    ]


def device_section(
    layout: _Layout,
    device: str,
    attrs: list[ArchivedAttribute],
    opts: ArchiverOptions,
) -> list[dict]:
    """A device's archived attributes: states, plots and events."""
    panels = []
    discrete = [a for a in attrs if a.is_discrete]
    # Device state first, then health, then the rest in name order.
    discrete.sort(key=lambda a: (a.dtype != "DevState", "health" not in a.name, a.name))
    metric = _metric_case(attrs, with_device=False)
    for i, chunk in enumerate(_chunks(discrete, 12) if discrete else []):
        title = "States" if i == 0 else "States (continued)"
        panels.append(
            state_timeline(
                layout,
                title,
                series_sql(_attr_filters(device, chunk), metric),
                _overrides(chunk, False),
                len(chunk),
            )
        )

    numeric = [a for a in attrs if a.is_numeric]
    groups: dict[str, list[ArchivedAttribute]] = {}
    for attr in numeric:
        key = attr.unit if opts.group_by == "unit" else attr.name
        groups.setdefault(key, []).append(attr)
    width = GRID_WIDTH // opts.columns
    for key in sorted(groups, key=lambda k: (k == "", k.lower())):
        members = groups[key]
        unit, quantity = grafana_unit(members[0].unit)
        for i, chunk in enumerate(_chunks(members, opts.max_series_per_panel)):
            if len(chunk) == 1:
                title = prettify(chunk[0].display)
            else:
                title = quantity + (f" ({i + 1})" if len(members) > len(chunk) else "")
            if members[0].unit:
                title += f" [{members[0].unit}]"
            panels.append(
                timeseries(
                    layout,
                    title,
                    series_sql(_attr_filters(device, chunk), metric),
                    unit,
                    width,
                    opts.panel_height,
                )
            )

    other = [a for a in attrs if not a.is_numeric and not a.is_discrete]
    if other:
        layout.newline()
        panels.append(
            table(
                layout,
                "Text and array events (newest first)",
                events_sql(_attr_filters(device, other), metric),
                GRID_WIDTH,
                opts.panel_height,
            )
        )
    return panels


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


def _variables(attrs: list[ArchivedAttribute]) -> list[dict]:
    devices_sql = (
        f"SELECT DISTINCT {DEVICE_KEY} FROM att_conf c "
        "WHERE NOT coalesce(c.hide, false) ORDER BY 1"
    )
    attributes_sql = (
        f"SELECT DISTINCT c.name FROM att_conf c WHERE {DEVICE_KEY} IN ($device) "
        "AND NOT coalesce(c.hide, false) ORDER BY 1"
    )
    every = {"text": ["All"], "value": ["$__all"]}
    return [
        {
            "type": "datasource",
            "name": "ds",
            "label": "Archive",
            "query": DATASOURCE_TYPE,
            "current": {},
            "hide": 0,
        },
        {
            "type": "query",
            "name": "device",
            "label": "Device",
            "datasource": DATASOURCE,
            "query": devices_sql,
            "definition": devices_sql,
            "refresh": 1,
            "multi": True,
            "includeAll": True,
            "current": every,
            "sort": 1,
        },
        {
            "type": "query",
            "name": "attribute",
            "label": "Attribute",
            "datasource": DATASOURCE,
            "query": attributes_sql,
            "definition": attributes_sql,
            "refresh": 1,
            "multi": True,
            "includeAll": True,
            "current": every,
            "sort": 1,
        },
    ]


def dashboard_uid(title: str) -> str:
    """A stable uid (Grafana allows 40 characters)."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]


def build_dashboard(
    snapshot: ArchiveSnapshot, title: str, opts: ArchiverOptions
) -> dict:
    """The Grafana dashboard JSON for everything in ``snapshot``."""
    attrs = [a for a in snapshot.attributes if not opts.excluded(a.device, a.name)]
    layout = _Layout()
    panels: list[dict] = []

    for name, build in (("Explore", explore_section), ("Archiver", archiver_section)):
        header = row(layout, name, False)
        panels.append(header)
        panels.extend(build(layout, attrs, opts))
    by_device: dict[str, list[ArchivedAttribute]] = {}
    for attr in attrs:
        by_device.setdefault(attr.device, []).append(attr)
    collapsed = opts.collapse_devices and len(by_device) > 1
    for device in sorted(by_device):
        header = row(layout, device, collapsed)
        children = device_section(layout, device, by_device[device], opts)
        panels.append(header)
        if collapsed:
            header["panels"] = children
        else:
            panels.extend(children)
    return {
        "uid": dashboard_uid(title),
        "title": title,
        "tags": ["hdbpp", "tango", "archive"],
        "timezone": "browser",
        "editable": True,
        "graphTooltip": 1,  # shared crosshair
        "schemaVersion": 39,
        "time": {"from": opts.time_from, "to": "now"},
        "refresh": opts.refresh,
        "templating": {"list": _variables(attrs)},
        "panels": panels,
        "description": f"Generated by ska-taranta-setup from {snapshot.source}.",
    }


def write_dashboard(dashboard: dict, path: Path) -> Path:
    """Write the dashboard JSON (stable output, for diff-friendly commits)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n")
    return path


class GrafanaError(RuntimeError):
    """Grafana refused the dashboard."""


def upload_dashboard(
    url: str, dashboard: dict, user: str, password: str, session=None
) -> str:
    """Create or replace the dashboard in a running Grafana; return its URL."""
    import requests

    session = session or requests.Session()
    base = url.rstrip("/")
    try:
        response = session.post(
            f"{base}/api/dashboards/db",
            json={
                "dashboard": {**dashboard, "id": None},
                "overwrite": True,
                "message": "ska-taranta archiver-dashboard",
            },
            auth=(user, password) if user else None,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise GrafanaError(f"Can't reach Grafana at {base}: {exc}") from exc
    if response.status_code != 200:
        raise GrafanaError(
            f"Grafana answered {response.status_code}: {response.text[:300]}"
        )
    return base + response.json().get("url", "")
