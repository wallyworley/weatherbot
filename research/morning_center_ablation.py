"""Exact point-in-time morning center ablation.

This is the production-grade check after the fast RPS diagnosis:

    - logged_model:      the fair_prob stored on each signal
    - rebuilt_prod:      production distribution rebuilt with run_time <= signal.ts
    - nbm_only:          same NBM shape/bias, but no HRRR/GFS center pull
    - gfs_only:          same shape, center shifted fully to GFS when available
    - nbm_gfs_50_50:     same shape, center halfway between NBM/GFS
    - station_gfs_50_50: use NBM/GFS only for explicitly supplied stations

Every non-logged variant uses build_station_distribution(..., as_of=signal.ts),
then applies the live empirical calibrator by default so the probabilities are
on the same scale the bot would trade. This is still a research harness: it
does not change live trading behavior.

Usage:
    python -m weather_bot.research.morning_center_ablation --days 45
    python -m weather_bot.research.morning_center_ablation --days 21 --no-calibrator
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Callable

import pytz

from weather_bot.config import STATIONS
from weather_bot.data import persistence
from weather_bot.models.distribution import build_station_distribution, lead_day_for_station
from weather_bot.research.morning_market_skill import (
    Row,
    Window,
    _fetch_window,
    _metrics_for,
    _print_metric,
    _rps_metrics,
)
from weather_bot.strategy.probability_calibration import calibrate_fair_probability, probability_bin


REPORTS_DIR = Path(__file__).resolve().parent / "reports"


@dataclass(frozen=True)
class Variant:
    name: str
    weights: dict[str, float] | None
    station_weights: dict[str, dict[str, float]] | None = None
    guidance_source: str | None = None
    deb_sources: tuple[str, ...] | None = None


def _variant_key(weights: dict[str, float] | None) -> tuple[tuple[str, float], ...] | None:
    if weights is None:
        return None
    return tuple(sorted((k, float(v)) for k, v in weights.items()))


def _station_gfs_weights(stations: set[str]) -> dict[str, dict[str, float]]:
    return {station: {"NBM": 0.5, "GFS": 0.5} for station in stations}


def _variants(station_gfs: set[str], include: set[str] | None = None) -> list[Variant]:
    variants = [
        Variant("logged_model", None),
        Variant("rebuilt_prod", None),
        Variant("nbm_only", {"NBM": 1.0}),
        Variant("gfs_only", {"GFS": 1.0}),
        Variant("nbm_gfs_50_50", {"NBM": 0.5, "GFS": 0.5}),
    ]
    if station_gfs:
        variants.append(Variant("station_gfs_50_50", None, _station_gfs_weights(station_gfs)))
    variants.extend([
        Variant("nws_grid_center", None, guidance_source="NWS_GRID"),
        Variant("pfm_center", None, guidance_source="NWS_PFM"),
        Variant("lamp_peak_center", None, guidance_source="LAMP"),
        Variant("mav_center", None, guidance_source="MAV"),
        Variant(
            "deb_recent_mae_center",
            {"NBM": 1.0},
            deb_sources=("NBM", "HRRR", "GFS", "NWS_GRID", "LAMP", "MAV"),
        ),
    ])
    if include:
        variants = [variant for variant in variants if variant.name in include]
    return variants


def _weights_for(row: Row, variant: Variant) -> dict[str, float] | None:
    if variant.station_weights is not None:
        return variant.station_weights.get(row.station, {"NBM": 1.0})
    return variant.weights


def _same_local_time_asof(station: str, target: date, template_asof: datetime) -> datetime:
    tz = pytz.timezone(STATIONS[station].tz)
    local_template = template_asof.astimezone(tz)
    local_cutoff = tz.localize(
        datetime.combine(
            target,
            time(
                local_template.hour,
                local_template.minute,
                local_template.second,
                local_template.microsecond,
            ),
        )
    )
    return local_cutoff.astimezone(timezone.utc)


def _truth_tmax(station: str, target: date) -> float | None:
    sql = """
    SELECT COALESCE(c.tmax_f, d.tmax_f) AS tmax
      FROM stations s
      LEFT JOIN cli_obs c ON c.station = s.code AND c.local_date = %s
      LEFT JOIN daily_obs d ON d.station = s.code AND d.local_date = %s
     WHERE s.code = %s
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (target, target, station))
        row = cur.fetchone()
        return float(row["tmax"]) if row and row["tmax"] is not None else None


