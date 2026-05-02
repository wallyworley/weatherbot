#!/bin/bash
# Pull atmospheric signals (BL height, 850/925mb temps, cloud, solar) per
# active fetch station from Open-Meteo's GFS feed.
set -e

cd /Users/walterworley/dev/weather_bot
set -a
[[ -f .env ]] && source .env
set +a
/Users/walterworley/dev/weather_bot/.venv/bin/python -m weather_bot.jobs.pull_atmos
