"""
Best-guess choices of Taranta widget for each attribute and command.

The goal is a dashboard that's useful straight away and a sensible starting
point for hand-editing, not a perfect one. Decisions are made from the
attribute's type, format, writability, enum labels, limits and name:

=====================================  =====================================
Attribute                               Widget
=====================================  =====================================
``State``                               ``DEVICE_STATUS`` (LED + name)
``healthState``                         LED, green when OK
status-like enum (has ok/error labels)  LED, green on the "good" label
other read-only enum                    display with enum labels
writable enum (adminMode, ...)          dropdown writer with the labels
boolean                                 LED (red when true for faults)
writable boolean                        switch
bounded physical quantity (temp, V...)  dial, and a trend plot
other physical/timing number            display, and a trend plot
other number / string                   display
writable number / string                writer
numeric array                           spectrum plot
other array                             display (JSON)
``healthInfo``                          logger
=====================================  =====================================

Attributes are grouped into sections: an SKA-standard "Device" section, then
one section per name family of three or more (``pwsl_*``, ``tsrc1_*``, ...),
then generic sections for whatever is left.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from ska_taranta_setup import widgets as w
from ska_taranta_setup.model import (
    AttributeInfo,
    CommandInfo,
    DeviceInterface,
    prettify,
    split_words,
)

GOOD_LABELS = {
    "ok", "normal", "on", "online", "locked", "link_up", "up", "running", "good",
    "true", "synchronised", "synchronized", "synced", "valid", "pass", "nominal",
    "healthy", "ready", "connected",
}  # fmt: skip
BAD_LABELS = {
    "error", "critical", "fault", "fail", "failed", "failure", "warning", "down",
    "alarm", "invalid", "link_down", "not_found", "offline", "unlocked", "degraded",
    "unknown", "disconnected", "lost",
}  # fmt: skip
STATUS_WORDS = {"status", "state", "health", "lock", "locked", "alarm", "sync"}
FAULT_WORDS = {
    "fault",
    "error",
    "alarm",
    "fail",
    "failed",
    "overload",
    "lost",
    "unlock",
}

#: Name words that identify a physical quantity, and what it's called.
QUANTITIES: dict[str, str] = {
    "temp": "Temperature", "temperature": "Temperature",
    "vin": "Voltage", "vout": "Voltage", "volt": "Voltage", "voltage": "Voltage",
    "vcc": "Voltage",
    "current": "Current", "curr": "Current", "amps": "Current",
    "power": "Power", "pow": "Power",
    "offset": "Timing", "delay": "Timing", "asym": "Timing", "jitter": "Timing",
    "drift": "Timing", "skew": "Timing",
    "load": "Load", "usage": "Load", "util": "Load", "utilisation": "Load",
    "percent": "Load",
    "fan": "Fan", "rpm": "Fan",
    "humidity": "Humidity", "pressure": "Pressure",
    "freq": "Frequency", "frequency": "Frequency",
}  # fmt: skip
#: Quantities that read well on a dial (if we know its range).
DIAL_QUANTITIES = {
    "Temperature",
    "Voltage",
    "Current",
    "Power",
    "Load",
    "Fan",
    "Humidity",
}
#: Name words that mean "this is a counter or an identifier": never plotted.
COUNTER_WORDS = {
    "pkts", "packets", "count", "counter", "total", "bytes", "handled", "dropped",
    "ignored", "since", "uptime", "free", "rank", "class", "accuracy", "code",
    "stratum", "index", "id", "num", "number", "serial", "errors",
}  # fmt: skip

#: SKA base-class attributes, in the order they appear in the "Device" section.
DEVICE_SECTION_ORDER = [
    "healthstate",
    "status",
    "adminmode",
    "controlmode",
    "simulationmode",
    "testmode",
    "obsstate",
    "obsmode",
    "healthinfo",
    "versionid",
    "buildstate",
    "logginglevel",
]
#: Attributes not shown at all (covered elsewhere or of no use on a dashboard).
HIDDEN_ATTRIBUTES = {"state", "loggingtargets"}
HIDDEN_COMMANDS = {"init", "state", "status"}

MIN_FAMILY_SIZE = 3
MAX_LINES_PER_PLOT = 6
MAX_PLOTS_PER_SECTION = 2
INT32_LIMITS = (-(2**31), 2**31 - 1)
UINT32_MAX = 2**32 - 1


@dataclass
class Section:
    """A titled group of widgets, rendered as one BOX."""

    title: str
    widgets: list[dict[str, Any]] = field(default_factory=list)
    #: Numeric attributes worth a trend plot, keyed by quantity.
    trends: dict[str, list[tuple[AttributeInfo, str]]] = field(default_factory=dict)
    #: Dials, laid out in rows rather than one per line.
    dials: list[dict[str, Any]] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Whether there is nothing to show."""
        return not (self.widgets or self.trends or self.dials)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def quantity(attr: AttributeInfo) -> str | None:
    """The physical quantity an attribute measures, guessed from its name/unit."""
    unit = attr.unit.strip().lower()
    if unit in {"c", "°c", "degc", "deg c", "k"}:
        return "Temperature"
    if unit in {"v", "mv"}:
        return "Voltage"
    if unit in {"a", "ma"}:
        return "Current"
    if unit in {"w", "mw", "dbm"}:
        return "Power"
    if unit == "%":
        return "Load"
    if unit in {"hz", "khz", "mhz", "ghz"}:
        return "Frequency"
    if unit in {"s", "ms", "us", "ns", "ps"}:
        return "Timing"
    words = split_words(attr.name)
    if any(word in COUNTER_WORDS for word in words):
        return None
    for word in words:
        if word in QUANTITIES:
            return QUANTITIES[word]
    return None


