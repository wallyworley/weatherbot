#!/usr/bin/env bash
set -euo pipefail
cd /Users/walterworley/dev/weather_bot
set -a
[[ -f .env ]] && source .env
set +a
source .venv/bin/activate
exec python -m weather_bot.main
