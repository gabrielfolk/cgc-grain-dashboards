"""Saskatchewan Crop Planning Guide: per-acre yield, price and costs by crop, soil zone and year.

Saskatchewan Agriculture publishes the guide each January, before seeding. It budgets each
crop per acre for the Brown, Dark Brown and Black soil zones: target yield, expected farm-gate
price, variable expenses (seed, fertilizer, chemicals, fuel, repairs, custom work, insurance,
interest) and total expenses (variable plus depreciation, taxes and imputed interest on land,
machinery and buildings).

    python src/crop_guide.py            # parse cached PDFs, write reference/sk_crop_planning_guide.csv
    python src/crop_guide.py --refresh  # re-download the PDFs first

Two layouts:
    2013-2019  one wide table per soil zone, crops as columns (headers stacked over several lines)
    2020-      one page per crop, soil zones as columns
Both are read by position: headers and numbers are right-aligned, so each number belongs to the
header words that share its right edge. Every column is checked against the guide's own
arithmetic (yield x price = gross revenue) before it is kept.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd
import pdfplumber
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "external" / "sk_cpg"
OUT = ROOT / "reference" / "sk_crop_planning_guide.csv"
UA = {"User-Agent": "Mozilla/5.0"}

# Saskatchewan Publications Centre archive (product 122661) holds 2015 on; usask library holds earlier years
PUBSASK = "https://publications.saskatchewan.ca/api/v1/products/122661/formats/{}/download"
USASK = "https://library.usask.ca/gp/sk/da/crops/Cropplanguide/guide/{}.pdf"
FORMAT_IDS = {2015: 146670, 2016: 146669, 2017: 146668, 2018: 146667, 2019: 146666, 2020: 146665,
              2021: 146664, 2022: 146663, 2023: 146662, 2024: 146661, 2025: 142532, 2026: 153138}
URLS = {2013: USASK.format(2013), 2014: USASK.format(2014), **{y: PUBSASK.format(i) for y, i in FORMAT_IDS.items()}}

ZONES = {"brown": "Brown", "dark brown": "Dark Brown", "black": "Black"}
# guide crop name (lower case, singular/plural and label variants) -> our key
CROPS = {
    "canola": "canola", "ht canola": "canola",
    "spring wheat": "spring_wheat", "hard red spring wheat": "spring_wheat",
    "durum wheat": "durum",
    "malt barley": "malt_barley", "feed barley": "feed_barley",
    "oats": "oats",
    "edible yellow peas": "yellow_peas", "edible yellow pea": "yellow_peas", "yellow peas": "yellow_peas",
    "edible green peas": "green_peas", "edible green pea": "green_peas", "green peas": "green_peas",
    "red lentils": "red_lentils", "red lentil": "red_lentils",
    "large green lentils": "large_green_lentils", "large green lentil": "large_green_lentils",
    "flax": "flax", "soybean": "soybeans", "soybeans": "soybeans",
}
ROWS = {  # row key -> label pattern
    "yield": re.compile(r"(Estimated|Target) Yield"),
    "price": re.compile(r"Price\s*(/|\(\$/)"),
    "gross": re.compile(r"Gross Revenue"),
    "var_cost": re.compile(r"Total Variable Expenses"),
    "total_cost": re.compile(r"Total Expenses"),
}
NUM = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d+)?$|^-?\d+(\.\d+)?$")


def download(refresh: bool = False) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for year, url in URLS.items():
        path = RAW / f"{year}.pdf"
        if path.exists() and not refresh:
            continue
        for _ in range(4):
            try:
                r = requests.get(url, headers=UA, timeout=120)
            except requests.RequestException:
                continue
            if r.ok and r.content[:4] == b"%PDF":
                path.write_bytes(r.content)
                break
        else:
            print(f"could not download the {year} guide from {url}")


def _lines(words: list[dict]) -> list[list[dict]]:
    """Group words into text lines by vertical position."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) < 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in lines]


def _tables(page) -> list[dict]:
    """Tables on a page: [{"top", "header_words", "columns": {x1: {row: value}}}].

    A table starts at a yield row. Its columns are the right edges of the numbers in its
    rows; header words are those above the yield row (and below the previous table) whose
    right edge lines up with a column.
    """
    lines = _lines(page.extract_words())
    text = lambda l: " ".join(w["text"] for w in l)
    starts = [i for i, l in enumerate(lines) if ROWS["yield"].search(text(l))]
    tables = []
    prev_end = 0.0   # top of the previous table's last budget row
    for n, s in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        rows: dict[str, dict[float, float]] = {}
        last_row = lines[s][0]["top"]
        for l in lines[s:end]:
            t = text(l)
            key = next((k for k, pat in ROWS.items() if pat.search(t)), None)
            if key is None or key in rows:
                continue
            last_row = l[0]["top"]
            nums = [w for w in l if NUM.match(w["text"])]
            # labels like "(A)" and "(AxB)=(C)" never parse as numbers, so all nums are values
            rows[key] = {round(w["x1"]): float(w["text"].replace(",", "")) for w in nums}
        header_top, prev_end = prev_end, last_row
        if not rows.get("yield"):
            continue
        cols = sorted(set().union(*[set(r) for r in rows.values()]))
        # merge right edges within 8 pt into one column (decimal places shift the edge a little);
        # a column sits at its rightmost edge, where the headers align
        groups: list[list[float]] = []
        for c in cols:
            if groups and c - groups[-1][-1] <= 8:
                groups[-1].append(c)
            else:
                groups.append([c])
        merged = [g[-1] for g in groups]
        snap = lambda x: min(merged, key=lambda c: abs(c - x))
        columns = {c: {} for c in merged}
        for key, vals in rows.items():
            for x, v in vals.items():
                columns[snap(x)][key] = v
        # header region: lines between the previous table's last budget row and this yield row
        header = [w for l in lines if header_top < l[0]["top"] < lines[s][0]["top"] for w in l]
        tables.append({"header_words": header, "columns": columns})
    return tables


