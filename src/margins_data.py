"""Build dashboard/margins/data.json for the Producer Margins page.

Deliveries for the crops that have a margin (from the overview's build) plus the margins
from src/margins.py. Run after src/ingest.py:
    python src/margins_data.py
"""

from __future__ import annotations

import json
from pathlib import Path

import dashboard_data
import margins

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dashboard" / "margins" / "data.json"


def main() -> None:
    d = dashboard_data.build()
    cur = d["meta"]["current_year"]
    m = margins.build(now_harvest=int(cur[:4]))
    grains = [g for g in d["grains"] if g in {c["grain"] for c in m["crops"]}]
    pick = lambda table: {g: table[g] for g in grains if g in table}
    data = {
        "meta": d["meta"],
        "grains": grains,
        "years": d["years"],
        "deliveries": pick(d["deliveries"]),
        "production": pick(d["production"]),
        "province": pick(d["province"]),
        "channel": pick(d["channel"]),
        "margins": m,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    OUT_PATH.write_text(text)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)} ({len(text) / 1e3:.0f} KB, {len(grains)} grains, margins {m['meta']['first_year']}-{m['meta']['last_year']})")


if __name__ == "__main__":
    main()
