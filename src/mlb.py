"""Build reference/mlb_seasons.csv from the MLB Stats API (statsapi.mlb.com, no key): per regular season, games
played, league attendance, and Toronto Blue Jays home games and attendance at Rogers Centre in Toronto.

Used by the mustard page, which sets these against Canadian mustard exports and domestic use.

    python src/mlb.py            # every season from 2016 to the last completed one

Run it once a year after the regular season ends (early October), then src/grain_data.py and src/build_site.py.

- games: regular-season games played, sum of every team's wins and losses / 2 (standings).
- league_attendance: sum of every team's regular-season home attendance (attendance endpoint).
- jays_toronto_games, jays_toronto_attendance: Blue Jays regular-season home games played at Rogers Centre, and
  their attendance. In 2020 the Jays played home games in Buffalo without fans; in 2021 they played in Dunedin
  and Buffalo before returning to Toronto on July 30, so 2021 is summed game by game from box scores.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reference" / "mlb_seasons.csv"
API = "https://statsapi.mlb.com/api/v1"
JAYS, TORONTO = 141, "Rogers Centre"
FIRST = 2016


def get(path: str, **params) -> dict:
    r = requests.get(f"{API}/{path}", params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def season(y: int) -> dict:
    standings = get("standings", leagueId="103,104", season=y, standingsTypes="regularSeason")
    games = sum(t["wins"] + t["losses"] for rec in standings["records"] for t in rec["teamRecords"]) // 2
    league = sum(r.get("attendanceTotalHome") or 0 for r in get("attendance", leagueId="103,104", season=y, gameType="R")["records"])
    sched = get("schedule", sportId=1, teamId=JAYS, season=y, gameType="R")
    home = {g["gamePk"]: g["venue"]["name"] for d in sched["dates"] for g in d["games"]
            if g["teams"]["home"]["team"]["id"] == JAYS and g["status"]["abstractGameState"] == "Final"
            and g["status"]["detailedState"] != "Postponed"}
    in_toronto = [pk for pk, v in home.items() if v == TORONTO]
    if home and len(in_toronto) == len(home):  # every home game in Toronto: the season total is Toronto's
        att = get("attendance", teamId=JAYS, season=y, gameType="R")["records"][0]["attendanceTotalHome"] or 0
    else:  # some home games elsewhere: add up the Toronto games from their box scores
        att = 0
        for pk in in_toronto:
            info = get(f"game/{pk}/boxscore").get("info", [])
            a = next((i.get("value", "") for i in info if i.get("label") == "Att"), "")
            att += int(re.sub(r"[^\d]", "", a) or 0)
    return {"season": y, "games": games, "league_attendance": league,
            "jays_toronto_games": len(in_toronto), "jays_toronto_attendance": att}


def main() -> None:
    today = dt.date.today()
    last = today.year if today.month >= 10 else today.year - 1  # the regular season ends in late September
    rows = [season(y) for y in range(FIRST, last + 1)]
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[*rows[0], "source"])
        w.writeheader()
        for r in rows:
            w.writerow({**r, "source": "MLB Stats API (statsapi.mlb.com): standings, attendance, schedule, box scores"})
    for r in rows:
        print(r)
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
