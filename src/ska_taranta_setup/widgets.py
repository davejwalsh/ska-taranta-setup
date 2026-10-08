"""
Builders for Taranta widget JSON.

The defaults here mirror the ``definition`` objects of the widgets in
``taranta/src/dashboard/widgets`` (Taranta 2.18). Only ``inputs`` that differ
from the widget's own defaults strictly need to be given, but Taranta stores
the full set, so we do too: it keeps the generated files identical in shape
to dashboards exported from the UI.

Sizes and positions are in Taranta grid units (``MIN_WIDGET_SIZE`` pixels:
10 in the SKA Taranta image); see :class:`~ska_taranta_setup.dashboard.LayoutOptions`.
"""

from __future__ import annotations

import json
from typing import Any

from ska_taranta_setup.model import AttributeInfo, CommandInfo

#: Widget types Taranta gives ``bigWidget`` slots to inside a BOX.
BIG_WIDGETS = {
    "ATTRIBUTE_LOGGER",
    "ATTRIBUTE_PLOT",
    "ATTRIBUTE_SCATTER",
    "ATTRIBUTEHEATMAP",
    "BOX",
    "EMBED_PAGE",
    "IMAGEDISPLAY",
    "SPECTRUM",
    "SPECTRUM_2D",
    "TABULAR_VIEW",
    "TIMELINE",
}

GREEN = "#3ac73a"
RED = "#e0524f"


def css(**declarations: str) -> str:
    """
    CSS for a widget's style inputs, e.g. ``css(font_weight="bold")``.

    Taranta's parser takes one ``property: value`` per line (a one-line
    ``a:1;b:2;`` is read as a single broken declaration and ignored).
    """
    return "\n".join(f"{k.replace('_', '-')}: {v}" for k, v in declarations.items())


#: SKAO brand colours (from ska-ser-sphinx-theme, which follows the SKA brand
#: guidelines).
SKAO_NAVY = "#070068"  # Primary Blueshift Navy, rgb(7, 0, 104)
SKAO_MAGENTA = "#e40769"  # Primary Redshift Magenta, rgb(228, 7, 105)
SKAO_MAGENTA_ON_NAVY = "#f81b7f"  # Magenta variant with contrast on navy
SKAO_GREY = "#c2c7ca"  # Tech 2 accent 5, rgb(194, 199, 202)

#: Fonts: headings in the SKA brand font (Noto Sans) where installed, falling
#: back to common sans-serifs; values in Taranta's Helvetica.
HEADING_FONT = '"Noto Sans", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'

#: Section colour themes: (accent for the heading strip and frame, tint for
#: the section's background).
THEMES: dict[str, tuple[str, str]] = {
    "device": (SKAO_NAVY, "#f1f2f9"),
    "status": ("#2e7d4f", "#f0f7f2"),
    "family": ("#4a5a6a", "#f4f6f8"),
    "measurements": ("#6a4c93", "#f6f2fa"),
    "information": ("#6b7280", "#f6f7f8"),
    "settings": ("#b7791f", "#fcf6ec"),
    "commands": ("#5b6abf", "#f3f4fb"),  # neutral indigo: not a warning colour
    "expert": ("#374151", "#f2f3f5"),
    "trends": ("#1f6f8b", "#f0f7fa"),
    "subsystem": (SKAO_NAVY, "#f1f2f9"),
    "cell": ("#4a5a6a", "#f7f9fb"),
}
PAGE_TITLE = (SKAO_NAVY, "#ffffff")  # background, text
BAND_TITLE = ("#e6e8ef", SKAO_NAVY)

#: Applied to every row widget: padding so labels and values don't touch the
#: box edge, and a faint rule between rows so they read as a table.
ROW_CSS = css(
    padding="0 10px",
    box_sizing="border-box",
    border_bottom="1px solid rgba(20, 40, 70, 0.08)",
)
#: Value displays also clip, so an unexpectedly long value can't spill over
#: the row below.
DISPLAY_CSS = ROW_CSS + "\n" + css(overflow="hidden")
#: Rows sit on their section's tinted background.
CLEAR = "transparent"
PLOT_COLOURS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]


