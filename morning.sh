#!/usr/bin/env bash
#
# Morning paper trading routine. Run once per day AFTER Kalshi market close
# (markets close at 04:59 UTC = 00:59 EDT the following day, so any time
# after ~1 AM local is safe).
#
# Steps:
#   1. Pull final METAR — locks in yesterday's daily TMAX/TMIN
#   2. Settle unsettled paper fills against observed daily_obs
#   3. Print P&L report + realized vs expected edge diff (the calibration signal)
#   4. Retrain bias table from full history (runs after new obs land)
#   5. Run nightly verification — brier / CRPS / log-loss over history
#
# Usage:
#   ./morning.sh                 # run all five steps
#   ./morning.sh --skip-verify   # skip the (slower) verification step
#

set -euo pipefail

# Pin CWD to the script's directory so relative paths work regardless of
# where it's invoked from.
cd "$(dirname "$0")"

# Activate venv.
if [[ -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    echo "ERROR: .venv not found. Run 'python -m venv .venv && pip install -r requirements.txt' first." >&2
    exit 1
fi

SKIP_VERIFY=0
for arg in "$@"; do
    case "$arg" in
        --skip-verify) SKIP_VERIFY=1 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \?//'
            exit 0
            ;;
    esac
done

hr() { printf '\n\033[1;36m==[ %s ]==\033[0m\n' "$1"; }

hr "1/4  Pull latest METAR (fills in yesterday's daily_obs)"
python -m weather_bot.jobs.pull_metar

hr "2/4  Settle unsettled paper fills"
python -m weather_bot.jobs.settle_paper_fills

hr "3/4  Paper trading report (last 7 days)"
python -m weather_bot.jobs.paper_report --days 7 --show-open

hr "4/5  Retrain bias table (full history, point-in-time safe)"
python -m weather_bot.jobs.retrain_bias

if [[ $SKIP_VERIFY -eq 0 ]]; then
    hr "5/5  Nightly verification (Brier / CRPS / log-loss)"
    python -m weather_bot.jobs.nightly_verify
else
    hr "5/5  Verification skipped (--skip-verify)"
fi

echo
echo "Done. Check the 'EXPECTED vs REALIZED EDGE' section above — if the"
echo "diff is large and negative, the model was over-confident and we need"
echo "to revisit HRRR weighting or interior-knot scaling."
