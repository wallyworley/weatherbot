"""Execution-quality and market-microstructure report.

This is a research-only report. It uses the data we already log:

- `paper_fill` for assumed entries
- `signal` for fair value, edge, and intended size
- `market_snapshot` for top-of-book price/depth around the fill
- forecast table `ingested_at` timestamps for a rough forecast-update age view

It does not pretend to backtest cross-platform gaps because no second venue
price feed is stored yet.
"""
from __future__ import annotations

import argparse
import csv
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from psycopg.rows import dict_row

from weather_bot.data import persistence


@dataclass(frozen=True)
class FillExecutionRow:
    fill_id: int
    ticker: str
    station: str | None
    valid_date: date | None
    side: str
    ts: datetime
    price: float
    contracts: int
    fees: float
    settled: bool
    net_pnl: float | None
    fair_prob: float
    edge: float
    size_usd: float
    snapshot_age_sec: float | None
    book_ask: float | None
    book_bid: float | None
    book_ask_size: int | None
    ask_minus_fill: float | None
    top_book_covers_fill: bool | None
    m2m_15m: float | None
    m2m_30m: float | None
    m2m_60m: float | None
    age_nbm_min: float | None
    age_hrrr_min: float | None
    age_gfs_min: float | None
    age_ecmwf_min: float | None


