"""Market-relative distribution-center benchmark.

Scores WeatherBot's stored fair probabilities against Kalshi market-implied
probabilities on the same station/date/lead-day bucket set. This is a research
report only; it does not import or modify trading logic.

Usage:
    python -m weather_bot.research.market_relative_center_benchmark --days 60
    python -m weather_bot.jobs.market_relative_center_benchmark_report --days 60
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


_REALIZED_SQL = """
COALESCE(
    NULLIF(km.payload->>'expiration_value','')::float,
    CASE WHEN km.var = 'TMAX_DAILY' THEN co.tmax_f
         WHEN km.var = 'TMIN_DAILY' THEN co.tmin_f END,
    CASE WHEN km.var = 'TMAX_DAILY' THEN d.tmax_f
         WHEN km.var = 'TMIN_DAILY' THEN d.tmin_f END
)
"""

_YES_WIN_SQL = """
CASE
    WHEN {realized} IS NULL THEN NULL
    WHEN km.lower_f IS NOT NULL AND {realized} <  km.lower_f THEN 0
    WHEN km.upper_f IS NOT NULL AND {realized} >= km.upper_f THEN 0
    ELSE 1
END
""".format(realized=_REALIZED_SQL)


@dataclass(frozen=True)
class BucketRow:
    station: str
    valid_date: date
    var: str
    lead_day: int
    ticker: str
    ts: datetime
    lower_f: float | None
    upper_f: float | None
    truth_f: float
    yes_win: int
    model_p: float
    market_p: float


@dataclass(frozen=True)
class EventScore:
    station: str
    valid_date: date
    var: str
    lead_day: int
    n_buckets: int
    truth_f: float
    model_brier: float
    market_brier: float
    diff_brier: float
    model_rps: float
    market_rps: float
    diff_rps: float
    model_crps: float
    market_crps: float
    diff_crps: float
    model_center_f: float
    market_center_f: float
    model_center_abs_error_f: float
    market_center_abs_error_f: float
    diff_center_abs_error_f: float
    model_prob_sum: float
    market_prob_sum: float


@dataclass(frozen=True)
class GroupSummary:
    station: str
    lead_day: int
    n_events: int
    avg_buckets: float
    model_brier: float
    market_brier: float
    diff_brier: float
    diff_brier_ci_low: float | None
    diff_brier_ci_high: float | None
    brier_skill_vs_market: float | None
    model_rps: float
    market_rps: float
    diff_rps: float
    diff_rps_ci_low: float | None
    diff_rps_ci_high: float | None
    rps_skill_vs_market: float | None
    model_crps: float
    market_crps: float
    diff_crps: float
    diff_crps_ci_low: float | None
    diff_crps_ci_high: float | None
    crps_skill_vs_market: float | None
    model_center_mae_f: float
    market_center_mae_f: float
    diff_center_mae_f: float


def _mean(xs: Iterable[float]) -> float:
    return statistics.fmean(list(xs))


def _paired_ci(diffs: list[float]) -> tuple[float | None, float | None]:
    if len(diffs) < 2:
        return None, None
    se = statistics.stdev(diffs) / math.sqrt(len(diffs))
    center = statistics.fmean(diffs)
    return center - 1.96 * se, center + 1.96 * se


def _skill(model_score: float, market_score: float) -> float | None:
    if market_score <= 0:
        return None
    return 1.0 - model_score / market_score


def _clamp_prob(p: float) -> float:
    return min(1.0, max(0.0, float(p)))


def _normalize(ps: list[float]) -> list[float] | None:
    clean = [_clamp_prob(p) for p in ps]
    total = sum(clean)
    if total <= 0:
        return None
    return [p / total for p in clean]


def _bucket_representative(row: BucketRow, default_width: float) -> float | None:
    if row.lower_f is not None and row.upper_f is not None:
        return (float(row.lower_f) + float(row.upper_f)) / 2.0
    if row.lower_f is None and row.upper_f is not None:
        return float(row.upper_f) - default_width / 2.0
    if row.lower_f is not None and row.upper_f is None:
        return float(row.lower_f) + default_width / 2.0
    return None


def _typical_bucket_width(rows: list[BucketRow]) -> float:
    widths = [
        float(r.upper_f) - float(r.lower_f)
        for r in rows
        if r.lower_f is not None and r.upper_f is not None and r.upper_f > r.lower_f
    ]
    return statistics.median(widths) if widths else 1.0


def _brier(probs: list[float], winner_idx: int) -> float:
    return statistics.fmean((p - (1.0 if i == winner_idx else 0.0)) ** 2 for i, p in enumerate(probs))


def _rps(probs: list[float], winner_idx: int) -> float:
    if len(probs) < 2:
        return 0.0
    cum_f = 0.0
    cum_o = 0.0
    total = 0.0
    for i, p in enumerate(probs):
        cum_f += p
        cum_o += 1.0 if i == winner_idx else 0.0
        total += (cum_f - cum_o) ** 2
    return total / (len(probs) - 1)


def _crps_discrete(values: list[float], probs: list[float], truth: float) -> float:
    """Discrete CRPS in degrees F for a weighted empirical distribution."""
    first = sum(p * abs(x - truth) for x, p in zip(values, probs))
    second = 0.0
    for xi, pi in zip(values, probs):
        for xj, pj in zip(values, probs):
            second += pi * pj * abs(xi - xj)
    return first - 0.5 * second


def _expected_value(values: list[float], probs: list[float]) -> float:
    return sum(x * p for x, p in zip(values, probs))


def collect_bucket_rows(days: int, max_lead_day: int, var: str) -> list[BucketRow]:
    """Return one latest signal per bucket ticker per station/date/lead day."""
    sql = f"""
    WITH scored AS (
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
          LEFT JOIN cli_obs co
                 ON co.station = km.station AND co.local_date = km.valid_date
          LEFT JOIN daily_obs d
                 ON d.station = km.station AND d.local_date = km.valid_date
         WHERE km.valid_date >= CURRENT_DATE - (%(days)s || ' days')::interval
           AND km.valid_date < CURRENT_DATE
           AND km.var = %(var)s
           AND s.fair_prob IS NOT NULL
           AND s.market_ask IS NOT NULL
           AND s.market_bid IS NOT NULL
    ),
    ranked AS (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY ticker, lead_day
                   ORDER BY ts DESC
               ) AS rn
          FROM scored
         WHERE truth_f IS NOT NULL
           AND yes_win IS NOT NULL
           AND model_p BETWEEN 0 AND 1
           AND market_p BETWEEN 0 AND 1
           AND lead_day BETWEEN 0 AND %(max_lead_day)s
    )
    SELECT *
      FROM ranked
     WHERE rn = 1
     ORDER BY station, valid_date, var, lead_day, lower_f NULLS FIRST, upper_f NULLS LAST
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, {"days": days, "max_lead_day": max_lead_day, "var": var})
        rows = cur.fetchall()

    return [
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
            model_p=float(r["model_p"]),
            market_p=float(r["market_p"]),
        )
        for r in rows
    ]


