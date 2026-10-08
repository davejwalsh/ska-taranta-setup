"""
Lay out sections and assemble Taranta dashboard (``.wj``) files.

Every page sits on one grid, centred on the screen: ``columns`` columns of
``section_width_px``. Sections are themed BOXes (a coloured heading strip
on a tinted background) placed left to right in rows; the boxes in a row are
stretched to the same height so their tops and bottoms line up.

Taranta arranges a BOX's children itself, so we size each BOX and give each
child a fixed height (its custom-height marker); child coordinates are filled
in to match, as Taranta's editor would store them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from itertools import count
from pathlib import Path
from typing import Any

from ska_taranta_setup import widgets as w
from ska_taranta_setup.heuristics import (
    Section,
    device_sections,
    overview_widgets,
    repeated_blocks,
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
    gap_px: float = 24
    row_px: float = 38
    #: Rows for "big" widgets kept inside sections (the health-info logger).
    big_slots: int = 3
    margin_px: float = 10
    header_px: float = 56
    #: Kept for compatibility; titles are heading strips now.
    title_padding: float = 0.25
    dials_per_row: int = 3
    #: Height of a row of dials.
    dial_px: float = 170
    #: Height of a section's heading strip.
    heading_px: float = 32
    #: Trend plots: height, and how many grid columns each one spans.
    plot_px: float = 320
    plot_span: int = 2
    #: Width of a navigation button.
    link_px: float = 170
    #: Height of one item in a dropdown's menu, and the menu's own padding.
    menu_item_px: float = 32
    menu_padding_px: float = 16
    #: Most menu items to reserve room for; longer menus scroll.
    menu_max_items: int = 5
    #: Padding inside each box, between its frame and its contents.
    inset_px: int = 10
    #: Space between a title bar and what's below it.
    header_gap_px: float = 20
    #: Screen width the page is centred on.
    screen_width_px: float = 1920
    #: The SKAO banner at the top of each page (logo, title, brand stripe).
    banner: bool = True
    #: Logo for the banner: an SVG/PNG/JPEG path (default: the SKAO mark).
    logo: str = ""
    logo_px: int = 34
    banner_px: float = 58
    stripe_px: float = 6

    def units(self, px: float) -> float:
        """Pixels to grid units."""
        return round(px / self.tile_size, 2)

    @property
    def section_width(self) -> float:
        """Section (grid column) width in units."""
        return self.units(self.section_width_px)

    @property
    def gap(self) -> float:
        """Gap between sections in units."""
        return self.units(self.gap_px)

    @property
    def row_height(self) -> float:
        """Height of one row in units."""
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
    def inset(self) -> float:
        """Padding inside each box, in units."""
        return self.units(self.inset_px)

    @property
    def header_gap(self) -> float:
        """Space below a title bar, in units."""
        return self.units(self.header_gap_px)

    @property
    def page_width(self) -> float:
        """Width of a full row of sections, in units."""
        return round(self.columns * (self.section_width + self.gap) - self.gap, 2)

    @property
    def left(self) -> float:
        """Left edge of the page, centring it on the screen."""
        spare = self.units(self.screen_width_px) - self.page_width
        return round(max(self.gap, spare / 2), 2)

    @property
    def link_width(self) -> float:
        """Width of a navigation button, in units."""
        return self.units(self.link_px)

    def column_x(self, column: float) -> float:
        """Left edge of a grid column (fractions allowed), in units."""
        return round(self.left + column * (self.section_width + self.gap), 2)

    def span_width(self, span: int) -> float:
        """Width of a box spanning ``span`` grid columns, in units."""
        return round(span * self.section_width + (span - 1) * self.gap, 2)

    def box_height(self, content: float) -> float:
        """A box's height for ``content`` units of children, in units."""
        return round(content + self.margin + 2 * self.inset, 2)


#: Taranta's "custom height" marker for a BOX child (shared/utils/canvas.js).
CUSTOM_HEIGHT = -1
#: Layout hint (stripped before saving): a widget's height in pixels.
PX = "_px"


def _height(widget: dict[str, Any], opts: LayoutOptions) -> float:
    """A widget's height in a vertical box, in units."""
    if PX in widget:
        return opts.units(widget[PX])
    if widget["type"] in w.BIG_WIDGETS:
        return round(opts.big_slots * opts.row_height, 2)
    rows = min(widget.get(w.MENU_ROWS, 0), opts.menu_max_items)
    menu = rows * opts.menu_item_px + (opts.menu_padding_px if rows else 0)
    return round(widget.get(w.ROWS, 1) * opts.row_height + opts.units(menu), 2)


