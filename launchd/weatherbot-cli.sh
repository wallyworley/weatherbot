#!/bin/bash
# Pull NWS CLI (Daily Climate Report) for each fetch station — Kalshi NHIGH
# settlement authority. Writes to cli_obs; settle_paper_fills prefers it
# over METAR-derived daily_obs.
set -e

cd /Users/walterworley/dev/weather_bot
set -a
[[ -f .env ]] && source .env
set +a
/Users/walterworley/dev/weather_bot/.venv/bin/python -m weather_bot.jobs.pull_cli --days-back 2