def _source_center_as_of(station: str, target: date, source: str, as_of: datetime) -> float | None:
    source = source.upper()
    try:
        if source == "NBM":
            rows = persistence.nbm_percentiles_as_of(station, target, as_of, var="TMAX_DAILY")
            for row in rows:
                if int(row["percentile"]) == 50:
                    return float(row["value"])
            return None
        if source == "HRRR":
            val = persistence.hrrr_tmax_as_of(station, target, as_of)
            return float(val) if val is not None else None
        if source == "GFS":
            val = persistence.gfs_tmax_as_of(station, target, as_of)
            return float(val) if val is not None else None
        if source in {"NWS_GRID", "NWS_PFM", "LAMP", "MAV"}:
            val = persistence.guidance_tmax_as_of(station, target, source, as_of)
            return float(val) if val is not None else None
    except Exception as exc:
        print(f"warning: {source} center failed for {station} {target} {as_of}: {exc}", file=sys.stderr)
    return None


_DEB_CENTER_CACHE: dict[tuple, float | None] = {}
_TRUTH_CACHE: dict[tuple[str, date], float | None] = {}


def _deb_recent_mae_center(
    station: str,
    target: date,
    as_of: datetime,
    sources: tuple[str, ...],
    lookback_days: int,
    min_samples: int,
) -> float | None:
    """Recent inverse-MAE center blend, computed point-in-time.

    For each prior day, source errors are measured using the same station-local
    time of day as `as_of`, then compared against CLI/daily truth. This avoids
    letting late-day guidance choose the morning weights.
    """
    key = (station, target, as_of, sources, lookback_days, min_samples)
    if key in _DEB_CENTER_CACHE:
        return _DEB_CENTER_CACHE[key]

    current: dict[str, float] = {}
    for source in sources:
        value = _source_center_as_of(station, target, source, as_of)
        if value is not None:
            current[source] = value
    if not current:
        _DEB_CENTER_CACHE[key] = None
        return None

    errors: dict[str, list[float]] = {source: [] for source in current}
    for offset in range(1, lookback_days + 1):
        hist_date = target.fromordinal(target.toordinal() - offset)
        tkey = (station, hist_date)
        if tkey not in _TRUTH_CACHE:
            _TRUTH_CACHE[tkey] = _truth_tmax(station, hist_date)
        truth = _TRUTH_CACHE[tkey]
        if truth is None:
            continue
        hist_asof = _same_local_time_asof(station, hist_date, as_of)
        for source in current:
            value = _source_center_as_of(station, hist_date, source, hist_asof)
            if value is not None:
                errors[source].append(abs(value - truth))

    maes: dict[str, float] = {}
    for source, vals in errors.items():
        if len(vals) >= min_samples:
            maes[source] = sum(vals) / len(vals)

    if not maes:
        center = sum(current.values()) / len(current)
        _DEB_CENTER_CACHE[key] = center
        return center

    inv = {source: 1.0 / (mae + 0.1) for source, mae in maes.items() if source in current}
    denom = sum(inv.values())
    if denom <= 0:
        center = sum(current.values()) / len(current)
    else:
        center = sum(current[source] * weight for source, weight in inv.items()) / denom
    _DEB_CENTER_CACHE[key] = center
    return center