def _real_limit(value: float | None) -> float | None:
    """Ignore 'limits' that are just the integer type's full range."""
    if value is None or value in INT32_LIMITS or value >= UINT32_MAX:
        return None
    return value


def _nice(value: float, up: bool) -> float:
    """Round to a 'nice' number for a dial end-stop."""
    if value == 0:
        return 0
    magnitude = 10 ** math.floor(math.log10(abs(value)))
    step = magnitude / 2
    rounded = (math.ceil if up else math.floor)(value / step) * step
    return float(round(rounded, 6))


def dial_range(attr: AttributeInfo, kind: str) -> tuple[float, float] | None:
    """A sensible dial range from limits, alarms or the kind of quantity."""
    low = _real_limit(attr.min_value)
    high = _real_limit(attr.max_value)
    if high is None and attr.max_alarm is not None:
        high = _nice(
            attr.max_alarm * 1.2 if attr.max_alarm > 0 else attr.max_alarm, up=True
        )
    if low is None and attr.min_alarm is not None:
        low = min(0.0, _nice(attr.min_alarm * 1.2, up=False))
    if kind == "Load" and high is None:
        low, high = 0.0, 100.0
    if high is None:
        return None
    if low is None:
        low = 0.0 if high > 0 else _nice(high * 2, up=False)
    return (low, high) if low < high else None


def status_compare(attr: AttributeInfo) -> int | None:
    """Index of the 'good' enum label, if this enum is a status indicator."""
    labels = [label.strip().lower() for label in attr.enum_labels]
    good = [i for i, label in enumerate(labels) if label in GOOD_LABELS]
    if not good:
        return None
    has_bad = any(label in BAD_LABELS for label in labels)
    named_like_status = bool(set(split_words(attr.name)) & STATUS_WORDS)
    return good[0] if (has_bad or named_like_status) else None


def family_key(name: str) -> str:
    """
    The 'family' an attribute name belongs to.

    That's its first word, or everything up to an index near the front, so
    that repeated blocks (ports, sources, channels) each get their own section.

    >>> (
    ...     family_key("pwsl_temp"),
    ...     family_key("tsrc1_name"),
    ...     family_key("gntpRxPkts"),
    ... )
    ('pwsl', 'tsrc1', 'gntp')
    >>> family_key("net_wr12_sfp_temp"), family_key("gpsChannelTrkStat3")
    ('net_wr12', 'gps')
    """
    words = split_words(name)
    if not words:
        return name
    for i, word in enumerate(words[1:3], start=1):
        if word.isdigit():
            return "_".join([*words[: i - 1], words[i - 1] + word])
    return words[0]


