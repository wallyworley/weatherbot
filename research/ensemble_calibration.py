"""Calibrated true-ensemble replay.

Raw member counting can be underdispersed for range markets: the members cluster
too tightly, so tail buckets look too unlikely and center buckets look too
certain. This report tests simple EMOS-lite transforms without changing the
trading path.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from research import shadow_ensemble


BIAS_OFFSETS_F = [-2.0, -1.0, 0.0, 1.0, 2.0]
SPREAD_MULTIPLIERS = [1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
SIGMA_FLOOR_F = 0.75


@dataclass(frozen=True)
class EnsembleReplayRow:
    signal_id: int
    ticker: str
    ts: datetime
    station: str
    valid_date: date
    lead_day: int
    yes_won: int
    n_members: int
    member_mean_f: float
    member_sigma_f: float
    original_p_yes: float
    raw_member_p_yes: float
    calibrated_p_yes: float
    calibrated_variant: str
    original_brier: float
    raw_member_brier: float
    calibrated_brier: float


def normal_cdf(x: float, mean: float, sigma: float) -> float:
    sigma = max(float(sigma), SIGMA_FLOOR_F)
    return 0.5 * (1.0 + math.erf((float(x) - mean) / (sigma * math.sqrt(2.0))))


def normal_prob_between(mean: float, sigma: float, lo: float | None, hi: float | None) -> float:
    lo_cdf = 0.0 if lo is None else normal_cdf(lo, mean, sigma)
    hi_cdf = 1.0 if hi is None else normal_cdf(hi, mean, sigma)
    return max(0.0, min(1.0, hi_cdf - lo_cdf))


def calibrated_member_probability(
    members: list[float],
    lower_f: float | None,
    upper_f: float | None,
    *,
    bias_offset_f: float = 0.0,
    spread_multiplier: float = 1.0,
) -> float | None:
    """Convert member mean/spread into a bucket probability."""
    if not members:
        return None
    mean = statistics.fmean(members) + bias_offset_f
    sigma = statistics.pstdev(members) if len(members) >= 2 else SIGMA_FLOOR_F
    sigma = max(SIGMA_FLOOR_F, sigma * spread_multiplier)
    return normal_prob_between(mean, sigma, lower_f, upper_f)


def _all_members(row: dict) -> list[float]:
    values: list[float] = []
    for column in shadow_ensemble.TRUE_ENSEMBLE_COLUMNS.values():
        values.extend(float(v) for v in row.get(column) or [] if v is not None)
    return values


def _yes_won(row: dict) -> int:
    obs = float(row["obs_tmax"])
    lo = row.get("lower_f")
    hi = row.get("upper_f")
    return int((lo is None or obs >= float(lo)) and (hi is None or obs < float(hi)))


def _brier(p: float, y: int) -> float:
    return (float(p) - int(y)) ** 2


def _variant_name(bias: float, spread: float) -> str:
    return f"bias={bias:+.1f},spread={spread:.2f}"


def _candidate_variants() -> list[tuple[float, float]]:
    return [(bias, spread) for bias in BIAS_OFFSETS_F for spread in SPREAD_MULTIPLIERS]


def choose_best_variant(rows: list[dict]) -> tuple[float, float, float]:
    """Choose the lowest-Brier variant on a training set."""
    best: tuple[float, float, float] | None = None
    for bias, spread in _candidate_variants():
        scores = []
        for row in rows:
            members = _all_members(row)
            if len(members) < shadow_ensemble.TRUE_ENSEMBLE_MIN_MEMBERS:
                continue
            p = calibrated_member_probability(
                members,
                row.get("lower_f"),
                row.get("upper_f"),
                bias_offset_f=bias,
                spread_multiplier=spread,
            )
            if p is None:
                continue
            scores.append(_brier(p, _yes_won(row)))
        if not scores:
            continue
        score = statistics.fmean(scores)
        if best is None or score < best[2]:
            best = (bias, spread, score)
    return best or (0.0, 1.0, float("nan"))


def _split_train_test(rows: list[dict], train_fraction: float = 0.70) -> tuple[list[dict], list[dict]]:
    ordered = sorted(rows, key=lambda r: r["ts"])
    split = max(1, min(len(ordered) - 1, int(len(ordered) * train_fraction)))
    return ordered[:split], ordered[split:]


def replay(days_back: int = 30, limit: int | None = None, per_group_limit: int = 200) -> tuple[list[EnsembleReplayRow], dict]:
    raw_rows = [
        row for row in shadow_ensemble._signal_rows(days_back=days_back, limit=limit, per_group_limit=per_group_limit)
        if len(_all_members(row)) >= shadow_ensemble.TRUE_ENSEMBLE_MIN_MEMBERS
    ]
    if len(raw_rows) < 2:
        return [], {"bias": 0.0, "spread": 1.0, "train_brier": None, "train_n": len(raw_rows), "test_n": 0}

    train_rows, test_rows = _split_train_test(raw_rows)
    bias, spread, train_brier = choose_best_variant(train_rows)
    variant = _variant_name(bias, spread)

    out: list[EnsembleReplayRow] = []
    for row in test_rows:
        members = _all_members(row)
        yes_won = _yes_won(row)
        raw_p = shadow_ensemble.ensemble_prob_between(members, row.get("lower_f"), row.get("upper_f"))
        calibrated_p = calibrated_member_probability(
            members,
            row.get("lower_f"),
            row.get("upper_f"),
            bias_offset_f=bias,
            spread_multiplier=spread,
        )
        if raw_p is None or calibrated_p is None:
            continue
        mean = statistics.fmean(members)
        sigma = max(SIGMA_FLOOR_F, statistics.pstdev(members) if len(members) >= 2 else 0.0)
        original_p = float(row["original_p_yes"])
        out.append(
            EnsembleReplayRow(
                signal_id=int(row["signal_id"]),
                ticker=row["ticker"],
                ts=row["ts"],
                station=row["station"],
                valid_date=row["valid_date"],
                lead_day=int(row["lead_day"]),
                yes_won=yes_won,
                n_members=len(members),
                member_mean_f=mean,
                member_sigma_f=sigma,
                original_p_yes=original_p,
                raw_member_p_yes=raw_p,
                calibrated_p_yes=calibrated_p,
                calibrated_variant=variant,
                original_brier=_brier(original_p, yes_won),
                raw_member_brier=_brier(raw_p, yes_won),
                calibrated_brier=_brier(calibrated_p, yes_won),
            )
        )

    meta = {
        "bias": bias,
        "spread": spread,
        "train_brier": train_brier,
        "train_n": len(train_rows),
        "test_n": len(out),
        "raw_rows": len(raw_rows),
    }
    return out, meta


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _summary(rows: list[EnsembleReplayRow]) -> dict:
    if not rows:
        return {}
    return {
        "n": len(rows),
        "original_brier": _mean([r.original_brier for r in rows]),
        "raw_member_brier": _mean([r.raw_member_brier for r in rows]),
        "calibrated_brier": _mean([r.calibrated_brier for r in rows]),
    }


def _fmt(value: float | None, digits: int = 4) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def write_csv(rows: list[EnsembleReplayRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def render_markdown(rows: list[EnsembleReplayRow], meta: dict, days_back: int) -> str:
    s = _summary(rows)
    variant = _variant_name(float(meta["bias"]), float(meta["spread"]))
    lines = [
        f"# Calibrated Ensemble Replay - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Window: last {days_back} completed valid dates. Research-only; production weights are unchanged.",
        f"Train/test split is chronological: train n={meta.get('train_n')}, test n={meta.get('test_n')}.",
        f"Best train variant: `{variant}` with train Brier {_fmt(meta.get('train_brier'))}.",
        "",
        "## Test Set",
        "",
        "| n | original Brier | raw member Brier | calibrated member Brier | cal vs original | cal vs raw |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    if s:
        lines.append(
            f"| {s['n']} | {_fmt(s['original_brier'])} | {_fmt(s['raw_member_brier'])} | "
            f"{_fmt(s['calibrated_brier'])} | "
            f"{s['calibrated_brier'] - s['original_brier']:+.4f} | "
            f"{s['calibrated_brier'] - s['raw_member_brier']:+.4f} |"
        )
    else:
        lines.append("| 0 | - | - | - | - | - |")

    lines.extend([
        "",
        "## By Station / Lead",
        "",
        "| station | lead | n | original | raw member | calibrated | cal vs original |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    groups: dict[tuple[str, int], list[EnsembleReplayRow]] = {}
    for row in rows:
        groups.setdefault((row.station, row.lead_day), []).append(row)
    for (station, lead), vals in sorted(groups.items()):
        gs = _summary(vals)
        lines.append(
            f"| {station} | {lead} | {gs['n']} | {_fmt(gs['original_brier'])} | "
            f"{_fmt(gs['raw_member_brier'])} | {_fmt(gs['calibrated_brier'])} | "
            f"{gs['calibrated_brier'] - gs['original_brier']:+.4f} |"
        )

    lines.extend([
        "",
        "## Interpretation",
        "",
        "- Negative `cal vs original` means the calibrated ensemble beat the bot's logged probability.",
        "- Negative `cal vs raw` means spread/bias calibration improved raw member counting.",
        "- This is still event-correlated because many signals share the same station-day outcome. Promote nothing without reliability bins and more settled dates.",
    ])
    return "\n".join(lines) + "\n"


def run(days_back: int = 30, out_dir: Path = Path("research/reports"), limit: int | None = None, per_group_limit: int = 200) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, meta = replay(days_back=days_back, limit=limit, per_group_limit=per_group_limit)
    stem = f"ensemble_calibration_{date.today()}"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"
    write_csv(rows, csv_path)
    md_path.write_text(render_markdown(rows, meta, days_back))
    return {"rows": len(rows), "csv_path": str(csv_path), "report_path": str(md_path), "meta": meta}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--per-group-limit", type=int, default=200)
    args = parser.parse_args()
    result = run(days_back=args.days_back, limit=args.limit, per_group_limit=args.per_group_limit)
    print(Path(result["report_path"]).read_text())
