"""
Discover the full interface of a project's Tango devices.

The only reliable way to see every attribute of a Tango device, including
dynamic ones created in ``init_device`` (SNMP devices, for example), is to ask a
running device. Two ways of doing that are supported:

* **local** (default): each device class is started in a background process
  with no Tango database (``-file=`` property file), using the same properties
  it gets when deployed, and then queried through a ``DeviceProxy``. This is
  the approach used by ``ska-tango-difdoc`` and needs no cluster.
* **live**: query devices that are already running, via ``TANGO_HOST``.

Devices of the same class whose ``interface_key_properties`` (e.g. ``Model``)
match share one introspection, so large deployments stay quick.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ska_taranta_setup.config import Config, load_pyproject
from ska_taranta_setup.model import (
    AttributeInfo,
    CommandInfo,
    DeviceInstance,
    DeviceInterface,
    Snapshot,
)

logger = logging.getLogger(__name__)

READY_MARKER = "Ready to accept request"


class IntrospectionError(RuntimeError):
    """A device could not be started or queried."""


def _import_tango() -> Any:
    try:
        import tango
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise IntrospectionError(
            "pytango is required to introspect devices. Run ska-taranta from the "
            "project's own environment (e.g. `uv run ska-taranta discover`)."
        ) from exc
    return tango


# --------------------------------------------------------------------------
# Locating device classes in the host project
# --------------------------------------------------------------------------


def find_class_modules(root: Path) -> dict[str, str]:
    """
    Map class names to the module that defines them, without importing.

    Module paths are derived from ``src/`` layout (or the project root), and
    ``[project.scripts]`` entries are used as a fallback.
    """
    mapping: dict[str, str] = {}
    src = root / "src" if (root / "src").is_dir() else root
    for path in sorted(src.rglob("*.py")):
        if any(
            part.startswith(".") or part in {"tests", "build"} for part in path.parts
        ):
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        module = ".".join(path.relative_to(src).with_suffix("").parts)
        module = module.removesuffix(".__init__")
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.bases:
                mapping.setdefault(node.name, module)

    scripts = load_pyproject(root).get("project", {}).get("scripts", {})
    for name, target in scripts.items():
        module, _, attr = str(target).partition(":")
        class_name = attr.split(".")[0]
        if class_name and class_name[0].isupper():
            mapping.setdefault(class_name, module)
        elif name[0].isupper():
            mapping.setdefault(name, module)
    return mapping


# --------------------------------------------------------------------------
# Converting pytango metadata to the model
# --------------------------------------------------------------------------


def _enum_name(value: Any) -> str:
    return getattr(value, "name", None) or str(value).rsplit(".", 1)[-1]


def query_interface(proxy: Any) -> DeviceInterface:
    """Read attribute and command metadata from a ``DeviceProxy``."""
    tango = _import_tango()
    attributes = []
    for info in proxy.attribute_list_query_ex():
        attributes.append(
            AttributeInfo.from_dict(
                {
                    "name": info.name,
                    "dtype": tango.CmdArgType(info.data_type).name,
                    "dformat": _enum_name(info.data_format),
                    "writable": _enum_name(info.writable),
                    "label": info.label,
                    "unit": info.unit if info.unit != "No unit" else "",
                    "description": (
                        "" if info.description == "No description" else info.description
                    ),
                    "disp_level": _enum_name(info.disp_level),
                    "enum_labels": list(getattr(info, "enum_labels", []) or []),
                    "min_value": info.min_value,
                    "max_value": info.max_value,
                    "min_alarm": info.min_alarm,
                    "max_alarm": info.max_alarm,
                    "max_dim_x": info.max_dim_x,
                }
            )
        )
    commands = [
        CommandInfo(
            name=info.cmd_name,
            in_type=_enum_name(info.in_type),
            out_type=_enum_name(info.out_type),
            doc_in=info.in_type_desc if info.in_type_desc != "Uninitialised" else "",
            doc_out=info.out_type_desc if info.out_type_desc != "Uninitialised" else "",
            disp_level=_enum_name(info.disp_level),
        )
        for info in proxy.command_list_query()
    ]
    return DeviceInterface(
        class_name=proxy.info().dev_class, attributes=attributes, commands=commands
    )


def interface_id(interface: DeviceInterface) -> str:
    """A stable key for an interface, so identical ones are stored once."""
    digest = hashlib.sha1(
        json.dumps(asdict(interface), sort_keys=True, default=str).encode()
    ).hexdigest()[:8]
    return f"{interface.class_name}-{digest}"


# --------------------------------------------------------------------------
# Local (no database) introspection
# --------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _format_property(value: Any) -> str:
    """Format a property value for a Tango property file."""

    def quote(item: Any) -> str:
        if isinstance(item, bool):
            item = str(item).lower()
        return '"' + str(item).replace('"', '\\"') + '"'

    if isinstance(value, (list, tuple)):
        return ", ".join(quote(v) for v in value) if value else '""'
    return quote(value)


def write_property_file(
    path: Path,
    class_name: str,
    trl: str,
    properties: dict[str, Any],
    server: str | None = None,
) -> None:
    """Write a Tango property file declaring one device of ``server``."""
    server = server or class_name
    lines = [f'{server}/introspect/DEVICE/{class_name}: "{trl}"', ""]
    for key, value in properties.items():
        if value is None:
            continue
        lines.append(f"{trl}->{key}: {_format_property(value)}")
    path.write_text("\n".join(lines) + "\n")


@dataclass
class _Server:
    group_key: tuple[str, ...]
    trl: str
    class_name: str
    port: int
    process: subprocess.Popen[str]
    log_path: Path
    timeout: float = 20.0
    container: str = ""  # docker container name, when run from an image

    def log(self) -> str:
        try:
            return self.log_path.read_text()
        except OSError:
            return ""

    def wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if READY_MARKER in self.log():
                return
            if self.process.poll() is not None:
                raise IntrospectionError(
                    f"{self.class_name} exited with code {self.process.returncode}:\n"
                    + self.log()[-3000:]
                )
            time.sleep(0.1)
        raise IntrospectionError(
            f"{self.class_name} did not start within {timeout}s:\n" + self.log()[-3000:]
        )

    def stop(self) -> None:
        if self.container:
            subprocess.run(
                ["docker", "rm", "-f", self.container],
                capture_output=True,
                check=False,
            )
            self.container = ""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def _is_trl(value: Any) -> bool:
    text = str(value)
    return text.count("/") == 2 and " " not in text and ":" not in text


HOST_PROPERTY = re.compile(r"(?i).*(host|hostname|address|ip)$")


def sanitise_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """
    Make deployment properties safe to use for a local, offline start.

    * Properties naming other devices are dropped. Aggregating devices
      (controllers, rollups) connect to their sub-devices in ``init_device``;
      with no database that fails and the server exits.
    * Host/address properties point at localhost. In-cluster names like
      ``wrsimulator-grandmaster-snmp`` don't resolve outside k8s and the
      device hangs in ``init_device``; nothing is ever contacted.
    """
    kept = {}
    for key, value in properties.items():
        items = value if isinstance(value, (list, tuple)) else [value]
        if items and all(_is_trl(v) for v in items):
            continue
        is_host = HOST_PROPERTY.fullmatch(key) and isinstance(value, str) and value
        kept[key] = "127.0.0.1" if is_host else value
    return kept


IP_ADDRESS = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def sanitise_env(env: dict[str, str]) -> dict[str, str]:
    """
    A deployment's environment, made safe for an offline start.

    Tango's own variables (TANGO_HOST, ports) are dropped, as the server runs
    with no database on a port of our choosing. Hosts and IP addresses (e.g.
    the RFI monitor's ``device_ip``, a real instrument) point at localhost, so
    nothing is contacted.
    """
    safe = {}
    for key, value in env.items():
        if key.upper().startswith("TANGO_"):
            continue
        hosty = HOST_PROPERTY.fullmatch(key) or IP_ADDRESS.match(value)
        safe[key] = "127.0.0.1" if hosty and value else value
    return safe


def find_executable(root: Path, name: str, build_dirs: list[str]) -> Path | None:
    """A built executable called ``name`` under the project's build directories."""
    for pattern in build_dirs:
        for build in sorted(root.glob(pattern)):
            if not build.is_dir():
                continue
            for path in sorted(build.rglob(name)):
                if path.is_file() and os.access(path, os.X_OK):
                    return path
    return None


def docker_available() -> bool:
    """Whether a Docker daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@dataclass
class _Launch:
    """How to start one device server for introspection."""

    how: str  # "python", "executable" or "image"
    cmd: list[str]
    env: dict[str, str]
    server: str  # server name, for the property file
    timeout: float
    container: str = ""
    prop_dir: str = ""  # where the property file is, as the server sees it


def plan_launch(
    config: Config,
    device: DeviceInstance,
    modules: dict[str, str],
    port: int,
    tag: str,
) -> _Launch:
    """
    Decide how to start a device's server.

    In order: its Python class, a built executable, or the deployment's
    container image.
    """
    class_name = device.class_name
    module = modules.get(class_name)
    if module is not None:
        return _Launch(
            "python",
            [sys.executable, "-m", "ska_taranta_setup._serve", module, class_name],
            {**os.environ, "PYTHONUNBUFFERED": "1"},
            class_name,
            config.startup_timeout,
        )
    server = device.server or Path(device.launch.get("executable", "")).name
    server = server or class_name
    env = sanitise_env(device.launch.get("env", {}))
    executable = find_executable(config.root, server, config.cpp_build_dirs)
    if executable is not None:
        return _Launch(
            "executable",
            [str(executable)],
            {**os.environ, **env},
            executable.name,
            config.cpp_startup_timeout,
        )
    image = device.launch.get("image")
    if image and docker_available():
        container = f"ska-taranta-introspect-{tag}"
        entrypoint = device.launch.get("executable") or f"/app/bin/{server}"
        cmd = ["docker", "run", "--rm", "-t", "--name", container]
        cmd += ["--entrypoint", entrypoint, "-p", f"127.0.0.1:{port}:{port}"]
        cmd += ["-v", "{workdir}:/ska-taranta:ro"]
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
        cmd.append(image)
        return _Launch(
            "image",
            cmd,
            dict(os.environ),
            Path(entrypoint).name,
            config.container_startup_timeout,
            container=container,
            prop_dir="/ska-taranta",
        )
    reasons = [f"no Python class {class_name} in the project"]
    reasons.append(
        f"no built executable '{server}' under {', '.join(config.cpp_build_dirs)}"
    )
    if image:
        reasons.append(f"Docker isn't available to run {image}")
    else:
        reasons.append("no container image for it in the chart")
    raise IntrospectionError(
        "; ".join(reasons) + ". Build it, start Docker, or use `discover --live`."
    )


def _group_key(config: Config, device: DeviceInstance) -> tuple[str, ...]:
    return (
        device.class_name,
        *(str(device.properties.get(p, "")) for p in config.interface_key_properties),
    )


def introspect_local(
    config: Config,
    devices: list[DeviceInstance],
    report: Callable[[str], None] | None = None,
) -> tuple[Snapshot, list[str]]:
    """
    Start each distinct device locally and read its interface.

    Python device classes are imported and run; C++ (or other compiled)
    servers run from a built executable or from their container image.
    """
    tango = _import_tango()
    report = report or logger.info
    modules = find_class_modules(config.root)
    errors: list[str] = []
    snapshot = Snapshot(devices=devices)

    groups: dict[tuple[str, ...], list[DeviceInstance]] = {}
    for device in devices:
        groups.setdefault(_group_key(config, device), []).append(device)

    workdir = Path(tempfile.mkdtemp(prefix="ska-taranta-"))
    workdir.chmod(0o755)  # readable from inside a container
    servers: list[_Server] = []
    try:
        for key, members in groups.items():
            representative = members[0]
            class_name = representative.class_name
            port = _free_port()
            tag = f"{os.getpid()}-{len(servers)}"
            try:
                launch = plan_launch(config, representative, modules, port, tag)
            except IntrospectionError as exc:
                errors.append(f"{class_name}: {exc}")
                continue
            # Overrides (e.g. Host=127.0.0.1) apply, but never to the properties
            # that select the interface: those must come from the deployment.
            props = {
                **sanitise_properties(representative.properties),
                **config.properties.get(class_name, {}),
                **{
                    k: representative.properties[k]
                    for k in config.interface_key_properties
                    if k in representative.properties
                },
            }
            prop_name = f"{class_name}-{len(servers)}.prop"
            prop_file = workdir / prop_name
            write_property_file(
                prop_file, class_name, representative.trl, props, launch.server
            )
            prop_file.chmod(0o644)
            log_path = workdir / f"{class_name}-{len(servers)}.log"
            log = log_path.open("w")
            if launch.how == "image":
                cmd = [part.replace("{workdir}", str(workdir)) for part in launch.cmd]
                cmd += [
                    "introspect",
                    f"-file={launch.prop_dir}/{prop_name}",
                    "-ORBendPoint",
                    f"giop:tcp::{port}",
                    "-ORBendPointPublish",
                    f"giop:tcp:127.0.0.1:{port}",
                ]
                where = representative.launch["image"]
            else:
                cmd = [
                    *launch.cmd,
                    "introspect",
                    f"-file={prop_file}",
                    "-ORBendPoint",
                    f"giop:tcp:127.0.0.1:{port}",
                ]
                where = "Python" if launch.how == "python" else launch.cmd[0]
            report(f"  starting {representative.trl} ({class_name}) from {where}")
            process = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                cwd=config.root,
                env=launch.env,
                start_new_session=True,
            )
            servers.append(
                _Server(
                    key,
                    representative.trl,
                    class_name,
                    port,
                    process,
                    log_path,
                    launch.timeout,
                    launch.container,
                )
            )

        for server in servers:
            try:
                server.wait_ready(server.timeout)
                proxy = tango.DeviceProxy(
                    f"tango://127.0.0.1:{server.port}/{server.trl}#dbase=no"
                )
                interface = query_interface(proxy)
            except (IntrospectionError, tango.DevFailed) as exc:
                errors.append(f"{server.trl} ({server.class_name}): {exc}")
                continue
            finally:
                server.stop()
            key_id = interface_id(interface)
            snapshot.interfaces[key_id] = interface
            for member in groups[server.group_key]:
                member.interface = key_id
    finally:
        for server in servers:
            server.stop()

    snapshot.source = "local"
    return snapshot, errors


# --------------------------------------------------------------------------
# Live introspection
# --------------------------------------------------------------------------


def introspect_live(
    config: Config, devices: list[DeviceInstance] | None = None
) -> tuple[Snapshot, list[str]]:
    """
    Query devices running behind ``TANGO_HOST``.

    If ``devices`` is empty, every exported device in the database (other
    than the Tango system devices) is used.
    """
    tango = _import_tango()
    if config.tango_host:
        os.environ["TANGO_HOST"] = config.tango_host
    errors: list[str] = []
    if not devices:
        db = tango.Database()
        devices = []
        for trl in db.get_device_exported("*"):
            if trl.lower().startswith(("dserver/", "sys/")):
                continue
            devices.append(
                DeviceInstance(trl=trl.lower(), class_name=db.get_class_for_device(trl))
            )
    snapshot = Snapshot(devices=devices, source="live")
    for device in devices:
        try:
            interface = query_interface(tango.DeviceProxy(device.trl))
        except tango.DevFailed as exc:
            errors.append(f"{device.trl}: {exc.args[0].desc.strip()}")
            continue
        key_id = interface_id(interface)
        snapshot.interfaces.setdefault(key_id, interface)
        device.interface = key_id
        device.class_name = interface.class_name
    return snapshot, errors
