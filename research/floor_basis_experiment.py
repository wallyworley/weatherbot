"""EXP-B1: research-only alternate intraday-floor basis experiment.

RESEARCH-ONLY. Does not change production trading behavior. Supports
docs/research/DEFECT_METAR_CLI_FLOOR.md and EXPERIMENT_PLAN_NEXT.md EXP-B1.

Production conditions same-day TMAX on a HARD floor at the max METAR temperature
observed so far (`models/distribution.py` -> cdf.floor). Kalshi settles on CLI.
METAR peaks over-read CLI on ~20% of lead-0 events, zeroing the winning bucket on
~2%. This harness rebuilds each lead-0 distribution point-in-time (as_of = the
event's coherent-snapshot timestamp, no future data) and re-scores the model under
several floor policies against the same market midpoints, using the canonical
benchmark scoring functions. Only the floor changes; the production center
(bias + HRRR/GFS blend) is held fixed so the floor effect is isolated.

Policies
  prod_hard   : production hard floor = metar_max-so-far (baseline to beat)
  floor_off   : no intraday floor
  soft_w0.50  : blend 0.50*floored + 0.50*unfloored bucket probs (cap injected confidence)
  soft_w0.25  : blend 0.25*floored + 0.75*unfloored
  minus_0.5   : floor = metar_max - 0.5 F   (fixed buffer; IN-SAMPLE diagnostic)
  minus_1.0   : floor = metar_max - 1.0 F   (fixed buffer; IN-SAMPLE diagnostic)
  minus_wf    : floor = metar_max - delta, delta = trailing p85 of prior-day
                (full-day metar_max - CLI) over-read for the station (WALK-FORWARD,
                no leakage) -> the promotion candidate

Negative diff vs market = model better than market. The promotion-relevant
columns are `dBrier_vs_mkt` / `dRPS_vs_mkt` (is the gap to market reduced?) and
`vs_prod` (is it better than the current production floor?). Fixed-buffer and
soft-weight policies are in-sample and are diagnostics only.

Usage:
    python -m weather_bot.research.floor_basis_experiment --days 3650 --workers 4
"""
from __future__ import annotations

import argparse
import statistics as st
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import date, datetime, timezone

from weather_bot.data import persistence
from weather_bot.models.distribution import build_station_distribution
from weather_bot.research.market_relative_center_benchmark import (
    BucketRow,
    EventScore,
    _mean,
    _paired_ci,
    collect_coherent_snapshot_rows,
    score_event,
)

POLICIES = (
    "prod_hard",
    "floor_off",
    "soft_w0.50",
    "soft_w0.25",
    "minus_0.5",
    "minus_1.0",
    "minus_wf",
)


def _overread_table(days: int) -> dict[str, list[tuple[date, float]]]:
    """Per-station sorted [(local_date, full_day_metar_max - cli_tmax)] for prior-day delta."""
    sql = """
    WITH m AS (
        SELECT mo.station,
               (mo.obs_time AT TIME ZONE st.tz)::date AS local_date,
               MAX(mo.temp_f) AS metar_max
          FROM metar_obs mo JOIN stations st ON st.code = mo.station
         WHERE mo.temp_f IS NOT NULL
           AND mo.obs_time >= CURRENT_DATE - (%(days)s || ' days')::interval
         GROUP BY 1, 2
    )
    SELECT m.station, m.local_date, (m.metar_max - co.tmax_f) AS over_read
      FROM m JOIN cli_obs co ON co.station = m.station AND co.local_date = m.local_date
     WHERE co.tmax_f IS NOT NULL
     ORDER BY m.station, m.local_date
    """
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, {"days": days})
        for r in cur.fetchall():
            out[r["station"]].append((r["local_date"], float(r["over_read"])))
    return out


def _wf_delta(table: dict, station: str, target: date,
              lookback_days: int = 60, min_samples: int = 10, q: float = 0.85) -> float:
    """Walk-forward floor buffer = max(0, q-quantile of PRIOR-day over-read)."""
    series = table.get(station, [])
    prior = [
        ov for (d, ov) in series
        if d < target and (target - d).days <= lookback_days
    ]
    if len(prior) < min_samples:
        return 0.0
    prior_sorted = sorted(prior)
    idx = min(len(prior_sorted) - 1, max(0, int(round(q * (len(prior_sorted) - 1)))))
    return max(0.0, prior_sorted[idx])


