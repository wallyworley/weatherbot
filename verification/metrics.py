"""
Forecast verification.

We compute three core metrics nightly against settled daily_obs:

  - Brier score (bucket-level): average (forecast_prob - outcome)^2 across
    all buckets of a given event.
  - CRPS (distributional): integral of (CDF - H(x - obs))^2 dx, which is the
    proper scoring rule for continuous probabilistic forecasts. Lower = better.
  - Log loss: -log(prob_assigned_to_observed_bucket), penalizes overconfidence.

Plus the reliability diagram: for forecasts in each prob bin (e.g., 0-10,
10-20, ..., 90-100%), what fraction actually realized? A well-calibrated
forecaster's curve hugs the diagonal.

These outputs land in the `verification` table. Plot them externally (Grafana
or a Jupyter notebook) — we're not building a UI in the bot itself.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

import numpy as np

# NumPy 2.0 renamed np.trapz -> np.trapezoid. Support both.
_trapezoid = getattr(np, "trapezoid", None) or np.trapz  # type: ignore[attr-defined]

from weather_bot.config import ACTIVE_STATIONS
from weather_bot.data import persistence
from weather_bot.models.distribution import PiecewiseCDF, build_station_distribution, lead_day_for_station

log = logging.getLogger(__name__)

# Fixed bucket edges (°F) for bucket-level Brier/log-loss — 1 degree bins.
BUCKET_WIDTH = 1.0
BUCKET_MIN = -20.0
BUCKET_MAX = 130.0
BUCKET_EDGES = np.arange(BUCKET_MIN, BUCKET_MAX + BUCKET_WIDTH, BUCKET_WIDTH)


def cdf_to_bucket_probs(cdf: PiecewiseCDF, edges: np.ndarray = BUCKET_EDGES) -> np.ndarray:
    lo = np.array([cdf.cdf(x) for x in edges[:-1]])
    hi = np.array([cdf.cdf(x) for x in edges[1:]])
    probs = np.clip(hi - lo, 1e-12, 1.0)
    probs = probs / probs.sum()
    return probs


def brier_bucket(probs: np.ndarray, outcome_idx: int) -> float:
    target = np.zeros_like(probs)
    target[outcome_idx] = 1.0
    return float(((probs - target) ** 2).sum())


def crps(cdf: PiecewiseCDF, obs: float, grid: np.ndarray | None = None) -> float:
    """Approximate CRPS by trapezoidal integration over a temperature grid."""
    if grid is None:
        grid = np.arange(BUCKET_MIN, BUCKET_MAX, 0.5)
    f = np.array([cdf.cdf(x) for x in grid])
    h = (grid >= obs).astype(float)
    diff2 = (f - h) ** 2
    return float(_trapezoid(diff2, grid))


def log_loss_bucket(probs: np.ndarray, outcome_idx: int) -> float:
    return float(-np.log(probs[outcome_idx]))


def _bucket_idx(obs: float, edges: np.ndarray = BUCKET_EDGES) -> int:
    i = int(np.clip(np.searchsorted(edges, obs, side="right") - 1, 0, len(edges) - 2))
    return i


def run(lookback_days: int = 14) -> None:
    """Verify the last N days of NBM-based forecasts vs. observed Tmax/Tmin."""
    today = datetime.now(tz=timezone.utc).date()
    run_date = today
    for code in ACTIVE_STATIONS:
        for var in ("TMAX_DAILY", "TMIN_DAILY"):
            per_lead: dict[int, list[dict]] = defaultdict(list)
            for i in range(1, lookback_days + 1):
                d = today - timedelta(days=i)
                obs_rows = persistence.get_daily_obs(code, d, d)
                if not obs_rows:
                    continue
                obs = obs_rows[0]["tmax_f"] if var == "TMAX_DAILY" else obs_rows[0]["tmin_f"]
                if obs is None:
                    continue
                # Re-build the distribution as it would have been at station-local midnight.
                from weather_bot.config import STATIONS
                import pytz
                tz = pytz.timezone(STATIONS[code].tz)
                local_midnight = tz.localize(datetime.combine(d, datetime.min.time()))
                now_utc = local_midnight.astimezone(timezone.utc)
                cdf = build_station_distribution(code, d, var=var, now_utc=now_utc)
                if cdf is None:
                    continue
                probs = cdf_to_bucket_probs(cdf)
                oi = _bucket_idx(obs)
                lead_day = lead_day_for_station(code, d, now_utc)
                per_lead[lead_day].append(dict(
                    brier=brier_bucket(probs, oi),
                    crps=crps(cdf, obs),
                    log_loss=log_loss_bucket(probs, oi),
                    prob_assigned=float(probs[oi]),
                ))

            for lead_day, samples in per_lead.items():
                if not samples:
                    continue
                arr_br  = np.array([s["brier"] for s in samples])
                arr_cr  = np.array([s["crps"] for s in samples])
                arr_ll  = np.array([s["log_loss"] for s in samples])
                arr_pa  = np.array([s["prob_assigned"] for s in samples])

                # Reliability diagram in 10% bins on the probability ASSIGNED to the observed bucket
                # (a rough proxy; a proper reliability diagram requires per-bucket pairs).
                bins = np.linspace(0.0, 1.0, 11)
                bin_idx = np.clip(np.digitize(arr_pa, bins) - 1, 0, 9)
                reliability = {}
                for b in range(10):
                    mask = bin_idx == b
                    if mask.any():
                        reliability[f"{int(bins[b]*100):02d}-{int(bins[b+1]*100):02d}"] = {
                            "mean_forecast_prob": float(arr_pa[mask].mean()),
                            "empirical_freq":     float(mask.sum() / len(samples)),
                            "n": int(mask.sum()),
                        }

                persistence.connect   # just to remind ourselves the import is used
                with persistence.connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO verification(run_date, station, var, lead_day,
                                                 brier, crps, log_loss, reliability, n)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (run_date, station, var, lead_day)
                        DO UPDATE SET brier=EXCLUDED.brier, crps=EXCLUDED.crps,
                                      log_loss=EXCLUDED.log_loss,
                                      reliability=EXCLUDED.reliability, n=EXCLUDED.n
                        """,
                        (run_date, code, var, lead_day,
                         float(arr_br.mean()), float(arr_cr.mean()), float(arr_ll.mean()),
                         json.dumps(reliability), int(len(samples))),
                    )
                    conn.commit()
                log.info("Verification %s %s lead=%d n=%d brier=%.4f crps=%.3f",
                         code, var, lead_day, len(samples), arr_br.mean(), arr_cr.mean())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