def attribute_ref(device: str, attr: AttributeInfo, label: str = "") -> dict[str, Any]:
    """Reference to an attribute, as stored in widget inputs."""
    return {"device": device, "attribute": attr.name.lower(), "label": label}


def widget(
    widget_type: str,
    inputs: dict[str, Any],
    width: float = 10,
    height: float = 2,
) -> dict[str, Any]:
    """A widget with no position yet; layout fills in id, x, y and order."""
    return {
        "type": widget_type,
        "width": width,
        "height": height,
        "inputs": inputs,
    }


def label(
    text: str,
    size: float = 1.0,
    background: str = "#ffffff",
    colour: str = "#1a1a1a",
    style: str = "",
) -> dict[str, Any]:
    """A static text label."""
    return widget(
        "LABEL",
        {
            "text": text,
            "textColor": colour,
            "backgroundColor": background,
            "borderWidth": 0,
            "borderColor": "#000000",
            "font": "Helvetica",
            "automaticResize": "Disabled",
            "size": size,
            "linkTo": "",
            "customCss": style,
        },
    )


FRAME_COLOUR = "#c3cad4"


def heading(text: str, kind: str = "family") -> dict[str, Any]:
    """A section's heading strip: its name on the section's accent colour."""
    accent, _ = THEMES.get(kind, THEMES["family"])
    return label(
        text,
        size=1.05,
        background=accent,
        colour="#ffffff",
        style=css(
            font_family=HEADING_FONT,
            font_weight="600",
            letter_spacing="0.02em",
            padding="0 10px",
            box_sizing="border-box",
            border_radius="4px",
            display="flex",
            align_items="center",
            white_space="nowrap",
        ),
    )


def data_uri(data: bytes, mime: str) -> str:
    """
    An image as a data URI that survives Taranta's CSS parser.

    The parser drops the first ``;`` in a value, which breaks ``;base64``
    URIs, so every byte other than letters and digits is percent-encoded.
    """
    from urllib.parse import quote

    return f"data:{mime},{quote(data, safe='')}"


def banner(text: str, logo: str, logo_px: int, size: float = 1.5) -> dict[str, Any]:
    """The SKAO page banner: logo on the left, title in white on navy."""
    return label(
        text,
        size=size,
        background=SKAO_NAVY,
        colour="#ffffff",
        style=css(
            font_family=HEADING_FONT,
            font_weight="600",
            letter_spacing="0.01em",
            padding=f"0 16px 0 {logo_px + 30}px",
            box_sizing="border-box",
            border_radius="8px 8px 0 0",
            display="flex",
            align_items="center",
            background_image=f'url("{logo}")',
            background_repeat="no-repeat",
            background_position="16px center",
            background_size=f"{logo_px}px {logo_px}px",
        ),
    )


def brand_stripe() -> dict[str, Any]:
    """The SKAO magenta-to-navy stripe under the banner."""
    return label(
        "",
        background=SKAO_NAVY,
        style=css(
            background_image=f"linear-gradient(90deg, {SKAO_MAGENTA}, {SKAO_NAVY})",
            border_radius="0 0 8px 8px",
        ),
    )


def title_bar(text: str, size: float, colours: tuple[str, str]) -> dict[str, Any]:
    """A page or band title."""
    background, colour = colours
    return label(
        text,
        size=size,
        background=background,
        colour=colour,
        style=css(
            font_family=HEADING_FONT,
            font_weight="600",
            padding="0 16px",
            box_sizing="border-box",
            border_radius="6px",
            display="flex",
            align_items="center",
        ),
    )


