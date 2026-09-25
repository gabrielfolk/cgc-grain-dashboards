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
