# CGC Grain Statistics Analysis

Analysis of Canadian Grain Commission [Grain Statistics Weekly](https://www.grainscanada.gc.ca/en/grain-research/statistics/grain-statistics-weekly/)
data: producer deliveries, elevator stocks, exports and processing for grains, oilseeds and pulses (crop years 2013-14 onward).

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python src/ingest.py        # downloads ~230 MB of CSVs on first run
```

Re-run `src/ingest.py` weekly (CGC publishes Thursdays). Completed crop years are cached in `data/raw/`; only
the current year is re-downloaded. `--refresh` re-downloads everything.

## Data

- `data/processed/gsw.parquet`: every CGC row, cleaned, long format:
  `crop_year, grain_week, week_ending, worksheet, metric, period, grain, grade, region, ktonnes`
- `src/db.py`: `connect()` returns a DuckDB connection with views:
  - `gsw`: the parquet file
  - `producer_deliveries`: weekly farmer deliveries by grain / province / channel (see docstring)
  - `commercial_stocks`: week-end stocks by grain in country elevators, processors and port terminals;
    the sum matches CGC's published commercial stocks total

## Dashboard

`dashboard/index.html` ("Prairie Delivery Pace") reads `dashboard/data.json`. To refresh:

```sh
.venv/bin/python src/ingest.py && .venv/bin/python src/dashboard_data.py
```

Crop deep dives (canola, wheat, durum, barley) share one page template, `dashboard/pipeline.html`.
`.venv/bin/python src/grain_data.py` writes `dashboard/<crop>/data.json` and a copy of the template for each crop.
Edit the template, not the copies. Crop settings (labels, which uses to show, notes, page links) are in `GRAINS` and
`LINKS` in `src/grain_data.py`.

Then republish each page with its data.json. To preview locally, run `python3 -m http.server` inside `dashboard/`.

## Forecasts

`src/canola_forecast.py` forecasts weekly canola crush and exports 1-4 weeks ahead and full crop-year totals. It
backtests every method on held-out crop years against simple baselines. Results and method:
`reports/canola_forecast.md`.

External inputs are in `src/external.py`: StatCan production and farm prices, and CBOT soy prices from Yahoo. When
StatCan publishes a new in-season canola estimate (late August, mid-September, early December), add a row to
`reference/statcan_canola_vintages.csv` and run `.venv/bin/python src/external.py --refresh`.

## Weekly refresh

```sh
.venv/bin/python src/ingest.py
.venv/bin/python src/dashboard_data.py
.venv/bin/python src/grain_data.py
.venv/bin/python src/canola_forecast.py
.venv/bin/python src/build_site.py
git add -A && git commit -m "Weekly refresh: week N" && git push
```

## Website

The dashboards are published with GitHub Pages at https://gabrielfolk.github.io/cgc-grain-dashboards/ from the
`docs/` folder on `main`. `src/build_site.py` turns the pages in `dashboard/` into complete HTML documents in
`docs/` and rewrites the links between pages to relative paths. Pushing `docs/` updates the site in about a minute.

### Quirks handled
- Column names and order differ between crop years; 2013-14 to 2017-18 live under a `/csv/` URL.
- Missing files return HTTP 200 with an HTML page (detected and skipped).
- Label typos and variants are normalized (`Sasktachewan`, `B.C.`, `Peas*`, ...); footnote rows are dropped.
- 2024-25 week 48 is published month-first; day/month-swapped dates are repaired.
- Weeks 21/22 are one combined holiday report (14-day gap).
- CGC's "Current Week" rows sum to 1-4% less than the crop-year totals (revisions go only into the cumulative
  figures). Use cumulative values, or `producer_deliveries.weekly_kt`, which is derived from them.