def _model_probs(cdf, brows: list[BucketRow], floor) -> list[float]:
    cdf.floor = floor
    return [cdf.prob_between(r.lower_f, r.upper_f) for r in brows]


def _score_policy(brows: list[BucketRow], model_probs: list[float]) -> EventScore | None:
    rows = [replace(r, model_p=p) for r, p in zip(brows, model_probs)]
    return score_event(rows)


def _winner_norm(model_probs: list[float], brows: list[BucketRow]) -> float | None:
    win_idx = [i for i, r in enumerate(brows) if r.yes_win == 1]
    if len(win_idx) != 1:
        return None
    total = sum(min(1.0, max(0.0, p)) for p in model_probs) or 1.0
    return min(1.0, max(0.0, model_probs[win_idx[0]])) / total


def _process_event(station, vdate, brows, overread):
    ts = max(r.ts for r in brows)
    try:
        cdf = build_station_distribution(station, vdate, var="TMAX_DAILY", now_utc=ts, as_of=ts)
    except Exception:
        cdf = None
    if cdf is None:
        return None
    base_floor = cdf.floor  # = metar_max-so-far as-of ts (production)
    floored = _model_probs(cdf, brows, base_floor)
    unfloored = _model_probs(cdf, brows, None)
    wf = _wf_delta(overread, station, vdate)

    probs_by_policy = {
        "prod_hard": floored,
        "floor_off": unfloored,
        "soft_w0.50": [0.50 * f + 0.50 * u for f, u in zip(floored, unfloored)],
        "soft_w0.25": [0.25 * f + 0.75 * u for f, u in zip(floored, unfloored)],
        "minus_0.5": _model_probs(cdf, brows, None if base_floor is None else base_floor - 0.5),
        "minus_1.0": _model_probs(cdf, brows, None if base_floor is None else base_floor - 1.0),
        "minus_wf": _model_probs(cdf, brows, None if base_floor is None else base_floor - wf),
    }
    result = {"wf_delta": wf, "base_floor": base_floor, "scores": {}, "winner": {}}
    for pol, probs in probs_by_policy.items():
        result["scores"][pol] = _score_policy(brows, probs)
        result["winner"][pol] = _winner_norm(probs, brows)
    return result