def score_event(rows: list[BucketRow]) -> EventScore | None:
    rows = sorted(rows, key=lambda r: (float("-inf") if r.lower_f is None else r.lower_f))
    if len(rows) < 3:
        return None
    winners = [i for i, r in enumerate(rows) if r.yes_win == 1]
    if len(winners) != 1:
        return None
    width = _typical_bucket_width(rows)
    values = [_bucket_representative(r, width) for r in rows]
    if any(v is None for v in values):
        return None
    values_f = [float(v) for v in values if v is not None]
    model_probs = _normalize([r.model_p for r in rows])
    market_probs = _normalize([r.market_p for r in rows])
    if model_probs is None or market_probs is None:
        return None

    winner_idx = winners[0]
    truth = float(rows[0].truth_f)
    model_brier = _brier(model_probs, winner_idx)
    market_brier = _brier(market_probs, winner_idx)
    model_rps = _rps(model_probs, winner_idx)
    market_rps = _rps(market_probs, winner_idx)
    model_crps = _crps_discrete(values_f, model_probs, truth)
    market_crps = _crps_discrete(values_f, market_probs, truth)
    model_center = _expected_value(values_f, model_probs)
    market_center = _expected_value(values_f, market_probs)

    return EventScore(
        station=rows[0].station,
        valid_date=rows[0].valid_date,
        var=rows[0].var,
        lead_day=rows[0].lead_day,
        n_buckets=len(rows),
        truth_f=truth,
        model_brier=model_brier,
        market_brier=market_brier,
        diff_brier=model_brier - market_brier,
        model_rps=model_rps,
        market_rps=market_rps,
        diff_rps=model_rps - market_rps,
        model_crps=model_crps,
        market_crps=market_crps,
        diff_crps=model_crps - market_crps,
        model_center_f=model_center,
        market_center_f=market_center,
        model_center_abs_error_f=abs(model_center - truth),
        market_center_abs_error_f=abs(market_center - truth),
        diff_center_abs_error_f=abs(model_center - truth) - abs(market_center - truth),
        model_prob_sum=sum(_clamp_prob(r.model_p) for r in rows),
        market_prob_sum=sum(_clamp_prob(r.market_p) for r in rows),
    )


