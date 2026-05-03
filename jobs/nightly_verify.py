"""Nightly forecast verification (Brier / CRPS / log-loss).

Verification-only: does NOT recompute bias tables. Bias retraining is owned
by `jobs.retrain_bias` which uses a point-in-time-safe SQL path with proper
timezone-aware lead_day arithmetic. Previously this script also called
`bias_correction.recompute()` which would overwrite retrain_bias's output
with the older convention (different lead_day formula, no tz handling) —
removed 2026-05-03 after a code review caught the clobber.
"""
import logging

from weather_bot.verification import metrics

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    metrics.run()
