"""EXP-C1b — walk-forward conditioned forecast centers vs the market.

RESEARCH-ONLY. No production behavior touched. Implements the LOCKED pre-registration
`docs/research/EXP_C1B_PREREGISTRATION.md` (EXP-2026-008) exactly. Builds nothing in
production; scores candidate forecast centers market-relative on the canonical
coherent-snapshot benchmark, walk-forward.

Six fixed candidate centers (all keep NBM spread/shape; swap only the center, as in
EXP-C1): gfs_bc, ecmwf_bc, hrrr_bc (lead-0), invmae_blend_bc, regime_agree, obs_anchor_l0.
Baseline: nbm_only (a candidate must beat both market AND nbm_only).

Locked protocol highlights:
- Training cutoff: trailing stats use only hist dates in
  [station_local_date(as_of) − 30, station_local_date(as_of) − 1] (leakage-safe for lead-1).
- Trailing forecasts reconstructed point-in-time at the same station-local time as as_of.
- Bias = trailing mean(fcst − CLI); MAE = trailing mean|fcst − CLI|; min 8 samples else
  fallback (bias 0 / drop from blend).
- inverse-MAE weight = 1/(MAE + 0.5). regime τ = trailing median |NBM−GFS|.
- obs_anchor remaining-rise = trailing mean(CLI − metar_max-at-hour) by fixed local-hour
  bucket {<10,10-11,12-13,14-15,>=16}; <8 samples -> rise 0.
- Pass (per applicable lead cohort): beat market AND nbm_only on Brier+RPS at
  Bonferroni-6 level (z=2.638); robust >=2 stations and >=2 splits; >=100 correction-applied
  station-days. Chronological held-out reported as confirmation only.

Usage:
    python -m weather_bot.research.center_market_benchmark_wf --days 3650 --workers 6
"""
from __future__ import annotations

import argparse
import math
import statistics as st
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import date, datetime, time, timezone

import pytz

from weather_bot.config import STATIONS
from weather_bot.data import persistence
from weather_bot.models.distribution import build_station_distribution, station_local_date
from weather_bot.research.market_relative_center_benchmark import (
    BucketRow,
    _mean,
    collect_coherent_snapshot_rows,
    score_event,
)

_LOOKBACK = 30
_MIN_SAMPLES = 8
_BONF_Z = 2.638  # two-sided alpha = 0.05/6
CANDIDATES = ("gfs_bc", "ecmwf_bc", "hrrr_bc", "invmae_blend_bc", "regime_agree", "obs_anchor_l0")
LEAD0_ONLY = {"hrrr_bc", "obs_anchor_l0"}
ALL_VARIANTS = ("nbm_only",) + CANDIDATES


def _paired_ci_z(diffs: list[float], z: float) -> tuple[float | None, float | None]:
    if len(diffs) < 2:
        return None, None
    se = st.stdev(diffs) / math.sqrt(len(diffs))
    c = st.fmean(diffs)
    return c - z * se, c + z * se


def _same_local_time_asof(station: str, target: date, template_asof: datetime) -> datetime:
    tz = pytz.timezone(STATIONS[station].tz)
    lt = template_asof.astimezone(tz)
    local = tz.localize(datetime.combine(target, time(lt.hour, lt.minute, lt.second, lt.microsecond)))
    return local.astimezone(timezone.utc)


# Inlined SQL so all of an event's lookups run on ONE connection (the connect
# handshake over the SSH tunnel dominates runtime; round-trips on a warm cursor are cheap).
_NBM_SQL = """
SELECT value FROM prob_forecast
 WHERE station=%s AND valid_date=%s AND var='TMAX_DAILY' AND percentile=50 AND run_time<=%s
 ORDER BY run_time DESC LIMIT 1
"""
_DET_SQL = """
SELECT MAX(value) AS tmax FROM det_forecast
 WHERE station=%s AND model=%s AND var='TMP_2M' AND (valid_time AT TIME ZONE %s)::date=%s
   AND run_time=(SELECT MAX(run_time) FROM det_forecast
                  WHERE station=%s AND model=%s AND var='TMP_2M'
                    AND (valid_time AT TIME ZONE %s)::date=%s AND run_time<=%s)
"""
_METAR_SQL = """
SELECT MAX(mo.temp_f) AS m FROM metar_obs mo JOIN stations st ON st.code = mo.station
 WHERE mo.station=%s AND (mo.obs_time AT TIME ZONE st.tz)::date=%s
   AND mo.obs_time <= %s AND mo.temp_f IS NOT NULL
"""