def _score_variant(
    rows: list[Row],
    variant: Variant,
    apply_calibrator: bool,
    workers: int = 1,
    event_asof: bool = True,
    deb_lookback_days: int = 21,
    deb_min_samples: int = 3,
) -> tuple[dict | None, dict | None, int]:
    if variant.name == "logged_model":
        return _metrics_for(rows, lambda r: r.model_p), _rps_metrics(rows, lambda r: r.model_p), 0

    probs: dict[int, float] = {}
    dist_cache: dict[tuple, object] = {}
    cal_delta_cache: dict[tuple[str, int, int], float] = {}
    misses = 0

    group_asof: dict[tuple[str, object, str], object] = {}
    if event_asof:
        for row in rows:
            gkey = (row.station, row.valid_date, row.var)
            group_asof[gkey] = max(group_asof.get(gkey, row.ts), row.ts)

    build_inputs: dict[tuple, tuple[Row, dict[str, float] | None, object]] = {}
    row_keys: dict[int, tuple] = {}
    for idx, row in enumerate(rows):
        weights = _weights_for(row, variant)
        as_of_ts = group_asof.get((row.station, row.valid_date, row.var), row.ts) if event_asof else row.ts
        key = (
            row.station,
            row.valid_date,
            row.var,
            as_of_ts,
            _variant_key(weights),
            variant.guidance_source,
        )
        row_keys[idx] = key
        build_inputs.setdefault(key, (row, weights, as_of_ts))

    def _build_one(item: tuple[tuple, tuple[Row, dict[str, float] | None, object]]) -> tuple[tuple, object]:
        key, (row, weights, as_of_ts) = item
        try:
            cdf = build_station_distribution(
                row.station,
                row.valid_date,
                row.var,
                now_utc=as_of_ts,
                as_of=as_of_ts,
                center_blend_weights=weights,
            )
            if cdf is not None and (variant.guidance_source is not None or variant.deb_sources is not None):
                if variant.deb_sources is not None:
                    center = _deb_recent_mae_center(
                        row.station,
                        row.valid_date,
                        as_of_ts,
                        variant.deb_sources,
                        deb_lookback_days,
                        deb_min_samples,
                    )
                else:
                    center = persistence.guidance_tmax_as_of(
                        row.station,
                        row.valid_date,
                        variant.guidance_source,
                        as_of_ts,
                    )
                if center is None:
                    cdf = None
                else:
                    cdf.shift += float(center) - cdf.median()
        except Exception as exc:
            print(
                f"warning: {variant.name} distribution failed for "
                f"{row.station} {row.valid_date} {row.ts}: {exc}",
                file=sys.stderr,
            )
            cdf = None
        return key, cdf

    items = list(build_inputs.items())
    if workers <= 1:
        for built, item in enumerate(items, start=1):
            key, cdf = _build_one(item)
            dist_cache[key] = cdf
            if built % 5 == 0:
                print(f"  {variant.name}: built {built}/{len(items)} PIT distributions", file=sys.stderr, flush=True)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_build_one, item) for item in items]
            for built, future in enumerate(as_completed(futures), start=1):
                key, cdf = future.result()
                dist_cache[key] = cdf
                if built % 25 == 0 or built == len(items):
                    print(f"  {variant.name}: built {built}/{len(items)} PIT distributions", file=sys.stderr, flush=True)

    for idx, row in enumerate(rows):
        key = row_keys[idx]
        cdf = dist_cache[key]
        if cdf is None:
            misses += 1
            continue

        raw = cdf.prob_between(row.lower_f, row.upper_f)
        if apply_calibrator:
            as_of_ts = key[3]
            lead_day = max(0, lead_day_for_station(row.station, row.valid_date, as_of_ts))
            raw_clipped = min(0.99, max(0.01, float(raw)))
            cal_key = (row.station, lead_day, probability_bin(raw_clipped))
            if cal_key not in cal_delta_cache:
                cal = calibrate_fair_probability(row.station, raw_clipped, lead_day=lead_day)
                cal_delta_cache[cal_key] = cal.calibrated_prob - raw_clipped
            raw = min(0.99, max(0.01, raw_clipped + cal_delta_cache[cal_key]))
        probs[idx] = raw

    def _prob(r: Row) -> float | None:
        try:
            idx = row_index[id(r)]
        except KeyError:
            return None
        return probs.get(idx)

    row_index = {id(row): idx for idx, row in enumerate(rows)}
    return _metrics_for(rows, _prob), _rps_metrics(rows, _prob), misses


def _summary_line(name: str, metrics: dict | None, rps: dict | None, misses: int) -> str:
    if not metrics:
        return f"{name:<20} no scored rows"
    rps_text = "RPS=n/a"
    if rps:
        rps_text = (
            f"RPS={rps['model_rps']:.4f} market_RPS={rps['market_rps']:.4f} "
            f"RPS_skill={rps['rps_skill']:+.2f}"
        )
    return (
        f"{name:<20} n={metrics['n']:>5} Brier={metrics['model_brier']:.4f} "
        f"market={metrics['market_brier']:.4f} skill={metrics['skill']:+.2f} "
        f"REL={metrics['model_rel']:.4f} RES={metrics['model_res']:.4f} "
        f"{rps_text} misses={misses}"
    )


def _write_report(result: dict, label: str) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_label = label.replace(" ", "_").replace(":", "")
    md_path = REPORTS_DIR / f"morning_center_ablation_{safe_label}.md"
    json_path = REPORTS_DIR / f"morning_center_ablation_{safe_label}.json"

    lines = [
        f"# Morning Center Ablation - {result['generated_at']}",
        "",
        f"Window: `{result['window']['start']}-{result['window']['end']}` local",
        f"Days: `{result['days']}`",
        f"Pick: `{result['pick']}`",
        f"Variable: `{result['var']}`",
        f"Calibrator: `{'ON' if result['apply_calibrator'] else 'OFF'}`",
        f"Rows: `{result['rows']}`",
        "",
        "Positive skill means the variant beats the market midpoint. All variants are research-only.",
        "",
        "```text",
    ]
    lines.extend(result["lines"])
    lines.append("```")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(result["interpretation"])
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return md_path, json_path


