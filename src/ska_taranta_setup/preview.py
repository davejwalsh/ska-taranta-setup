"""
Wireframe previews of ``.wj`` dashboards, without a running Taranta.

Draws each widget's box with its label and a hint of what it shows, at
Taranta's real scale, so a layout can be checked before deploying. Values
are placeholders: this shows where things go, not live data.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

#: Default px per grid unit (Taranta's MIN_WIDGET_SIZE in the SKA image).
DEFAULT_TILE = 10

FILL = {
    "BOX": "#ffffff",
    "LABEL": "#dfe6ee",
    "DEVICE_STATUS": "#e6f7e6",
    "LED_DISPLAY": "#e6f7e6",
    "BOOLEAN_DISPLAY": "#e6f7e6",
    "ATTRIBUTE_DISPLAY": "#f7f8fa",
    "ATTRIBUTE_DIAL": "#fff4e0",
    "ATTRIBUTE_PLOT": "#e8f0fb",
    "SPECTRUM": "#e8f0fb",
    "ATTRIBUTE WRITER DROPDOWN": "#f3e8fb",
    "ATTRIBUTE_WRITER": "#f3e8fb",
    "COMMAND": "#fde8e8",
    "ATTRIBUTE_LOGGER": "#f0f0f0",
    "DASHLINK": "#e3ecfa",
}


def _flatten(widgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for widget in widgets:
        out.append(widget)
        out.extend(_flatten(widget.get("innerWidgets", [])))
    return out


def _caption(widget: dict[str, Any]) -> tuple[str, str]:
    """Left-hand text and right-hand hint for a widget."""
    inputs = widget["inputs"]
    kind = widget["type"]
    attr = inputs.get("attribute") or {}
    name = attr.get("label") or attr.get("attribute") or ""
    if kind == "LABEL":
        return inputs.get("text", ""), ""
    if kind == "DEVICE_STATUS":
        return str(inputs.get("device", "")).split("://")[-1], "● State"
    if kind == "DASHLINK":
        return (
            "",
            f"[ {inputs.get('ButtonText', '')} ] → {inputs.get('DefaultDashboard')}",
        )
    if kind == "COMMAND":
        return inputs.get("title", ""), f"[ {inputs.get('buttonText', '')} ]"
    if kind == "ATTRIBUTE_PLOT":
        labels = [a["attribute"].get("label", "") for a in inputs.get("attributes", [])]
        return "Plot: " + ", ".join(labels), ""
    hints = {
        "LED_DISPLAY": "●",
        "BOOLEAN_DISPLAY": "◐",
        "ATTRIBUTE_DISPLAY": "value",
        "ATTRIBUTE WRITER DROPDOWN": "[ ▼ ] [Set]",
        "ATTRIBUTE_WRITER": "[ ____ ] [Set]",
        "ATTRIBUTE_DIAL": f"{inputs.get('min')}…{inputs.get('max')}",
        "SPECTRUM": "spectrum",
        "ATTRIBUTE_LOGGER": "log",
    }
    return name, hints.get(kind, kind)


def dashboard_svg(dashboard: dict[str, Any], tile: int = DEFAULT_TILE) -> str:
    """Render a dashboard as an SVG wireframe."""
    widgets = _flatten(dashboard.get("widget") or dashboard.get("widgets", []))
    if not widgets:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>"
    width = max(w["x"] + w["width"] for w in widgets) * tile + 20
    height = max(w["y"] + w["height"] for w in widgets) * tile + 20
    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width:.0f}' "
        f"height='{height:.0f}' font-family='Helvetica,Arial,sans-serif' "
        "font-size='11'>",
        f"<rect width='{width:.0f}' height='{height:.0f}' fill='#eef1f5'/>",
    ]
    for widget in widgets:
        x, y = widget["x"] * tile, widget["y"] * tile
        w_px, h_px = widget["width"] * tile, widget["height"] * tile
        kind = widget["type"]
        stroke = "#b3bcc8" if kind == "BOX" else "#dde1e6"
        parts.append(
            f"<rect x='{x:.1f}' y='{y:.1f}' width='{w_px:.1f}' height='{h_px:.1f}' "
            f"fill='{FILL.get(kind, '#ffffff')}' stroke='{stroke}'>"
            f"<title>{html.escape(kind)}</title></rect>"
        )
        if kind == "BOX":
            title = widget["inputs"].get("title", "")
            if title:
                parts.append(
                    f"<text x='{x + 5:.1f}' y='{y + 14:.1f}' font-weight='bold' "
                    f"font-size='12'>{html.escape(title)}</text>"
                )
            continue
        left, right = _caption(widget)
        bold = " font-weight='bold' font-size='14'" if kind == "LABEL" else ""
        parts.append(
            f"<text x='{x + 5:.1f}' y='{y + 18:.1f}'{bold}>"
            f"{html.escape(str(left)[:70])}</text>"
        )
        if right:
            parts.append(
                f"<text x='{x + w_px - 6:.1f}' y='{y + 18:.1f}' text-anchor='end' "
                f"fill='#6b7480'>{html.escape(right)}</text>"
            )
    parts.append("</svg>")
    return "\n".join(parts)


def write_preview(files: list[Path], output: Path, tile: int = DEFAULT_TILE) -> Path:
    """Write one HTML page with a wireframe of each dashboard."""
    sections = []
    for path in files:
        dashboard = json.loads(path.read_text())
        sections.append(
            f"<h2>{html.escape(dashboard.get('name', path.stem))}</h2>"
            f"<p class='file'>{html.escape(path.name)}</p>"
            f"<div class='frame'>{dashboard_svg(dashboard, tile)}</div>"
        )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Taranta Dashboard Preview</title>
<style>
  body {{ font-family: Helvetica, Arial, sans-serif; margin: 16px; background: #fafbfc;
         color: #1a1a1a; }}
  h2 {{ margin: 32px 0 4px; font-size: 18px; }}
  .file {{ margin: 0 0 8px; color: #6b7480; font-size: 12px; }}
  .frame {{ overflow-x: auto; border: 1px solid #dde1e6; }}
  .note {{ color: #6b7480; font-size: 13px; }}
</style></head><body>
<h1>Taranta dashboard preview</h1>
<p class="note">Wireframes at Taranta's scale ({tile}&nbsp;px grid). Values are
placeholders; hover a widget to see its type.</p>
{"".join(sections)}
</body></html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page)
    return output