def _heading(text: str, kind: str, opts: LayoutOptions) -> dict[str, Any]:
    strip = w.heading(text, kind)
    strip[PX] = opts.heading_px
    return strip


def _dial_rows(section: Section, opts: LayoutOptions) -> list[dict[str, Any]]:
    rows = []
    for start in range(0, len(section.dials), opts.dials_per_row):
        row = w.box(
            "",
            section.dials[start : start + opts.dials_per_row],
            layout="horizontal",
            inset=0,
            frame=False,
        )
        row[PX] = opts.dial_px
        rows.append(row)
    return rows


def themed_box(
    title: str, kind: str, children: list[dict[str, Any]], opts: LayoutOptions
) -> dict[str, Any]:
    """A section box: heading strip, then ``children``; sized, not positioned."""
    contents = ([_heading(title, kind, opts)] if title else []) + children
    box = w.box("", contents, big_slot=opts.big_slots, inset=opts.inset_px, kind=kind)
    box["width"] = opts.section_width
    box["height"] = opts.box_height(sum(_height(c, opts) for c in contents))
    return box


def section_box(section: Section, opts: LayoutOptions) -> dict[str, Any]:
    """A section's values, LEDs, controls and dials (its plots go to Trends)."""
    return themed_box(
        section.title,
        section.kind,
        [*section.widgets, *_dial_rows(section, opts)],
        opts,
    )


def trend_boxes(
    sections: list[Section], device: str, opts: LayoutOptions, max_plots: int
) -> list[dict[str, Any]]:
    """
    A wide box per trend plot, titled by section and quantity.

    Repeated blocks (ports, channels: ``Net WR0`` .. ``Net WR15``) are left
    out; their dials and values are in their sections, and a plot each would
    swamp the page.
    """
    boxes = []
    repeated = repeated_blocks(sections)
    for section in [s for s in sections if s.title not in repeated]:
        for quantity, plot in trend_plots(section, device, max_plots):
            plot[PX] = opts.plot_px
            box = themed_box(f"{section.title} · {quantity}", "trends", [plot], opts)
            box["width"] = opts.span_width(opts.plot_span)
            boxes.append(box)
    return boxes


#: Section kinds pinned to the start and end of a page; the rest are ordered by
#: height so that the sections sharing a row are of similar height.
HEAD_KINDS = ("device", "status")
TAIL_KINDS = ("settings", "commands", "expert")


def arrange(sections: list[Section], opts: LayoutOptions) -> list[dict[str, Any]]:
    """
    Section boxes in grid order.

    Rows are stretched to their tallest box, so mixing a tall section with
    short ones leaves empty space. Device and Status lead, Settings, Commands
    and Expert close, and everything between goes tallest first; the tallest
    of those share the first row with Device, which is usually tall too.
    """
    boxes = [(s, section_box(s, opts)) for s in sections]
    head = [b for s, b in boxes if s.kind in HEAD_KINDS]
    tail = sorted(
        ((s, b) for s, b in boxes if s.kind in TAIL_KINDS),
        key=lambda sb: TAIL_KINDS.index(sb[0].kind),
    )
    middle = [b for s, b in boxes if s.kind not in HEAD_KINDS + TAIL_KINDS]
    middle.sort(key=lambda b: -b["height"])  # stable: equal heights keep order
    return [*head, *middle, *(b for _, b in tail)]


def _position_children(box: dict[str, Any], opts: LayoutOptions) -> None:
    """Fill in child geometry the way Taranta's BOX arranges it."""
    children = box.get("innerWidgets", [])
    if not children:
        return
    border = box["inputs"]["borderWidth"] / opts.tile_size
    if box["inputs"]["layout"] == "vertical":
        y = box["y"] + border
        for child in children:
            child["x"] = round(box["x"] + border, 2)
            child["y"] = round(y, 2)
            child["width"] = round(box["width"] - 2 * border, 2)
            child["height"] = _height(child, opts)
            # Fix each child's height, rather than letting Taranta share the
            # box's height out equally.
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


