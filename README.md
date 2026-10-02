# CGC Grain Dashboards

Market analysis of Western Canadian grains, oilseeds and pulses for commodity analysts and traders. The dashboards
are built from the Canadian Grain Commission's weekly
[Grain Statistics Weekly](https://www.grainscanada.gc.ca/en/grain-research/statistics/grain-statistics-weekly/),
with Statistics Canada crop, livestock and price data and CBOT futures. Crop years run from 2013-14 to the present.

**Live site:** https://gabrielfolk.github.io/cgc-grain-dashboards/

## The dashboards

| Page | Path | What it answers |
|---|---|---|
| **Overview** | `/` | What are farmers doing across all 15 crops? Selling pace sized to the crop, cross-crop Things to watch (elevators, exports, stocks, ports, Thunder Bay), the all-crops weekly flash, pace cards, a 13-year heatmap, crop mix, and all-crops deliveries by province and commercial stocks. |
| **Crop pages** (15) | `/wheat/`, `/canola/`, `/durum/`, `/barley/`, `/peas/`, `/oats/`, `/lentils/`, `/soybeans/`, `/corn/`, `/flaxseed/`, `/rye/`, `/beans/`, `/canaryseed/`, `/chickpeas/`, `/mustard/` | One crop from farm to use: generated "Things to watch" cards, weekly flash, pace math, farmer selling (pace sized to the crop, rank, projection, direct-to-processor and province shares), deliveries by province this week, country elevator throughput (net build, weeks of cover), port terminals (Pacific receipts vs exports, weeks of cover, Thunder Bay before freeze-up, export grade mix), exports by port, processing, feed and stocks. Canola adds a backtested crush and export forecast. |
| **Feed Grains** | `/feed/` | How much barley, wheat, durum, oats and corn is fed in Canada, split into Western Canada and Ontario + Quebec (estimated: StatCan publishes feed use for Canada only). Covers quality, livestock numbers by region, energy-adjusted feed prices (Alberta and Ontario) and a backtested feed-use estimate. |
| **Producer Margins** | `/margins/` | What does an acre of each crop earn after variable costs, at seeding (April) and harvest (August) prices, and are farmers selling faster where margins are good? Saskatchewan budgets, 9 crops, 2017 on. Delivery detail is on the crop pages. |

Every page has summary tiles or cards, a weekly flash table (with a copy-to-spreadsheet button) and charts.
Methodology notes are at the foot of each page.

## How it fits together

```
CGC weekly CSVs ──> src/ingest.py ──> data/processed/gsw.parquet
                                           │
                                     src/db.py (DuckDB views: gsw, producer_deliveries, commercial_stocks)
                                           │
StatCan tables, CBOT/FX (Yahoo),     ┌─────┴──────────────────────────────────────────┐
CGC quality pages ──> src/external.py│                                                │
                                     ├─ src/dashboard_data.py  ─> dashboard/data.json  │ overview
                                     ├─ src/grain_data.py      ─> dashboard/<crop>/    │ deep dives (template)
                                     ├─ src/canola_forecast.py ─> dashboard/canola/forecast.json
                                     └─ src/feed_data.py       ─> dashboard/feed/data.json
                                                   │
                                       src/build_site.py ─> docs/ ─> GitHub Pages

SK Crop Planning Guide PDFs ──> src/crop_guide.py ──> reference/sk_crop_planning_guide.csv
                                                             │
                              src/margins.py (+ StatCan prices, yields) ──> src/margins_data.py ──> dashboard/margins/data.json
```

- **Pages are static HTML** that load their own `data.json`, with no server or build framework.
- **`src/build_site.py`** wraps each page in a full HTML document, adds the navigation bar and the favicon,
  and writes `docs/`.
- **GitHub Pages** serves `docs/` from `main`, and the site updates about a minute after a push.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python src/ingest.py        # downloads ~230 MB of CGC CSVs on first run
```

Raw and processed data (`data/`) and the virtual environment are not in git; `src/ingest.py` and
`src/external.py` re-create them.

## Weekly refresh

CGC publishes on Thursdays.

```sh
.venv/bin/python src/ingest.py            # re-downloads the current crop year only
.venv/bin/python src/dashboard_data.py
.venv/bin/python src/grain_data.py
.venv/bin/python src/canola_forecast.py
.venv/bin/python src/feed_data.py
.venv/bin/python src/margins_data.py
.venv/bin/python src/build_site.py
git add -A && git commit -m "Weekly refresh: week N" && git push
```

**Each January,** when Saskatchewan Agriculture publishes the new Crop Planning Guide: add its format id to `FORMAT_IDS`
in `src/crop_guide.py` (from the Publications Centre archive, product 122661), then run `.venv/bin/python src/crop_guide.py`.
The parser checks every budget against the guide's own arithmetic and stops if a column fails.

**When StatCan publishes:**
- **Production estimates** (late August, mid-September, early December): add a row for each new in-season canola
  estimate to `reference/statcan_canola_vintages.csv`.
- **Any new release** (production, stocks, supply and disposition, livestock or prices): run
  `.venv/bin/python src/external.py --refresh` to re-download the cached tables.

To preview locally, run `python3 -m http.server` inside `dashboard/`. Links between pages are relative, so they work
there too; only the built site in `docs/` has the navigation bar.

## Project layout

```
src/
  ingest.py          download and clean CGC Grain Statistics Weekly
  db.py              DuckDB views over the cleaned data, plus the Canada Eastern grade filter
  external.py        StatCan tables, Yahoo futures, CGC harvest-quality pages (cached in data/raw/external/)
  crop_guide.py      parse the Saskatchewan Crop Planning Guide PDFs into per-acre budgets
  margins.py         producer margins by crop (price x trend yield - guide costs)
  margins_data.py    data for the Producer Margins page (margins plus deliveries of the same crops)
  dashboard_data.py  data for the overview page
  grain_data.py      data for the crop pages, plus crop settings (GRAINS), province/channel splits and StatCan production
  canola_forecast.py canola crush and export forecast, with backtest
  feed_data.py       data and feed-use estimate for the feed page, with backtest
  build_site.py      builds docs/ (HTML wrapper, navigation bar, favicons)
dashboard/
  index.html         overview page (edited directly)
  pipeline.html      crop deep-dive template: edit this, not dashboard/<crop>/index.html
  <crop>/            generated page copy plus data.json per crop
  feed/index.html    feed page (edited directly)
  margins/index.html Producer Margins page (edited directly)
reports/             forecast and estimate write-ups (canola_forecast.md, feed_estimate.md)
reference/           hand-curated inputs (StatCan in-season canola estimates, with sources) and the parsed
                     Crop Planning Guide budgets (sk_crop_planning_guide.csv)
docs/                built site, served by GitHub Pages (generated; don't edit)
TODO.md              next steps
```

**Common changes:**
- **Add a crop page:** add an entry to `GRAINS` (and its StatCan name to `STATCAN_CROPS`) in `src/grain_data.py`,
  then add its slug to `NAV` (top level or in the Special Crops menu; optionally `CROP_COLORS`) in `src/build_site.py`. The overview links to it
  automatically.
- **Add a new analysis page:** create `dashboard/<name>/index.html` and its data script, and add it to `NAV` in
  `src/build_site.py`.

## Definitions

- **Producer margins:** Saskatchewan monthly farm price times trend yield (average of the five previous
  Saskatchewan harvests), less the Crop Planning Guide's variable or total costs per acre (Dark Brown zone,
  stubble seeded). From 2017, when the guide moved to a higher-input system.
- **Crop year:** Aug 1 to Jul 31. "CYTD" is crop year to date. "5-yr" is the average of the previous five crop years
  at the same week.
- **Weekly values** are week-over-week changes in CGC's crop-year totals. CGC applies revisions only to the totals,
  so its own weekly rows add up 1–4% short. Weeks 21–22 are one combined holiday report.
- **Producer deliveries:** deliveries to licensed primary elevators, plus direct deliveries to processors, plus
  producer cars.
- **Exports:** licensed port terminal exports, plus shipments from country elevators straight to export or
  container loaders. Before 2018-19, Vancouver and Prince Rupert are reported together as Pacific.
- **Commercial stocks:** week-end stocks in country elevators, processors and port terminals. Grain on farms is not
  included.
- **Western grain only:** wheat, corn (and, in 2013-15, barley) graded Canada Eastern (CE grades) is left out of
  exports, terminal receipts and terminal stocks. The wheat page shows eastern exports as a memo line. Add them back
  to match CGC's all-Canada totals.
- **Domestic feed grains:** CGC's domestic feed grain table is a *subset* of elevator handlings, not extra supply.
  CGC notes that elevators handle only about 10–15% of feed grain use.
- **Sized to crop:** the share of StatCan's western production estimate delivered so far, compared with the usual
  share by the same week.

## Forecasts and estimates

Both are backtested year by year on earlier crop years only, against simple baselines, and the results are published
even where a baseline does better.

- **Canola crush and exports** (`src/canola_forecast.py`): forecasts weekly flows 1–4 weeks ahead and full
  crop-year totals, using StatCan's in-season production estimates as they were published. See
  `reports/canola_forecast.md`.
- **Feed use by grain** (`src/feed_data.py`): a 50/50 blend of a price-and-availability share model and each grain's
  five-year average, for Canada; eastern corn is its five-year average. Regions split each grain by its five-year
  regional shares. See `reports/feed_estimate.md`.

## CGC data quirks handled

- Column names and order differ between crop years, and 2013-14 to 2017-18 live under a `/csv/` URL.
- Missing files return HTTP 200 with an HTML "page not found" page; these are detected and skipped.
- Label typos and variants are normalized (`Sasktachewan`, `B.C.`, `Peas*`, ...), and footnote rows are dropped.
- 2024-25 week 48 is published month-first; day/month-swapped dates are repaired.
- Some tables lag the rest of the report (e.g. feed grain deliveries); pages use each series' latest reported week and
  label it.

## Sources and licence

- **Canadian Grain Commission:** [Grain Statistics Weekly](https://www.grainscanada.gc.ca/en/grain-research/statistics/grain-statistics-weekly/)
  and harvest quality reports.
- **Statistics Canada tables:**

  | Topic | Tables |
  |---|---|
  | Field crops | 32-10-0359 |
  | Farm product prices | 32-10-0077 |
  | Supply and disposition | 32-10-0013, 32-10-0014, 32-10-0015 |
  | Cattle and hogs | 32-10-0125, 32-10-0126, 32-10-0130, 32-10-0160 |
  | Poultry | 32-10-0117 |

  In-season production estimates come from [The Daily](https://www150.statcan.gc.ca/n1/dai-quo/index-eng.htm).

CGC and Statistics Canada data are used under the
[Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada). CBOT futures and
USD/CAD are weekly closes from Yahoo Finance.
