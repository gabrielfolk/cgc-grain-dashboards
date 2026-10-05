# CGC Grain Dashboards

Market analysis of Canadian grains, oilseeds and pulses for commodity analysts and traders. The dashboards are built
from the Canadian Grain Commission's weekly
[Grain Statistics Weekly](https://www.grainscanada.gc.ca/en/grain-research/statistics/grain-statistics-weekly/)
(Western Canada), with Statistics Canada crop, livestock and price data and CBOT futures. Crop years run from 2013-14
to the present.

**Live site:** https://gabrielfolk.github.io/cgc-grain-dashboards/

- [DATA.md](DATA.md): sources, definitions, data quirks and what each page's data file holds
- [reports/canola_forecast.md](reports/canola_forecast.md), [reports/feed_estimate.md](reports/feed_estimate.md):
  forecast methods and backtests
- [TODO.md](TODO.md): next steps and dated reminders

## The dashboards

| Page | Path | What it answers |
|---|---|---|
| **Overview** | `/` | What are farmers doing across all 15 crops? Selling pace sized to the crop, cross-crop Things to watch (elevators, exports, stocks, ports, Thunder Bay), the all-crops weekly flash (deliveries, elevator shipments, exports, processing, stocks), pace cards, a 13-year heatmap, crop mix, all-crops deliveries by province, country elevator shipments and net build, and commercial stocks. |
| **Crop pages** (15) | `/wheat/`, `/canola/`, `/durum/`, `/barley/`, `/peas/`, `/oats/`, `/lentils/`, `/soybeans/`, `/corn/`, `/flaxseed/`, `/rye/`, `/beans/`, `/canaryseed/`, `/chickpeas/`, `/mustard/` | One crop from farm to use: generated Things to watch cards, weekly flash, pace math, farmer selling (pace sized to the crop, rank, projection, direct-to-processor and province shares), deliveries by province this week, country elevator throughput (net build, weeks of cover), port terminals (Pacific receipts vs exports, weeks of cover, Thunder Bay before freeze-up, export grade mix), exports by port, processing, feed and stocks. Canola adds a backtested crush and export forecast. |
| **Feed Grains** | `/feed/` | How much barley, wheat, durum, oats and Canadian and US corn Canada's livestock eat, from a feed model for West (MB, SK, AB, BC) and East (Ontario, Quebec and the Atlantic provinces), every crop year from 2012-13. The page walks through the three steps: animal numbers and slaughter set the grain energy needed, price and availability split it across grains, and corn is split by origin. It compares the result with StatCan's residual feed figure and a backtested forecast of it, and shows the drivers: energy-adjusted prices, supply, quality and livestock. |
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
   (cached in data/raw/external/)    ├─ src/dashboard_data.py  ─> dashboard/data.json  │ overview
                                     ├─ src/grain_data.py      ─> dashboard/<crop>/    │ crop pages (template)
                                     ├─ src/canola_forecast.py ─> dashboard/canola/forecast.json
                                     └─ src/feed_data.py       ─> dashboard/feed/data.json
                                          (+ src/feed_model.py, feed demand model)
                                                   │
                                       src/build_site.py ─> docs/ ─> GitHub Pages

SK Crop Planning Guide PDFs ──> src/crop_guide.py ──> reference/sk_crop_planning_guide.csv
                                                             │
                              src/margins.py (+ StatCan prices, yields) ──> src/margins_data.py ──> dashboard/margins/data.json
```

- **Pages are static HTML** that load their own `data.json`, with no server, framework or build step for the
  front end. All charts are hand-drawn SVG; the only external resource is Google Fonts.
- **Every number and every insight is computed from the data.** Things to watch cards, summary notes and
  rankings are generated in the page's script at load time, so they update with each refresh. Nothing on a page is
  written by hand for a particular week.
- **`src/build_site.py`** wraps each page in a full HTML document, adds the navigation bar and favicon, and writes
  `docs/`.
- **GitHub Pages** serves `docs/` from `main`, and the site updates about a minute after a push.

## Setup

Python 3.10+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python src/ingest.py        # downloads ~230 MB of CGC CSVs on first run
```

`data/` (raw downloads, caches and the processed parquet) and `.venv/` are not in git. `src/ingest.py` rebuilds the
CGC data, and each script downloads the StatCan tables and market series it needs on first use.

## Weekly refresh

CGC publishes on Thursdays.

```sh
.venv/bin/python src/external.py --markets  # CBOT corn and soy complex, USD/CAD (quick)
.venv/bin/python src/ingest.py              # re-downloads the current crop year only
.venv/bin/python src/dashboard_data.py      # overview
.venv/bin/python src/grain_data.py          # crop pages
.venv/bin/python src/canola_forecast.py
.venv/bin/python src/feed_data.py
.venv/bin/python src/margins_data.py        # uses the overview's deliveries, so run after dashboard_data.py
.venv/bin/python src/build_site.py
git add -A && git commit -m "Weekly refresh: week N" && git push
```

Before pushing, preview the site and check it (see [Checking a build](#checking-a-build)).

### Other updates

- **After any StatCan release** (production estimates, stocks, supply and disposition, livestock, farm prices):
  run `.venv/bin/python src/external.py --refresh`, which re-downloads every cached StatCan table and market series,
  then the weekly steps from `dashboard_data.py` on. Scripts only download what is missing, so without this, cached
  tables never update.
- **StatCan in-season canola estimates** (late August, mid-September, early December): also add a row to
  `reference/statcan_canola_vintages.csv` (harvest year, release date, Mt, survey, link to The Daily). The canola
  forecast's backtest uses these as they were published.
- **Each January,** when Saskatchewan Agriculture publishes the new Crop Planning Guide: add its format id to
  `FORMAT_IDS` in `src/crop_guide.py` (Publications Centre archive, product 122661), then run
  `.venv/bin/python src/crop_guide.py`, `src/margins_data.py` and `src/build_site.py`. The parser checks every budget
  against the guide's own arithmetic and stops if a column fails.

Dated reminders (for example, when StatCan's August farm prices come out) are in [TODO.md](TODO.md).

## Checking a build

```sh
cd docs && ../.venv/bin/python -m http.server 8000     # then open http://localhost:8000/
```

- Open each page you changed and the browser console: no script errors, and no empty charts, tables or cards.
- Check the numbers you touched against the source rows (see [DATA.md](DATA.md) for what each series is).
- Check at phone width (about 390 px): the page must not scroll sideways; wide tables scroll inside their frame.

`dashboard/` can be previewed the same way. Links between pages are relative, so they work there too; only the
built site in `docs/` has the navigation bar.

## Project layout

```
src/
  ingest.py          download and clean CGC Grain Statistics Weekly into data/processed/gsw.parquet
  db.py              DuckDB views over the cleaned data, plus the Canada Eastern grade filter
  external.py        StatCan tables, Yahoo futures and FX, CGC harvest-quality pages (cached in data/raw/external/)
  dashboard_data.py  data for the overview page
  grain_data.py      data for the crop pages, plus crop settings (GRAINS), province/channel splits and production
  canola_forecast.py canola crush and export forecast, with backtest
  feed_data.py       data for the feed page: StatCan residual, its regional split and forecast (with backtest)
  feed_model.py      feed demand model: animals x feeding rates by province, class and grain
  crop_guide.py      parse the Saskatchewan Crop Planning Guide PDFs into per-acre budgets
  margins.py         producer margins by crop (price x trend yield - guide costs)
  margins_data.py    data for the Producer Margins page (margins plus deliveries of the same crops)
  build_site.py      builds docs/ (HTML wrapper, navigation bar, favicons)
dashboard/
  index.html         overview page (edited directly)
  pipeline.html      crop page template: edit this, not dashboard/<crop>/index.html
  <crop>/            generated page copy plus data.json per crop
  feed/index.html    Feed Grains page (edited directly)
  margins/index.html Producer Margins page (edited directly)
reports/             forecast and estimate write-ups (canola_forecast.md, feed_estimate.md)
reference/           hand-curated inputs: StatCan in-season canola estimates (with sources), the parsed
                     Crop Planning Guide budgets (sk_crop_planning_guide.csv) and StatCan's 1999 per-animal
                     feed rates (statcan_livestock_feed_1999.csv)
docs/                built site, served by GitHub Pages (generated; don't edit)
DATA.md              sources, definitions and data file contents
TODO.md              next steps
```

Each script's module docstring explains what it builds, how to run it and its method.

## Making changes

**Common changes:**
- **Add a crop page:** add an entry to `GRAINS` (and its StatCan name to `STATCAN_CROPS`) in `src/grain_data.py`,
  then add its slug to `NAV` (top level or in the Special Crops menu; optionally `CROP_COLORS`) in
  `src/build_site.py`. The overview links to it automatically.
- **Add an analysis page:** create `dashboard/<name>/index.html` and its data script, and add it to `NAV` and
  `LABELS` in `src/build_site.py`. A cross-crop theme gets its own page; the overview stays all-crops only.
- **Change every crop page:** edit `dashboard/pipeline.html`, then run `src/grain_data.py` to copy it out.

**Conventions:**
- **Reorganize freely, but don't drop an existing analysis** when moving things between pages.
- **Write insights as rules over the data,** not as text about this week. A Things to watch card is a threshold and
  wording that adapt to direction; check it against every crop for misfires.
- **Confirm a series' definition from the source before labelling it.** CGC's definitions are in the explanatory
  notes of its old PDF weekly reports, not in the CSVs. Ask whether a new series is a subset of something already
  counted, and whether it covers Western Canada only. See [DATA.md](DATA.md).
- **Backtest every estimate year by year on earlier years only,** against simple baselines (last year, 5-year
  average, pace), and publish the results even when a baseline wins. Don't pick a method just because it scored best
  on a handful of test years, and keep data an analyst wouldn't have had at the time (such as final production) out
  of backtests.
- **Pages are for analysts and traders:** dense, copyable flash tables, trade terms, terse copy, method in the notes
  at the foot of the page. Factual interpretation, no trading advice.
- **Page, section and chart titles are title case** (applied by the site stylesheet); generated card headlines stay
  as sentences.
