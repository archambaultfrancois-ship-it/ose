"""Generate project branding from the Web Console's existing SVG mark."""

import argparse
from html import escape
from pathlib import Path
import xml.etree.ElementTree as ET


DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parent.parent
SOURCE = ROOT / "ose-ws/frontend/src/assets/console-mark.svg"
NAME = "Orchestration, Simulation, Exécution"


def mark():
    source = SOURCE.read_text(encoding="utf-8")
    attributes = ET.fromstring(source).attrib
    style = " ".join(
        f'{key}="{escape(value, quote=True)}"'
        for key, value in attributes.items() if key != "viewBox"
    )
    shapes = source.split(">", 1)[1].rsplit("</svg>", 1)[0].strip()
    return (
        '<rect x=".75" y=".75" width="62.5" height="62.5" rx="16" '
        'fill="#1b3743" stroke="#41616a" stroke-width="1.5" />\n'
        f'<g transform="translate(8 8)" {style}>\n{shapes}\n</g>'
    )


def svg(filename, width, height, title, content):
    (DIRECTORY / filename).write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">\n'
        f'  <title id="title">{escape(title)}</title>\n'
        '  <desc id="description">The turquoise OSE Web Console mark, '
        'reused as the OSE project identity.</desc>\n'
        f'{content}\n</svg>\n', encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--png", action="store_true", help="also export PNGs using CairoSVG")
    args = parser.parse_args()
    if args.png:
        try:
            import cairosvg
        except ImportError:
            parser.error("--png requires CairoSVG: python3 -m pip install CairoSVG")
    logo = mark()
    svg("ose-logo.svg", 64, 64, "OSE logo", logo)
    svg("ose-banner.svg", 1280, 320, f"OSE — {NAME}", f'''
  <rect width="1280" height="320" rx="24" fill="#101e2d" />
  <g transform="translate(64 64) scale(3)">{logo}</g>
  <g font-family="DejaVu Sans, Arial, sans-serif">
    <text x="304" y="150" fill="#f3f5f7" font-size="100" font-weight="700" letter-spacing="8">OSE</text>
    <text x="308" y="204" fill="#dae4ed" font-size="32">{NAME}</text>
    <text x="310" y="254" fill="#7dd6cb" font-size="21" letter-spacing="2">SDL · OSE · WEB CONSOLE</text>
  </g>''')
    svg("ose-social-preview.svg", 1280, 640, f"OSE — {NAME}", f'''
  <rect width="1280" height="640" fill="#101e2d" />
  <g transform="translate(80 136) scale(3.5)">{logo}</g>
  <g font-family="DejaVu Sans, Arial, sans-serif">
    <text x="352" y="260" fill="#f3f5f7" font-size="116" font-weight="700" letter-spacing="8">OSE</text>
    <text x="356" y="322" fill="#dae4ed" font-size="32">{NAME}</text>
    <text x="80" y="464" fill="#dae4ed" font-size="30">Typed messages. Executable graphs. Live diagnostics.</text>
    <text x="80" y="544" fill="#7dd6cb" font-size="22" letter-spacing="3">SDL · OSE · WEB CONSOLE</text>
  </g>''')
    if args.png:
        for name, size in (("ose-logo", 512), ("ose-social-preview", 1280)):
            cairosvg.svg2png(
                url=str(DIRECTORY / f"{name}.svg"),
                write_to=str(DIRECTORY / f"{name}.png"),
                output_width=size,
            )


if __name__ == "__main__":
    main()
