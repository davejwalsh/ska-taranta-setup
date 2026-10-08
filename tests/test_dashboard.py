"""Tests for layout and the .wj files."""

import json

import pytest

from ska_taranta_setup.dashboard import LayoutOptions, write_dashboards
from ska_taranta_setup.preview import write_preview


def _all(widgets):
    for widget in widgets:
        yield widget
        yield from _all(widget.get("innerWidgets", []))


def _overlap(a, b, eps=1e-6):
    return (
        a["x"] < b["x"] + b["width"] - eps
        and b["x"] < a["x"] + a["width"] - eps
        and a["y"] < b["y"] + b["height"] - eps
        and b["y"] < a["y"] + a["height"] - eps
    )


def test_generated_files(tmp_path, sat_lmc_snapshot):
    """Files are importable-shaped, ids unique, and nothing overlaps."""
    devices = [d for d in sat_lmc_snapshot.devices if d.interface]
    files = write_dashboards("SAT", devices, sat_lmc_snapshot, tmp_path, "taranta")
    assert len(files) == len(devices) + 2  # overview + UTC subsystem
    assert files[0].name == "sat-overview.wj"

    for path in files:
        data = json.loads(path.read_text())
        # Fields Taranta's importer requires.
        assert data["name"] and data["user"] and data["updateTime"]
        widgets = list(_all(data["widget"]))
        ids = [w["id"] for w in widgets]
        assert len(ids) == len(set(ids))
        top = data["widget"]
        for i, a in enumerate(top):
            for b in top[i + 1 :]:
                # Navigation buttons are layered on the page banner on purpose.
                if {a["type"], b["type"]} == {"LABEL", "DASHLINK"}:
                    continue
                assert not _overlap(a, b), (path.name, a["id"], b["id"])
        for widget in widgets:
            for child in widget.get("innerWidgets", []):
                assert (
                    child["y"] + child["height"]
                    <= widget["y"] + widget["height"] + 1e-6
                )
                assert (
                    child["x"] + child["width"] <= widget["x"] + widget["width"] + 1e-6
                )
            ref = widget["inputs"].get("attribute")
            if isinstance(ref, dict) and ref.get("device"):
                assert ref["device"].startswith("taranta://")


def test_regeneration_is_stable(tmp_path, sat_lmc_snapshot):
    """Same input, same bytes: keeps committed dashboards diff-friendly."""
    devices = [d for d in sat_lmc_snapshot.devices if d.interface]
    first = [
        p.read_bytes()
        for p in write_dashboards("S", devices, sat_lmc_snapshot, tmp_path / "a", "t")
    ]
    second = [
        p.read_bytes()
        for p in write_dashboards("S", devices, sat_lmc_snapshot, tmp_path / "b", "t")
    ]
    assert first == second


def test_columns_option(tmp_path, sat_lmc_snapshot):
    """Sections pack into the requested number of columns."""
    device = next(
        d for d in sat_lmc_snapshot.devices if d.trl == "low-sat/grandmaster/1"
    )
    opts = LayoutOptions(columns=2)
    (path,) = [
        p
        for p in write_dashboards("S", [device], sat_lmc_snapshot, tmp_path, "t", opts)
        if "grandmaster" in p.name
    ]
    boxes = [w for w in json.loads(path.read_text())["widget"] if w["type"] == "BOX"]
    assert len({b["x"] for b in boxes}) == 2


def test_preview(tmp_path, sat_lmc_snapshot):
    """The preview page has one wireframe per dashboard."""
    devices = [d for d in sat_lmc_snapshot.devices if d.interface]
    files = write_dashboards("S", devices, sat_lmc_snapshot, tmp_path, "t")
    html = write_preview(files, tmp_path / "preview.html").read_text()
    assert html.count("<svg") == len(files)


def test_css_is_one_declaration_per_line(tmp_path, sat_lmc_snapshot):
    """Taranta ignores one-line ``a:1;b:2;`` CSS, so never emit it."""
    devices = [d for d in sat_lmc_snapshot.devices if d.interface]
    for path in write_dashboards("S", devices, sat_lmc_snapshot, tmp_path, "t"):
        for widget in _all(json.loads(path.read_text())["widget"]):
            for key, value in widget["inputs"].items():
                if key.lower().endswith("css") and value:
                    for line in value.split("\n"):
                        # One "property: value" per line; values may contain
                        # colons (data: URIs), never a semicolon.
                        prop, sep, _ = line.partition(":")
                        assert sep and prop and " " not in prop, (key, line)
                        assert ";" not in line, (widget["type"], key, value)


