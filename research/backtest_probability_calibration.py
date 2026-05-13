"""Walk-forward replay for empirical probability calibration.

This answers the question: if the current signal-based calibrator had existed,
and could only use calibration evidence known before each signal timestamp,
would it have improved probability quality and entry decisions?

The replay is intentionally signal/opportunity based, not a full portfolio
simulator. It does not model "already have an open fill for this ticker/side"
state. Use Brier and calibration error first; use replay P&L only as a rough
entry-quality stress test.
"""
from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from psycopg.rows import dict_row

from weather_bot.config import (
    BANKROLL_USD,
    PROB_CALIBRATION_DAYS_BACK,
    PROB_CALIBRATION_MAX_DELTA,
    PROB_CALIBRATION_MIN_BUCKET_N,
    PROB_CALIBRATION_PRIOR_N,
)
from weather_bot.data import persistence
from weather_bot.strategy import ev
from weather_bot.strategy.probability_calibration import (
    CalibrationResult,
    CalibrationStats,
    choose_stats,
    probability_bin,
    shrink_to_observed,
)

log = logging.getLogger(__name__)

_CAL_RAW_RE = re.compile(r"\bCAL\|raw=([0-9.]+)\|")


@dataclass(frozen=True)
class ReplayRow:
    signal_id: int
    ts: datetime
    ticker: str
    station: str
    lead_day: int
    raw_p_yes: float
    cal_p_yes: float
    yes_won: float
    cal_source: str
    cal_n: int
    raw_action: str
    cal_action: str
    raw_side: str
    cal_side: str
    raw_pnl: float
    cal_pnl: float
    raw_brier_yes: float
    cal_brier_yes: float
    raw_brier_side: float
    cal_brier_side: float


def raw_probability_from_signal(fair_prob: float, notes: str | None) -> float:
    """Recover the uncalibrated probability when CAL notes are present."""
    if notes:
        match = _CAL_RAW_RE.search(notes)
        if match:
            return min(0.99, max(0.01, float(match.group(1))))
    return min(0.99, max(0.01, float(fair_prob)))


def side_won(side: str, yes_won: float) -> float:
    return float(yes_won if side == "YES" else 1.0 - yes_won)


def signal_price(sig: ev.Signal) -> float | None:
    if sig.side == "YES":
        return sig.market_ask
    if sig.market_bid is None:
        return None
    return 1.0 - sig.market_bid


def simulated_entry_pnl(sig: ev.Signal, yes_won: float, bankroll: float = BANKROLL_USD) -> float:
    """P&L for one replayed entry if the evaluated signal opens."""
    if sig.action != "OPEN" or sig.size_usd < 1.0:
        return 0.0
    price = signal_price(sig)
    if price is None or price <= 0 or price >= 1:
        return 0.0
    contracts = max(1, int(sig.size_usd / price))
    fees = ev.fee_for_order(float(price), contracts)
    payout = side_won(sig.side, yes_won)
    return (payout - float(price)) * contracts - fees


def calibrate_walk_forward(
    station: str,
    raw_prob: float,
    asof_utc: datetime,
    lead_day: int,
    days_back: int = PROB_CALIBRATION_DAYS_BACK,
    min_n: int = PROB_CALIBRATION_MIN_BUCKET_N,
    prior_n: float = PROB_CALIBRATION_PRIOR_N,
    max_delta: float = PROB_CALIBRATION_MAX_DELTA,
) -> CalibrationResult:
    raw = min(0.99, max(0.01, float(raw_prob)))
    bin_id = probability_bin(raw)
    rows = _bucket_stats_asof(station, bin_id, asof_utc, lead_day, days_back)
    stats = choose_stats(rows, min_n=min_n)
    if stats is None:
        return CalibrationResult(raw, raw, applied=False, bin=bin_id)
    calibrated, shrink, delta = shrink_to_observed(
        raw,
        stats.mean_pred,
        stats.observed_freq,
        int(round(stats.n)),
        prior_n=prior_n,
        max_delta=max_delta,
    )
    return CalibrationResult(
        raw_prob=raw,
        calibrated_prob=calibrated,
        applied=abs(calibrated - raw) > 1e-9,
        source=stats.source,
        bin=bin_id,
        n=int(round(stats.n)),
        mean_pred=stats.mean_pred,
        observed_freq=stats.observed_freq,
        shrink=shrink,
        delta=delta,
    )


