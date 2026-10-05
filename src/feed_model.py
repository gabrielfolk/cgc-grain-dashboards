"""Feed demand model: grain fed by livestock, estimated from animal numbers and feeding rates.

StatCan's "animal feed, waste and dockage" is the residual of its supply and disposition
balance: whatever isn't exported, processed, seeded or left in stock. This model builds feed
use from the demand side instead, the way StatCan's own Livestock Feed Requirements Study did:

    grain fed = animals (by class and province) x grain fed per animal

Feeding rates
    StatCan, Livestock Feed Requirements Study 1999-2001 (catalogue 23-501-X, Table 7): grain fed
    per animal by class and province, by ingredient (wheat, oats, barley, corn, ...), from
    provincial livestock specialists, the Animal Nutrition Association of Canada and feed mills.
    Saved in reference/statcan_livestock_feed_1999.csv. It is the latest edition (January 2003),
    so classes whose feeding has changed are updated from current published guides:
      feedlot cattle   Manitoba Agriculture, Cost of Production: Beef Feedlot Finishing (2026):
                       18.5 lb/day rolled barley, applied to fed cattle marketed (see Cattle on feed)
      backgrounding    Manitoba Agriculture, Cost of Production: Beef Backgrounding (2026): 5 to
                       8 lb/day barley (500 to 900 lb), applied per head on feeder and stocker
                       operations per day
      hogs             Manitoba Agriculture, Cost of Production: Swine Farrow-Finish (2025):
                       279.3 kg of feed per pig grown 26 to 123 kg, 33.2 kg in the nursery, sows
                       3.0 kg/day dry and 6.5 kg/day lactating. Each stage's 1999 rate is scaled
                       by the guide's amount over Manitoba's 1999 amount.
      dairy cows       the 1999 rate scaled by milk sold per cow against 1999 (StatCan
                       32-10-0113 and 32-10-0130). Alberta's Dairy Cost Study finds milk per kg of
                       concentrate flat at 2.2 litres from 1997 to 2019 (Alberta Agriculture,
                       "Explained in Brief: Dairy Feed Costs", 2021), so concentrate rises with milk.
      broilers, turkeys  the 1999 rate per kilogram of meat produced (StatCan 32-10-0117), so
                       heavier birds need more feed
    Other classes (beef cows, bulls, replacement heifers, layers, sheep, veal calves) use the
    1999 rate. Horses, other poultry and fish are left out (no current counts; under 0.4 Mt).

What the model counts: grain an animal needs, on 1999 feeding practice. Co-products that replace
grain in today's rations (distillers' grains from ethanol plants, millfeeds, bakery waste) are
not netted out, so where they are fed, mostly in Ontario and Quebec, the model runs above the
grain actually used.

Cattle on feed
    Grain fed to finishing cattle = fed cattle marketed x days on feed x 18.5 lb/day.
    Fed cattle marketed = (slaughter + live international exports, StatCan 32-10-0139, by
    province, July to June) x the steer and heifer share of slaughter (FED_SHARE, AAFC federally
    inspected slaughter). Days on feed = the region's average of cattle on feeding operations x
    365 / fed cattle marketed, over the years with both, so the level matches StatCan's
    inventories while the year-to-year change follows marketings. For a crop year whose
    marketings aren't published yet, last year's marketings move with the July 1 cattle on feed.

Grain mix
    Each class keeps its 1999 provincial mix of wheat, oats, barley and corn, except western
    feedlot and backgrounding cattle, which follow the Manitoba guides (barley) with a share of
    wheat and corn (see WEST_CATTLE_MIX). The mix then shifts each crop year with relative price
    and availability:
        share_i  proportional to  base_share_i x price_i ^ -sigma x availability_i ^ alpha
    price_i = energy-adjusted price over its average (Alberta farm prices and US corn delivered to
    southern Alberta in the West, Ontario farm prices in the East); availability_i = supply over
    its average (West only; see feed_data.availability()). sigma and alpha are passed in
    (feed_data fits them to how StatCan's grain shares move from year to year). The West's corn is then set to
    StatCan's measured western corn use where published (see demand()), because the 1999 rations
    predate Manitoba's corn crop.

Durum
    Durum and wheat trade independently, so durum is its own grain. The 1999 study reports
    wheat only (durum included), so in the West each class's wheat is split into wheat and durum
    at durum's usual share of western wheat and durum fed (see demand()), and durum then shifts
    on its own price (Alberta durum). No durum is grown or fed in the East.

Regions: West = MB, SK, AB, BC; East = ON, QC and the Atlantic provinces.
Crop year Aug-Jul: inventories average July 1 of the first year and January 1 of the second;
annual flows (pig crop, poultry meat, milk) use the calendar year the crop year starts in.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import external

ROOT = Path(__file__).resolve().parent.parent
COEFFS = ROOT / "reference" / "statcan_livestock_feed_1999.csv"

WEST = ["Manitoba", "Saskatchewan", "Alberta", "British Columbia"]
EAST = ["Ontario", "Quebec", "New Brunswick", "Nova Scotia", "Prince Edward Island", "Newfoundland and Labrador"]
PROVINCES = WEST + EAST
FEED_GRAINS = ["barley", "wheat", "durum", "oats", "corn"]
STUDY_GRAINS = ["barley", "wheat", "oats", "corn"]  # the 1999 study's columns; its wheat includes durum

LB = 0.45359237 / 1000          # tonnes per pound
FEEDLOT_BARLEY_LB_DAY = 18.5     # Manitoba feedlot finishing guide, 2026
BACKGROUND_BARLEY_LB_DAY = 6.5   # Manitoba backgrounding guide, 2026: 5, 6, 7 and 8 lb/day by weight band
BACKGROUND_DAYS = 160            # same guide: 500 to 900 lb over 160 days, over winter
# Veal calf slaughter by province, 1999 (study table 6), to split Canada's calf slaughter
VEAL_SHARE_1999 = {"Newfoundland and Labrador": 0.6, "Prince Edward Island": 1.1, "Nova Scotia": 4.1, "New Brunswick": 2.8,
                   "Quebec": 221.9, "Ontario": 130.8, "Manitoba": 9.9, "Saskatchewan": 12.2, "Alberta": 19.3, "British Columbia": 9.1}
# Manitoba farrow-finish guide (2025) against Manitoba's 1999 rates, complete feed per head
HOG_FACTORS = {
    "Feeder Pigs": 279.3 / 256.0,           # kg per pig grown 26-123 kg / 1999 feeder pig
    "Weaner Pigs": 33.2 / 59.0,             # nursery feed per pig / 1999 weaner
    "Sows & Bred Gilts": (6.5 * 50 + 3.0 * 315) / 1019.0,  # ~50 lactating days a year
}
# Western feedlot and backgrounding grain mix: barley-based (Manitoba guides; Western Canadian
# finishing diets are typically over 80% barley grain), with some wheat and corn
WEST_CATTLE_MIX = {"barley": 0.85, "wheat": 0.08, "corn": 0.07, "oats": 0.0}  # wheat includes durum
# East (Ontario/Quebec) feedlots: the 1999 Ontario finished-cattle mix
SIGMA = 1.0   # default substitution elasticity between grains on energy-adjusted price
# Steers and heifers as a share of cattle slaughter: AAFC federally inspected slaughter, January 1
# to June 7, 2025 (West 984,649 steers and heifers, 163,245 cows and bulls; East 254,965 and 53,634)
FED_SHARE = {"west": 0.858, "east": 0.826}
ENERGY = {"barley": 1.00, "wheat": 1.08, "durum": 1.06, "oats": 0.85, "corn": 1.12}


def crop_year_label(y0: int) -> str:
    return f"{y0}-{y0 + 1}"


# ------------------------------------------------------------------ coefficients

def coefficients() -> dict:
    """{province: {subclass: {grain: t per head}}} for the study's four grains (1999 study).
    Suppressed provincial values (layers, turkeys) fall back to the Canada rate."""
    c = pd.read_csv(COEFFS)
    out: dict = {}
    canada = {r.subclass: r for r in c[c.province == "Canada"].itertuples()}
    for r in c.itertuples():
        src = r if not pd.isna(r.complete) else canada[r.subclass]
        out.setdefault(r.province, {})[r.subclass] = {g: float(getattr(src, g)) for g in STUDY_GRAINS} | {"complete": float(src.complete)}
    return out


# ------------------------------------------------------------------ animal numbers

def _semiannual(df: pd.DataFrame, value_filter, geo_col="GEO") -> dict:
    """{province: {(year, 'jan'|'jul'): value}} from a table with a Survey date column."""
    d = df[value_filter(df)]
    out: dict = {}
    for geo, y, s, v in zip(d[geo_col], d["REF_DATE"], d["Survey date"], d["VALUE"]):
        if pd.isna(v):
            continue
        out.setdefault(geo, {})[(int(y), "jan" if s.startswith("At January") else "jul")] = float(v)
    return out


def crop_year_avg(series: dict, y0: int) -> float | None:
    """Average of July 1 (first year) and January 1 (second year); July alone if January isn't out."""
    v = [series.get((y0, "jul")), series.get((y0 + 1, "jan"))]
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def populations(years: list[int]) -> dict:
    """Animals by crop year (keyed by its first calendar year) and province.

    Inventories ('000 head): crop-year average of July 1 and January 1. Flows: pig crop and pigs
    finished in the province ('000 head), chicken and turkey meat (t), average layers ('000);
    the latest calendar year available up to the crop year's first year. Also returns, per crop
    year, the dairy milk factor and Canada's veal calf slaughter."""
    cat = external.statcan("32100130")
    hog_inv = external.statcan("32100160")
    hog_sd = external.statcan("32100200")
    poultry = external.statcan("32100117")
    eggs = external.statcan("32100121")
    milk = external.statcan("32100113")
    sheep = external.statcan("32100129")
    calves = external.statcan("32100125")
    cattle_sd = external.statcan("32100139")

    def cattle(livestock, farm_type):
        return _semiannual(cat, lambda d: (d["Livestock"] == livestock) & (d["Farm type"] == farm_type))

    inventories = {
        "Beef Cows": cattle("Beef cows", "On all cattle operations"),
        "Dairy Cows": cattle("Dairy cows", "On all cattle operations"),
        "Bulls": cattle("Bulls, 1 year and over", "On all cattle operations"),
        "Beef Rep Heifers": cattle("Heifers for beef replacement", "On all cattle operations"),
        "Dairy Heifers": cattle("Heifers for dairy replacement", "On all cattle operations"),
        "Rams & Ewes": _semiannual(sheep, lambda d: d["Livestock"] == "Sheep, 1 year or older"),
        "Sows & Bred Gilts": _semiannual(hog_inv, lambda d: d["Livestock"] == "Sows and gilts, 6 months and over"),
        "Boars": _semiannual(hog_inv, lambda d: d["Livestock"] == "Boars, 6 months and over"),
    }
    for kind, ft in (("feedlot", "On feeding operations"), ("background", "On feeder and stocker operations")):
        inventories[kind] = {}
        for lv in ("Steers, 1 year and over", "Heifers for slaughter", "Calves, under 1 year"):
            for geo, s in cattle(lv, ft).items():
                for k, v in s.items():
                    inventories[kind].setdefault(geo, {})[k] = inventories[kind].get(geo, {}).get(k, 0.0) + v

    # annual flows, {(geo, item, year): value}
    flows: dict = {}
    sd = hog_sd.groupby(["GEO", "Supply and disposition of hogs", "REF_DATE"])["VALUE"].sum(min_count=2)
    flows.update({k: float(v) for k, v in sd.items() if not pd.isna(v)})
    pw = poultry[(poultry["Production and disposition"] == "Production, total") & (poultry["Estimates"] == "Weight (kilograms)")]
    flows.update({(g, c, int(y)): float(v) for g, c, y, v in zip(pw["GEO"], pw["Commodity"], pw["REF_DATE"], pw["VALUE"]) if not pd.isna(v)})
    lay = eggs[eggs["Production and disposition"] == "Average number of layers"].assign(y=lambda d: d["REF_DATE"].str[:4].astype(int))
    flows.update({(g, "layers", y): float(v) for (g, y), v in lay.groupby(["GEO", "y"])["VALUE"].mean().items()})
    ms = milk[milk["Dairy distribution"] == "Milk sold off farms, total"].assign(y=lambda d: d["REF_DATE"].str[:4].astype(int))
    full = ms.groupby(["GEO", "y"])["VALUE"].agg(["sum", "count"])
    flows.update({(g, "milk", y): float(r["sum"]) for (g, y), r in full.iterrows() if r["count"] == 12})
    cs = calves[(calves["Livestock"] == "Calves") & (calves["Livestock estimates"] == "Total slaughter") & (calves["GEO"] == "Canada")]
    flows.update({("Canada", "calf slaughter", int(y)): float(v) for y, v in zip(cs["REF_DATE"], cs["VALUE"]) if not pd.isna(v)})

    # cattle slaughter and live international exports by province, half-years
    half = cattle_sd[cattle_sd["Supply and disposition of cattle"].isin(["Slaughter of cattle", "International exports of cattle"])]
    half = {(g, i, int(y), s.startswith("July")): float(v) for g, i, y, s, v in
            zip(half["GEO"], half["Supply and disposition of cattle"], half["REF_DATE"], half["Survey date"], half["VALUE"]) if not pd.isna(v)}

    def crop_year_flow(geo, item, y0):
        """July to December of y0 plus January to June of y0 + 1; None until both are out."""
        a, b = half.get((geo, item, y0, True)), half.get((geo, item, y0 + 1, False))
        return a + b if a is not None and b is not None else None

    def latest(geo, item, y):
        """(value, year) for calendar year y, or the latest of the two years before it."""
        for yy in (y, y - 1, y - 2):
            if (geo, item, yy) in flows:
                return flows[(geo, item, yy)], yy
        return None, None

    def milk_per_cow(y):
        kl, yy = latest("Canada", "milk", y)
        cows = crop_year_avg(inventories["Dairy Cows"]["Canada"], yy - 1) if kl else None
        return kl / cows if cows else None

    milk_1999 = milk_per_cow(1999)
    out: dict = {}
    for y0 in years:
        rec: dict = {}
        for p in PROVINCES:
            a = {k: crop_year_avg(s.get(p, {}), y0) for k, s in inventories.items()}
            # backgrounding is a winter program: cattle on feeder and stocker operations at January 1
            bg = inventories["background"].get(p, {})
            a["background"] = bg.get((y0 + 1, "jan"), bg.get((y0, "jan")))
            pc, hy = latest(p, "Pig crop", y0)
            a["Weaner Pigs"] = pc
            if pc is not None:
                g = lambda item: latest(p, item, hy)[0] or 0.0
                a["Feeder Pigs"] = pc + g("Interprovincial imports of hogs") - g("Interprovincial exports of hogs") \
                    - g("International exports of hogs") - g("Deaths and condemnations of hogs")
            a["Chickens t"], py = latest(p, "Chicken (including stewing hen)", y0)
            a["Turkeys t"], _ = latest(p, "Turkey", y0)
            a["Layers"], _ = latest(p, "layers", y0)
            sl, ex = crop_year_flow(p, "Slaughter of cattle", y0), crop_year_flow(p, "International exports of cattle", y0)
            a["cattle_slaughter"], a["cattle_exports"] = sl, ex
            a["fed_marketed"] = (sl + ex) * FED_SHARE["west" if p in WEST else "east"] if sl is not None and ex is not None else None
            a["feedlot_jul"] = inventories["feedlot"].get(p, {}).get((y0, "jul"))
            rec[p] = a
        mpc = milk_per_cow(y0)
        calf, _ = latest("Canada", "calf slaughter", y0)
        cattle_dates = sorted(k for k in inventories["Beef Cows"]["Canada"] if k[0] in (y0, y0 + 1) and k != (y0, "jan"))
        rec["_meta"] = {"milk_factor": mpc / milk_1999 if mpc else None, "calf_slaughter": calf,
                        "inventory_dates": [f"{y}-{'01' if s == 'jan' else '07'}" for y, s in cattle_dates],
                        "hog_year": latest("Canada", "Pig crop", y0)[1], "poultry_year": latest("Canada", "Chicken (including stewing hen)", y0)[1],
                        "milk_year": latest("Canada", "milk", y0)[1]}
        out[y0] = rec
    return out


