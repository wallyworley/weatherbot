"""EXP-C2: METAR-to-CLI late-day gap edge audit.

Research-only. Does not modify trading logic.

This tests a narrow external thesis: when the live METAR high is near the top
of the currently implied Kalshi temperature bracket, the bracket(s) above may
retain more CLI-settlement probability than the market prices.

The experiment uses canonical coherent lead-0 snapshots, reconstructs the
station-local METAR max available at that snapshot, finds the bucket containing
that live max, and scores whether settlement finished above that bucket.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from weather_bot.data import persistence
from weather_bot.research.market_relative_center_benchmark import (
    BucketRow,
    _REALIZED_SQL,
    _YES_WIN_SQL,
    _normalize,
    coherent_snapshot_rows,
)


DEFAULT_STATIONS = ("KLAX", "KHOU", "KMIA", "KNYC", "KMDW")
HOUR_SEGMENTS = (
    ("all", 0),
    ("noon_plus", 12),
    ("two_pm_plus", 14),
    ("four_pm_plus", 16),
)


@dataclass(frozen=True)
class GapEvent:
    station: str
    valid_date: date
    snapshot_ts: datetime
    local_hour: int
    truth_f: float
    metar_max_f: float
    observed_bucket: str
    top_gap_f: float
    outcome_above_any: int
    market_p_above_any: float
    model_p_above_any: float
    outcome_next_bucket: int | None
    market_p_next_bucket: float | None
    model_p_next_bucket: float | None
    next_bucket: str | None
    n_buckets: int


@dataclass(frozen=True)
class GroupSummary:
    group: str
    n: int
    actual_above_rate: float
    market_p_above: float
    model_p_above: float
    market_residual: float
    market_residual_ci_low: float | None
    market_residual_ci_high: float | None
    market_brier_above: float
    model_brier_above: float
    model_minus_market_brier: float
    next_n: int
    actual_next_rate: float | None
    market_p_next: float | None
    market_next_residual: float | None
    market_next_residual_ci_low: float | None
    market_next_residual_ci_high: float | None


def _mean(xs: Iterable[float]) -> float:
    return statistics.fmean(list(xs))


def _ci(xs: list[float]) -> tuple[float | None, float | None]:
    if len(xs) < 2:
        return None, None
    center = statistics.fmean(xs)
    se = statistics.stdev(xs) / math.sqrt(len(xs))
    return center - 1.96 * se, center + 1.96 * se


def _brier_binary(ps: list[float], ys: list[int]) -> float:
    return statistics.fmean((p - y) ** 2 for p, y in zip(ps, ys))


def _bucket_label(row: BucketRow) -> str:
    if row.lower_f is None and row.upper_f is not None:
        return f"<{row.upper_f:g}"
    if row.lower_f is not None and row.upper_f is None:
        return f">={row.lower_f:g}"
    return f"[{row.lower_f:g},{row.upper_f:g})"


def _metar_max_so_far(station: str, local_date: date, as_of: datetime) -> float | None:
    sql = """
    SELECT MAX(mo.temp_f) AS m
      FROM metar_obs mo
      JOIN stations st ON st.code = mo.station
     WHERE mo.station = %s
       AND (mo.obs_time AT TIME ZONE st.tz)::date = %s
       AND mo.obs_time <= %s
       AND mo.temp_f IS NOT NULL
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, local_date, as_of))
        row = cur.fetchone()
        return float(row["m"]) if row and row["m"] is not None else None


def _local_hour(station: str, ts: datetime) -> int:
    sql = "SELECT EXTRACT(HOUR FROM (%s AT TIME ZONE tz))::int AS h FROM stations WHERE code=%s"
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (ts, station))
        row = cur.fetchone()
        return int(row["h"]) if row else -1


def _event_rows(rows: list[BucketRow]) -> dict[tuple[str, date], list[BucketRow]]:
    out: dict[tuple[str, date], list[BucketRow]] = defaultdict(list)
    for r in rows:
        if r.lead_day == 0 and r.var == "TMAX_DAILY":
            out[(r.station, r.valid_date)].append(r)
    return out


