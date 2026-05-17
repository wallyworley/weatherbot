"""Forecast-update lag and probability-edge replay.

This asks whether logged signal edges are followed by market repricing after
fresh forecast updates. Positive signed movement means the YES market moved in
the direction implied by our fair probability.
"""
from __future__ import annotations

import argparse
import csv
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from psycopg.rows import dict_row

from weather_bot.data import persistence


PROB_ERROR_FLOOR = 0.05


@dataclass(frozen=True)
class LagRow:
    signal_id: int
    ticker: str
    ts: datetime
    station: str | None
    valid_date: date | None
    lead_day: int
    action: str
    fair_prob: float
    yes_mid: float
    prob_edge: float
    abs_prob_edge: float
    edge_z: float
    freshest_source: str
    freshest_age_min: float | None
    m2m_15m: float | None
    m2m_30m: float | None
    m2m_60m: float | None
    signed_m2m_15m: float | None
    signed_m2m_30m: float | None
    signed_m2m_60m: float | None


def edge_z_score(prob_edge: float, error_floor: float = PROB_ERROR_FLOOR) -> float:
    return abs(float(prob_edge)) / max(float(error_floor), 1e-9)


def _signed_move(prob_edge: float, future_mid: float | None, current_mid: float) -> float | None:
    if future_mid is None or prob_edge == 0:
        return None
    direction = 1.0 if prob_edge > 0 else -1.0
    return direction * (float(future_mid) - float(current_mid))


def _freshest_source(ages: dict[str, float | None]) -> tuple[str, float | None]:
    available = [(source, age) for source, age in ages.items() if age is not None and age >= 0]
    if not available:
        return "unknown", None
    return min(available, key=lambda item: item[1])