def poultry_kg_per_bird_1999() -> dict:
    """1999 kg of meat per bird by province (StatCan 32-10-0117), to turn the 1999 per-bird
    feed rates into feed per kg of meat."""
    p = external.statcan("32100117")
    p = p[(p["Production and disposition"] == "Production, total") & (p["REF_DATE"] == 1999)]
    out: dict = {}
    for com, cls in (("Chicken (including stewing hen)", "Chickens"), ("Turkey", "Turkeys")):
        g = p[p["Commodity"] == com].pivot_table(index="GEO", columns="Estimates", values="VALUE")
        for geo, r in g.iterrows():
            if r.get("Birds") and not pd.isna(r.get("Weight (kilograms)")):
                out.setdefault(geo, {})[cls] = r["Weight (kilograms)"] / r["Birds"]  # both in thousands
    return out


# ------------------------------------------------------------------ demand

# population key -> study subclasses it stands for (each at the population's head count)
CLASS_MAP = {
    "Beef Cows": ["Beef Cows"], "Bulls": ["Bulls on Beef farms"],
    "Beef Rep Heifers": ["Beef Rep Heifers > 1 year", "Beef Rep Heifers < 1 year"],
    "Dairy Cows": ["Dairy Cows"], "Dairy Heifers": ["Dairy Heifers > 1 year", "Other Dairy calves < 1 year"],
    "Rams & Ewes": ["Rams & Ewes"], "Sows & Bred Gilts": ["Sows & Bred Gilts"], "Boars": ["Boars"],
    "Weaner Pigs": ["Weaner Pigs"], "Feeder Pigs": ["Feeder Pigs"], "Layers": ["Layers"],
}
# classes reported on the page
GROUPS = {"Beef Cows": "beef_herd", "Bulls": "beef_herd", "Beef Rep Heifers": "beef_herd", "veal": "beef_herd",
          "feedlot": "feedlot", "background": "feedlot",
          "Dairy Cows": "dairy", "Dairy Heifers": "dairy",
          "Sows & Bred Gilts": "hogs", "Boars": "hogs", "Weaner Pigs": "hogs", "Feeder Pigs": "hogs",
          "Chickens": "poultry", "Turkeys": "poultry", "Layers": "poultry", "Rams & Ewes": "sheep"}