def _collect_all_valid_rows(
    days: int,
    stations: tuple[str, ...],
    tick_minutes: int,
    min_buckets: int,
) -> tuple[list[BucketRow], dict]:
    sql = f"""
    SELECT s.ticker,
           s.ts,
           s.fair_prob::float AS model_p,
           ((s.market_ask::float + s.market_bid::float) / 2.0) AS market_p,
           km.station,
           km.valid_date,
           km.var,
           km.lower_f::float AS lower_f,
           km.upper_f::float AS upper_f,
           {_REALIZED_SQL} AS truth_f,
           {_YES_WIN_SQL} AS yes_win,
           GREATEST(0, (km.valid_date - (s.ts AT TIME ZONE st.tz)::date)::int) AS lead_day
      FROM signal s
      JOIN kalshi_market km ON km.ticker = s.ticker
      JOIN stations st ON st.code = km.station
      LEFT JOIN cli_obs co ON co.station = km.station AND co.local_date = km.valid_date
      LEFT JOIN daily_obs d ON d.station = km.station AND d.local_date = km.valid_date
     WHERE km.valid_date >= CURRENT_DATE - (%(days)s || ' days')::interval
       AND km.valid_date < CURRENT_DATE
       AND km.var = 'TMAX_DAILY'
       AND km.station = ANY(%(stations)s)
       AND GREATEST(0, (km.valid_date - (s.ts AT TIME ZONE st.tz)::date)::int) = 0
       AND s.fair_prob IS NOT NULL
       AND s.market_ask IS NOT NULL
       AND s.market_bid IS NOT NULL
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, {"days": days, "stations": list(stations)})
        rows = cur.fetchall()

    out = []
    for r in rows:
        if r["truth_f"] is None or r["yes_win"] is None:
            continue
        model_p = float(r["model_p"])
        market_p = float(r["market_p"])
        if not (0.0 <= model_p <= 1.0 and 0.0 <= market_p <= 1.0):
            continue
        out.append(
            BucketRow(
                station=r["station"],
                valid_date=r["valid_date"],
                var=r["var"],
                lead_day=int(r["lead_day"]),
                ticker=r["ticker"],
                ts=r["ts"],
                lower_f=float(r["lower_f"]) if r["lower_f"] is not None else None,
                upper_f=float(r["upper_f"]) if r["upper_f"] is not None else None,
                truth_f=float(r["truth_f"]),
                yes_win=int(r["yes_win"]),
                model_p=model_p,
                market_p=market_p,
            )
        )
    return coherent_snapshot_rows(out, tick_minutes=tick_minutes, min_buckets=min_buckets)


def _sort_rows(rows: list[BucketRow]) -> list[BucketRow]:
    return sorted(rows, key=lambda r: (float("-inf") if r.lower_f is None else r.lower_f))


def _contains(row: BucketRow, value: float) -> bool:
    if row.lower_f is not None and value < row.lower_f:
        return False
    if row.upper_f is not None and value >= row.upper_f:
        return False
    return True


def _find_gap_event(rows: list[BucketRow], top_gap_max: float) -> GapEvent | None:
    rows = _sort_rows(rows)
    if len(rows) < 3:
        return None
    station = rows[0].station
    valid_date = rows[0].valid_date
    snapshot_ts = max(r.ts for r in rows)
    truth = float(rows[0].truth_f)
    metar_max = _metar_max_so_far(station, valid_date, snapshot_ts)
    if metar_max is None:
        return None

    observed_idx = None
    for i, row in enumerate(rows):
        if _contains(row, metar_max):
            observed_idx = i
            break
    if observed_idx is None:
        return None

    observed = rows[observed_idx]
    if observed.upper_f is None:
        return None
    top_gap = float(observed.upper_f) - metar_max
    if top_gap < -1e-9 or top_gap > top_gap_max:
        return None

    model_probs = _normalize([r.model_p for r in rows])
    market_probs = _normalize([r.market_p for r in rows])
    if model_probs is None or market_probs is None:
        return None

    above_idxs = [
        i for i, r in enumerate(rows)
        if r.lower_f is not None and r.lower_f >= float(observed.upper_f) - 1e-9
    ]
    if not above_idxs:
        return None

    next_idx = above_idxs[0]
    next_row = rows[next_idx]
    outcome_above_any = int(truth >= float(observed.upper_f) - 1e-9)
    outcome_next = int(_contains(next_row, truth))

    return GapEvent(
        station=station,
        valid_date=valid_date,
        snapshot_ts=snapshot_ts,
        local_hour=_local_hour(station, snapshot_ts),
        truth_f=truth,
        metar_max_f=metar_max,
        observed_bucket=_bucket_label(observed),
        top_gap_f=top_gap,
        outcome_above_any=outcome_above_any,
        market_p_above_any=sum(market_probs[i] for i in above_idxs),
        model_p_above_any=sum(model_probs[i] for i in above_idxs),
        outcome_next_bucket=outcome_next,
        market_p_next_bucket=market_probs[next_idx],
        model_p_next_bucket=model_probs[next_idx],
        next_bucket=_bucket_label(next_row),
        n_buckets=len(rows),
    )


def collect_events(
    days: int,
    stations: tuple[str, ...],
    top_gap_max: float,
    tick_minutes: int,
    min_buckets: int,
) -> tuple[list[GapEvent], dict]:
    rows, diag = _collect_all_valid_rows(
        days=days,
        stations=stations,
        tick_minutes=tick_minutes,
        min_buckets=min_buckets,
    )

    events = []
    skipped = defaultdict(int)
    for _key, vals in _event_rows(rows).items():
        event = _find_gap_event(vals, top_gap_max=top_gap_max)
        if event is None:
            skipped["not_conditioned"] += 1
            continue
        events.append(event)
    diag = dict(diag)
    diag["conditioned_events"] = len(events)
    diag["skipped"] = dict(skipped)
    return sorted(events, key=lambda e: (e.station, e.valid_date, e.snapshot_ts)), diag


def summarize_group(name: str, events: list[GapEvent]) -> GroupSummary | None:
    if not events:
        return None
    y_above = [e.outcome_above_any for e in events]
    market_above = [e.market_p_above_any for e in events]
    model_above = [e.model_p_above_any for e in events]
    residuals = [y - p for y, p in zip(y_above, market_above)]
    ci = _ci(residuals)

    next_events = [e for e in events if e.outcome_next_bucket is not None and e.market_p_next_bucket is not None]
    if next_events:
        y_next = [int(e.outcome_next_bucket) for e in next_events]
        p_next = [float(e.market_p_next_bucket) for e in next_events]
        next_residuals = [y - p for y, p in zip(y_next, p_next)]
        next_ci = _ci(next_residuals)
        actual_next = _mean(y_next)
        market_next = _mean(p_next)
        next_resid = _mean(next_residuals)
    else:
        actual_next = market_next = next_resid = None
        next_ci = (None, None)

    return GroupSummary(
        group=name,
        n=len(events),
        actual_above_rate=_mean(y_above),
        market_p_above=_mean(market_above),
        model_p_above=_mean(model_above),
        market_residual=_mean(residuals),
        market_residual_ci_low=ci[0],
        market_residual_ci_high=ci[1],
        market_brier_above=_brier_binary(market_above, y_above),
        model_brier_above=_brier_binary(model_above, y_above),
        model_minus_market_brier=_brier_binary(model_above, y_above) - _brier_binary(market_above, y_above),
        next_n=len(next_events),
        actual_next_rate=actual_next,
        market_p_next=market_next,
        market_next_residual=next_resid,
        market_next_residual_ci_low=next_ci[0],
        market_next_residual_ci_high=next_ci[1],
    )


def summarize(events: list[GapEvent], diag: dict, days: int, top_gap_max: float, stations: tuple[str, ...]) -> str:
    groups: list[tuple[str, list[GapEvent]]] = [("ALL", events)]
    for st in stations:
        groups.append((st, [e for e in events if e.station == st]))
    for label, min_hour in HOUR_SEGMENTS[1:]:
        groups.append((label, [e for e in events if e.local_hour >= min_hour]))
    for st in stations:
        groups.append((f"{st}_noon_plus", [e for e in events if e.station == st and e.local_hour >= 12]))

    summaries = [s for name, vals in groups if (s := summarize_group(name, vals)) is not None]

    lines = [
        f"# EXP-C2 - METAR/CLI Gap Conditional Edge - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        "Research-only. No production trading code or behavior changed.",
        "",
        "## Question",
        "",
        "When lead-0 live METAR max is close to the top of its current Kalshi bucket, "
        "does CLI settlement finish above that bucket more often than the market prices?",
        "",
        "## Setup",
        "",
        f"- stations: {', '.join(stations)}",
        f"- lookback: {days} days",
        f"- condition: `observed_bucket.upper - metar_max_so_far <= {top_gap_max:.2f}F`",
        "- selection: canonical coherent lead-0 TMAX snapshot",
        "- market/model probabilities are normalized across captured buckets before summing the above-bucket mass",
        "",
        "## Coverage",
        "",
        f"- coherent snapshot events available: {diag.get('events_with_snapshot')} / {diag.get('events_total')}",
        f"- conditioned events scored: {diag.get('conditioned_events')}",
        "",
        "## Results",
        "",
        "| group | n | actual P(above) | market P(above) | residual actual-market | 95% CI | market Brier | model Brier | model-market Brier | next n | actual P(next) | market P(next) | next residual | next 95% CI |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for s in summaries:
        ci = (
            f"[{s.market_residual_ci_low:+.3f},{s.market_residual_ci_high:+.3f}]"
            if s.market_residual_ci_low is not None else "n/a"
        )
        next_ci = (
            f"[{s.market_next_residual_ci_low:+.3f},{s.market_next_residual_ci_high:+.3f}]"
            if s.market_next_residual_ci_low is not None else "n/a"
        )
        if s.actual_next_rate is None or s.market_p_next is None or s.market_next_residual is None:
            next_actual = next_market = next_residual = "n/a"
        else:
            next_actual = f"{s.actual_next_rate:.3f}"
            next_market = f"{s.market_p_next:.3f}"
            next_residual = f"{s.market_next_residual:+.3f}"
        lines.append(
            f"| {s.group} | {s.n} | {s.actual_above_rate:.3f} | {s.market_p_above:.3f} | "
            f"{s.market_residual:+.3f} | {ci} | {s.market_brier_above:.4f} | "
            f"{s.model_brier_above:.4f} | {s.model_minus_market_brier:+.4f} | "
            f"{s.next_n} | {next_actual} | {next_market} | {next_residual} | {next_ci} |"
        )

    lines += [
        "",
        "## Interpretation Rules",
        "",
        "- Positive residual means the market underpriced above-bucket settlement in this sample.",
        "- A convincing signal needs positive residual with CI excluding 0, enough events per station, and fresh walk-forward confirmation.",
        "- If residual is not positive or sample is thin, this does not reopen trading; it is only a collection/research lead.",
    ]
    return "\n".join(lines) + "\n"


def write_csv(events: list[GapEvent], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not events:
            return
        writer = csv.DictWriter(f, fieldnames=list(asdict(events[0]).keys()))
        writer.writeheader()
        for event in events:
            writer.writerow(asdict(event))


def run(
    days: int,
    stations: tuple[str, ...],
    top_gap_max: float,
    tick_minutes: int,
    min_buckets: int,
    out_dir: Path,
) -> str:
    events, diag = collect_events(
        days=days,
        stations=stations,
        top_gap_max=top_gap_max,
        tick_minutes=tick_minutes,
        min_buckets=min_buckets,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"exp_c2_metar_cli_gap_events_{date.today()}.csv"
    md_path = out_dir / f"exp_c2_metar_cli_gap_{date.today()}.md"
    write_csv(events, csv_path)
    report = summarize(events, diag, days=days, top_gap_max=top_gap_max, stations=stations)
    md_path.write_text(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3650)
    parser.add_argument("--stations", default=",".join(DEFAULT_STATIONS))
    parser.add_argument("--top-gap", type=float, default=0.5)
    parser.add_argument("--tick-minutes", type=int, default=10)
    parser.add_argument("--min-buckets", type=int, default=3)
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    args = parser.parse_args()
    stations = tuple(s.strip().upper() for s in args.stations.split(",") if s.strip())
    print(
        run(
            days=args.days,
            stations=stations,
            top_gap_max=args.top_gap,
            tick_minutes=args.tick_minutes,
            min_buckets=args.min_buckets,
            out_dir=args.out_dir,
        )
    )


if __name__ == "__main__":
    main()
