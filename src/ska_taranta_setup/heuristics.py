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
    _ACRONYMS,
    AttributeInfo,
    CommandInfo,
    DeviceInterface,
    prettify,
    split_words,
)
from ska_taranta_setup.options import GenerateOptions

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
    "speed": "Speed", "velocity": "Speed",
    "direction": "Direction", "azimuth": "Direction", "bearing": "Direction",
    "rainfall": "Rainfall", "rain": "Rainfall", "precipitation": "Rainfall",
}  # fmt: skip
#: Units (lower-case) that identify a quantity, checked before the name.
UNIT_QUANTITIES: dict[str, str] = {
    "c": "Temperature", "°c": "Temperature", "degc": "Temperature",
    "deg c": "Temperature", "celsius": "Temperature", "k": "Temperature",
    "kelvin": "Temperature",
    "v": "Voltage", "mv": "Voltage",
    "a": "Current", "ma": "Current",
    "w": "Power", "mw": "Power", "dbm": "Power",
    "%": "Load",
    "hz": "Frequency", "khz": "Frequency", "mhz": "Frequency", "ghz": "Frequency",
    "s": "Timing", "ms": "Timing", "us": "Timing", "ns": "Timing", "ps": "Timing",
    "mbar": "Pressure", "hpa": "Pressure", "pa": "Pressure", "kpa": "Pressure",
    "bar": "Pressure",
    "ms-1": "Speed", "m/s": "Speed", "m.s-1": "Speed", "km/h": "Speed",
    "kmh-1": "Speed", "knots": "Speed",
    "deg": "Direction", "degrees": "Direction", "°": "Direction",
    "mm": "Rainfall", "mm.min-1": "Rainfall", "mm/h": "Rainfall", "mmh-1": "Rainfall",
}  # fmt: skip
#: Last name-words of raw signals behind a measurement (ADC counts, 4-20 mA
#: loop currents): diagnostics, shown as values but not dialled or plotted.
RAW_SUFFIXES = {"adc", "raw", "counts", "count"}
#: Quantities that read well on a dial (if we know its range).
DIAL_QUANTITIES = {
    "Temperature",
    "Voltage",
    "Current",
    "Power",
    "Load",
    "Fan",
    "Humidity",
    "Pressure",
    "Speed",
    "Direction",
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
#: Names of spectrum attributes that are an X axis for the others.
X_AXIS_NAMES = {
    "x", "xvalues", "xaxis", "frequencies", "frequency", "freqs", "freq",
    "frequencyaxis", "freqaxis", "timebase", "times", "timestamps",
}  # fmt: skip
MAX_CHART_LINES = 6

#: Attributes not shown at all (covered elsewhere or of no use on a dashboard).
HIDDEN_ATTRIBUTES = {"state", "loggingtargets"}
HIDDEN_COMMANDS = {"state", "status"}  # shown by the device status widget
#: Commands listed first in a Commands section.
FIRST_COMMANDS = ["init"]

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
    #: Colour theme: device, status, family, measurements, information,
    #: settings, commands, expert (see ``widgets.THEMES``).
    kind: str = "family"
    #: Numeric attributes worth a trend plot, keyed by quantity.
    trends: dict[str, list[tuple[AttributeInfo, str]]] = field(default_factory=dict)
    #: Dials, laid out in rows rather than one per line.
    dials: list[dict[str, Any]] = field(default_factory=list)
    #: Wide charts (spectra against frequency), shown in the Trends area.
    charts: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Whether there is nothing to show."""
        return not (self.widgets or self.trends or self.dials or self.charts)

    @property
    def has_box(self) -> bool:
        """Whether the section has anything for a box (charts go elsewhere)."""
        return bool(self.widgets or self.dials)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def is_raw_signal(attr: AttributeInfo) -> bool:
    """
    Whether an attribute is the raw signal behind a measurement.

    ``temperatureADC`` (counts) and ``windSpeedCurrent`` (a 4-20 mA loop) are
    diagnostics for ``temperature`` and ``windSpeed``, not measurements.
    """
    words = split_words(attr.name)
    if not words:
        return False
    if attr.unit.strip().lower() == "counts" or words[-1] in RAW_SUFFIXES:
        return True
    # "...Current" on another quantity is its loop current, not a current.
    return words[-1] == "current" and any(
        w in QUANTITIES and QUANTITIES[w] != "Current" for w in words[:-1]
    )


def quantity(attr: AttributeInfo) -> str | None:
    """The physical quantity an attribute measures, guessed from its name/unit."""
    if is_raw_signal(attr):
        return None
    words = split_words(attr.name)
    unit = attr.unit.strip().lower()
    if unit in UNIT_QUANTITIES:
        kind = UNIT_QUANTITIES[unit]
        # "percent" and "%" are humidity for a humidity attribute.
        if kind == "Load" and "humidity" in words:
            return "Humidity"
        return kind
    if unit == "percent":
        return "Humidity" if "humidity" in words else "Load"
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
    """
    A sensible dial range from limits, alarms or the kind of quantity.

    Real limits win; otherwise the alarm limits with 10% of their span either
    side (not going below zero for a quantity whose alarms are all positive).
    """
    if kind == "Direction":
        return (0.0, 360.0)
    if kind in ("Humidity", "Load") and _real_limit(attr.max_value) is None:
        return (0.0, 100.0)  # percentages
    low = _real_limit(attr.min_value)
    high = _real_limit(attr.max_value)
    lo_alarm, hi_alarm = attr.min_alarm, attr.max_alarm
    if hi_alarm is not None and lo_alarm is not None and hi_alarm > lo_alarm:
        span = hi_alarm - lo_alarm
        # Round to a step that suits the span (500-1100 mbar -> steps of 50).
        step = 10 ** math.floor(math.log10(span)) / 2
        if high is None:
            high = math.ceil((hi_alarm + 0.1 * span) / step) * step
        if low is None:
            low = math.floor((lo_alarm - 0.1 * span) / step) * step
            if lo_alarm >= 0:
                low = max(low, 0.0)
        high, low = float(round(high, 6)), float(round(low, 6))
    else:
        if high is None and hi_alarm is not None:
            high = _nice(hi_alarm * 1.2 if hi_alarm > 0 else hi_alarm, up=True)
        if low is None and lo_alarm is not None:
            low = min(0.0, _nice(lo_alarm * 1.2, up=False))
    if kind in ("Load", "Humidity") and high is None:
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
    if len(words[0]) == 1 and not (len(words) > 1 and words[1].isdigit()):
        # A one-letter prefix (fStartHz, bEnabled) isn't a family.
        return name.lower()
    for i, word in enumerate(words[1:3], start=1):
        if word.isdigit():
            return "_".join([*words[: i - 1], words[i - 1] + word])
    return words[0]


def family_title(key: str) -> str:
    """
    A section title for a family key.

    Short keys with no vowels (``pwsl``, ``tsrc1``, ``gntp``) or known acronyms
    are upper-cased; words like ``wind`` are capitalised.

    >>> family_title("pwsl"), family_title("tsrc1"), family_title("wind")
    ('PWSL', 'TSRC1', 'Wind')
    """
    alpha = re.sub(r"\d+", "", key)
    acronym = alpha in _ACRONYMS or not re.search(r"[aeiou]", alpha)
    return key.upper() if len(alpha) <= 4 and acronym else prettify(key)


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


def _enum_setter(device: str, attr: AttributeInfo, text: str) -> list[dict[str, Any]]:
    """Current value (the dropdown doesn't show it), then a dropdown to change it."""
    setter = w.dropdown_writer(device, attr, text, _enum_choices(attr))
    setter["inputs"]["showAttribute"] = "None"
    return [w.attribute_display(device, attr, text), setter]


def _forced(
    section: Section, device: str, attr: AttributeInfo, text: str, kind: str
) -> None:
    """Place an attribute as the widget kind configured for it."""
    if kind == "hide":
        return
    if kind == "led":
        if attr.is_bool:
            compare = "true"
        elif attr.name.lower() == "healthstate":
            compare = "0"
        else:
            compare = str(status_compare(attr) or 0)
        section.widgets.append(w.led(device, attr, text, compare=compare))
    elif kind == "dial":
        bounds = dial_range(attr, quantity(attr) or "Load") or (0.0, 100.0)
        section.dials.append(w.dial(device, attr, text, *bounds))
    elif kind == "plot":
        section.trends.setdefault(quantity(attr) or text, []).append((attr, text))
    elif kind == "writer":
        section.widgets.append(w.writer(device, attr, text))
    elif kind == "dropdown":
        section.widgets.extend(_enum_setter(device, attr, text))
    elif kind == "switch":
        section.widgets.append(w.boolean_display(device, attr, text))
    elif kind == "logger":
        section.widgets.append(w.logger(device, attr, text))
    elif kind == "spectrum":
        section.widgets.append(w.spectrum(device, attr, text))
    else:
        section.widgets.append(w.attribute_display(device, attr, text))


def place_attribute(
    section: Section,
    device: str,
    attr: AttributeInfo,
    family: str | None = None,
    options: GenerateOptions | None = None,
) -> None:
    """Add the widget(s) for one attribute to a section."""
    options = options or GenerateOptions()
    text = short_label(attr, family)
    name = attr.name.lower()

    forced = options.widget_override(attr.name)
    if forced is not None:
        _forced(section, device, attr, text, forced)
        return

    if name == "healthstate":
        section.widgets.append(w.led(device, attr, text, compare="0"))
        return
    if name == "healthinfo":
        section.widgets.append(w.logger(device, attr, text))
        return

    if attr.is_enum and attr.is_scalar:
        if attr.is_writable:
            section.widgets.extend(_enum_setter(device, attr, text))
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
        dialable = options.dials and kind in DIAL_QUANTITIES
        bounds = dial_range(attr, kind) if dialable else None
        if bounds is not None:
            section.dials.append(w.dial(device, attr, text, *bounds))
        else:
            section.widgets.append(w.attribute_display(device, attr, text))
        if kind is not None and options.plots:
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


def device_sections(
    device: str, interface: DeviceInterface, options: GenerateOptions | None = None
) -> list[Section]:
    """Group a device's attributes and commands into dashboard sections."""
    options = options or GenerateOptions()
    device_section = Section("Device", widgets=[w.device_status(device)], kind="device")
    expert = Section("Expert", kind="expert")
    class_name = interface.class_name
    attrs = [
        a
        for a in interface.attributes
        if a.name.lower() not in HIDDEN_ATTRIBUTES
        and not options.attribute_excluded(a.name, class_name)
        and (options.expert or not a.is_expert)
    ]

    def place(target: Section, attr: AttributeInfo, family: str | None = None) -> None:
        place_attribute(target, device, attr, family, options)

    # Spectra against an X axis (e.g. an RFI monitor's traces against
    # frequency) go on one chart instead of each against its index.
    spectrum = Section("Spectrum", kind="measurements")
    numeric_spectra = [a for a in attrs if a.is_spectrum and a.is_numeric]
    axis = next(
        (a for a in numeric_spectra if "".join(split_words(a.name)) in X_AXIS_NAMES),
        None,
    )
    if axis is not None and len(numeric_spectra) > 1:
        traces = [
            a
            for a in numeric_spectra
            if a is not axis and options.widget_override(a.name) != "hide"
        ][:MAX_CHART_LINES]
        if traces:
            chart = w.spectrum_2d(
                device, axis, traces, [short_label(a, None) for a in traces]
            )
            spectrum.charts.append((f"vs {prettify(axis.name)}", chart))
            charted = {axis.name.lower(), *(a.name.lower() for a in traces)}
            attrs = [a for a in attrs if a.name.lower() not in charted]

    by_name = {a.name.lower(): a for a in attrs}
    for name in DEVICE_SECTION_ORDER:
        if name in by_name:
            place(device_section, by_name.pop(name))
    remaining = [a for a in attrs if a.name.lower() in by_name]

    families: dict[str, list[AttributeInfo]] = {}
    for attr in remaining:
        families.setdefault(family_key(attr.name), []).append(attr)

    status = Section("Status", kind="status")
    family_sections: list[Section] = []
    measurements = Section("Measurements", kind="measurements")
    information = Section("Information", kind="information")
    settings = Section("Settings", kind="settings")

    for key, members in families.items():
        if len(members) >= MIN_FAMILY_SIZE:
            section = Section(family_title(key))
            for attr in members:
                place(expert if attr.is_expert else section, attr, key)
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
            place(target, attr)

    commands = Section("Commands", kind="commands")
    order = {name: i for i, name in enumerate(FIRST_COMMANDS)}
    ordered = sorted(
        interface.commands, key=lambda c: order.get(c.name.lower(), len(order))
    )
    for cmd in ordered:
        if cmd.name.lower() in HIDDEN_COMMANDS or options.command_excluded(
            cmd.name, class_name
        ):
            continue
        if _is_expert_command(cmd):
            if options.expert:
                expert.widgets.append(command_widget(device, cmd))
        else:
            commands.widgets.append(command_widget(device, cmd))

    sections = [
        device_section,
        status,
        *family_sections,
        measurements,
        spectrum,
        information,
        settings,
        commands,
        expert,
    ]
    return [s for s in sections if not s.is_empty()]


def _indicators(section: Section) -> int:
    leds = sum(1 for x in section.widgets if x["type"] == "LED_DISPLAY")
    return leds + len(section.dials)


def repeated_blocks(sections: list[Section]) -> set[str]:
    """Titles of sections that are one of 3+ indexed copies (Net WR0..WR15)."""
    stems: dict[str, int] = {}
    for s in sections:
        stem = re.sub(r"\d+$", "", s.title)
        stems[stem] = stems.get(stem, 0) + 1
    return {s.title for s in sections if stems[re.sub(r"\d+$", "", s.title)] > 2}


def summary_sections(sections: list[Section], count: int) -> list[Section]:
    """
    The sections to show for a device on a summary page.

    The "Device" section, then up to ``count`` others, preferring the most
    indicator-heavy (LEDs and dials). Repeated indexed blocks (``NET WR0`` ..
    ``NET WR15``) are left to the device's own page.
    """
    head, rest = sections[0], sections[1:]
    repeated = repeated_blocks(rest)
    candidates = [
        s
        for s in rest
        if s.title not in {"Commands", "Expert", "Information", "Settings"}
        and s.title not in repeated
        and _indicators(s) > 0
    ]
    chosen = sorted(candidates, key=lambda s: -_indicators(s))[:count]
    return [head, *(s for s in rest if s in chosen)]


def headline_attributes(
    interface: DeviceInterface, options: GenerateOptions | None = None
) -> list[AttributeInfo]:
    """Attributes summarising a device in status bars and overview tiles."""
    options = options or GenerateOptions()
    chosen = [
        a
        for name in options.status_attributes
        if (a := interface.attribute(name)) is not None
    ]
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
        and not options.attribute_excluded(a.name, interface.class_name)
        and status_compare(a) is not None
        and a not in chosen
    ]
    # Prefer top-level indicators (general_status) over ones inside a family.
    candidates.sort(key=lambda a: family_sizes[family_key(a.name)] >= MIN_FAMILY_SIZE)
    return chosen + candidates[: options.headline_status]


def status_widgets(
    device: str, interface: DeviceInterface, options: GenerateOptions | None = None
) -> list[dict[str, Any]]:
    """State plus headline LEDs, for a device's cell in a status bar."""
    section = Section("")
    section.widgets.append(w.device_status(device, show_name=False))
    for attr in headline_attributes(interface, options):
        place_attribute(section, device, attr, options=options)
    return section.widgets


def overview_widgets(
    device: str, interface: DeviceInterface, options: GenerateOptions | None = None
) -> list[dict[str, Any]]:
    """The handful of widgets that summarise a device on an overview tile."""
    options = options or GenerateOptions()
    section = Section("")
    section.widgets.append(w.device_status(device))
    for name in ("healthstate", "adminmode", "controlmode", "obsstate"):
        attr = interface.attribute(name)
        if attr is None or options.attribute_excluded(attr.name, interface.class_name):
            continue
        if name == "healthstate":
            place_attribute(section, device, attr, options=options)
        else:
            section.widgets.append(
                w.attribute_display(device, attr, attr.display_label)
            )
    shown = {"healthstate", "adminmode", "controlmode", "obsstate"}
    for attr in headline_attributes(interface, options):
        if attr.name.lower() not in shown:
            place_attribute(section, device, attr, options=options)
    return section.widgets


def trend_plots(
    section: Section, device: str, max_plots: int = MAX_PLOTS_PER_SECTION
) -> list[tuple[str, dict[str, Any]]]:
    """
    Plots for a section's trend-worthy attributes, one per quantity.

    Returns ``(quantity, plot)`` pairs, at most ``max_plots``; quantities with
    no dial come first, since a dial already shows the current value.
    """
    dialled = {d["inputs"]["attribute"]["attribute"] for d in section.dials}

    def on_dials(item: tuple[str, list[tuple[AttributeInfo, str]]]) -> bool:
        return all(a.name.lower() in dialled for a, _ in item[1])

    plots = []
    for kind, series in sorted(section.trends.items(), key=on_dials):
        for start in range(0, len(series), MAX_LINES_PER_PLOT):
            chunk = series[start : start + MAX_LINES_PER_PLOT]
            plot = w.plot(device, [a for a, _ in chunk], [label for _, label in chunk])
            plots.append((kind, plot))
    return plots[:max_plots]


@dataclass
class AttributePlan:
    """Where one attribute ends up on a device's dashboard, or why it doesn't."""

    name: str
    section: str = ""
    widgets: list[str] = field(default_factory=list)
    hidden: str = ""  # reason, if not shown
    order: int = 0  # position of its section on the page


WIDGET_NAMES = {
    "ATTRIBUTE_DISPLAY": "value",
    "LED_DISPLAY": "LED",
    "ATTRIBUTE WRITER DROPDOWN": "dropdown",
    "ATTRIBUTE_WRITER": "writer",
    "BOOLEAN_DISPLAY": "switch",
    "ATTRIBUTE_LOGGER": "log",
    "ATTRIBUTE_DIAL": "dial",
    "SPECTRUM": "spectrum",
}


def attribute_plan(
    interface: DeviceInterface, options: GenerateOptions | None = None
) -> list[AttributePlan]:
    """Every attribute of an interface: its section and widgets, or why hidden."""
    options = options or GenerateOptions()
    sections = device_sections("d", interface, options)
    plans: dict[str, AttributePlan] = {
        a.name.lower(): AttributePlan(a.name) for a in interface.attributes
    }

    def note(name: str, section: Section, kind: str) -> None:
        plan = plans.get(name)
        if plan is None:
            return
        if not plan.section:
            plan.section, plan.order = section.title, sections.index(section)
        if kind not in plan.widgets:
            plan.widgets.append(kind)

    for section in sections:
        for x in [*section.widgets, *section.dials]:
            ref = x["inputs"].get("attribute")
            if isinstance(ref, dict) and ref.get("attribute"):
                note(ref["attribute"], section, WIDGET_NAMES.get(x["type"], x["type"]))
        for series in section.trends.values():
            for attr, _ in series:
                note(attr.name.lower(), section, "plot")
        for _, chart in section.charts:
            x_name = chart["inputs"]["attributeX"]["attribute"]
            note(x_name, section, "chart X axis")
            for line in chart["inputs"]["attributes"]:
                note(line["attribute"]["attribute"], section, f"chart vs {x_name}")
    for key, plan in plans.items():
        if plan.widgets:
            continue
        attr = interface.attribute(key)
        if key == "state":
            plan.hidden = "built in (shown by the device status widget)"
        elif key in HIDDEN_ATTRIBUTES:
            plan.hidden = "built in (not useful on a dashboard)"
        elif options.attribute_excluded(plan.name):
            plan.hidden = "exclude_attributes"
        elif options.attribute_excluded(plan.name, interface.class_name):
            plan.hidden = f'exclude_attributes_by_class["{interface.class_name}"]'
        elif attr is not None and attr.is_expert and not options.expert:
            plan.hidden = "expert = false"
        elif options.widget_override(plan.name) == "hide":
            plan.hidden = "widgets: hide"
        else:
            plan.hidden = "not shown"
    return list(plans.values())
