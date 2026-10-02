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
    status_widgets,
    summary_sections,
    trend_plots,
)
from ska_taranta_setup.hierarchy import Hierarchy, Subsystem, build_hierarchy
from ska_taranta_setup.model import DeviceInstance, Snapshot
from ska_taranta_setup.options import GenerateOptions, SubsystemSpec

#: Taranta version written into the file (the importer reads it).
TARANTA_FILE_VERSION = "2.18.9"
#: Fixed timestamps keep regenerated files diff-friendly; Taranta replaces
#: them when the dashboard is imported.
TIMESTAMP = "2026-01-01T00:00:00.000Z"


@dataclass
class LayoutOptions:
    """
    Layout sizes, in pixels, converted to Taranta grid units.

    Taranta positions widgets on a grid of ``MIN_WIDGET_SIZE`` pixels, which
    the SKA Taranta image sets to 10 (the upstream default is 20), so sizes are
    given in pixels and divided by ``tile_size``.
    """

    tile_size: int = 10
    columns: int = 4
    section_width_px: float = 440
    gap_px: float = 30
    row_px: float = 38
    big_slots: int = 5
    margin_px: float = 14
    header_px: float = 56
    #: BOX title padding, in units; Taranta adds it above and below the title.
    title_padding: float = 0.25
    dials_per_row: int = 3
    #: Width of a navigation button.
    link_px: float = 170
    #: Height of one item in a dropdown's menu, and the menu's own padding.
    menu_item_px: float = 32
    menu_padding_px: float = 16

    def units(self, px: float) -> float:
        """Pixels to grid units."""
        return round(px / self.tile_size, 2)

    @property
    def section_width(self) -> float:
        """Section width in units."""
        return self.units(self.section_width_px)

    @property
    def gap(self) -> float:
        """Gap between sections in units."""
        return self.units(self.gap_px)

    @property
    def row_height(self) -> float:
        """Height of one small-widget slot in units."""
        return self.units(self.row_px)

    @property
    def margin(self) -> float:
        """Spare space at the bottom of each section in units."""
        return self.units(self.margin_px)

    @property
    def header_height(self) -> float:
        """Height of the dashboard title band in units."""
        return self.units(self.header_px)

    @property
    def title_height(self) -> float:
        """Height Taranta gives a BOX title (text size 1), in units."""
        size = 1 + 2 * self.title_padding
        return size * 2 if self.tile_size < 15 else size

    @property
    def page_width(self) -> float:
        """Width of a full row of sections, in units."""
        return round(self.columns * (self.section_width + self.gap) - self.gap, 2)

    @property
    def link_width(self) -> float:
        """Width of a navigation button, in units."""
        return self.units(self.link_px)

    @property
    def border(self) -> float:
        """A 1px BOX border in units."""
        return 1 / self.tile_size


#: Taranta's "custom height" marker for a BOX child (shared/utils/canvas.js).
CUSTOM_HEIGHT = -1


def _height(widget: dict[str, Any], opts: LayoutOptions) -> float:
    """A widget's height in a vertical box, in units."""
    slots = opts.big_slots if widget["type"] in w.BIG_WIDGETS else 1
    rows = widget.get(w.MENU_ROWS, 0)
    menu = rows * opts.menu_item_px + (opts.menu_padding_px if rows else 0)
    return round(slots * opts.row_height + opts.units(menu), 2)


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


def section_box(
    section: Section, device: str, opts: LayoutOptions, max_plots: int = 2
) -> dict[str, Any]:
    """Render a section as a sized BOX widget (not yet positioned)."""
    children = [
        *section.widgets,
        *_dial_rows(section, opts),
        *trend_plots(section, device, max_plots),
    ]
    box = w.box(
        section.title, children, big_slot=opts.big_slots, padding=opts.title_padding
    )
    title = opts.title_height if section.title else 0
    box["width"] = opts.section_width
    box["height"] = round(
        title + sum(_height(child, opts) for child in children) + opts.margin, 2
    )
    return box


def _position_children(box: dict[str, Any], opts: LayoutOptions) -> None:
    """Fill in child geometry the way Taranta's BOX arranges it."""
    children = box.get("innerWidgets", [])
    if not children:
        return
    border = box["inputs"]["borderWidth"] / opts.tile_size
    if box["inputs"]["layout"] == "vertical":
        y = box["y"] + (opts.title_height if box["inputs"]["title"] else 0) + border
        for child in children:
            child["x"] = round(box["x"] + border, 2)
            child["y"] = round(y, 2)
            child["width"] = round(box["width"] - 2 * border, 2)
            child["height"] = _height(child, opts)
            # Fix each child's height, rather than letting Taranta share the
            # box's height out equally, so dropdowns can be taller than rows.
            child["percentage"] = CUSTOM_HEIGHT
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
    columns = max(1, opts.columns)
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
        widget.pop(w.MENU_ROWS, None)
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


def bottom(widgets_: list[dict[str, Any]]) -> float:
    """The lowest edge of some placed widgets."""
    return max((x["y"] + x["height"] for x in widgets_), default=0.0)