def _center_as_of(cur, station: str, vd: date, source: str, as_of: datetime) -> float | None:
    if source == "NBM":
        cur.execute(_NBM_SQL, (station, vd, as_of))
        r = cur.fetchone()
        return float(r["value"]) if r and r["value"] is not None else None
    tz = STATIONS[station].tz
    cur.execute(_DET_SQL, (station, source, tz, vd, station, source, tz, vd, as_of))
    r = cur.fetchone()
    return float(r["tmax"]) if r and r["tmax"] is not None else None


def _metar_max_asof(cur, station: str, vd: date, as_of: datetime) -> float | None:
    cur.execute(_METAR_SQL, (station, vd, as_of))
    r = cur.fetchone()
    return float(r["m"]) if r and r["m"] is not None else None


def _hour_bucket(h: int) -> str:
    if h < 10:
        return "<10"
    if h <= 11:
        return "10-11"
    if h <= 13:
        return "12-13"
    if h <= 15:
        return "14-15"
    return ">=16"


# Batched trailing lookups: one query per source over unnest(dates[], cutoffs[]),
# so an event needs ~6 round-trips instead of ~150 (decisive over the SSH tunnel).
_TRUTH_BATCH = "SELECT local_date AS d, tmax_f FROM cli_obs WHERE station=%s AND local_date = ANY(%s::date[])"
_NBM_BATCH = """
SELECT v.d AS d,
       (SELECT p.value FROM prob_forecast p
         WHERE p.station=%s AND p.valid_date=v.d AND p.var='TMAX_DAILY' AND p.percentile=50
           AND p.run_time<=v.c ORDER BY p.run_time DESC LIMIT 1) AS val
  FROM unnest(%s::date[], %s::timestamptz[]) AS v(d, c)
"""
_DET_BATCH = """
SELECT v.d AS d,
       (SELECT MAX(f.value) FROM det_forecast f
         WHERE f.station=%s AND f.model=%s AND f.var='TMP_2M'
           AND (f.valid_time AT TIME ZONE %s)::date=v.d
           AND f.run_time=(SELECT MAX(f2.run_time) FROM det_forecast f2
                            WHERE f2.station=%s AND f2.model=%s AND f2.var='TMP_2M'
                              AND (f2.valid_time AT TIME ZONE %s)::date=v.d
                              AND f2.run_time<=v.c)) AS val
  FROM unnest(%s::date[], %s::timestamptz[]) AS v(d, c)
"""
_METAR_BATCH = """
SELECT v.d AS d,
       (SELECT MAX(mo.temp_f) FROM metar_obs mo
         WHERE mo.station=%s AND (mo.obs_time AT TIME ZONE %s)::date=v.d
           AND mo.obs_time<=v.c AND mo.temp_f IS NOT NULL) AS val
  FROM unnest(%s::date[], %s::timestamptz[]) AS v(d, c)
"""


def _trailing_dates_cutoffs(station: str, lead: int, anchor: date, as_of: datetime):
    """Trailing (valid_date, forecast-cutoff) pairs over [anchor-30, anchor-1].

    LEAD-ALIGNED (fix found during the 2026-06-07 VPS run): each trailing day's
    forecast is reconstructed at the SAME lead and local time as the event —
    cutoff = (hd - lead) at as_of's local time. For lead-0 this is hd itself
    (unchanged); for lead-1 it is the evening-before-hd run, matching the lead-1
    current forecast (rather than a more-accurate same-day reconstruction). The
    truth cutoff is unchanged (hd < station_local_date(as_of)); only the forecast
    reconstruction time is lead-aligned.
    """
    dates, cutoffs = [], []
    for off in range(1, _LOOKBACK + 1):
        hd = date.fromordinal(anchor.toordinal() - off)
        cut_date = date.fromordinal(hd.toordinal() - lead)
        dates.append(hd)
        cutoffs.append(_same_local_time_asof(station, cut_date, as_of))
    return dates, cutoffs


