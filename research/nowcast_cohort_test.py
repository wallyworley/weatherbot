"""EXP-2026-010 (EXP-C2): lead-0 obs-timing nowcast cohort test. RESEARCH-ONLY.

Implements docs/research/EXP_C2_NOWCAST_PREREGISTRATION.md exactly.

Reads the persisted EXP-2026-009 forensics CSV, filters the LOCKED primary cohort
(lead_day==0, local hour in [13,17], latest_metar_age_min<=10), builds the single locked
signal `obs_anchor_dist` = metar_max_so_far + Normal(remaining-rise mean, std) bucket
distribution (remaining-rise climo from strictly-prior days only), and scores it
market-relative (Brier and RPS, paired) on a chronological held-out split.

No production code is touched. No trading change. Run on the VPS (DB + CSV are there).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from weather_bot.data import persistence

# Reuse the forensics tz helper so the climo matches the dataset build exactly.
from research.market_information_forensics import _station_tz  # noqa: E402

# ---- LOCKED cohort + signal parameters (EXP_C2_NOWCAST_PREREGISTRATION.md) ----
HOUR_LO, HOUR_HI = 13, 17          # local afternoon window (inclusive)
METAR_AGE_MAX_MIN = 10.0           # fresh-observation threshold
TICK_MIN = 10                      # climo local-time flooring (matches forensics build)
CLIMO_LOOKBACK_DAYS = 60
MIN_CLIMO_N = 8                    # min prior days to estimate remaining-rise mean/std
STD_FLOOR_F = 0.5                  # avoid degenerate Normal
HELD_OUT_FRAC = 0.40              # later 40% of dates = evaluation portion
_Z = 1.96


# ---------------------------------------------------------------------------
# scoring helpers (identical math to market_relative_center_benchmark)
# ---------------------------------------------------------------------------
def _clamp(p: float) -> float:
    return min(1.0, max(0.0, float(p)))


def _normalize(ps: list[float]) -> list[float] | None:
    clean = [_clamp(p) for p in ps]
    total = sum(clean)
    if total <= 0:
        return None
    return [p / total for p in clean]


def _brier(probs: list[float], winner_idx: int) -> float:
    return statistics.fmean((p - (1.0 if i == winner_idx else 0.0)) ** 2 for i, p in enumerate(probs))


def _rps(probs: list[float], winner_idx: int) -> float:
    if len(probs) < 2:
        return 0.0
    cum_f = cum_o = total = 0.0
    for i, p in enumerate(probs):
        cum_f += p
        cum_o += 1.0 if i == winner_idx else 0.0
        total += (cum_f - cum_o) ** 2
    return total / (len(probs) - 1)


def _paired_ci(diffs: list[float]) -> tuple[float | None, float | None]:
    if len(diffs) < 2:
        return None, None
    se = statistics.stdev(diffs) / math.sqrt(len(diffs))
    center = statistics.fmean(diffs)
    return center - _Z * se, center + _Z * se


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _normal_mass(lower: float | None, upper: float | None, mu: float, sigma: float) -> float:
    lo = _phi((lower - mu) / sigma) if lower is not None else 0.0
    hi = _phi((upper - mu) / sigma) if upper is not None else 1.0
    return max(0.0, hi - lo)


# ---------------------------------------------------------------------------
# remaining-rise climatology: mean + std + n from STRICTLY-PRIOR days only.
# Mirrors market_information_forensics._remaining_heating_climo SQL, but also
# returns the sample std (the one field missing from the forensics CSV).
# ---------------------------------------------------------------------------
_CLIMO_SQL = """
WITH hist AS (
    SELECT d::date AS local_date
      FROM generate_series(
           %(valid_date)s::date - (%(lookback_days)s || ' days')::interval,
           %(valid_date)s::date - interval '1 day',
           interval '1 day'
      ) AS d
),
before_obs AS (
    SELECT h.local_date, MAX(m.temp_f)::float AS max_so_far
      FROM hist h
      LEFT JOIN metar_obs m
        ON m.station = %(station)s
       AND (m.obs_time AT TIME ZONE %(tz)s)::date = h.local_date
       AND (m.obs_time AT TIME ZONE %(tz)s)::time <= %(local_time)s::time
     GROUP BY h.local_date
),
full_metar AS (
    SELECT h.local_date, MAX(m.temp_f)::float AS metar_tmax
      FROM hist h
      LEFT JOIN metar_obs m
        ON m.station = %(station)s
       AND (m.obs_time AT TIME ZONE %(tz)s)::date = h.local_date
     GROUP BY h.local_date
),
finals AS (
    SELECT h.local_date,
           COALESCE(c.tmax_f::float, d.tmax_f::float, fm.metar_tmax) AS final_tmax
      FROM hist h
      LEFT JOIN cli_obs c ON c.station = %(station)s AND c.local_date = h.local_date
      LEFT JOIN daily_obs d ON d.station = %(station)s AND d.local_date = h.local_date
      LEFT JOIN full_metar fm ON fm.local_date = h.local_date
)
SELECT (f.final_tmax - b.max_so_far)::float AS remaining
  FROM finals f
  JOIN before_obs b ON b.local_date = f.local_date
 WHERE f.final_tmax IS NOT NULL
   AND b.max_so_far IS NOT NULL
