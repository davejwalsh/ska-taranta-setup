"""
Builders for Taranta widget JSON.

The defaults here mirror the ``definition`` objects of the widgets in
``taranta/src/dashboard/widgets`` (Taranta 2.18). Only ``inputs`` that differ
from the widget's own defaults strictly need to be given, but Taranta stores
the full set, so we do too: it keeps the generated files identical in shape
to dashboards exported from the UI.

Sizes and positions are in Taranta grid units (``MIN_WIDGET_SIZE`` pixels,
20 by default).
"""

from __future__ import annotations

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
FIELD_CSS = (
    "background:#f7f8fa;border:1px solid #dde1e6;border-radius:4px;padding:2px 6px;"
)
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


def label(text: str, size: float = 1.0, background: str = "#ffffff") -> dict[str, Any]:
    """A static text label."""
    return widget(
        "LABEL",
        {
            "text": text,
            "textColor": "#1a1a1a",
            "backgroundColor": background,
            "borderWidth": 0,
            "borderColor": "#000000",
            "font": "Helvetica",
            "automaticResize": "Disabled",
            "size": size,
            "linkTo": "",
            "customCss": "",
        },
    )


def box(
    title: str,
    children: list[dict[str, Any]],
    layout: str = "vertical",
    big_slot: int = 5,
    small_slot: int = 1,
    border: int = 1,
) -> dict[str, Any]:
    """A BOX that arranges ``children`` itself (vertically or horizontally)."""
    result = widget(
        "BOX",
        {
            "title": title,
            "bigWidget": big_slot,
            "smallWidget": small_slot,
            "textColor": "#1a1a1a",
            "backgroundColor": "#ffffff",
            "borderColor": "#c3cad4",
            "borderWidth": border,
            "borderStyle": "solid",
            "textSize": 1,
            "fontFamily": "Helvetica",
            "layout": layout,
            "alignment": "Left",
            "padding": 0,
            "customCss": "font-weight:bold;" if title else "",
        },
    )
    result["innerWidgets"] = children
    return result


def device_status(device: str) -> dict[str, Any]:
    """Device State LED and name."""
    return widget(
        "DEVICE_STATUS",
        {
            "device": device,
            "state": {"device": None, "attribute": None},
            "showDeviceName": True,
            "showTangoDB": False,
            "showStateString": True,
            "alignValueRight": True,
            "showStateLED": True,
            "LEDSize": 1,
            "textColor": "#000000",
            "backgroundColor": "#ffffff",
            "textSize": 1,
            "linkTo": "",
            "widgetCss": "",
        },
    )


def attribute_display(device: str, attr: AttributeInfo, text: str) -> dict[str, Any]:
    """Read-only value display (scalars, enums with labels, arrays as JSON)."""
    return widget(
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
            "backgroundColor": "#ffffff",
            "size": 1,
            "font": "Helvetica",
            "widgetCss": FIELD_CSS,
        },
    )


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
            "customCss": "",
        },
    )


def dial(
    device: str, attr: AttributeInfo, text: str, low: float, high: float
) -> dict[str, Any]:
    """A gauge for a bounded physical quantity."""
    return widget(
        "ATTRIBUTE_DIAL",
        {
            "attribute": attribute_ref(device, attr, text),
            "min": low,
            "max": high,
            "label": "attribute",
            "showWriteValue": False,
            "showTangoDB": False,
            "widgetCss": "",
        },
    )


def dropdown_writer(
    device: str, attr: AttributeInfo, text: str, values: list[tuple[str, str]]
) -> dict[str, Any]:
    """Choose-and-set for enums (adminMode, controlMode, ...)."""
    return widget(
        "ATTRIBUTE WRITER DROPDOWN",
        {
            "attribute": attribute_ref(device, attr, text),
            "submitButtonTitle": "Set",
            "dropdownTitle": text,
            "writeValues": [{"title": t, "value": v} for t, v in values],
            "writeValuesSpectrum": {"device": None, "attribute": None, "label": ""},
            "showDevice": False,
            "showTangoDB": False,
            "showAttribute": "Label",
            "textColor": "#1a1a1a",
            "backgroundColor": "#ffffff",
            "size": 1,
            "font": "Helvetica",
            "dropdownButtonCss": "",
            "submitButtonCss": "",
            "customCss": "",
        },
    )


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
            "backgroundColor": "#ffffff",
            "size": 1,
            "font": "Helvetica",
            "widgetCss": "",
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
            "widgetCSS": "",
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
            "linesDisplayed": 4,
            "showLastValue": False,
            "showDevice": False,
            "showTangoDB": False,
            "showAttribute": "Label",
            "showTime": True,
            "OuterDivCSS": FIELD_CSS,
            "LastValueCSS": "",
            "TableCSS": "font-size:11px;",
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


def command(device: str, cmd: CommandInfo, title: str) -> dict[str, Any]:
    """A button (with an input box if the command takes an argument)."""
    return widget(
        "COMMAND",
        {
            "title": title,
            "buttonText": cmd.name,
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
            "backgroundColor": "#ffffff",
            "size": 1,
            "font": "Helvetica",
            "btnCss": "",
            "widgetCss": "",
        },
    )
