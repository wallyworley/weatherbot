#!/bin/bash
# Pull METAR for neighbor stations — multi-station spatial triangulation
# around each primary station for regional gradient features.
set -e

cd /Users/walterworley/dev/weather_bot
set -a
[[ -f .env ]] && source .env
set +a
/Users/walterworley/dev/weather_bot/.venv/bin/python -m weather_bot.jobs.pull_neighbor_metar
