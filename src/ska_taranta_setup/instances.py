"""
Find which Tango devices a project deploys, and with which properties.

Sources, in order of preference:

1. Explicit ``devices`` in ``[tool.ska-taranta-setup]``.
2. ``helmfile write-values`` for the configured environment. This renders the
   real per-device properties (including ones derived from telmodel, such as
   the SNMP ``Model``), which is what makes the dashboards accurate.
3. ``helm template`` of the umbrella chart: charts using ``ska-tango-util``
   render a dsconfig ``configuration.json`` (in a ConfigMap) listing every
   server, class, device and property, however the chart builds them
   (e.g. one device per entry of a ``station_ids`` list).
4. The raw ``values*.yaml`` files of the chart and helmfile, used as-is.

Both the ``ska-tango-devices`` chart layout
(``devices: {Class: {trl: {prop: value}}}``) and the older ``ska-tango-util``
layout (``deviceServers: ... classes: [{name, devices: [{name, properties}]}]``)
are understood.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from ska_taranta_setup.config import Config
from ska_taranta_setup.model import DeviceInstance

logger = logging.getLogger(__name__)

TANGO_DEVICES_CHART = "ska-tango-devices"


class _AnyTagLoader(yaml.SafeLoader):
    """A YAML loader that tolerates custom tags."""


_AnyTagLoader.add_multi_constructor("", lambda loader, suffix, node: None)


def _looks_like_trl(name: str) -> bool:
    return isinstance(name, str) and name.count("/") == 2


def _from_tango_devices(devices: dict[str, Any]) -> Iterator[DeviceInstance]:
    """Parse the ``ska-tango-devices`` chart's ``devices`` mapping."""
    for class_name, by_trl in (devices or {}).items():
        if not isinstance(by_trl, dict):
            continue
        for trl, props in by_trl.items():
            if _looks_like_trl(trl):
                yield DeviceInstance(
                    trl=trl.lower(),
                    class_name=class_name,
                    properties=dict(props or {}),
                )


def _from_classes_list(classes: list[Any]) -> Iterator[DeviceInstance]:
    """Parse the older ``ska-tango-util`` ``classes`` list."""
    for cls in classes or []:
        if not isinstance(cls, dict) or "name" not in cls:
            continue
        for dev in cls.get("devices") or []:
            if not isinstance(dev, dict) or not _looks_like_trl(dev.get("name", "")):
                continue
            props: dict[str, Any] = {}
            for prop in dev.get("properties") or []:
                values = prop.get("values", [])
                props[prop["name"]] = values[0] if len(values) == 1 else values
            yield DeviceInstance(
                trl=dev["name"].lower(), class_name=cls["name"], properties=props
            )


def find_devices_in_values(values: Any) -> list[DeviceInstance]:
    """Recursively find device instances anywhere in a helm values tree."""
    found: list[DeviceInstance] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            tango_devices = node.get(TANGO_DEVICES_CHART)
            if isinstance(tango_devices, dict) and "devices" in tango_devices:
                found.extend(_from_tango_devices(tango_devices["devices"]))
            if isinstance(node.get("classes"), list):
                found.extend(_from_classes_list(node["classes"]))
            for key, child in node.items():
                if key != TANGO_DEVICES_CHART:
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(values)
    return found


def _merge(instances: list[DeviceInstance]) -> list[DeviceInstance]:
    """De-duplicate by TRL; later definitions add to earlier ones."""
    merged: dict[str, DeviceInstance] = {}
    for inst in instances:
        if inst.trl in merged:
            merged[inst.trl].properties.update(inst.properties)
        else:
            merged[inst.trl] = inst
    return sorted(merged.values(), key=lambda d: (d.class_name, d.trl))


def _helmfile_path(root: Path) -> Path | None:
    for candidate in (
        "helmfile.d/helmfile.yaml.gotmpl",
        "helmfile.d/helmfile.yaml",
        "helmfile.yaml.gotmpl",
        "helmfile.yaml",
    ):
        if (root / candidate).is_file():
            return root / candidate
    return None


def _outside_venv() -> dict[str, str]:
    """
    The environment with the active virtualenv removed.

    helmfile templates run helper scripts with whatever ``python`` is on
    PATH. Under ``uv run`` that's the project venv, whose packages can change
    their behaviour (e.g. ``fqdn`` turns on jsonschema hostname checks that
    telmodel data fails). ``make`` deploys don't run in the venv, so match that.
    """
    env = dict(os.environ)
    venv = env.pop("VIRTUAL_ENV", None)
    if venv:
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep) if not p.startswith(venv)
        )
    return env


