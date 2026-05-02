#!/bin/bash
# Pull GFS hourly forecasts via Open-Meteo into det_forecast.
set -e

cd /Users/walterworley/dev/weather_bot
set -a
[[ -f .env ]] && source .env
set +a
/Users/walterworley/dev/weather_bot/.venv/bin/python -m weather_bot.data.gfs_fetcher