# animal numbers shown on the page ('000 head; poultry meat in t; layers '000)
DRIVERS = ["cattle_slaughter", "cattle_exports", "fed_marketed", "feedlot", "background", "Beef Cows", "Dairy Cows",
           "Sows & Bred Gilts", "Weaner Pigs", "Feeder Pigs", "Chickens t", "Turkeys t", "Layers"]


def split_durum(mix: dict, durum_share: float) -> dict:
    """Split an energy-share mix's wheat into wheat and durum; durum_share is durum's share of
    wheat and durum in tonnes."""
    w = mix.get("wheat", 0.0)
    d_e, w_e = durum_share * ENERGY["durum"], (1 - durum_share) * ENERGY["wheat"]
    return mix | {"wheat": w * w_e / (d_e + w_e), "durum": w * d_e / (d_e + w_e)}


def class_demand(pop: dict, coef: dict, kgpb: dict, region_of, durum_share: float = 0.0, days_on_feed: dict | None = None) -> list[dict]:
    """Barley-equivalent grain demand (kt) and base mix for every province and class in one crop year.
    In the West, wheat is split into wheat and durum (durum_share: durum's tonnage share).
    days_on_feed {region: days}: cattle on feed are fed cattle marketed x days on feed; without
    it (or without marketings), the average cattle on feeding operations x 365."""
    rows = []
    meta = pop["_meta"]
    calf_total = sum(VEAL_SHARE_1999.values())

    def add(p, cls, kt_by_grain):
        beq = sum(kt_by_grain.get(g, 0.0) * ENERGY[g] for g in STUDY_GRAINS)
        if beq > 0:
            rows.append({"province": p, "region": region_of(p), "class": cls, "group": GROUPS[cls], "beq": beq,
                         "base_mix": {g: kt_by_grain.get(g, 0.0) * ENERGY[g] / beq for g in FEED_GRAINS}})

    for p in PROVINCES:
        a, c = pop[p], coef[p]
        west = p in WEST
        for key, subs in CLASS_MAP.items():
            n = a.get(key)
            if not n:
                continue
            factor = HOG_FACTORS.get(key, 1.0) * (meta["milk_factor"] if key == "Dairy Cows" else 1.0)
            add(p, key, {g: n * sum(c[s][g] for s in subs) * factor for g in STUDY_GRAINS})
        # bull calves are a third of bulls (study convention)
        if a.get("Bulls"):
            add(p, "Bulls", {g: a["Bulls"] / 3 * c["Bull Calves < 1 year"][g] for g in STUDY_GRAINS})
        if meta["calf_slaughter"]:
            add(p, "veal", {g: meta["calf_slaughter"] * VEAL_SHARE_1999[p] / calf_total * c["Slaughter Calves"][g] for g in STUDY_GRAINS})
        for cls in ("Chickens", "Turkeys"):
            meat = a.get(cls + " t")
            per_bird = kgpb.get(p, {}).get(cls) or kgpb["Canada"][cls]
            if meat:
                add(p, cls, {g: c[cls][g] / per_bird * meat for g in STUDY_GRAINS})
        # cattle on feed: head x days x barley-equivalent grain per day
        fed = a.get("fed_marketed")
        feedlot = (fed, days_on_feed[region_of(p)]) if fed and days_on_feed else (a.get("feedlot"), 365)
        for cls, n, days, lb in (("feedlot", *feedlot, FEEDLOT_BARLEY_LB_DAY),
                                 ("background", a.get("background"), BACKGROUND_DAYS, BACKGROUND_BARLEY_LB_DAY)):
            if not n:
                continue
            beq = n * days * lb * LB  # '000 head x t = kt
            if west:
                mix = WEST_CATTLE_MIX
            else:
                f = c["Steers & Heifers Slaughter"]
                tot = sum(f[g] * ENERGY[g] for g in STUDY_GRAINS)
                mix = {g: f[g] * ENERGY[g] / tot for g in STUDY_GRAINS}
            rows.append({"province": p, "region": region_of(p), "class": cls, "group": "feedlot", "beq": beq,
                         "base_mix": {g: mix.get(g, 0.0) for g in FEED_GRAINS}})
    for row in rows:
        if row["region"] == "west":
            row["base_mix"] = split_durum(row["base_mix"], durum_share)
    return rows


