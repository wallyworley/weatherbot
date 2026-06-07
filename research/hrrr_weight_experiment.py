"""EXP-B2: research-only HRRR late-day weight-curve experiment.

RESEARCH-ONLY. Does not change production trading behavior. Supports
docs/research/DEFECT_HRRR_WEIGHT_CURVE.md and EXPERIMENT_PLAN_NEXT.md EXP-B2.

Production blends same-day (lead-0) TMAX toward HRRR with a weight curve that
reaches 0.9 at 15:00 and 0.95 by 18:00 (`models/distribution.py:_hrrr_blend_weight`),
shifting the center by `w * (hrrr_val - nbm_median)`. The audit found HRRR's
point-in-time daily-TMAX MAE is WORSE than NBM at exactly the hours the curve
trusts it most (13-16h).

This harness rebuilds each lead-0 distribution point-in-time with an NBM-only
center (`center_blend_weights={"NBM":1.0}`, so production HRRR/GFS policy is
bypassed; bias + intraday floor retained), then re-applies the HRRR/GFS center
shift under several weight policies, faithfully mirroring the production formula
(`shift += w*(hrrr_val - nbm_median)`, GFS fallback 0.30 when HRRR weight is 0).
Each policy is scored against the SAME market midpoints with the canonical
benchmark scoring, overall and BY LOCAL HOUR BAND.

Policies
  prod_curve : production HRRR curve + 0.30 GFS fallback (current behavior; baseline)
  w0_nbm     : NBM-only center (HRRR off, no GFS)            -> the w=0 null
  w0_gfs     : NBM + 0.30*GFS, HRRR off (replace HRRR with GFS fallback)
  flat_0.30  : HRRR-only, constant w=0.30
  cap_0.50   : production curve capped at w<=0.50 (mirror prod, cap the late overweight)

IN-SAMPLE DIAGNOSTIC ONLY (pre-calibrator raw CDF). Negative dX_vs_mkt = model
better than market. `vs_prod` = dBrier_vs_mkt(policy) - dBrier_vs_mkt(prod_curve).
Promotion requires production-like re-score + walk-forward OOS validation
(WEATHERBOT_PROMOTION_CRITERIA.md); a fitted by-hour curve is deferred (high
overfit risk) — these fixed policies bracket it.

Usage:
    python -m weather_bot.research.hrrr_weight_experiment --days 3650 --workers 4
"""
from __future__ import annotations

import argparse
import statistics as st
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import date, datetime, timezone

import pytz

from weather_bot.config import STATIONS
from weather_bot.data import persistence
from weather_bot.models.distribution import _hrrr_blend_weight, build_station_distribution
from weather_bot.research.market_relative_center_benchmark import (
    BucketRow,
    EventScore,
    _mean,
    _paired_ci,
    collect_coherent_snapshot_rows,
    score_event,
)

POLICIES = ("prod_curve", "w0_nbm", "w0_gfs", "flat_0.30", "cap_0.50")
_GFS_FALLBACK_W = 0.30


def _hour_band(h: int) -> str:
    if h <= 12:
        return "<=12"
    if h <= 14:
        return "13-14"
    if h <= 16:
        return "15-16"
    return ">=17"


BANDS = ("<=12", "13-14", "15-16", ">=17")


def _bias_adj(station: str, model: str, month: int, val: float | None) -> float | None:
    if val is None:
        return None
    b = persistence.get_station_bias(station, model, "TMAX_DAILY", month, 0)
    if b and b.get("mean_bias_f") is not None:
        return float(val) - float(b["mean_bias_f"])
    return float(val)


def _shift_add(policy, prod_w, hrrr_d, gfs_d, nbm_median):
    """Center shift to add on top of the NBM-only center, per policy."""
    hrrr_shift = (hrrr_d - nbm_median) if hrrr_d is not None else None
    gfs_shift = (gfs_d - nbm_median) if gfs_d is not None else None
    if policy == "prod_curve":
        if hrrr_shift is not None and prod_w > 0:
            return prod_w * hrrr_shift
        return _GFS_FALLBACK_W * gfs_shift if gfs_shift is not None else 0.0
    if policy == "w0_nbm":
        return 0.0
    if policy == "w0_gfs":
        return _GFS_FALLBACK_W * gfs_shift if gfs_shift is not None else 0.0
    if policy == "flat_0.30":
        return 0.30 * hrrr_shift if hrrr_shift is not None else 0.0
    if policy == "cap_0.50":
        w = min(prod_w, 0.50)
        if hrrr_shift is not None and w > 0:
            return w * hrrr_shift
        return _GFS_FALLBACK_W * gfs_shift if gfs_shift is not None else 0.0
    return 0.0


def _process_event(station, vdate, brows):
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
    month = vdate.month
    hrrr_d = _bias_adj(station, "HRRR", month, persistence.hrrr_tmax_as_of(station, vdate, ts))
    gfs_d = _bias_adj(station, "GFS", month, persistence.gfs_tmax_as_of(station, vdate, ts))
    hour_local = ts.astimezone(pytz.timezone(STATIONS[station].tz)).hour
    prod_w = _hrrr_blend_weight(hour_local)
    band = _hour_band(hour_local)

    scores = {}
    for pol in POLICIES:
        cdf.shift = base_shift + _shift_add(pol, prod_w, hrrr_d, gfs_d, nbm_median)
        probs = [cdf.prob_between(r.lower_f, r.upper_f) for r in brows]
        scores[pol] = score_event([replace(r, model_p=p) for r, p in zip(brows, probs)])
    return {"band": band, "hour": hour_local, "scores": scores,
            "hrrr_avail": hrrr_d is not None, "prod_w": prod_w}