"""

_climo_cache: dict[tuple[str, date, time], tuple[float | None, float | None, int]] = {}


def remaining_rise_stats(cur, station: str, valid_date: date, local_time: time) -> tuple[float | None, float | None, int]:
    key = (station, valid_date, local_time)
    if key in _climo_cache:
        return _climo_cache[key]
    cur.execute(
        _CLIMO_SQL,
        {
            "station": station,
            "valid_date": valid_date,
            "lookback_days": CLIMO_LOOKBACK_DAYS,
            "tz": _station_tz(station),
            "local_time": local_time,
        },
    )
    vals = [float(r["remaining"]) for r in cur.fetchall()]
    if len(vals) < MIN_CLIMO_N:
        out = (None, None, len(vals))
    else:
        out = (statistics.fmean(vals), statistics.pstdev(vals), len(vals))
    _climo_cache[key] = out
    return out


# ---------------------------------------------------------------------------
# cohort + scoring
# ---------------------------------------------------------------------------
@dataclass
class Scored:
    station: str
    valid_date: date
    d_brier: float          # obs - market (negative = obs beats market)
    d_rps: float
    boundary_le_half: bool


def _floor_local_time(local_time_str: str) -> tuple[int, time]:
    dt = datetime.fromisoformat(local_time_str)
    minute = ((dt.hour * 60 + dt.minute) // TICK_MIN) * TICK_MIN
    return dt.hour, time(minute // 60, minute % 60)


def _winner_idx(buckets: list[dict], settle: float) -> int | None:
    for i, b in enumerate(buckets):
        lo, hi = b["lower_f"], b["upper_f"]
        if (lo is None or settle >= lo) and (hi is None or settle < hi):
            return i
    return None


def score_cohort(csv_path: Path) -> list[Scored]:
    out: list[Scored] = []
    skipped = defaultdict(int)
    with persistence.connect() as conn, conn.cursor() as cur, open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # --- cohort filter (LOCKED) ---
            if int(row["lead_day"]) != 0:
                continue
            age = row.get("latest_metar_age_min") or ""
            if age == "" or float(age) > METAR_AGE_MAX_MIN:
                skipped["age"] += 1
                continue
            mx = row.get("live_metar_max_f") or ""
            settle_s = row.get("final_cli_settlement_f") or ""
            if mx == "" or settle_s == "":
                skipped["obs_or_settle"] += 1
                continue
            local_hour, floored = _floor_local_time(row["local_time"])
            if not (HOUR_LO <= local_hour <= HOUR_HI):
                continue

            buckets = json.loads(row["bucket_set_json"])
            buckets = [
                {"ticker": b["ticker"], "lower_f": b["lower_f"], "upper_f": b["upper_f"]}
                for b in buckets
            ]
            buckets.sort(key=lambda b: (b["lower_f"] is not None, b["lower_f"]))
            if len(buckets) < 2:
                skipped["buckets"] += 1
                continue

            settle = float(settle_s)
            widx = _winner_idx(buckets, settle)
            if widx is None:
                skipped["no_winner"] += 1
                continue

            mkt_raw = json.loads(row["market_probs_json"])
            mkt = _normalize([(mkt_raw.get(b["ticker"]) or 0.0) for b in buckets])
            if mkt is None:
                skipped["market"] += 1
                continue

            station = row["station"]
            vdate = date.fromisoformat(row["valid_date"])
            rr_mean, rr_std, n = remaining_rise_stats(cur, station, vdate, floored)
            if rr_mean is None:
                skipped["climo"] += 1
                continue

            mu = float(mx) + rr_mean
            sigma = max(rr_std or 0.0, STD_FLOOR_F)
            obs = _normalize([_normal_mass(b["lower_f"], b["upper_f"], mu, sigma) for b in buckets])
            if obs is None:
                skipped["obs_dist"] += 1
                continue

            bdist = row.get("live_metar_boundary_distance_f") or ""
            boundary_le_half = bdist != "" and float(bdist) <= 0.5

            out.append(
                Scored(
                    station=station,
                    valid_date=vdate,
                    d_brier=_brier(obs, widx) - _brier(mkt, widx),
                    d_rps=_rps(obs, widx) - _rps(mkt, widx),
                    boundary_le_half=boundary_le_half,
                )
            )
    if skipped:
        print(f"[skips] {dict(skipped)}", file=sys.stderr)
    return out


def _summary(rows: list[Scored]) -> dict:
    if not rows:
        return {"n": 0, "stations": 0}
    db = [r.d_brier for r in rows]
    dr = [r.d_rps for r in rows]
    blo, bhi = _paired_ci(db)
    rlo, rhi = _paired_ci(dr)
    return {
        "n": len(rows),
        "stations": len({r.station for r in rows}),
        "d_brier": statistics.fmean(db),
        "d_brier_ci": (blo, bhi),
        "d_rps": statistics.fmean(dr),
        "d_rps_ci": (rlo, rhi),
    }


def _fmt(s: dict) -> str:
    if s.get("n", 0) == 0:
        return "n=0"
    blo, bhi = s["d_brier_ci"]
    rlo, rhi = s["d_rps_ci"]
    ci = lambda lo, hi: f"[{lo:+.4f},{hi:+.4f}]" if lo is not None else "[n/a]"
    return (
        f"n={s['n']:>5} st={s['stations']:>2} "
        f"dBrier={s['d_brier']:+.4f} {ci(blo,bhi)}  "
        f"dRPS={s['d_rps']:+.4f} {ci(rlo,rhi)}"
    )


def _ci_excludes_zero_negative(s: dict, metric: str) -> bool:
    lo, hi = s[f"d_{metric}_ci"]
    return lo is not None and hi < 0.0


def run(csv_path: Path, out_path: Path) -> None:
    rows = score_cohort(csv_path)
    if not rows:
        print("No cohort rows. Nothing to score.", file=sys.stderr)
        sys.exit(2)

    # chronological split: earlier 60% of dates = design, later 40% = held-out OOS.
    dates = sorted({r.valid_date for r in rows})
    cut = dates[int(len(dates) * (1 - HELD_OUT_FRAC))] if len(dates) > 1 else dates[0]
    design = [r for r in rows if r.valid_date < cut]
    held = [r for r in rows if r.valid_date >= cut]

    # held-out chronological sub-splits (for pass criterion 3)
    held_dates = sorted({r.valid_date for r in held})
    mid = held_dates[len(held_dates) // 2] if len(held_dates) > 1 else None
    sub_a = [r for r in held if mid is not None and r.valid_date < mid]
    sub_b = [r for r in held if mid is None or r.valid_date >= mid]

    held_s = _summary(held)
    per_station = {st: _summary([r for r in held if r.station == st]) for st in sorted({r.station for r in held})}
    neg_brier_stations = sum(1 for s in per_station.values() if s.get("n", 0) > 0 and s["d_brier"] < 0)
    neg_brier_subs = sum(1 for s in (sub_a, sub_b) if s and _summary(s)["d_brier"] < 0)

    # pass criteria (held-out PRIMARY cohort)
    crit = {
        "brier_neg_ci": _ci_excludes_zero_negative(held_s, "brier") if held_s.get("n") else False,
        "rps_neg_ci": _ci_excludes_zero_negative(held_s, "rps") if held_s.get("n") else False,
        "n>=100": held_s.get("n", 0) >= 100,
        "stations>=2": held_s.get("stations", 0) >= 2,
        ">=2 neg stations": neg_brier_stations >= 2,
        ">=2 neg subsplits": neg_brier_subs >= 2,
    }
    passed = all(crit.values())

    # boundary secondary cut (confirmatory only)
    held_bnd = _summary([r for r in held if r.boundary_le_half])
    held_far = _summary([r for r in held if not r.boundary_le_half])

    lines = []
    lines.append("# EXP-C2 Lead-0 Obs-Timing Nowcast — Results")
    lines.append("")
    lines.append(f"_generated {datetime.utcnow():%Y-%m-%d %H:%M} UTC_")
    lines.append("")
    lines.append("Implements `EXP_C2_NOWCAST_PREREGISTRATION.md` (LOCKED). Research-only; no trading change.")
    lines.append("Negative dBrier / dRPS = the obs-anchored nowcast BEATS the market.")
    lines.append("")
    lines.append("## Cohort")
    lines.append(f"- lead-0, local hour [{HOUR_LO},{HOUR_HI}], latest METAR age <= {METAR_AGE_MAX_MIN:g} min")
    lines.append(f"- total cohort events: {len(rows)} across {len({r.station for r in rows})} stations, {len(dates)} dates")
    lines.append(f"- chronological cut date: {cut} (design < cut <= held-out)")
    lines.append("")
    lines.append("## Primary result (market-relative, paired)")
    lines.append("")
    lines.append("| split | result |")
    lines.append("|---|---|")
    lines.append(f"| design (earlier 60%) | {_fmt(_summary(design))} |")
    lines.append(f"| **HELD-OUT (later 40%)** | **{_fmt(held_s)}** |")
    lines.append(f"| overall | {_fmt(_summary(rows))} |")
    lines.append("")
    lines.append("### Held-out per-station")
    lines.append("")
    lines.append("| station | result |")
    lines.append("|---|---|")
    for st, s in per_station.items():
        lines.append(f"| {st} | {_fmt(s)} |")
    lines.append("")
    lines.append("### Held-out chronological sub-splits")
    lines.append("")
    lines.append(f"- sub-A: {_fmt(_summary(sub_a))}")
    lines.append(f"- sub-B: {_fmt(_summary(sub_b))}")
    lines.append("")
    lines.append("### Secondary boundary cut (confirmatory only, held-out)")
    lines.append("")
    lines.append(f"- boundary <= 0.5F: {_fmt(held_bnd)}")
    lines.append(f"- boundary  > 0.5F: {_fmt(held_far)}")
    lines.append("")
    lines.append("## Pass criteria (held-out primary cohort)")
    lines.append("")
    for k, v in crit.items():
        lines.append(f"- [{'x' if v else ' '}] {k}")
    lines.append("")
    verdict = "PASS" if passed else "NO PASS"
    lines.append(f"## VERDICT: {verdict}")
    lines.append("")
    if passed:
        lines.append(
            "The obs-anchored nowcast beats the market in the pre-registered held-out cohort. "
            "This is a forecast-information candidate ONLY; it now requires the full "
            "`WEATHERBOT_PROMOTION_CRITERIA.md` path (production-like re-score, fresh OOS "
            "station-days, realistic fills) before any trading change."
        )
    else:
        lines.append(
            "The obs-anchored nowcast does NOT beat the market in the pre-registered held-out "
            "cohort. Per the locked decision rule, this closes the last edge-adjacent avenue: "
            "WeatherBot moves to observation-only analytics. No production change."
        )
    lines.append("")

    out_path.write_text("\n".join(lines))
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description="EXP-C2 lead-0 obs-timing nowcast cohort test (research-only).")
    ap.add_argument("--csv", required=True, help="path to the EXP-2026-009 forensics CSV")
    ap.add_argument("--out", default="research/reports/exp_c2_nowcast_results.md")
    args = ap.parse_args()
    run(Path(args.csv), Path(args.out))


if __name__ == "__main__":
    main()
