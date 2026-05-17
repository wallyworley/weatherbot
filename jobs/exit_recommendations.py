"""Flag open paper positions that have reached an early-exit threshold."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from psycopg.rows import dict_row

from weather_bot.data import persistence


def _rows(threshold: float) -> list[dict]:
    sql = """
    WITH open_fills AS (
        SELECT pf.id, pf.ticker, pf.side, pf.ts, pf.price, pf.contracts, pf.fees,
               km.station, km.valid_date, km.lower_f, km.upper_f
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
         WHERE pf.settled = FALSE
    )
    SELECT f.*,
           ms.ts AS snapshot_ts,
           CASE WHEN f.side = 'YES' THEN ms.yes_bid ELSE ms.no_bid END::float AS exit_bid
      FROM open_fills f
      LEFT JOIN LATERAL (
          SELECT *
            FROM market_snapshot ms
           WHERE ms.ticker = f.ticker
           ORDER BY ms.ts DESC
           LIMIT 1
      ) ms ON true
     ORDER BY f.station, f.valid_date, f.ticker
    """
    out = []
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            exit_bid = row["exit_bid"]
            if exit_bid is None:
                continue
            price = float(row["price"])
            max_gain = 1.0 - price
            gain = float(exit_bid) - price
            progress = gain / max_gain if max_gain > 0 else 0.0
            if progress >= threshold:
                out.append({**dict(row), "gain": gain, "progress": progress})
    return out


def render(rows: list[dict], threshold: float) -> str:
    lines = [
        f"# Early Exit Recommendations - {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}",
        "",
        f"Threshold: {threshold:.0%} of max gain. Paper/research advisory only.",
        "",
        "| fill | station | ticker | side | entry | exit bid | progress | contracts | snapshot |",
        "|---:|---|---|---|---:|---:|---:|---:|---|",
    ]
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['station']} | {r['ticker']} | {r['side']} | "
            f"{float(r['price']):.3f} | {float(r['exit_bid']):.3f} | "
            f"{float(r['progress']):.1%} | {r['contracts']} | {r['snapshot_ts']} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.70)
    args = parser.parse_args()
    print(render(_rows(args.threshold), args.threshold))
