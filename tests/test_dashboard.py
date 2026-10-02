"""Tests for layout and the .wj files."""

import json

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
                        assert line.count(":") == 1, (widget["type"], key, value)
                        assert ";" not in line, (widget["type"], key, value)
