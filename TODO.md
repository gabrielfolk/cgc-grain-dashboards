# TODO

- [ ] **Automate the weekly refresh.** CGC publishes on Thursdays. Add a scheduled GitHub Action that runs the refresh
  scripts and `src/build_site.py`, then commits `docs/`. The site updates on push. Pushing workflow files first
  needs the `workflow` token scope: run `gh auth refresh -s workflow`. The claude.ai copies of the pages still need
  republishing separately.
- [ ] **Add more crop deep dives:** peas, lentils, oats. Each needs one entry in `GRAINS` in `src/grain_data.py`,
  plus its URL in `LINKS` after publishing.
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
