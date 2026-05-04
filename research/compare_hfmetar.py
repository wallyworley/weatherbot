"""Backtest 5-min HFMETAR vs hourly METAR against CLI ground truth.

Question this answers:
    If we switched the bot's daily TMAX source from hourly METAR (top-of-hour
    only) to 5-min MADIS HFMETAR (12 obs/hour), how much closer would we get
    to the NWS CLI value that Kalshi NHIGH actually settles on?

Output: research/reports/hfmetar_compare_YYYY-MM-DD.{csv,md}

Method per station-day:
    cli_tmax    NWS CLI / settlement authority   (reuse compare_observations helpers)
    hourly_tmax max temp over routine + SPECI METAR rows in IEM
    hf_tmax     max temp over all rows including 5-min HFMETAR (T-group precision)

Three quantities matter:
    abs_hourly_gap = |cli - hourly_tmax|     (current state)
    abs_hf_gap     = |cli - hf_tmax|         (proposed)
    closure        = abs_hourly_gap - abs_hf_gap   (positive = improvement)
"""
from __future__ import annotations

import argparse
import csv
import logging
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytz

from weather_bot.config import ACTIVE_FETCH_STATIONS, STATIONS
from weather_bot.data import iem_fetcher
from weather_bot.data import nws_text_products as nws

log = logging.getLogger(__name__)


@dataclass
class DayRow:
    station: str
    data_date: date
    cli_tmax: Optional[float]
    hourly_tmax: Optional[float]   # report_type 3,4 only (matches current bot)
    hf_tmax: Optional[float]       # all reports including 5-min HFMETAR
    n_hourly_obs: int
    n_hf_obs: int
    cli_minus_hourly: Optional[float]
    cli_minus_hf: Optional[float]
    closure: Optional[float]       # |cli-hourly| - |cli-hf|; positive = HF helps


def _cli_tmax(station: str, target: date) -> Optional[float]:
    """CLI TMAX for the given local calendar day from the cli_obs table.

    Reads only what `jobs/pull_cli` has captured into the DB. Sparse coverage
    in cli_obs will reduce the paired-day sample size — run pull_cli with
    `--days-back N` first if you need a wider review window.
    """
    try:
        return nws.get_cli_tmax(station, target)
    except Exception as e:
        log.warning("CLI lookup %s/%s failed: %s", station, target, e)
        return None


def _local_day_extremes(rows: list[dict], station_code: str, target: date) -> tuple[Optional[float], int]:
    """Return (max temp_f over rows whose obs_time falls in target's local day, n_samples)."""
    tz = pytz.timezone(STATIONS[station_code].tz)
    local_start = tz.localize(datetime.combine(target, datetime.min.time()))
    local_end = local_start + timedelta(days=1)
    samples: list[float] = []
    for r in rows:
        t = r["obs_time"].astimezone(tz)
        if local_start <= t < local_end and r.get("temp_f") is not None:
            samples.append(r["temp_f"])
    if not samples:
        return None, 0
    return max(samples), len(samples)


def _split_hourly(rows: list[dict]) -> list[dict]:
    """Approximate the bot's current view: only top-of-hour routine METARs.

    HFMETAR rows are tagged "MADISHF" in the raw remarks; routine METARs
    are not. Filter on that to recover the report_type=3,4 subset without
    re-fetching.
    """
    return [r for r in rows if r.get("raw") and "MADISHF" not in r["raw"]]


def collect(station: str, days_back: int, throttle_sec: float = 1.1) -> list[DayRow]:
    """Pull a single full-window HFMETAR fetch per station, slice by local day."""
    today = date.today()
    start = today - timedelta(days=days_back)
    end = today + timedelta(days=1)

    log.info("HFMETAR fetch %s %s -> %s", station, start, end)
    all_rows = iem_fetcher.fetch_historical_5min(station, start, end)
    hourly_rows = _split_hourly(all_rows)
    log.info("%s: %d total rows, %d hourly-only", station, len(all_rows), len(hourly_rows))

    out: list[DayRow] = []
    for i in range(1, days_back + 1):
        d = today - timedelta(days=i)
        cli = _cli_tmax(station, d)
        time.sleep(throttle_sec)  # IEM CGI is also called by fetch_text_iem inside _cli_tmax
        hf_tmax, n_hf = _local_day_extremes(all_rows, station, d)
        hourly_tmax, n_hr = _local_day_extremes(hourly_rows, station, d)

        cli_minus_hourly = (cli - hourly_tmax) if (cli is not None and hourly_tmax is not None) else None
        cli_minus_hf = (cli - hf_tmax) if (cli is not None and hf_tmax is not None) else None
        closure = (
            abs(cli_minus_hourly) - abs(cli_minus_hf)
            if (cli_minus_hourly is not None and cli_minus_hf is not None)
            else None
        )
        out.append(DayRow(
            station=station, data_date=d,
            cli_tmax=cli,
            hourly_tmax=hourly_tmax,
            hf_tmax=hf_tmax,
            n_hourly_obs=n_hr,
            n_hf_obs=n_hf,
            cli_minus_hourly=cli_minus_hourly,
            cli_minus_hf=cli_minus_hf,
            closure=closure,
        ))
    return out


