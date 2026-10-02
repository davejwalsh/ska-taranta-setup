"""
Lay out sections and assemble Taranta dashboard (``.wj``) files.

Each :class:`~ska_taranta_setup.heuristics.Section` becomes a vertical BOX.
Taranta itself arranges a BOX's children (each "small" widget gets
``smallWidget`` slots of height, each "big" one ``bigWidget`` slots), so we
only need to size the BOX; child coordinates are filled in to match, which is
what Taranta's editor would store. Boxes are then packed into columns,
shortest column first.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any

from ska_taranta_setup import widgets as w
from ska_taranta_setup.heuristics import (
    Section,
    device_sections,
    overview_widgets,
    trend_plots,
)
from ska_taranta_setup.model import DeviceInstance, Snapshot

#: Taranta version written into the file (the importer reads it).
TARANTA_FILE_VERSION = "2.18.9"
#: Fixed timestamps keep regenerated files diff-friendly; Taranta replaces
#: them when the dashboard is imported.
TIMESTAMP = "2026-01-01T00:00:00.000Z"


@dataclass
class LayoutOptions:
    """Sizes in Taranta grid units (20px by default)."""

    columns: int = 4
    section_width: float = 18
    gap: float = 1
    row_height: float = 1.5
    big_slots: int = 6
    title_height: float = 1
    margin: float = 0.6
    dials_per_row: int = 3
    header_height: float = 2.5


def _slots(widget: dict[str, Any], opts: LayoutOptions) -> int:
    return opts.big_slots if widget["type"] in w.BIG_WIDGETS else 1


def _dial_rows(section: Section, opts: LayoutOptions) -> list[dict[str, Any]]:
    rows = []
    for start in range(0, len(section.dials), opts.dials_per_row):
        rows.append(
            w.box(
                "",
                section.dials[start : start + opts.dials_per_row],
                layout="horizontal",
                border=0,
            )
        )
    return rows


def section_box(section: Section, device: str, opts: LayoutOptions) -> dict[str, Any]:
    """Render a section as a sized BOX widget (not yet positioned)."""
    children = [
        *section.widgets,
        *_dial_rows(section, opts),
        *trend_plots(section, device),
    ]
    box = w.box(section.title, children, big_slot=opts.big_slots)
    slots = sum(_slots(child, opts) for child in children)
    title = opts.title_height if section.title else 0
    box["width"] = opts.section_width
    box["height"] = round(title + slots * opts.row_height + opts.margin, 2)
    return box


def _position_children(box: dict[str, Any], opts: LayoutOptions) -> None:
    """Fill in child geometry the way Taranta's BOX arranges it."""
    children = box.get("innerWidgets", [])
    if not children:
        return
    border = box["inputs"]["borderWidth"] / 20
    if box["inputs"]["layout"] == "vertical":
        y = box["y"] + (opts.title_height if box["inputs"]["title"] else 0) + border
        for child in children:
            child["x"] = round(box["x"] + border, 2)
            child["y"] = round(y, 2)
            child["width"] = round(box["width"] - 2 * border, 2)
            child["height"] = round(_slots(child, opts) * opts.row_height, 2)
            y += child["height"]
            _position_children(child, opts)
    else:
        width = (box["width"] - 2 * border) / len(children)
        # Round the edges, not the widths, so neighbours tile exactly.
        edges = [
            round(box["x"] + border + i * width, 2) for i in range(len(children) + 1)
        ]
        for i, child in enumerate(children):
            child["x"] = edges[i]
            child["y"] = round(box["y"] + border, 2)
            child["width"] = round(edges[i + 1] - edges[i], 2)
            child["height"] = round(box["height"] - 2 * border, 2)
            _position_children(child, opts)


def pack(
    boxes: list[dict[str, Any]], opts: LayoutOptions, top: float
) -> list[dict[str, Any]]:
    """Place boxes in columns, each into the currently shortest column."""
    columns = max(1, min(opts.columns, len(boxes)))
    heights = [top] * columns
    for box in boxes:
        col = heights.index(min(heights))
        box["x"] = opts.gap + col * (opts.section_width + opts.gap)
        box["y"] = heights[col]
        heights[col] += box["height"] + opts.gap
        _position_children(box, opts)
    return boxes


