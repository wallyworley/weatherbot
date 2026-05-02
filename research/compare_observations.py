"""Compare CLI vs DSM vs METAR-reconstructed observations.

For each day in the past N days at one or more stations, pulls:
- CLI tmax/tmin    (NWS Daily Climate Report — Kalshi settlement authority)
- DSM tmax/tmin    (NWS Daily Summary Message — automated ASOS, early preview)
- METAR tmax/tmin  (max/min over hourly obs already in our DB)

Outputs CSV + a short markdown summary in research/reports/.

Question this answers:
    Does our METAR-reconstructed TMAX/TMIN match what NWS publishes?
    Where it doesn't, that's settlement risk we're flying blind on.
"""
from __future__ import annotations

import argparse
import csv
import logging
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from weather_bot.config import ACTIVE_FETCH_STATIONS
from weather_bot.data import persistence

from research.sources.nws_text_products import (
    STATION_TO_LOC, fetch_text_iem, get_product, list_products,
    parse_cli_yesterday, parse_dsm,
)

log = logging.getLogger(__name__)


@dataclass
class DayRow:
    station: str
    data_date: date
    cli_tmax: Optional[float]
    cli_tmin: Optional[float]
    dsm_tmax: Optional[float]
    dsm_tmin: Optional[float]
    metar_tmax: Optional[float]
    metar_tmin: Optional[float]
    cli_vs_metar_tmax: Optional[float]   # cli - metar
    dsm_vs_metar_tmax: Optional[float]
    cli_vs_dsm_tmax: Optional[float]


