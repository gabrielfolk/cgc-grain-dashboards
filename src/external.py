"""External data for the canola forecast: StatCan production and prices, CBOT soy complex, USD/CAD.

Everything here is looked up "as of" a date, so a backtest only sees what had been
published by then.

    production_known(harvest_year, as_of)  canola production expected at that date (t)
    prices_as_of(as_of)                    price features at that date

Sources (downloaded and cached under data/raw/):
    StatCan 32-10-0359  final area, yield and production by crop year
    StatCan 32-10-0077  monthly farm price, canola, Saskatchewan (CAD/t)
    reference/statcan_canola_vintages.csv  StatCan's in-season production estimates as released
    Yahoo Finance weekly closes: ZL=F soybean oil (US cents/lb), ZM=F soybean meal (USD/short ton),
                                 ZS=F soybeans (US cents/bu), CAD=X (CAD per USD)
There is no free history for ICE canola futures, so canola's own price comes from StatCan's
farm price. That series is published about two months after the month ends, so it is lagged
to its approximate release date.

    python src/external.py --refresh   # re-download everything
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import zipfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "external"
VINTAGES = ROOT / "reference" / "statcan_canola_vintages.csv"
UA = {"User-Agent": "Mozilla/5.0"}

STATCAN_TABLES = {"production": "32100359", "prices": "32100077"}
YAHOO = {"soyoil": "ZL=F", "soymeal": "ZM=F", "soybeans": "ZS=F", "usdcad": "CAD=X"}
DECEMBER_RELEASE = (12, 4)      # StatCan's November survey, published in early December
FARM_PRICE_LAG_DAYS = 52        # month M's farm price is usable ~52 days after month end
LB_PER_T = 2204.62
BU_SOY_PER_T = 36.744
SHORT_TON_PER_T = 1.10231


def _statcan_csv(table: str, refresh: bool) -> pd.DataFrame:
    path = RAW / f"{table}.csv"
    if refresh or not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        meta = requests.get(f"https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/{table}/en", timeout=60).json()
        z = zipfile.ZipFile(io.BytesIO(requests.get(meta["object"], timeout=300).content))
        path.write_bytes(z.read(f"{table}.csv"))
    return pd.read_csv(path, low_memory=False)


def _yahoo_weekly(symbol: str, refresh: bool) -> pd.Series:
    path = RAW / f"yahoo_{symbol.replace('=', '_')}.json"
    if refresh or not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=1356998400&period2={int(dt.datetime.now().timestamp())}&interval=1wk"
        path.write_text(requests.get(url, headers=UA, timeout=60).text)
    res = json.loads(path.read_text())["chart"]["result"][0]
    s = pd.Series(res["indicators"]["quote"][0]["close"], index=pd.to_datetime(res["timestamp"], unit="s").normalize())
    return s.dropna()


@lru_cache(maxsize=None)
def load(refresh: bool = False) -> dict:
    prod = _statcan_csv(STATCAN_TABLES["production"], refresh)
    canola = prod[(prod["GEO"] == "Canada") & prod["Type of crop"].str.startswith("Canola")]
    table = canola.pivot_table(index="REF_DATE", columns="Harvest disposition", values="VALUE", aggfunc="first")
    crop = pd.DataFrame({
        "production_t": table["Production (metric tonnes)"],
        "seeded_ha": table["Seeded area (hectares)"],
        "yield_kg_ha": table["Average yield (kilograms per hectare)"],
    })

    prices = _statcan_csv(STATCAN_TABLES["prices"], refresh)
    sk = prices[(prices["GEO"] == "Saskatchewan") & prices["Farm products"].str.startswith("Canola")]
    month_end = pd.to_datetime(sk["REF_DATE"]) + pd.offsets.MonthEnd(0)
    farm = pd.Series(sk["VALUE"].to_numpy(), index=month_end + pd.Timedelta(days=FARM_PRICE_LAG_DAYS)).sort_index()

    vint = pd.read_csv(VINTAGES, parse_dates=["released"])
    markets = pd.DataFrame({k: _yahoo_weekly(sym, refresh) for k, sym in YAHOO.items()}).sort_index().ffill()
    return {"crop": crop, "farm_price": farm, "vintages": vint, "markets": markets}


WESTERN = ["Manitoba", "Saskatchewan", "Alberta", "British Columbia"]


def western_production() -> pd.DataFrame:
    """Production (t) by StatCan crop name and harvest year, summed over the western provinces
    (the region CGC's weekly data covers). The latest year is StatCan's latest estimate."""
    prod = _statcan_csv(STATCAN_TABLES["production"], refresh=False)
    p = prod[(prod["Harvest disposition"] == "Production (metric tonnes)") & prod["GEO"].isin(WESTERN)]
    return p.pivot_table(index="REF_DATE", columns="Type of crop", values="VALUE", aggfunc="sum")


def production_known(harvest_year: int, as_of: pd.Timestamp) -> tuple[float, str]:
    """Canola production (t) StatCan had published for a harvest by `as_of`.

    From early December: the final table value (the November survey is within a few
    percent of the final revision, so this is a small look-ahead). Before that: the latest
    in-season estimate released by then. Before any estimate: seeded area x the average
    yield of the previous five harvests.
    """
    d = load()
    if as_of >= pd.Timestamp(harvest_year, *DECEMBER_RELEASE) and harvest_year in d["crop"].index:
        return float(d["crop"].loc[harvest_year, "production_t"]), "november survey"
    v = d["vintages"]
    v = v[(v["harvest_year"] == harvest_year) & (v["released"] <= as_of)]
    if len(v):
        row = v.sort_values("released").iloc[-1]
        return float(row["production_mt"]) * 1e6, row["release"]
    crop = d["crop"]
    prior_yield = crop.loc[harvest_year - 5:harvest_year - 1, "yield_kg_ha"].mean()
    return float(crop.loc[harvest_year, "seeded_ha"] * prior_yield / 1000), "area x trend yield"


def prices_as_of(as_of: pd.Timestamp) -> dict:
    """Price features using only data published by `as_of`."""
    d = load()
    m = d["markets"].loc[:as_of]
    if len(m) < 60:
        return {}
    last, ago4, ago13 = m.iloc[-1], m.iloc[-5], m.iloc[-14]
    year = m.iloc[-157:]  # ~3 years for "normal" levels
    usd_t = lambda r: pd.Series({
        "oil": r["soyoil"] / 100 * LB_PER_T,
        "meal": r["soymeal"] * SHORT_TON_PER_T,
        "soy": r["soybeans"] / 100 * BU_SOY_PER_T,
    })
    now = usd_t(last)
    # soybean crush oil share: value of the oil in a bushel over oil + meal (11 lb oil, 44 lb meal)
    oil_share = lambda r: (r["soyoil"] / 100 * 11) / (r["soyoil"] / 100 * 11 + r["soymeal"] / 2000 * 44)
    farm = d["farm_price"].loc[:as_of]
    canola = float(farm.iloc[-1]) if len(farm) else np.nan
    # canola crush margin proxy (CAD/t seed): 43% oil at soyoil value, 57% meal at 75% of soymeal value
    product = lambda r: (0.43 * r["soyoil"] / 100 * LB_PER_T + 0.57 * 0.75 * r["soymeal"] * SHORT_TON_PER_T) * r["usdcad"]
    margin = product(last) - canola
    margin_hist = year.apply(product, axis=1) - farm.reindex(year.index, method="ffill").to_numpy()
    return {
        "soyoil_ret13": float(np.log(last["soyoil"] / ago13["soyoil"])),
        "oil_share": float(oil_share(last)),
        "oil_share_chg13": float(oil_share(last) - oil_share(ago13)),
        "fx_ret4": float(np.log(last["usdcad"] / ago4["usdcad"])),
        "canola_vs_soy": float(np.log(canola / (now["soy"] * last["usdcad"]))),
        "margin_vs_3y": float((margin - np.nanmean(margin_hist)) / (np.nanstd(margin_hist) or 1)),
        "canola_price": canola,
        "margin_proxy": float(margin),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    load.cache_clear()
    d = load(refresh=args.refresh)
    print(d["crop"].tail(6).round(0).to_string())
    print("markets:", d["markets"].index.min().date(), "..", d["markets"].index.max().date())
    today = pd.Timestamp.today().normalize()
    print("production known today:", production_known(today.year if today.month >= 8 else today.year - 1, today))
    print("prices today:", {k: round(v, 3) for k, v in prices_as_of(today).items()})