def _fetch_rows(days_back: int) -> list[FillExecutionRow]:
    sql = """
    WITH fills AS (
        SELECT pf.id AS fill_id, pf.ticker, km.station, km.valid_date, pf.side,
               pf.ts, pf.price, pf.contracts, pf.fees, pf.settled, pf.payout,
               s.fair_prob, s.edge, s.size_usd
          FROM paper_fill pf
          JOIN signal s ON s.id = pf.signal_id
          JOIN kalshi_market km ON km.ticker = pf.ticker
         WHERE pf.ts >= now() - (%(days_back)s || ' days')::interval
    )
    SELECT f.*,
           CASE WHEN f.settled THEN (COALESCE(f.payout, 0) - f.price) * f.contracts - f.fees END AS net_pnl,
           EXTRACT(EPOCH FROM (f.ts - snap.ts)) AS snapshot_age_sec,
           CASE WHEN f.side = 'YES' THEN snap.yes_ask ELSE snap.no_ask END::float AS book_ask,
           CASE WHEN f.side = 'YES' THEN snap.yes_bid ELSE snap.no_bid END::float AS book_bid,
           CASE WHEN f.side = 'YES' THEN snap.yes_ask_size ELSE snap.no_ask_size END AS book_ask_size,
           (CASE WHEN f.side = 'YES' THEN snap.yes_ask ELSE snap.no_ask END::float - f.price) AS ask_minus_fill,
           CASE
             WHEN (CASE WHEN f.side = 'YES' THEN snap.yes_ask_size ELSE snap.no_ask_size END) IS NULL THEN NULL
             ELSE (CASE WHEN f.side = 'YES' THEN snap.yes_ask_size ELSE snap.no_ask_size END) >= f.contracts
           END AS top_book_covers_fill,
           (CASE WHEN f.side = 'YES' THEN ms15.yes_bid ELSE ms15.no_bid END::float - f.price) AS m2m_15m,
           (CASE WHEN f.side = 'YES' THEN ms30.yes_bid ELSE ms30.no_bid END::float - f.price) AS m2m_30m,
           (CASE WHEN f.side = 'YES' THEN ms60.yes_bid ELSE ms60.no_bid END::float - f.price) AS m2m_60m,
           EXTRACT(EPOCH FROM (f.ts - nbm.last_ingested_at)) / 60.0 AS age_nbm_min,
           EXTRACT(EPOCH FROM (f.ts - hrrr.last_ingested_at)) / 60.0 AS age_hrrr_min,
           EXTRACT(EPOCH FROM (f.ts - gfs.last_ingested_at)) / 60.0 AS age_gfs_min,
           EXTRACT(EPOCH FROM (f.ts - ecmwf.last_ingested_at)) / 60.0 AS age_ecmwf_min
      FROM fills f
      LEFT JOIN LATERAL (
          SELECT *
            FROM market_snapshot ms
           WHERE ms.ticker = f.ticker AND ms.ts <= f.ts
           ORDER BY ms.ts DESC
           LIMIT 1
      ) snap ON true
      LEFT JOIN LATERAL (
          SELECT *
            FROM market_snapshot ms
           WHERE ms.ticker = f.ticker AND ms.ts >= f.ts + interval '15 minutes'
           ORDER BY ms.ts ASC
           LIMIT 1
      ) ms15 ON true
      LEFT JOIN LATERAL (
          SELECT *
            FROM market_snapshot ms
           WHERE ms.ticker = f.ticker AND ms.ts >= f.ts + interval '30 minutes'
           ORDER BY ms.ts ASC
           LIMIT 1
      ) ms30 ON true
      LEFT JOIN LATERAL (
          SELECT *
            FROM market_snapshot ms
           WHERE ms.ticker = f.ticker AND ms.ts >= f.ts + interval '60 minutes'
           ORDER BY ms.ts ASC
           LIMIT 1
      ) ms60 ON true
      LEFT JOIN LATERAL (
          SELECT MAX(ingested_at) AS last_ingested_at
            FROM prob_forecast pf
           WHERE pf.model = 'NBM_QMD' AND pf.ingested_at <= f.ts
      ) nbm ON true
      LEFT JOIN LATERAL (
          SELECT MAX(ingested_at) AS last_ingested_at
            FROM det_forecast df
           WHERE df.model = 'HRRR' AND df.ingested_at <= f.ts
      ) hrrr ON true
      LEFT JOIN LATERAL (
          SELECT MAX(ingested_at) AS last_ingested_at
            FROM det_forecast df
           WHERE df.model = 'GFS' AND df.ingested_at <= f.ts
      ) gfs ON true
      LEFT JOIN LATERAL (
          SELECT MAX(ingested_at) AS last_ingested_at
            FROM det_forecast df
           WHERE df.model = 'ECMWF' AND df.ingested_at <= f.ts
      ) ecmwf ON true
     ORDER BY f.ts DESC
    """
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"days_back": days_back})
        return [
            FillExecutionRow(
                fill_id=int(r["fill_id"]),
                ticker=r["ticker"],
                station=r["station"],
                valid_date=r["valid_date"],
                side=r["side"],
                ts=r["ts"],
                price=float(r["price"]),
                contracts=int(r["contracts"]),
                fees=float(r["fees"]),
                settled=bool(r["settled"]),
                net_pnl=None if r["net_pnl"] is None else float(r["net_pnl"]),
                fair_prob=float(r["fair_prob"]),
                edge=float(r["edge"]),
                size_usd=float(r["size_usd"]),
                snapshot_age_sec=None if r["snapshot_age_sec"] is None else float(r["snapshot_age_sec"]),
                book_ask=None if r["book_ask"] is None else float(r["book_ask"]),
                book_bid=None if r["book_bid"] is None else float(r["book_bid"]),
                book_ask_size=None if r["book_ask_size"] is None else int(r["book_ask_size"]),
                ask_minus_fill=None if r["ask_minus_fill"] is None else float(r["ask_minus_fill"]),
                top_book_covers_fill=r["top_book_covers_fill"],
                m2m_15m=None if r["m2m_15m"] is None else float(r["m2m_15m"]),
                m2m_30m=None if r["m2m_30m"] is None else float(r["m2m_30m"]),
                m2m_60m=None if r["m2m_60m"] is None else float(r["m2m_60m"]),
                age_nbm_min=None if r["age_nbm_min"] is None else float(r["age_nbm_min"]),
                age_hrrr_min=None if r["age_hrrr_min"] is None else float(r["age_hrrr_min"]),
                age_gfs_min=None if r["age_gfs_min"] is None else float(r["age_gfs_min"]),
                age_ecmwf_min=None if r["age_ecmwf_min"] is None else float(r["age_ecmwf_min"]),
            )
            for r in cur.fetchall()
        ]


