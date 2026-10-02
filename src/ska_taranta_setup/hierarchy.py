"""
Group devices into subsystems.

Aggregating devices name the devices they look after in their properties
(``SubServerTrls``, ``SatUtcTrl``, ...), and those references are kept in the
discovery snapshot. They give a device tree, for example on ska-sat-lmc::

    low-sat/control/ci          SatUtcTrl -> low-sat/utc/ci
    low-sat/utc/ci              SubServerTrls -> low-sat/endnode/ci-1,
                                                 low-sat/grandmaster/1

Automatically, every device with children gets a subsystem page holding
itself and its children ("UTC" above), unless all its children already have
their own pages: such a device (the controller above) is an umbrella over
subsystems, and is shown at the top of the overview instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ska_taranta_setup.model import DeviceInstance, prettify
from ska_taranta_setup.options import GenerateOptions, SubsystemSpec


@dataclass
class Subsystem:
    """A set of devices shown together on one page."""

    name: str
    devices: list[DeviceInstance]
    detail: str = "summary"

    @property
    def root(self) -> DeviceInstance:
        """The device the subsystem is named after (shown first)."""
        return self.devices[0]


@dataclass
class Hierarchy:
    """How a project's devices are organised on the dashboards."""

    subsystems: list[Subsystem] = field(default_factory=list)
    #: Devices with children whose children all have their own subsystem.
    umbrellas: list[DeviceInstance] = field(default_factory=list)
    #: Devices in no subsystem.
    standalone: list[DeviceInstance] = field(default_factory=list)

    def subsystem_of(self, device: DeviceInstance) -> Subsystem | None:
        """The (first) subsystem a device belongs to."""
        for subsystem in self.subsystems:
            if any(d.trl == device.trl for d in subsystem.devices):
                return subsystem
        return None


def _trls(value: Any) -> list[str]:
    items = value if isinstance(value, (list, tuple)) else [value]
    return [
        str(v).lower()
        for v in items
        if isinstance(v, str) and v.count("/") == 2 and " " not in v and ":" not in v
    ]


def device_references(devices: list[DeviceInstance]) -> dict[str, list[str]]:
    """Map each device's TRL to the known devices its properties refer to."""
    known = {d.trl for d in devices}
    children: dict[str, list[str]] = {}
    for device in devices:
        refs: list[str] = []
        for value in device.properties.values():
            refs.extend(t for t in _trls(value) if t in known and t != device.trl)
        if refs:
            children[device.trl] = list(dict.fromkeys(refs))
    return children


def subsystem_name(device: DeviceInstance) -> str:
    """A page name from a device's TRL family, e.g. ``low-sat/utc/ci`` -> UTC."""
    return prettify(device.trl.split("/")[1])


def auto_hierarchy(devices: list[DeviceInstance], detail: str) -> Hierarchy:
    """Work out subsystems from device references."""
    by_trl = {d.trl: d for d in devices}
    children = device_references(devices)
    hierarchy = Hierarchy()
    for device in devices:
        kids = children.get(device.trl)
        if not kids:
            continue
        if all(children.get(k) for k in kids):
            hierarchy.umbrellas.append(device)
            continue
        hierarchy.subsystems.append(
            Subsystem(
                subsystem_name(device),
                [device, *(by_trl[k] for k in kids)],
                detail,
            )
        )
    placed = {d.trl for s in hierarchy.subsystems for d in s.devices}
    placed |= {d.trl for d in hierarchy.umbrellas}
    hierarchy.standalone = [d for d in devices if d.trl not in placed]
    return hierarchy


def configured_hierarchy(
    devices: list[DeviceInstance], specs: list[SubsystemSpec], detail: str
) -> Hierarchy:
    """Subsystems as listed in configuration."""
    hierarchy = Hierarchy()
    for spec in specs:
        members: list[DeviceInstance] = []
        for pattern in spec.devices:
            for device in devices:
                if re.fullmatch(pattern, device.trl) and device not in members:
                    members.append(device)
        if members:
            hierarchy.subsystems.append(
                Subsystem(spec.name, members, spec.detail or detail)
            )
    placed = {d.trl for s in hierarchy.subsystems for d in s.devices}
    hierarchy.standalone = [d for d in devices if d.trl not in placed]
    return hierarchy


def build_hierarchy(
    devices: list[DeviceInstance],
    options: GenerateOptions,
    specs: list[SubsystemSpec] | None = None,
) -> Hierarchy:
    """The hierarchy for these options: configured, automatic, or flat."""
    if specs is not None:
        return configured_hierarchy(devices, specs, options.subsystem_detail)
    if options.subsystems == "auto":
        return auto_hierarchy(devices, options.subsystem_detail)
    return Hierarchy(standalone=list(devices))
