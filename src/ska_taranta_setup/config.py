"""
Configuration, read from ``[tool.ska-taranta-setup]`` in the host project.

Every setting has a sensible default so that ``ska-taranta`` works with no
configuration at all in a "standard" SKA repo layout. ``ska-taranta init``
writes the guessed values into ``pyproject.toml`` so they are visible and
editable.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOOL_KEY = "ska-taranta-setup"


@dataclass
class Config:
    """Settings for a host project."""

    root: Path
    project_name: str = ""
    #: Dashboard title prefix (defaults to the project name).
    title: str = ""
    #: Number of columns sections are packed into.
    columns: int = 4
    #: Helm umbrella chart that gets the Taranta subcharts added.
    chart: str = ""
    #: Where generated dashboards (.wj) are written.
    dashboards_dir: str = "dashboards"
    #: Where the discovery snapshot is cached.
    snapshot: str = "taranta/devices.json"
    #: helmfile environment used to render device instances.
    helmfile_environment: str = "minikube-ci"
    #: Extra environment variables used when rendering helmfile values.
    helmfile_env: dict[str, str] = field(default_factory=dict)
    #: Device classes to leave out of dashboards (regular expressions).
    exclude_classes: list[str] = field(default_factory=lambda: [r".*Simulator$"])
    #: Device TRLs to leave out of dashboards (regular expressions).
    exclude_devices: list[str] = field(default_factory=list)
    #: Device properties that change a device's interface (e.g. SNMP ``Model``).
    #: Devices of the same class that agree on these share one introspection.
    interface_key_properties: list[str] = field(default_factory=lambda: ["Model"])
    #: Per-class property overrides used when starting a device to introspect it.
    properties: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Explicit devices, ``{ClassName = ["domain/family/member", ...]}``. When
    #: set, these are used instead of rendering the helm charts.
    devices: dict[str, list[str]] = field(default_factory=dict)
    #: Name of the Tango DB as Taranta sees it (the URL path segment before
    #: ``/taranta``, e.g. ``/ska-sat-lmc/taranta/`` -> ``taranta``).
    tango_db: str = "taranta"
    #: Taranta-facing Tango host, used by ``discover --live``.
    tango_host: str = ""
    #: Seconds to wait for each device server to start when introspecting.
    startup_timeout: float = 20.0
    #: Taranta helm chart versions to add to the umbrella chart.
    taranta_version: str = "2.18.9"
    taranta_auth_version: str = "0.3.1"
    tangogql_version: str = "1.0.13"

    @property
    def snapshot_path(self) -> Path:
        """Absolute path to the snapshot file."""
        return self.root / self.snapshot

    @property
    def dashboards_path(self) -> Path:
        """Absolute path to the dashboards directory."""
        return self.root / self.dashboards_dir

    @property
    def chart_path(self) -> Path | None:
        """Absolute path to the umbrella chart, if one was found."""
        return self.root / self.chart if self.chart else None

    def is_excluded(self, class_name: str, trl: str) -> bool:
        """Whether a device should be left out of dashboards."""
        return any(re.fullmatch(p, class_name) for p in self.exclude_classes) or any(
            re.fullmatch(p, trl) for p in self.exclude_devices
        )


def _guess_chart(root: Path, project_name: str) -> str:
    charts = root / "charts"
    if not charts.is_dir():
        return ""
    if project_name and (charts / project_name / "Chart.yaml").is_file():
        return f"charts/{project_name}"
    candidates = sorted(p.parent for p in charts.glob("*/Chart.yaml"))
    return str(candidates[0].relative_to(root)) if candidates else ""


def load_pyproject(root: Path) -> dict[str, Any]:
    """Load the host project's pyproject.toml (empty if absent)."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(root: Path | str = ".") -> Config:
    """Build the configuration for the project rooted at ``root``."""
    root = Path(root).resolve()
    pyproject = load_pyproject(root)
    project_name = pyproject.get("project", {}).get("name", "") or root.name
    tool = pyproject.get("tool", {})
    raw: dict[str, Any] = dict(tool.get(TOOL_KEY, {}))

    # Reuse ska-tango-difdoc's per-class properties so a project that already
    # documents its devices with ``tangodocgen --auto`` needs no extra config.
    properties: dict[str, dict[str, Any]] = {}
    for class_name, spec in tool.get("tangodifdoc", {}).items():
        if isinstance(spec, dict) and "properties" in spec:
            properties[class_name] = dict(spec["properties"])
    for class_name, props in raw.pop("properties", {}).items():
        properties.setdefault(class_name, {}).update(props)

    known = {k.replace("-", "_"): v for k, v in raw.items()}
    known = {k: v for k, v in known.items() if k in Config.__dataclass_fields__}
    config = Config(root=root, project_name=project_name, properties=properties)
    for key, value in known.items():
        setattr(config, key, value)
    if not config.chart:
        config.chart = _guess_chart(root, project_name)
    if not config.title:
        config.title = project_name
    return config
