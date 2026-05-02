#!/bin/bash
# Weekly per-bucket calibration drift snapshot. Surfaces miscalibrated bins
# and bin-10 (90-100%) drift since prior snapshot.
set -e

cd /Users/walterworley/dev/weather_bot
set -a
[[ -f .env ]] && source .env
set +a
/Users/walterworley/dev/weather_bot/.venv/bin/python -m weather_bot.jobs.calibration_drift
