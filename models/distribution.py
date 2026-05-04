"""
Probabilistic distribution builder.

Given:
  - NBM percentiles (P1..P99) for daily Tmax/Tmin at a station
  - Optional HRRR deterministic Tmax for same-day
  - Station bias correction table

Produce:
  - A continuous PDF/CDF over the temperature variable
  - Bucket probability queries: P(lo <= T < hi)

Design choices:
  - We fit a piecewise-linear CDF through the (percentile, value) knots.
    This is non-parametric and respects the calibrated NBM shape; it's
    robust to non-Gaussian tails.
  - Outside the observed percentile range we extrapolate with exponential
    tails to avoid hard clipping.
  - HRRR blend: for same-day forecasts, shift the fitted distribution's
    mean by `w * (HRRR_value - NBM_median)` where w grows as the day
    progresses. Variance is unchanged (HRRR is a single value — we don't
    know its uncertainty yet).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Sequence

import numpy as np

from weather_bot.data import persistence
from weather_bot.models import bias_correction

log = logging.getLogger(__name__)


@dataclass
class PiecewiseCDF:
    """CDF from sorted (value, percentile) knots; linear interior, exponential tails.

    Optional `floor` / `ceiling` fields encode intraday conditioning — for
    same-day TMAX we know the final high can't be below what's already been
    observed, and vice-versa for TMIN.
    """
    values: np.ndarray           # temperatures (sorted ascending)
    probs:  np.ndarray           # cumulative probabilities in (0,1), same length
    tail_scale: float = 2.5      # F degrees; scale of exponential tails
    shift: float = 0.0           # additive shift applied at query time
    floor: float | None = None   # truncate: P(X < floor) = 0
    ceiling: float | None = None # truncate: P(X > ceiling) = 0

    def _raw_cdf(self, x: float) -> float:
        """Unconditional CDF before floor/ceiling conditioning."""
        x = x - self.shift
        v, p = self.values, self.probs
        if x <= v[0]:
            # Exponential lower tail: P(X <= x) = p[0] * exp((x - v[0]) / tail_scale)
            return float(p[0] * np.exp((x - v[0]) / self.tail_scale))
        if x >= v[-1]:
            # Exponential upper tail: P(X > x) = (1 - p[-1]) * exp((v[-1] - x) / tail_scale)
            return float(1.0 - (1.0 - p[-1]) * np.exp((v[-1] - x) / self.tail_scale))
        return float(np.interp(x, v, p))

    def cdf(self, x: float) -> float:
        """CDF conditioned on floor <= X <= ceiling (if set)."""
        if self.floor is None and self.ceiling is None:
            return self._raw_cdf(x)

        lo = self.floor if self.floor is not None else -np.inf
        hi = self.ceiling if self.ceiling is not None else np.inf

        if x < lo:
            return 0.0
        if x >= hi:
            return 1.0

        raw_lo = self._raw_cdf(lo) if self.floor is not None else 0.0
        raw_hi = self._raw_cdf(hi) if self.ceiling is not None else 1.0
        denom = raw_hi - raw_lo
        if denom <= 1e-9:
            # Conditioning region has ~zero mass; treat as uniform across [lo, hi].
            if x <= lo:
                return 0.0
            if x >= hi:
                return 1.0
            return (x - lo) / (hi - lo) if np.isfinite(hi - lo) else 0.5

        return max(0.0, min(1.0, (self._raw_cdf(x) - raw_lo) / denom))

    def prob_between(self, lo: float | None, hi: float | None) -> float:
        lo_cdf = 0.0 if lo is None else self.cdf(lo)
        hi_cdf = 1.0 if hi is None else self.cdf(hi)
        return max(0.0, hi_cdf - lo_cdf)

    def quantile(self, q: float) -> float:
        return float(np.interp(q, self.probs, self.values)) + self.shift

    def median(self) -> float:
        return self.quantile(0.5)


def build_cdf_from_percentiles(percentile_rows: Sequence[dict]) -> PiecewiseCDF | None:
    """Build a CDF from a list of {percentile: int, value: float} rows."""
    if not percentile_rows:
        return None
    pairs = sorted(((float(r["percentile"]) / 100.0, float(r["value"])) for r in percentile_rows))
    # Ensure strictly monotonic values; collapse duplicates by tiny jitter.
    ps = np.array([p for p, _ in pairs])
    vs = np.array([v for _, v in pairs])
    for i in range(1, len(vs)):
        if vs[i] <= vs[i - 1]:
            vs[i] = vs[i - 1] + 1e-3
    return PiecewiseCDF(values=vs, probs=ps)


def _intraday_bounds(station: str, target_date: date, now_utc: datetime) -> tuple[float | None, float | None]:
    """Return (tmax_so_far, tmin_so_far) from METAR for the station-local day up to now.

    For same-day TMAX the day's final high can't be BELOW what's already been
    observed (persistence floor). Symmetric for TMIN (intraday min → ceiling).
    Returns (None, None) if we have no observations yet for the local day.
    """
    from weather_bot.config import STATIONS
    import pytz

    try:
        tz = pytz.timezone(STATIONS[station].tz)
    except Exception:
        return (None, None)

    # Local day window in UTC.
    local_midnight = datetime.combine(target_date, datetime.min.time())
    local_midnight = tz.localize(local_midnight)
    start_utc = local_midnight.astimezone(timezone.utc)
    end_utc = now_utc

    if end_utc <= start_utc:
        return (None, None)  # Target day is in the future.

    sql = """
        SELECT MAX(temp_f) AS tmax_so_far, MIN(temp_f) AS tmin_so_far
          FROM metar_obs
         WHERE station = %s
           AND obs_time >= %s
           AND obs_time <= %s
           AND temp_f IS NOT NULL
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, start_utc, end_utc))
        row = cur.fetchone()
        if not row:
            return (None, None)
        return (row["tmax_so_far"], row["tmin_so_far"])