def box(
    title: str,
    children: list[dict[str, Any]],
    layout: str = "vertical",
    big_slot: int = 5,
    small_slot: int = 1,
    inset: int = 8,
    padding: float = 0,
    frame: bool = True,
    kind: str = "family",
) -> dict[str, Any]:
    """
    A BOX that arranges ``children`` itself (vertically or horizontally).

    Taranta places a box's children at the inside of its border and stretches
    them to fill it, so the border is the only way to pad them: ``inset`` px of
    border in the box's own tint, with an outline drawing the visible frame
    in the theme's accent colour.
    """
    accent, tint = THEMES.get(kind, THEMES["family"])
    declarations: dict[str, str] = {}
    if frame:
        declarations.update(
            outline=f"2px solid {accent}33",
            outline_offset="-2px",
            border_radius="8px",
        )
    if title:
        declarations.update(font_weight="bold", font_family=HEADING_FONT)
    background = tint if frame else CLEAR
    result = widget(
        "BOX",
        {
            "title": title,
            "bigWidget": big_slot,
            "smallWidget": small_slot,
            "textColor": "#1a1a1a",
            "backgroundColor": background,
            "borderColor": background,
            "borderWidth": inset,
            "borderStyle": "solid",
            "textSize": 1,
            "fontFamily": "Helvetica",
            "layout": layout,
            "alignment": "Left",
            "padding": padding if title else 0,
            "customCss": css(**declarations),
        },
    )
    result["innerWidgets"] = children
    return result


def device_status(device: str, show_name: bool = True) -> dict[str, Any]:
    """Device State LED and name."""
    return widget(
        "DEVICE_STATUS",
        {
            "device": device,
            "state": {"device": None, "attribute": None},
            "showDeviceName": show_name,
            "showTangoDB": False,
            "showStateString": True,
            "alignValueRight": True,
            "showStateLED": True,
            "LEDSize": 1,
            "textColor": "#000000",
            "backgroundColor": CLEAR,
            "textSize": 1,
            "linkTo": "",
            "widgetCss": ROW_CSS,
        },
    )


def attribute_display(device: str, attr: AttributeInfo, text: str) -> dict[str, Any]:
    """Read-only value display (scalars, enums with labels, arrays as JSON)."""
    result = widget(
        "ATTRIBUTE_DISPLAY",
        {
            "attribute": attribute_ref(device, attr, text),
            "precision": 3,
            "format": "",
            "showUnit": True,
            "showDevice": False,
            "jsonCollapsed": True,
            "showTangoDB": False,
            "showAttribute": "Label",
            "alignTextCenter": False,
            "alignValueRight": True,
            "scientificNotation": False,
            "showEnumLabels": True,
            "showAttrQuality": False,
            "textColor": "#1a1a1a",
            "backgroundColor": CLEAR,
            "size": 1,
            "font": "Helvetica",
            "widgetCss": DISPLAY_CSS,
        },
    )
    if attr.name.lower() in LONG_TEXT:
        result[ROWS] = 2
    return result


def led(
    device: str,
    attr: AttributeInfo,
    text: str,
    compare: str,
    relation: str = "=",
    true_colour: str = GREEN,
    false_colour: str = RED,
) -> dict[str, Any]:
    """An LED that is ``true_colour`` when ``value <relation> compare``."""
    return widget(
        "LED_DISPLAY",
        {
            "attribute": attribute_ref(device, attr, text),
            "relation": relation,
            "compare": compare,
            "trueColor": true_colour,
            "falseColor": false_colour,
            "ledSize": 1,
            "textSize": 1,
            "showAttributeValue": False,
            "showDeviceName": False,
            "showTangoDB": False,
            "showAttribute": "Label",
            "alignTextCenter": False,
            "alignValueRight": True,
            "customCss": ROW_CSS,
        },
    )


#: Layout hint (stripped before saving): the caption shown above a dial.
CAPTION = "_caption"


