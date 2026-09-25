"""Build the static GitHub Pages site in docs/ from the dashboard pages.

    python src/build_site.py

The dashboard pages are written as page bodies (a claude.ai page adds the document
skeleton). This wraps each one in a full HTML document, copies its data files, and
points cross-links at the other pages on the same site instead of claude.ai.
GitHub Pages serves docs/ from the main branch.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dashboard"
OUT = ROOT / "docs"

# page folder (relative to dashboard/ and docs/) -> its claude.ai URL
PAGES = {
    "": "https://claude.ai/artifact/Mv4x8SXoKdvg8o5mj19TMf",
    "canola": "https://claude.ai/artifact/L9q7H7aYtaMgHopbBbEtjk",
    "wheat": "https://claude.ai/artifact/7ihCwx3TgUNdg2yfSKKyG4",
    "durum": "https://claude.ai/artifact/GUgDjqNEfP8vHb1HrKi2uH",
    "barley": "https://claude.ai/artifact/U6tuj2LiEW7VGqnQLQYwVa",
}
DATA_FILES = ["data.json", "forecast.json"]

# Bar-chart favicon, embedded so every page has it without a separate file.
# (claude.ai adds its own icon; on GitHub Pages the page must declare one.)
FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='7' fill='#101311'/>"
    "<rect x='7' y='17' width='4' height='8' rx='1.5' fill='#1baf7a'/>"
    "<rect x='14' y='11' width='4' height='14' rx='1.5' fill='#eb6834'/>"
    "<rect x='21' y='6' width='4' height='19' rx='1.5' fill='#3987e5'/>"
    "</svg>"
)
FAVICON = "data:image/svg+xml," + quote(FAVICON_SVG)

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
HEAD = HEAD.replace("__FAVICON__", FAVICON)


def relink(text: str, page: str) -> str:
    """Replace claude.ai page URLs with relative links from `page`'s folder."""
    for target, url in PAGES.items():
        rel = os.path.relpath(OUT / target, OUT / page).replace(os.sep, "/")
        text = text.replace(url, "./" if rel == "." else rel + "/")
    return text


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for page in PAGES:
        src_dir, out_dir = SRC / page, OUT / page
        out_dir.mkdir(parents=True, exist_ok=True)
        body = relink((src_dir / "index.html").read_text(), page)
        (out_dir / "index.html").write_text(HEAD + body + "\n</html>\n")
        for name in DATA_FILES:
            if (src_dir / name).exists():
                (out_dir / name).write_text(relink((src_dir / name).read_text(), page))
        print(f"docs/{page + '/' if page else ''}index.html")
    (OUT / ".nojekyll").write_text("")  # serve files as-is, no Jekyll processing


if __name__ == "__main__":
    main()