def _interpret(results: dict[str, dict]) -> str:
    logged = results.get("logged_model", {}).get("metrics")
    nbm = results.get("nbm_only", {}).get("metrics")
    prod = results.get("rebuilt_prod", {}).get("metrics")
    if not logged or not nbm:
        return "Insufficient scored rows to compare the center ablations."
    pieces = []
    if nbm["model_brier"] < logged["model_brier"]:
        pieces.append("NBM-only improved Brier versus the logged model, so disabling morning center pulls has evidence as a damage-reduction candidate.")
    else:
        pieces.append("NBM-only did not improve Brier versus the logged model, so do not disable morning center pulls on this run alone.")
    if prod and abs(prod["model_brier"] - logged["model_brier"]) > 0.01:
        pieces.append("Rebuilt production differs materially from logged probabilities; treat calibrator/bias-history statefulness as part of the uncertainty.")
    best_name, best = min(
        ((name, data["metrics"]) for name, data in results.items() if data.get("metrics")),
        key=lambda item: item[1]["model_brier"],
    )
    pieces.append(f"Best in this run by Brier was `{best_name}` with skill {best['skill']:+.2f}; it still must be positive out of sample before sizing.")
    return " ".join(pieces)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--pick", choices=("first", "last"), default="last")
    ap.add_argument("--var", choices=("TMAX_DAILY",), default="TMAX_DAILY")
    ap.add_argument("--morning-start", default="06:00")
    ap.add_argument("--morning-end", default="09:00")
    ap.add_argument("--no-calibrator", action="store_true")
    ap.add_argument(
        "--station-gfs",
        default="KAUS,KDCA,KLAS,KLAX,KPHX,KMSP",
        help="Comma-separated stations for exploratory station_gfs_50_50 variant.",
    )
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument(
        "--variants",
        default="",
        help=("Comma-separated subset: logged_model,rebuilt_prod,nbm_only,gfs_only,"
              "nbm_gfs_50_50,station_gfs_50_50,nws_grid_center,pfm_center,"
              "lamp_peak_center,mav_center,deb_recent_mae_center"),
    )
    ap.add_argument("--deb-lookback-days", type=int, default=21)
    ap.add_argument("--deb-min-samples", type=int, default=3)
    ap.add_argument("--max-rows", type=int, default=0, help="Debug cap on bucket rows after window fetch.")
    ap.add_argument("--workers", type=int, default=1, help="Parallel PIT distribution builders.")
    ap.add_argument(
        "--row-asof",
        action="store_true",
        help="Use each bucket row's own timestamp instead of one latest timestamp per station/date event.",
    )
    args = ap.parse_args()

    window = Window("morning", args.morning_start, args.morning_end)
    rows = _fetch_window(window, args.days, args.pick, args.var)
    if args.max_rows > 0:
        rows = rows[:args.max_rows]
    apply_calibrator = not args.no_calibrator
    station_gfs = {s.strip().upper() for s in args.station_gfs.split(",") if s.strip()}

    print(
        f"Morning center ablation: {len(rows)} bucket rows, "
        f"{args.days}d, {window.start}-{window.end} local, calibrator={'ON' if apply_calibrator else 'OFF'}"
    )

    results: dict[str, dict] = {}
    lines: list[str] = []
    include = {v.strip() for v in args.variants.split(",") if v.strip()} or None
    selected = _variants(station_gfs, include)
    if include and len(selected) != len(include):
        known = {v.name for v in _variants(station_gfs)}
        missing = include - known
        raise SystemExit(f"Unknown variants: {', '.join(sorted(missing))}")

    for variant in selected:
        print(f"Scoring {variant.name}...", file=sys.stderr, flush=True)
        metrics, rps, misses = _score_variant(
            rows,
            variant,
            apply_calibrator,
            workers=max(1, args.workers),
            event_asof=not args.row_asof,
            deb_lookback_days=args.deb_lookback_days,
            deb_min_samples=args.deb_min_samples,
        )
        results[variant.name] = {"metrics": metrics, "rps": rps, "misses": misses}
        line = _summary_line(variant.name, metrics, rps, misses)
        lines.append(line)
        print(line)
        if metrics:
            _print_metric(f"{variant.name} detail", metrics)

    interpretation = _interpret(results)
    print("\nInterpretation:")
    print(interpretation)

    result = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "days": args.days,
        "pick": args.pick,
        "var": args.var,
        "window": {"start": window.start, "end": window.end},
        "apply_calibrator": apply_calibrator,
        "rows": len(rows),
        "event_asof": not args.row_asof,
        "station_gfs": sorted(station_gfs),
        "deb_lookback_days": args.deb_lookback_days,
        "deb_min_samples": args.deb_min_samples,
        "results": results,
        "lines": lines,
        "interpretation": interpretation,
    }
    if not args.no_report:
        label = f"{args.days}d_{window.start}_{window.end}_{'cal' if apply_calibrator else 'raw'}"
        md_path, json_path = _write_report(result, label)
        print(f"\nWrote {md_path}")
        print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
