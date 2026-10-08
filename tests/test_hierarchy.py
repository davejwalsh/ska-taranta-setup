"""Tests for subsystems, linked pages and generation options."""

import json
from pathlib import Path

import pytest

from ska_taranta_setup.config import load_config
from ska_taranta_setup.dashboard import write_dashboards
from ska_taranta_setup.heuristics import Section, device_sections, place_attribute
from ska_taranta_setup.hierarchy import build_hierarchy, device_references
from ska_taranta_setup.model import AttributeInfo, DeviceInstance
from ska_taranta_setup.options import GenerateOptions, OptionsError, SubsystemSpec
from ska_taranta_setup.widgets import THEMES


def _all(widgets):
    for widget in widgets:
        yield widget
        yield from _all(widget.get("innerWidgets", []))


def _heading(box):
    """A section box's name: the text of its heading strip (first child)."""
    first = (box.get("innerWidgets") or [{}])[0]
    return first.get("inputs", {}).get("text") if first.get("type") == "LABEL" else None


def _devices(snapshot):
    return [d for d in snapshot.devices if d.interface]


def _links(path):
    return [
        json.loads(x["inputs"]["DefaultDashboard"])["name"]
        for x in _all(json.loads(path.read_text())["widget"])
        if x["type"] == "DASHLINK"
    ]


def test_references_from_properties(sat_lmc_snapshot):
    """TRL-valued properties become parent -> child links."""
    refs = device_references(_devices(sat_lmc_snapshot))
    assert refs == {
        "low-sat/control/ci": ["low-sat/utc/ci"],
        "low-sat/utc/ci": ["low-sat/endnode/ci-1", "low-sat/grandmaster/1"],
    }


def test_auto_hierarchy(sat_lmc_snapshot):
    """UTC gets a page; the controller is an umbrella; the clock stands alone."""
    hierarchy = build_hierarchy(_devices(sat_lmc_snapshot), GenerateOptions())
    (utc,) = hierarchy.subsystems
    assert utc.name == "UTC"
    assert [d.trl for d in utc.devices] == [
        "low-sat/utc/ci",
        "low-sat/endnode/ci-1",
        "low-sat/grandmaster/1",
    ]
    assert [d.trl for d in hierarchy.umbrellas] == ["low-sat/control/ci"]
    assert [d.trl for d in hierarchy.standalone] == ["low-sat/meridianclock/1"]


def test_configured_hierarchy(sat_lmc_snapshot):
    """Hand-defined subsystems replace the automatic ones."""
    specs = [
        SubsystemSpec("Timing", ["low-sat/grandmaster/.*", "low-sat/endnode/.*"]),
        SubsystemSpec("Clocks", ["low-sat/meridianclock/.*"], detail="full"),
    ]
    hierarchy = build_hierarchy(_devices(sat_lmc_snapshot), GenerateOptions(), specs)
    assert [(s.name, s.root.trl, s.detail) for s in hierarchy.subsystems] == [
        ("Timing", "low-sat/grandmaster/1", "summary"),
        ("Clocks", "low-sat/meridianclock/1", "full"),
    ]
    assert {d.trl for d in hierarchy.standalone} == {
        "low-sat/control/ci",
        "low-sat/utc/ci",
    }


def test_pages_link_to_each_other(tmp_path, sat_lmc_snapshot):
    """Overview -> subsystem -> device pages, and back up again."""
    files = {
        p.name: p
        for p in write_dashboards(
            "S", _devices(sat_lmc_snapshot), sat_lmc_snapshot, tmp_path, "taranta"
        )
    }
    assert "s-subsystem-utc.wj" in files
    names = {json.loads(p.read_text())["name"] for p in files.values()}
    # Every link points at a dashboard that's generated.
    for path in files.values():
        assert set(_links(path)) <= names, path.name
    assert "S - UTC" in _links(files["s-overview.wj"])
    assert _links(files["s-subsystem-utc.wj"]) == [
        "S - Overview",
        "S - low-sat/utc/ci",
        "S - low-sat/endnode/ci-1",
        "S - low-sat/grandmaster/1",
    ]
    assert _links(files["s-low-sat-grandmaster-1.wj"]) == ["S - Overview", "S - UTC"]
    assert _links(files["s-low-sat-meridianclock-1.wj"]) == ["S - Overview"]