def price_shift(base: dict, rel: dict, avail: dict | None = None, sigma: float = SIGMA, alpha: float = 0.0) -> dict:
    """Shift an energy-share mix by relative price and availability:
    share_i ~ base_i x rel_i ^ -sigma x avail_i ^ alpha."""
    avail = avail or {}
    w = {g: base[g] * rel[g] ** -sigma * avail.get(g, 1.0) ** alpha for g in FEED_GRAINS if base[g] > 0}
    tot = sum(w.values())
    return {g: w.get(g, 0.0) / tot for g in FEED_GRAINS} if tot else base


def days_on_feed(pops: dict) -> dict:
    """{region: days}: average cattle on feeding operations x 365 / fed cattle marketed, over the
    crop years with both."""
    out = {}
    for r, provs in (("west", WEST), ("east", EAST)):
        d = [sum(pop[p].get("feedlot") or 0 for p in provs) * 365 / sum(pop[p]["fed_marketed"] for p in provs)
             for pop in pops.values() if all(pop[p].get("fed_marketed") for p in provs)]
        out[r] = float(np.mean(d)) if d else 365.0
    return out


def project_marketings(pops: dict) -> None:
    """Crop years without published marketings: last year's fed cattle marketed x this July 1's
    cattle on feed over last July 1's, by province (in place). Marks the source in _meta."""
    for y0 in sorted(pops):
        prev = pops.get(y0 - 1)
        pops[y0]["_meta"]["cattle_source"] = "marketings"
        for p in PROVINCES:
            a = pops[y0][p]
            if a.get("fed_marketed") is None and prev and prev[p].get("fed_marketed") and a.get("feedlot_jul") and prev[p].get("feedlot_jul"):
                a["fed_marketed"] = prev[p]["fed_marketed"] * a["feedlot_jul"] / prev[p]["feedlot_jul"]
                pops[y0]["_meta"]["cattle_source"] = "projected"