def score_events(rows: list[BucketRow]) -> list[EventScore]:
    groups: dict[tuple[str, date, str, int], list[BucketRow]] = defaultdict(list)
    for row in rows:
        groups[(row.station, row.valid_date, row.var, row.lead_day)].append(row)
    scores = []
    for vals in groups.values():
        score = score_event(vals)
        if score is not None:
            scores.append(score)
    return sorted(scores, key=lambda s: (s.station, s.lead_day, s.valid_date))


def summarize_group(station: str, lead_day: int, scores: list[EventScore]) -> GroupSummary:
    diff_brier = [s.diff_brier for s in scores]
    diff_rps = [s.diff_rps for s in scores]
    diff_crps = [s.diff_crps for s in scores]
    brier_ci = _paired_ci(diff_brier)
    rps_ci = _paired_ci(diff_rps)
    crps_ci = _paired_ci(diff_crps)
    model_brier = _mean(s.model_brier for s in scores)
    market_brier = _mean(s.market_brier for s in scores)
    model_rps = _mean(s.model_rps for s in scores)
    market_rps = _mean(s.market_rps for s in scores)
    model_crps = _mean(s.model_crps for s in scores)
    market_crps = _mean(s.market_crps for s in scores)
    model_center_mae = _mean(s.model_center_abs_error_f for s in scores)
    market_center_mae = _mean(s.market_center_abs_error_f for s in scores)
    return GroupSummary(
        station=station,
        lead_day=lead_day,
        n_events=len(scores),
        avg_buckets=_mean(s.n_buckets for s in scores),
        model_brier=model_brier,
        market_brier=market_brier,
        diff_brier=_mean(diff_brier),
        diff_brier_ci_low=brier_ci[0],
        diff_brier_ci_high=brier_ci[1],
        brier_skill_vs_market=_skill(model_brier, market_brier),
        model_rps=model_rps,
        market_rps=market_rps,
        diff_rps=_mean(diff_rps),
        diff_rps_ci_low=rps_ci[0],
        diff_rps_ci_high=rps_ci[1],
        rps_skill_vs_market=_skill(model_rps, market_rps),
        model_crps=model_crps,
        market_crps=market_crps,
        diff_crps=_mean(diff_crps),
        diff_crps_ci_low=crps_ci[0],
        diff_crps_ci_high=crps_ci[1],
        crps_skill_vs_market=_skill(model_crps, market_crps),
        model_center_mae_f=model_center_mae,
        market_center_mae_f=market_center_mae,
        diff_center_mae_f=model_center_mae - market_center_mae,
    )


