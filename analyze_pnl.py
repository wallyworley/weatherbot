#!/usr/bin/env python3
"""
Analyze realized P&L from paper trading.
Queries settled positions and calculates win rate, PnL stats.
"""
import sys
from pathlib import Path
from datetime import datetime

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from weather_bot.data.persistence import connect


def analyze_pnl():
    """Query settled trades and calculate statistics."""

    sql = """
    SELECT
      pf.id,
      pf.ticker,
      pf.side,
      pf.price as entry_price,
      pf.contracts,
      pf.fees,
      pf.payout,
      pf.settled,
      pf.ts,
      ROUND(COALESCE(pf.payout - (pf.contracts::numeric * pf.price + pf.fees), 0)::numeric, 2) as realized_pnl
    FROM paper_fill pf
    WHERE pf.settled = true OR pf.payout IS NOT NULL
    ORDER BY pf.ts DESC;
    """

    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        trades = cur.fetchall()

    if not trades:
        print("No settled trades yet.")
        return

    # Convert to list of dicts for easier analysis
    trade_list = [
        {
            'id': t['id'],
            'ticker': t['ticker'],
            'side': t['side'],
            'entry_price': float(t['entry_price']),
            'contracts': int(t['contracts']),
            'fees': float(t['fees']),
            'payout': float(t['payout']) if t['payout'] else 0,
            'settled': t['settled'],
            'ts': t['ts'],
            'pnl': float(t['realized_pnl'])
        }
        for t in trades
    ]

    # Calculate stats
    total_trades = len(trade_list)
    winning_trades = sum(1 for t in trade_list if t['pnl'] > 0)
    losing_trades = sum(1 for t in trade_list if t['pnl'] < 0)
    breakeven_trades = sum(1 for t in trade_list if t['pnl'] == 0)

    total_pnl = sum(t['pnl'] for t in trade_list)
    avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
    win_rate = winning_trades / total_trades if total_trades > 0 else 0

    max_win = max((t['pnl'] for t in trade_list), default=0)
    max_loss = min((t['pnl'] for t in trade_list), default=0)

    # Print summary
    print("\n" + "="*70)
    print(f"REALIZED P&L ANALYSIS - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    print(f"\nTotal settled trades:    {total_trades}")
    print(f"Winning trades:          {winning_trades} ({win_rate*100:.1f}%)")
    print(f"Losing trades:           {losing_trades}")
    print(f"Breakeven trades:        {breakeven_trades}")
    print(f"\nTotal P&L:               ${total_pnl:,.2f}")
    print(f"Average P&L per trade:   ${avg_pnl:,.2f}")
    print(f"Best trade:              ${max_win:,.2f}")
    print(f"Worst trade:             ${max_loss:,.2f}")

    # Detailed trades
    print(f"\n{'ID':<6} {'Ticker':<20} {'Side':<4} {'Entry':<7} {'Contracts':<10} {'PnL':<10} {'Settled'}")
    print("-"*70)
    for t in trade_list:
        settled_str = "✓" if t['settled'] else "○"
        print(f"{t['id']:<6} {t['ticker']:<20} {t['side']:<4} ${t['entry_price']:<6.3f} {t['contracts']:<10} ${t['pnl']:<9,.2f} {settled_str}")

    print("\n" + "="*70)


if __name__ == "__main__":
    analyze_pnl()