def _header_for(col_x1: float, header_words: list[dict], tol: float = 6) -> str:
    # skip the all-caps zone heading ("DARK BROWN SOIL ZONE 2017") and footnote asterisks
    ws = [w for w in header_words if abs(w["x1"] - col_x1) <= tol and not re.fullmatch(r"[A-Z'()]+|\d{4}", w["text"])]
    return " ".join(w["text"].rstrip("*") for w in sorted(ws, key=lambda w: w["top"]))


def _check(rec: dict) -> bool:
    """The guide's own arithmetic: yield x price = gross revenue, costs present and ordered."""
    y, p, g, v, t = (rec.get(k) for k in ("yield", "price", "gross", "var_cost", "total_cost"))
    if None in (y, p, g, v, t):
        return False
    return abs(y * p - g) <= max(1.0, 0.01 * g) and 0 < v < t


def parse_year(year: int) -> list[dict]:
    out = []
    zone = None
    with pdfplumber.open(RAW / f"{year}.pdf") as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            full = " ".join(w["text"] for w in words)
            if year <= 2019:
                m = re.search(r"(DARK BROWN|BROWN|BLACK)\s+SOIL\s+Z\s*ONE", full.upper())
                if m:
                    zone = ZONES[m.group(1).lower()]
            for tab in _tables(page):
                if year <= 2019:
                    if zone is None:
                        continue
                    hw = [w for w in tab["header_words"] if w["text"] not in ("Seeded", "Crops", "Stubble", "Crop")]
                    head = " ".join(w["text"] for w in tab["header_words"])
                    fallow_only = "Fallow Seeded" in head and "Stubble" not in head
                    found = []
                    for x1, rec in sorted(tab["columns"].items()):
                        name = re.sub(r"\s+", " ", _header_for(x1, hw)).strip().lower()
                        if name in CROPS:
                            found.append((name, rec))
                    # 2013-2015 tables put fallow-seeded columns left of stubble-seeded ones, so a crop
                    # listed twice is fallow first, stubble second
                    names = [n for n, _ in found]
                    for i, (name, rec) in enumerate(found):
                        twice = names.count(name) > 1
                        seeded = "fallow" if fallow_only or (twice and names.index(name) == i) else "stubble"
                        out.append({"year": year, "zone": zone, "crop": CROPS[name], "guide_name": name, "seeded": seeded, **rec})
                else:
                    first = " ".join(w["text"] for w in _lines(words)[0]) if words else ""
                    title = re.match(rf"{year}\s+(.+)$", first)
                    if not title:
                        continue
                    name = re.sub(r"\*+", "", title.group(1)).strip().lower()
                    crop = CROPS.get(name)
                    if crop is None:
                        continue
                    # zone headers sit a few points off the numbers, so pair them by order, left to right:
                    # "Dark" stacked over "Brown" makes Dark Brown
                    hw = tab["header_words"]
                    darks = [w for w in hw if w["text"] == "Dark"]
                    zones = []
                    for w in hw:
                        w = {**w, "text": w["text"].rstrip("*")}
                        if w["text"] == "Black":
                            zones.append((w["x1"], "Black"))
                        elif w["text"] == "Brown":
                            dark = any(abs(d["x1"] - w["x1"]) < 12 and d["top"] < w["top"] for d in darks)
                            zones.append((w["x1"], "Dark Brown" if dark else "Brown"))
                    cols = [(x1, rec) for x1, rec in sorted(tab["columns"].items()) if len(rec) == len(ROWS)]
                    if len(cols) != len(zones):
                        continue
                    for (x1, rec), (_, z) in zip(cols, sorted(zones)):
                        out.append({"year": year, "zone": z, "crop": crop, "guide_name": name, "seeded": "stubble", **rec})
    return out


def build() -> pd.DataFrame:
    """Stubble-seeded budgets for every crop, zone and year, checked against the guide's arithmetic."""
    rows = []
    for year in URLS:
        if (RAW / f"{year}.pdf").exists():
            rows += parse_year(year)
    df = pd.DataFrame(rows).drop_duplicates()
    df = df[df["seeded"] == "stubble"].drop(columns="seeded")
    bad = df[~df.apply(_check, axis=1)]
    if len(bad):
        raise ValueError(f"guide columns fail yield x price = gross revenue:\n{bad}")
    # The 2019 guide prints the Brown-zone cereals twice; the second copy (a two-page spread)
    # uses fuel and insurance costs that match no other zone. Keep the first table printed.
    key = ["year", "zone", "crop"]
    conflicts = df[df.duplicated(key, keep=False)]
    if len(conflicts):
        print(f"kept the first of {len(conflicts)} conflicting budgets: "
              + ", ".join(sorted({f"{r.year} {r.zone} {r.crop}" for r in conflicts.itertuples()})))
    df = df.drop_duplicates(key, keep="first")
    df["yield_unit"] = df["yield"].map(lambda v: "lb/ac" if v > 400 else "bu/ac")   # lentils are budgeted in lb
    df["source"] = df["year"].map(URLS)
    cols = ["year", "zone", "crop", "guide_name", "yield", "yield_unit", "price", "gross", "var_cost", "total_cost", "source"]
    return df[cols].sort_values(["year", "crop", "zone"]).reset_index(drop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    download(args.refresh)
    df = build()
    df.to_csv(OUT, index=False)
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(df)} budgets, {df['year'].min()}-{df['year'].max()}")
    print(df[df["zone"] == "Dark Brown"].pivot_table(index="crop", columns="year", values="var_cost").round(0).to_string())
