"""Producer margins by crop: what an acre earned at April (seeding) and August (harvest) prices.

For each crop and harvest year:
    revenue  = Saskatchewan farm price that month ($/t)  x  trend yield (t/ac)
    margin over variable costs = revenue - the Crop Planning Guide's variable expenses ($/ac)
    margin over total costs    = revenue - the guide's total expenses ($/ac)
    break-even price           = costs / trend yield ($/t)

Sources:
    Costs   Saskatchewan Crop Planning Guide, Dark Brown soil zone, stubble-seeded
            (reference/sk_crop_planning_guide.csv, built by src/crop_guide.py). Published each
            January, so April and August use the same year's budget.
    Price   StatCan 32-10-0077, monthly farm product prices, Saskatchewan ($/t).
    Yield   StatCan 32-10-0359, Saskatchewan average yield, mean of the five previous harvests:
            what a grower could expect at seeding. April and August use the same yield, so the
            change between them is price alone.
    Area    StatCan 32-10-0359, Saskatchewan seeded area.

Years start in 2017: that guide moved to a higher-input system, so earlier budgets are not
comparable. Soybeans are left out (StatCan's Saskatchewan soybean price has too many gaps).

    python src/margins.py     # print the latest table (dashboard_data.py writes it to data.json)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import external

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "reference" / "sk_crop_planning_guide.csv"

FIRST_YEAR = 2017
ZONE = "Dark Brown"
ACRES_PER_HA = 2.471054
LB_PER_T = 2204.62
MONTHS = {"apr": 4, "aug": 8}

# key: (label, guide crop, StatCan price series, StatCan yield/area crop, CGC grain, lb per bushel)
CROPS = {
    "canola": ("Canola", "canola", "Canola (including rapeseed)", "Canola (rapeseed)", "Canola", 50),
    "spring_wheat": ("Spring wheat", "spring_wheat", "Wheat (except durum wheat)", "Wheat, spring", "Wheat", 60),
    "durum": ("Durum", "durum", "Durum wheat", "Wheat, durum", "Amber Durum", 60),
    "malt_barley": ("Malt barley", "malt_barley", "Barley for malt and other human consumption", "Barley", "Barley", 48),
    "feed_barley": ("Feed barley", "feed_barley", "Barley for animal feed", "Barley", "Barley", 48),
    "oats": ("Oats", "oats", "Oats", "Oats", "Oats", 34),
    "peas": ("Peas", "yellow_peas", "Dry peas", "Peas, dry", "Peas", 60),
    "lentils": ("Lentils", "red_lentils", "Lentils", "Lentils", "Lentils", None),  # guide prices lentils per lb
    "flax": ("Flax", "flax", "Flaxseed", "Flaxseed", "Flaxseed", 56),
}


def _prices() -> pd.DataFrame:
    """Saskatchewan monthly farm prices ($/t), columns = our crop keys, index = month start."""
    p = external.statcan("32100077")
    p = p[p["GEO"] == "Saskatchewan"].copy()
    p["series"] = p["Farm products"].str.replace(r"\s*\[\d+\]$", "", regex=True)
    wide = p.pivot_table(index="REF_DATE", columns="series", values="VALUE", aggfunc="first")
    wide.index = pd.to_datetime(wide.index)
    return pd.DataFrame({k: wide[c[2]] for k, c in CROPS.items()})


def _crop_stats() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Saskatchewan yield (t/ac) and seeded area (ha) by harvest year, columns = our crop keys."""
    t = external.statcan("32100359")
    t = t[t["GEO"] == "Saskatchewan"]
    piv = lambda disp: t[t["Harvest disposition"] == disp].pivot_table(index="REF_DATE", columns="Type of crop", values="VALUE", aggfunc="first")
    y = piv("Average yield (kilograms per hectare)") / 1000 / ACRES_PER_HA
    a = piv("Seeded area (hectares)")
    return (pd.DataFrame({k: y[c[3]] for k, c in CROPS.items()}),
            pd.DataFrame({k: a[c[3]] for k, c in CROPS.items()}))


def _guide_price_per_t(price: float, lb_per_bu: int | None) -> float:
    return price * LB_PER_T if lb_per_bu is None else price * LB_PER_T / lb_per_bu


def build() -> dict:
    guide = pd.read_csv(GUIDE)
    guide = guide[guide["zone"] == ZONE].set_index(["crop", "year"])
    prices = _prices()
    yields, area = _crop_stats()
    latest_month = prices.dropna(how="all").index.max()
    last_year = int(guide.index.get_level_values("year").max())
    years = list(range(FIRST_YEAR, last_year + 1))

    r2 = lambda v: None if v is None or not np.isfinite(v) else round(float(v), 2)
    rows: dict = {}
    for key, (label, gcrop, *_rest, lb_bu) in CROPS.items():
        for year in years:
            if (gcrop, year) not in guide.index:
                continue
            g = guide.loc[(gcrop, year)]
            prior = yields[key].loc[year - 5:year - 1].dropna()
            if len(prior) < 5:
                continue
            ty = prior.mean()                             # trend yield, t/ac
            rec = {
                "yield_t_ac": r2(ty),
                "var_cost": r2(g["var_cost"]), "total_cost": r2(g["total_cost"]),
                "be_var": r2(g["var_cost"] / ty), "be_total": r2(g["total_cost"] / ty),
                "guide_price": r2(_guide_price_per_t(g["price"], lb_bu)),
                "area_ha": r2(area[key].get(year)), "area_prev_ha": r2(area[key].get(year - 1)),
            }
            # "latest" is the most recent published month, taken at the same calendar month in
            # every year so it compares year on year like April and August
            points = {m: pd.Timestamp(year, mo, 1) for m, mo in {**MONTHS, "latest": latest_month.month}.items()}
            for m, when in points.items():
                price = prices[key].get(when)
                if price is None or not np.isfinite(price):
                    continue
                rev = price * ty
                rec[m] = {"price": r2(price), "revenue": r2(rev),
                          "rovc": r2(rev - g["var_cost"]), "rotc": r2(rev - g["total_cost"])}
            rows.setdefault(key, {})[str(year)] = rec

    return {
        "meta": {
            "zone": ZONE,
            "first_year": str(FIRST_YEAR),
            "last_year": str(last_year),
            "latest_price_month": latest_month.strftime("%Y-%m"),
            "latest_year": str(latest_month.year),
        },
        "crops": [{"key": k, "label": c[0], "grain": c[4]} for k, c in CROPS.items()],
        "years": [str(y) for y in years],
        "rows": rows,
    }


if __name__ == "__main__":
    d = build()
    print(d["meta"])
    for m in ("apr", "aug", "latest"):
        tab = {c["label"]: {y: (v.get(m) or {}).get("rovc") for y, v in d["rows"][c["key"]].items()} for c in d["crops"]}
        print(f"\nmargin over variable costs, $/ac, {m}")
        print(pd.DataFrame(tab).T.to_string())