def test_box_children_have_fixed_heights(tmp_path, sat_lmc_snapshot):
    """Every vertical-box child has a fixed height, so dropdown menus fit."""
    devices = [d for d in sat_lmc_snapshot.devices if d.interface]
    for path in write_dashboards("S", devices, sat_lmc_snapshot, tmp_path, "t"):
        for widget in _all(json.loads(path.read_text())["widget"]):
            assert "_menu_rows" not in widget  # layout hint, never saved
            if widget["type"] == "BOX" and widget["inputs"]["layout"] == "vertical":
                for child in widget["innerWidgets"]:
                    assert child["percentage"] == -1
                    if child["type"] == "ATTRIBUTE WRITER DROPDOWN":
                        # Room for the menu, up to 5 items (longer ones scroll).
                        rows = min(len(child["inputs"]["writeValues"]), 5)
                        assert child["height"] >= 3.8 + rows * 3.0


def test_skao_banner(tmp_path, sat_lmc_snapshot):
    """Every page starts with the SKAO banner (with logo) and brand stripe."""
    devices = [d for d in sat_lmc_snapshot.devices if d.interface]
    for path in write_dashboards("S", devices, sat_lmc_snapshot, tmp_path, "t"):
        banner, stripe = json.loads(path.read_text())["widget"][:2]
        assert banner["inputs"]["backgroundColor"] == "#070068"
        assert 'background-image: url("data:image/png,' in banner["inputs"]["customCss"]
        assert (
            "linear-gradient(90deg, #e40769, #070068)" in stripe["inputs"]["customCss"]
        )


def test_banner_off(tmp_path, sat_lmc_snapshot):
    """Banner = false falls back to a plain title bar with no logo."""
    devices = [d for d in sat_lmc_snapshot.devices if d.interface]
    files = write_dashboards(
        "S", devices, sat_lmc_snapshot, tmp_path, "t", LayoutOptions(banner=False)
    )
    assert "data:image" not in files[0].read_text()


def test_trend_titles_dont_repeat():
    """A section named after its quantity gets "Pressure", not "Pressure · Pressure"."""
    from ska_taranta_setup.dashboard import trend_boxes
    from ska_taranta_setup.heuristics import Section, place_attribute
    from ska_taranta_setup.model import AttributeInfo

    pressure = Section("Pressure")
    place_attribute(
        pressure, "d", AttributeInfo(name="pressure", unit="mbar", dtype="DevDouble")
    )
    psu = Section("PSU")
    place_attribute(psu, "d", AttributeInfo(name="psu_vin", dtype="DevDouble"))
    titles = [
        b["innerWidgets"][0]["inputs"]["text"]
        for b in trend_boxes([pressure, psu], "d", LayoutOptions(), 2)
    ]
    assert titles == ["Pressure", "PSU · Voltage"]


@pytest.mark.parametrize(("screen", "columns"), [(1920, 4), (2560, 5), (1366, 3)])
def test_layout_fills_the_screen(screen, columns):
    """Columns and section width are worked out to fill the screen width."""
    opts = LayoutOptions(screen_width_px=screen)
    assert opts.columns == columns
    assert opts.section_width_px >= opts.min_section_px
    used = (opts.left + opts.page_width) * opts.tile_size + opts.gap_px
    assert screen - opts.scrollbar_px - 2 <= used <= screen - opts.scrollbar_px + 2


def test_explicit_columns_still_fill():
    """A fixed column count gets wider sections rather than empty space."""
    opts = LayoutOptions(columns=3, screen_width_px=1920)
    assert opts.columns == 3
    assert opts.section_width_px > 550


def test_gap_under_section_headings(tmp_path, sat_lmc_snapshot):
    """Each section's heading strip is followed by a small blank gap."""
    devices = [d for d in sat_lmc_snapshot.devices if d.interface]
    path = write_dashboards("S", devices, sat_lmc_snapshot, tmp_path, "t")[-1]
    opts = LayoutOptions()
    for box in json.loads(path.read_text())["widget"]:
        kids = box.get("innerWidgets") or []
        if box["type"] == "BOX" and kids and kids[0]["type"] == "LABEL":
            gap = kids[1]
            assert gap["type"] == "LABEL" and gap["inputs"]["text"] == ""
            assert gap["height"] == opts.units(opts.heading_gap_px)
            assert kids[2]["y"] == round(gap["y"] + gap["height"], 2)
