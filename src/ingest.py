"""Download and normalize Canadian Grain Commission "Grain Statistics Weekly" data.

Each crop year (Aug 1 - Jul 31) is published as one long-format CSV that CGC
re-publishes every week. Completed crop years are cached in data/raw/ and never
re-downloaded; the current crop year is always refreshed.

Output: data/processed/gsw.parquet with columns
    crop_year, grain_week, week_ending, worksheet, metric, period,
    grain, grade, region, ktonnes

Usage:
    python src/ingest.py            # fetch missing years + refresh current
    python src/ingest.py --refresh  # re-download every year
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_PATH = ROOT / "data" / "processed" / "gsw.parquet"

BASE_URL = "https://www.grainscanada.gc.ca/en/grain-research/statistics/grain-statistics-weekly"
# CGC moved to canada.ca in autumn 2026; the grainscanada.gc.ca copy of 2026-27 stopped at week 9.
CANADA_CA_URL = "https://www.canada.ca/content/dam/cgc-ccg/documents/statistiques-statistics/grain-statistics-weekly"
FIRST_CANADA_CA_YEAR = 2021  # 2021-22 on are on canada.ca (earlier years only on the old site, verified 2026-11-12)
FIRST_SHORT_NAME_YEAR = 2025  # canada.ca names files grain-stats-weekly-* from 2025-26, grain-statistics-weekly-* before
FIRST_CROP_YEAR = 2013  # 2012-13 and earlier are PDF only
LAST_LEGACY_YEAR = 2017  # 2013-14 .. 2017-18 live under a /csv/ subfolder

COLUMNS = [
    "crop_year", "grain_week", "week_ending", "worksheet", "metric",
    "period", "grain", "grade", "region", "ktonnes",
]
LABEL_COLUMNS = ["crop_year", "worksheet", "metric", "period", "grain", "grade", "region"]

# Spelling variants and typos seen across crop years -> canonical label.
FIXES = {
    "region": {
        "Sasktachewan": "Saskatchewan",
        "B.C.": "British Columbia",
        "Alberta/BC": "Alberta & B.C.",
        "-": "",
    },
    "period": {"Week ago": "Week Ago"},
    "grain": {
        "Peas*": "Peas",
        "Sunflower*": "Sunflower",
        "Fababeans": "Faba Beans",
        "Safflower Seed": "Safflower",
        "U.S. Flaxseed": "U.S. Flax",
        "Ukrainian Whe": "Ukrainian Wheat",
        "European Rape": "European Rapeseed",
    },
}


def crop_year_label(start: int) -> str:
    return f"{start}-{str(start + 1)[-2:]}"


def current_crop_year_start(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today.month >= 8 else today.year - 1


def csv_url(start: int) -> str:
    if start >= FIRST_CANADA_CA_YEAR:
        years = f"{start}-{start + 1}"
        name = "grain-stats-weekly" if start >= FIRST_SHORT_NAME_YEAR else "grain-statistics-weekly"
        return f"{CANADA_CA_URL}/{years}/{name}-{years}.csv"
    sub = "csv/" if start <= LAST_LEGACY_YEAR else ""
    return f"{BASE_URL}/{crop_year_label(start)}/{sub}gsw-shg-en.csv"


def download(start: int, force: bool) -> Path | None:
    """Fetch one crop year's CSV into data/raw/ (completed years are reused unless forced). None if CGC hasn't published it."""
    path = RAW_DIR / f"gsw-{crop_year_label(start)}.csv"
    if path.exists() and not force:
        return path

    url = csv_url(start)
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
    if resp.status_code == 404:  # canada.ca answers a not-yet-published year with a real 404
        print(f"  {crop_year_label(start)}: not available ({url})")
        return None
    resp.raise_for_status()
    # Missing files come back as HTTP 200 with an HTML error page.
    if resp.content.lstrip()[:1] == b"<":
        print(f"  {crop_year_label(start)}: not available ({url})")
        return None

    path.write_bytes(resp.content)
    print(f"  {crop_year_label(start)}: downloaded {len(resp.content) / 1e6:.1f} MB")
    return path


def normalize(path: Path) -> pd.DataFrame:
    """Read one raw CSV into the standard columns, whatever its header spelling and column order."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    # Header spelling ("crop_year" vs "Crop Year") and column order both vary
    # between crop years, so map by normalized name.
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns={"week_ending_date": "week_ending"})
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    df = df[COLUMNS]

    for col in LABEL_COLUMNS:
        df[col] = df[col].str.strip()
    df["grain_week"] = pd.to_numeric(df["grain_week"]).astype("int16")
    df["week_ending"] = fix_week_dates(df, pd.to_datetime(df["week_ending"], format="%d/%m/%Y"))
    df["ktonnes"] = pd.to_numeric(df["ktonnes"].str.replace(",", ""), errors="coerce")
    return clean(df)