def test_subsystem_page_has_status_bar_and_bands(tmp_path, sat_lmc_snapshot):
    """A status cell per member device on top, then a band per device."""
    write_dashboards(
        "S", _devices(sat_lmc_snapshot), sat_lmc_snapshot, tmp_path, "taranta"
    )
    widgets = json.loads((tmp_path / "s-subsystem-utc.wj").read_text())["widget"]
    titles = [_heading(x) for x in widgets if x["type"] == "BOX"]
    assert titles[:3] == ["utc/ci", "endnode/ci-1", "grandmaster/1"]
    cells = [x for x in widgets if x["type"] == "BOX"][:3]
    assert len({c["height"] for c in cells}) == 1  # padded to the same height
    # Cells sit on the grid: two per section column, left-justified.
    xs = [c["x"] for c in cells]
    assert xs[0] < xs[1] < xs[2]
    bands = [
        x["inputs"]["text"]
        for x in widgets
        if x["type"] == "LABEL" and x["inputs"]["text"]
    ]
    assert [b.split()[0] for b in bands[1:]] == [
        "low-sat/utc/ci",
        "low-sat/endnode/ci-1",
        "low-sat/grandmaster/1",
    ]
    # Each band starts with the device's (compact, blue) Device section.
    device_boxes = [
        x for x in widgets if x["type"] == "BOX" and _heading(x) == "Device"
    ]
    assert len(device_boxes) == 3
    device_tint = THEMES["device"][1]
    assert {x["inputs"]["backgroundColor"] for x in device_boxes} == {device_tint}
    # Summary detail: the grandmaster's 16 NET WRn port sections stay on its page.
    assert not any(t and t.startswith("Net WR") for t in titles)


def test_full_detail_includes_everything(tmp_path, sat_lmc_snapshot):
    """Detail = 'full' puts every section of every device on the page."""
    specs = [SubsystemSpec("GM", ["low-sat/grandmaster/1"], detail="full")]
    write_dashboards(
        "S",
        _devices(sat_lmc_snapshot),
        sat_lmc_snapshot,
        tmp_path,
        "t",
        subsystems=specs,
    )
    widgets = json.loads((tmp_path / "s-subsystem-gm.wj").read_text())["widget"]
    titles = [_heading(x) for x in widgets if x["type"] == "BOX"]
    sections = [t for t in titles if t and " · " not in t]
    trends = [t for t in titles if t and " · " in t]
    assert sum(1 for t in sections if t.startswith("Net WR")) == 16
    # Full detail includes trend plots, but not one per repeated port block.
    assert trends
    assert not any(t.startswith("Net WR") for t in trends)


def test_no_device_pages(tmp_path, sat_lmc_snapshot):
    """device_dashboards = false: no device pages and no Details links."""
    files = write_dashboards(
        "S",
        _devices(sat_lmc_snapshot),
        sat_lmc_snapshot,
        tmp_path,
        "t",
        options=GenerateOptions(device_dashboards=False),
    )
    assert sorted(p.name for p in files) == ["s-overview.wj", "s-subsystem-utc.wj"]
    names = {json.loads(p.read_text())["name"] for p in files}
    for path in files:
        assert set(_links(path)) <= names


def test_widget_overrides_and_excludes():
    """Forced widget kinds, hidden attributes and excluded commands."""
    options = GenerateOptions(
        widgets={"pwsl_temp": "display", "hostname": "hide", ".*_vin": "dial"},
        exclude_attributes=["serial_.*"],
        exclude_commands=["GetVersionInfo"],
        plots=False,
    )
    section = Section("s")
    for name in ("pwsl_temp", "hostname", "pwsl_vin"):
        attr = AttributeInfo(name=name, dtype="DevDouble", max_alarm=50)
        place_attribute(section, "d", attr, options=options)
    assert [x["type"] for x in section.widgets] == ["ATTRIBUTE_DISPLAY"]
    assert [x["type"] for x in section.dials] == ["ATTRIBUTE_DIAL"]
    assert section.trends == {}

    from ska_taranta_setup.model import CommandInfo, DeviceInterface

    interface = DeviceInterface(
        "X",
        attributes=[AttributeInfo(name="serial_number"), AttributeInfo(name="other")],
        commands=[CommandInfo(name="GetVersionInfo"), CommandInfo(name="Reset")],
    )
    sections = device_sections("d", interface, options)
    shown = {
        x["inputs"].get("attribute", {}).get("attribute")
        for s in sections
        for x in s.widgets
        if isinstance(x["inputs"].get("attribute"), dict)
    }
    assert "serial_number" not in shown
    commands = [
        x["inputs"]["command"]["command"]
        for s in sections
        for x in s.widgets
        if x["type"] == "COMMAND"
    ]
    assert commands == ["Reset"]