def family_title(key: str) -> str:
    """A section title for a family key."""
    alpha = re.sub(r"\d+", "", key)
    return key.upper() if len(alpha) <= 4 else prettify(key)


def short_label(attr: AttributeInfo, family: str | None) -> str:
    """The label shown next to a value; drops the family prefix inside a family."""
    if attr.label and attr.label.lower() not in {attr.name.lower(), ""}:
        return attr.label
    words = split_words(attr.name)
    if family:
        prefix = split_words(family)
        if words[: len(prefix)] == prefix and len(words) > len(prefix):
            return prettify("_".join(words[len(prefix) :]))
    return prettify(attr.name)


# --------------------------------------------------------------------------
# Attribute -> widget
# --------------------------------------------------------------------------


def _enum_choices(attr: AttributeInfo) -> list[tuple[str, str]]:
    return [
        (prettify(label) if label.isupper() or "_" in label else label, str(i))
        for i, label in enumerate(attr.enum_labels)
        if not label.startswith("_SNMPEnum_INVALID")
    ]


def place_attribute(
    section: Section, device: str, attr: AttributeInfo, family: str | None = None
) -> None:
    """Add the widget(s) for one attribute to a section."""
    text = short_label(attr, family)
    name = attr.name.lower()

    if name == "healthstate":
        section.widgets.append(w.led(device, attr, text, compare="0"))
        return
    if name == "healthinfo":
        section.widgets.append(w.logger(device, attr, text))
        return

    if attr.is_enum and attr.is_scalar:
        if attr.is_writable:
            section.widgets.append(
                w.dropdown_writer(device, attr, text, _enum_choices(attr))
            )
            return
        good = status_compare(attr)
        if good is not None:
            section.widgets.append(w.led(device, attr, text, compare=str(good)))
        else:
            section.widgets.append(w.attribute_display(device, attr, text))
        return

    if attr.is_bool and attr.is_scalar:
        if attr.is_writable:
            section.widgets.append(w.boolean_display(device, attr, text))
        elif set(split_words(attr.name)) & FAULT_WORDS:
            section.widgets.append(
                w.led(
                    device, attr, text, "true", true_colour=w.RED, false_colour=w.GREEN
                )
            )
        else:
            section.widgets.append(w.led(device, attr, text, "true"))
        return

    if attr.is_numeric and attr.is_scalar:
        if attr.is_writable:
            section.widgets.append(w.writer(device, attr, text))
            return
        kind = quantity(attr)
        bounds = dial_range(attr, kind) if kind in DIAL_QUANTITIES else None
        if bounds is not None:
            section.dials.append(w.dial(device, attr, text, *bounds))
        else:
            section.widgets.append(w.attribute_display(device, attr, text))
        if kind is not None:
            section.trends.setdefault(kind, []).append((attr, text))
        return

    if attr.is_spectrum and attr.is_numeric:
        section.widgets.append(w.spectrum(device, attr, text))
        return

    if attr.is_scalar and attr.is_writable:
        section.widgets.append(w.writer(device, attr, text))
        return

    # Strings, string/bool/enum arrays, images: show the value.
    section.widgets.append(w.attribute_display(device, attr, text))


def command_widget(device: str, cmd: CommandInfo) -> dict[str, Any]:
    """The widget for a command."""
    return w.command(device, cmd, prettify(cmd.name))


def _is_expert_command(cmd: CommandInfo) -> bool:
    return cmd.is_expert or "test" in cmd.doc_in.lower()


# --------------------------------------------------------------------------
# Device -> sections
# --------------------------------------------------------------------------