def write_csv(rows: list[DayRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))


def _abs_mean(xs: list[Optional[float]]) -> Optional[float]:
    vs = [abs(x) for x in xs if x is not None]
    return statistics.fmean(vs) if vs else None


def _signed_mean(xs: list[Optional[float]]) -> Optional[float]:
    vs = [x for x in xs if x is not None]
    return statistics.fmean(vs) if vs else None


def summarize(rows: list[DayRow]) -> str:
    by_station: dict[str, list[DayRow]] = {}
    for r in rows:
        by_station.setdefault(r.station, []).append(r)

    lines = ["# HFMETAR vs hourly METAR — gap to CLI\n",
             f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_\n",
             "Does switching daily TMAX from hourly METAR to 5-min HFMETAR close "
             "the gap to NWS CLI (Kalshi NHIGH settlement authority)?\n",
             "Closure = |CLI−hourly| − |CLI−HF|. Positive = HFMETAR is closer.\n"]

    all_closures: list[Optional[float]] = []
    for st, st_rows in by_station.items():
        n = len(st_rows)
        n_overlap = sum(1 for r in st_rows if r.closure is not None)
        closures = [r.closure for r in st_rows]
        cli_v_hourly = [r.cli_minus_hourly for r in st_rows]
        cli_v_hf = [r.cli_minus_hf for r in st_rows]
        all_closures.extend(closures)

        helped = sum(1 for c in closures if c is not None and c > 0.05)
        hurt = sum(1 for c in closures if c is not None and c < -0.05)
        tied = n_overlap - helped - hurt

        lines.append(f"\n## {st}\n")
        lines.append(f"- days surveyed: {n}  (paired CLI+METAR: {n_overlap})")
        if abs_v_hourly := _abs_mean(cli_v_hourly):
            lines.append(f"- |CLI − hourly TMAX| mean abs: **{abs_v_hourly:.2f}°F**  (signed {_signed_mean(cli_v_hourly):+.2f})")
        if abs_v_hf := _abs_mean(cli_v_hf):
            lines.append(f"- |CLI − HF TMAX|     mean abs: **{abs_v_hf:.2f}°F**  (signed {_signed_mean(cli_v_hf):+.2f})")
        if mean_closure := _signed_mean(closures):
            lines.append(f"- mean closure: **{mean_closure:+.2f}°F** per day")
        lines.append(f"- HFMETAR closer: {helped}/{n_overlap} · hourly closer: {hurt}/{n_overlap} · tied: {tied}/{n_overlap}")

        big_help = sorted(
            (r for r in st_rows if r.closure is not None and r.closure > 0.5),
            key=lambda r: -r.closure,
        )[:5]
        if big_help:
            lines.append("\n  Largest HFMETAR wins:")
            for r in big_help:
                lines.append(
                    f"  - {r.data_date}: CLI {r.cli_tmax}°F · hourly {r.hourly_tmax}°F · "
                    f"HF {r.hf_tmax}°F · closure {r.closure:+.1f}°F"
                )

    # Aggregate verdict
    overall = _signed_mean(all_closures)
    overall_helped = sum(1 for c in all_closures if c is not None and c > 0.05)
    overall_total = sum(1 for c in all_closures if c is not None)
    lines.append("\n## Aggregate\n")
    if overall is not None:
        lines.append(f"- mean closure across all stations/days: **{overall:+.2f}°F**")
        lines.append(f"- HFMETAR closer on **{overall_helped}/{overall_total}** station-days "
                     f"({100 * overall_helped / overall_total:.0f}%)")
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

    csv_path = out_dir / f"hfmetar_compare_{date.today()}.csv"
    md_path = out_dir / f"hfmetar_compare_{date.today()}.md"
    write_csv(all_rows, csv_path)
    md_path.write_text(summarize(all_rows))
    log.info("wrote %s and %s", csv_path, md_path)
    print(md_path.read_text())