def _bucket_stats_asof(
    station: str,
    bin_id: int,
    asof_utc: datetime,
    lead_day: int,
    days_back: int,
) -> list[dict]:
    sql = """
    WITH signal_outcomes AS (
        SELECT km.station,
               km.ticker,
               COALESCE((regexp_match(s.notes, 'CAL\\|raw=([0-9.]+)\\|'))[1]::double precision,
                        s.fair_prob) AS p_yes,
               GREATEST(0, (km.valid_date - (s.ts AT TIME ZONE st.tz)::date)::int) AS lead_day,
               CASE
                   WHEN truth.value_f IS NULL THEN NULL
                   WHEN (km.lower_f IS NULL OR truth.value_f >= km.lower_f)
                    AND (km.upper_f IS NULL OR truth.value_f < km.upper_f)
                   THEN 1.0
                   ELSE 0.0
               END AS yes_won
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
          JOIN stations st ON st.code = km.station
          LEFT JOIN cli_obs c
                 ON c.station = km.station
                AND c.local_date = km.valid_date
                AND c.fetched_at <= %(asof)s
          LEFT JOIN daily_obs d
                 ON d.station = km.station
                AND d.local_date = km.valid_date
                AND d.updated_at <= %(asof)s
          LEFT JOIN LATERAL (
              SELECT CASE
                       WHEN km.var = 'TMAX_DAILY' THEN COALESCE(c.tmax_f, d.tmax_f)
                       WHEN km.var = 'TMIN_DAILY' THEN COALESCE(c.tmin_f, d.tmin_f)
                       ELSE NULL
                     END AS value_f
          ) truth ON TRUE
         WHERE s.ts < %(asof)s
           AND s.ts >= %(asof)s - (%(days_back)s || ' days')::interval
           AND s.fair_prob IS NOT NULL
           AND km.station IS NOT NULL
           AND km.valid_date IS NOT NULL
           AND km.var IN ('TMAX_DAILY', 'TMIN_DAILY')
    ), binned AS (
        SELECT station,
               ticker,
               lead_day,
               LEAST(10, GREATEST(1, WIDTH_BUCKET(p_yes, 0, 1, 10))) AS bin,
               p_yes,
               yes_won
          FROM signal_outcomes
         WHERE yes_won IS NOT NULL
    ), weighted AS (
        SELECT *,
               1.0 / COUNT(*) OVER (PARTITION BY ticker, bin) AS event_weight
          FROM binned
    ), bucketed AS (
        SELECT *
          FROM weighted
         WHERE bin = %(bin_id)s
    )
    SELECT source, n, mean_pred, observed_freq
      FROM (
        SELECT 'station_lead' AS source,
               SUM(event_weight) AS n,
               SUM(p_yes * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
               SUM(yes_won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
               0 AS priority
          FROM bucketed
         WHERE station = %(station)s AND lead_day = %(lead_day)s
        UNION ALL
        SELECT 'lead' AS source,
               SUM(event_weight) AS n,
               SUM(p_yes * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
               SUM(yes_won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
               1 AS priority
          FROM bucketed
         WHERE lead_day = %(lead_day)s
        UNION ALL
        SELECT 'station' AS source,
               SUM(event_weight) AS n,
               SUM(p_yes * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
               SUM(yes_won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
               2 AS priority
          FROM bucketed
         WHERE station = %(station)s
        UNION ALL
        SELECT 'global' AS source,
               SUM(event_weight) AS n,
               SUM(p_yes * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
               SUM(yes_won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
               3 AS priority
          FROM bucketed
      ) stats
     WHERE n > 0 AND mean_pred IS NOT NULL AND observed_freq IS NOT NULL
     ORDER BY priority
    """
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql,
            {
                "station": station,
                "bin_id": bin_id,
                "asof": asof_utc,
                "lead_day": lead_day,
                "days_back": days_back,
            },
        )
        return [dict(r) for r in cur.fetchall()]


