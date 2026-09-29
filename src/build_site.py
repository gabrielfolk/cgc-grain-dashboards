"""Build the static GitHub Pages site in docs/ from the dashboard pages.

    python src/build_site.py

The dashboard pages are written as page bodies. This wraps each one in a full HTML
document (charset, viewport, base styles and a favicon), adds the site navigation bar,
and copies its data files.
Links between pages are relative, so the same files work in docs/ and when previewing
dashboard/ locally. GitHub Pages serves docs/ from the main branch.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import quote

from grain_data import GRAINS

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dashboard"
OUT = ROOT / "docs"

# Page folders, relative to dashboard/ and docs/ ("" is the all-crops page), in nav order.
# A list entry is a dropdown menu: (menu label, [page folders]).
NAV = ["", "margins", "canola", "wheat", "durum", "barley", "peas", "oats", "lentils", "soybeans", "corn",
       ("Special crops", ["flaxseed", "rye", "beans", "canaryseed", "chickpeas", "mustard"]), "feed"]
LABELS = {"": "Overview", "margins": "Margins", "feed": "Feed grains",
          **{slug: cfg["title"].removesuffix(" Pipeline") for slug, cfg in GRAINS.items()}}
PAGES = [p for entry in NAV for p in (entry[1] if isinstance(entry, tuple) else [entry])]
assert set(GRAINS) <= set(PAGES), "every crop page needs a place in NAV"
DATA_FILES = ["data.json", "forecast.json"]

# Bar-chart favicons, embedded so every page has one without a separate file
# The all-crops page uses multicoloured bars on dark; each crop page gets its own colour.
CROP_COLORS = {
    "canola": "#f2c200", "wheat": "#c89b3c", "durum": "#e0662a", "barley": "#4e9a3a", "feed": "#7c5cc4", "margins": "#2a78d6",
    "peas": "#7cb342", "oats": "#cbb68a", "lentils": "#b5651d", "soybeans": "#a8a23c", "corn": "#e8a317",
    "flaxseed": "#5b7fb8", "rye": "#8d6e63", "beans": "#a0522d", "canaryseed": "#d4c05a", "chickpeas": "#d9a066",
    "mustard": "#c9a90a",
}


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

  /* site navigation: sticks to the top, full width, uses each page's own theme tokens */
  .site-nav {
    position: sticky; top: 0; z-index: 5;
    margin: -28px -16px 24px;
    padding-top: env(safe-area-inset-top, 0px);
    background: var(--surface); border-bottom: 1px solid var(--border);
  }
  .site-nav-inner {
    max-width: 1240px; margin: 0 auto; padding: 0 16px;
    display: flex; align-items: center; gap: 4px 20px;
    overflow-x: auto; scrollbar-width: none;
  }
  .site-nav-inner::-webkit-scrollbar { display: none; }
  .site-nav .site-name {
    font: 500 11px/1 var(--mono, ui-monospace, monospace); letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--muted); white-space: nowrap; margin-right: auto; padding: 14px 0;
  }
  .site-nav ul { list-style: none; margin: 0; padding: 0; display: flex; gap: 2px; }
  .site-nav a {
    display: block; padding: 13px 10px 11px; white-space: nowrap;
    font: 500 13px/1 var(--sans, system-ui, sans-serif); color: var(--ink-2); text-decoration: none;
    border-bottom: 2px solid transparent;
  }
  .site-nav a:hover { color: var(--ink); }
  .site-nav a[aria-current="page"] { color: var(--ink); font-weight: 600; border-bottom-color: var(--ink); }
  .site-nav a:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; }

  /* dropdown: opens below on wide screens; on narrow screens the bar scrolls, so its links open inline */
  .site-nav details { display: flex; }
  .site-nav summary {
    list-style: none; cursor: pointer; padding: 13px 10px 11px; white-space: nowrap;
    font: 500 13px/1 var(--sans, system-ui, sans-serif); color: var(--ink-2); border-bottom: 2px solid transparent;
  }
  .site-nav summary::-webkit-details-marker { display: none; }
  .site-nav summary::after { content: " ▾"; font-size: 10px; }
  .site-nav details[open] summary::after { content: " ▴"; }
  .site-nav summary:hover { color: var(--ink); }
  .site-nav summary:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; }
  .site-nav summary.current { color: var(--ink); font-weight: 600; border-bottom-color: var(--ink); }
  .site-nav .menu { display: flex; gap: 2px; }
  @media (min-width: 1180px) {
    .site-nav-inner { overflow: visible; }
    .site-nav details { position: relative; }
    .site-nav .menu {
      position: absolute; top: 100%; left: 0; z-index: 6; flex-direction: column; min-width: 150px; padding: 4px;
      background: var(--surface); border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 6px 20px rgba(0, 0, 0, 0.14);
    }
    .site-nav .menu a { padding: 9px 10px; border-bottom: 0; border-radius: 4px; }
    .site-nav .menu a:hover { background: var(--hover); }
    .site-nav .menu a[aria-current="page"] { background: var(--chip); }
  }
  @media (max-width: 1320px) { .site-nav .site-name { display: none; } }
</style>
"""


# closes an open dropdown on an outside click or Escape
NAV_SCRIPT = """<script>
(function () {
  const menus = document.querySelectorAll(".site-nav details");
  document.addEventListener("click", (e) => { for (const d of menus) if (!d.contains(e.target)) d.open = false; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") for (const d of menus) if (d.open) { d.open = false; d.querySelector("summary").focus(); } });
})();
</script>
"""


def nav(page: str) -> str:
    """Navigation bar for `page`, with links relative to its folder."""
    up = "../" if page else ""

    def link(target: str) -> str:
        return (f'<a href="{up + (target + "/" if target else "") or "./"}"'
                + (' aria-current="page"' if target == page else "") + f">{LABELS[target]}</a>")

    items = []
    for entry in NAV:
        if isinstance(entry, tuple):
            label, targets = entry
            current = ' class="current"' if page in targets else ""
            items.append(f"<li><details><summary{current}>{label}</summary>"
                         f'<div class="menu">{"".join(link(t) for t in targets)}</div></details></li>')
        else:
            items.append(f"<li>{link(entry)}</li>")
    return (f'<nav class="site-nav" aria-label="Dashboards"><div class="site-nav-inner">'
            f'<span class="site-name">CGC grain dashboards</span><ul>{"".join(items)}</ul></div></nav>\n' + NAV_SCRIPT)


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for page in PAGES:
        src_dir, out_dir = SRC / page, OUT / page
        out_dir.mkdir(parents=True, exist_ok=True)
        body = (src_dir / "index.html").read_text()
        assert body.count('<div class="wrap">') == 1, f"{page or 'overview'}: expected one content wrapper"
        body = body.replace('<div class="wrap">', nav(page) + '<div class="wrap">')
        (out_dir / "index.html").write_text(HEAD.replace("__FAVICON__", favicon(page)) + body + "\n</html>\n")
        for name in DATA_FILES:
            if (src_dir / name).exists():
                shutil.copyfile(src_dir / name, out_dir / name)
        print(f"docs/{page + '/' if page else ''}index.html")
    (OUT / ".nojekyll").write_text("")  # serve files as-is, no Jekyll processing


if __name__ == "__main__":
    main()
