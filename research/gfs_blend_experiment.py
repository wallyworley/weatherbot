"""EXP-B3: research-only GFS center-blend weight experiment.

RESEARCH-ONLY. Does not change production trading behavior. Supports
docs/research/DEFECT_GFS_BLEND.md and EXPERIMENT_PLAN_NEXT.md EXP-B3.

Production blends the TMAX center toward GFS at a constant 0.30 weight at lead>=1
(and as a lead-0 fallback when HRRR is unavailable):
`shift += 0.30 * (gfs_val - nbm_median)` (`models/distribution.py`). The audit
found GFS's standalone point-in-time daily-TMAX MAE is WORSE than NBM, which made
the in-code "GFS beats NBM" justification false (already corrected in code). BUT
EXP-B2 taught us point-MAE != market-relative value: a partial blend toward a
worse-but-decorrelated center can still improve the distribution. This experiment
asks the right question: **does the GFS blend improve market-relative scores vs
NBM-only, and at what weight?**

Method (mirrors EXP-B2): rebuild each event's distribution point-in-time with an
NBM-only center (`center_blend_weights={"NBM":1.0}`; bias retained; lead-0 keeps the
intraday floor, lead-1 has none), then re-apply the GFS center shift
`w*(gfs_d - nbm_median)` for a sweep of weights, and score each against the SAME
market midpoints with the canonical benchmark scoring, segmented by lead day.
GFS is bias-adjusted with the event's lead, matching production. Pre-calibrator.

Lead-1 is the primary regime (GFS is the active center augmentation there; no HRRR,
no floor). Lead-0 GFS is only a production fallback (HRRR usually wins) and is shown
for completeness.

Policies: gfs_off (w=0, the null) · gfs_0.15 · prod_0.30 (current) · gfs_0.50 · gfs_1.00.

IN-SAMPLE DIAGNOSTIC ONLY. Negative dX_vs_mkt = model better than market.
`vs_prod` = dBrier_vs_mkt(policy) - dBrier_vs_mkt(prod_0.30). Promotion requires
production-like re-score + walk-forward OOS (WEATHERBOT_PROMOTION_CRITERIA.md).

Usage:
    python -m weather_bot.research.gfs_blend_experiment --days 3650 --workers 4
"""
from __future__ import annotations

import argparse
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

WEIGHTS = {"gfs_off": 0.0, "gfs_0.15": 0.15, "prod_0.30": 0.30, "gfs_0.50": 0.50, "gfs_1.00": 1.00}
POLICIES = tuple(WEIGHTS.keys())


def _gfs_bias_adj(station: str, month: int, lead: int, val: float | None) -> float | None:
    if val is None:
        return None
    b = persistence.get_station_bias(station, "GFS", "TMAX_DAILY", month, max(lead, 0))
    if b and b.get("mean_bias_f") is not None:
        return float(val) - float(b["mean_bias_f"])
    return float(val)


def _process_event(station, vdate, lead, brows):
    ts = max(r.ts for r in brows)
    try:
        cdf = build_station_distribution(
            station, vdate, var="TMAX_DAILY", now_utc=ts, as_of=ts,
            center_blend_weights={"NBM": 1.0},
        )
    except Exception:
        cdf = None
    if cdf is None:
        return None
    base_shift = cdf.shift
    nbm_median = cdf.median()
    gfs_d = _gfs_bias_adj(station, vdate.month, lead,
                          persistence.gfs_tmax_as_of(station, vdate, ts))
    gfs_avail = gfs_d is not None
    gfs_shift = (gfs_d - nbm_median) if gfs_avail else 0.0

    scores = {}
    for pol, w in WEIGHTS.items():
        cdf.shift = base_shift + w * gfs_shift
        probs = [cdf.prob_between(r.lower_f, r.upper_f) for r in brows]
        scores[pol] = score_event([replace(r, model_p=p) for r, p in zip(brows, probs)])
    return {"lead": lead, "gfs_avail": gfs_avail, "scores": scores}


def _agg(slist: list[EventScore]) -> dict | None:
    if not slist:
        return None
    dB = [s.diff_brier for s in slist]
    return {
        "n": len(slist),
        "model_brier": _mean(s.model_brier for s in slist),
        "dBrier": _mean(dB),
        "dBrier_ci": _paired_ci(dB),
        "dRPS": _mean(s.diff_rps for s in slist),
        "dCRPS": _mean(s.diff_crps for s in slist),
        "dCenterMAE": _mean(s.diff_center_abs_error_f for s in slist),
    }