def summarize(scores: list[EventScore]) -> list[GroupSummary]:
    groups: dict[tuple[str, int], list[EventScore]] = defaultdict(list)
    for score in scores:
        groups[(score.station, score.lead_day)].append(score)
    return [
        summarize_group(station, lead_day, vals)
        for (station, lead_day), vals in sorted(groups.items())
    ]


def _fmt(value: float | None, digits: int = 4, signed: bool = False) -> str:
    if value is None or not math.isfinite(value):
        return ""
    sign = "+" if signed else ""
    return f"{value:{sign}.{digits}f}"


def _evidence_statement(label: str, summaries: list[GroupSummary]) -> str:
    if not summaries:
        return f"{label}: no scored events."
    total_events = sum(s.n_events for s in summaries)
    weighted_diff_brier = sum(s.diff_brier * s.n_events for s in summaries) / total_events
    weighted_diff_rps = sum(s.diff_rps * s.n_events for s in summaries) / total_events
    weighted_diff_crps = sum(s.diff_crps * s.n_events for s in summaries) / total_events
    better = sum(
        1
        for s in summaries
        if s.n_events >= 5 and s.diff_brier < 0 and s.diff_rps < 0 and s.diff_crps < 0
    )
    worse = sum(
        1
        for s in summaries
        if s.n_events >= 5 and s.diff_brier > 0 and s.diff_rps > 0 and s.diff_crps > 0
    )
    if weighted_diff_brier < 0 and weighted_diff_rps < 0 and weighted_diff_crps < 0:
        verdict = "WeatherBot lower on all three weighted score deltas"
    elif weighted_diff_brier > 0 and weighted_diff_rps > 0 and weighted_diff_crps > 0:
        verdict = "market lower on all three weighted score deltas"
    else:
        verdict = "mixed weighted score deltas"
    return (
        f"{label}: {verdict}; weighted deltas "
        f"Brier={weighted_diff_brier:+.4f}, RPS={weighted_diff_rps:+.4f}, "
        f"CRPS={weighted_diff_crps:+.3f} F. "
        f"Station/lead groups with all three WeatherBot-better deltas: {better}; "
        f"market-better groups: {worse}."
    )


