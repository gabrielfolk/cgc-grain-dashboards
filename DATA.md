# Data reference

What every series on the dashboards is, where it comes from, and what each page's data file holds. For how to run
the pipeline, see [README.md](README.md). For forecast methods, see [reports/](reports/).

## Conventions

- **Crop year:** Aug 1 to Jul 31, written `2025-2026` in data files and `2025-26` on pages. Corn's StatCan balance
  runs Sep–Aug; it is labelled with the Aug–Jul crop year it mostly overlaps.
- **Grain week:** CGC's week 1 to 52 of the crop year. Weeks 21–22 are one combined holiday report.
- **Units:** thousand tonnes (kt) unless stated. Prices are C$/t unless stated.
- **CYTD:** crop year to date. **LY:** last crop year at the same week. **5-yr:** the average of the previous five
  crop years at the same week.
- **Weekly values** are week-over-week changes in CGC's crop-year totals. CGC applies revisions only to the totals,
  so its own weekly rows add up 1–4% short.
- **Regions:**
  - **Western Canada** (CGC's scope and the crop pages) = MB, SK, AB, BC. Before 2017-18 CGC reports Alberta and
    BC together.
  - **On the Feed Grains page:** West = MB, SK, AB, BC; East = Ontario, Quebec and the Atlantic provinces;
    West + East = Canada.

## Canadian Grain Commission: Grain Statistics Weekly

[Grain Statistics Weekly](https://www.grainscanada.gc.ca/en/grain-research/statistics/grain-statistics-weekly/),
one long-format CSV per crop year, re-published every Thursday. `src/ingest.py` loads them into
`data/processed/gsw.parquet` (columns: crop year, grain week, week ending, worksheet, metric, period, grain, grade,
region, kt); `src/db.py` adds DuckDB views on top.

**Scope: Western Canadian grain.** Wheat, corn and (in 2013-15) barley graded Canada Eastern (CE grades, grown in
Ontario and Quebec) are left out of exports, terminal receipts and terminal stocks. The wheat page shows eastern
exports as a memo line; add them back to match CGC's all-Canada totals.

| Series | Definition |
|---|---|
| **Producer deliveries** | Deliveries to licensed primary (country) elevators, plus direct deliveries to processors, plus producer cars. |
| **Deliveries by province** | Elevators and producer cars only; CGC rarely gives a province for direct-to-processor deliveries. |
| **Processing** | Grain processed at licensed facilities (Process worksheet, "Milled/Mfg Grain"): crush, milling, malting. |
| **Exports** | Licensed port terminal exports, plus shipments from country elevators straight to export destinations or container loaders. Before 2018-19, Vancouver and Prince Rupert are reported together as Pacific. |
| **Terminal receipts** | Unloads at port terminals. |
| **Thunder Bay disposition** | Where Thunder Bay terminals send grain: straight to export, to domestic users, or by lake to other terminals. |
| **Country elevator throughput** | Grain into and out of primary elevators, all destinations. With country stocks, shows whether elevators are filling up or being drawn down. |
| **Commercial stocks** | Week-end stocks at country elevators, processors and port terminals. Grain on farms is not included. |
| **Domestic feed grains** | CGC's "Feed Grains" table: feed-grade grain delivered to and shipped from primary elevators for domestic feed. A **subset** of elevator handlings, not extra supply. CGC notes elevators handle only about 10–15% of feed grain use. |
| **US corn receipts** | US corn received at licensed primary and process elevators (Imported Grains worksheet). |
| **Export grade mix** | Terminal exports by grade. |

CGC's definitions are in the explanatory notes of its old PDF weekly reports (for example
`grain-statistics-weekly/2012-13/13gsw_shg52.pdf`), not in the CSVs.

**Quirks handled in `src/ingest.py`:**
- Column names and order differ between crop years, and 2013-14 to 2017-18 live under a `/csv/` URL.
- Missing files return HTTP 200 with an HTML "page not found" page; these are detected and skipped.
- Label typos and variants are normalized (`Sasktachewan`, `B.C.`, `Peas*`, ...), and footnote text that leaks into
  label columns is dropped.
- 2024-25 week 48 is published month-first; day/month-swapped dates are repaired.
- Some tables lag the rest of the report (for example feed grain deliveries); pages use each series' latest reported
  week and label it.

## Statistics Canada

Downloaded through StatCan's web data service and cached in `data/raw/external/<table>.csv` by `src/external.py`.
StatCan data is published with a lag; `src/external.py --refresh` picks up new releases.

| Table | Contents | Used for |
|---|---|---|
| [32-10-0359](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210035901) | Area, yield and production of field crops, by province | Crop pages ("sized to crop", western production); margins (trend yield, area); canola forecast; feed page (production by region) |
| [32-10-0077](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210007701) | Monthly farm product prices, by province | Margins (Saskatchewan); canola forecast (Saskatchewan canola); feed page (Alberta grain and livestock, Ontario grain) |
| [32-10-0013](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210001301) | Supply and disposition of grains, Canada | Feed page: feed use, supply, stocks, imports for barley, wheat, durum, oats |
| [32-10-0014](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210001401) | Supply and disposition of corn: Canada, Ontario, Quebec, other provinces | Feed page: corn feed, imports, exports, industrial use, regional corn split |
| [32-10-0015](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210001501) | Farm supply and disposition of grains, by region | Feed page: on-farm feed by region (basis of the regional split) |
| [32-10-0130](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210013001) | Cattle inventories, by class, farm type and province | Feed page: livestock charts and the demand model (cattle on feeding and feeder operations, cows, heifers, bulls) |
| [32-10-0160](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210016001) | Hog inventories, by class and province | Feed page; demand model (sows, boars) |
| [32-10-0200](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210020001) | Supply and disposition of hogs, by province | Demand model: pig crop and pigs finished in each province |
| [32-10-0113](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210011301) | Milk production and utilization | Demand model: milk sold per dairy cow |
| [32-10-0121](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210012101) | Production and disposition of eggs, monthly | Demand model: layers by province |
| [32-10-0129](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210012901) | Sheep and lambs | Demand model: sheep |
| [32-10-0125](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210012501), [32-10-0126](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210012601) | Cattle and hog slaughter and meat production, Canada | Feed page |
| [32-10-0117](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3210011701) | Poultry production, by province | Feed page (meat production); demand model (chicken and turkey meat) |

Tables 32-10-0007 (stocks) and 32-10-0139 (cattle supply and disposition) are in the cache but not used yet.

**Notes:**
- **Supply and disposition tables are cumulative within the crop year:** December (Aug–Dec), March (Aug–Mar) and
  July (full year; corn: August). The feed page uses the full-year figures; where August corn isn't published yet,
  it scales March by the usual March-to-August ratio and marks the value with `*`.
- **Feed use** ("animal feed, waste and dockage") is a residual of the balance, so it also absorbs measurement error.
- **StatCan publishes feed use for Canada only,** and has suppressed provincial corn feed since 2007-08. The feed
  page's West/East split of it is estimated; the method is in [reports/feed_estimate.md](reports/feed_estimate.md)
  and in the page's notes.
- **The feed page's headline is a demand model,** not StatCan's residual: animal numbers × feeding rates from StatCan's
  [Livestock Feed Requirements Study](https://www150.statcan.gc.ca/n1/pub/23-501-x/23-501-x2003001-eng.pdf)
  (1999 rates, catalogue 23-501-X; saved in `reference/statcan_livestock_feed_1999.csv`), updated with Manitoba
  Agriculture's 2025–2026 cost-of-production guides for feedlot cattle, backgrounding and hogs, and with milk per cow
  for dairy. See `src/feed_model.py` and [reports/feed_estimate.md](reports/feed_estimate.md).
- **In-season production estimates** are not kept as vintages in the tables. The canola forecast needs them as
  released, so they are recorded by hand in `reference/statcan_canola_vintages.csv`, with links to
  [The Daily](https://www150.statcan.gc.ca/n1/dai-quo/index-eng.htm).
- StatCan's Saskatchewan soybean farm price has too many gaps to use, so the margins page leaves soybeans out.

## Market data

Weekly closes from the Yahoo Finance chart API, cached in `data/raw/external/yahoo_<symbol>.json`:

| Symbol | Series | Used for |
|---|---|---|
| `ZC=F` | CBOT corn, US cents/bu | Feed page: US corn delivered to southern Alberta (CBOT + an editable basis and freight) |
| `ZS=F`, `ZL=F`, `ZM=F` | CBOT soybeans, soybean oil (US cents/lb), soybean meal (US$/short ton) | Canola forecast price features |
| `CAD=X` | Canadian dollars per US dollar | Both |

There is no free history for ICE canola futures (Yahoo's `RS=F` is empty), so canola's own price comes from
StatCan's monthly farm price, lagged to its release date.

## Saskatchewan Crop Planning Guide

Saskatchewan Agriculture's per-acre crop budgets, published each January: target yield, expected price, variable and
total costs by crop and soil zone. `src/crop_guide.py` parses the PDFs (2013 on) into
`reference/sk_crop_planning_guide.csv`. The margins page uses the Dark Brown zone, stubble seeded, from 2017, when the
guide moved to a higher-input system; earlier budgets are not comparable. In 2020 it moved to 80th-percentile target
yields.

- **Margin over variable costs:** Saskatchewan farm price × trend yield (average of the five previous Saskatchewan
  harvests), less the guide's variable costs per acre. April (seeding) and August (harvest) prices use the same
  yield, so the change between them is price alone.

## Data files

Each page loads its own `data.json` (and the canola page also `forecast.json`) from `dashboard/<page>/`, copied
into `docs/` by `src/build_site.py`. All are generated; don't edit them. Every file has a `meta` block with the
current crop year, latest CGC week, week-ending date and build date.

**Weekly series format:** `{series: {crop_year: [52 values]}}`, crop-year-to-date for flows and week-end levels for
stocks; `null` for weeks not yet reported.

| File | Built by | Main keys |
|---|---|---|
| `dashboard/data.json` (overview) | `dashboard_data.py` | `grains`, `deliveries`, `stocks`, `province`, `channel`, `province_weekly`, `exports`, `process`, `production`, `groups` |
| `dashboard/<crop>/data.json` | `grain_data.py` | `flows` (deliveries, process, feed, exports and receipts by port, Thunder Bay disposition, elevator throughput, eastern exports memo), `stocks`, `province`, `channel`, `production`, `grades`; `meta` holds the page's title, lede, notes and settings |
| `dashboard/canola/forecast.json` | `canola_forecast.py` | `weekly` and `full_year` forecasts for crush and exports, `supply`, backtest results |
| `dashboard/feed/data.json` | `feed_data.py`, `feed_model.py` | `demand_model` (by crop year: `by_region` grain kt, `by_group` barley-equivalent kt, `drivers`, `relative_price`, `meta`), `demand_model_meta`, `supply_disposition`, `farm_feed`, `regional_feed`, `corn`, `production`, `prices` (Alberta, `ontario`, CBOT, FX), `livestock` (`inventory.west` / `.east`), `weekly` (CGC), `model` (residual forecast fit and backtest), `panel`, `estimate` |
| `dashboard/margins/data.json` | `margins_data.py` | `deliveries`, `production` and `margins` (`rows` by crop and year, `now`) |

Series definitions inside each file are in the building script's module docstring.

## Reference files

Hand-curated inputs, in git:

- `reference/statcan_canola_vintages.csv`: StatCan's in-season canola production estimates as released (harvest
  year, release date, Mt, survey, link).
- `reference/sk_crop_planning_guide.csv`: the parsed Crop Planning Guide budgets.
- `reference/statcan_livestock_feed_1999.csv`: StatCan's per-animal feed rates by province, livestock subclass and
  ingredient (t per head per year; poultry per bird), from table 7 of the Livestock Feed Requirements Study 1999–2001.
  Blank values are suppressed in the source; the model uses the Canada rate for them.

## Licence

CGC and Statistics Canada data are used under the
[Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada). CBOT futures and
USD/CAD are from Yahoo Finance. The Saskatchewan Crop Planning Guide is published by the Government of
Saskatchewan; the Manitoba cost-of-production guides by the Government of Manitoba; the dairy feed cost brief by
the Government of Alberta.