def _trailing_model_stats(cur, station: str, lead: int, anchor: date, as_of: datetime):
    """Per-model trailing bias/MAE and trailing |NBM-GFS| over [anchor-30, anchor-1]."""
    sources = ("NBM", "GFS", "ECMWF") + (("HRRR",) if lead == 0 else ())
    dates, cutoffs = _trailing_dates_cutoffs(station, lead, anchor, as_of)
    tz = STATIONS[station].tz

    cur.execute(_TRUTH_BATCH, (station, dates))
    truth = {r["d"]: float(r["tmax_f"]) for r in cur.fetchall() if r["tmax_f"] is not None}

    vals: dict[str, dict] = {}
    for s in sources:
        if s == "NBM":
            cur.execute(_NBM_BATCH, (station, dates, cutoffs))
        else:
            cur.execute(_DET_BATCH, (station, s, tz, station, s, tz, dates, cutoffs))
        vals[s] = {r["d"]: float(r["val"]) for r in cur.fetchall() if r["val"] is not None}

    errs = {s: [] for s in sources}
    abss = {s: [] for s in sources}
    disagree = []
    for d in dates:
        if d not in truth:
            continue
        for s in sources:
            if d in vals[s]:
                errs[s].append(vals[s][d] - truth[d])
                abss[s].append(abs(vals[s][d] - truth[d]))
        if d in vals.get("NBM", {}) and d in vals.get("GFS", {}):
            disagree.append(abs(vals["NBM"][d] - vals["GFS"][d]))

    bias = {s: (st.fmean(errs[s]) if len(errs[s]) >= _MIN_SAMPLES else None) for s in sources}
    mae = {s: (st.fmean(abss[s]) if len(abss[s]) >= _MIN_SAMPLES else None) for s in sources}
    tau = st.median(disagree) if len(disagree) >= _MIN_SAMPLES else None
    return bias, mae, tau


def _trailing_remaining_rise(cur, station: str, anchor: date, as_of: datetime) -> float | None:
    """Trailing mean(CLI - metar_max-at-cutoff). obs_anchor is lead-0 only, so cutoffs are
    same-local-time on each hist day (lead 0). All share the event's hour bucket (implicit)."""
    dates, cutoffs = _trailing_dates_cutoffs(station, 0, anchor, as_of)
    tz = STATIONS[station].tz
    cur.execute(_TRUTH_BATCH, (station, dates))
    truth = {r["d"]: float(r["tmax_f"]) for r in cur.fetchall() if r["tmax_f"] is not None}
    cur.execute(_METAR_BATCH, (station, tz, dates, cutoffs))
    mm = {r["d"]: float(r["val"]) for r in cur.fetchall() if r["val"] is not None}
    vals = [truth[d] - mm[d] for d in dates if d in truth and d in mm]
    return st.fmean(vals) if len(vals) >= _MIN_SAMPLES else None


