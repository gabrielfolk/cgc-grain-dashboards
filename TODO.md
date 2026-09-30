# TODO

- [ ] **Late October 2026: add August farm prices to the margins.** StatCan publishes August prices about eight
  weeks after the month ends (table 32-10-0077). Once they are out, run `.venv/bin/python src/external.py --refresh`,
  then `src/margins_data.py` and `src/build_site.py`. This fills in the 2026 harvest-price (August) margins on the
  Producer Margins page, which show "not published yet" until then.
- [ ] **January 2027: add the 2027 Crop Planning Guide.** When Saskatchewan Agriculture publishes it, add its format
  id to `FORMAT_IDS` in `src/crop_guide.py` (Publications Centre archive, product 122661), run
  `.venv/bin/python src/crop_guide.py`, then `src/margins_data.py` and `src/build_site.py`.

- [ ] **Automate the weekly refresh.** CGC publishes on Thursdays. Add a scheduled GitHub Action that runs the refresh
  scripts and `src/build_site.py`, then commits `docs/`. The site updates on push. Pushing workflow files first
  needs the `workflow` token scope: run `gh auth refresh -s workflow`.
- [ ] **Add a render check to the refresh.** Load every page in headless Chrome after `src/build_site.py` and fail
  if a page errors or a section comes up empty (the "Things to watch" cards, charts, flash tables). Run it in the
  scheduled refresh so unattended updates can't publish a broken page.
- [ ] **Check the site at phone width.** The province and Thunder Bay tables, the "Things to watch" cards and the
  Special Crops menu have only been checked at desktop widths.
- [ ] **Share the chart code between pages.** The overview, margins and crop template each carry their own copy of
  the chart helpers; move them into one shared script if more pages are added.
- [ ] **Find a rail data source.** CGC's weekly CSVs have no current rail or in-transit data (Western Rail stops in
  2013-14, In-Transit partway through 2024-25). Candidates: the railways' weekly performance reports or the Grain
  Monitoring Program.
- [ ] **Re-test the canola forecast with real prices.** Get ICE canola futures and canola oil and meal prices
  (for example from Barchart), then re-test the price features in `src/canola_forecast.py`. The free soybean-complex
  proxies made forecasts worse.
- [ ] **Extend the forecast to wheat and durum exports,** using StatCan production estimates the same way as canola.
- [ ] **Add more supply data to the forecast:** StatCan's September canola estimate and on-farm stocks (table 32-10-0007).
- [ ] **Re-score the forecast each season.** The backtest has only eight test years.
- [ ] **Decide on repo visibility.** The repo is public for now. To make the code private while keeping the site
  public, either upgrade to GitHub Pro and switch the repo to private (same site address), or use two repos: this one
  private, plus a small public repo that holds only the built `docs/` site. On GitHub Free, a private repo can't serve
  a public Pages site.
- [ ] **Consider a GitHub no-reply commit email** (Settings → Emails) so the personal email isn't shown in the
  public commit history.
- [ ] **Add CGC harvest-sample grade distributions to the feed page** (share of CWRS, durum, barley and oats samples
  grading feed). CGC publishes them in different formats each year (HTML tables, spreadsheets, PDFs), and the
  2023-2025 reports have moved on CGC's site, so each year needs its own parser.
- [ ] **Add AAFC weekly slaughter data** to the feed page for a timelier livestock signal than StatCan's annual figures.
