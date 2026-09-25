"""DuckDB views over the processed CGC data for analysis.

    from db import connect
    con = connect()
    con.sql("select * from producer_deliveries limit 5").df()

Views
-----
gsw
    Every row from data/processed/gsw.parquet (all worksheets, long format).

producer_deliveries
    What farmers delivered each week, by grain, province and channel:
        channel = 'primary'        licensed primary (country) elevators
                  'process'        directly to processors (crushers, mills...)
                  'producer_cars'  farmer-loaded rail cars
    Summed across channels, this is CGC's "producer deliveries" total.
    `cumulative_kt` is CGC's crop-year-to-date figure; `weekly_kt` is its
    week-over-week difference. CGC's own "Current Week" rows don't add up to
    the cumulative totals because revisions only reach the cumulative figures,
    so weekly_kt is derived this way to stay consistent with the official totals.
    The week 21/22 holiday report covers two weeks.

commercial_stocks
    Week-end stocks in the licensed handling system, by grain and location:
        location = 'country'    primary (country) elevators
                   'process'    processors (crushers, mills...)
                   'terminal'   port terminals (all ports)
    Summed across locations, this matches CGC's published "Commercial Stocks"
    total (which CGC only publishes in the CSVs for 2014-15 to 2024-25). Grain
    stored on farms is not included.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
GSW_PATH = ROOT / "data" / "processed" / "gsw.parquet"


def connect(path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    if not GSW_PATH.exists():
        raise FileNotFoundError(f"{GSW_PATH} not found - run `python src/ingest.py` first")
    con = duckdb.connect(path)
    con.execute(f"create or replace view gsw as select * from read_parquet('{GSW_PATH}')")
    con.execute(
        """
        create or replace view producer_deliveries as
        with cumulative as (
            select
                crop_year, grain_week, week_ending, grain,
                coalesce(nullif(region, ''), 'Unspecified') as province,
                case worksheet
                    when 'Primary' then 'primary'
                    when 'Process' then 'process'
                    when 'Producer Cars' then 'producer_cars'
                end as channel,
                sum(ktonnes) as cumulative_kt
            from gsw
            where period = 'Crop Year'
              and ((worksheet = 'Primary' and metric = 'Deliveries')
                or (worksheet = 'Process' and metric = 'Producer Deliveries')
                or (worksheet = 'Producer Cars' and metric = 'Shipments'))
            group by all
        )
        select
            *,
            cumulative_kt - coalesce(lag(cumulative_kt) over (
                partition by crop_year, grain, province, channel order by grain_week
            ), 0) as weekly_kt
        from cumulative
        """
    )
    con.execute(
        """
        create or replace view commercial_stocks as
        select
            crop_year, grain_week, week_ending, grain,
            case worksheet when 'Primary' then 'country' when 'Process' then 'process' else 'terminal' end as location,
            sum(ktonnes) as kt
        from gsw
        where period = 'Current Week' and metric = 'Stocks'
          and worksheet in ('Primary', 'Process', 'Terminal Stocks')
        group by all
        """
    )
    return con
