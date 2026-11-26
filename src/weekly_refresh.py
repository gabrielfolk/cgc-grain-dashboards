"""Run the whole weekly refresh: re-download every source, rebuild every page, and check the built site.

Steps, in order (any failure stops the refresh, and nothing is published):
    external.py --refresh    StatCan tables, AAFC and USDA files, CBOT and USD/CAD (Yahoo)
    ingest.py                CGC Grain Statistics Weekly, with its checks on the current crop year
    dashboard_data.py, grain_data.py, canola_forecast.py, feed_data.py, margins_data.py, methods_data.py
    build_site.py            docs/
    check_site.py            headless Chrome render check of every page in docs/

It skips publishing (after the downloads, before any page is rebuilt) when there is no new CGC week and no
source other than the market series changed, unless --force. It
writes a short summary to data/refresh_summary.md: the first line is the commit message, and the rest lists
what changed (CGC week, sources) and any warnings to look at.

Usage:
    python src/weekly_refresh.py          # what the scheduled GitHub Action runs
    python src/weekly_refresh.py --force  # rebuild and check even if nothing new came in

Exit codes: 0 when the site was rebuilt and checked, or nothing new came in (see "updated" below);
1 when a step failed, or when there is no new CGC week and the latest one is more than 11 days old
(CGC missed a week or moved the file). In a GitHub Action it also sets the step output updated=true|false.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EXTERNAL = ROOT / "data" / "raw" / "external"
SUMMARY = ROOT / "data" / "refresh_summary.md"
PUBLISHED = ROOT / "data" / "published_sources.json"  # source hashes as of the last successful build
BUILD = ["dashboard_data.py", "grain_data.py", "canola_forecast.py", "feed_data.py", "margins_data.py", "methods_data.py", "build_site.py"]

# Human names for the cached source files, for the summary.
SOURCE_NAMES = {
    "32100359": "StatCan 32-10-0359 (production)", "32100077": "StatCan 32-10-0077 (farm prices)",
    "32100007": "StatCan 32-10-0007 (stocks)", "aafc_red_meat": "AAFC red meat slaughter",
    "aafc_poultry": "AAFC poultry slaughter", "ers_cattle": "USDA ERS cattle trade",
}


def run(script: str, *args: str) -> str:
    """Run one of the src/ scripts, echo its output, and return it. Raises on failure."""
    print(f"\n===== {script} {' '.join(args)}", flush=True)
    proc = subprocess.run([sys.executable, str(ROOT / "src" / script), *args], cwd=ROOT,
                          capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    print(out[-6000:], flush=True)
    if proc.returncode:
        raise RuntimeError(f"{script} failed (exit {proc.returncode})\n\n{out[-3000:]}")
    return out


def source_hashes() -> dict[str, str]:
    """Hash of each cached source file, except the Yahoo series (which change every day)."""
    return {p.stem: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(EXTERNAL.glob("*.csv"))}


def published_week() -> tuple[str, int] | None:
    """The CGC crop year and week the published site shows."""
    path = ROOT / "docs" / "data.json"
    if not path.exists():
        return None
    meta = json.loads(path.read_text())["meta"]
    return meta["current_year"], int(meta["latest_week"])


def ingested_week() -> tuple[str, int, str]:
    df = pd.read_parquet(ROOT / "data" / "processed" / "gsw.parquet", columns=["crop_year", "grain_week", "week_ending"])
    year = df["crop_year"].astype(str).max()
    cur = df[df["crop_year"].astype(str) == year]
    week = int(cur["grain_week"].max())
    ending = cur.loc[cur["grain_week"] == week, "week_ending"].iloc[0]
    return year, week, f"{ending:%Y-%m-%d}"


def set_output(name: str, value: str) -> None:
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="rebuild and check even if nothing new came in")
    args = ap.parse_args()
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    set_output("updated", "false")

    before_week = published_week()
    # Compare with the last successful build, not the start of this run, so a source that changed
    # during a failed run is still picked up next time.
    before_sources = json.loads(PUBLISHED.read_text()) if PUBLISHED.exists() else source_hashes()
    warnings: list[str] = []
    try:
        out = run("external.py", "--refresh")
        warnings += [line for line in out.splitlines() if line.startswith("WARNING")]
        out = run("ingest.py")
        warnings += [line for line in out.splitlines() if line.startswith("WARNING")]
    except RuntimeError as e:
        SUMMARY.write_text(f"Refresh failed while downloading\n\n```\n{e}\n```\n")
        print(f"\nREFRESH FAILED: {e}")
        return 1

    year, week, ending = ingested_week()
    after_sources = source_hashes()
    # Only files already in the baseline count: a file that is new to the cache (an empty cache on
    # the first run, or a table a script fetches for the first time) is not new data.
    changed_keys = [k for k in after_sources if k in before_sources and before_sources[k] != after_sources[k]]
    changed = [SOURCE_NAMES.get(k, f"StatCan {k[:2]}-{k[2:4]}-{k[4:]}" if k.isdigit() else k) for k in changed_keys]
    new_week = before_week is None or (year, week) > before_week

    if not (new_week or changed or args.force):
        stale = [w for w in warnings if "days ago" in w]  # ingest.py: latest week older than STALE_DAYS
        if stale:  # CGC missed a week or moved the file (as when it moved to canada.ca in autumn 2026)
            SUMMARY.write_text(f"Refresh failed: no new CGC week\n\nThe site still shows {year} week {week} "
                               f"(ending {ending}).\n\n```\n{stale[0]}\n```\n\nIf this is the combined "
                               "Christmas report, it will clear when the next week is published.\n")
            print(f"\nREFRESH FAILED: no new CGC week. {stale[0]}")
            return 1
        PUBLISHED.write_text(json.dumps({**source_hashes(), **before_sources}, indent=1))  # add new files only
        msg = f"No new data: the site already shows CGC {year} week {week}, and no source changed."
        SUMMARY.write_text(msg + "\n")
        print(f"\n{msg}")
        return 0

    try:
        for script in BUILD:
            out = run(script)
            warnings += [line for line in out.splitlines() if line.startswith("WARNING")]
        run("check_site.py")
    except RuntimeError as e:
        SUMMARY.write_text(f"Refresh failed while building CGC week {week}\n\n```\n{e}\n```\n")
        print(f"\nREFRESH FAILED: {e}")
        return 1

    parts = [f"CGC week {week} (ending {ending})" if new_week else None,
             ", ".join(changed) if changed else None]
    title = "Refresh: " + "; ".join(p for p in parts if p) if any(parts) else "Refresh: rebuild"
    lines = [title, "", f"Automated weekly refresh, {dt.date.today():%Y-%m-%d}.", ""]
    lines.append(f"- CGC: {year} week {week}, ending {ending}"
                 + ("" if new_week else " (no new week)"))
    lines.append("- Sources changed: " + (", ".join(changed) if changed else "none"))
    if any(k.isdigit() for k in changed_keys):
        lines.append("- A StatCan table changed. If it is a canola production estimate, add it to "
                     "reference/statcan_canola_vintages.csv (see README, Other updates).")
    lines += [f"- {w}" for w in warnings]
    lines.append("- Render check: all pages passed")
    SUMMARY.write_text("\n".join(lines) + "\n")
    PUBLISHED.write_text(json.dumps(source_hashes(), indent=1))  # after the build, which may fetch more tables
    set_output("updated", "true")
    print("\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