def _finalise(widgets_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every widget an id and order, as Taranta stores them."""
    ids = count(1)

    def visit(widget: dict[str, Any], order: int) -> dict[str, Any]:
        widget["id"] = str(next(ids))
        widget["canvas"] = "0"
        widget["order"] = order
        widget["valid"] = 1
        for i, child in enumerate(widget.get("innerWidgets", [])):
            visit(child, i)
        return widget

    return [visit(widget, i) for i, widget in enumerate(widgets_)]


def dashboard_file(name: str, widgets_: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap widgets in the top-level structure of an exported ``.wj`` file."""
    return {
        "name": name,
        "version": TARANTA_FILE_VERSION,
        "user": "ska-taranta-setup",
        "insertTime": TIMESTAMP,
        "updateTime": TIMESTAMP,
        "group": None,
        "groupWriteAccess": False,
        "lastUpdatedBy": None,
        "widget": _finalise(widgets_),
        "variables": [],
        "environment": ["*/*"],
        "filters": [],
    }


def _header(text: str, opts: LayoutOptions, columns: int) -> dict[str, Any]:
    header = w.label(text, size=1.4, background="#dfe6ee")
    header.update(
        x=opts.gap,
        y=opts.gap / 2,
        width=columns * (opts.section_width + opts.gap) - opts.gap,
        height=opts.header_height - opts.gap / 2,
    )
    return header


def full_trl(tango_db: str, trl: str) -> str:
    """The device name as Taranta stores it, e.g. ``taranta://low-sat/control/ci``."""
    return f"{tango_db}://{trl}" if tango_db else trl


def device_dashboard(
    title: str,
    device: DeviceInstance,
    snapshot: Snapshot,
    tango_db: str,
    opts: LayoutOptions | None = None,
) -> dict[str, Any] | None:
    """A detailed dashboard for one device."""
    opts = opts or LayoutOptions()
    interface = snapshot.interface_for(device)
    if interface is None:
        return None
    trl = full_trl(tango_db, device.trl)
    boxes = [section_box(s, trl, opts) for s in device_sections(trl, interface)]
    columns = max(1, min(opts.columns, len(boxes)))
    header = _header(f"{title} — {device.trl}  ({device.class_name})", opts, columns)
    return dashboard_file(
        f"{title} - {device.trl}", [header, *pack(boxes, opts, opts.header_height)]
    )


def overview_dashboard(
    title: str,
    devices: list[DeviceInstance],
    snapshot: Snapshot,
    tango_db: str,
    opts: LayoutOptions | None = None,
) -> dict[str, Any]:
    """One summary panel per device."""
    opts = opts or LayoutOptions()
    boxes = []
    for device in devices:
        interface = snapshot.interface_for(device)
        if interface is None:
            continue
        section = Section(device.trl)
        section.widgets = overview_widgets(full_trl(tango_db, device.trl), interface)
        boxes.append(section_box(section, device.trl, opts))
    columns = max(1, min(opts.columns, len(boxes)))
    header = _header(f"{title} — Overview", opts, columns)
    return dashboard_file(
        f"{title} - Overview", [header, *pack(boxes, opts, opts.header_height)]
    )


def slugify(text: str) -> str:
    """A filesystem-friendly name."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def write_dashboards(
    title: str,
    devices: list[DeviceInstance],
    snapshot: Snapshot,
    out_dir: Path,
    tango_db: str,
    opts: LayoutOptions | None = None,
) -> list[Path]:
    """Generate the overview and per-device dashboards; return the files written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = slugify(title)
    written = []

    def save(stem: str, content: dict[str, Any]) -> None:
        path = out_dir / f"{prefix}-{stem}.wj"
        path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
        written.append(path)

    save("overview", overview_dashboard(title, devices, snapshot, tango_db, opts))
    for device in devices:
        content = device_dashboard(title, device, snapshot, tango_db, opts)
        if content is not None:
            save(slugify(device.trl), content)
    return written
