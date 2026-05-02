"""Weekly per-bucket calibration drift snapshot + detector.

Captures the current per-bucket calibration state (from settled paper fills,
30-day window, 10 deciles) and compares against the prior snapshot to flag
drift. Output goes to:

    research/reports/calibration_<YYYY-MM-DD>.json   (raw snapshot)
    research/reports/calibration_drift.log           (one-line drift summary per run)

Surfaces:
- Bin 10 (90–100% predicted) — the most-traded confidence range
- Any bin where mean_pred lands outside the Wilson 95% CI of observed_freq
- Drift in bin 10 gap since prior snapshot (>0.05 absolute = significant)

Intended cadence: weekly via launchd. Pure read; no DB writes.

Companion to:
- dashboard/queries.py::bucket_calibration (the same query)
- dashboard/app.py per-bucket calibration table (the same data, rendered)
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import date
from pathlib import Path
from typing import Optional

from weather_bot.dashboard.queries import bucket_calibration

log = logging.getLogger(__name__)

REPORTS_DIR = Path("research/reports")
DRIFT_THRESHOLD = 0.05      # absolute gap-change in bin 10 that triggers attention
GAP_FLAG = 0.10             # |mean_pred - observed_freq| above which a bin is "miscalibrated"


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _load_prior_snapshot(today: date) -> Optional[dict]:
    """Most recent calibration snapshot strictly before today's date."""
    if not REPORTS_DIR.exists():
        return None
    candidates = sorted(REPORTS_DIR.glob("calibration_*.json"))
    candidates = [c for c in candidates if c.stem != f"calibration_{today}"]
    if not candidates:
        return None
    with candidates[-1].open() as f:
        return {"path": str(candidates[-1]), "data": json.load(f)}


def _format_bin10(snapshot_rows: list[dict]) -> Optional[dict]:
    for r in snapshot_rows:
        if int(r["bin"]) == 10:
            return r
    return None


def run(days_back: int = 30) -> dict:
    today = date.today()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    df = bucket_calibration(days_back=days_back, n_bins=10)
    if df.empty:
        log.warning("No settled fills in last %d days — skipping snapshot", days_back)
        return {"status": "no_data"}

    rows = df.to_dict(orient="records")
    out_path = REPORTS_DIR / f"calibration_{today}.json"
    out_path.write_text(json.dumps(rows, default=str, indent=2))
    log.info("snapshot written to %s (%d bins)", out_path, len(rows))

    flagged: list[dict] = []
    for r in rows:
        n = int(r["n"] or 0)
        n_won = int(r["n_won"] or 0)
        mean_pred = float(r["mean_pred"])
        observed = float(r["observed_freq"])
        ci_lo, ci_hi = _wilson(n_won, n)
        if mean_pred < ci_lo or mean_pred > ci_hi:
            flagged.append({"bin": int(r["bin"]), "n": n, "mean_pred": mean_pred,
                              "observed": observed, "ci_lo": ci_lo, "ci_hi": ci_hi})

    bin10_now = _format_bin10(rows)
    bin10_gap_now = (float(bin10_now["mean_pred"]) - float(bin10_now["observed_freq"])) if bin10_now else None
    bin10_drift = None
    prior_path = None
    prior = _load_prior_snapshot(today)
    if prior is not None:
        prior_path = prior["path"]
        bin10_prior = _format_bin10(prior["data"])
        if bin10_now and bin10_prior:
            bin10_gap_prior = float(bin10_prior["mean_pred"]) - float(bin10_prior["observed_freq"])
            bin10_drift = bin10_gap_now - bin10_gap_prior

    significant_drift = bin10_drift is not None and abs(bin10_drift) >= DRIFT_THRESHOLD

    result = {
        "status": "ok",
        "snapshot_path": str(out_path),
        "prior_snapshot_path": prior_path,
        "bin10_n": int(bin10_now["n"]) if bin10_now else 0,
        "bin10_predicted": float(bin10_now["mean_pred"]) if bin10_now else None,
        "bin10_observed": float(bin10_now["observed_freq"]) if bin10_now else None,
        "bin10_gap": bin10_gap_now,
        "bin10_drift_since_prior": bin10_drift,
        "miscalibrated_bins": flagged,
        "significant_drift": significant_drift,
    }
    log.info("bin10: n=%s predicted=%.3f observed=%.3f gap=%+.3f drift=%s flagged_bins=%d %s",
             result["bin10_n"], result["bin10_predicted"] or 0.0,
             result["bin10_observed"] or 0.0, result["bin10_gap"] or 0.0,
             f"{bin10_drift:+.3f}" if bin10_drift is not None else "no-prior",
             len(flagged),
             "⚠ DRIFT" if significant_drift else "")

    # Append a one-line summary to the running drift log.
    log_path = REPORTS_DIR / "calibration_drift.log"
    line = (f"{today} bin10_n={result['bin10_n']} "
            f"pred={result['bin10_predicted']:.3f} obs={result['bin10_observed']:.3f} "
            f"gap={result['bin10_gap']:+.3f} "
            f"drift={'%+.3f' % bin10_drift if bin10_drift is not None else 'NA'} "
            f"flagged={len(flagged)}{' ⚠' if significant_drift else ''}\n")
    with log_path.open("a") as f:
        f.write(line)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-back", type=int, default=30)
    ap.add_argument("--json", action="store_true", help="Also dump full result as JSON to stdout")
    args = ap.parse_args()
    result = run(days_back=args.days_back)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
