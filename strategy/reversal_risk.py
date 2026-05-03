"""Composite reversal-risk score for a Kalshi NHIGH signal.

Combines seven independent signals — most of which we built in Sprints 1-2 —
into a single 0-1 score that estimates "how likely is this prediction to flip
before settlement?"

Inspired by dailydewpoint.com's Reversal Risk panel + the OpenClaw v2 Reddit
writeup's six-signal composite. Our specific signals:

  1. Model spread       — stddev across NBM p50, HRRR daily-MAX, GFS daily-MAX
  2. Prob gap           — |fair_prob - market_mid|
  3. Boundary mass      — probability mass within ±1°F of bucket edges
  4. Time remaining     — hours until 11 PM ET (climate-day end)
  5. Overnight jump     — magnitude of NWS revision since prior evening
  6. Regional gradient  — primary station vs neighbor-network mean
  7. Rate of change     — recent °F/hour from intraday METAR

Each component is normalized to 0-1 with soft saturation, then weighted
into a composite. Output includes the score, a label (LOW/MEDIUM/HIGH),
and the per-component breakdown for diagnostic clarity.

Initially diagnostic-only — recorded on every signal in `signal.reversal_risk`
JSONB. Decision logic (sizing modifier or gate) deferred until we have a
backtest like we did for the agreement gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from weather_bot.data import persistence


# Component weights (sum = 1.0). Heavier weight on signals our research has
# directly confirmed predict reversal: model spread + boundary mass.
WEIGHTS = {
    "model_spread":      0.20,
    "prob_gap":          0.20,
    "boundary_mass":     0.20,
    "time_remaining":    0.10,
    "overnight_jump":    0.10,
    "regional_gradient": 0.10,
    "rate_of_change":    0.10,
}

# Saturation thresholds — value at which a component's risk maxes out at 1.0.
SATURATION = {
    "model_spread":      5.0,    # °F stddev across models
    "prob_gap":          0.30,   # 30 percentage points
    "boundary_mass":     0.30,   # 30% probability within ±1°F of edges
    "time_remaining":    14.0,   # 14 hours = full intraday window
    "overnight_jump":    3.0,    # °F NBM revision overnight
    "regional_gradient": 3.0,    # °F primary vs neighbor mean
    "rate_of_change":    4.0,    # °F/hour
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _hours_until_climate_day_end(station_tz: str = "America/New_York") -> float:
    """Hours until 11 PM ET (Kalshi NHIGH expiration time / NWS climate-day end)."""
    now = datetime.now(tz=ZoneInfo(station_tz))
    end = now.replace(hour=23, minute=0, second=0, microsecond=0)
    if end < now:
        end = end.replace(day=end.day + 1)   # already past 11 PM, next day
    return (end - now).total_seconds() / 3600.0


def _model_spread(station: str, valid_date: date) -> Optional[float]:
    """Stddev of (NBM p50, HRRR daily-MAX, GFS daily-MAX). Returns None if <2 models."""
    nbm = persistence.latest_nbm_percentiles(station, valid_date, "TMAX_DAILY")
    nbm_p50 = next((float(r["value"]) for r in nbm if r["percentile"] == 50), None)
    hrrr = persistence.latest_hrrr_tmax(station, valid_date)
    gfs = persistence.latest_gfs_tmax(station, valid_date)
    vals = [v for v in (nbm_p50, hrrr, gfs) if v is not None]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    return variance ** 0.5


def _boundary_mass(cdf, lower_f: Optional[float], upper_f: Optional[float],
                    epsilon: float = 1.0) -> Optional[float]:
    """Probability mass within ±epsilon of either bucket edge. High = outcome
    is on a knife-edge; small temperature shift flips win/lose."""
    if cdf is None:
        return None
    mass = 0.0
    if lower_f is not None:
        mass += cdf.prob_between(lower_f - epsilon, lower_f + epsilon)
    if upper_f is not None:
        mass += cdf.prob_between(upper_f - epsilon, upper_f + epsilon)
    return mass


@dataclass
class ReversalRisk:
    score: float
    label: str           # 'LOW' | 'MEDIUM' | 'HIGH'
    components: dict     # {name: {value, normalized, weighted_contribution}}

    def to_jsonb(self) -> dict:
        return {
            "score": round(self.score, 4),
            "label": self.label,
            "components": {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                                for kk, vv in v.items()}
                            for k, v in self.components.items()},
        }


def compute(
    station: str,
    valid_date: date,
    lower_f: Optional[float],
    upper_f: Optional[float],
    fair_prob: float,
    market_mid: Optional[float],
    cdf=None,
    station_tz: str = "America/New_York",
) -> ReversalRisk:
    """Compute composite reversal-risk score and breakdown."""
    components: dict[str, dict] = {}

    # 1. Model spread
    spread = _model_spread(station, valid_date)
    components["model_spread"] = {
        "value": round(spread, 2) if spread is not None else None,
        "normalized": _clamp01(spread / SATURATION["model_spread"]) if spread is not None else 0.0,
    }

    # 2. Prob gap
    gap = abs(fair_prob - market_mid) if market_mid is not None else None
    components["prob_gap"] = {
        "value": round(gap, 4) if gap is not None else None,
        "normalized": _clamp01(gap / SATURATION["prob_gap"]) if gap is not None else 0.0,
    }

    # 3. Boundary mass
    bmass = _boundary_mass(cdf, lower_f, upper_f)
    components["boundary_mass"] = {
        "value": round(bmass, 4) if bmass is not None else None,
        "normalized": _clamp01(bmass / SATURATION["boundary_mass"]) if bmass is not None else 0.0,
    }

    # 4. Time remaining (only meaningful for same-day; next-day signals get max)
    days_to_settle = (valid_date - date.today()).days
    if days_to_settle <= 0:
        hrs = _hours_until_climate_day_end(station_tz)
        time_norm = _clamp01(hrs / SATURATION["time_remaining"])
    else:
        time_norm = 1.0   # full window of opportunity for revision
        hrs = days_to_settle * 24
    components["time_remaining"] = {"value": round(hrs, 1), "normalized": time_norm}

    # 5. Overnight jump
    from weather_bot.dashboard.queries import nws_overnight_jump
    overnight = nws_overnight_jump(station, valid_date)
    jump = abs(overnight["jump_f"]) if overnight else None
    components["overnight_jump"] = {
        "value": round(jump, 2) if jump is not None else None,
        "normalized": _clamp01(jump / SATURATION["overnight_jump"]) if jump is not None else 0.0,
    }

    # 6. Regional gradient (only if station has neighbors defined and obs data)
    from weather_bot.data.neighbor_obs import regional_field
    field = regional_field(station)
    grad = abs(field["vs_mean"]) if field and field["n_stations"] >= 2 else None
    components["regional_gradient"] = {
        "value": round(grad, 2) if grad is not None else None,
        "normalized": _clamp01(grad / SATURATION["regional_gradient"]) if grad is not None else 0.0,
    }

    # 7. Rate of temperature change
    from weather_bot.dashboard.queries import temp_rate_of_change
    rate_obj = temp_rate_of_change(station, lookback_hours=2)
    rate = abs(rate_obj["rate_f_per_hr"]) if rate_obj else None
    components["rate_of_change"] = {
        "value": round(rate, 2) if rate is not None else None,
        "normalized": _clamp01(rate / SATURATION["rate_of_change"]) if rate is not None else 0.0,
    }

    # Composite: weighted sum of normalized components.
    score = 0.0
    for name, weight in WEIGHTS.items():
        norm = components[name]["normalized"]
        weighted = norm * weight
        components[name]["weight"] = weight
        components[name]["weighted_contribution"] = weighted
        score += weighted

    label = "HIGH" if score >= 0.60 else ("MEDIUM" if score >= 0.30 else "LOW")
    return ReversalRisk(score=score, label=label, components=components)
