"""
Paper trading P&L report.

Usage:
    python -m weather_bot.jobs.paper_report
    python -m weather_bot.jobs.paper_report --days 30
    python -m weather_bot.jobs.paper_report --show-open

Reports:
  * Overall stats: win rate, total notional, gross P&L, fees, net P&L, ROI
  * Realized edge vs expected edge (sanity check on signal calibration)
  * Per-day breakdown
  * Optional: list of currently open (unsettled) positions
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from weather_bot.data import persistence


def _hr(title: str):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _overall_stats(cur, days: int) -> None:
    _hr(f"OVERALL — last {days} days")
    cur.execute(
        """
        SELECT
            COUNT(*)                                               AS n_total,
            COUNT(*) FILTER (WHERE settled)                        AS n_settled,
            COUNT(*) FILTER (WHERE settled AND payout > 0)         AS n_wins,
            COALESCE(SUM(price * contracts), 0)                    AS notional,
            COALESCE(SUM(fees), 0)                                 AS fees,
            COALESCE(SUM(CASE WHEN settled
                              THEN (payout - price) * contracts - fees END), 0) AS net_pnl,
            COALESCE(SUM(CASE WHEN settled
                              THEN (payout - price) * contracts END), 0)        AS gross_pnl
        FROM paper_fill
        WHERE ts >= (CURRENT_DATE - %s::INT)
        """,
        (days,),
    )
    r = cur.fetchone()
    if not r or r["n_total"] == 0:
        print("  (no paper fills in window)")
        return

    settled = r["n_settled"] or 0
    wins = r["n_wins"] or 0
    net = float(r["net_pnl"] or 0)
    gross = float(r["gross_pnl"] or 0)
    notional = float(r["notional"] or 0)
    fees = float(r["fees"] or 0)

    print(f"  fills total      : {r['n_total']}")
    print(f"  fills settled    : {settled}")
    if settled:
        print(f"  wins / losses    : {wins} / {settled - wins}  "
              f"({100*wins/settled:.1f}% win rate)")
    print(f"  notional staked  : ${notional:,.2f}")
    print(f"  fees             : ${fees:,.2f}")
    print(f"  gross P&L        : ${gross:+,.2f}")
    print(f"  net P&L          : ${net:+,.2f}")
    if notional > 0 and settled > 0:
        print(f"  ROI on settled   : {100*net/notional:+.2f}%")


def _edge_realization(cur, days: int) -> None:
    _hr("EXPECTED vs REALIZED EDGE (calibration check)")
    cur.execute(
        """
        SELECT pf.id, pf.ticker, pf.side, pf.price, pf.contracts, pf.fees,
               pf.settled, pf.payout, s.edge AS expected_edge
        FROM paper_fill pf
        LEFT JOIN signal s ON s.id = pf.signal_id
        WHERE pf.settled = TRUE
          AND pf.ts >= (CURRENT_DATE - %s::INT)
        """,
        (days,),
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no settled fills yet)")
        return

    exp_total = 0.0
    real_total = 0.0
    for r in rows:
        per_contract_real = (r["payout"] - r["price"]) - (r["fees"] / r["contracts"])
        exp_total += (r["expected_edge"] or 0) * r["contracts"]
        real_total += per_contract_real * r["contracts"]
    n = len(rows)
    print(f"  settled fills    : {n}")
    print(f"  expected edge ∑  : ${exp_total:+,.2f}   "
          f"(avg ${exp_total/n:+,.4f} per contract)")
    print(f"  realized edge ∑  : ${real_total:+,.2f}  "
          f"(avg ${real_total/n:+,.4f} per contract)")
    print(f"  diff             : ${real_total - exp_total:+,.2f}   "
          "(negative = forecast was too confident)")


def _per_day_breakdown(cur, days: int) -> None:
    _hr(f"PER-DAY — last {days} days")
    cur.execute(
        """
        SELECT km.valid_date AS d,
               COUNT(*)                                         AS n,
               COUNT(*) FILTER (WHERE pf.settled)               AS n_settled,
               COUNT(*) FILTER (WHERE pf.settled AND pf.payout>0) AS wins,
               SUM(pf.price * pf.contracts)                     AS notional,
               SUM(CASE WHEN pf.settled
                        THEN (pf.payout - pf.price) * pf.contracts - pf.fees END) AS net_pnl
        FROM paper_fill pf
        JOIN kalshi_market km ON km.ticker = pf.ticker
        WHERE km.valid_date >= (CURRENT_DATE - %s::INT)
        GROUP BY km.valid_date
        ORDER BY km.valid_date DESC
        """,
        (days,),
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no activity)")
        return
    print(f"  {'date':<12} {'fills':>6} {'settled':>8} {'wins':>5} "
          f"{'notional':>10} {'net P&L':>10}")
    for r in rows:
        net = r["net_pnl"]
        net_txt = f"${float(net):+,.2f}" if net is not None else "       -"
        print(
            f"  {str(r['d']):<12} {r['n']:>6} {r['n_settled']:>8} "
            f"{r['wins'] or 0:>5} ${float(r['notional']):>9,.2f} {net_txt:>10}"
        )


def _open_positions(cur) -> None:
    _hr("OPEN POSITIONS (unsettled)")
    cur.execute(
        """
        SELECT pf.id, pf.ticker, pf.side, pf.price, pf.contracts, pf.fees,
               km.valid_date, km.lower_f, km.upper_f, km.var, km.station
        FROM paper_fill pf
        JOIN kalshi_market km ON km.ticker = pf.ticker
        WHERE pf.settled = FALSE
        ORDER BY km.valid_date ASC, pf.ticker
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("  (none)")
        return
    print(f"  {'ticker':<26} {'side':<4} {'price':>6} {'size':>5} "
          f"{'valid':<12} {'var':<12} {'range':<14}")
    for r in rows:
        rng = f"[{r['lower_f']},{r['upper_f']})"
        print(
            f"  {r['ticker']:<26} {r['side']:<4} {r['price']:>6.3f} "
            f"{r['contracts']:>5} {str(r['valid_date']):<12} {r['var']:<12} {rng:<14}"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--show-open", action="store_true")
    args = ap.parse_args()

    with persistence.connect() as conn, conn.cursor() as cur:
        _overall_stats(cur, args.days)
        _edge_realization(cur, args.days)
        _per_day_breakdown(cur, args.days)
        if args.show_open:
            _open_positions(cur)


if __name__ == "__main__":
    main()
