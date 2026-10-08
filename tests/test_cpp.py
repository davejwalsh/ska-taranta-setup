"""Tests for C++ (non-Python) projects: discovery, launching, settings file."""

import pytest

from ska_taranta_setup import introspect
from ska_taranta_setup.config import load_config
from ska_taranta_setup.heuristics import device_sections, family_key
from ska_taranta_setup.instances import _launches, from_dsconfig
from ska_taranta_setup.introspect import (
    IntrospectionError,
    plan_launch,
    sanitise_env,
    write_property_file,
)
from ska_taranta_setup.model import AttributeInfo, DeviceInterface
from ska_taranta_setup.scaffold import init_project

# What `helm template` gives for ska-low-rfi-monitor (trimmed).
DSCONFIG = {
    "servers": {
        "RFIMonitor": {
            "m1": {"RFIMonitor": {"low/rfi/m1": {}}},
            "sim": {"RFIMonitor": {"low/rfi/sim": {}}},
        }
    }
}
STATEFULSET = {
    "kind": "StatefulSet",
    "spec": {
        "template": {
            "spec": {
                "containers": [
                    {
                        "image": "artefact.skao.int/ska-low-rfi-monitor:1.0.0",
                        "command": ["/app/bin/RFIMonitor", "m1", "-ORBendPoint", "x"],
                        "env": [
                            {"name": "TANGO_HOST", "value": "tango-databaseds:10000"},
                            {"name": "device_ip", "value": "10.30.10.62"},
                            {"name": "LOG_LEVEL", "value": "debug"},
                        ],
                    }
                ]
            }
        }
    },
}


def _rfi_device():
    (device, _) = from_dsconfig(DSCONFIG)
    device.launch = _launches([STATEFULSET])[(device.server, device.instance)]
    return device


def test_dsconfig_records_server_and_instance():
    """Server and instance names come through from the dsconfig."""
    m1, sim = from_dsconfig(DSCONFIG)
    assert (m1.trl, m1.class_name, m1.server, m1.instance) == (
        "low/rfi/m1",
        "RFIMonitor",
        "RFIMonitor",
        "m1",
    )
    assert sim.instance == "sim"


def test_launch_info_from_the_chart():
    """Image, executable and environment come from the server's StatefulSet."""
    launch = _rfi_device().launch
    assert launch["image"] == "artefact.skao.int/ska-low-rfi-monitor:1.0.0"
    assert launch["executable"] == "/app/bin/RFIMonitor"
    assert launch["env"]["device_ip"] == "10.30.10.62"


def test_sanitise_env():
    """Tango variables go; hosts and IPs (real instruments) point at localhost."""
    env = sanitise_env(
        {"TANGO_HOST": "db:10000", "device_ip": "10.30.10.62", "MODE": "fast"}
    )
    assert env == {"device_ip": "127.0.0.1", "MODE": "fast"}


def test_property_file_uses_server_name(tmp_path):
    """C++ servers are named after their executable, not their class."""
    path = tmp_path / "p"
    write_property_file(path, "RFIMonitor", "low/rfi/m1", {}, server="RFIMonitor")
    assert path.read_text().startswith(
        'RFIMonitor/introspect/DEVICE/RFIMonitor: "low/rfi/m1"'
    )


def test_python_class_wins(tmp_path):
    """A Python device class is run directly."""
    config = load_config(tmp_path)
    launch = plan_launch(config, _rfi_device(), {"RFIMonitor": "pkg.mod"}, 1234, "t")
    assert launch.how == "python"


def test_built_executable_next(tmp_path, monkeypatch):
    """With no Python class, a built executable named after the server is used."""
    exe = tmp_path / "build" / "src" / "RFIMonitor"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(introspect, "docker_available", lambda: True)
    launch = plan_launch(load_config(tmp_path), _rfi_device(), {}, 1234, "t")
    assert (launch.how, launch.cmd, launch.server) == (
        "executable",
        [str(exe)],
        "RFIMonitor",
    )
    assert launch.env["device_ip"] == "127.0.0.1"


def test_container_image_last(tmp_path, monkeypatch):
    """Otherwise the server runs from the deployment's image, with a safe env."""
    monkeypatch.setattr(introspect, "docker_available", lambda: True)
    launch = plan_launch(load_config(tmp_path), _rfi_device(), {}, 4567, "t")
    assert launch.how == "image"
    cmd = " ".join(launch.cmd)
    assert "--entrypoint /app/bin/RFIMonitor" in cmd
    assert "-p 127.0.0.1:4567:4567" in cmd
    assert "-e device_ip=127.0.0.1" in cmd
    assert "TANGO_HOST" not in cmd
    assert launch.cmd[-1] == "artefact.skao.int/ska-low-rfi-monitor:1.0.0"


def test_no_way_to_start_it(tmp_path, monkeypatch):
    """A clear error, pointing at --live, when nothing can start the server."""
    monkeypatch.setattr(introspect, "docker_available", lambda: False)
    with pytest.raises(IntrospectionError, match="discover --live"):
        plan_launch(load_config(tmp_path), _rfi_device(), {}, 1, "t")


def test_settings_file_without_pyproject(tmp_path):
    """C++ projects keep settings in ska-taranta.toml; init writes it."""
    chart = tmp_path / "charts" / "rfi"
    chart.mkdir(parents=True)
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: rfi\nversion: 0.1.0\n")
    (chart / "values.yaml").write_text("global: {}\n")
    (tmp_path / "CMakeLists.txt").write_text("project(RFIMonitor)\n")
    report = init_project(load_config(tmp_path), helm_update=False)
    assert "ska-taranta.toml: written (no pyproject.toml)" in report.changes
    assert not (tmp_path / "pyproject.toml").exists()
    (tmp_path / "ska-taranta.toml").write_text(
        (tmp_path / "ska-taranta.toml").read_text() + '\ntitle = "RFI"\n'
    )
    config = load_config(tmp_path)
    assert (config.title, config.chart) == ("RFI", "charts/rfi")


def test_one_letter_prefixes_are_not_families():
    """fStartHz, fStopHz ... don't make an "F" section."""
    assert family_key("fStartHz") != family_key("fStopHz")
    assert family_key("tsrc1_name") == "tsrc1"


def test_spectra_against_frequency_chart():
    """Spectra with an X-axis spectrum become one chart of traces against it."""
    spec = {"dtype": "DevDouble", "dformat": "SPECTRUM"}
    interface = DeviceInterface(
        "RFIMonitor",
        attributes=[
            AttributeInfo(name="xValues", **spec),
            AttributeInfo(name="yValues", **spec),
            AttributeInfo(name="maxHold", **spec),
            AttributeInfo(name="peakLeveldBm", dtype="DevDouble"),
        ],
    )
    sections = device_sections("d", interface)
    charts = [chart for s in sections for _, chart in s.charts]
    (chart,) = charts
    assert chart["type"] == "SPECTRUM_2D"
    assert chart["inputs"]["attributeX"]["attribute"] == "xvalues"
    assert [a["attribute"]["attribute"] for a in chart["inputs"]["attributes"]] == [
        "yvalues",
        "maxhold",
    ]
    # ...and not also as separate index plots.
    assert not any(x["type"] == "SPECTRUM" for s in sections for x in s.widgets)
