"""
Data model for discovered Tango devices and their interfaces.

These are deliberately plain, JSON-serialisable dataclasses so that a
discovery run can be cached to disk (``taranta/devices.json``), committed,
reviewed and edited, and then fed to the dashboard generator without needing
pytango or a running device.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

NUMERIC_TYPES = {
    "DevShort",
    "DevLong",
    "DevLong64",
    "DevFloat",
    "DevDouble",
    "DevUShort",
    "DevULong",
    "DevULong64",
    "DevUChar",
}
FLOAT_TYPES = {"DevFloat", "DevDouble"}
SNAPSHOT_VERSION = 1


def _clean_limit(value: Any) -> float | None:
    """Convert a Tango limit string ("Not specified", "1.5") to a float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class AttributeInfo:
    """Metadata for a single Tango attribute."""

    name: str
    dtype: str = "DevString"
    dformat: str = "SCALAR"
    writable: str = "READ"
    label: str = ""
    unit: str = ""
    description: str = ""
    disp_level: str = "OPERATOR"
    enum_labels: list[str] = field(default_factory=list)
    min_value: float | None = None
    max_value: float | None = None
    min_alarm: float | None = None
    max_alarm: float | None = None
    max_dim_x: int = 0

    @property
    def is_numeric(self) -> bool:
        """Whether the attribute holds a number."""
        return self.dtype in NUMERIC_TYPES

    @property
    def is_float(self) -> bool:
        """Whether the attribute holds a floating-point number."""
        return self.dtype in FLOAT_TYPES

    @property
    def is_bool(self) -> bool:
        """Whether the attribute holds a boolean."""
        return self.dtype == "DevBoolean"

    @property
    def is_enum(self) -> bool:
        """Whether the attribute is an enumeration."""
        return self.dtype == "DevEnum" or bool(self.enum_labels)

    @property
    def is_string(self) -> bool:
        """Whether the attribute holds a string."""
        return self.dtype == "DevString"

    @property
    def is_scalar(self) -> bool:
        """Whether the attribute is a scalar."""
        return self.dformat == "SCALAR"

    @property
    def is_spectrum(self) -> bool:
        """Whether the attribute is a 1D array."""
        return self.dformat == "SPECTRUM"

    @property
    def is_image(self) -> bool:
        """Whether the attribute is a 2D array."""
        return self.dformat == "IMAGE"

    @property
    def is_writable(self) -> bool:
        """Whether a client can write the attribute."""
        return self.writable in {"WRITE", "READ_WRITE", "READ_WITH_WRITE"}

    @property
    def is_expert(self) -> bool:
        """Whether the attribute is flagged for experts only."""
        return self.disp_level == "EXPERT"

    @property
    def display_label(self) -> str:
        """A human-friendly label, falling back to a prettified name."""
        if self.label and self.label != self.name:
            return self.label
        return prettify(self.name)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttributeInfo:
        """Build from a (possibly partial) dictionary."""
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        for key in ("min_value", "max_value", "min_alarm", "max_alarm"):
            if key in known:
                known[key] = _clean_limit(known[key])
        return cls(**known)


