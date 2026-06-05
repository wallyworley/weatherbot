"""Market-relative forecast skill scorer.

This is the fitness function for the alpha hunt:

    skill_vs_market = 1 - model_brier / market_brier

Positive means our model beats the tradable Kalshi market price. Negative means
the market is the better forecaster and any apparent model edge is suspect.

The scorer picks one signal per bucket-market in each local-time horizon window
(default: last quote in the window), scores both our fair probability and the
market YES midpoint against settlement, and segments the result by station and
observable regimes.

Usage:
    python -m weather_bot.research.morning_market_skill --days 45
    python -m weather_bot.research.morning_market_skill --days 45 --min-n 10
"""
from __future__ import annotations

import argparse
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean
from typing import Callable

from weather_bot.data import persistence
from weather_bot.research.wind_direction_audit import degrees_to_octant, parse_wind_dir

_CAL_RAW_RE = re.compile(r"\bCAL\|raw=([0-9.]+)\|")


_REALIZED_SQL = """
COALESCE(
    NULLIF(km.payload->>'expiration_value','')::float,
    CASE WHEN km.var = 'TMAX_DAILY' THEN co.tmax_f
         WHEN km.var = 'TMIN_DAILY' THEN co.tmin_f END
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
class Window:
    name: str
    start: str
    end: str


@dataclass
class Row:
    ticker: str
    ts: object
    station: str
    valid_date: object
    var: str
    lower_f: float | None
    upper_f: float | None
    yes_win: int
    model_p: float
    raw_model_p: float | None
    market_p: float
    local_hour: int
    market_bucket: str
    wind_octant: str
    temp_vs_nbm_bucket: str
    model_vote_bucket: str
    risk_label: str
    boundary_bucket: str


def _bucket_prob(p: float) -> str:
    lo = int(min(9, max(0, math.floor(p * 10)))) * 10
    return f"{lo:02d}-{lo + 10:02d}%"


def _bucket_temp_delta(delta: float | None) -> str:
    if delta is None:
        return "temp_vs_nbm:missing"
    if delta <= -6:
        return "temp_vs_nbm:<=-6F"
    if delta <= -3:
        return "temp_vs_nbm:-6..-3F"
    if delta <= -1:
        return "temp_vs_nbm:-3..-1F"
    if delta < 1:
        return "temp_vs_nbm:-1..+1F"
    if delta < 3:
        return "temp_vs_nbm:+1..+3F"
    if delta < 6:
        return "temp_vs_nbm:+3..+6F"
    return "temp_vs_nbm:>=+6F"


def _bucket_boundary_mass(v: float | None) -> str:
    if v is None:
        return "boundary:missing"
    if v < 0.25:
        return "boundary:<0.25"
    if v < 0.50:
        return "boundary:0.25-0.50"
    if v < 0.75:
        return "boundary:0.50-0.75"
    return "boundary:>=0.75"


def _model_vote_bucket(row: dict) -> str:
    votes = row.get("model_votes") or {}
    try:
        n_yes = int(votes.get("n_yes") or 0)
        n_no = int(votes.get("n_no") or 0)
    except (TypeError, ValueError):
        return "votes:missing"
    return f"votes_yes:{n_yes}_no:{n_no}"


def _risk_label(row: dict) -> str:
    risk = row.get("reversal_risk") or {}
    label = risk.get("label")
    return f"risk:{label}" if label else "risk:missing"


def _boundary_bucket(row: dict) -> str:
    risk = row.get("reversal_risk") or {}
    comp = risk.get("components") or {}
    boundary = comp.get("boundary_mass") or {}
    value = boundary.get("value")
    try:
        value_f = float(value) if value is not None else None
    except (TypeError, ValueError):
        value_f = None
    return _bucket_boundary_mass(value_f)


def _wind_octant(raw: str | None) -> str:
    d = parse_wind_dir(raw or "")
    return f"wind:{degrees_to_octant(d)}" if d is not None else "wind:missing"


def _raw_prob_from_notes(notes: str | None) -> float | None:
    if not notes:
        return None
    m = _CAL_RAW_RE.search(notes)
    if not m:
        return None
    try:
        raw = float(m.group(1))
    except ValueError:
        return None
    return min(1.0, max(0.0, raw))


def _fetch_window(window: Window, days: int, pick: str, var: str | None) -> list[Row]:
    order = "DESC" if pick == "last" else "ASC"
    var_filter = "AND km.var = %(var)s" if var else ""
    sql = f"""
        WITH candidates AS (
            SELECT DISTINCT ON (s.ticker)
                   s.ticker,
                   s.ts,
                   s.fair_prob::float AS model_p,
                   s.notes,
                   ((s.market_ask::float + s.market_bid::float) / 2.0) AS market_p,
                   s.model_votes,
                   s.reversal_risk,
                   km.station,
                   km.valid_date,
                   km.var,
                   km.lower_f::float AS lower_f,
                   km.upper_f::float AS upper_f,
                   {_YES_WIN_SQL} AS yes_win,
                   EXTRACT(HOUR FROM (s.ts AT TIME ZONE st.tz))::int AS local_hour,
                   mo.raw AS metar_raw,
                   mo.temp_f AS obs_temp_f,
                   nbm.value AS nbm_p50
              FROM signal s
              JOIN kalshi_market km ON km.ticker = s.ticker
              JOIN stations st ON st.code = km.station
              LEFT JOIN cli_obs co
                     ON co.station = km.station AND co.local_date = km.valid_date
              LEFT JOIN LATERAL (
                    SELECT m.raw, m.temp_f, m.obs_time
                      FROM metar_obs m
                     WHERE m.station = km.station
                       AND m.obs_time <= s.ts
                       AND m.obs_time >= s.ts - INTERVAL '3 hours'
                     ORDER BY m.obs_time DESC
                     LIMIT 1
              ) mo ON true
              LEFT JOIN LATERAL (
                    SELECT pf.value
                      FROM prob_forecast pf
                     WHERE pf.station = km.station
                       AND pf.valid_date = km.valid_date
                       AND pf.var = km.var
                       AND pf.model = 'NBM_QMD'
                       AND pf.percentile = 50
                       AND pf.run_time <= s.ts
                     ORDER BY pf.run_time DESC
                     LIMIT 1
              ) nbm ON true
             WHERE km.valid_date >= CURRENT_DATE - (%(days)s || ' days')::interval
               AND km.valid_date < CURRENT_DATE
               {var_filter}
               AND s.fair_prob IS NOT NULL
               AND s.market_ask IS NOT NULL
               AND s.market_bid IS NOT NULL
               AND (s.ts AT TIME ZONE st.tz)::time >= %(start)s::time
               AND (s.ts AT TIME ZONE st.tz)::time <  %(end)s::time
             ORDER BY s.ticker, s.ts {order}
        )
        SELECT *
          FROM candidates
         WHERE yes_win IS NOT NULL
           AND model_p BETWEEN 0 AND 1
           AND market_p BETWEEN 0 AND 1
    """
    params = {"days": days, "start": window.start, "end": window.end}
    if var:
        params["var"] = var
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    out: list[Row] = []
    for r in rows:
        temp_delta = None
        if r.get("obs_temp_f") is not None and r.get("nbm_p50") is not None:
            temp_delta = float(r["obs_temp_f"]) - float(r["nbm_p50"])
        out.append(Row(
            ticker=r["ticker"],
            ts=r["ts"],
            station=r["station"],
            valid_date=r["valid_date"],
            var=r["var"],
            lower_f=float(r["lower_f"]) if r.get("lower_f") is not None else None,
            upper_f=float(r["upper_f"]) if r.get("upper_f") is not None else None,
            yes_win=int(r["yes_win"]),
            model_p=float(r["model_p"]),
            raw_model_p=_raw_prob_from_notes(r.get("notes")),
            market_p=float(r["market_p"]),
            local_hour=int(r["local_hour"]),
            market_bucket=f"market:{_bucket_prob(float(r['market_p']))}",
            wind_octant=_wind_octant(r.get("metar_raw")),
            temp_vs_nbm_bucket=_bucket_temp_delta(temp_delta),
            model_vote_bucket=_model_vote_bucket(r),
            risk_label=_risk_label(r),
            boundary_bucket=_boundary_bucket(r),
        ))
    return out


def _brier_decomposition(
    rows: list[Row],
    prob: Callable[[Row], float | None],
    bins: int = 10,
) -> dict | None:
    usable = [(p, r.yes_win) for r in rows if (p := prob(r)) is not None]
    if not usable:
        return None
    n = len(usable)
    base = mean(y for _, y in usable)
    uncertainty = base * (1.0 - base)
    by_bin: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, y in usable:
        b = min(bins - 1, max(0, int(float(p) * bins)))
        by_bin[b].append((float(p), y))

    reliability = 0.0
    resolution = 0.0
    for xs in by_bin.values():
        w = len(xs) / n
        p_bar = mean(p for p, _ in xs)
        y_bar = mean(y for _, y in xs)
        reliability += w * (p_bar - y_bar) ** 2
        resolution += w * (y_bar - base) ** 2

    brier = mean((p - y) ** 2 for p, y in usable)
    return {
        "n": n,
        "brier": brier,
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": uncertainty,
        "decomp_brier": reliability - resolution + uncertainty,
    }


def _metrics(rows: list[Row]) -> dict | None:
    if not rows:
        return None
    n = len(rows)
    model_brier = mean((r.model_p - r.yes_win) ** 2 for r in rows)
    market_brier = mean((r.market_p - r.yes_win) ** 2 for r in rows)
    skill = 1.0 - model_brier / market_brier if market_brier > 0 else float("nan")
    base = mean(r.yes_win for r in rows)
    model_decomp = _brier_decomposition(rows, lambda r: r.model_p)
    market_decomp = _brier_decomposition(rows, lambda r: r.market_p)
    return {
        "n": n,
        "model_brier": model_brier,
        "market_brier": market_brier,
        "skill": skill,
        "base": base,
        "model_mean": mean(r.model_p for r in rows),
        "market_mean": mean(r.market_p for r in rows),
        "edge_abs": mean(abs(r.model_p - r.market_p) for r in rows),
        "model_rel": model_decomp["reliability"] if model_decomp else float("nan"),
        "model_res": model_decomp["resolution"] if model_decomp else float("nan"),
        "market_rel": market_decomp["reliability"] if market_decomp else float("nan"),
        "market_res": market_decomp["resolution"] if market_decomp else float("nan"),
    }


def _rps_by_market(rows: list[Row], prob: Callable[[Row], float | None]) -> dict | None:
    """Mean normalized ordinal RPS over market-days.

    Per (station, valid_date) we order the captured buckets by temperature
    (lower_f), normalize the chosen per-bucket YES probabilities into a
    distribution, and score the ordinal Ranked Probability Score against the
    one-hot winning bucket. RPS rewards being *near* the right bucket — unlike
    per-bucket Brier, which penalizes a 1-bucket miss like a 5-bucket miss.

    Comparing RPS-skill to Brier-skill answers: are we under-confident around
    the right center (RPS-skill >> Brier-skill) or centered wrong (similar)?
    """
    groups: dict[tuple, list[Row]] = defaultdict(list)
    for r in rows:
        groups[(r.station, str(r.valid_date), r.var)].append(r)

    rps_vals: list[float] = []
    skipped = 0
    for g in groups.values():
        if len(g) < 3:                      # too few buckets to form an ordinal CDF
            skipped += 1
            continue
        g_sorted = sorted(
            g, key=lambda r: (r.lower_f if r.lower_f is not None else float("-inf")))
        winners = [i for i, r in enumerate(g_sorted) if r.yes_win == 1]
        if len(winners) != 1:               # winning bucket not captured (or ambiguous)
            skipped += 1
            continue
        wi = winners[0]
        ps = [max(0.0, prob(r) or 0.0) for r in g_sorted]
        tot = sum(ps)
        if tot <= 0:
            skipped += 1
            continue
        ps = [p / tot for p in ps]          # normalize captured buckets into a distribution
        k_n = len(ps)
        cum_f = cum_o = rps = 0.0
        for k in range(k_n):
            cum_f += ps[k]
            cum_o += 1.0 if k == wi else 0.0
            rps += (cum_f - cum_o) ** 2
        rps_vals.append(rps / (k_n - 1))    # normalized to [0,1], comparable across K
    if not rps_vals:
        return None
    return {"n_markets": len(rps_vals), "skipped": skipped, "rps": mean(rps_vals)}


def _rps_metrics(rows: list[Row]) -> dict | None:
    model = _rps_by_market(rows, lambda r: r.model_p)
    market = _rps_by_market(rows, lambda r: r.market_p)
    if not model or not market:
        return None
    skill = 1.0 - model["rps"] / market["rps"] if market["rps"] > 0 else float("nan")
    return {
        "n_markets": model["n_markets"], "skipped": model["skipped"],
        "model_rps": model["rps"], "market_rps": market["rps"], "rps_skill": skill,
    }


def _print_rps(rows: list[Row], brier_skill: float) -> None:
    r = _rps_metrics(rows)
    if not r:
        return
    print("\nOrdinal RPS (distance-aware: rewards being near the right bucket)")
    print("----------------------------------------------------------------")
    print(
        f"  market-days={r['n_markets']} (skipped {r['skipped']})  "
        f"model_RPS={r['model_rps']:.4f}  market_RPS={r['market_rps']:.4f}  "
        f"RPS_skill={r['rps_skill']:+.2f}   (Brier_skill={brier_skill:+.2f})"
    )
    gap = r["rps_skill"] - brier_skill
    if gap >= 0.10:
        verdict = ("RPS_skill >> Brier_skill: we're CENTERED ~RIGHT but under-confident/imprecise "
                   "→ lever = sharpen the distribution (spread/shape resolution), not the mean.")
    elif gap <= -0.05:
        verdict = ("RPS_skill < Brier_skill: our misses land FARTHER from the truth than the market's "
                   "→ definitively CENTERED WRONG → lever = fix the meteorological mean "
                   "(better inputs: ensemble/regime). Sharpening the distribution will not help.")
    elif gap <= 0.03:
        verdict = ("RPS_skill ~ Brier_skill: ordinal closeness doesn't rescue us → CENTERED WRONG "
                   "→ lever = fix the meteorological mean (better inputs: ensemble/regime).")
    else:
        verdict = ("Mixed: partial center error + under-confidence — segment by station to localize "
                   "before committing a lever.")
    print(f"  → {verdict}")


def _print_metric(label: str, m: dict) -> None:
    print(
        f"{label:<34} n={m['n']:>5}  "
        f"model={m['model_brier']:.4f}  market={m['market_brier']:.4f}  "
        f"skill={m['skill']:+.2f}  "
        f"base={m['base']:.3f} model_p={m['model_mean']:.3f} "
        f"market_p={m['market_mean']:.3f} |gap|={m['edge_abs']:.3f}  "
        f"REL mkt/model={m['market_rel']:.4f}/{m['model_rel']:.4f}  "
        f"RES mkt/model={m['market_res']:.4f}/{m['model_res']:.4f}"
    )


def _print_raw_vs_calibrated(rows: list[Row]) -> None:
    raw_rows = [r for r in rows if r.raw_model_p is not None]
    if not raw_rows:
        return
    raw = _brier_decomposition(raw_rows, lambda r: r.raw_model_p)
    cal = _brier_decomposition(raw_rows, lambda r: r.model_p)
    mkt = _brier_decomposition(raw_rows, lambda r: r.market_p)
    if not raw or not cal or not mkt:
        return
    raw_skill = 1.0 - raw["brier"] / mkt["brier"] if mkt["brier"] > 0 else float("nan")
    cal_skill = 1.0 - cal["brier"] / mkt["brier"] if mkt["brier"] > 0 else float("nan")
    print("\nRaw vs calibrated model probability (rows with CAL|raw=...)")
    print("----------------------------------------------------------")
    print(f"{'source':<12}{'n':>6}{'brier':>10}{'skill':>9}{'REL':>10}{'RES':>10}{'UNC':>10}")
    for label, d, skill in (
        ("market", mkt, 0.0),
        ("raw", raw, raw_skill),
        ("calibrated", cal, cal_skill),
    ):
        print(
            f"{label:<12}{d['n']:>6}{d['brier']:>10.4f}{skill:>+9.2f}"
            f"{d['reliability']:>10.4f}{d['resolution']:>10.4f}{d['uncertainty']:>10.4f}"
        )
    print(
        "  Decomposition uses decile bins: Brier = reliability - resolution + uncertainty.\n"
        "  If raw RES >> calibrated RES with similar/worse REL, the calibrator is suppressing information."
    )


def _groups(rows: list[Row], key: Callable[[Row], str]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def _print_groups(title: str, rows: list[Row], key: Callable[[Row], str], min_n: int, limit: int) -> None:
    scored = []
    for label, rs in _groups(rows, key).items():
        if len(rs) < min_n:
            continue
        m = _metrics(rs)
        if m:
            scored.append((label, m))
    if not scored:
        return
    print(f"\n{title}")
    print("-" * len(title))
    for label, m in sorted(scored, key=lambda x: x[1]["skill"], reverse=True)[:limit]:
        _print_metric(label, m)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--pick", choices=("first", "last"), default="last")
    ap.add_argument("--var", choices=("TMAX_DAILY", "TMIN_DAILY"), default="TMAX_DAILY")
    ap.add_argument("--min-n", type=int, default=20)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--morning-start", default="06:00")
    ap.add_argument("--morning-end", default="09:00")
    ap.add_argument("--settlement-start", default="17:00")
    ap.add_argument("--settlement-end", default="18:00")
    args = ap.parse_args()

    windows = [
        Window("morning", args.morning_start, args.morning_end),
        Window("near_settlement", args.settlement_start, args.settlement_end),
    ]
    for window in windows:
        rows = _fetch_window(window, args.days, args.pick, args.var)
        print("\n" + "=" * 88)
        print(
            f"{window.name.upper()} MARKET-RELATIVE SKILL "
            f"({window.start}-{window.end} local, {args.var}, last {args.days}d, pick={args.pick})"
        )
        print("=" * 88)
        overall = _metrics(rows)
        if not overall:
            print("(no settled scored rows)")
            continue
        _print_metric("OVERALL", overall)
        print("skill = 1 - model_brier / market_brier; positive means model beats market.")
        _print_rps(rows, overall["skill"])
        _print_raw_vs_calibrated(rows)

        _print_groups("By Station", rows, lambda r: r.station, max(5, args.min_n // 2), args.limit)
        _print_groups("By Station + Morning Wind", rows, lambda r: f"{r.station}/{r.wind_octant}", 5, args.limit)
        _print_groups("By Station + Temp vs NBM", rows, lambda r: f"{r.station}/{r.temp_vs_nbm_bucket}", 5, args.limit)
        _print_groups("By Market Probability Bucket", rows, lambda r: r.market_bucket, args.min_n, args.limit)
        _print_groups("By Model Vote Split", rows, lambda r: r.model_vote_bucket, args.min_n, args.limit)
        _print_groups("By Reversal Risk Label", rows, lambda r: r.risk_label, args.min_n, args.limit)
        _print_groups("By Boundary Mass", rows, lambda r: r.boundary_bucket, args.min_n, args.limit)


if __name__ == "__main__":
    main()