def collect_rows(days_back: int = 30, limit: int = 2500) -> list[LagRow]:
    sql = """
    WITH base AS (
        SELECT s.id AS signal_id, s.ticker, s.ts, km.station, km.valid_date, s.action,
               s.fair_prob::float AS fair_prob,
               ((s.market_ask + s.market_bid) / 2.0)::float AS yes_mid,
               GREATEST(0, (km.valid_date - (s.ts AT TIME ZONE st.tz)::date)::int) AS lead_day
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
          JOIN stations st ON st.code = km.station
         WHERE s.ts >= now() - (%(days_back)s || ' days')::interval
           AND s.market_ask IS NOT NULL
           AND s.market_bid IS NOT NULL
           AND s.fair_prob IS NOT NULL
         ORDER BY s.ts DESC
         LIMIT %(limit)s
    )
    SELECT b.*,
           ((ms15.yes_ask + ms15.yes_bid) / 2.0)::float AS mid_15m,
           ((ms30.yes_ask + ms30.yes_bid) / 2.0)::float AS mid_30m,
           ((ms60.yes_ask + ms60.yes_bid) / 2.0)::float AS mid_60m,
           EXTRACT(EPOCH FROM (b.ts - nbm.last_ingested_at)) / 60.0 AS age_nbm_min,
           EXTRACT(EPOCH FROM (b.ts - hrrr.last_ingested_at)) / 60.0 AS age_hrrr_min,
           EXTRACT(EPOCH FROM (b.ts - gfs.last_ingested_at)) / 60.0 AS age_gfs_min,
           EXTRACT(EPOCH FROM (b.ts - ecmwf.last_ingested_at)) / 60.0 AS age_ecmwf_min,
           EXTRACT(EPOCH FROM (b.ts - ens.last_ingested_at)) / 60.0 AS age_ensemble_min
      FROM base b
      LEFT JOIN LATERAL (
          SELECT *
            FROM market_snapshot ms
           WHERE ms.ticker = b.ticker AND ms.ts >= b.ts + interval '15 minutes'
             AND ms.yes_ask IS NOT NULL AND ms.yes_bid IS NOT NULL
           ORDER BY ms.ts ASC
           LIMIT 1
      ) ms15 ON true
      LEFT JOIN LATERAL (
          SELECT *
            FROM market_snapshot ms
           WHERE ms.ticker = b.ticker AND ms.ts >= b.ts + interval '30 minutes'
             AND ms.yes_ask IS NOT NULL AND ms.yes_bid IS NOT NULL
           ORDER BY ms.ts ASC
           LIMIT 1
      ) ms30 ON true
      LEFT JOIN LATERAL (
          SELECT *
            FROM market_snapshot ms
           WHERE ms.ticker = b.ticker AND ms.ts >= b.ts + interval '60 minutes'
             AND ms.yes_ask IS NOT NULL AND ms.yes_bid IS NOT NULL
           ORDER BY ms.ts ASC
           LIMIT 1
      ) ms60 ON true
      LEFT JOIN LATERAL (
          SELECT MAX(ingested_at) AS last_ingested_at
            FROM prob_forecast pf
           WHERE pf.station = b.station AND pf.valid_date = b.valid_date
             AND pf.ingested_at <= b.ts
      ) nbm ON true
      LEFT JOIN LATERAL (
          SELECT MAX(ingested_at) AS last_ingested_at
            FROM det_forecast df
            JOIN stations st2 ON st2.code = df.station
           WHERE df.station = b.station AND df.model = 'HRRR'
             AND (df.valid_time AT TIME ZONE st2.tz)::date = b.valid_date
             AND df.ingested_at <= b.ts
      ) hrrr ON true
      LEFT JOIN LATERAL (
          SELECT MAX(ingested_at) AS last_ingested_at
            FROM det_forecast df
            JOIN stations st2 ON st2.code = df.station
           WHERE df.station = b.station AND df.model = 'GFS'
             AND (df.valid_time AT TIME ZONE st2.tz)::date = b.valid_date
             AND df.ingested_at <= b.ts
      ) gfs ON true
      LEFT JOIN LATERAL (
          SELECT MAX(ingested_at) AS last_ingested_at
            FROM det_forecast df
            JOIN stations st2 ON st2.code = df.station
           WHERE df.station = b.station AND df.model = 'ECMWF'
             AND (df.valid_time AT TIME ZONE st2.tz)::date = b.valid_date
             AND df.ingested_at <= b.ts
      ) ecmwf ON true
      LEFT JOIN LATERAL (
          SELECT MAX(ingested_at) AS last_ingested_at
            FROM ensemble_forecast ef
            JOIN stations st2 ON st2.code = ef.station
           WHERE ef.station = b.station
             AND (ef.valid_time AT TIME ZONE st2.tz)::date = b.valid_date
             AND ef.ingested_at <= b.ts
      ) ens ON true
     ORDER BY b.ts DESC
    """
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"days_back": days_back, "limit": limit})
        rows = cur.fetchall()

    out: list[LagRow] = []
    for r in rows:
        yes_mid = float(r["yes_mid"])
        fair_prob = float(r["fair_prob"])
        prob_edge = fair_prob - yes_mid
        ages = {
            "NBM": None if r["age_nbm_min"] is None else float(r["age_nbm_min"]),
            "HRRR": None if r["age_hrrr_min"] is None else float(r["age_hrrr_min"]),
            "GFS": None if r["age_gfs_min"] is None else float(r["age_gfs_min"]),
            "ECMWF": None if r["age_ecmwf_min"] is None else float(r["age_ecmwf_min"]),
            "ENSEMBLE": None if r["age_ensemble_min"] is None else float(r["age_ensemble_min"]),
        }
        freshest_source, freshest_age = _freshest_source(ages)
        mid_15 = None if r["mid_15m"] is None else float(r["mid_15m"])
        mid_30 = None if r["mid_30m"] is None else float(r["mid_30m"])
        mid_60 = None if r["mid_60m"] is None else float(r["mid_60m"])
        out.append(
            LagRow(
                signal_id=int(r["signal_id"]),
                ticker=r["ticker"],
                ts=r["ts"],
                station=r["station"],
                valid_date=r["valid_date"],
                lead_day=int(r["lead_day"]),
                action=r["action"],
                fair_prob=fair_prob,
                yes_mid=yes_mid,
                prob_edge=prob_edge,
                abs_prob_edge=abs(prob_edge),
                edge_z=edge_z_score(prob_edge),
                freshest_source=freshest_source,
                freshest_age_min=freshest_age,
                m2m_15m=None if mid_15 is None else mid_15 - yes_mid,
                m2m_30m=None if mid_30 is None else mid_30 - yes_mid,
                m2m_60m=None if mid_60 is None else mid_60 - yes_mid,
                signed_m2m_15m=_signed_move(prob_edge, mid_15, yes_mid),
                signed_m2m_30m=_signed_move(prob_edge, mid_30, yes_mid),
                signed_m2m_60m=_signed_move(prob_edge, mid_60, yes_mid),
            )
        )
    return out