def dial(
    device: str, attr: AttributeInfo, text: str, low: float, high: float
) -> dict[str, Any]:
    """A gauge for a bounded physical quantity, captioned with name and unit."""
    result = widget(
        "ATTRIBUTE_DIAL",
        {
            "attribute": attribute_ref(device, attr, text),
            "min": low,
            "max": high,
            "label": "",  # the caption above names it
            "showWriteValue": False,
            "showTangoDB": False,
            "widgetCss": "",
        },
    )
    unit = attr.unit.strip()
    result[CAPTION] = f"{text} ({unit})" if unit else text
    return result


def caption(text: str) -> dict[str, Any]:
    """A gauge's title, centred above it."""
    return label(
        text,
        size=0.95,
        background=CLEAR,
        colour="#1a1a1a",
        style=css(
            font_family=HEADING_FONT,
            font_weight="600",
            display="flex",
            align_items="center",
            justify_content="center",
            white_space="nowrap",
        ),
    )


def spacer() -> dict[str, Any]:
    """Empty space in a row."""
    return label("", background=CLEAR)


#: Layout hint (stripped before saving): extra rows of height a widget needs
#: below it, e.g. for a dropdown's menu. Taranta clips anything that overflows a
#: widget (its wrapper is ``overflow: auto``), so the menu has to fit inside.
MENU_ROWS = "_menu_rows"
#: Layout hint (stripped before saving): rows of height a widget needs.
ROWS = "_rows"
#: Attributes whose values are usually long enough to wrap onto two lines.
LONG_TEXT = {"buildstate", "versioninfo", "description"}


def dropdown_writer(
    device: str, attr: AttributeInfo, text: str, values: list[tuple[str, str]]
) -> dict[str, Any]:
    """
    Choose-and-set for enums, tall enough for its menu to open inside it.

    It doesn't show the attribute's current value; pair it with a display.
    """
    result = widget(
        "ATTRIBUTE WRITER DROPDOWN",
        {
            "attribute": attribute_ref(device, attr, text),
            "submitButtonTitle": "Set",
            "dropdownTitle": "Choose…",
            "writeValues": [{"title": t, "value": v} for t, v in values],
            "writeValuesSpectrum": {"device": None, "attribute": None, "label": ""},
            "showDevice": False,
            "showTangoDB": False,
            "showAttribute": "Label",
            "textColor": "#1a1a1a",
            "backgroundColor": CLEAR,
            "size": 1,
            "font": "Helvetica",
            "dropdownButtonCss": "",
            "submitButtonCss": "",
            "customCss": ROW_CSS,
        },
    )
    result[MENU_ROWS] = len(values)
    return result


def writer(device: str, attr: AttributeInfo, text: str) -> dict[str, Any]:
    """Free-form value writer for numeric and string attributes."""
    return widget(
        "ATTRIBUTE_WRITER",
        {
            "title": "",
            "attribute": attribute_ref(device, attr, text),
            "showDevice": False,
            "showTangoDB": False,
            "showAttribute": "Label",
            "alignValueRight": True,
            "textColor": "#1a1a1a",
            "backgroundColor": CLEAR,
            "size": 1,
            "font": "Helvetica",
            "widgetCss": ROW_CSS,
        },
    )


def boolean_display(device: str, attr: AttributeInfo, text: str) -> dict[str, Any]:
    """Switch for writable booleans."""
    return widget(
        "BOOLEAN_DISPLAY",
        {
            "attribute": attribute_ref(device, attr, text),
            "showAttribute": "Label",
            "showDevice": False,
            "showTangoDB": False,
            "alignSwitchRight": True,
            "widgetCSS": ROW_CSS,
            "OnCSS": "",
            "OffCSS": "",
        },
    )


def logger(device: str, attr: AttributeInfo, text: str) -> dict[str, Any]:
    """A scrolling log of an attribute's recent values."""
    return widget(
        "ATTRIBUTE_LOGGER",
        {
            "attribute": attribute_ref(device, attr, text),
            "linesDisplayed": 3,
            "showLastValue": False,
            "showDevice": False,
            "showTangoDB": False,
            "showAttribute": "Label",
            "showTime": True,
            "OuterDivCSS": ROW_CSS,
            "LastValueCSS": "",
            "TableCSS": css(font_size="11px"),
        },
    )