def _signal_rows(
    start_date: date,
    end_date: date,
    limit: int | None = None,
    all_signals: bool = False,
) -> list[dict]:
    sql = """
    WITH base AS (
        SELECT s.id AS signal_id,
               s.ts,
               s.ticker,
               s.fair_prob,
               COALESCE((regexp_match(s.notes, 'CAL\\|raw=([0-9.]+)\\|'))[1]::double precision,
                        s.fair_prob) AS raw_p_yes,
               s.market_ask,
               s.market_bid,
               s.notes,
               km.station,
               km.var,
               km.valid_date,
               km.lower_f,
               km.upper_f,
               GREATEST(0, (km.valid_date - (s.ts AT TIME ZONE st.tz)::date)::int) AS lead_day,
               CASE
                   WHEN truth.value_f IS NULL THEN NULL
                   WHEN (km.lower_f IS NULL OR truth.value_f >= km.lower_f)
                    AND (km.upper_f IS NULL OR truth.value_f < km.upper_f)
                   THEN 1.0
                   ELSE 0.0
               END AS yes_won
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
          JOIN stations st ON st.code = km.station
          LEFT JOIN cli_obs c ON c.station = km.station AND c.local_date = km.valid_date
          LEFT JOIN daily_obs d ON d.station = km.station AND d.local_date = km.valid_date
          LEFT JOIN LATERAL (
              SELECT CASE
                       WHEN km.var = 'TMAX_DAILY' THEN COALESCE(c.tmax_f, d.tmax_f)
                       WHEN km.var = 'TMIN_DAILY' THEN COALESCE(c.tmin_f, d.tmin_f)
                       ELSE NULL
                     END AS value_f
          ) truth ON TRUE
         WHERE s.ts::date BETWEEN %(start_date)s AND %(end_date)s
           AND s.fair_prob IS NOT NULL
           AND s.market_ask IS NOT NULL
           AND s.market_bid IS NOT NULL
           AND km.station IS NOT NULL
           AND km.valid_date IS NOT NULL
           AND km.var IN ('TMAX_DAILY', 'TMIN_DAILY')
           AND truth.value_f IS NOT NULL
    ), ranked AS (
        SELECT *,
               LEAST(10, GREATEST(1, WIDTH_BUCKET(raw_p_yes, 0, 1, 10))) AS raw_bin,
               ROW_NUMBER() OVER (
                   PARTITION BY ticker, LEAST(10, GREATEST(1, WIDTH_BUCKET(raw_p_yes, 0, 1, 10)))
                   ORDER BY ts
               ) AS rn
          FROM base
    )
    SELECT *
      FROM ranked
     WHERE (%(all_signals)s OR rn = 1)
     ORDER BY ts
    """
    if limit is not None:
        sql += "\n LIMIT %(limit)s"
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "all_signals": all_signals,
    }
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def replay(
    start_date: date,
    end_date: date,
    days_back: int = PROB_CALIBRATION_DAYS_BACK,
    limit: int | None = None,
    all_signals: bool = False,
) -> list[ReplayRow]:
    rows: list[ReplayRow] = []
    for r in _signal_rows(start_date, end_date, limit=limit, all_signals=all_signals):
        raw_p = raw_probability_from_signal(float(r["fair_prob"]), r.get("notes"))
        cal = calibrate_walk_forward(
            str(r["station"]),
            raw_p,
            r["ts"],
            int(r["lead_day"]),
            days_back=days_back,
        )
        yes_won = float(r["yes_won"])
        raw_sig = ev.evaluate(r["ticker"], raw_p, float(r["market_ask"]), float(r["market_bid"]))
        cal_sig = ev.evaluate(r["ticker"], cal.calibrated_prob, float(r["market_ask"]), float(r["market_bid"]))
        raw_side_p = raw_p if raw_sig.side == "YES" else 1.0 - raw_p
        cal_side_p = cal.calibrated_prob if cal_sig.side == "YES" else 1.0 - cal.calibrated_prob
        rows.append(
            ReplayRow(
                signal_id=int(r["signal_id"]),
                ts=r["ts"],
                ticker=str(r["ticker"]),
                station=str(r["station"]),
                lead_day=int(r["lead_day"]),
                raw_p_yes=raw_p,
                cal_p_yes=cal.calibrated_prob,
                yes_won=yes_won,
                cal_source=cal.source,
                cal_n=cal.n,
                raw_action=raw_sig.action,
                cal_action=cal_sig.action,
                raw_side=raw_sig.side,
                cal_side=cal_sig.side,
                raw_pnl=simulated_entry_pnl(raw_sig, yes_won),
                cal_pnl=simulated_entry_pnl(cal_sig, yes_won),
                raw_brier_yes=(raw_p - yes_won) ** 2,
                cal_brier_yes=(cal.calibrated_prob - yes_won) ** 2,
                raw_brier_side=(raw_side_p - side_won(raw_sig.side, yes_won)) ** 2,
                cal_brier_side=(cal_side_p - side_won(cal_sig.side, yes_won)) ** 2,
            )
        )
    return rows


def _mean(values: Iterable[float]) -> float | None:
    vals = list(values)
    if not vals:
        return None
    return sum(vals) / len(vals)


def summarize(rows: list[ReplayRow]) -> dict:
    if not rows:
        return {}
    applied = [r for r in rows if abs(r.cal_p_yes - r.raw_p_yes) > 1e-9]
    raw_open = [r for r in rows if r.raw_action == "OPEN"]
    cal_open = [r for r in rows if r.cal_action == "OPEN"]
    return {
        "n": len(rows),
        "n_calibrated": len(applied),
        "raw_brier_yes": _mean(r.raw_brier_yes for r in rows),
        "cal_brier_yes": _mean(r.cal_brier_yes for r in rows),
        "raw_brier_side": _mean(r.raw_brier_side for r in rows),
        "cal_brier_side": _mean(r.cal_brier_side for r in rows),
        "raw_opens": len(raw_open),
        "cal_opens": len(cal_open),
        "raw_pnl": sum(r.raw_pnl for r in rows),
        "cal_pnl": sum(r.cal_pnl for r in rows),
    }


