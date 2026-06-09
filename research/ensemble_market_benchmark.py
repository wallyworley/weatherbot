"""EXP-2026-013: do the shadow ensembles beat the market-implied distribution?

RESEARCH-ONLY. No production change. Locked pre-registration:
docs/research/EXP_2026_013_ENSEMBLE_MARKET_BENCHMARK.md (registry EXP-2026-013).

Scores the four never-benchmarked `ensemble_forecast` models
(WEATHERNEXT2, ECMWF_AIFS_ENS, ECMWF_IFS_ENS, GFS_ENS) against the Kalshi
market on the canonical coherent-snapshot benchmark, three locked variants each:

  <m>_center    : ensemble median of member daily TMAX -> NBM-only shape shift (EXP-C1 method)
  <m>_center_bc : same center minus trailing mean residual vs CLI truth over strictly-prior
                  valid_dates (same station/model/lead; min 5, max 30 days; walk-forward)
  <m>_dist      : member-frequency bucket probabilities with fixed Laplace-0.5 smoothing

Leakage guard: run selection by `ingested_at <= snapshot_ts` (run_time is untrusted
metadata for the Open-Meteo-sourced ensembles). Member daily TMAX = max of the member's
values with valid_time inside the station-local valid_date.

Usage:
    python -m weather_bot.research.ensemble_market_benchmark --days 60 --workers 4
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from weather_bot.config import STATIONS
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

MODELS = ("WEATHERNEXT2", "ECMWF_AIFS_ENS", "ECMWF_IFS_ENS", "GFS_ENS")
BC_MIN_DAYS = 5
BC_MAX_DAYS = 30
MIN_MEMBERS = 10
LAPLACE = 0.5


def _local_day_bounds_utc(station: str, vdate: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(STATIONS[station].tz)
    start = datetime.combine(vdate, time.min, tzinfo=tz)
    end = datetime.combine(vdate + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def member_daily_tmax(station: str, model: str, vdate: date, as_of: datetime) -> dict[str, float] | None:
    """Per-member daily TMAX from the latest run physically ingested by `as_of`."""
    day_start, day_end = _local_day_bounds_utc(station, vdate)
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_time FROM ensemble_forecast
            WHERE station=%s AND model=%s AND var='TMP_2M'
              AND ingested_at <= %s AND valid_time >= %s AND valid_time < %s
            ORDER BY run_time DESC LIMIT 1
            """,
            (station, model, as_of, day_start, day_end),
        )
        row = cur.fetchone()
        if not row:
            return None
        run_time = row["run_time"]
        cur.execute(
            """
            SELECT member, MAX(value) AS vmax FROM ensemble_forecast
            WHERE station=%s AND model=%s AND var='TMP_2M' AND run_time=%s
              AND valid_time >= %s AND valid_time < %s
            GROUP BY member
            """,
            (station, model, run_time, day_start, day_end),
        )
        vals = {r["member"]: float(r["vmax"]) for r in cur.fetchall() if r["vmax"] is not None}
    return vals if len(vals) >= MIN_MEMBERS else None


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _dist_probs(member_maxes: list[float], brows: list[BucketRow]) -> list[float]:
    n_b = len(brows)
    n_m = len(member_maxes)
    probs = []
    for r in brows:
        lo = r.lower_f if r.lower_f is not None else float("-inf")
        hi = r.upper_f if r.upper_f is not None else float("inf")
        cnt = sum(1 for v in member_maxes if lo <= v < hi)
        probs.append((cnt + LAPLACE) / (n_m + LAPLACE * n_b))
    return probs


def _process_event(station, vdate, lead, brows):
    """Everything except the walk-forward bc scoring (done in a chronological pass)."""
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

    raw_centers: dict[str, float] = {}
    scores: dict[str, EventScore | None] = {}

    def center_score(center: float) -> EventScore | None:
        cdf.shift = base_shift + (float(center) - nbm_median)
        probs = [cdf.prob_between(r.lower_f, r.upper_f) for r in brows]
        return score_event([replace(r, model_p=p) for r, p in zip(brows, probs)])

    any_model = False
    for model in MODELS:
        members = member_daily_tmax(station, model, vdate, ts)
        if not members:
            continue
        any_model = True
        vals = list(members.values())
        c = _median(vals)
        raw_centers[model] = c
        scores[f"{model}_center"] = center_score(c)
        dprobs = _dist_probs(vals, brows)
        scores[f"{model}_dist"] = score_event(
            [replace(r, model_p=p) for r, p in zip(brows, dprobs)]
        )
    if not any_model:
        return None
    scores["nbm_only"] = center_score(nbm_median)
    return {
        "station": station, "vdate": vdate, "lead": lead, "brows": brows,
        "cdf": cdf, "base_shift": base_shift, "nbm_median": nbm_median,
        "truth": brows[0].truth_f, "raw_centers": raw_centers, "scores": scores,
    }


def _bc_pass(records: list[dict]) -> None:
    """Chronological walk-forward bias-corrected centers (strictly-prior residuals)."""
    hist: dict[tuple, list[tuple[date, float]]] = defaultdict(list)
    for rec in sorted(records, key=lambda r: (r["vdate"], r["station"], r["lead"])):
        for model, c in rec["raw_centers"].items():
            key = (rec["station"], model, rec["lead"])
            prior = [res for d, res in hist[key] if d < rec["vdate"]][-BC_MAX_DAYS:]
            if len(prior) >= BC_MIN_DAYS:
                bc = c - _mean(prior)
                cdf = rec["cdf"]
                cdf.shift = rec["base_shift"] + (bc - rec["nbm_median"])
                probs = [cdf.prob_between(r.lower_f, r.upper_f) for r in rec["brows"]]
                rec["scores"][f"{model}_center_bc"] = score_event(
                    [replace(r, model_p=p) for r, p in zip(rec["brows"], probs)]
                )
            hist[key].append((rec["vdate"], c - float(rec["truth"])))
        rec.pop("cdf", None)
        rec.pop("brows", None)


