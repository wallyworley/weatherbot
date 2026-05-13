"""Empirical calibration for model bucket probabilities.

The distribution builder produces a raw YES probability for a Kalshi bucket.
That raw probability can be overconfident in specific ranges. This module
shrinks the live probability toward the observed frequency of settled logged
signals in the same decile, falling back through a small hierarchy when the
most local bucket is too thin.
"""
from __future__ import annotations

from dataclasses import dataclass

from weather_bot.config import (
    PROB_CALIBRATION_DAYS_BACK,
    PROB_CALIBRATION_ENABLED,
    PROB_CALIBRATION_MAX_DELTA,
    PROB_CALIBRATION_MIN_BUCKET_N,
    PROB_CALIBRATION_PRIOR_N,
)
from weather_bot.data import persistence


@dataclass(frozen=True)
class CalibrationResult:
    raw_prob: float
    calibrated_prob: float
    applied: bool
    source: str = "none"
    bin: int | None = None
    n: int = 0
    mean_pred: float | None = None
    observed_freq: float | None = None
    shrink: float = 0.0
    delta: float = 0.0

    def note(self) -> str:
        if not self.applied:
            return "CAL|applied=0"
        return (
            f"CAL|raw={self.raw_prob:.3f}|cal={self.calibrated_prob:.3f}|"
            f"src={self.source}|bin={self.bin}|n={self.n}|"
            f"pred={self.mean_pred:.3f}|obs={self.observed_freq:.3f}|"
            f"shrink={self.shrink:.2f}|delta={self.delta:+.3f}"
        )


@dataclass(frozen=True)
class CalibrationStats:
    source: str
    n: float
    mean_pred: float
    observed_freq: float


def probability_bin(prob: float, n_bins: int = 10) -> int:
    p = min(0.999999, max(0.0, float(prob)))
    return int(p * n_bins) + 1


def shrink_to_observed(
    raw_prob: float,
    mean_pred: float,
    observed_freq: float,
    n: int,
    prior_n: float = PROB_CALIBRATION_PRIOR_N,
    max_delta: float = PROB_CALIBRATION_MAX_DELTA,
) -> tuple[float, float, float]:
    """Return (calibrated_prob, shrink, applied_delta).

    The adjustment is the observed calibration gap for the bucket, shrunk by
    n/(n+prior_n) and capped so one noisy bucket cannot violently move a live
    probability.
    """
    raw = float(raw_prob)
    if n <= 0:
        return raw, 0.0, 0.0
    shrink = n / (n + prior_n) if prior_n > 0 else 1.0
    delta = shrink * (float(observed_freq) - float(mean_pred))
    delta = max(-max_delta, min(max_delta, delta))
    calibrated = min(0.99, max(0.01, raw + delta))
    return calibrated, shrink, delta


def choose_stats(rows: list[dict], min_n: int = PROB_CALIBRATION_MIN_BUCKET_N) -> CalibrationStats | None:
    """Pick the first hierarchy row with enough effective event evidence."""
    for row in rows:
        n = float(row["n"])
        if n >= min_n:
            return CalibrationStats(
                source=str(row["source"]),
                n=n,
                mean_pred=float(row["mean_pred"]),
                observed_freq=float(row["observed_freq"]),
            )
    return None