def _metar_extremes(station: str, target: date) -> tuple[Optional[float], Optional[float]]:
    sql = """
        SELECT MAX(temp_f) AS tmax, MIN(temp_f) AS tmin
          FROM metar_obs
         WHERE station = %s AND obs_time::date = %s
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, target))
        r = cur.fetchone()
    if not r:
        return None, None
    return (float(r["tmax"]) if r["tmax"] is not None else None,
            float(r["tmin"]) if r["tmin"] is not None else None)


# NWS API only exposes ~6 days of archive; for older dates skip it entirely
# and fall through to IEM (saves time + avoids pointless timeouts).
_NWS_ARCHIVE_DAYS = 6


def _product_for_data_date(type_: str, station: str, target: date):
    """Find the morning CLI/DSM issued on (target+1) UTC. Skips afternoon
    intraday issues (which cover the issue day, not the target)."""
    loc = STATION_TO_LOC.get(station)
    if not loc:
        return None
    if (date.today() - target).days > _NWS_ARCHIVE_DAYS:
        return None   # NWS won't have it; caller's IEM fallback will handle it
    start = datetime.combine(target + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(hours=18)
    try:
        products = list_products(type_, loc, start=start, end=end, limit=20)
    except Exception as e:
        log.warning("NWS list_products %s/%s %s failed: %s", type_, station, target, e)
        return None
    if not products:
        return None
    try:
        return get_product(products[0]["id"])
    except Exception as e:
        log.warning("NWS get_product %s failed: %s", products[0]["id"], e)
        return None


def collect(station: str, days_back: int) -> list[DayRow]:
    rows: list[DayRow] = []
    today = date.today()
    for i in range(1, days_back + 1):
        d = today - timedelta(days=i)
        log.info("collect %s %s", station, d)
        cli_obs, dsm_obs = None, None
        # NWS API first (last ~6 days only); IEM fallback for older history.
        if cli_prod := _product_for_data_date("CLI", station, d):
            cli_obs = parse_cli_yesterday(cli_prod.text)
        elif iem_text := fetch_text_iem("CLI", station, d):
            cli_obs = parse_cli_yesterday(iem_text)
        if dsm_prod := _product_for_data_date("DSM", station, d):
            dsm_obs = parse_dsm(dsm_prod.text)
        elif iem_text := fetch_text_iem("DSM", station, d):
            dsm_obs = parse_dsm(iem_text)
        m_max, m_min = _metar_extremes(station, d)

        cli_max = cli_obs.tmax_f if cli_obs else None
        dsm_max = dsm_obs.tmax_f if dsm_obs else None
        cli_min = cli_obs.tmin_f if cli_obs else None
        dsm_min = dsm_obs.tmin_f if dsm_obs else None

        rows.append(DayRow(
            station=station, data_date=d,
            cli_tmax=cli_max, cli_tmin=cli_min,
            dsm_tmax=dsm_max, dsm_tmin=dsm_min,
            metar_tmax=m_max, metar_tmin=m_min,
            cli_vs_metar_tmax=(cli_max - m_max) if (cli_max is not None and m_max is not None) else None,
            dsm_vs_metar_tmax=(dsm_max - m_max) if (dsm_max is not None and m_max is not None) else None,
            cli_vs_dsm_tmax=(cli_max - dsm_max) if (cli_max is not None and dsm_max is not None) else None,
        ))
    return rows


def write_csv(rows: list[DayRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))


def _abs_mean(xs: list[float]) -> Optional[float]:
    xs = [abs(x) for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _signed_mean(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def summarize(rows: list[DayRow]) -> str:
    """Markdown summary with discrepancy stats per station."""
    by_station: dict[str, list[DayRow]] = {}
    for r in rows:
        by_station.setdefault(r.station, []).append(r)

    lines = ["# Observation Source Comparison\n",
             f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_\n",
             "Compares NWS CLI (settlement authority) vs NWS DSM (automated ASOS) "
             "vs our METAR-reconstructed daily extremes.\n"]
    for st, st_rows in by_station.items():
        n = len(st_rows)
        n_cli = sum(1 for r in st_rows if r.cli_tmax is not None)
        n_dsm = sum(1 for r in st_rows if r.dsm_tmax is not None)
        n_metar = sum(1 for r in st_rows if r.metar_tmax is not None)
        cli_vs_metar = [r.cli_vs_metar_tmax for r in st_rows]
        dsm_vs_metar = [r.dsm_vs_metar_tmax for r in st_rows]
        cli_vs_dsm = [r.cli_vs_dsm_tmax for r in st_rows]

        # Discrepancies > 0.5°F flag potential settlement risk.
        big_cli_vs_metar = [r for r in st_rows
                             if r.cli_vs_metar_tmax is not None and abs(r.cli_vs_metar_tmax) > 0.5]
        big_cli_vs_dsm = [r for r in st_rows
                           if r.cli_vs_dsm_tmax is not None and abs(r.cli_vs_dsm_tmax) > 0.5]

        lines.append(f"\n## {st}\n")
        lines.append(f"- days surveyed: {n}  (CLI rows: {n_cli}, DSM rows: {n_dsm}, METAR rows: {n_metar})")
        lines.append(f"- CLI vs METAR TMAX:  signed mean {_signed_mean(cli_vs_metar):+.2f}°F · "
                      f"abs mean {_abs_mean(cli_vs_metar):.2f}°F" if _signed_mean(cli_vs_metar) is not None else
                      "- CLI vs METAR TMAX: no overlap")
        lines.append(f"- DSM vs METAR TMAX:  signed mean {_signed_mean(dsm_vs_metar):+.2f}°F · "
                      f"abs mean {_abs_mean(dsm_vs_metar):.2f}°F" if _signed_mean(dsm_vs_metar) is not None else
                      "- DSM vs METAR TMAX: no overlap")
        lines.append(f"- CLI vs DSM TMAX:    signed mean {_signed_mean(cli_vs_dsm):+.2f}°F · "
                      f"abs mean {_abs_mean(cli_vs_dsm):.2f}°F" if _signed_mean(cli_vs_dsm) is not None else
                      "- CLI vs DSM TMAX: no overlap")
        lines.append(f"- days where |CLI−METAR| > 0.5°F: **{len(big_cli_vs_metar)}** of {n_metar}")
        lines.append(f"- days where |CLI−DSM|   > 0.5°F: **{len(big_cli_vs_dsm)}** of {min(n_cli, n_dsm)}")

        if big_cli_vs_metar:
            lines.append("\n  Notable CLI vs METAR mismatches:")
            for r in big_cli_vs_metar[:8]:
                lines.append(f"  - {r.data_date}: CLI {r.cli_tmax}°F, METAR {r.metar_tmax}°F "
                              f"(diff {r.cli_vs_metar_tmax:+.1f}°F)")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", nargs="+", default=ACTIVE_FETCH_STATIONS)
    ap.add_argument("--days-back", type=int, default=30)
    ap.add_argument("--out-dir", default="research/reports")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    all_rows: list[DayRow] = []
    for st in args.stations:
        all_rows.extend(collect(st, args.days_back))

    csv_path = out_dir / f"obs_compare_{date.today()}.csv"
    md_path = out_dir / f"obs_compare_{date.today()}.md"
    write_csv(all_rows, csv_path)
    md_path.write_text(summarize(all_rows))
    log.info("wrote %s and %s", csv_path, md_path)
    print(md_path.read_text())