def _hrrr_blend_weight(hour_local: int) -> float:
    """Weight on HRRR (vs NBM) as the local day progresses (same-day only).

    Prior curve reached only 0.33 at 10 AM and 0.8 at 4 PM. That proved too
    timid — in paper trading for KNYC 2026-04-19, HRRR projected 49.6°F (true
    TMAX was 53.1°F), but at 0.33 weight the blended distribution still placed
    p50=60.4°F. Same-day HRRR incorporates latest surface obs and current
    boundary-layer state; once past dawn, it should dominate.

    New curve:
      06h → 0.2   (HRRR already has overnight obs)
      10h → 0.6   (most uncertainty resolved)
      15h → 0.9   (peak heating, TMAX nearly locked)
      18h+ → 0.95
    """
    if hour_local < 6:
        return 0.0
    if hour_local <= 10:
        # 0.2 at 6am → 0.6 at 10am
        return 0.2 + 0.1 * (hour_local - 6)
    if hour_local <= 15:
        # 0.6 at 10am → 0.9 at 3pm
        return 0.6 + 0.06 * (hour_local - 10)
    if hour_local <= 18:
        return 0.9 + 0.017 * (hour_local - 15)
    return 0.95


def build_station_distribution(
    station: str,
    target_date: date,
    var: str = "TMAX_DAILY",
    now_utc: datetime | None = None,
    apply_bias: bool = True,
) -> PiecewiseCDF | None:
    """Assemble NBM CDF + bias correction + optional HRRR blend for a station-day.

    `apply_bias=False` skips the station_bias shift and width inflation entirely.
    Used by the divergence-bypass path in main.py to diagnose whether the bias
    correction is the source of fair-vs-market disagreement.
    """
    rows = persistence.latest_nbm_percentiles(station, target_date, var=var)
    cdf = build_cdf_from_percentiles(rows)
    if cdf is None:
        log.warning("No NBM percentiles for %s %s %s", station, target_date, var)
        return None

    now = now_utc or datetime.now(tz=timezone.utc)
    lead_day = (target_date - now.date()).days
    month = target_date.month

    # Apply station bias correction: shift mean AND inflate distribution width.
    bias_row = persistence.get_station_bias(station, "NBM_QMD", var, month, max(lead_day, 0)) if apply_bias else None
    if bias_row:
        # Bias shrinkage: the station_bias table's mean_bias_f comes from small
        # samples (often n<30). Applying raw bias at full strength pushed the
        # distribution FURTHER from reality on Apr 20 (n=19, raw bias=+4.34°F,
        # SE=2.82°F, CI straddled zero). Shrink toward zero proportional to
        # sample confidence; zero it out entirely when |mean| < SE.
        n = int(bias_row.get("sample_size") or 0)
        raw_bias = float(bias_row["mean_bias_f"])
        raw_std = float(bias_row["stddev_f"])
        target_std = raw_std

        # Empirical-Bayes style shrinkage: lambda = n / (n + k), k=10 prior strength.
        #   n=30 -> 0.75 applied | n=19 -> 0.66 | n=10 -> 0.50 | n=5 -> 0.33
        _PRIOR_N = 10
        shrink = n / (n + _PRIOR_N) if n > 0 else 0.0

        # Statistical-significance gate: if |mean| < SE(mean), the sign of the
        # bias is unreliable — zero it out rather than apply noise as signal.
        se_mean = raw_std / (n ** 0.5) if n > 0 else float("inf")
        if abs(raw_bias) < se_mean:
            shrink = 0.0

        # Staleness deweight: when the cycle is stale, its bias is dominated
        # by initialization drift (not the climatological pattern in the bias
        # table). On Apr 19, 2026, a 14h-old 00z cycle was running ~5°F cold
        # but the April climatological bias said NBM runs +4°F warm — applying
        # the bias at full strength pushed the forecast further wrong-direction.
        # Taper to zero linearly between FULL_H and ZERO_H of cycle age.
        _STALE_FULL_H = 8.0
        _STALE_ZERO_H = 18.0
        staleness = 1.0
        run_time = rows[0].get("run_time") if rows else None
        if run_time is not None:
            age_h = (now - run_time).total_seconds() / 3600.0
            if age_h > _STALE_FULL_H:
                staleness = max(0.0, 1.0 - (age_h - _STALE_FULL_H) / (_STALE_ZERO_H - _STALE_FULL_H))

        effective_bias = shrink * raw_bias * staleness
        cdf.shift -= effective_bias
        log.info(
            "Bias shrink %s/NBM_QMD/%s/m%d/l%d: n=%d raw=%+.2f se=%.2f shrink=%.2f stale=%.2f applied=%+.2f",
            station, var, month, lead_day, n, raw_bias, se_mean, shrink, staleness, effective_bias,
        )
        # Inflate interior knots to match empirical residual std, but CAP the
        # widening factor. The bias table's stddev_f comes from small samples
        # and can overshoot the true forecast width. Unbounded rescaling
        # produced bimodal-looking distributions with 76% mass in the tails.
        # 1.5x was the original cap; backtest on 58 settled fills (May 1 2026)
        # showed shoulder buckets 3-5°F from median were still over-inflated,
        # producing a cluster of YES-side fills at fair=30-45% / market=5-20%
        # that lost 7-of-8. Tightening to 1.10x improved Brier 0.139→0.133 and
        # replay P&L by ~$20 while preserving high-divergence (>=0.40) home runs.
        _MAX_WIDEN_FACTOR = 1.10
        if target_std > 0 and len(cdf.values) >= 2:
            cur_p90 = float(np.interp(0.90, cdf.probs, cdf.values))
            cur_p10 = float(np.interp(0.10, cdf.probs, cdf.values))
            current_std = (cur_p90 - cur_p10) / 2.56
            if current_std > 0 and target_std > current_std:
                scale = min(target_std / current_std, _MAX_WIDEN_FACTOR)
                median_val = float(np.interp(0.5, cdf.probs, cdf.values))
                cdf.values = median_val + scale * (cdf.values - median_val)
        # NOTE: we deliberately do NOT bump `cdf.tail_scale` up to target_std.
        # Prior behavior set it to 11°F decay on top of the widened interior,
        # which created excess tail mass. Keep the default 2.5°F — the interior
        # knot rescaling already covers the bulk of the width correction.

    from weather_bot.config import STATIONS
    import pytz
    tz = pytz.timezone(STATIONS[station].tz)
    hr_local = now.astimezone(tz).hour

    # Same-day HRRR blend.
    hrrr_used = False
    if lead_day == 0 and var == "TMAX_DAILY":
        hrrr_val = persistence.latest_hrrr_tmax(station, target_date)
        if hrrr_val is not None:
            hrrr_bias = persistence.get_station_bias(station, "HRRR", var, month, 0)
            if hrrr_bias:
                hrrr_val -= float(hrrr_bias["mean_bias_f"])
            w = _hrrr_blend_weight(hr_local)
            if w > 0:
                nbm_median = cdf.median()
                cdf.shift += w * (hrrr_val - nbm_median)
                hrrr_used = True
                log.info("HRRR blend %s %s: val=%.1f w=%.2f shift=%+.2f",
                         station, target_date, hrrr_val, w, w * (hrrr_val - nbm_median))

    # GFS blend — multi-day (lead >= 1) and same-day fallback when HRRR unavailable.
    # GFS consistently beats NBM at all stations (MAE 1.05-1.24°F vs 1.56-2.85°F),
    # so a modest constant weight is safe. Lower weight than HRRR: GFS lacks the
    # boundary-layer obs that make same-day HRRR so accurate.
    _GFS_WEIGHT = 0.30
    if var == "TMAX_DAILY" and (lead_day >= 1 or (lead_day == 0 and not hrrr_used)):
        gfs_val = persistence.latest_gfs_tmax(station, target_date)
        if gfs_val is not None:
            gfs_bias = persistence.get_station_bias(station, "GFS", var, month, max(lead_day, 0))
            if gfs_bias:
                gfs_val -= float(gfs_bias["mean_bias_f"])
            w = _GFS_WEIGHT
            nbm_median = cdf.median()
            cdf.shift += w * (gfs_val - nbm_median)
            log.info("GFS blend %s %s lead=%d: val=%.1f w=%.2f shift=%+.2f",
                     station, target_date, lead_day, gfs_val, w, w * (gfs_val - nbm_median))

    # Intraday persistence conditioning — only on same-day markets.
    # TMAX: day's final high >= max observed so far (floor).
    # TMIN: day's final low  <= min observed so far (ceiling).
    if lead_day == 0:
        tmax_so_far, tmin_so_far = _intraday_bounds(station, target_date, now)
        if var == "TMAX_DAILY" and tmax_so_far is not None:
            cdf.floor = float(tmax_so_far)
            log.info("Intraday floor for %s TMAX %s: %.1f°F", station, target_date, tmax_so_far)
        elif var == "TMIN_DAILY" and tmin_so_far is not None:
            cdf.ceiling = float(tmin_so_far)
            log.info("Intraday ceiling for %s TMIN %s: %.1f°F", station, target_date, tmin_so_far)

    return cdf


# ---------------------------------------------------------------------------
# Bucket probability helper
# ---------------------------------------------------------------------------
def bucket_probabilities(cdf: PiecewiseCDF, buckets: Sequence[tuple[float | None, float | None]]) -> list[float]:
    """Return P(lo <= T < hi) for each bucket. lo=None => -inf, hi=None => +inf."""
    return [cdf.prob_between(lo, hi) for lo, hi in buckets]
