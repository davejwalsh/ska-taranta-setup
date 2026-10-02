"""
Options that control what gets generated.

Read from ``pyproject.toml``::

    [tool.ska - taranta - setup.generate]  # GenerateOptions
    [tool.ska - taranta - setup.layout]  # dashboard.LayoutOptions
    [[tool.ska - taranta - setup.subsystems]]  # SubsystemSpec, repeatable

Unknown keys are an error, so a typo doesn't silently do nothing.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field, fields
from typing import Any

#: Widget kinds that ``GenerateOptions.widgets`` can force for an attribute.
WIDGET_KINDS = {
    "led",
    "display",
    "dial",
    "plot",
    "writer",
    "dropdown",
    "switch",
    "logger",
    "spectrum",
    "hide",
}
DETAIL_LEVELS = {"summary", "full"}


class OptionsError(ValueError):
    """Invalid generation options in pyproject.toml."""


def build(cls: type, data: dict[str, Any], where: str) -> Any:
    """Make dataclass ``cls`` from a TOML table, rejecting unknown keys."""
    names = {f.name for f in fields(cls)}
    values = {}
    for key, value in data.items():
        name = key.replace("-", "_")
        if name not in names:
            close = difflib.get_close_matches(name, names, n=1)
            hint = f" (did you mean {close[0]!r}?)" if close else ""
            raise OptionsError(f"[{where}]: unknown option {key!r}{hint}")
        values[name] = value
    return cls(**values)


@dataclass
class GenerateOptions:
    """What goes on the dashboards."""

    #: Write a detailed dashboard for every device.
    device_dashboards: bool = True
    #: ``"auto"``: group devices into subsystem pages using the device
    #: references in their properties (e.g. ``SubServerTrls``); ``"none"``:
    #: no subsystem pages. Ignored when ``[[subsystems]]`` are configured.
    subsystems: str = "auto"
    #: How much of each device a subsystem page shows: ``"summary"`` (status,
    #: controls and the most indicator-heavy sections) or ``"full"``.
    subsystem_detail: str = "summary"
    #: Sections per device on a summary subsystem page (besides "Device").
    summary_sections: int = 3
    #: Attributes shown for every device in the status bars, after State.
    status_attributes: list[str] = field(default_factory=lambda: ["healthState"])
    #: Extra status LEDs per device in status bars and overview tiles, picked
    #: automatically (e.g. ``general_status``).
    headline_status: int = 3
    #: Devices per row of a status bar.
    status_bar_columns: int = 6
    #: Show expert-level attributes and commands (in an "Expert" section).
    expert: bool = True
    #: Add trend plots for physical quantities.
    plots: bool = True
    max_plots_per_section: int = 2
    #: Use dials for bounded physical quantities.
    dials: bool = True
    #: Attributes / commands to leave out (case-insensitive regexes on name).
    exclude_attributes: list[str] = field(default_factory=list)
    exclude_commands: list[str] = field(default_factory=list)
    #: Force a widget kind for attributes: ``{"regex" = "kind"}``. Kinds:
    #: led, display, dial, plot, writer, dropdown, switch, logger, spectrum, hide.
    widgets: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate choices."""
        if self.subsystems not in {"auto", "none"}:
            raise OptionsError(
                f"generate.subsystems must be 'auto' or 'none', not {self.subsystems!r}"
            )
        if self.subsystem_detail not in DETAIL_LEVELS:
            raise OptionsError(
                "generate.subsystem_detail must be 'summary' or 'full', "
                f"not {self.subsystem_detail!r}"
            )
        for pattern, kind in self.widgets.items():
            if kind not in WIDGET_KINDS:
                raise OptionsError(
                    f"generate.widgets[{pattern!r}]: unknown kind {kind!r}; "
                    f"use one of {', '.join(sorted(WIDGET_KINDS))}"
                )
            re.compile(pattern)

    def widget_override(self, attribute: str) -> str | None:
        """The forced widget kind for an attribute, if any (first match wins)."""
        for pattern, kind in self.widgets.items():
            if re.fullmatch(pattern, attribute, re.IGNORECASE):
                return kind
        return None

    def attribute_excluded(self, attribute: str) -> bool:
        """Whether an attribute is left out."""
        return any(
            re.fullmatch(p, attribute, re.IGNORECASE) for p in self.exclude_attributes
        )

    def command_excluded(self, command: str) -> bool:
        """Whether a command is left out."""
        return any(
            re.fullmatch(p, command, re.IGNORECASE) for p in self.exclude_commands
        )


@dataclass
class SubsystemSpec:
    """A hand-defined subsystem page."""

    #: Page name, e.g. "UTC".
    name: str
    #: Device TRL regexes, in display order; the first device matched is the
    #: subsystem's root (shown first, and linked from the overview).
    devices: list[str] = field(default_factory=list)
    #: Override ``generate.subsystem_detail`` for this page.
    detail: str | None = None

    def __post_init__(self) -> None:
        """Validate choices."""
        if not self.devices:
            raise OptionsError(f"subsystem {self.name!r}: `devices` is empty")
        if self.detail is not None and self.detail not in DETAIL_LEVELS:
            raise OptionsError(
                f"subsystem {self.name!r}: detail must be 'summary' or 'full'"
            )