@dataclass
class CommandInfo:
    """Metadata for a single Tango command."""

    name: str
    in_type: str = "DevVoid"
    out_type: str = "DevVoid"
    doc_in: str = ""
    doc_out: str = ""
    disp_level: str = "OPERATOR"
    #: For a DevEnum argument: its ``[label, value]`` choices, when they can be
    #: found in the project's source (Tango doesn't publish them for commands).
    in_enum: list[list[Any]] = field(default_factory=list)

    @property
    def takes_argument(self) -> bool:
        """Whether the command needs an input argument."""
        return self.in_type != "DevVoid"

    @property
    def is_expert(self) -> bool:
        """Whether the command is flagged for experts only."""
        return self.disp_level == "EXPERT"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandInfo:
        """Build from a (possibly partial) dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class DeviceInterface:
    """The public interface of a device: its attributes and commands."""

    class_name: str
    attributes: list[AttributeInfo] = field(default_factory=list)
    commands: list[CommandInfo] = field(default_factory=list)

    def attribute(self, name: str) -> AttributeInfo | None:
        """Look up an attribute by case-insensitive name."""
        lowered = name.lower()
        for attr in self.attributes:
            if attr.name.lower() == lowered:
                return attr
        return None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceInterface:
        """Build from a dictionary."""
        return cls(
            class_name=data["class_name"],
            attributes=[AttributeInfo.from_dict(a) for a in data.get("attributes", [])],
            commands=[CommandInfo.from_dict(c) for c in data.get("commands", [])],
        )


@dataclass
class DeviceInstance:
    """A deployed device: its TRL, class and the interface it exposes."""

    trl: str
    class_name: str
    properties: dict[str, Any] = field(default_factory=dict)
    interface: str | None = None  # key into Snapshot.interfaces
    #: Device server name and instance (e.g. ``RFIMonitor`` / ``m1``), if known.
    server: str = ""
    instance: str = ""
    #: How the deployment runs the server, from its rendered chart:
    #: ``{"image": ..., "executable": ..., "env": {...}}``. Used to start a
    #: C++ server from its own image for introspection.
    launch: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceInstance:
        """Build from a dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Snapshot:
    """Everything discovered about a project's devices."""

    devices: list[DeviceInstance] = field(default_factory=list)
    interfaces: dict[str, DeviceInterface] = field(default_factory=dict)
    source: str = ""

    def interface_for(self, device: DeviceInstance) -> DeviceInterface | None:
        """Return the interface of a device, if it was discovered."""
        if device.interface is None:
            return None
        return self.interfaces.get(device.interface)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "version": SNAPSHOT_VERSION,
            "source": self.source,
            "devices": [asdict(d) for d in self.devices],
            "interfaces": {k: asdict(v) for k, v in self.interfaces.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        """Deserialise from a dictionary."""
        return cls(
            devices=[DeviceInstance.from_dict(d) for d in data.get("devices", [])],
            interfaces={
                k: DeviceInterface.from_dict(v)
                for k, v in data.get("interfaces", {}).items()
            },
            source=data.get("source", ""),
        )

    def save(self, path: Path) -> None:
        """Write the snapshot to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str) + "\n")

    @classmethod
    def load(cls, path: Path) -> Snapshot:
        """Read a snapshot from a JSON file."""
        return cls.from_dict(json.loads(path.read_text()))


def split_words(name: str) -> list[str]:
    """
    Split a snake_case or camelCase identifier into lowercase words.

    >>> split_words("pwsl_temp")
    ['pwsl', 'temp']
    >>> split_words("healthState")
    ['health', 'state']
    >>> split_words("gntpRxPktsByIRQ")
    ['gntp', 'rx', 'pkts', 'by', 'irq']
    """
    import re

    words: list[str] = []
    for chunk in re.split(r"[_\-\s.]+", name):
        words.extend(
            re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]?[a-z]+|[A-Z]+|\d+", chunk)
        )
    return [w.lower() for w in words if w]


_ACRONYMS = {
    "ip", "ipv", "mac", "utc", "ntp", "ptp", "gps", "cpu", "psu", "id",
    "irq", "snmp", "wr", "rx", "tx", "pps", "fpga", "sfp", "tai", "dns", "ok", "iq",
}  # fmt: skip


def prettify(name: str) -> str:
    """
    Turn an attribute name into a display label.

    >>> prettify("net_wr0_ipv4_addr")
    'Net WR0 IPV4 Addr'
    >>> prettify("healthState")
    'Health State'
    """
    out: list[str] = []
    for word in split_words(name):
        if word.isdigit() and out:
            out[-1] += word
            continue
        out.append(word.upper() if word in _ACRONYMS else word.capitalize())
    return " ".join(out)
