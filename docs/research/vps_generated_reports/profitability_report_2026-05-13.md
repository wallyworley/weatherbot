# Profitability Report — 2026-05-13

Window: last 14 days.

## 1. Maker / wait-for-better-entry replay

Conservative proxy: count a fill only if a later snapshot crossed better than the actual entry price.

| improvement | reviewed | filled | fill rate | missed | gross savings | missed-fill P&L |
|---:|---:|---:|---:|---:|---:|---:|
| 1c | 100 | 81 | 81.0% | 19 | $+97.29 | $+78.71 |
| 2c | 100 | 77 | 77.0% | 23 | $+186.26 | $+191.46 |
| 3c | 100 | 76 | 76.0% | 24 | $+278.58 | $+198.63 |

## 2. Early-exit replay

Exit rule: first snapshot where mark-to-market reaches a fraction of max gain.

| threshold | reviewed | hits | hit rate | exit P&L | held P&L for hits | delta |
|---:|---:|---:|---:|---:|---:|---:|
| 50% | 100 | 50 | 50.0% | $+251.16 | $+251.46 | $-0.30 |
| 70% | 100 | 44 | 44.0% | $+317.62 | $+301.39 | $+16.23 |
| 85% | 100 | 32 | 32.0% | $+360.61 | $+300.24 | $+60.37 |

## 3. Divergence replay

Replay DIVERGENCE skips as fixed-size paper entries with corrected order-level fees.

- Replayable divergence skips: 596
- Win rate: 16.8%
- Net P&L: $-4,943.97

| station | side | divergence band | n | win rate | net P&L | P&L/trade |
|---|---|---|---:|---:|---:|---:|
| KMDW | NO | 50-60pp | 34 | 14.7% | $-309.96 | $-9.12 |
| KMDW | NO | 60-70pp | 55 | 0.0% | $-1,166.47 | $-21.21 |
| KMDW | NO | 70pp+ | 2 | 0.0% | $-42.62 | $-21.31 |
| KMDW | YES | 50-60pp | 25 | 0.0% | $-522.09 | $-20.88 |
| KMDW | YES | 60-70pp | 7 | 0.0% | $-145.10 | $-20.73 |
| KMDW | YES | 70pp+ | 14 | 0.0% | $-297.81 | $-21.27 |
| KMIA | NO | 50-60pp | 21 | 0.0% | $-437.18 | $-20.82 |
| KMIA | NO | 60-70pp | 16 | 0.0% | $-338.70 | $-21.17 |
| KMIA | NO | 70pp+ | 13 | 0.0% | $-277.12 | $-21.32 |
| KMIA | YES | 50-60pp | 7 | 14.3% | $-53.59 | $-7.66 |
| KMIA | YES | 60-70pp | 5 | 0.0% | $-106.80 | $-21.36 |
| KMIA | YES | 70pp+ | 5 | 0.0% | $-106.72 | $-21.34 |
| KNYC | NO | 50-60pp | 61 | 27.9% | $-544.95 | $-8.93 |
| KNYC | NO | 60-70pp | 17 | 0.0% | $-356.11 | $-20.95 |
| KNYC | NO | 70pp+ | 51 | 0.0% | $-1,081.50 | $-21.21 |
| KNYC | YES | 50-60pp | 42 | 14.3% | $-592.79 | $-14.11 |
| KNYC | YES | 60-70pp | 29 | 10.3% | $-433.11 | $-14.93 |
| KNYC | YES | 70pp+ | 192 | 35.4% | $+1,868.65 | $+9.73 |

## Suggested interpretation

- Ship maker-first only if savings are positive after accounting for missed-fill P&L and fill rate stays acceptable.
- Ship early exits only if the exit delta remains positive across multiple thresholds.
- Never enable all DIVERGENCE automatically; use grouped results to identify a narrow station/side/band exception.
