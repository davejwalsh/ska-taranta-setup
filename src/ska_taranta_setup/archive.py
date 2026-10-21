"""
Find what an HDB++ archiver stores, for ``ska-taranta archiver-dashboard``.

The archived attributes come from, in order:

1. the HDB++ TimescaleDB itself: ``att_conf`` lists every archived
   attribute and the table its data is in, ``att_parameter`` its label, unit
   and enum labels. The database is reached with ``psql`` and a DSN, inside
   its Kubernetes pod (``kubectl exec``), or inside its Docker Compose
   container (``docker exec``);
2. the project's yaml2archiving configuration files, resolved against the
   device interfaces in ``taranta/devices.json`` (no database needed).

How to connect comes from the event subscriber's ``LibConfiguration``
property (``connect_string=user=... password=... host=... dbname=...``),
found in the rendered chart or any dsconfig/compose file in the project.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ska_taranta_setup.model import DeviceInstance, Snapshot

logger = logging.getLogger(__name__)

#: HDB++ table name suffixes (``att_scalar_devdouble``) to Tango type names.
TANGO_TYPES = {
    name.lower(): name
    for name in (
        "DevBoolean", "DevUChar", "DevShort", "DevUShort", "DevLong", "DevULong",
        "DevLong64", "DevULong64", "DevFloat", "DevDouble", "DevString",
        "DevState", "DevEnum", "DevEncoded",
    )
}  # fmt: skip
FORMATS = {"scalar": "SCALAR", "array": "SPECTRUM", "image": "IMAGE"}
TABLE_FORMATS = {v: k for k, v in FORMATS.items()}
NUMERIC = {
    "DevUChar", "DevShort", "DevUShort", "DevLong", "DevULong", "DevLong64",
    "DevULong64", "DevFloat", "DevDouble",
}  # fmt: skip
#: Directories never searched for archiving configuration.
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "build", ".tox", "__pycache__"}
SNAPSHOT_VERSION = 1
EVENT_SUBSCRIBER = "HdbEventSubscriber"
_SEP = "\x1f"  # field separator for psql output: never in Tango names


class ArchiveError(RuntimeError):
    """The archive couldn't be read."""


@dataclass
class ArchivedAttribute:
    """One attribute the archiver stores."""

    device: str  # domain/family/member, lower case as HDB++ stores it
    name: str  # lower case, as HDB++ stores it
    table: str  # e.g. att_scalar_devdouble
    label: str = ""
    unit: str = ""
    enum_labels: list[str] = field(default_factory=list)

    @property
    def dtype(self) -> str:
        """The Tango data type, from the table name."""
        return TANGO_TYPES.get(self.table.rsplit("_", 1)[-1], "DevString")

    @property
    def dformat(self) -> str:
        """SCALAR, SPECTRUM or IMAGE, from the table name."""
        parts = self.table.split("_")
        return FORMATS.get(parts[1] if len(parts) > 2 else "scalar", "SCALAR")

    @property
    def trl(self) -> str:
        """``domain/family/member/attribute``."""
        return f"{self.device}/{self.name}"

    @property
    def display(self) -> str:
        """The attribute's real (camelCase) name when known."""
        if self.label and self.label.lower() == self.name:
            return self.label
        return self.name

    @property
    def is_numeric(self) -> bool:
        """A number that can be plotted."""
        return self.dformat == "SCALAR" and self.dtype in NUMERIC

    @property
    def is_discrete(self) -> bool:
        """A state, enum or boolean: shown as a state timeline."""
        return self.dformat == "SCALAR" and self.dtype in {
            "DevState",
            "DevEnum",
            "DevBoolean",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchivedAttribute:
        """Build from a dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ArchiveSnapshot:
    """Everything known about what's archived."""

    attributes: list[ArchivedAttribute] = field(default_factory=list)
    source: str = ""

    @property
    def devices(self) -> list[str]:
        """Archived devices, sorted."""
        return sorted({a.device for a in self.attributes})

    def save(self, path: Path) -> None:
        """Write to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": SNAPSHOT_VERSION,
            "source": self.source,
            "attributes": [asdict(a) for a in self.attributes],
        }
        path.write_text(json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> ArchiveSnapshot:
        """Read from a JSON file."""
        data = json.loads(path.read_text())
        return cls(
            attributes=[ArchivedAttribute.from_dict(a) for a in data["attributes"]],
            source=data.get("source", ""),
        )


# --------------------------------------------------------------------------
# Connecting
# --------------------------------------------------------------------------


@dataclass
class ArchiveDB:
    """Where the HDB++ database is, as the event subscriber connects to it."""

    host: str = ""
    port: int = 5432
    dbname: str = "hdb"
    user: str = "postgres"
    password: str = ""

    @classmethod
    def from_connect_string(cls, text: str) -> ArchiveDB:
        """Parse libpq ``key=value`` pairs (the ES ``connect_string``)."""
        values = dict(re.findall(r"(\w+)=(\S+)", text))
        return cls(
            host=values.get("host", ""),
            port=int(values.get("port", 5432)),
            dbname=values.get("dbname", "hdb"),
            user=values.get("user", "postgres"),
            password=values.get("password", ""),
        )

    @property
    def dsn(self) -> str:
        """A ``postgresql://`` URL."""
        auth = f"{self.user}:{self.password}@" if self.password else f"{self.user}@"
        return f"postgresql://{auth}{self.host}:{self.port}/{self.dbname}"


CONNECT_STRING = re.compile(r"connect_string=([^\"'\]\n]+)")


def _connect_string(items: Any) -> str | None:
    for item in items if isinstance(items, list) else [items]:
        match = CONNECT_STRING.search(str(item))
        if match:
            return match.group(1).strip()
    return None


def find_archive_db(
    root: Path, devices: list[DeviceInstance] | None = None
) -> ArchiveDB | None:
    """
    The database the event subscriber writes to.

    From an ``HdbEventSubscriber``'s ``LibConfiguration`` in ``devices``
    (as rendered from the chart), else from any file in the project that
    configures one (dsconfig JSON, compose files, ...).
    """
    for device in devices or []:
        if device.class_name == EVENT_SUBSCRIBER:
            found = _connect_string(device.properties.get("LibConfiguration"))
            if found:
                return ArchiveDB.from_connect_string(found)
    for path in _project_files(root, (".json", ".yaml", ".yml")):
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if "connect_string=" in text and "libhdb++" in text:
            found = _connect_string(text)
            if found and "{{" not in found:
                return ArchiveDB.from_connect_string(found)
    return None


def _project_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in SKIP_DIRS and not (d == "charts" and Path(dirpath) != root)
        )
        for name in sorted(filenames):
            if name.endswith(suffixes):
                found.append(Path(dirpath) / name)
    return found


