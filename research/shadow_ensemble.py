"""Shadow-only point-ensemble replay.

This does not alter trading. It asks: if NBM/HRRR/GFS/ECMWF point forecasts
were blended into a simple temperature distribution, would the resulting bucket
probabilities have been better calibrated than the current signal probabilities?
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from psycopg.rows import dict_row

from weather_bot.config import BANKROLL_USD
from weather_bot.data import persistence
from weather_bot.strategy import ev


MODEL_DEFAULT_WEIGHTS = {
    "lead0": {"NBM": 0.30, "HRRR": 0.40, "GFS": 0.15, "ECMWF": 0.15},
    "lead1p": {"NBM": 0.35, "GFS": 0.30, "ECMWF": 0.35},
}


@dataclass(frozen=True)
class ShadowRow:
    signal_id: int
    ticker: str
    ts: datetime
    station: str
    valid_date: date
    lead_day: int
    lower_f: float | None
    upper_f: float | None
    yes_won: int
    original_p_yes: float
    shadow_p_yes: float | None
    original_brier: float
    shadow_brier: float | None
    shadow_mean_f: float | None
    shadow_sigma_f: float | None
    weights: str
    shadow_action: str | None
    shadow_side: str | None
    shadow_pnl: float | None


def normal_cdf(x: float, mean: float, sigma: float) -> float:
    sigma = max(float(sigma), 0.25)
    return 0.5 * (1.0 + math.erf((float(x) - mean) / (sigma * math.sqrt(2.0))))


def normal_prob_between(mean: float, sigma: float, lo: float | None, hi: float | None) -> float:
    lo_cdf = 0.0 if lo is None else normal_cdf(lo, mean, sigma)
    hi_cdf = 1.0 if hi is None else normal_cdf(hi, mean, sigma)
    return max(0.0, min(1.0, hi_cdf - lo_cdf))


def normalize_weights(base: dict[str, float], points: dict[str, float | None]) -> dict[str, float]:
    available = {m: w for m, w in base.items() if points.get(m) is not None and w > 0}
    total = sum(available.values())
    if total <= 0:
        return {}
    return {m: w / total for m, w in available.items()}


def _signal_rows(days_back: int, limit: int | None = None, per_group_limit: int = 200) -> list[dict]:
    sql = """
    WITH base AS (
        SELECT s.id AS signal_id, s.ts, s.ticker, s.fair_prob AS original_p_yes,
               s.market_ask, s.market_bid, s.action AS original_action,
               km.station, km.valid_date, km.lower_f, km.upper_f,
               GREATEST(0, (km.valid_date - (s.ts AT TIME ZONE st.tz)::date)::int) AS lead_day,
               COALESCE(cli.tmax_f, obs.tmax_f) AS obs_tmax
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
          JOIN stations st ON st.code = km.station
          LEFT JOIN cli_obs cli ON cli.station = km.station AND cli.local_date = km.valid_date
          LEFT JOIN daily_obs obs ON obs.station = km.station AND obs.local_date = km.valid_date
         WHERE km.var = 'TMAX_DAILY'
           AND km.valid_date >= CURRENT_DATE - (%(days_back)s || ' days')::interval
           AND km.valid_date < CURRENT_DATE
           AND s.fair_prob IS NOT NULL
           AND COALESCE(cli.tmax_f, obs.tmax_f) IS NOT NULL
    ),
    ranked AS (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY station, lead_day
                   ORDER BY ts DESC
               ) AS rn
          FROM base
    )
    SELECT b.*,
           nbm.p25 AS nbm_p25, nbm.p50 AS nbm_p50, nbm.p75 AS nbm_p75,
           hrrr.pred AS hrrr_tmax,
           gfs.pred AS gfs_tmax,
           ecmwf.pred AS ecmwf_tmax
      FROM ranked b
      LEFT JOIN LATERAL (
          SELECT MAX(value) FILTER (WHERE percentile=25) AS p25,
                 MAX(value) FILTER (WHERE percentile=50) AS p50,
                 MAX(value) FILTER (WHERE percentile=75) AS p75
            FROM prob_forecast pf
           WHERE pf.station = b.station
             AND pf.valid_date = b.valid_date
             AND pf.var = 'TMAX_DAILY'
             AND pf.run_time = (
                 SELECT MAX(run_time)
                   FROM prob_forecast pf2
                  WHERE pf2.station = b.station
                    AND pf2.valid_date = b.valid_date
                    AND pf2.var = 'TMAX_DAILY'
                    AND pf2.run_time <= b.ts
             )
      ) nbm ON true
      LEFT JOIN LATERAL (
          SELECT MAX(value)::float AS pred
            FROM det_forecast df
            JOIN stations st2 ON st2.code = df.station
           WHERE df.station = b.station
             AND df.model = 'HRRR'
             AND df.var = 'TMP_2M'
             AND (df.valid_time AT TIME ZONE st2.tz)::date = b.valid_date
             AND df.run_time = (
                 SELECT MAX(df2.run_time)
                   FROM det_forecast df2
                   JOIN stations st3 ON st3.code = df2.station
                  WHERE df2.station = b.station
                    AND df2.model = 'HRRR'
                    AND df2.var = 'TMP_2M'
                    AND (df2.valid_time AT TIME ZONE st3.tz)::date = b.valid_date
                    AND df2.run_time <= b.ts
             )
      ) hrrr ON true
      LEFT JOIN LATERAL (
          SELECT MAX(value)::float AS pred
            FROM det_forecast df
            JOIN stations st2 ON st2.code = df.station
           WHERE df.station = b.station
             AND df.model = 'GFS'
             AND df.var = 'TMP_2M'
             AND (df.valid_time AT TIME ZONE st2.tz)::date = b.valid_date
             AND df.run_time = (
                 SELECT MAX(df2.run_time)
                   FROM det_forecast df2
                   JOIN stations st3 ON st3.code = df2.station
                  WHERE df2.station = b.station
                    AND df2.model = 'GFS'
                    AND df2.var = 'TMP_2M'
                    AND (df2.valid_time AT TIME ZONE st3.tz)::date = b.valid_date
                    AND df2.run_time <= b.ts
             )
      ) gfs ON true
      LEFT JOIN LATERAL (
          SELECT MAX(value)::float AS pred
            FROM det_forecast df
            JOIN stations st2 ON st2.code = df.station
           WHERE df.station = b.station
             AND df.model = 'ECMWF'
             AND df.var = 'TMP_2M'
             AND (df.valid_time AT TIME ZONE st2.tz)::date = b.valid_date
             AND df.run_time = (
                 SELECT MAX(df2.run_time)
                   FROM det_forecast df2
                   JOIN stations st3 ON st3.code = df2.station
                  WHERE df2.station = b.station
                    AND df2.model = 'ECMWF'
                    AND df2.var = 'TMP_2M'
                    AND (df2.valid_time AT TIME ZONE st3.tz)::date = b.valid_date
                    AND df2.run_time <= b.ts
             )
      ) ecmwf ON true
     WHERE b.rn <= %(per_group_limit)s
     ORDER BY b.station, b.lead_day, b.ts DESC
    """
    if limit:
        sql += "\n LIMIT %(limit)s"
    params = {"days_back": days_back, "limit": limit, "per_group_limit": per_group_limit}
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _shadow_distribution(row: dict) -> tuple[float | None, float | None, float | None, dict[str, float]]:
    points = {
        "NBM": float(row["nbm_p50"]) if row.get("nbm_p50") is not None else None,
        "HRRR": float(row["hrrr_tmax"]) if row.get("hrrr_tmax") is not None else None,
        "GFS": float(row["gfs_tmax"]) if row.get("gfs_tmax") is not None else None,
        "ECMWF": float(row["ecmwf_tmax"]) if row.get("ecmwf_tmax") is not None else None,
    }
    base = MODEL_DEFAULT_WEIGHTS["lead0"] if int(row["lead_day"]) == 0 else MODEL_DEFAULT_WEIGHTS["lead1p"]
    weights = normalize_weights(base, points)
    if not weights:
        return None, None, None, {}

    mean = sum(weights[m] * points[m] for m in weights)
    model_vals = [points[m] for m in weights]
    model_spread = statistics.pstdev(model_vals) if len(model_vals) >= 2 else 0.0
    nbm_sigma = None
    if row.get("nbm_p25") is not None and row.get("nbm_p75") is not None:
        nbm_sigma = max(0.25, (float(row["nbm_p75"]) - float(row["nbm_p25"])) / 1.349)
    sigma_parts = [v for v in [nbm_sigma, model_spread * 1.35] if v is not None]
    sigma = max(1.5, *sigma_parts) if sigma_parts else 2.5
    p_yes = normal_prob_between(mean, sigma, row.get("lower_f"), row.get("upper_f"))
    return p_yes, mean, sigma, weights


def _yes_won(row: dict) -> int:
    obs = float(row["obs_tmax"])
    lo = row.get("lower_f")
    hi = row.get("upper_f")
    return int((lo is None or obs >= float(lo)) and (hi is None or obs < float(hi)))


def _simulated_shadow_pnl(row: dict, p_yes: float | None) -> tuple[str | None, str | None, float | None]:
    if p_yes is None:
        return None, None, None
    ev_logger = logging.getLogger("weather_bot.strategy.ev")
    old_disabled = ev_logger.disabled
    ev_logger.disabled = True
    try:
        sig = ev.evaluate(
            row["ticker"],
            p_yes,
            float(row["market_ask"]) if row.get("market_ask") is not None else None,
            float(row["market_bid"]) if row.get("market_bid") is not None else None,
            bankroll=BANKROLL_USD,
        )
    finally:
        ev_logger.disabled = old_disabled
    if sig.action != "OPEN":
        return sig.action, sig.side, None
    price = sig.market_ask if sig.side == "YES" else (1.0 - sig.market_bid if sig.market_bid is not None else None)
    if price is None or price <= 0 or price >= 1:
        return sig.action, sig.side, None
    contracts = max(1, int(sig.size_usd / price))
    fee = ev.fee_for_order(price, contracts)
    yes_won = _yes_won(row)
    won = bool(yes_won) if sig.side == "YES" else not bool(yes_won)
    payout = 1.0 if won else 0.0
    return sig.action, sig.side, (payout - price) * contracts - fee


def replay(days_back: int = 30, limit: int | None = None, per_group_limit: int = 200) -> list[ShadowRow]:
    out: list[ShadowRow] = []
    for row in _signal_rows(days_back=days_back, limit=limit, per_group_limit=per_group_limit):
        yes_won = _yes_won(row)
        original_p = float(row["original_p_yes"])
        shadow_p, mean, sigma, weights = _shadow_distribution(row)
        action, side, pnl = _simulated_shadow_pnl(row, shadow_p)
        out.append(
            ShadowRow(
                signal_id=int(row["signal_id"]),
                ticker=row["ticker"],
                ts=row["ts"],
                station=row["station"],
                valid_date=row["valid_date"],
                lead_day=int(row["lead_day"]),
                lower_f=float(row["lower_f"]) if row.get("lower_f") is not None else None,
                upper_f=float(row["upper_f"]) if row.get("upper_f") is not None else None,
                yes_won=yes_won,
                original_p_yes=original_p,
                shadow_p_yes=shadow_p,
                original_brier=(original_p - yes_won) ** 2,
                shadow_brier=None if shadow_p is None else (shadow_p - yes_won) ** 2,
                shadow_mean_f=mean,
                shadow_sigma_f=sigma,
                weights=",".join(f"{m}:{w:.2f}" for m, w in sorted(weights.items())),
                shadow_action=action,
                shadow_side=side,
                shadow_pnl=pnl,
            )
        )
    return out


def _metrics(rows: list[ShadowRow]) -> dict:
    usable = [r for r in rows if r.shadow_brier is not None]
    shadow_opens = [r for r in usable if r.shadow_action == "OPEN" and r.shadow_pnl is not None]
    return {
        "n": len(usable),
        "original_brier": statistics.fmean(r.original_brier for r in usable) if usable else None,
        "shadow_brier": statistics.fmean(r.shadow_brier for r in usable) if usable else None,
        "shadow_open_n": len(shadow_opens),
        "shadow_pnl": sum(float(r.shadow_pnl) for r in shadow_opens),
    }


def write_csv(rows: list[ShadowRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def render_markdown(rows: list[ShadowRow], days_back: int) -> str:
    m = _metrics(rows)
    lines = [
        f"# Shadow Ensemble Replay — {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Window: last {days_back} completed valid dates. This is research-only; it does not affect trading.",
        "Shadow P&L is an unconstrained replay of every eligible signal, so use Brier/calibration first and dollars only as a rough stress test.",
        "",
        "## Overall",
        "",
        "| n | original Brier | shadow Brier | delta | shadow opens | shadow P&L |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    if m["n"]:
        delta = m["shadow_brier"] - m["original_brier"]
        lines.append(
            f"| {m['n']} | {m['original_brier']:.4f} | {m['shadow_brier']:.4f} | {delta:+.4f} | "
            f"{m['shadow_open_n']} | ${m['shadow_pnl']:+.2f} |"
        )
    else:
        lines.append("| 0 | — | — | — | 0 | — |")

    groups: dict[tuple[str, int], list[ShadowRow]] = defaultdict(list)
    for row in rows:
        groups[(row.station, row.lead_day)].append(row)
    lines.extend([
        "",
        "## By station / lead",
        "",
        "| station | lead | n | original Brier | shadow Brier | delta | shadow P&L |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for (station, lead), vals in sorted(groups.items()):
        gm = _metrics(vals)
        if not gm["n"]:
            continue
        delta = gm["shadow_brier"] - gm["original_brier"]
        lines.append(
            f"| {station} | {lead} | {gm['n']} | {gm['original_brier']:.4f} | "
            f"{gm['shadow_brier']:.4f} | {delta:+.4f} | ${gm['shadow_pnl']:+.2f} |"
        )

    lines.extend([
        "",
        "## Promotion rule",
        "",
        "- Keep this shadow-only until it beats original Brier on at least 50 settled signals.",
        "- Then replay fixed-size P&L and reliability bins before using it in `main.py`.",
    ])
    return "\n".join(lines) + "\n"


def run(
    days_back: int = 30,
    out_dir: Path = Path("research/reports"),
    limit: int | None = None,
    per_group_limit: int = 200,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = replay(days_back=days_back, limit=limit, per_group_limit=per_group_limit)
    stem = f"shadow_ensemble_{date.today()}"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"
    write_csv(rows, csv_path)
    md_path.write_text(render_markdown(rows, days_back))
    return {"rows": len(rows), "csv_path": str(csv_path), "report_path": str(md_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--per-group-limit", type=int, default=200,
                        help="Max signals per station/lead bucket to replay.")
    args = parser.parse_args()
    result = run(days_back=args.days_back, limit=args.limit, per_group_limit=args.per_group_limit)
    print(Path(result["report_path"]).read_text())
