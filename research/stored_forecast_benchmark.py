"""Benchmark stored forecast sources against CLI/daily TMAX truth.

Unlike `research.compare_forecasts`, this script only uses forecasts already
captured in Postgres. That makes it the right report for deciding whether the
new live GFS/ECMWF pulls have earned their way into the trading model.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from psycopg.rows import dict_row

from weather_bot.config import ACTIVE_FETCH_STATIONS
from weather_bot.data import persistence


@dataclass(frozen=True)
class ForecastErrorRow:
    station: str
    model: str
    run_time: datetime
    valid_date: date
    lead_day: int
    pred_tmax: float
    truth_tmax: float
    error_f: float
    abs_error_f: float


def collect_rows(days_back: int = 30, max_lead_day: int = 7) -> list[ForecastErrorRow]:
    sql = """
    WITH truth AS (
        SELECT s.code AS station,
               d::date AS valid_date,
               COALESCE(c.tmax_f, o.tmax_f) AS truth_tmax
          FROM stations s
          CROSS JOIN generate_series(
              (CURRENT_DATE - (%(days_back)s || ' days')::interval)::date,
              CURRENT_DATE - INTERVAL '1 day',
              '1 day'::interval
          ) AS d
          LEFT JOIN cli_obs c ON c.station = s.code AND c.local_date = d::date
          LEFT JOIN daily_obs o ON o.station = s.code AND o.local_date = d::date
         WHERE s.code = ANY(%(stations)s)
    ),
    nbm AS (
        SELECT pf.station,
               'NBM'::text AS model,
               pf.run_time,
               pf.valid_date,
               GREATEST(0, (pf.valid_date - (pf.run_time AT TIME ZONE st.tz)::date)::int) AS lead_day,
               pf.value::float AS pred_tmax
          FROM prob_forecast pf
          JOIN stations st ON st.code = pf.station
         WHERE pf.var = 'TMAX_DAILY'
           AND pf.percentile = 50
           AND pf.station = ANY(%(stations)s)
    ),
    det AS (
        SELECT df.station,
               df.model,
               df.run_time,
               (df.valid_time AT TIME ZONE st.tz)::date AS valid_date,
               GREATEST(0, ((df.valid_time AT TIME ZONE st.tz)::date
                    - (df.run_time AT TIME ZONE st.tz)::date)::int) AS lead_day,
               MAX(df.value)::float AS pred_tmax
          FROM det_forecast df
          JOIN stations st ON st.code = df.station
         WHERE df.var = 'TMP_2M'
           AND df.model IN ('HRRR', 'GFS', 'ECMWF')
           AND df.station = ANY(%(stations)s)
         GROUP BY df.station, df.model, df.run_time,
                  (df.valid_time AT TIME ZONE st.tz)::date,
                  GREATEST(0, ((df.valid_time AT TIME ZONE st.tz)::date
                    - (df.run_time AT TIME ZONE st.tz)::date)::int)
    ),
    preds AS (
        SELECT * FROM nbm
        UNION ALL
        SELECT * FROM det
    )
    SELECT p.station, p.model, p.run_time, p.valid_date, p.lead_day,
           p.pred_tmax, t.truth_tmax,
           p.pred_tmax - t.truth_tmax AS error_f,
           ABS(p.pred_tmax - t.truth_tmax) AS abs_error_f
      FROM preds p
      JOIN truth t ON t.station = p.station AND t.valid_date = p.valid_date
     WHERE t.truth_tmax IS NOT NULL
       AND p.valid_date >= CURRENT_DATE - (%(days_back)s || ' days')::interval
       AND p.valid_date < CURRENT_DATE
       AND p.lead_day BETWEEN 0 AND %(max_lead_day)s
     ORDER BY p.valid_date, p.station, p.lead_day, p.model, p.run_time
    """
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql,
            {
                "days_back": days_back,
                "max_lead_day": max_lead_day,
                "stations": ACTIVE_FETCH_STATIONS,
            },
        )
        rows = cur.fetchall()

    return [
        ForecastErrorRow(
            station=r["station"],
            model=r["model"],
            run_time=r["run_time"],
            valid_date=r["valid_date"],
            lead_day=int(r["lead_day"]),
            pred_tmax=float(r["pred_tmax"]),
            truth_tmax=float(r["truth_tmax"]),
            error_f=float(r["error_f"]),
            abs_error_f=float(r["abs_error_f"]),
        )
        for r in rows
    ]


def metric_summary(rows: Iterable[ForecastErrorRow]) -> dict:
    rows = list(rows)
    if not rows:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    errs = [r.error_f for r in rows]
    return {
        "n": len(rows),
        "mae": statistics.fmean(abs(e) for e in errs),
        "rmse": math.sqrt(statistics.fmean(e * e for e in errs)),
        "bias": statistics.fmean(errs),
    }


def grouped_metrics(rows: list[ForecastErrorRow]) -> dict[tuple, dict]:
    groups: dict[tuple, list[ForecastErrorRow]] = defaultdict(list)
    for row in rows:
        groups[(row.station, row.lead_day, row.model)].append(row)
    return {key: metric_summary(vals) for key, vals in groups.items()}


def write_csv(rows: list[ForecastErrorRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def render_markdown(rows: list[ForecastErrorRow], days_back: int) -> str:
    lines = [
        f"# Stored Forecast Benchmark — {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Window: last {days_back} completed valid dates. Truth is CLI TMAX when available, else daily_obs TMAX.",
        "",
        "## Leaderboard by lead day",
        "",
        "| lead | model | n | MAE | RMSE | bias |",
        "|---:|---|---:|---:|---:|---:|",
    ]

    lead_model: dict[tuple[int, str], list[ForecastErrorRow]] = defaultdict(list)
    for row in rows:
        lead_model[(row.lead_day, row.model)].append(row)
    for (lead, model), vals in sorted(lead_model.items()):
        m = metric_summary(vals)
        lines.append(
            f"| {lead} | {model} | {m['n']} | {m['mae']:.2f} | {m['rmse']:.2f} | {m['bias']:+.2f} |"
        )

    lines.extend([
        "",
        "## By station / lead / model",
        "",
        "| station | lead | model | n | MAE | RMSE | bias |",
        "|---|---:|---|---:|---:|---:|---:|",
    ])
    for (station, lead, model), m in sorted(grouped_metrics(rows).items()):
        lines.append(
            f"| {station} | {lead} | {model} | {m['n']} | {m['mae']:.2f} | {m['rmse']:.2f} | {m['bias']:+.2f} |"
        )

    lines.extend([
        "",
        "## Interpretation rules",
        "",
        "- Treat models with fewer than 20 paired rows as provisional.",
        "- Treat HRRR lead-1+ with caution: short HRRR runs may cover only part of the valid day, so daily max can be artificially cold.",
        "- Promote a challenger only if it improves MAE/RMSE and is complementary in bias direction.",
        "- Use this report to choose shadow-ensemble weights; do not directly trade from it.",
    ])
    return "\n".join(lines) + "\n"


def run(days_back: int = 30, max_lead_day: int = 7, out_dir: Path = Path("research/reports")) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = collect_rows(days_back=days_back, max_lead_day=max_lead_day)
    stem = f"stored_forecast_benchmark_{date.today()}"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"
    write_csv(rows, csv_path)
    md_path.write_text(render_markdown(rows, days_back))
    return {"rows": len(rows), "csv_path": str(csv_path), "report_path": str(md_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--max-lead-day", type=int, default=7)
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    args = parser.parse_args()
    result = run(args.days_back, args.max_lead_day, args.out_dir)
    print(Path(result["report_path"]).read_text())