def _process_event(station, vdate, lead, brows):
    ts = max(r.ts for r in brows)
    try:
        cdf = build_station_distribution(station, vdate, var="TMAX_DAILY", now_utc=ts, as_of=ts,
                                         center_blend_weights={"NBM": 1.0})
    except Exception:
        cdf = None
    if cdf is None:
        return None
    base_shift = cdf.shift
    nbm_median = cdf.median()
    anchor = station_local_date(station, ts)

    # One connection for ALL of this event's point-in-time lookups (warm cursor).
    with persistence.connect() as conn, conn.cursor() as cur:
        nbm_now = _center_as_of(cur, station, vdate, "NBM", ts)
        gfs_now = _center_as_of(cur, station, vdate, "GFS", ts)
        ecmwf_now = _center_as_of(cur, station, vdate, "ECMWF", ts)
        hrrr_now = _center_as_of(cur, station, vdate, "HRRR", ts) if lead == 0 else None
        bias, mae, tau = _trailing_model_stats(cur, station, lead, anchor, ts)
        if lead == 0:
            _rise = _trailing_remaining_rise(cur, station, anchor, ts)
            _mm_now = _metar_max_asof(cur, station, vdate, ts)
        else:
            _rise = _mm_now = None

    def bc(now, src):  # bias-corrected center; applied flag
        if now is None:
            return None, False
        b = bias.get(src)
        return (now - b, True) if b is not None else (now, False)

    gfs_bc_c, gfs_app = bc(gfs_now, "GFS")
    ecmwf_bc_c, ecmwf_app = bc(ecmwf_now, "ECMWF")
    hrrr_bc_c, hrrr_app = bc(hrrr_now, "HRRR")

    # invmae blend over {NBM(center=nbm_median), gfs_bc, ecmwf_bc}
    blend_parts = {}
    if mae.get("NBM") is not None:
        blend_parts["NBM"] = (nbm_median, mae["NBM"])
    if gfs_bc_c is not None and mae.get("GFS") is not None:
        blend_parts["GFS"] = (gfs_bc_c, mae["GFS"])
    if ecmwf_bc_c is not None and mae.get("ECMWF") is not None:
        blend_parts["ECMWF"] = (ecmwf_bc_c, mae["ECMWF"])
    blend_c = None
    blend_app = len(blend_parts) >= 2  # blend meaningful only with >=2 models
    if blend_parts:
        wsum = sum(1.0 / (m + 0.5) for _, m in blend_parts.values())
        blend_c = sum(c * (1.0 / (m + 0.5)) for c, m in blend_parts.values()) / wsum

    # regime_agree
    regime_c, regime_app = None, False
    if nbm_now is not None and gfs_now is not None and tau is not None:
        regime_app = True
        regime_c = nbm_median if abs(nbm_now - gfs_now) <= tau else (blend_c if blend_c is not None else nbm_median)

    # obs_anchor_l0 (uses _rise / _mm_now computed in the connection block above)
    obs_c, obs_app = None, False
    if lead == 0:
        if _mm_now is not None and _rise is not None:
            obs_c, obs_app = _mm_now + _rise, True
        else:
            obs_c, obs_app = nbm_median, False  # fallback to NBM center

    centers = {
        "nbm_only": (nbm_median, True),
        "gfs_bc": (gfs_bc_c, gfs_app),
        "ecmwf_bc": (ecmwf_bc_c, ecmwf_app),
        "hrrr_bc": (hrrr_bc_c, hrrr_app) if lead == 0 else (None, False),
        "invmae_blend_bc": (blend_c, blend_app),
        "regime_agree": (regime_c, regime_app),
        "obs_anchor_l0": (obs_c, obs_app) if lead == 0 else (None, False),
    }
    scores, applied = {}, {}
    for name, (c, app) in centers.items():
        applied[name] = app
        if c is None:
            scores[name] = None
            continue
        cdf.shift = base_shift + (float(c) - nbm_median)
        probs = [cdf.prob_between(r.lower_f, r.upper_f) for r in brows]
        scores[name] = score_event([replace(r, model_p=p) for r, p in zip(brows, probs)])
    return {"lead": lead, "station": station, "vdate": vdate, "scores": scores, "applied": applied}


def _events_for(records, lead, name, applied_only):
    out = []
    for rec in records:
        if rec["lead"] != lead:
            continue
        if rec["scores"].get(name) is None:
            continue
        if applied_only and not rec["applied"].get(name):
            continue
        out.append(rec)
    return out


def _market_rel(recs, name):
    ss = [rec["scores"][name] for rec in recs]
    dB = [s.diff_brier for s in ss]
    dR = [s.diff_rps for s in ss]
    return {
        "n": len(ss), "model_brier": _mean(s.model_brier for s in ss),
        "dBrier": _mean(dB), "dBrier_ci": _paired_ci_z(dB, _BONF_Z),
        "dRPS": _mean(dR), "dRPS_ci": _paired_ci_z(dR, _BONF_Z),
        "dCRPS": _mean(s.diff_crps for s in ss),
    }


def _paired_vs_nbm(recs, name, metric):
    deltas = [rec["scores"][name].__getattribute__(metric) - rec["scores"]["nbm_only"].__getattribute__(metric)
              for rec in recs if rec["scores"].get("nbm_only") is not None]
    return {"n": len(deltas), "mean": _mean(deltas) if deltas else None,
            "ci": _paired_ci_z(deltas, _BONF_Z)} if deltas else None


def _fmt_ci(ci):
    return f"[{ci[0]:+.4f}, {ci[1]:+.4f}]" if ci and ci[0] is not None else ""