# --------------------------------------------------------------------------
# Querying
# --------------------------------------------------------------------------


@dataclass
class Psql:
    """Runs SQL with ``psql``: locally, in a pod, or in a container."""

    command: list[str]
    where: str
    env: dict[str, str] = field(default_factory=dict)

    def query(self, sql: str) -> list[list[str]]:
        """Rows of text fields."""
        proc = subprocess.run(
            [*self.command, "-X", "-A", "-t", "-F", _SEP, "-c", sql],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, **self.env},
            timeout=60,
        )
        if proc.returncode != 0:
            raise ArchiveError(
                f"psql ({self.where}) failed: {proc.stderr.strip()[-1000:]}"
            )
        return [line.split(_SEP) for line in proc.stdout.split("\n") if line]


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _psql_args(db: ArchiveDB) -> list[str]:
    return ["psql", "-h", "127.0.0.1", "-U", db.user, "-d", db.dbname]


def psql_in_pod(db: ArchiveDB, namespace: str) -> Psql | None:
    """Run psql inside the database's pod, found from its Service."""
    if not shutil.which("kubectl") or not db.host:
        return None
    service = db.host.split(".")[0]
    selector = _run(
        [
            "kubectl",
            "get",
            "svc",
            service,
            "-n",
            namespace,
            "-o",
            "jsonpath={.spec.selector}",
        ]
    )
    if not selector:
        return None
    labels = ",".join(f"{k}={v}" for k, v in json.loads(selector).items())
    pod = _run(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            labels,
            "--field-selector=status.phase=Running",
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ]
    )
    if not pod:
        return None
    return Psql(
        [
            "kubectl",
            "exec",
            "-i",
            "-n",
            namespace,
            pod,
            "--",
            "env",
            f"PGPASSWORD={db.password}",
            *_psql_args(db),
        ],
        f"pod {namespace}/{pod}",
    )


def psql_in_container(db: ArchiveDB) -> Psql | None:
    """Run psql in the database's Docker Compose container (service == host)."""
    if not shutil.which("docker") or not db.host:
        return None
    container = _run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.service={db.host}",
            "--format",
            "{{.Names}}",
        ]
    ).splitlines()
    if not container:
        return None
    return Psql(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={db.password}",
            container[0],
            *_psql_args(db),
        ],
        f"container {container[0]}",
    )