def test_options_reject_typos(tmp_path):
    """A misspelt option is an error with a suggestion, not silently ignored."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\n[tool.ska-taranta-setup.generate]\nplot = false\n'
    )
    with pytest.raises(OptionsError, match="did you mean 'plots'"):
        load_config(tmp_path)


def test_config_subsystems(tmp_path):
    """[[subsystems]] and [generate] tables are read from pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\n'
        "[tool.ska-taranta-setup.generate]\n"
        'subsystem_detail = "full"\nheadline_status = 1\n'
        "[[tool.ska-taranta-setup.subsystems]]\n"
        'name = "UTC"\ndevices = ["low-sat/utc/.*"]\n'
    )
    config = load_config(tmp_path)
    assert config.generate.subsystem_detail == "full"
    assert config.generate.headline_status == 1
    assert config.subsystems == [SubsystemSpec("UTC", ["low-sat/utc/.*"])]


@pytest.mark.parametrize(
    "bad",
    [
        {"subsystems": "sometimes"},
        {"subsystem_detail": "lots"},
        {"widgets": {"x": "sparkline"}},
    ],
)
def test_invalid_choices(bad):
    """Bad values for choice options are rejected."""
    with pytest.raises(OptionsError):
        GenerateOptions(**bad)


def test_standalone_device_without_interface_is_skipped(tmp_path, sat_lmc_snapshot):
    """Devices that failed introspection don't break page generation."""
    devices = [*_devices(sat_lmc_snapshot), DeviceInstance("x/y/z", "Broken")]
    files = write_dashboards("S", devices, sat_lmc_snapshot, tmp_path, "t")
    assert not any("x-y-z" in p.name for p in files)


def test_cli_device_pages_toggle(tmp_path):
    """`generate --no-device-pages` writes only the overview and subsystem pages."""
    from click.testing import CliRunner

    from ska_taranta_setup.cli import main

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\n')
    (tmp_path / "taranta").mkdir()
    fixture = Path(__file__).parent / "fixtures" / "sat-lmc-devices.json"
    (tmp_path / "taranta" / "devices.json").write_text(fixture.read_text())
    runner = CliRunner()
    result = runner.invoke(main, ["-C", str(tmp_path), "generate", "--no-device-pages"])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in (tmp_path / "dashboards").glob("*.wj")) == [
        "p-overview.wj",
        "p-subsystem-utc.wj",
    ]
    result = runner.invoke(main, ["-C", str(tmp_path), "generate"])
    assert len(list((tmp_path / "dashboards").glob("*.wj"))) == 7


def test_link_target_is_json(tmp_path, sat_lmc_snapshot):
    """DASHLINK parses its target as JSON {name, id}; a bare name does nothing."""
    write_dashboards("S", _devices(sat_lmc_snapshot), sat_lmc_snapshot, tmp_path, "t")
    for path in tmp_path.glob("*.wj"):
        for x in _all(json.loads(path.read_text())["widget"]):
            if x["type"] == "DASHLINK":
                target = json.loads(x["inputs"]["DefaultDashboard"])
                assert set(target) == {"name", "id"}
                assert x["inputs"]["HideDropdown"] is True


def test_exclude_attributes_by_class(tmp_path):
    """Per-class excludes remove attributes from dashboards and the listing."""
    from click.testing import CliRunner

    from ska_taranta_setup.cli import main

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\n'
        "[tool.ska-taranta-setup.generate.exclude_attributes_by_class]\n"
        'SatWhiteRabbit = ["hdd.*", "net_wr_sfp_.*"]\n'
    )
    (tmp_path / "taranta").mkdir()
    fixture = Path(__file__).parent / "fixtures" / "sat-lmc-devices.json"
    (tmp_path / "taranta" / "devices.json").write_text(fixture.read_text())
    runner = CliRunner()

    listing = runner.invoke(main, ["-C", str(tmp_path), "attributes", "-d", "endnode"])
    assert listing.exit_code == 0, listing.output
    assert "hdd1_free" in listing.output
    assert 'exclude_attributes_by_class["SatWhiteRabbit"]' in listing.output

    result = runner.invoke(main, ["-C", str(tmp_path), "generate"])
    assert result.exit_code == 0, result.output
    page = (tmp_path / "dashboards" / "p-low-sat-endnode-ci-1.wj").read_text()
    assert "hdd1_free" not in page and "net_wr_sfp_temp" not in page
    assert "net_wr_status" in page  # other NET attributes stay
    # Other classes are untouched.
    utc = (tmp_path / "dashboards" / "p-low-sat-utc-ci.wj").read_text()
    assert "healthstate" in utc
