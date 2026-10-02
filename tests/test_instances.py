"""Tests for finding device instances in helm values."""

from ska_taranta_setup.config import load_config
from ska_taranta_setup.instances import discover_instances, find_devices_in_values
from ska_taranta_setup.introspect import (
    find_class_modules,
    sanitise_properties,
    write_property_file,
)


def test_ska_tango_devices_layout():
    """``ska-tango-devices.devices.<Class>.<trl>: props``, at any depth."""
    values = {
        "ska-tango-devices": {
            "devices": {
                "SatWhiteRabbit": {
                    "low-sat/grandmaster/1": {"Model": "Z16-SWITCH-V5_2"},
                    "not-a-trl": {},
                }
            }
        }
    }
    (dev,) = find_devices_in_values({"umbrella": values})
    assert (dev.trl, dev.class_name, dev.properties) == (
        "low-sat/grandmaster/1",
        "SatWhiteRabbit",
        {"Model": "Z16-SWITCH-V5_2"},
    )


def test_ska_tango_util_layout():
    """The older ``deviceServers ... classes: [...]`` layout."""
    values = {
        "deviceServers": {
            "ctl": {
                "server": {
                    "instances": [
                        {
                            "classes": [
                                {
                                    "name": "MccsController",
                                    "devices": [
                                        {
                                            "name": "Low-MCCS/Control/Control",
                                            "properties": [
                                                {
                                                    "name": "Stations",
                                                    "values": ["a", "b"],
                                                },
                                                {"name": "Level", "values": ["5"]},
                                            ],
                                        }
                                    ],
                                }
                            ]
                        }
                    ]
                }
            }
        }
    }
    (dev,) = find_devices_in_values(values)
    assert dev.trl == "low-mccs/control/control"
    assert dev.properties == {"Stations": ["a", "b"], "Level": "5"}


def test_explicit_devices_in_pyproject(tmp_path):
    """``devices`` in config wins over helm."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\n[tool.ska-taranta-setup.devices]\nFoo = ["a/b/c"]\n'
    )
    devices, source = discover_instances(load_config(tmp_path))
    assert source == "pyproject.toml"
    assert [(d.class_name, d.trl) for d in devices] == [("Foo", "a/b/c")]


def test_values_file_fallback(tmp_path):
    """With no helmfile, plain values files (templating stripped) are read."""
    chart = tmp_path / "charts" / "p"
    chart.mkdir(parents=True)
    (chart / "values.yaml").write_text(
        "ska-tango-devices:\n  devices:\n    Foo:\n      a/b/c:\n        X: 1\n"
        "{{- if .Values.x }}\nignored: true\n{{- end }}\n"
    )
    devices, source = discover_instances(load_config(tmp_path))
    assert source == "values files"
    assert devices[0].properties == {"X": 1}


def test_sanitise_properties():
    """Sub-device TRLs are dropped and hosts point at localhost."""
    props = {
        "SatUtcTrl": "low-sat/utc/ci",
        "SubServerTrls": ["a/b/c", "d/e/f"],
        "Host": "wrsimulator-grandmaster-snmp",
        "Model": "Z16",
        "Port": 161,
    }
    assert sanitise_properties(props) == {
        "Host": "127.0.0.1",
        "Model": "Z16",
        "Port": 161,
    }


def test_property_file(tmp_path):
    """Property files declare the device under its real TRL."""
    path = tmp_path / "p"
    write_property_file(path, "Foo", "a/b/c", {"S": 'say "hi"', "L": [1, 2], "B": True})
    text = path.read_text()
    assert 'Foo/introspect/DEVICE/Foo: "a/b/c"' in text
    assert 'a/b/c->S: "say \\"hi\\""' in text
    assert 'a/b/c->L: "1", "2"' in text
    assert 'a/b/c->B: "true"' in text


def test_find_class_modules(tmp_path):
    """Classes are found by parsing source, falling back to scripts."""
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "dev.py").write_text("class MyDevice(Device):\n    pass\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\n[project.scripts]\nOther = "pkg.other:Other.main"\n'
    )
    assert find_class_modules(tmp_path) == {"MyDevice": "pkg.dev", "Other": "pkg.other"}