def _group_summary(rows: list[ReplayRow], key: str) -> list[dict]:
    groups: dict[object, list[ReplayRow]] = {}
    for row in rows:
        groups.setdefault(getattr(row, key), []).append(row)
    out = []
    for value, sub in sorted(groups.items(), key=lambda kv: str(kv[0])):
        s = summarize(sub)
        out.append({"group": value, **s})
    return out


def render_markdown(rows: list[ReplayRow], start_date: date, end_date: date) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Walk-Forward Probability Calibration Backtest — {date.today().isoformat()}",
        "",
        f"_generated {now}_",
        "",
        f"Window: `{start_date}` through `{end_date}`.",
        "",
        "This is a signal/opportunity replay. It uses only calibration evidence known before each signal timestamp. P&L is an unconstrained entry replay, so use Brier/calibration first.",
        "",
    ]
    s = summarize(rows)
    if not s:
        lines.append("No replayable settled signal outcomes found.")
        return "\n".join(lines)

    delta_yes = s["cal_brier_yes"] - s["raw_brier_yes"]
    delta_side = s["cal_brier_side"] - s["raw_brier_side"]
    lines += [
        "## Overall",
        "",
        "| signals | calibrated | raw YES Brier | calibrated YES Brier | delta | raw side Brier | calibrated side Brier | delta | raw opens | calibrated opens | raw P&L | calibrated P&L |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {s['n']} | {s['n_calibrated']} | "
            f"{s['raw_brier_yes']:.4f} | {s['cal_brier_yes']:.4f} | {delta_yes:+.4f} | "
            f"{s['raw_brier_side']:.4f} | {s['cal_brier_side']:.4f} | {delta_side:+.4f} | "
            f"{s['raw_opens']} | {s['cal_opens']} | ${s['raw_pnl']:+.2f} | ${s['cal_pnl']:+.2f} |"
        ),
        "",
        "## By Station",
        "",
        "| station | n | calibrated | raw YES Brier | cal YES Brier | delta | raw opens | cal opens | raw P&L | cal P&L |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in _group_summary(rows, "station"):
        lines.append(
            f"| {row['group']} | {row['n']} | {row['n_calibrated']} | "
            f"{row['raw_brier_yes']:.4f} | {row['cal_brier_yes']:.4f} | "
            f"{row['cal_brier_yes'] - row['raw_brier_yes']:+.4f} | "
            f"{row['raw_opens']} | {row['cal_opens']} | ${row['raw_pnl']:+.2f} | ${row['cal_pnl']:+.2f} |"
        )
    lines += [
        "",
        "## By Lead Day",
        "",
        "| lead | n | calibrated | raw YES Brier | cal YES Brier | delta | raw opens | cal opens | raw P&L | cal P&L |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in _group_summary(rows, "lead_day"):
        lines.append(
            f"| {row['group']} | {row['n']} | {row['n_calibrated']} | "
            f"{row['raw_brier_yes']:.4f} | {row['cal_brier_yes']:.4f} | "
            f"{row['cal_brier_yes'] - row['raw_brier_yes']:+.4f} | "
            f"{row['raw_opens']} | {row['cal_opens']} | ${row['raw_pnl']:+.2f} | ${row['cal_pnl']:+.2f} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "- Negative Brier delta is good: calibration improved probability accuracy.",
        "- Lower calibrated opens is expected if the model was overconfident.",
        "- P&L here ignores portfolio/open-position constraints. Treat it as an entry-quality stress test, not a trading ledger.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat, default=None)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None)
    parser.add_argument("--days-back", type=int, default=PROB_CALIBRATION_DAYS_BACK)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--all-signals", action="store_true",
                        help="Replay every repeated signal. Default uses one signal per ticker/probability bucket.")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    end = args.end_date or date.today()
    start = args.start_date or (end - timedelta(days=30))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("weather_bot.strategy.ev").setLevel(logging.ERROR)
    rows = replay(start, end, days_back=args.days_back, limit=args.limit, all_signals=args.all_signals)
    report = render_markdown(rows, start, end)
    print(report)
    if not args.no_write:
        out_dir = Path("research/reports")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"prob_calibration_walkforward_{date.today().isoformat()}.md"
        out_path.write_text(report)
        log.info("wrote %s", out_path)


if __name__ == "__main__":
    main()