def demand(years: list[int], prices: dict, corn_west: dict | None = None, durum_share: float = 0.0,
           availability: dict | None = None, sigma: float = SIGMA, alpha: float = 0.0) -> dict:
    """Feed grain demand by crop year: {crop_year: {"by_region": {region: {grain: kt}},
    "by_group": {region: {group: kt barley-eq}}, ...}}.

    prices: {region: {grain: {crop_year: C$/t}}}; relative prices are each grain's
    energy-adjusted price over its average across all years (a grain without prices in a
    region, durum in the East, stays at 1).
    durum_share: durum's share (tonnes) of the wheat and durum fed in the West before prices
    shift it, e.g. its usual share of StatCan's western feed use.
    availability: {region: {grain: {crop_year: supply / its average}}}; missing = 1.
    corn_west: {crop_year: kt} corn used in the West (StatCan's corn balance for the provinces
    other than Ontario and Quebec: production, imports and stock changes, all measured). The
    1999 rations predate Manitoba's corn crop (about 0.5 Mt then, over 2 Mt now), so where it is
    published the West's corn is set to it, and the rest of the West's demand is split among
    barley, wheat and oats. Other years use the corn share of the last three published years,
    shifted by price."""
    coef = coefficients()
    kgpb = poultry_kg_per_bird_1999()
    pops = populations(sorted(set(years) | {min(years) - 1}))
    project_marketings(pops)
    dof = days_on_feed(pops)
    corn_west = corn_west or {}
    availability = availability or {}
    region_of = lambda p: "west" if p in WEST else "east"
    avg = {r: {g: np.mean([v / ENERGY[g] for v in prices[r][g].values()]) for g in FEED_GRAINS if prices[r].get(g)} for r in prices}
    out: dict = {}
    for y0 in years:
        cy = crop_year_label(y0)
        rows = class_demand(pops[y0], coef, kgpb, region_of, durum_share, dof)
        rec = {"by_region": {r: {g: 0.0 for g in FEED_GRAINS} for r in ("west", "east")},
               "by_group": {r: {} for r in ("west", "east")},
               "beq": {r: 0.0 for r in ("west", "east")}, "relative_price": {}, "availability": {},
               "base_mix": {r: {g: 0.0 for g in FEED_GRAINS} for r in ("west", "east")}, "corn_west": None}
        for r in ("west", "east"):
            pr = {g: prices[r].get(g, {}).get(cy) for g in FEED_GRAINS}
            rel = {g: (pr[g] / ENERGY[g]) / avg[r][g] if pr[g] else 1.0 for g in FEED_GRAINS}
            av = {g: availability.get(r, {}).get(g, {}).get(cy, 1.0) for g in FEED_GRAINS}
            rec["relative_price"][r] = {g: round(v, 3) for g, v in rel.items()}
            rec["availability"][r] = {g: round(v, 3) for g, v in av.items()}
            for row in rows:
                if row["region"] != r:
                    continue
                for g in FEED_GRAINS:
                    rec["base_mix"][r][g] += row["beq"] * row["base_mix"][g]
                mix = price_shift(row["base_mix"], rel, av, sigma, alpha)
                for g in FEED_GRAINS:
                    rec["by_region"][r][g] += row["beq"] * mix[g] / ENERGY[g]
                rec["by_group"][r][row["group"]] = rec["by_group"][r].get(row["group"], 0.0) + row["beq"]
                rec["beq"][r] += row["beq"]
        rec["base_mix"] = {r: {g: v / rec["beq"][r] for g, v in m.items()} for r, m in rec["base_mix"].items()}
        rec["meta"] = {k: v for k, v in pops[y0]["_meta"].items()} | {"days_on_feed": {r: round(v, 1) for r, v in dof.items()}}
        def region_sum(r, k):
            v = [pops[y0][p].get(k) for p in PROVINCES if region_of(p) == r]
            return None if all(x is None for x in v) else round(sum(x or 0.0 for x in v), 1)
        rec["drivers"] = {r: {k: region_sum(r, k) for k in DRIVERS} for r in ("west", "east")}
        out[cy] = rec

    # western corn: observed use where published, else the recent observed share shifted by price
    for cy, rec in out.items():
        w, beq = rec["by_region"]["west"], rec["beq"]["west"]
        if cy in corn_west:
            corn_t, source = min(corn_west[cy], beq / ENERGY["corn"]), "observed"
        else:
            prior = [c for c in sorted(corn_west) if c < cy][-3:]
            if not prior:
                continue
            share = np.mean([corn_west[c] * ENERGY["corn"] / out[c]["beq"]["west"] for c in prior if c in out])
            rel = rec["relative_price"]["west"]
            others = sum(w[g] * ENERGY[g] * rel[g] ** -sigma for g in FEED_GRAINS if g != "corn") / max(1e-9, sum(w[g] * ENERGY[g] for g in FEED_GRAINS if g != "corn"))
            s_c = share * rel["corn"] ** -sigma / (share * rel["corn"] ** -sigma + (1 - share) * others)
            corn_t, source = beq * s_c / ENERGY["corn"], "projected from " + ", ".join(prior)
        rest = beq - corn_t * ENERGY["corn"]
        small = {g: w[g] * ENERGY[g] for g in FEED_GRAINS if g != "corn"}
        tot = sum(small.values())
        for g in small:
            w[g] = rest * small[g] / tot / ENERGY[g]
        w["corn"] = corn_t
        rec["corn_west"] = source
    return out