def _variants() -> list[str]:
    out = ["nbm_only"]
    for m in MODELS:
        out += [f"{m}_center", f"{m}_center_bc", f"{m}_dist"]
    return out


def _agg(slist: list[EventScore]) -> dict | None:
    if not slist:
        return None
    dB = [s.diff_brier for s in slist]
    dR = [s.diff_rps for s in slist]
    by_station = defaultdict(list)
    for s in slist:
        by_station[s.station].append(s.diff_brier)
    beat = sum(1 for v in by_station.values() if _mean(v) < 0)
    return {
        "n": len(slist),
        "dBrier": _mean(dB), "dBrier_ci": _paired_ci(dB),
        "dRPS": _mean(dR), "dRPS_ci": _paired_ci(dR),
        "dCRPS": _mean(s.diff_crps for s in slist),
        "dCenterMAE": _mean(s.diff_center_abs_error_f for s in slist),
        "stations": len(by_station), "stations_beating_mkt": beat,
    }


def _paired_vs_nbm(records, lead, name):
    db, dr = [], []
    for rec in records:
        if rec["lead"] != lead:
            continue
        a, b = rec["scores"].get(name), rec["scores"].get("nbm_only")
        if a is None or b is None:
            continue
        db.append(a.diff_brier - b.diff_brier)
        dr.append(a.diff_rps - b.diff_rps)
    if not db:
        return None
    return {"n": len(db), "dB": _mean(db), "dB_ci": _paired_ci(db),
            "dR": _mean(dr), "dR_ci": _paired_ci(dr)}


def _fmt_ci(ci) -> str:
    return f"[{ci[0]:+.4f}, {ci[1]:+.4f}]" if ci and ci[0] is not None else ""


def run(days: int = 60, workers: int = 4) -> str:
    rows, _ = collect_coherent_snapshot_rows(days, max_lead_day=1, var="TMAX_DAILY",
                                             tick_minutes=10, min_buckets=3)
    events: dict[tuple, list[BucketRow]] = defaultdict(list)
    for r in rows:
        events[(r.station, r.valid_date, r.lead_day)].append(r)

    records: list[dict] = []
    n_no_ensemble = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(_process_event, s, d, l, b) for (s, d, l), b in events.items()]
        for fut in as_completed(futs):
            res = fut.result()
            if res is None:
                n_no_ensemble += 1
            else:
                records.append(res)
    _bc_pass(records)

    lead_n: dict[int, int] = defaultdict(int)
    for rec in records:
        lead_n[rec["lead"]] += 1

    lines = [
        f"# EXP-2026-013 — Shadow-Ensemble Market-Relative Benchmark — {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        "Locked prereg: `EXP_2026_013_ENSEMBLE_MARKET_BENCHMARK.md`. Negative dBrier/dRPS",
        "= variant beats the market. Pass bar: BOTH negative with paired CI excluding 0,",
        "n>=100, >=2 stations beating. Run selection: `ingested_at <= snapshot_ts`.",
        "",
        f"Cohort: lead-0 = {lead_n.get(0, 0)}, lead-1 = {lead_n.get(1, 0)} events with >=1 "
        f"ensemble model; {n_no_ensemble} events skipped (no qualifying ensemble run / no NBM).",
    ]
    for lead in (1, 0):
        lines += [
            "", f"## Lead {lead} — vs market",
            "",
            "| variant | n | st | st beating mkt | dBrier_vs_mkt | 95% CI | dRPS_vs_mkt | 95% CI | dCRPS | dCenterMAE |",
            "|---|---:|---:|---:|---:|---|---:|---|---:|---:|",
        ]
        for name in _variants():
            slist = [rec["scores"][name] for rec in records
                     if rec["lead"] == lead and rec["scores"].get(name) is not None]
            r = _agg(slist)
            if r is None:
                lines.append(f"| {name} | 0 | | | | | | | | |")
                continue
            lines.append(
                f"| {name} | {r['n']} | {r['stations']} | {r['stations_beating_mkt']} | "
                f"{r['dBrier']:+.4f} | {_fmt_ci(r['dBrier_ci'])} | "
                f"{r['dRPS']:+.4f} | {_fmt_ci(r['dRPS_ci'])} | "
                f"{r['dCRPS']:+.3f} | {r['dCenterMAE']:+.2f} |"
            )
        lines += [
            "", f"### Lead {lead} — paired vs nbm_only (negative = better than NBM baseline)",
            "",
            "| variant − nbm_only | n | ΔBrier | 95% CI | ΔRPS | 95% CI |",
            "|---|---:|---:|---|---:|---|",
        ]
        for name in _variants():
            if name == "nbm_only":
                continue
            p = _paired_vs_nbm(records, lead, name)
            if p is None:
                continue
            lines.append(
                f"| {name} − nbm_only | {p['n']} | {p['dB']:+.4f} | {_fmt_ci(p['dB_ci'])} | "
                f"{p['dR']:+.4f} | {_fmt_ci(p['dR_ci'])} |"
            )
    lines += [
        "",
        "Limitations per prereg §8: NBM bias-corrected vs ensembles raw (`_center_bc` is the",
        "fair read, esp. WEATHERNEXT2 whose 6-hourly sampling biases raw daily-max cold);",
        "one summer month; `_dist` spread uncalibrated by design. No production change.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=60)
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
