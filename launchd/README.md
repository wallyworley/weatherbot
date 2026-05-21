# launchd templates

Templates for the macOS launchd agents that run the bot. **These are templates** —
you install them by copying the plist to `~/Library/LaunchAgents/` and the wrapper
shell script to `~/Library/Scripts/`, then `launchctl bootstrap` it.

## What's here

| Template | Purpose | Cadence |
|---|---|---|
| `com.walter.weatherbot-gfs.plist` + `weatherbot-gfs.sh` | Pull GFS forecasts via Open-Meteo into `det_forecast` | Hourly at :17 |
| `com.walter.weatherbot-ecmwf.plist` + `weatherbot-ecmwf.sh` | Pull ECMWF forecasts via Open-Meteo into `det_forecast` | Hourly at :27 |
| `com.walter.weatherbot-cli.plist` + `weatherbot-cli.sh` | Pull NWS CLI (Daily Climate Report) into `cli_obs` — Kalshi NHIGH settlement source | Daily at 9:23 AM ET |
| `com.walter.weatherbot-calibration-drift.plist` + `weatherbot-calibration-drift.sh` | Weekly per-bucket calibration snapshot + drift detector | Sundays at 8:23 AM |
| `com.walter.weatherbot-atmos.plist` + `weatherbot-atmos.sh` | Pull atmospheric signals (BL height, 850/925mb temps, cloud, solar) into `atmosphere_signals` | Hourly at :11 |
| `com.walter.weatherbot-neighbor-metar.plist` + `weatherbot-neighbor-metar.sh` | Pull METAR for neighbor stations (spatial triangulation around each primary) | Every 30 min at :13/:43 |

## Install one

```bash
PROJ=/Users/walterworley/dev/weather_bot
NAME=gfs   # or: cli, calibration-drift

cp $PROJ/launchd/com.walter.weatherbot-$NAME.plist ~/Library/LaunchAgents/
cp $PROJ/launchd/weatherbot-$NAME.sh ~/Library/Scripts/
chmod +x ~/Library/Scripts/weatherbot-$NAME.sh

launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.walter.weatherbot-$NAME.plist
launchctl enable     gui/$UID/com.walter.weatherbot-$NAME

# Verify it's loaded:
launchctl list | grep weatherbot-$NAME

# Force one immediate run to test:
launchctl kickstart -k gui/$UID/com.walter.weatherbot-$NAME

# Check the output:
tail -n 50 $PROJ/logs/$NAME.out $PROJ/logs/$NAME.err
```

## Uninstall

```bash
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.walter.weatherbot-$NAME.plist  # or:
launchctl bootout   gui/$UID/com.walter.weatherbot-$NAME
rm ~/Library/LaunchAgents/com.walter.weatherbot-$NAME.plist
rm ~/Library/Scripts/weatherbot-$NAME.sh
```

## Why off-minute schedules

Every plist uses an "off" minute (`:17`, `:23`) instead of `:00` or `:30`.
Hitting `:00 on the dot` means Open-Meteo, NWS API, and IEM all see the bot's
requests at the same instant as every other naive cron user on the planet.
Off-minutes spread the load and reduce timeout chance.

## Cadence rationale

- **GFS/ECMWF hourly**: Open-Meteo serves the latest deterministic runs with lag. Hourly catches each new run promptly even though the model cycles themselves are 6-hourly. Adequate for daily-temperature markets (we don't need sub-hourly forecast freshness).
- **CLI daily 9:23 AM ET**: Morning CLI (the YESTERDAY-data, settlement-grade issuance) is published ~6–8 AM ET depending on the WFO. 9:23 ET gives the latest east-coast WFO time to publish, well before the afternoon intraday CLI starts confusing things.
- **Calibration weekly**: aggregate Brier shifts on the order of 0.005–0.01/week with normal trade volume. Weekly cadence catches meaningful drift while avoiding noisy daily fluctuations.