def grid(
    boxes: list[dict[str, Any]], opts: LayoutOptions, top: float
) -> list[dict[str, Any]]:
    """
    Place boxes left to right in rows on the page grid.

    A box ``n`` columns wide (from its width) takes ``n`` grid columns. Every
    box in a row is stretched to the row's tallest, so rows line up.
    """
    row: list[dict[str, Any]] = []
    column = 0
    y = top

    def finish_row() -> float:
        height = max(b["height"] for b in row)
        for b in row:
            b["height"] = height
            _position_children(b, opts)
        return y + height + opts.gap

    for box in boxes:
        span = max(
            1, round((box["width"] + opts.gap) / (opts.section_width + opts.gap))
        )
        span = min(span, opts.columns)
        if row and column + span > opts.columns:
            y = finish_row()
            row, column = [], 0
        box["x"] = opts.column_x(column)
        box["y"] = round(y, 2)
        box["width"] = opts.span_width(span)
        row.append(box)
        column += span
    if row:
        finish_row()
    return boxes


def _finalise(widgets_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every widget an id and order, as Taranta stores them."""
    ids = count(1)

    def visit(widget: dict[str, Any], order: int) -> dict[str, Any]:
        widget["id"] = str(next(ids))
        widget["canvas"] = "0"
        widget["order"] = order
        widget["valid"] = 1
        for hint in (w.MENU_ROWS, w.ROWS, PX):
            widget.pop(hint, None)
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
    size: float = 1.5,
    colours: tuple[str, str] = w.PAGE_TITLE,
    height_px: float | None = None,
) -> list[dict[str, Any]]:
    """
    A full-width title bar, with navigation buttons at its right end.

    ``links`` are ``(dashboard name, button text)`` pairs.
    """
    top = opts.gap / 2 if top is None else top
    height = opts.units(height_px) if height_px else opts.header_height - opts.gap / 2
    link_space = len(links) * (opts.link_width + opts.gap / 2)
    title = w.title_bar(text, size, colours)
    widgets_ = [_place(title, opts.left, top, opts.page_width - link_space, height)]
    x = opts.left + opts.page_width - link_space + opts.gap / 2
    for name, label in links:
        link = w.dashboard_link(name, label)
        widgets_.append(_place(link, x, top, opts.link_width, height))
        x += opts.link_width + opts.gap / 2
    return widgets_


def logo_uri(opts: LayoutOptions) -> str:
    """The banner logo as a data URI: ``opts.logo``, or the bundled SKAO mark."""
    if opts.logo:
        path = Path(opts.logo)
        mime = {
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(path.suffix.lower(), "image/png")
        return w.data_uri(path.read_bytes(), mime)
    asset = resources.files("ska_taranta_setup.templates").joinpath("skao_logo.png")
    return w.data_uri(asset.read_bytes(), "image/png")


def page_header(
    text: str, opts: LayoutOptions, links: list[tuple[str, str]] = ()
) -> list[dict[str, Any]]:
    """
    The standard page header: the SKAO banner over the brand stripe.

    The banner has the circular SKAO logo, the title in white on navy, and the
    navigation buttons; the stripe is SKAO magenta fading to navy.

    With ``banner = false`` it's a plain title bar.
    """
    if not opts.banner:
        return header(text, opts, links)
    top = opts.gap / 2
    height = opts.units(opts.banner_px)
    link_space = len(links) * (opts.link_width + opts.gap / 2)
    title = w.banner(text, logo_uri(opts), opts.logo_px)
    widgets_ = [_place(title, opts.left, top, opts.page_width, height)]
    # Buttons sit inside the right end of the banner.
    x = opts.left + opts.page_width - link_space
    button_height = height * 0.6
    for name, label in links:
        link = w.dashboard_link(name, label)
        widgets_.append(
            _place(
                link,
                x,
                top + (height - button_height) / 2,
                opts.link_width,
                button_height,
            )
        )
        x += opts.link_width + opts.gap / 2
    stripe = w.brand_stripe()
    widgets_.insert(
        1,
        _place(
            stripe, opts.left, top + height, opts.page_width, opts.units(opts.stripe_px)
        ),
    )
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
    Per-device status cells (state + headline LEDs), on the page grid.

    Two cells fit each grid column, so they line up with the sections below.
    Cells are padded with blank rows to the same height, since Taranta would
    otherwise stretch a short cell's widgets to fill it.
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
    per_row = ctx.options.status_bar_columns or 2 * opts.columns
    width = (opts.section_width - opts.gap) / 2
    slots = max(len(widgets_) for _, widgets_ in contents)
    cells = []
    for i, (device, widgets_) in enumerate(contents):
        blank = [w.label("", background=w.CLEAR) for _ in range(slots - len(widgets_))]
        cell = themed_box(short_name(device.trl), "cell", widgets_ + blank, opts)
        row, col = divmod(i, per_row)
        _place(
            cell,
            opts.left + col * (width + opts.gap),
            top + row * (cell["height"] + opts.gap / 2),
            width,
            cell["height"],
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
    compact = Section(section.title, kind=section.kind)
    for widget in section.widgets:
        ref = widget["inputs"].get("attribute") or {}
        if widget["type"] == "DEVICE_STATUS" or (
            ref.get("attribute") in SUMMARY_DEVICE_ATTRIBUTES
        ):
            compact.widgets.append(widget)
    return compact


def device_band(
    ctx: Context,
    device: DeviceInstance,
    sections: list[Section],
    top: float,
    trends: bool = False,
) -> list[dict[str, Any]]:
    """A device's name bar, with its sections on the grid below it."""
    opts = ctx.layout
    band = header(
        f"{device.trl}  ·  {device.class_name}",
        opts,
        ctx.device_link(device),
        top=top,
        size=1.15,
        colours=w.BAND_TITLE,
        height_px=44,
    )
    widgets_ = band + grid(
        arrange(sections, opts), opts, bottom(band) + opts.header_gap
    )
    if trends:
        widgets_ += _trends(ctx, device, sections, bottom(widgets_) + opts.gap)
    return widgets_


def _trends(
    ctx: Context, device: DeviceInstance, sections: list[Section], top: float
) -> list[dict[str, Any]]:
    """A "Trends" bar and the device's plots, each spanning two columns."""
    opts = ctx.layout
    trl = full_trl(ctx.tango_db, device.trl)
    boxes = trend_boxes(sections, trl, opts, ctx.options.max_plots_per_section)
    if not boxes:
        return []
    bar = header("Trends", opts, top=top, size=1.1, colours=w.BAND_TITLE, height_px=40)
    return bar + grid(boxes, opts, bottom(bar) + opts.header_gap)


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
    top = page_header(
        f"{ctx.title} — {device.trl}  ·  {device.class_name}", opts, links
    )
    widgets_ = top + grid(arrange(sections, opts), opts, bottom(top) + opts.header_gap)
    widgets_ += _trends(ctx, device, sections, bottom(widgets_) + opts.gap)
    return dashboard_file(ctx.name(device.trl), widgets_)


def subsystem_dashboard(ctx: Context, subsystem: Subsystem) -> dict[str, Any]:
    """A status bar for the subsystem, then a band per device."""
    opts = ctx.layout
    widgets_ = page_header(
        f"{ctx.title} — {subsystem.name}", opts, [(ctx.overview_name, "Overview")]
    )
    widgets_ += status_bar(ctx, subsystem.devices, bottom(widgets_) + opts.header_gap)
    full = subsystem.detail == "full"
    for device in subsystem.devices:
        sections = _sections(ctx, device)
        if not sections:
            continue
        if not full:
            sections = summary_sections(sections, ctx.options.summary_sections)
            sections[0] = _compact_device_section(sections[0])
        widgets_ += device_band(
            ctx, device, sections, bottom(widgets_) + opts.gap, trends=full
        )
    return dashboard_file(ctx.name(subsystem.name), widgets_)


def overview_dashboard(ctx: Context, devices: list[DeviceInstance]) -> dict[str, Any]:
    """A status bar for everything, then a tile per subsystem and device."""
    opts = ctx.layout
    widgets_ = page_header(f"{ctx.title} — Overview", opts)
    widgets_ += status_bar(ctx, devices, bottom(widgets_) + opts.header_gap)

    def links(targets: list[tuple[str, str]]) -> list[dict[str, Any]]:
        return [w.dashboard_link(name, text) for name, text in targets]

    tiles = []
    hierarchy = ctx.hierarchy
    for device in [*hierarchy.umbrellas, *hierarchy.standalone]:
        interface = ctx.snapshot.interface_for(device)
        if interface is None:
            continue
        trl = full_trl(ctx.tango_db, device.trl)
        children = overview_widgets(trl, interface, ctx.options)
        tiles.append(
            themed_box(
                device.trl, "device", children + links(ctx.device_link(device)), opts
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
        tiles.append(
            themed_box(subsystem.name, "subsystem", members + links(target), opts)
        )
    widgets_ += grid(tiles, opts, bottom(widgets_) + opts.gap)
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