def render_markdown(
    scores: list[EventScore],
    summaries: list[GroupSummary],
    days: int,
    max_lead_day: int,
    var: str,
) -> str:
    lead_groups: dict[int, list[EventScore]] = defaultdict(list)
    for score in scores:
        lead_groups[score.lead_day].append(score)
    lead_summaries = [
        summarize_group("ALL", lead, vals)
        for lead, vals in sorted(lead_groups.items())
    ]

    lines = [
        f"# Market-Relative Forecast Center Benchmark - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Window: last {days} completed valid dates; variable `{var}`; lead days 0-{max_lead_day}.",
        "",
        "Method: one latest stored signal per bucket ticker per station/date/lead day. "
        "WeatherBot fair probabilities and market midpoints are normalized over the same captured "
        "ordered bucket set before scoring. Brier is mean bucket Brier, RPS is normalized ranked "
        "probability score, and CRPS is a discrete bucket-center approximation in degrees F. "
        "For all deltas, negative means WeatherBot scored better than the market.",
        "",
        "## Evidence Statement",
        "",
        _evidence_statement("All scored station/lead groups", summaries),
        "",
        "## By Lead Day",
        "",
        "| lead | events | buckets | model Brier | market Brier | diff | model RPS | market RPS | diff | model CRPS | market CRPS | diff | model center MAE | market center MAE | diff |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in lead_summaries:
        lines.append(
            f"| {s.lead_day} | {s.n_events} | {s.avg_buckets:.1f} | "
            f"{s.model_brier:.4f} | {s.market_brier:.4f} | {s.diff_brier:+.4f} | "
            f"{s.model_rps:.4f} | {s.market_rps:.4f} | {s.diff_rps:+.4f} | "
            f"{s.model_crps:.3f} | {s.market_crps:.3f} | {s.diff_crps:+.3f} | "
            f"{s.model_center_mae_f:.2f} | {s.market_center_mae_f:.2f} | {s.diff_center_mae_f:+.2f} |"
        )

    lines.extend([
        "",
        "## By Station And Lead Day",
        "",
        "| station | lead | events | buckets | model Brier | market Brier | diff | model RPS | market RPS | diff | model CRPS | market CRPS | diff | CRPS diff 95% CI | model center MAE | market center MAE | diff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|",
    ])
    for s in summaries:
        ci = (
            f"[{_fmt(s.diff_crps_ci_low, 3, signed=True)}, "
            f"{_fmt(s.diff_crps_ci_high, 3, signed=True)}]"
            if s.diff_crps_ci_low is not None else ""
        )
        lines.append(
            f"| {s.station} | {s.lead_day} | {s.n_events} | {s.avg_buckets:.1f} | "
            f"{s.model_brier:.4f} | {s.market_brier:.4f} | {s.diff_brier:+.4f} | "
            f"{s.model_rps:.4f} | {s.market_rps:.4f} | {s.diff_rps:+.4f} | "
            f"{s.model_crps:.3f} | {s.market_crps:.3f} | {s.diff_crps:+.3f} | {ci} | "
            f"{s.model_center_mae_f:.2f} | {s.market_center_mae_f:.2f} | {s.diff_center_mae_f:+.2f} |"
        )

    lines.extend([
        "",
        "## Evidence Rule",
        "",
        "This report supports a forecast information advantage only where WeatherBot's paired deltas are negative "
        "against the market on the same station/date/lead-day events, with enough repeated events to make the "
        "difference stable. Confidence intervals are normal-approximation paired intervals over event-level deltas.",
    ])
    return "\n".join(lines) + "\n"


def write_csvs(scores: list[EventScore], summaries: list[GroupSummary], out_dir: Path, stem: str) -> tuple[Path, Path]:
    event_csv = out_dir / f"{stem}_events.csv"
    summary_csv = out_dir / f"{stem}_summary.csv"
    with event_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(scores[0]).keys()) if scores else [])
        if scores:
            writer.writeheader()
            for score in scores:
                writer.writerow(asdict(score))
    with summary_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(summaries[0]).keys()) if summaries else [])
        if summaries:
            writer.writeheader()
            for summary in summaries:
                writer.writerow(asdict(summary))
    return event_csv, summary_csv


def run(
    days: int = 60,
    max_lead_day: int = 3,
    var: str = "TMAX_DAILY",
    out_dir: Path = Path("research/reports"),
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = collect_bucket_rows(days=days, max_lead_day=max_lead_day, var=var)
    scores = score_events(rows)
    summaries = summarize(scores)
    stem = f"market_relative_center_benchmark_{date.today()}"
    report_path = out_dir / f"{stem}.md"
    event_csv, summary_csv = write_csvs(scores, summaries, out_dir, stem)
    report_path.write_text(render_markdown(scores, summaries, days, max_lead_day, var))
    return {
        "bucket_rows": len(rows),
        "events": len(scores),
        "summary_rows": len(summaries),
        "report_path": str(report_path),
        "event_csv": str(event_csv),
        "summary_csv": str(summary_csv),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--max-lead-day", type=int, default=3)
    parser.add_argument("--var", choices=("TMAX_DAILY", "TMIN_DAILY"), default="TMAX_DAILY")
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    args = parser.parse_args()
    result = run(days=args.days, max_lead_day=args.max_lead_day, var=args.var, out_dir=args.out_dir)
    print(Path(result["report_path"]).read_text())


if __name__ == "__main__":
    main()