def _place(widget: dict[str, Any], x: float, y: float, width: float, height: float):
    widget.update(x=round(x, 2), y=round(y, 2), width=round(width, 2))
    widget["height"] = round(height, 2)
    return widget


def header(
    text: str,
    opts: LayoutOptions,
    links: list[tuple[str, str]] = (),
    top: float | None = None,
    size: float = 1.4,
    background: str = "#dfe6ee",
) -> list[dict[str, Any]]:
    """
    A full-width title band, with navigation buttons at its right end.

    ``links`` are ``(dashboard name, button text)`` pairs.
    """
    top = opts.gap / 2 if top is None else top
    height = opts.header_height - opts.gap / 2
    link_space = len(links) * (opts.link_width + opts.gap / 2)
    title = w.label(text, size=size, background=background)
    title["inputs"]["customCss"] = w.css(padding="0 12px", box_sizing="border-box")
    widgets_ = [_place(title, opts.gap, top, opts.page_width - link_space, height)]
    x = opts.gap + opts.page_width - link_space + opts.gap / 2
    for name, label in links:
        link = w.dashboard_link(name, label)
        widgets_.append(_place(link, x, top, opts.link_width, height))
        x += opts.link_width + opts.gap / 2
    return widgets_


def full_trl(tango_db: str, trl: str) -> str:
    """The device name as Taranta stores it, e.g. ``taranta://low-sat/control/ci``."""
    return f"{tango_db}://{trl}" if tango_db else trl


def short_name(trl: str) -> str:
    """A device's TRL without its domain, e.g. ``grandmaster/1``."""
    return trl.split("/", 1)[-1]


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------


@dataclass
class Context:
    """Everything a page builder needs."""

    title: str
    snapshot: Snapshot
    tango_db: str
    layout: LayoutOptions
    options: GenerateOptions
    hierarchy: Hierarchy

    def name(self, page: str) -> str:
        """Dashboard name for a page: overview, subsystem name, or device TRL."""
        return f"{self.title} - {page}"

    @property
    def overview_name(self) -> str:
        """The overview dashboard's name."""
        return self.name("Overview")

    def device_link(self, device: DeviceInstance) -> list[tuple[str, str]]:
        """A link to a device's own page, if those are generated."""
        if not self.options.device_dashboards:
            return []
        return [(self.name(device.trl), "Details")]


def status_bar(
    ctx: Context, devices: list[DeviceInstance], top: float
) -> list[dict[str, Any]]:
    """
    Rows of per-device status cells (state + headline LEDs) across the page.

    Cells are top-level boxes of equal height: shorter ones are padded with
    blank rows, since Taranta stretches a box's widgets to fill its height.
    """
    opts = ctx.layout
    contents = []
    for device in devices:
        interface = ctx.snapshot.interface_for(device)
        if interface is not None:
            trl = full_trl(ctx.tango_db, device.trl)
            contents.append((device, status_widgets(trl, interface, ctx.options)))
    if not contents:
        return []
    per_row = max(1, ctx.options.status_bar_columns)
    width = (opts.page_width - (per_row - 1) * opts.gap / 2) / per_row
    slots = max(len(widgets_) for _, widgets_ in contents)
    height = round(opts.title_height + slots * opts.row_height + opts.margin, 2)
    cells = []
    for i, (device, widgets_) in enumerate(contents):
        padding = [
            w.label("", background="#ffffff") for _ in range(slots - len(widgets_))
        ]
        cell = w.box(
            short_name(device.trl), widgets_ + padding, padding=opts.title_padding
        )
        row, col = divmod(i, per_row)
        _place(
            cell,
            opts.gap + col * (width + opts.gap / 2),
            top + row * (height + opts.gap / 2),
            width,
            height,
        )
        _position_children(cell, opts)
        cells.append(cell)
    return cells


#: Device-section widgets kept on summary pages (by attribute name).
SUMMARY_DEVICE_ATTRIBUTES = {
    "healthstate", "status", "adminmode", "controlmode", "obsstate", "simulationmode",
}  # fmt: skip


def _compact_device_section(section: Section) -> Section:
    """The "Device" section with just state, health, status and modes."""
    compact = Section(section.title)
    for widget in section.widgets:
        ref = widget["inputs"].get("attribute") or {}
        if widget["type"] == "DEVICE_STATUS" or (
            ref.get("attribute") in SUMMARY_DEVICE_ATTRIBUTES
        ):
            compact.widgets.append(widget)
    return compact


def device_band(
    ctx: Context, device: DeviceInstance, sections: list[Section], top: float
) -> list[dict[str, Any]]:
    """A device's name bar, with its sections packed in columns below it."""
    opts = ctx.layout
    trl = full_trl(ctx.tango_db, device.trl)
    band = header(
        f"{device.trl}  ({device.class_name})",
        opts,
        ctx.device_link(device),
        top=top,
        size=1.1,
        background="#eef1f5",
    )
    boxes = [
        section_box(s, trl, opts, ctx.options.max_plots_per_section) for s in sections
    ]
    return band + pack(boxes, opts, bottom(band) + opts.gap / 2)


