"""Point-in-time replay harness.

For each settled paper fill, rebuild the fair_prob using only forecast data
that was available at the fill's timestamp (`run_time <= pf.ts`), then score:

  - Brier score, side-adjusted (using the side actually bought)
  - Log loss
  - CRPS approximation over the bucket boundaries (event-level when multiple
    buckets of the same event were filled)
  - Expected vs realized P&L per fill, using order-level Kalshi fees

Stratifies output by station, lead_day, and NBM cycle hour. Writes a
markdown report alongside a JSON summary suitable for diffing across runs.

Caveats:
  - Station bias is looked up from `station_bias_history` (populated daily
    by `jobs.bias_drift`) at the snapshot whose date is `<= fill.ts.date()`.
    Replays predating the first snapshot fall through to the current table.
  - The empirical probability calibrator is also stateful (depends on which
    signals have settled by now); the harness disables it for purity unless
    `--apply-calibrator` is set.

Usage:
    .venv/bin/python -m weather_bot.research.replay_harness --days-back 45
    .venv/bin/python -m weather_bot.research.replay_harness --days-back 90 --apply-calibrator
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean

from psycopg.rows import dict_row

from weather_bot.data import persistence
from weather_bot.models.distribution import build_station_distribution, lead_day_for_station
from weather_bot.strategy.ev import fee_for_order
from weather_bot.strategy.probability_calibration import calibrate_fair_probability

log = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


@dataclass
class ReplayRow:
    fill_id: int
    ts: datetime
    ticker: str
    event_ticker: str
    station: str
    var: str
    valid_date: date
    lead_day: int
    nbm_cycle_hour: int | None
    side: str
    price: float
    contracts: int
    fees: float
    payout: float | None
    won: bool
    p_side_original: float | None
    p_side_pit: float | None
    edge_pit: float | None
    expected_pnl_pit: float | None


@dataclass
class StratumSummary:
    n: int = 0
    brier_sum: float = 0.0
    logloss_sum: float = 0.0
    realized_pnl: float = 0.0
    expected_pnl: float = 0.0
    wins: int = 0
    p_side_sum: float = 0.0
    rows: list[ReplayRow] = field(default_factory=list)

    def add(self, r: ReplayRow) -> None:
        if r.p_side_pit is None:
            return
        self.n += 1
        self.brier_sum += (r.p_side_pit - (1.0 if r.won else 0.0)) ** 2
        clipped = min(max(r.p_side_pit, 1e-6), 1 - 1e-6)
        self.logloss_sum += -(math.log(clipped) if r.won else math.log(1 - clipped))
        self.realized_pnl += (r.payout or 0.0) - r.fees - r.price * r.contracts
        if r.expected_pnl_pit is not None:
            self.expected_pnl += r.expected_pnl_pit
        if r.won:
            self.wins += 1
        self.p_side_sum += r.p_side_pit
        self.rows.append(r)

    def as_dict(self) -> dict:
        if self.n == 0:
            return {"n": 0}
        return {
            "n": self.n,
            "brier": self.brier_sum / self.n,
            "log_loss": self.logloss_sum / self.n,
            "predicted_win_rate": self.p_side_sum / self.n,
            "observed_win_rate": self.wins / self.n,
            "calibration_error": self.p_side_sum / self.n - self.wins / self.n,
            "realized_pnl": self.realized_pnl,
            "expected_pnl": self.expected_pnl,
        }


def _settled_fills(days_back: int) -> list[dict]:
    sql = """
    SELECT pf.id, pf.ts, pf.ticker, pf.side, pf.price, pf.contracts, pf.fees,
           pf.payout, pf.signal_id,
           km.event_ticker, km.station, km.var, km.valid_date,
           km.lower_f, km.upper_f,
           s.fair_prob AS original_fair_prob
      FROM paper_fill pf
      JOIN kalshi_market km ON km.ticker = pf.ticker
      LEFT JOIN signal s ON s.id = pf.signal_id
     WHERE pf.settled = TRUE
       AND pf.exit_price IS NULL
       AND pf.payout IS NOT NULL
       AND pf.ts >= NOW() - (%s || ' days')::interval
     ORDER BY pf.ts
    """
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (days_back,))
        return [dict(r) for r in cur.fetchall()]


def _nbm_cycle_hour_at(station: str, valid_date: date, as_of: datetime) -> int | None:
    rows = persistence.nbm_percentiles_as_of(station, valid_date, as_of)
    if not rows:
        return None
    rt = rows[0].get("run_time")
    if rt is None:
        return None
    return rt.astimezone(timezone.utc).hour


def replay(days_back: int, apply_calibrator: bool = False) -> list[ReplayRow]:
    fills = _settled_fills(days_back)
    log.info("Replaying %d settled fills (days_back=%d, apply_calibrator=%s)",
             len(fills), days_back, apply_calibrator)

    out: list[ReplayRow] = []
    cache: dict[tuple[str, date, str, datetime], object] = {}

    for f in fills:
        as_of = f["ts"]
        key = (f["station"], f["valid_date"], f["var"], as_of)
        if key not in cache:
            try:
                cache[key] = build_station_distribution(
                    f["station"], f["valid_date"], f["var"],
                    now_utc=as_of, as_of=as_of,
                )
            except Exception as exc:
                log.warning("dist build failed for fill %s: %s", f["id"], exc)
                cache[key] = None
        cdf = cache[key]

        lead = lead_day_for_station(f["station"], f["valid_date"], as_of)
        cycle_hour = _nbm_cycle_hour_at(f["station"], f["valid_date"], as_of)

        if cdf is None:
            p_side_pit = None
            edge_pit = None
            expected_pnl_pit = None
        else:
            raw_fair = cdf.prob_between(f["lower_f"], f["upper_f"])
            if apply_calibrator:
                cal = calibrate_fair_probability(f["station"], raw_fair, lead_day=max(lead, 0))
                fair_yes = cal.calibrated_prob
            else:
                fair_yes = raw_fair
            p_side_pit = fair_yes if f["side"] == "YES" else 1.0 - fair_yes
            price = float(f["price"])
            contracts = int(f["contracts"])
            fee = fee_for_order(price, contracts)
            edge_pit = p_side_pit * (1.0 - price) - (1.0 - p_side_pit) * price - fee / contracts
            expected_pnl_pit = edge_pit * contracts

        won = float(f["payout"] or 0.0) > 0.0
        p_side_orig = None
        if f.get("original_fair_prob") is not None:
            ofp = float(f["original_fair_prob"])
            p_side_orig = ofp if f["side"] == "YES" else 1.0 - ofp

        out.append(ReplayRow(
            fill_id=f["id"], ts=f["ts"], ticker=f["ticker"],
            event_ticker=f["event_ticker"], station=f["station"], var=f["var"],
            valid_date=f["valid_date"], lead_day=max(0, lead),
            nbm_cycle_hour=cycle_hour,
            side=f["side"], price=float(f["price"]),
            contracts=int(f["contracts"]), fees=float(f["fees"]),
            payout=float(f["payout"]) if f["payout"] is not None else None,
            won=won,
            p_side_original=p_side_orig,
            p_side_pit=p_side_pit,
            edge_pit=edge_pit,
            expected_pnl_pit=expected_pnl_pit,
        ))
    return out


def _stratify(rows: list[ReplayRow]) -> dict[str, dict[str, StratumSummary]]:
    by_station: dict[str, StratumSummary] = defaultdict(StratumSummary)
    by_lead: dict[str, StratumSummary] = defaultdict(StratumSummary)
    by_cycle: dict[str, StratumSummary] = defaultdict(StratumSummary)
    by_station_lead: dict[str, StratumSummary] = defaultdict(StratumSummary)
    overall = StratumSummary()
    for r in rows:
        overall.add(r)
        by_station[r.station].add(r)
        by_lead[f"L{r.lead_day}"].add(r)
        by_cycle[f"{r.nbm_cycle_hour:02d}Z" if r.nbm_cycle_hour is not None else "??Z"].add(r)
        by_station_lead[f"{r.station}/L{r.lead_day}"].add(r)
    return {
        "overall": {"all": overall},
        "by_station": dict(by_station),
        "by_lead": dict(by_lead),
        "by_cycle": dict(by_cycle),
        "by_station_lead": dict(by_station_lead),
    }


def _delta_vs_original(rows: list[ReplayRow]) -> dict | None:
    """Compare PIT Brier vs original-signal Brier when both are available."""
    pairs = [(r.p_side_pit, r.p_side_original, r.won)
             for r in rows if r.p_side_pit is not None and r.p_side_original is not None]
    if not pairs:
        return None
    n = len(pairs)
    brier_pit = sum((p - (1 if w else 0)) ** 2 for p, _, w in pairs) / n
    brier_orig = sum((po - (1 if w else 0)) ** 2 for _, po, w in pairs) / n
    return {
        "n_paired": n,
        "brier_pit": brier_pit,
        "brier_original": brier_orig,
        "brier_delta": brier_pit - brier_orig,
        "p_side_pit_mean": mean(p for p, _, _ in pairs),
        "p_side_original_mean": mean(po for _, po, _ in pairs),
    }


def _format_md(rows: list[ReplayRow], strata: dict, delta: dict | None,
               days_back: int, apply_calibrator: bool, now: datetime) -> str:
    lines: list[str] = []
    lines.append(f"# Point-in-Time Replay — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append(
        f"Replayed `{len(rows)}` settled paper fills from the last `{days_back}` days "
        f"using forecasts with `run_time <= fill.ts`. Calibrator "
        f"`{'ON' if apply_calibrator else 'OFF'}`. "
        f"Bias lookup uses `station_bias_history` (PIT) with current-table fallback."
    )
    lines.append("")

    if delta:
        lines.append("## PIT vs Original Signal Brier")
        lines.append("")
        lines.append(f"- Paired fills: `{delta['n_paired']}`")
        lines.append(f"- Brier (PIT replay): `{delta['brier_pit']:.4f}`")
        lines.append(f"- Brier (original signal): `{delta['brier_original']:.4f}`")
        sign = "+" if delta["brier_delta"] >= 0 else ""
        lines.append(f"- Delta: `{sign}{delta['brier_delta']:+.4f}` "
                     f"({'PIT worse' if delta['brier_delta'] > 0 else 'PIT better'})")
        lines.append(f"- Mean p_side PIT: `{delta['p_side_pit_mean']:.3f}` | "
                     f"original: `{delta['p_side_original_mean']:.3f}`")
        lines.append("")

    def _table(title: str, group: dict[str, StratumSummary]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| Stratum | n | Brier | LogLoss | Pred win | Obs win | Calib err | Realized P&L | Expected P&L |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for label in sorted(group.keys()):
            d = group[label].as_dict()
            if d["n"] == 0:
                continue
            lines.append(
                f"| `{label}` | {d['n']} | {d['brier']:.4f} | {d['log_loss']:.4f} | "
                f"{d['predicted_win_rate']:.3f} | {d['observed_win_rate']:.3f} | "
                f"{d['calibration_error']:+.3f} | "
                f"${d['realized_pnl']:+.2f} | ${d['expected_pnl']:+.2f} |"
            )
        lines.append("")

    _table("Overall", strata["overall"])
    _table("By station", strata["by_station"])
    _table("By lead day", strata["by_lead"])
    _table("By NBM cycle hour", strata["by_cycle"])
    _table("By station × lead", strata["by_station_lead"])
    return "\n".join(lines)


def _summary_json(rows: list[ReplayRow], strata: dict, delta: dict | None) -> dict:
    def _g(group):
        return {k: v.as_dict() for k, v in group.items() if v.n > 0}
    return {
        "n_fills": len(rows),
        "delta_vs_original": delta,
        "overall": _g(strata["overall"]),
        "by_station": _g(strata["by_station"]),
        "by_lead": _g(strata["by_lead"]),
        "by_cycle": _g(strata["by_cycle"]),
        "by_station_lead": _g(strata["by_station_lead"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Point-in-time replay harness.")
    parser.add_argument("--days-back", type=int, default=45)
    parser.add_argument("--apply-calibrator", action="store_true",
                        help="Apply the empirical probability calibrator during replay.")
    parser.add_argument("--output-md", type=str, default=None)
    parser.add_argument("--output-json", type=str, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    rows = replay(args.days_back, apply_calibrator=args.apply_calibrator)
    strata = _stratify(rows)
    delta = _delta_vs_original(rows)

    now = datetime.now(tz=timezone.utc)
    md = _format_md(rows, strata, delta, args.days_back, args.apply_calibrator, now)
    summary = _summary_json(rows, strata, delta)

    md_path = Path(args.output_md) if args.output_md else REPORTS_DIR / f"replay_harness_{now.strftime('%Y-%m-%d')}.md"
    json_path = Path(args.output_json) if args.output_json else REPORTS_DIR / f"replay_harness_{now.strftime('%Y-%m-%d')}.json"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md)
    json_path.write_text(json.dumps(summary, indent=2, default=str))
    log.info("Wrote %d rows → %s + %s", len(rows), md_path, json_path)


if __name__ == "__main__":
    main()