def plot(device: str, attrs: list[AttributeInfo], labels: list[str]) -> dict[str, Any]:
    """A time-series plot of several numeric attributes."""
    return widget(
        "ATTRIBUTE_PLOT",
        {
            "timeWindow": 600,
            "attributes": [
                {
                    "attribute": attribute_ref(device, attr, text),
                    "showAttribute": "Label",
                    "yAxis": "left",
                    "lineColor": PLOT_COLOURS[i % len(PLOT_COLOURS)],
                }
                for i, (attr, text) in enumerate(zip(attrs, labels, strict=True))
            ],
            "showZeroLine": False,
            "xScientificNotation": False,
            "yScientificNotation": False,
            "xLogarithmicScale": False,
            "yLogarithmicScale": False,
            "showTangoDB": False,
            "textColor": "#000000",
            "backgroundColor": "#ffffff",
        },
    )


def spectrum(device: str, attr: AttributeInfo, text: str) -> dict[str, Any]:
    """Line plot of a numeric array."""
    return widget(
        "SPECTRUM",
        {
            "attribute": attribute_ref(device, attr, text),
            "showSpecificYIndexValue": "",
            "showSpecificXIndexValue": "",
            "timeWindow": 120,
            "showAttribute": "Label",
            "showTitle": True,
            "showTangoDB": False,
            "xScientificNotation": False,
            "yScientificNotation": False,
            "xLogarithmicScale": False,
            "yLogarithmicScale": False,
            "inelastic": False,
            "lineColor": PLOT_COLOURS[0],
            "textColor": "#000000",
            "backgroundColor": "#ffffff",
        },
    )


def command(device: str, cmd: CommandInfo, label: str) -> dict[str, Any]:
    """
    A button (with an input box if the command takes an argument).

    The readable name goes on the button rather than in a separate title,
    which wrapped and was cut off in a single row next to the input box.
    """
    result = widget(
        "COMMAND",
        {
            "title": "",
            "buttonText": label,
            "command": {
                "device": device,
                "command": cmd.name,
                "acceptedType": cmd.in_type,
                "intypedesc": cmd.doc_in,
                "outtypedesc": cmd.doc_out,
                "outtype": cmd.out_type,
                "tag": "0",
            },
            "commandArgs": [],
            "showDevice": False,
            "showTangoDB": False,
            "showCommand": False,
            "requireConfirmation": True,
            "displayOutput": True,
            "alignButtonRight": True,
            "placeholder": "intype",
            "textColor": "#000000",
            "backgroundColor": CLEAR,
            "size": 1,
            "font": "Helvetica",
            "btnCss": "",
            "widgetCss": ROW_CSS,
        },
    )
    # A second row for the command's output, shown under the button.
    result[ROWS] = 2
    return result


def dashboard_link(dashboard_name: str, text: str) -> dict[str, Any]:
    """A button opening another dashboard (looked up by name when clicked)."""
    return widget(
        "DASHLINK",
        {
            # Taranta stores the target as JSON; it finds the dashboard by name,
            # falling back to the id. `ska-taranta upload` fills the id in.
            "DefaultDashboard": json.dumps({"name": dashboard_name, "id": ""}),
            "HideDropdown": True,
            "NewTab": False,
            "ButtonText": text,
            "DropDownCss": "",
            "ButtonCss": css(
                width="100%",
                height="100%",
                font_weight="600",
                font_family=HEADING_FONT,
                color=SKAO_NAVY,
                background="#ffffff",
                border=f"2px solid {SKAO_MAGENTA}",
                border_radius="6px",
            ),
            "CustomCss": css(padding="0 4px", box_sizing="border-box"),
        },
    )
