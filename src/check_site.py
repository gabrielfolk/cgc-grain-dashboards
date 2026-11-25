"""Render check for the built site: load every page in headless Chrome and fail if a page errors or
a section comes up empty.

A page fails if it throws a script error, logs a console error, fails to load a file it asks for,
or shows (is visible on screen) any of these empty:
    a chart (#ch-*) with no drawn SVG
    a table (#*-table, .flash) with no body rows
    a card or note block (#watch, #xwatch, #cards, #insights, #summary, #*-tiles) with no content
Each page is checked at desktop width and at phone width (390 px), where it must not scroll sideways.

Sections a page hides on purpose (the forecast on crops without one, ports on crops that don't
use them) are skipped, because only visible elements are checked.

Usage:
    python src/check_site.py            # check docs/
    python src/check_site.py dashboard  # check dashboard/ (no navigation bar)

Needs Playwright (pip install -r requirements-check.txt) and Google Chrome (or run
`playwright install chromium` and set CHECK_BROWSER=chromium).
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
WIDTHS = (1280, 390)  # desktop, and phone width (where the page must not scroll sideways)

# Runs in the page once it has settled: returns a list of problems.
CHECK_JS = """
() => {
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
    && getComputedStyle(el).visibility !== 'hidden';
  const name = el => '#' + (el.id || el.className);
  const bad = [];
  for (const el of document.querySelectorAll('[id^="ch-"]')) {
    if (!visible(el)) continue;
    const marks = el.querySelectorAll('svg path, svg rect, svg circle, svg line, svg polyline, svg polygon');
    if (marks.length < 3) bad.push(`chart ${name(el)} is empty`);
  }
  for (const el of document.querySelectorAll('table[id$="-table"], [id$="-table"] table, table.flash')) {
    if (!visible(el)) continue;
    const rows = el.tBodies.length ? [...el.tBodies].reduce((n, b) => n + b.rows.length, 0) : el.rows.length - 1;
    if (rows < 1) bad.push(`table ${name(el.id ? el : el.closest('[id]') || el)} has no rows`);
  }
  for (const el of document.querySelectorAll('#watch, #xwatch, #cards, #insights, #summary, [id$="-tiles"]')) {
    if (!visible(el)) continue;
    if (!el.children.length || !el.textContent.trim()) bad.push(`${name(el)} is empty`);
  }
  if (!document.querySelector('[id^="ch-"]')) bad.push('no charts on the page');
  return bad;
}
"""


def pages(site: Path) -> list[str]:
    """Every page in the site: the overview, each crop page, feed and margins."""
    crops = json.loads((site / "data.json").read_text())["meta"]["pages"].values()
    found = [""] + sorted(crops) + ["feed", "margins"]
    missing = [p for p in found if not (site / p / "index.html").exists()]
    if missing:
        raise SystemExit(f"missing pages in {site.name}/: {missing}")
    return found


def serve(site: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


def check_page(browser, base: str, page_path: str, width: int) -> list[str]:
    problems: list[str] = []
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
    page.on("console", lambda m: m.type == "error" and problems.append(f"console error: {m.text}"))
    # Google Fonts is the only external resource; a font that fails to load is not a broken page.
    page.on("requestfailed", lambda r: "fonts.g" not in r.url and problems.append(f"failed to load {r.url}"))
    page.on("response", lambda r: r.status >= 400 and "fonts.g" not in r.url
            and problems.append(f"HTTP {r.status} for {r.url}"))
    page.goto(f"{base}/{page_path}/" if page_path else f"{base}/", wait_until="networkidle")
    page.wait_for_timeout(500)  # let charts that draw after layout finish
    problems += page.evaluate(CHECK_JS)
    if width < 500:
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        if overflow > 1:
            problems.append(f"page scrolls sideways at {width}px ({overflow}px too wide)")
    page.close()
    return problems


def main() -> int:
    site = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "docs")
    httpd, base = serve(site)
    browser_name = os.environ.get("CHECK_BROWSER", "chrome")
    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if browser_name == "chromium" else browser_name)
        for page_path in pages(site):
            label = page_path or "overview"
            problems = [f"{w}px: {x}" for w in WIDTHS for x in check_page(browser, base, page_path, w)]
            for w in problems:
                print(f"FAIL {label}: {w}")
            failed += bool(problems)
            if not problems:
                print(f"ok   {label}")
        browser.close()
    httpd.shutdown()
    if failed:
        print(f"{failed} page(s) failed the render check")
        return 1
    print("All pages rendered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