def _bucket_stats(
    station: str,
    bin_id: int,
    days_back: int,
    lead_day: int | None = None,
) -> CalibrationStats | None:
    sql = """
    WITH signal_outcomes AS (
        SELECT km.station,
               km.ticker,
               s.side,
               s.fair_prob AS p_yes,
               GREATEST(0, (km.valid_date - (s.ts AT TIME ZONE st.tz)::date)::int) AS lead_day,
               CASE
                   WHEN truth.value_f IS NULL THEN NULL
                   WHEN (km.lower_f IS NULL OR truth.value_f >= km.lower_f)
                    AND (km.upper_f IS NULL OR truth.value_f < km.upper_f)
                   THEN 1.0
                   ELSE 0.0
               END AS yes_won
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
          JOIN stations st ON st.code = km.station
          LEFT JOIN cli_obs c ON c.station = km.station AND c.local_date = km.valid_date
          LEFT JOIN daily_obs d ON d.station = km.station AND d.local_date = km.valid_date
          LEFT JOIN LATERAL (
              SELECT CASE
                       WHEN km.var = 'TMAX_DAILY' THEN COALESCE(c.tmax_f, d.tmax_f)
                       WHEN km.var = 'TMIN_DAILY' THEN COALESCE(c.tmin_f, d.tmin_f)
                       ELSE NULL
                     END AS value_f
          ) truth ON TRUE
         WHERE km.valid_date >= CURRENT_DATE - (%s || ' days')::interval
           AND s.fair_prob IS NOT NULL
           AND km.station IS NOT NULL
           AND km.valid_date IS NOT NULL
           AND km.var IN ('TMAX_DAILY', 'TMIN_DAILY')
    ), binned AS (
        SELECT station,
               ticker,
               lead_day,
               LEAST(10, GREATEST(1, WIDTH_BUCKET(p_yes, 0, 1, 10))) AS bin,
               p_yes,
               yes_won
          FROM signal_outcomes
         WHERE yes_won IS NOT NULL
    ), weighted AS (
        SELECT *,
               1.0 / COUNT(*) OVER (PARTITION BY ticker, bin) AS event_weight
          FROM binned
    ), bucketed AS (
        SELECT *
          FROM weighted
         WHERE bin = %s
    )
    SELECT source, n, mean_pred, observed_freq
      FROM (
        SELECT 'station_lead' AS source,
               SUM(event_weight) AS n,
               SUM(p_yes * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
               SUM(yes_won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
               0 AS priority
          FROM bucketed
         WHERE station = %s AND %s::int IS NOT NULL AND lead_day = %s
        UNION ALL
        SELECT 'lead' AS source,
               SUM(event_weight) AS n,
               SUM(p_yes * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
               SUM(yes_won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
               1 AS priority
          FROM bucketed
         WHERE %s::int IS NOT NULL AND lead_day = %s
        UNION ALL
        SELECT 'station' AS source,
               SUM(event_weight) AS n,
               SUM(p_yes * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
               SUM(yes_won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
               2 AS priority
          FROM bucketed
         WHERE station = %s
        UNION ALL
        SELECT 'global' AS source,
               SUM(event_weight) AS n,
               SUM(p_yes * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
               SUM(yes_won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
               3 AS priority
          FROM bucketed
      ) stats
     WHERE n > 0 AND mean_pred IS NOT NULL AND observed_freq IS NOT NULL
     ORDER BY priority
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                days_back,
                bin_id,
                station, lead_day, lead_day,
                lead_day, lead_day,
                station,
            ),
        )
        rows = cur.fetchall()
    return choose_stats([dict(row) for row in rows])


def calibrate_fair_probability(
    station: str,
    raw_prob: float,
    days_back: int = PROB_CALIBRATION_DAYS_BACK,
    lead_day: int | None = None,
) -> CalibrationResult:
    """Calibrate raw YES probability using settled paper-fill reliability."""
    raw = min(0.99, max(0.01, float(raw_prob)))
    if not PROB_CALIBRATION_ENABLED:
        return CalibrationResult(raw, raw, applied=False)

    bin_id = probability_bin(raw)
    try:
        stats = _bucket_stats(station, bin_id, days_back, lead_day=lead_day)
    except Exception:
        return CalibrationResult(raw, raw, applied=False, bin=bin_id)
    if not stats:
        return CalibrationResult(raw, raw, applied=False, bin=bin_id)

    calibrated, shrink, delta = shrink_to_observed(
        raw,
        stats.mean_pred,
        stats.observed_freq,
        int(round(stats.n)),
    )
    return CalibrationResult(
        raw_prob=raw,
        calibrated_prob=calibrated,
        applied=abs(calibrated - raw) > 1e-9,
        source=stats.source,
        bin=bin_id,
        n=int(round(stats.n)),
        mean_pred=stats.mean_pred,
        observed_freq=stats.observed_freq,
        shrink=shrink,
        delta=delta,
    )