def run(days: int = 3650, workers: int = 4) -> str:
    rows, _ = collect_coherent_snapshot_rows(days, max_lead_day=1, var="TMAX_DAILY",
                                             tick_minutes=10, min_buckets=3)
    events: dict[tuple, list[BucketRow]] = defaultdict(list)
    for r in rows:
        events[(r.station, r.valid_date, r.lead_day)].append(r)

    # scores[(lead, policy)] = list[EventScore]
    scores: dict[tuple, list[EventScore]] = defaultdict(list)
    lead_n: dict[int, int] = defaultdict(int)
    gfs_avail_n: dict[int, int] = defaultdict(int)
    n_skipped = 0

    def work(item):
        (station, vdate, lead), brows = item
        return _process_event(station, vdate, lead, brows)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(work, it) for it in events.items()]
        for fut in as_completed(futs):
            res = fut.result()
            if res is None:
                n_skipped += 1
                continue
            lead = res["lead"]
            lead_n[lead] += 1
            if res["gfs_avail"]:
                gfs_avail_n[lead] += 1
            for pol in POLICIES:
                s = res["scores"][pol]
                if s is not None:
                    scores[(lead, pol)].append(s)

    lines = [
        f"# EXP-B3 — GFS Center-Blend Experiment - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        "IN-SAMPLE DIAGNOSTIC ONLY — not a production decision. NBM-only center rebuilt "
        "point-in-time (`center_blend_weights={'NBM':1.0}`; bias retained; lead-0 keeps "
        "the intraday floor; **pre-calibrator** raw CDF), GFS shift `w*(gfs_d-nbm_median)` "
        "re-applied per weight (GFS bias-adjusted with the event lead). Scored vs the SAME "
        "market mids on the canonical benchmark. Negative dX_vs_mkt = model better than "
        "market. `vs_prod` = dBrier_vs_mkt(policy) - dBrier_vs_mkt(prod_0.30). "
        "**Lead-1 is the primary GFS regime** (lead-0 GFS is only a fallback when HRRR is "
        "absent; shown for completeness).",
        "",
        f"Events: lead-0 = {lead_n.get(0,0)} (GFS available {gfs_avail_n.get(0,0)}); "
        f"lead-1 = {lead_n.get(1,0)} (GFS available {gfs_avail_n.get(1,0)}); skipped {n_skipped}.",
    ]

    for lead in (1, 0):
        lines += [
            "",
            f"## Lead {lead}" + (" (primary)" if lead == 1 else " (GFS fallback only)"),
            "",
            "| policy (GFS w) | n | model Brier | dBrier_vs_mkt | dBrier 95% CI | dRPS_vs_mkt | dCRPS_vs_mkt | dCenterMAE_vs_mkt | vs_prod (Brier) |",
            "|---|---:|---:|---:|---|---:|---:|---:|---:|",
        ]
        prod = _agg(scores[(lead, "prod_0.30")])
        for pol in POLICIES:
            r = _agg(scores[(lead, pol)])
            if r is None:
                lines.append(f"| {pol} | 0 | | | | | | | |")
                continue
            ci = r["dBrier_ci"]
            ci_txt = f"[{ci[0]:+.4f}, {ci[1]:+.4f}]" if ci[0] is not None else ""
            vs_prod = (r["dBrier"] - prod["dBrier"]) if prod else 0.0
            lines.append(
                f"| {pol} | {r['n']} | {r['model_brier']:.4f} | {r['dBrier']:+.4f} | {ci_txt} | "
                f"{r['dRPS']:+.4f} | {r['dCRPS']:+.3f} | {r['dCenterMAE']:+.2f} | {vs_prod:+.4f} |"
            )

    lines += [
        "",
        "Reading: `prod_0.30` is current behavior. The key question is whether any GFS "
        "weight beats `gfs_off` (NBM-only) at lead-1 — if so, the GFS blend is net-helpful "
        "via decorrelation (as HRRR was in EXP-B2), independent of the false standalone-MAE "
        "premise; if `gfs_off` wins, the blend should be demoted. All deltas are IN-SAMPLE "
        "and pre-calibrator; before any production change a candidate needs a production-like "
        "re-score (calibrator) and walk-forward OOS validation, no in-sample weight tuning "
        "(WEATHERBOT_PROMOTION_CRITERIA.md). ECMWF (also in det_forecast) is a possible "
        "research-only decorrelation follow-on, not tested here.",
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