def device_sections(device: str, interface: DeviceInterface) -> list[Section]:
    """Group a device's attributes and commands into dashboard sections."""
    device_section = Section("Device", widgets=[w.device_status(device)])
    expert = Section("Expert")
    attrs = [a for a in interface.attributes if a.name.lower() not in HIDDEN_ATTRIBUTES]

    by_name = {a.name.lower(): a for a in attrs}
    for name in DEVICE_SECTION_ORDER:
        if name in by_name:
            place_attribute(device_section, device, by_name.pop(name))
    remaining = [a for a in attrs if a.name.lower() in by_name]

    families: dict[str, list[AttributeInfo]] = {}
    for attr in remaining:
        families.setdefault(family_key(attr.name), []).append(attr)

    status = Section("Status")
    family_sections: list[Section] = []
    measurements = Section("Measurements")
    information = Section("Information")
    settings = Section("Settings")

    for key, members in families.items():
        if len(members) >= MIN_FAMILY_SIZE:
            section = Section(family_title(key))
            for attr in members:
                place_attribute(
                    expert if attr.is_expert else section, device, attr, key
                )
            family_sections.append(section)
            continue
        for attr in members:
            if attr.is_expert:
                target = expert
            elif attr.is_writable:
                target = settings
            elif (attr.is_enum or attr.is_bool) and attr.is_scalar:
                target = status
            elif attr.is_numeric:
                target = measurements
            else:
                target = information
            place_attribute(target, device, attr)

    commands = Section("Commands")
    for cmd in interface.commands:
        if cmd.name.lower() in HIDDEN_COMMANDS:
            continue
        (expert if _is_expert_command(cmd) else commands).widgets.append(
            command_widget(device, cmd)
        )

    sections = [
        device_section,
        status,
        *family_sections,
        measurements,
        information,
        settings,
        commands,
        expert,
    ]
    return [s for s in sections if not s.is_empty()]


def overview_widgets(device: str, interface: DeviceInterface) -> list[dict[str, Any]]:
    """The handful of widgets that summarise a device on the overview page."""
    section = Section("")
    section.widgets.append(w.device_status(device))
    for name in ("healthstate", "adminmode", "controlmode", "obsstate"):
        attr = interface.attribute(name)
        if attr is None:
            continue
        if name == "healthstate":
            place_attribute(section, device, attr)
        else:
            section.widgets.append(
                w.attribute_display(device, attr, attr.display_label)
            )
    # A few headline status indicators, preferring top-level ones (not in a family).
    family_sizes: dict[str, int] = {}
    for attr in interface.attributes:
        key = family_key(attr.name)
        family_sizes[key] = family_sizes.get(key, 0) + 1
    candidates = [
        a
        for a in interface.attributes
        if a.is_scalar
        and a.is_enum
        and not a.is_writable
        and a.name.lower() not in DEVICE_SECTION_ORDER
        and status_compare(a) is not None
    ]
    candidates.sort(key=lambda a: family_sizes[family_key(a.name)] >= MIN_FAMILY_SIZE)
    for attr in candidates[:4]:
        place_attribute(section, device, attr)
    return section.widgets


def trend_plots(section: Section, device: str) -> list[dict[str, Any]]:
    """
    Plots for a section's trend-worthy attributes, one per quantity.

    At most ``MAX_PLOTS_PER_SECTION``; quantities with no dial come first,
    since a dial already shows the current value.
    """
    dialled = {d["inputs"]["attribute"]["attribute"] for d in section.dials}

    def on_dials(series: list[tuple[AttributeInfo, str]]) -> bool:
        return all(a.name.lower() in dialled for a, _ in series)

    plots = []
    for series in sorted(section.trends.values(), key=on_dials):
        for start in range(0, len(series), MAX_LINES_PER_PLOT):
            chunk = series[start : start + MAX_LINES_PER_PLOT]
            plots.append(
                w.plot(device, [a for a, _ in chunk], [label for _, label in chunk])
            )
    return plots[:MAX_PLOTS_PER_SECTION]
