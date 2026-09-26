"""Build the static GitHub Pages site in docs/ from the dashboard pages.

    python src/build_site.py

The dashboard pages are written as page bodies. This wraps each one in a full HTML
document (charset, viewport, base styles and a favicon) and copies its data files.
Links between pages are relative, so the same files work in docs/ and when previewing
dashboard/ locally. GitHub Pages serves docs/ from the main branch.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dashboard"
OUT = ROOT / "docs"

# page folders, relative to dashboard/ and docs/ ("" is the all-crops page)
PAGES = ["", "canola", "wheat", "durum", "barley", "feed"]
DATA_FILES = ["data.json", "forecast.json"]

# Bar-chart favicons, embedded so every page has one without a separate file
# The all-crops page uses multicoloured bars on dark; each crop page gets its own colour.
CROP_COLORS = {"canola": "#f2c200", "wheat": "#c89b3c", "durum": "#e0662a", "barley": "#4e9a3a", "feed": "#7c5cc4"}


def favicon(page: str) -> str:
    if page in CROP_COLORS:
        bg, bars = CROP_COLORS[page], ["#101311"] * 3
    else:
        bg, bars = "#101311", ["#1baf7a", "#eb6834", "#3987e5"]
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        f"<rect width='32' height='32' rx='7' fill='{bg}'/>"
        f"<rect x='7' y='17' width='4' height='8' rx='1.5' fill='{bars[0]}'/>"
        f"<rect x='14' y='11' width='4' height='14' rx='1.5' fill='{bars[1]}'/>"
        f"<rect x='21' y='6' width='4' height='19' rx='1.5' fill='{bars[2]}'/>"
        "</svg>"
    )
    return "data:image/svg+xml," + quote(svg)


HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="icon" type="image/svg+xml" href="__FAVICON__">
<style>
  :root { color-scheme: light; padding-top: env(safe-area-inset-top, 0px); padding-bottom: env(safe-area-inset-bottom, 0px); }
  body { margin: 0; font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; background: #f9f9f7; }
  img { max-width: 100%; }
  [hidden] { display: none !important; }
</style>
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for page in PAGES:
        src_dir, out_dir = SRC / page, OUT / page
        out_dir.mkdir(parents=True, exist_ok=True)
        body = (src_dir / "index.html").read_text()
        (out_dir / "index.html").write_text(HEAD.replace("__FAVICON__", favicon(page)) + body + "\n</html>\n")
        for name in DATA_FILES:
            if (src_dir / name).exists():
                shutil.copyfile(src_dir / name, out_dir / name)
        print(f"docs/{page + '/' if page else ''}index.html")
    (OUT / ".nojekyll").write_text("")  # serve files as-is, no Jekyll processing


if __name__ == "__main__":
    main()
