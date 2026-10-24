"""Build docs/sentinel-demo.html by inlining docs/demo/story_data.json into docs/demo/template.html.

python scripts/export_demo_data.py   # once, writes the JSON
python scripts/build_demo.py
"""
from __future__ import annotations

from pathlib import Path

TEMPLATE = Path("docs/demo/template.html")
DATA = Path("docs/demo/story_data.json")
OUT = Path("docs/sentinel-demo.html")


def main() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    marker = "/*DATA*/null"
    if marker not in html:
        raise SystemExit("template has no data marker")
    OUT.write_text(html.replace(marker, DATA.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