def _avg(values: list[float | None]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return statistics.fmean(vals) if vals else None


def _bucket_age(minutes: float | None) -> str:
    if minutes is None:
        return "unknown"
    if minutes < 15:
        return "<15m"
    if minutes < 60:
        return "15-60m"
    if minutes < 180:
        return "1-3h"
    if minutes < 360:
        return "3-6h"
    return "6h+"


def _bucket_z(z: float) -> str:
    if z < 1:
        return "<1"
    if z < 2:
        return "1-2"
    if z < 3:
        return "2-3"
    return "3+"


def _fmt(value: float | None, digits: int = 4) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _group_rows(rows: list[LagRow], attr: str) -> dict[str, list[LagRow]]:
    groups: dict[str, list[LagRow]] = {}
    for row in rows:
        groups.setdefault(str(getattr(row, attr)), []).append(row)
    return groups


def write_csv(rows: list[LagRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _summary_row(label: str, rows: list[LagRow]) -> str:
    return (
        f"| {label} | {len(rows)} | {_fmt(_avg([r.abs_prob_edge for r in rows]), 3)} | "
        f"{_fmt(_avg([r.edge_z for r in rows]), 2)} | "
        f"{_fmt(_avg([r.signed_m2m_15m for r in rows]), 4)} | "
        f"{_fmt(_avg([r.signed_m2m_30m for r in rows]), 4)} | "
        f"{_fmt(_avg([r.signed_m2m_60m for r in rows]), 4)} |"
    )


def render_markdown(rows: list[LagRow], days_back: int) -> str:
    actionable = [r for r in rows if r.action == "OPEN"]
    lines = [
        f"# Forecast Update Lag Report - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Window: last {days_back} days. Signed movement is positive when the YES market moves toward our fair probability.",
        "",
        "## Overall Signals",
        "",
        "| cohort | n | avg abs prob edge | avg z | signed 15m | signed 30m | signed 60m |",
        "|---|---:|---:|---:|---:|---:|---:|",
        _summary_row("all", rows),
        _summary_row("OPEN only", actionable),
        "",
        "## By Probability-Edge Z",
        "",
        "| z bucket | n | avg abs prob edge | avg z | signed 15m | signed 30m | signed 60m |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    z_groups: dict[str, list[LagRow]] = {}
    for row in rows:
        z_groups.setdefault(_bucket_z(row.edge_z), []).append(row)
    for bucket in ["<1", "1-2", "2-3", "3+"]:
        vals = z_groups.get(bucket, [])
        if vals:
            lines.append(_summary_row(bucket, vals))

    lines.extend([
        "",
        "## By Freshest Forecast Age",
        "",
        "| age bucket | n | avg abs prob edge | avg z | signed 15m | signed 30m | signed 60m |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    age_groups: dict[str, list[LagRow]] = {}
    for row in rows:
        age_groups.setdefault(_bucket_age(row.freshest_age_min), []).append(row)
    for bucket in ["<15m", "15-60m", "1-3h", "3-6h", "6h+", "unknown"]:
        vals = age_groups.get(bucket, [])
        if vals:
            lines.append(_summary_row(bucket, vals))

    lines.extend([
        "",
        "## By Station / Lead",
        "",
        "| station/lead | n | avg abs prob edge | avg z | signed 15m | signed 30m | signed 60m |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    groups: dict[str, list[LagRow]] = {}
    for row in rows:
        groups.setdefault(f"{row.station} L{row.lead_day}", []).append(row)
    for label, vals in sorted(groups.items()):
        lines.append(_summary_row(label, vals))

    lines.extend([
        "",
        "## Interpretation",
        "",
        "- If high-z rows have positive signed movement, a probability-edge z gate has evidence.",
        "- If fresh-age rows have stronger signed movement than stale rows, forecast-update lag is actionable.",
        "- This is market movement, not realized P&L. It should inform order timing and TTL/reprice rules before it informs sizing.",
    ])
    return "\n".join(lines) + "\n"


def run(days_back: int = 30, out_dir: Path = Path("research/reports"), limit: int = 2500) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = collect_rows(days_back=days_back, limit=limit)
    stem = f"forecast_update_lag_{date.today()}"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"
    write_csv(rows, csv_path)
    md_path.write_text(render_markdown(rows, days_back))
    return {"rows": len(rows), "csv_path": str(csv_path), "report_path": str(md_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--limit", type=int, default=2500)
    args = parser.parse_args()
    result = run(days_back=args.days_back, limit=args.limit)
    print(Path(result["report_path"]).read_text())
