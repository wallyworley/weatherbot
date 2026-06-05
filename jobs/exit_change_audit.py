"""Before/after audit for the 2026-06-05 exit changes.

Watches whether lowering take-profit (0.70→0.35) and adding the stop-loss
(-0.50) actually cut the held-loser bleed. Groups settled fills by market day
and reports, per day: how many exited early (take-profit vs stop-loss) vs rode
to settlement, the held-loser P&L (the thing the changes should shrink), and
net P&L. Splits a before/after summary at the 2026-06-05 change.

Runs daily, emails the digest. Manual:
    python -m weather_bot.jobs.exit_change_audit
    python -m weather_bot.jobs.exit_change_audit --days 14 --no-email
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from weather_bot.data import persistence
from weather_bot.jobs import notify_email

log = logging.getLogger(__name__)

CHANGE_DATE = "2026-06-05"  # take-profit→0.35, stop-loss on, whitelist dropped

_PNL = (
    "CASE WHEN pf.exit_price IS NOT NULL "
    "THEN (pf.exit_price-pf.price)*pf.contracts-pf.fees-COALESCE(pf.exit_fees,0) "
    "WHEN pf.payout IS NOT NULL THEN (pf.payout-pf.price)*pf.contracts-pf.fees END"
)


def _daily(days: int) -> list[dict]:
    sql = f"""
        SELECT km.valid_date AS d,
               count(*) AS n,
               count(*) FILTER (WHERE pf.exit_reason LIKE 'TAKE_PROFIT%%') AS tp,
               count(*) FILTER (WHERE pf.exit_reason LIKE 'STOP_LOSS%%')   AS sl,
               count(*) FILTER (WHERE pf.exit_price IS NULL AND pf.payout=0) AS held_losers,
               round(COALESCE(sum({_PNL}) FILTER (WHERE pf.exit_price IS NULL AND pf.payout=0),0)::numeric,2) AS held_loss_pnl,
               round(COALESCE(sum({_PNL}) FILTER (WHERE pf.exit_price IS NOT NULL),0)::numeric,2) AS exit_pnl,
               round(COALESCE(sum({_PNL}),0)::numeric,2) AS net
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
         WHERE pf.settled = TRUE
           AND km.valid_date >= CURRENT_DATE - %s
         GROUP BY 1 ORDER BY 1
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (days,))
        return [dict(r) for r in cur.fetchall()]


def _summary(rows: list[dict]) -> dict:
    """Aggregate before vs on/after the change date."""
    out = {}
    for label, pred in (("before", lambda d: str(d) < CHANGE_DATE),
                        ("after", lambda d: str(d) >= CHANGE_DATE)):
        sel = [r for r in rows if pred(str(r["d"]))]
        n = sum(r["n"] for r in sel)
        exits = sum(r["tp"] + r["sl"] for r in sel)
        out[label] = {
            "days": len(sel),
            "n": n,
            "exit_rate": (exits / n * 100) if n else 0.0,
            "stop_losses": sum(r["sl"] for r in sel),
            "held_losers": sum(r["held_losers"] for r in sel),
            "held_loss_pnl": sum(float(r["held_loss_pnl"]) for r in sel),
            "net": sum(float(r["net"]) for r in sel),
        }
    return out


def _fmt(rows: list[dict], s: dict) -> str:
    lines = ["Exit-change before/after (2026-06-05: take-profit 0.70→0.35, stop-loss -0.50, whitelist dropped)", ""]
    lines.append(f"{'day':<12}{'n':>4}{'TP':>4}{'SL':>4}{'held_L':>8}{'held_$':>10}{'exit_$':>9}{'net':>9}")
    for r in rows:
        mark = " *" if str(r["d"]) == CHANGE_DATE else "  "
        lines.append(
            f"{str(r['d']):<12}{r['n']:>4}{r['tp']:>4}{r['sl']:>4}{r['held_losers']:>8}"
            f"{float(r['held_loss_pnl']):>10.2f}{float(r['exit_pnl']):>9.2f}{float(r['net']):>9.2f}{mark}"
        )
    b, a = s["before"], s["after"]
    lines += ["", "SUMMARY (held_$ per fill is the key number — should rise toward 0):",
        f"  BEFORE ({b['days']}d): n={b['n']} exit_rate={b['exit_rate']:.0f}% "
        f"stop_losses={b['stop_losses']} held_losers={b['held_losers']} "
        f"held_$={b['held_loss_pnl']:.0f} (={b['held_loss_pnl']/max(b['n'],1):.2f}/fill) net={b['net']:.0f}",
        f"  AFTER  ({a['days']}d): n={a['n']} exit_rate={a['exit_rate']:.0f}% "
        f"stop_losses={a['stop_losses']} held_losers={a['held_losers']} "
        f"held_$={a['held_loss_pnl']:.0f} (={a['held_loss_pnl']/max(a['n'],1):.2f}/fill) net={a['net']:.0f}",
        "",
        "Caveat: 6/5 is a transition day (open book straddles the change); read the trend, not one day."]
    return "\n".join(lines)


def run(days: int = 12, send_email: bool = True) -> str:
    rows = _daily(days)
    s = _summary(rows)
    report = _fmt(rows, s)
    log.info("\n%s", report)
    if send_email:
        b, a = s["before"], s["after"]
        bpf = b["held_loss_pnl"] / max(b["n"], 1)
        apf = a["held_loss_pnl"] / max(a["n"], 1)
        html = (
            f"<pre style='font-size:13px'>{report}</pre>"
            f"<p><b>Held-loss per fill:</b> before {bpf:+.2f} → after {apf:+.2f} "
            f"({'improved' if apf > bpf else 'worse'}). Stop-losses fired (after): {a['stop_losses']}.</p>"
        )
        notify_email.send_email(
            f"[weatherbot] exit-change audit — held-loss/fill {bpf:+.2f}→{apf:+.2f}", html)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=12)
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(run(days=args.days, send_email=not args.no_email))


if __name__ == "__main__":
    main()
