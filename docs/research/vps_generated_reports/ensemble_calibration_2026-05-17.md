# Calibrated Ensemble Replay - 2026-05-17

_generated 2026-05-17 00:31 UTC_

Window: last 30 completed valid dates. Research-only; production weights are unchanged.
Train/test split is chronological: train n=840, test n=360.
Best train variant: `bias=+2.0,spread=1.00` with train Brier 0.1412.

## Test Set

| n | original Brier | raw member Brier | calibrated member Brier | cal vs original | cal vs raw |
|---:|---:|---:|---:|---:|---:|
| 360 | 0.0386 | 0.1725 | 0.1503 | +0.1117 | -0.0223 |

## By Station / Lead

| station | lead | n | original | raw member | calibrated | cal vs original |
|---|---:|---:|---:|---:|---:|---:|
| KMDW | 0 | 120 | 0.0335 | 0.1483 | 0.1265 | +0.0930 |
| KMIA | 0 | 120 | 0.0052 | 0.2178 | 0.2002 | +0.1950 |
| KNYC | 0 | 120 | 0.0772 | 0.1515 | 0.1241 | +0.0470 |

## Interpretation

- Negative `cal vs original` means the calibrated ensemble beat the bot's logged probability.
- Negative `cal vs raw` means spread/bias calibration improved raw member counting.
- This is still event-correlated because many signals share the same station-day outcome. Promote nothing without reliability bins and more settled dates.