def run(days: int = 3650, workers: int = 4) -> str:
    rows, _ = collect_coherent_snapshot_rows(days, max_lead_day=0, var="TMAX_DAILY",
                                             tick_minutes=10, min_buckets=3)
    events: dict[tuple, list[BucketRow]] = defaultdict(list)
    for r in rows:
        events[(r.station, r.valid_date)].append(r)

    overread = _overread_table(days)

    scores: dict[str, list[EventScore]] = {p: [] for p in POLICIES}
    winner_zeroed: dict[str, int] = {p: 0 for p in POLICIES}
    n_events = 0
    n_skipped = 0
    wf_deltas: list[float] = []

    def work(item):
        (station, vdate), brows = item
        return _process_event(station, vdate, brows, overread)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(work, it) for it in events.items()]
        for fut in as_completed(futs):
            res = fut.result()
            if res is None:
                n_skipped += 1
                continue
            n_events += 1
            wf_deltas.append(res["wf_delta"])
            for pol in POLICIES:
                s = res["scores"][pol]
                if s is not None:
                    scores[pol].append(s)
                w = res["winner"][pol]
                if w is not None and w < 0.05:
                    winner_zeroed[pol] += 1

    def agg(pol):
        ss = scores[pol]
        if not ss:
            return None
        dB = [s.diff_brier for s in ss]
        dR = [s.diff_rps for s in ss]
        dC = [s.diff_crps for s in ss]
        return {
            "n": len(ss),
            "model_brier": _mean(s.model_brier for s in ss),
            "market_brier": _mean(s.market_brier for s in ss),
            "dBrier_vs_mkt": _mean(dB),
            "dBrier_ci": _paired_ci(dB),
            "dRPS_vs_mkt": _mean(dR),
            "dCRPS_vs_mkt": _mean(dC),
            "dCenterMAE_vs_mkt": _mean(s.diff_center_abs_error_f for s in ss),
            "winner_zeroed": winner_zeroed[pol],
        }

    a = {p: agg(p) for p in POLICIES}
    prod = a["prod_hard"]

    lines = [
        f"# EXP-B1 — METAR/CLI Floor Basis Experiment - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Lead-0 TMAX event groups evaluated: {n_events} ({n_skipped} skipped, no PIT "
        f"rebuild). Per-policy `n` is the subset that produced a score; a group is "
        f"unscoreable when the model's normalized mass on the captured ladder is "
        f"undefined (e.g., the hard floor truncates the whole captured ladder to zero "
        f"— itself an instance of the defect the soft floor avoids). "
        f"Walk-forward delta: median {st.median(wf_deltas):.2f} F "
        f"(p90 {sorted(wf_deltas)[int(0.9*(len(wf_deltas)-1))]:.2f} F) over prior-day over-read."
        if wf_deltas else f"Lead-0 TMAX event groups: {n_events} ({n_skipped} skipped).",
        "",
        "IN-SAMPLE DIAGNOSTIC ONLY — not a production decision. Floor changes only; "
        "production center (bias + HRRR/GFS) held fixed; **pre-calibrator** raw CDF "
        "probabilities (isolates the floor; a production-facing check must re-run the "
        "full production-like path, calibrator included, on the canonical benchmark). "
        "as_of = coherent-snapshot ts (no future data). Negative dX_vs_mkt = model better "
        "than market. `vs_prod` = dBrier_vs_mkt(policy) - dBrier_vs_mkt(prod_hard); "
        "negative = smaller market gap than the production floor. "
        "`winner<5%` = count of events where the winning bucket received <5% of the "
        "normalized model mass (a soft-starvation proxy; distinct from the literal "
        "floor-truncation count in DEFECT_METAR_CLI_FLOOR.md §3).",
        "",
        "| policy | n | model Brier | dBrier_vs_mkt | dBrier 95% CI | dRPS_vs_mkt | dCRPS_vs_mkt | dCenterMAE_vs_mkt | winner<5% | vs_prod (Brier) |",
        "|---|---:|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for pol in POLICIES:
        r = a[pol]
        if r is None:
            lines.append(f"| {pol} | 0 | | | | | | | | |")
            continue
        ci = r["dBrier_ci"]
        ci_txt = f"[{ci[0]:+.4f}, {ci[1]:+.4f}]" if ci[0] is not None else ""
        vs_prod = r["dBrier_vs_mkt"] - prod["dBrier_vs_mkt"] if prod else 0.0
        lines.append(
            f"| {pol} | {r['n']} | {r['model_brier']:.4f} | {r['dBrier_vs_mkt']:+.4f} | {ci_txt} | "
            f"{r['dRPS_vs_mkt']:+.4f} | {r['dCRPS_vs_mkt']:+.3f} | {r['dCenterMAE_vs_mkt']:+.2f} | "
            f"{r['winner_zeroed']} | {vs_prod:+.4f} |"
        )
    lines += [
        "",
        "Reading: `prod_hard` is the current production floor. A policy is an in-sample",
        "damage-reduction *candidate* (NOT a validated fix) only if `vs_prod (Brier)` is",
        "negative (smaller market gap) AND it does not worsen dRPS/dCRPS. All deltas here",
        "are IN-SAMPLE to this historical window; none is promotion evidence. Before any",
        "production-facing decision a candidate must be (a) implemented behind a research",
        "flag with the current hard floor as default, (b) re-scored through the full",
        "production-like path (calibrator included), and (c) validated WALK-FORWARD on",
        "fresh lead-0 station-days via the canonical benchmark, without tuning the soft",
        "weight in-sample. Promotion requires WEATHERBOT_PROMOTION_CRITERIA.md.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=3650)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out", type=str, default=None)
    args = p.parse_args()
    md = run(days=args.days, workers=args.workers)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
