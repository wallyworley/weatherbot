"""Cron job: pull true ensemble member forecasts via Open-Meteo."""
import argparse
import logging

from weather_bot.data import openmeteo_ensemble_fetcher


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        default=",".join(openmeteo_ensemble_fetcher.ENSEMBLE_MODELS),
        help="Comma-separated internal model labels to pull.",
    )
    parser.add_argument("--horizon-days", type=int, default=openmeteo_ensemble_fetcher.HORIZON_DAYS)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    requested = {m.strip().upper() for m in args.models.split(",") if m.strip()}
    models = {
        label: om_label
        for label, om_label in openmeteo_ensemble_fetcher.ENSEMBLE_MODELS.items()
        if label in requested
    }
    if not models:
        raise SystemExit(f"No known ensemble models requested: {sorted(requested)}")
    openmeteo_ensemble_fetcher.run(models=models, horizon_days=args.horizon_days)