def _agg(slist: list[EventScore], prod_slist: list[EventScore] | None = None) -> dict | None:
    if not slist:
        return None
    dB = [s.diff_brier for s in slist]
    out = {
        "n": len(slist),
        "model_brier": _mean(s.model_brier for s in slist),
        "dBrier": _mean(dB),
        "dBrier_ci": _paired_ci(dB),
        "dRPS": _mean(s.diff_rps for s in slist),
        "dCRPS": _mean(s.diff_crps for s in slist),
        "dCenterMAE": _mean(s.diff_center_abs_error_f for s in slist),
    }
    return out


def run(days: int = 3650, workers: int = 4) -> str:
    rows, _ = collect_coherent_snapshot_rows(days, max_lead_day=0, var="TMAX_DAILY",
                                             tick_minutes=10, min_buckets=3)
    events: dict[tuple, list[BucketRow]] = defaultdict(list)
    for r in rows:
        events[(r.station, r.valid_date)].append(r)

    # scores_all[policy] = list[EventScore]; scores_band[(band,policy)] = list
    scores_all: dict[str, list[EventScore]] = {p: [] for p in POLICIES}
    scores_band: dict[tuple, list[EventScore]] = defaultdict(list)
    band_n: dict[str, int] = defaultdict(int)
    hrrr_avail = 0
    n_events = 0
    n_skipped = 0

    def work(item):
        (station, vdate), brows = item
        return _process_event(station, vdate, brows)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(work, it) for it in events.items()]
        for fut in as_completed(futs):
            res = fut.result()
            if res is None:
                n_skipped += 1
                continue
            n_events += 1
            if res["hrrr_avail"]:
                hrrr_avail += 1
            band_n[res["band"]] += 1
            for pol in POLICIES:
                s = res["scores"][pol]
                if s is not None:
                    scores_all[pol].append(s)
                    scores_band[(res["band"], pol)].append(s)

    a = {p: _agg(scores_all[p]) for p in POLICIES}
    prod = a["prod_curve"]

    lines = [
        f"# EXP-B2 — HRRR Weight-Curve Experiment - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Lead-0 TMAX event groups: {n_events} ({n_skipped} skipped); HRRR available "
        f"as-of snapshot in {hrrr_avail}. Per-band n: "
        + ", ".join(f"{b}={band_n[b]}" for b in BANDS) + ".",
        "",
        "IN-SAMPLE DIAGNOSTIC ONLY — not a production decision. NBM-only center rebuilt "
        "point-in-time (`center_blend_weights={'NBM':1.0}`; bias + intraday floor "
        "retained; **pre-calibrator** raw CDF), HRRR/GFS shift re-applied per policy "
        "(production formula `shift += w*(hrrr-nbm_median)`, GFS fallback 0.30). "
        "as_of = coherent-snapshot ts (no future data). Scored vs the SAME market mids. "
        "Negative dX_vs_mkt = model better than market. `vs_prod` = "
        "dBrier_vs_mkt(policy) - dBrier_vs_mkt(prod_curve); negative = smaller market gap.",
        "",
        "## Overall (all lead-0 hours)",
        "",
        "| policy | n | model Brier | dBrier_vs_mkt | dBrier 95% CI | dRPS_vs_mkt | dCRPS_vs_mkt | dCenterMAE_vs_mkt | vs_prod (Brier) |",
        "|---|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for pol in POLICIES:
        r = a[pol]
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

    # By-hour-band matrices for the key question.
    def band_matrix(metric_key: str, title: str, fmt: str):
        out = [f"", f"## {title} by local-hour band", "",
               "| band | n | " + " | ".join(POLICIES) + " |",
               "|---|---:|" + "|".join(["---:"] * len(POLICIES)) + "|"]
        for b in BANDS:
            cells = []
            for pol in POLICIES:
                r = _agg(scores_band[(b, pol)])
                cells.append(format(r[metric_key], fmt) if r else "")
            out.append(f"| {b} | {band_n[b]} | " + " | ".join(cells) + " |")
        return out

    lines += band_matrix("dBrier", "dBrier_vs_mkt", "+.4f")
    lines += band_matrix("dCenterMAE", "dCenterMAE_vs_mkt", "+.2f")

    lines += [
        "",
        "Reading: `prod_curve` is current behavior. A policy is an in-sample "
        "damage-reduction *candidate* (NOT a validated fix) where `vs_prod (Brier)` is "
        "negative and dRPS/dCRPS are not worse — especially in the 13-16h bands where the "
        "production curve weights HRRR 0.78-0.92. All deltas are IN-SAMPLE and "
        "pre-calibrator; before any production decision a candidate must be re-scored "
        "through the full production-like path (calibrator included) and validated "
        "walk-forward on fresh lead-0 station-days, with no in-sample weight tuning "
        "(WEATHERBOT_PROMOTION_CRITERIA.md). A fitted by-hour curve is deferred (high "
        "overfit risk); these fixed policies bracket it.",
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