def _avg(values: Iterable[float | None]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return statistics.fmean(vals) if vals else None


def _price_band(price: float) -> str:
    if price < 0.10:
        return "<10c"
    if price < 0.25:
        return "10-25c"
    if price < 0.50:
        return "25-50c"
    if price < 0.75:
        return "50-75c"
    return "75c+"


def _age_bucket(minutes: float | None) -> str:
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


def _pnl(rows: list[FillExecutionRow]) -> float:
    return sum(float(r.net_pnl) for r in rows if r.net_pnl is not None)


def _settled(rows: list[FillExecutionRow]) -> list[FillExecutionRow]:
    return [r for r in rows if r.net_pnl is not None]


def _format_money(value: float | None) -> str:
    return "-" if value is None else f"${value:+.2f}"


def _format_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def _format_num(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render_markdown(rows: list[FillExecutionRow], days_back: int) -> str:
    settled = _settled(rows)
    with_snap = [r for r in rows if r.snapshot_age_sec is not None]
    top_short = [r for r in rows if r.top_book_covers_fill is False]
    lines = [
        f"# Execution Quality Report - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Window: last {days_back} days. Research-only; paper fills assume immediate top-of-book execution.",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| fills | {len(rows)} |",
        f"| settled fills | {len(settled)} |",
        f"| fills with prior book snapshot | {len(with_snap)} |",
        f"| avg prior snapshot age | {_format_num(_avg([r.snapshot_age_sec for r in with_snap]), 1)} sec |",
        f"| avg book ask - fill price | {_format_num(_avg([r.ask_minus_fill for r in with_snap]), 4)} |",
        f"| top-of-book too small for paper fill | {len(top_short)} |",
        f"| avg 15m mark-to-market bid edge | {_format_num(_avg([r.m2m_15m for r in rows]), 4)} |",
        f"| avg 30m mark-to-market bid edge | {_format_num(_avg([r.m2m_30m for r in rows]), 4)} |",
        f"| avg 60m mark-to-market bid edge | {_format_num(_avg([r.m2m_60m for r in rows]), 4)} |",
        "",
        "## Low-price convexity sleeve",
        "",
        "| side | price band | n | win rate | P&L | $/fill | avg contracts |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    groups: dict[tuple[str, str], list[FillExecutionRow]] = {}
    for r in settled:
        groups.setdefault((r.side, _price_band(r.price)), []).append(r)
    for (side, band), vals in sorted(groups.items()):
        wins = sum(1 for r in vals if r.net_pnl is not None and r.net_pnl > 0)
        pnl = _pnl(vals)
        lines.append(
            f"| {side} | {band} | {len(vals)} | {_format_pct(wins / len(vals))} | "
            f"{_format_money(pnl)} | {_format_money(pnl / len(vals))} | "
            f"{_format_num(_avg([r.contracts for r in vals]), 1)} |"
        )

    lines.extend([
        "",
        "## Forecast-update age buckets",
        "",
        "| source | age bucket | settled n | P&L | $/fill |",
        "|---|---|---:|---:|---:|",
    ])
    source_attrs = {
        "NBM": "age_nbm_min",
        "HRRR": "age_hrrr_min",
        "GFS": "age_gfs_min",
        "ECMWF": "age_ecmwf_min",
    }
    for source, attr in source_attrs.items():
        buckets: dict[str, list[FillExecutionRow]] = {}
        for r in settled:
            buckets.setdefault(_age_bucket(getattr(r, attr)), []).append(r)
        for bucket in ["<15m", "15-60m", "1-3h", "3-6h", "6h+", "unknown"]:
            vals = buckets.get(bucket, [])
            if not vals:
                continue
            pnl = _pnl(vals)
            lines.append(f"| {source} | {bucket} | {len(vals)} | {_format_money(pnl)} | {_format_money(pnl / len(vals))} |")

    lines.extend([
        "",
        "## Interpretation",
        "",
        "- Low-price bands are where convexity should show up: many small losses need occasional large wins to pay for them.",
        "- Positive 15m/30m/60m mark-to-market means the orderbook moved in our direction after the paper fill; negative means we were early or crossed too much spread.",
        "- Cross-platform gaps are not backtestable yet because no second-venue price feed is logged.",
    ])
    return "\n".join(lines) + "\n"


def write_csv(rows: list[FillExecutionRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def run(days_back: int = 45, out_dir: Path = Path("research/reports")) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _fetch_rows(days_back)
    stem = f"execution_quality_{date.today()}"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"
    write_csv(rows, csv_path)
    md_path.write_text(render_markdown(rows, days_back))
    return {"rows": len(rows), "csv_path": str(csv_path), "report_path": str(md_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=45)
    args = parser.parse_args()
    result = run(days_back=args.days_back)
    print(Path(result["report_path"]).read_text())