def psql_local(dsn: str) -> Psql:
    """Run psql on this machine, with a DSN."""
    if not shutil.which("psql"):
        raise ArchiveError(
            "--dsn needs `psql` on this machine (e.g. apt install postgresql-client)."
        )
    return Psql(["psql", dsn], "psql")


SCAN_SQL = """
SELECT c.domain || '/' || c.family || '/' || c.member, c.name, c.table_name,
       coalesce(p.label, ''), coalesce(p.unit, ''),
       coalesce(array_to_string(p.enum_labels, E'\\x1e'), '')
FROM att_conf c
LEFT JOIN LATERAL (
    SELECT label, unit, enum_labels FROM att_parameter
    WHERE att_parameter.att_conf_id = c.att_conf_id
    ORDER BY recv_time DESC LIMIT 1
) p ON true
WHERE NOT coalesce(c.hide, false)
ORDER BY 1, 2
"""


def scan_database(psql: Psql) -> ArchiveSnapshot:
    """Every attribute in the archive."""
    attributes = []
    for row in psql.query(SCAN_SQL):
        device, name, table, label, unit, enums = (row + [""] * 6)[:6]
        attributes.append(
            ArchivedAttribute(
                device=device.lower(),
                name=name.lower(),
                table=table,
                label=label,
                unit=unit,
                enum_labels=enums.split("\x1e") if enums else [],
            )
        )
    return ArchiveSnapshot(attributes, source=psql.where)


# --------------------------------------------------------------------------
# Offline: yaml2archiving configuration
# --------------------------------------------------------------------------


def _load_yaml(path: Path) -> Any:
    # Helm templating in chart data files, e.g. db: "{{ .Values.tango_host }}".
    text = re.sub(r"\{\{.*?\}\}", "x", path.read_text())
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def find_archiving_configs(root: Path) -> list[Path]:
    """yaml2archiving files: a ``configuration`` list and an archiver/manager."""
    found = []
    for path in _project_files(root, (".yaml", ".yml")):
        data = _load_yaml(path)
        if (
            isinstance(data, dict)
            and isinstance(data.get("configuration"), list)
            and ("archiver" in data or "manager" in data)
        ):
            found.append(path)
    return found


def table_for(dtype: str, dformat: str) -> str:
    """The HDB++ table an attribute of this type is stored in."""
    kind = TABLE_FORMATS.get(dformat, "scalar")
    return f"att_{kind}_{dtype.lower()}"


def scan_configs(
    paths: list[Path], snapshot: Snapshot, root: Path | None = None
) -> ArchiveSnapshot:
    """What the yaml2archiving files archive, typed from the device interfaces."""
    by_trl = {d.trl.lower(): d for d in snapshot.devices}
    attributes: dict[str, ArchivedAttribute] = {}
    for path in paths:
        for entry in _load_yaml(path).get("configuration", []):
            if not isinstance(entry, dict):
                continue
            if "class" in entry:
                devices = [
                    d for d in snapshot.devices if d.class_name == entry["class"]
                ]
            elif "device" in entry:
                device = by_trl.get(str(entry["device"]).lower())
                devices = [device] if device else []
            else:
                devices = []
            for device in devices:
                interface = snapshot.interface_for(device)
                for name in entry.get("attributes") or {}:
                    info = interface.attribute(name) if interface else None
                    if info is None:
                        logger.warning("%s/%s: not in devices.json", device.trl, name)
                        continue
                    attr = ArchivedAttribute(
                        device=device.trl.lower(),
                        name=info.name.lower(),
                        table=table_for(info.dtype, info.dformat),
                        label=info.name,
                        unit=info.unit,
                        enum_labels=list(info.enum_labels),
                    )
                    attributes[attr.trl] = attr
    ordered = sorted(attributes.values(), key=lambda a: (a.device, a.name))
    names = ", ".join(
        str(p.relative_to(root) if root and p.is_relative_to(root) else p)
        for p in paths
    )
    return ArchiveSnapshot(ordered, source=f"archiving config ({names})")


def kube_namespace(root: Path, default: str) -> str:
    """``$KUBE_NAMESPACE``, else the Makefile's ``KUBE_NAMESPACE ?= ...``."""
    if os.environ.get("KUBE_NAMESPACE"):
        return os.environ["KUBE_NAMESPACE"]
    makefile = root / "Makefile"
    if makefile.is_file():
        match = re.search(
            r"^KUBE_NAMESPACE\s*\??=\s*(\S+)\s*$", makefile.read_text(), re.MULTILINE
        )
        if match and "$" not in match.group(1):
            return match.group(1)
    return default