def from_helmfile(config: Config) -> list[DeviceInstance]:
    """Render helmfile values and pull device instances out of them."""
    helmfile = _helmfile_path(config.root)
    if helmfile is None or shutil.which("helmfile") is None:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            "helmfile",
            "-f",
            str(helmfile),
            "-e",
            config.helmfile_environment,
            "write-values",
            "--output-file-template",
            f"{tmp}/{{{{ .Release.Name }}}}.yaml",
        ]
        env = {**_outside_venv(), **{k: str(v) for k, v in config.helmfile_env.items()}}
        logger.info("Rendering helm values: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            cwd=helmfile.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                "helmfile write-values failed (exit %s):\n%s",
                proc.returncode,
                proc.stderr.strip()[-2000:],
            )
            return []
        instances: list[DeviceInstance] = []
        for path in sorted(Path(tmp).glob("*.yaml")):
            instances.extend(find_devices_in_values(yaml.safe_load(path.read_text())))
        return instances


def _unwrap(value: Any) -> Any:
    """Dsconfig stores every property as a list; single values read better."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def from_dsconfig(configuration: dict[str, Any]) -> list[DeviceInstance]:
    """Devices from a dsconfig JSON: servers -> instance -> class -> device."""
    found = []
    for instances in (configuration.get("servers") or {}).values():
        for classes in (instances or {}).values():
            for class_name, devices in (classes or {}).items():
                for trl, spec in (devices or {}).items():
                    if not _looks_like_trl(trl):
                        continue
                    props = (spec or {}).get("properties") or {}
                    found.append(
                        DeviceInstance(
                            trl=trl.lower(),
                            class_name=class_name,
                            properties={k: _unwrap(v) for k, v in props.items()},
                        )
                    )
    return found


def from_rendered_chart(config: Config) -> list[DeviceInstance]:
    """Render the umbrella chart and read the dsconfig JSON it produces."""
    chart = config.chart_path
    if chart is None or not (chart / "Chart.yaml").is_file():
        return []
    if shutil.which("helm") is None:
        return []
    cmd = ["helm", "template", config.project_name or "release", str(chart)]
    logger.info("Rendering chart: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=config.root,
        env=_outside_venv(),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning(
            "helm template failed (exit %s); is `helm dependency build` needed?\n%s",
            proc.returncode,
            proc.stderr.strip()[-2000:],
        )
        return []
    instances: list[DeviceInstance] = []
    for doc in yaml.safe_load_all(proc.stdout):
        if not isinstance(doc, dict) or doc.get("kind") != "ConfigMap":
            continue
        for key, value in (doc.get("data") or {}).items():
            if not key.endswith(".json") or '"servers"' not in str(value):
                continue
            try:
                instances.extend(from_dsconfig(json.loads(value)))
            except (ValueError, AttributeError):
                continue
    return instances


def from_values_files(config: Config) -> list[DeviceInstance]:
    """Read device instances from raw values files, ignoring templating."""
    instances: list[DeviceInstance] = []
    patterns = [
        "charts/*/values*.yaml",
        "helmfile.d/**/*.yaml",
        "helmfile.d/**/*.gotmpl",
    ]
    for pattern in patterns:
        for path in sorted(config.root.glob(pattern)):
            if "/.deploy/" in str(path):
                continue
            try:
                text = path.read_text()
                if "{{" in text:
                    # Strip go-template lines so plain-YAML parts still parse.
                    text = "\n".join(
                        line for line in text.splitlines() if "{{" not in line
                    )
                data = yaml.load(text, Loader=_AnyTagLoader)
            except (OSError, yaml.YAMLError):
                continue
            instances.extend(find_devices_in_values(data))
    return instances


def from_config(config: Config) -> list[DeviceInstance]:
    """Device instances listed explicitly in configuration."""
    return [
        DeviceInstance(trl=trl.lower(), class_name=class_name)
        for class_name, trls in config.devices.items()
        for trl in trls
    ]


def discover_instances(config: Config) -> tuple[list[DeviceInstance], str]:
    """Find device instances, returning them and a description of the source."""
    if config.devices:
        return _merge(from_config(config)), "pyproject.toml"
    instances = from_helmfile(config)
    if instances:
        return _merge(instances), f"helmfile ({config.helmfile_environment})"
    instances = from_rendered_chart(config)
    if instances:
        return _merge(instances), f"helm template ({config.chart})"
    instances = from_values_files(config)
    if instances:
        return _merge(instances), "values files"
    return [], "none"
