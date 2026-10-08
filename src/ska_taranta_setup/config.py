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

from ska_taranta_setup.options import GenerateOptions, SubsystemSpec, build

TOOL_KEY = "ska-taranta-setup"
#: Settings file for projects without a pyproject.toml (e.g. C++ with CMake):
#: the same keys as ``[tool.ska-taranta-setup]``, at the top level.
SETTINGS_FILE = "ska-taranta.toml"
#: Tango's own classes, which come with the ska-tango-base chart rather than
#: the project (e.g. TangoTest's sys/tg_test/1): never put on dashboards.
TANGO_SYSTEM_CLASSES = {
    "TangoTest", "DataBase", "DataBaseds", "DServer", "Starter", "TangoAccessControl",
}  # fmt: skip


@dataclass
class Config:
    """Settings for a host project."""

    root: Path
    project_name: str = ""
    #: Dashboard title prefix (defaults to the project name).
    title: str = ""
    #: Number of columns sections are packed into (0: fit the screen width).
    columns: int = 0
    #: Taranta's grid size in pixels (``MIN_WIDGET_SIZE`` in its config.js).
    #: The SKA Taranta image uses 10; upstream Taranta defaults to 20.
    tile_size: int = 10
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
    #: (Tango's own system and test classes are always left out too.)
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
    #: Taranta account ``upload`` logs in as. The password is never stored:
    #: it comes from ``$TARANTA_PASSWORD`` or a prompt.
    taranta_user: str = "user1"
    #: Taranta-facing Tango host, used by ``discover --live``.
    tango_host: str = ""
    #: Seconds to wait for each device server to start when introspecting.
    startup_timeout: float = 20.0
    #: C++ (compiled) servers: where to look for built executables, and how
    #: long to wait for them; servers run from a container image get longer,
    #: as the image may need pulling.
    cpp_build_dirs: list[str] = field(
        default_factory=lambda: ["build", "cmake-build-*", "out/build"]
    )
    cpp_startup_timeout: float = 90.0
    container_startup_timeout: float = 240.0
    #: Taranta helm chart versions to add to the umbrella chart.
    taranta_version: str = "2.18.9"
    taranta_auth_version: str = "0.3.1"
    tangogql_version: str = "1.0.13"
    #: ``[tool.ska-taranta-setup.generate]``: what goes on the dashboards.
    generate: GenerateOptions = field(default_factory=GenerateOptions)
    #: ``[tool.ska-taranta-setup.layout]``: sizes, passed to ``LayoutOptions``.
    layout: dict[str, Any] = field(default_factory=dict)
    #: ``[[tool.ska-taranta-setup.subsystems]]``: hand-defined subsystem pages
    #: (None: work them out from the devices).
    subsystems: list[SubsystemSpec] | None = None

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
        if class_name in TANGO_SYSTEM_CLASSES:
            return True
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
    standalone = root / SETTINGS_FILE
    if not raw and standalone.is_file():
        with standalone.open("rb") as fh:
            raw = tomllib.load(fh)

    # Reuse ska-tango-difdoc's per-class properties so a project that already
    # documents its devices with ``tangodocgen --auto`` needs no extra config.
    properties: dict[str, dict[str, Any]] = {}
    for class_name, spec in tool.get("tangodifdoc", {}).items():
        if isinstance(spec, dict) and "properties" in spec:
            properties[class_name] = dict(spec["properties"])
    for class_name, props in raw.pop("properties", {}).items():
        properties.setdefault(class_name, {}).update(props)

    where = f"tool.{TOOL_KEY}"
    generate = build(GenerateOptions, raw.pop("generate", {}), f"{where}.generate")
    layout = dict(raw.pop("layout", {}))
    specs = raw.pop("subsystems", None)
    subsystems = (
        [build(SubsystemSpec, s, f"[{where}.subsystems]") for s in specs]
        if specs is not None
        else None
    )

    known = {k.replace("-", "_"): v for k, v in raw.items()}
    known = {k: v for k, v in known.items() if k in Config.__dataclass_fields__}
    config = Config(
        root=root,
        project_name=project_name,
        properties=properties,
        generate=generate,
        layout=layout,
        subsystems=subsystems,
    )
    for key, value in known.items():
        setattr(config, key, value)
    if not config.chart:
        config.chart = _guess_chart(root, project_name)
    if not config.title:
        config.title = project_name
    return config