def fix_week_dates(df: pd.DataFrame, dates: pd.Series) -> pd.Series:
    """Repair week-ending dates entered month-first (e.g. 2024-25 week 48 is
    published as 06/07/2025 instead of 06/07 -> 2025-07-06).

    A week's date should fall 7 days after the previous week's; if it doesn't
    but the day/month-swapped date does, use the swapped date.
    """
    per_week = dates.groupby(df["grain_week"]).first().sort_index()
    fixed = per_week.copy()
    for week in per_week.index[1:]:
        expected = fixed.get(week - 1, pd.NaT) + pd.Timedelta(days=7)
        d = per_week[week]
        if pd.isna(expected) or abs((d - expected).days) <= 7 or d.day > 12:
            continue
        swapped = d.replace(month=d.day, day=d.month)
        if abs((swapped - expected).days) <= 7:
            fixed[week] = swapped
    return df["grain_week"].map(fixed)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    # Blank cells are either empty table cells or footnote text that leaked into
    # label columns (e.g. grain="There has been a downward adju..."); drop both.
    """Drop rows without a value (blank cells and leaked footnote text) and fix known label typos."""
    df = df[df["ktonnes"].notna()].copy()
    for col, mapping in FIXES.items():
        df[col] = df[col].replace(mapping)
    return df


# Main crop-year flows: CGC revisions move these by a fraction of a percent, never more than 2% down
# (checked over 2013-14 to 2025-26). Smaller series (imports, producer cars) do drop.
MAIN_FLOWS = [("Primary", "Deliveries"), ("Primary", "Shipments"), ("Process", "Producer Deliveries"),
              ("Process", "Milled/Mfg Grain"), ("Terminal Exports", "Exports")]
STALE_DAYS = 11  # CGC publishes Thursday for the week ending the Sunday before; older than this means a missed week


def check(df: pd.DataFrame, current: str, today: dt.date | None = None) -> tuple[list[str], list[str]]:
    """Sanity checks on the current crop year. Returns (errors, warnings).

    Errors mean the published file is wrong (a repeated week, dates that don't advance, a missing
    week that isn't the Christmas combined report, a main flow falling), so the refresh must stop.
    """
    errors, warnings = [], []
    cur = df[df["crop_year"] == current]
    if cur.empty:
        return [f"{current}: no rows"], warnings
    dates = cur.groupby("grain_week")["week_ending"].first().sort_index()
    weeks = list(dates.index)

    for prev, week in zip(weeks, weeks[1:]):
        step = (dates[week] - dates[prev]).days
        if step <= 0:
            errors.append(f"week {week} ends {dates[week]:%Y-%m-%d}, not after week {prev} ({dates[prev]:%Y-%m-%d})")
        elif week - prev > 1 and week == 52:  # 2014-15 folded week 51 into the final Jul 31 report
            warnings.append(f"weeks {prev + 1}-{week - 1} missing before the final week")
        elif week - prev > 1 and step != 7 * (week - prev):
            errors.append(f"weeks {prev + 1}-{week - 1} missing and dates step {step} days")
        elif week - prev > 1 and not (19 <= prev + 1 <= 23):  # the combined holiday report falls around week 21
            errors.append(f"weeks {prev + 1}-{week - 1} missing")
        elif week - prev == 1 and step != 7 and week != 52:  # week 52 always ends Jul 31
            warnings.append(f"week {week}: dates step {step} days")
    if weeks[0] != 1:
        warnings.append(f"crop year starts at week {weeks[0]}")

    key = ["worksheet", "metric", "period", "grain", "grade", "region"]
    values = cur.pivot_table(index=key, columns="grain_week", values="ktonnes", aggfunc="first", observed=True)
    for prev, week in zip(weeks, weeks[1:]):
        both = values[prev].notna() & values[week].notna() & (values[prev] != 0)
        if both.sum() > 100 and (values.loc[both, prev] == values.loc[both, week]).mean() > 0.95:
            errors.append(f"week {week} repeats week {prev} (CGC published the same numbers twice)")

    flows = cur[cur["period"] == "Crop Year"].groupby(["worksheet", "metric", "grain_week"], observed=True)["ktonnes"].sum()
    for ws, metric in MAIN_FLOWS:
        if (ws, metric) not in flows.index.droplevel(2):
            errors.append(f"{ws} / {metric}: missing")
            continue
        series = flows.loc[(ws, metric)].sort_index()
        change = series.diff() / series.shift()
        for week, c in change[(change < -0.02) & (series.shift() > 50)].items():
            errors.append(f"{ws} / {metric} crop-year total falls {c:.0%} in week {week}")

    age = ((today or dt.date.today()) - dates.iloc[-1].date()).days
    if age > STALE_DAYS:
        warnings.append(f"latest week {weeks[-1]} ended {dates.iloc[-1]:%Y-%m-%d}, {age} days ago: CGC may have "
                        f"missed a week or moved the file (check {CANADA_CA_URL.split('/content')[0]})")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-download all crop years")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    current = current_crop_year_start()
    print(f"Fetching crop years {crop_year_label(FIRST_CROP_YEAR)} .. {crop_year_label(current)}")
    paths = [
        p
        for start in range(FIRST_CROP_YEAR, current + 1)
        if (p := download(start, force=args.refresh or start == current))
    ]

    print("Normalizing...")
    df = pd.concat([normalize(p) for p in paths], ignore_index=True)
    errors, warnings = check(df, f"{current}-{current + 1}")
    for w in warnings:
        print(f"WARNING: {w}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        print(f"Not written: {OUT_PATH.relative_to(ROOT)} keeps the last good data. Check CGC's weekly Excel "
              "reports, and rerun once CGC fixes the file.")
        return 1

    for col in LABEL_COLUMNS:
        df[col] = df[col].astype("category")
    df.to_parquet(OUT_PATH, index=False)

    print(
        f"Wrote {OUT_PATH.relative_to(ROOT)}: {len(df):,} rows, "
        f"{df['crop_year'].nunique()} crop years, "
        f"{df['week_ending'].min():%Y-%m-%d} .. {df['week_ending'].max():%Y-%m-%d}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