def run(days: int = 3650, workers: int = 6) -> str:
    rows, _ = collect_coherent_snapshot_rows(days, max_lead_day=1, var="TMAX_DAILY",
                                             tick_minutes=10, min_buckets=3)
    events: dict[tuple, list[BucketRow]] = defaultdict(list)
    for r in rows:
        events[(r.station, r.valid_date, r.lead_day)].append(r)

    records: list[dict] = []
    n_skipped = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(_process_event, s, v, l, b) for (s, v, l), b in events.items()]
        for fut in as_completed(futs):
            res = fut.result()
            if res is None:
                n_skipped += 1
            else:
                records.append(res)

    lines = [
        f"# EXP-C1b — Walk-Forward Conditioned Forecast Centers - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        "Implements the LOCKED pre-registration `EXP_C1B_PREREGISTRATION.md` (EXP-2026-008). "
        "Walk-forward (trailing stats from valid_date < station_local_date(as_of), 30d, min 8); "
        "pre-calibrator; NBM shape, center swapped per variant; scored market-relative on the "
        "canonical benchmark. CIs are **Bonferroni-6** (z=2.638). Negative = beats market. "
        "`vs_nbm` = paired (variant − nbm_only). PASS needs negative market AND nbm deltas "
        "with CI excluding 0, in the applicable lead cohort, on the correction-applied subset.",
        "",
        f"Skipped (no PIT rebuild): {n_skipped}.",
    ]

    for lead in (1, 0):
        recs_nbm = _events_for(records, lead, "nbm_only", applied_only=False)
        lines += [
            "",
            f"## Lead {lead}",
            "",
            "Market-relative (Bonferroni-6 CIs; **correction-applied subset**):",
            "",
            "| center | n_applied | dBrier_vs_mkt | dBrier CI | dRPS_vs_mkt | dRPS CI | dCRPS | vs_nbm ΔBrier | vs_nbm CI | PASS? |",
            "|---|---:|---:|---|---:|---|---:|---:|---|:--:|",
        ]
        # baseline row (nbm_only, all events)
        b = _market_rel(recs_nbm, "nbm_only")
        lines.append(
            f"| nbm_only (base) | {b['n']} | {b['dBrier']:+.4f} | {_fmt_ci(b['dBrier_ci'])} | "
            f"{b['dRPS']:+.4f} | {_fmt_ci(b['dRPS_ci'])} | {b['dCRPS']:+.3f} | — | — | — |"
        )
        for name in CANDIDATES:
            if lead != 0 and name in LEAD0_ONLY:
                continue
            recs = _events_for(records, lead, name, applied_only=True)
            if len(recs) < 2:
                lines.append(f"| {name} | {len(recs)} | (insufficient) | | | | | | | no |")
                continue
            mr = _market_rel(recs, name)
            vn = _paired_vs_nbm(recs, name, "diff_brier")
            vn_r = _paired_vs_nbm(recs, name, "diff_rps")
            beats_mkt = (mr["dBrier_ci"][1] is not None and mr["dBrier_ci"][1] < 0
                         and mr["dRPS_ci"][1] is not None and mr["dRPS_ci"][1] < 0)
            beats_nbm = (vn and vn["ci"][1] is not None and vn["ci"][1] < 0
                         and vn_r and vn_r["ci"][1] is not None and vn_r["ci"][1] < 0)
            enough = mr["n"] >= 100
            passed = "**YES**" if (beats_mkt and beats_nbm and enough) else "no"
            lines.append(
                f"| {name} | {mr['n']} | {mr['dBrier']:+.4f} | {_fmt_ci(mr['dBrier_ci'])} | "
                f"{mr['dRPS']:+.4f} | {_fmt_ci(mr['dRPS_ci'])} | {mr['dCRPS']:+.3f} | "
                f"{(vn['mean'] if vn else 0):+.4f} | {_fmt_ci(vn['ci'] if vn else None)} | {passed} |"
            )
        # coverage
        lines += ["", "Correction-applied coverage (n events where the variant applied vs total in lead):", ""]
        tot = len(recs_nbm)
        cov = []
        for name in CANDIDATES:
            if lead != 0 and name in LEAD0_ONLY:
                continue
            na = len(_events_for(records, lead, name, applied_only=True))
            cov.append(f"{name}={na}/{tot}")
        lines.append(", ".join(cov) + ".")

    lines += [
        "",
        "## Verdict",
        "",
        "A variant PASSES only with **YES** above (negative market AND nbm deltas, Bonferroni-6 "
        "CI excluding 0, ≥100 correction-applied station-days) — and then also requires the §5 "
        "robustness check (≥2 stations, ≥2 splits) and a chronological held-out confirmation "
        "before being called a candidate. If no variant shows YES, the pre-committed decision "
        "(prereg §6) is: recommend the observation-only pivot, with thin-coverage variants "
        "labeled inconclusive (data-limited) rather than rejected.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=3650)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--out", type=str, default=None)
    args = p.parse_args()
    md = run(days=args.days, workers=args.workers)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
