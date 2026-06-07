"""EXP-C1 (first pass): can any forecast CENTER beat the market-implied center?

RESEARCH-ONLY. Does not change production trading behavior. Supports
EXPERIMENT_PLAN_NEXT.md EXP-C1 and WEATHERBOT_EXPERIMENT_REGISTRY.md EXP-2026-007.

This is the program's binding question (charter Q3). B1–B3 showed mechanical center
*tweaks* do not create edge; C1 asks whether any *center* — including ones the bot
does not currently use as a primary center (ECMWF, a multi-model decorrelation blend)
— beats the Kalshi market-implied center out of sample.

Method (parameter-free first pass, no fitting -> no walk-forward needed): for each
coherent-snapshot event, rebuild the NBM-only distribution point-in-time
(`center_blend_weights={"NBM":1.0}`; NBM bias retained; lead-0 keeps the intraday
floor; pre-calibrator), then shift the center to each candidate while KEEPING THE NBM
SHAPE (`cdf.shift = base + (candidate_center − nbm_median)`), and score against the
SAME market midpoints with the canonical benchmark scoring, by lead. This isolates the
*center* question (Q3): if the center were X (with NBM's spread), would it beat market?

Candidate centers (parameter-free):
  nbm_only   : NBM p50 (bias-corrected)            — baseline
  gfs_center : raw GFS daily-TMAX as-of
  ecmwf_center : raw ECMWF daily-TMAX as-of
  hrrr_center  : raw HRRR daily-TMAX as-of (mostly lead-0)
  blend_nge    : 0.5·NBM + 0.25·GFS + 0.25·ECMWF (renormalized over available)

LIMITATION: `station_bias` has only NBM rows, so GFS/ECMWF/HRRR centers are RAW
(un-bias-corrected) while NBM is bias-corrected — matches production but may disadvantage
the deterministic centers. Bias-corrected/regime-conditioned/obs-anchored centers need
walk-forward and are a deferred follow-on (EXP-C1b).

Headline question: does ANY center achieve a NEGATIVE market-relative Brier/RPS (beats
the market) with a paired CI excluding 0? Per B1–B3 and the morning ablation, the
expected answer is no — this formalizes it and adds ECMWF + a decorrelation blend.

IN-SAMPLE/walk-forward-free first pass; pre-calibrator. No production change.

Usage:
    python -m weather_bot.research.center_market_benchmark --days 3650 --workers 4
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

CENTERS = ("nbm_only", "gfs_center", "ecmwf_center", "hrrr_center", "blend_nge")
_BLEND_W = {"nbm": 0.5, "gfs": 0.25, "ecmwf": 0.25}


def _candidate_centers(nbm_median, gfs_d, ecmwf_d, hrrr_d):
    out = {"nbm_only": nbm_median}
    if gfs_d is not None:
        out["gfs_center"] = gfs_d
    if ecmwf_d is not None:
        out["ecmwf_center"] = ecmwf_d
    if hrrr_d is not None:
        out["hrrr_center"] = hrrr_d
    avail = {"nbm": nbm_median, "gfs": gfs_d, "ecmwf": ecmwf_d}
    wsum = sum(_BLEND_W[k] for k, v in avail.items() if v is not None)
    if wsum > 0:
        out["blend_nge"] = sum(_BLEND_W[k] * v for k, v in avail.items() if v is not None) / wsum
    return out


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
    gfs_d = persistence.gfs_tmax_as_of(station, vdate, ts)
    ecmwf_d = persistence.det_tmax_as_of(station, vdate, "ECMWF", ts)
    hrrr_d = persistence.hrrr_tmax_as_of(station, vdate, ts)

    centers = _candidate_centers(nbm_median, gfs_d, ecmwf_d, hrrr_d)
    scores = {}
    for name in CENTERS:
        c = centers.get(name)
        if c is None:
            scores[name] = None
            continue
        cdf.shift = base_shift + (float(c) - nbm_median)
        probs = [cdf.prob_between(r.lower_f, r.upper_f) for r in brows]
        scores[name] = score_event([replace(r, model_p=p) for r, p in zip(brows, probs)])
    return {"lead": lead, "scores": scores}


def _subset(records, lead, name):
    return [rec["scores"][name] for rec in records
            if rec["lead"] == lead and rec["scores"].get(name) is not None]


def _agg(slist: list[EventScore]) -> dict | None:
    if not slist:
        return None
    dB = [s.diff_brier for s in slist]
    dR = [s.diff_rps for s in slist]
    return {
        "n": len(slist),
        "model_brier": _mean(s.model_brier for s in slist),
        "dBrier": _mean(dB), "dBrier_ci": _paired_ci(dB),
        "dRPS": _mean(dR), "dRPS_ci": _paired_ci(dR),
        "dCRPS": _mean(s.diff_crps for s in slist),
        "dCenterMAE": _mean(s.diff_center_abs_error_f for s in slist),
    }


def _paired_vs_nbm(records, lead, name, metric):
    deltas = []
    for rec in records:
        if rec["lead"] != lead:
            continue
        a = rec["scores"].get(name)
        b = rec["scores"].get("nbm_only")
        if a is None or b is None:
            continue
        deltas.append(getattr(a, metric) - getattr(b, metric))
    if not deltas:
        return None
    return {"n": len(deltas), "mean": _mean(deltas), "ci": _paired_ci(deltas)}


def run(days: int = 3650, workers: int = 4) -> str:
    rows, _ = collect_coherent_snapshot_rows(days, max_lead_day=1, var="TMAX_DAILY",
                                             tick_minutes=10, min_buckets=3)
    events: dict[tuple, list[BucketRow]] = defaultdict(list)
    for r in rows:
        events[(r.station, r.valid_date, r.lead_day)].append(r)

    records: list[dict] = []
    lead_n: dict[int, int] = defaultdict(int)
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
            records.append(res)
            lead_n[res["lead"]] += 1

    lines = [
        f"# EXP-C1 (first pass) — Forecast-Center Market-Relative Benchmark - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        "**Question:** does any forecast CENTER beat the market-implied center? "
        "Parameter-free first pass: NBM-only shape, center swapped to each candidate "
        "point-in-time (`as_of`=snapshot ts; pre-calibrator; NBM bias-corrected, "
        "GFS/ECMWF/HRRR raw — see limitation). Negative dX_vs_mkt = center beats market. "
        "A center clears the bar only with NEGATIVE market-relative Brier AND RPS whose "
        "paired CI EXCLUDES 0.",
        "",
        f"Events: lead-0 = {lead_n.get(0,0)}; lead-1 = {lead_n.get(1,0)}; skipped {n_skipped}.",
    ]

    for lead in (1, 0):
        lines += [
            "",
            f"## Lead {lead}",
            "",
            "| center | n | model Brier | dBrier_vs_mkt | dBrier 95% CI | dRPS_vs_mkt | dRPS 95% CI | dCRPS_vs_mkt | dCenterMAE_vs_mkt |",
            "|---|---:|---:|---:|---|---:|---|---:|---:|",
        ]
        for name in CENTERS:
            r = _agg(_subset(records, lead, name))
            if r is None:
                lines.append(f"| {name} | 0 | | | | | | | |")
                continue
            bci, rci = r["dBrier_ci"], r["dRPS_ci"]
            bci_t = f"[{bci[0]:+.4f}, {bci[1]:+.4f}]" if bci[0] is not None else ""
            rci_t = f"[{rci[0]:+.4f}, {rci[1]:+.4f}]" if rci[0] is not None else ""
            lines.append(
                f"| {name} | {r['n']} | {r['model_brier']:.4f} | {r['dBrier']:+.4f} | {bci_t} | "
                f"{r['dRPS']:+.4f} | {rci_t} | {r['dCRPS']:+.3f} | {r['dCenterMAE']:+.2f} |"
            )
        # Paired vs NBM-only (does any center beat NBM?).
        lines += [
            "",
            f"### Lead {lead} — paired vs nbm_only (positive = center worse than NBM)",
            "",
            "| center − nbm_only | n | mean ΔBrier | 95% CI | mean ΔRPS | 95% CI |",
            "|---|---:|---:|---|---:|---|",
        ]
        for name in CENTERS:
            if name == "nbm_only":
                continue
            pb = _paired_vs_nbm(records, lead, name, "diff_brier")
            pr = _paired_vs_nbm(records, lead, name, "diff_rps")
            if pb is None:
                continue
            bci = pb["ci"]
            rci = pr["ci"]
            bci_t = f"[{bci[0]:+.4f}, {bci[1]:+.4f}]" if bci[0] is not None else ""
            rci_t = f"[{rci[0]:+.4f}, {rci[1]:+.4f}]" if rci[0] is not None else ""
            lines.append(
                f"| {name} − nbm_only | {pb['n']} | {pb['mean']:+.4f} | {bci_t} | "
                f"{pr['mean']:+.4f} | {rci_t} |"
            )

    lines += [
        "",
        "Reading: every dBrier_vs_mkt / dRPS_vs_mkt is the gap to the market (positive = "
        "market wins). The program-relevant pass needs a center with **negative** "
        "market-relative Brier AND RPS, CI excluding 0, OOS, ≥2 stations/regimes. The "
        "paired-vs-nbm tables show whether any alternative center even beats NBM (the "
        "current baseline). LIMITATION: deterministic centers are raw (no bias rows); "
        "bias-corrected / regime-conditioned / obs-anchored centers need walk-forward and "
        "are the EXP-C1b follow-on. Pre-calibrator; no production change.",
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