def _sections(ctx: Context, device: DeviceInstance) -> list[Section]:
    interface = ctx.snapshot.interface_for(device)
    if interface is None:
        return []
    return device_sections(full_trl(ctx.tango_db, device.trl), interface, ctx.options)


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


def device_dashboard(ctx: Context, device: DeviceInstance) -> dict[str, Any] | None:
    """A detailed dashboard for one device, linked back up the hierarchy."""
    sections = _sections(ctx, device)
    if not sections:
        return None
    opts = ctx.layout
    links = [(ctx.overview_name, "Overview")]
    subsystem = ctx.hierarchy.subsystem_of(device)
    if subsystem is not None:
        links.append((ctx.name(subsystem.name), subsystem.name))
    top = header(f"{ctx.title} — {device.trl}  ({device.class_name})", opts, links)
    trl = full_trl(ctx.tango_db, device.trl)
    boxes = [
        section_box(s, trl, opts, ctx.options.max_plots_per_section) for s in sections
    ]
    return dashboard_file(
        ctx.name(device.trl), [*top, *pack(boxes, opts, opts.header_height)]
    )


def subsystem_dashboard(ctx: Context, subsystem: Subsystem) -> dict[str, Any]:
    """A status bar for the subsystem, then a band per device."""
    opts = ctx.layout
    widgets_ = header(
        f"{ctx.title} — {subsystem.name}", opts, [(ctx.overview_name, "Overview")]
    )
    widgets_ += status_bar(ctx, subsystem.devices, bottom(widgets_) + opts.gap / 2)
    for device in subsystem.devices:
        sections = _sections(ctx, device)
        if not sections:
            continue
        if subsystem.detail == "summary":
            sections = summary_sections(sections, ctx.options.summary_sections)
            sections[0] = _compact_device_section(sections[0])
        widgets_ += device_band(ctx, device, sections, bottom(widgets_) + opts.gap)
    return dashboard_file(ctx.name(subsystem.name), widgets_)


def _tile(ctx: Context, title: str, widgets_: list[dict[str, Any]]) -> dict[str, Any]:
    section = Section(title)
    section.widgets = widgets_
    return section_box(section, "", ctx.layout)


def overview_dashboard(ctx: Context, devices: list[DeviceInstance]) -> dict[str, Any]:
    """A status bar for everything, then a tile per subsystem and device."""
    opts = ctx.layout
    widgets_ = header(f"{ctx.title} — Overview", opts)
    widgets_ += status_bar(ctx, devices, bottom(widgets_) + opts.gap / 2)

    def links(targets: list[tuple[str, str]]) -> list[dict[str, Any]]:
        return [w.dashboard_link(name, text) for name, text in targets]

    tiles = []
    hierarchy = ctx.hierarchy
    for device in [*hierarchy.umbrellas, *hierarchy.standalone]:
        interface = ctx.snapshot.interface_for(device)
        if interface is None:
            continue
        trl = full_trl(ctx.tango_db, device.trl)
        tiles.append(
            _tile(
                ctx,
                device.trl,
                overview_widgets(trl, interface, ctx.options)
                + links(ctx.device_link(device)),
            )
        )
    for subsystem in hierarchy.subsystems:
        members = []
        for device in subsystem.devices:
            interface = ctx.snapshot.interface_for(device)
            if interface is None:
                continue
            trl = full_trl(ctx.tango_db, device.trl)
            # State with the device's name, then its first headline (health).
            headline = status_widgets(trl, interface, ctx.options)[1:2]
            members += [w.device_status(trl), *headline]
        target = [(ctx.name(subsystem.name), f"Open {subsystem.name}")]
        tiles.append(_tile(ctx, subsystem.name, members + links(target)))
    widgets_ += pack(tiles, opts, bottom(widgets_) + opts.gap)
    return dashboard_file(ctx.overview_name, widgets_)


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
    options: GenerateOptions | None = None,
    subsystems: list[SubsystemSpec] | None = None,
) -> list[Path]:
    """Generate the overview, subsystem and device dashboards; return the files."""
    options = options or GenerateOptions()
    devices = [d for d in devices if snapshot.interface_for(d) is not None]
    ctx = Context(
        title=title,
        snapshot=snapshot,
        tango_db=tango_db,
        layout=opts or LayoutOptions(),
        options=options,
        hierarchy=build_hierarchy(devices, options, subsystems),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = slugify(title)
    written = []

    def save(stem: str, content: dict[str, Any]) -> None:
        path = out_dir / f"{prefix}-{stem}.wj"
        path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
        written.append(path)

    save("overview", overview_dashboard(ctx, devices))
    for subsystem in ctx.hierarchy.subsystems:
        save(
            f"subsystem-{slugify(subsystem.name)}", subsystem_dashboard(ctx, subsystem)
        )
    if options.device_dashboards:
        for device in devices:
            content = device_dashboard(ctx, device)
            if content is not None:
                save(slugify(device.trl), content)
    return written
