# Profitability Report — 2026-05-10

Window: last 14 days.

## 1. Maker / wait-for-better-entry replay

Conservative proxy: count a fill only if a later snapshot crossed better than the actual entry price.

| improvement | reviewed | filled | fill rate | missed | gross savings | missed-fill P&L |
|---:|---:|---:|---:|---:|---:|---:|
| 1c | 125 | 105 | 84.0% | 20 | $+112.59 | $+80.28 |
| 2c | 125 | 100 | 80.0% | 25 | $+216.40 | $+196.27 |
| 3c | 125 | 98 | 78.4% | 27 | $+323.19 | $+204.18 |

## 2. Early-exit replay

Exit rule: first snapshot where mark-to-market reaches a fraction of max gain.

| threshold | reviewed | hits | hit rate | exit P&L | held P&L for hits | delta |
|---:|---:|---:|---:|---:|---:|---:|
| 50% | 125 | 63 | 50.4% | $+289.06 | $+184.16 | $+104.90 |
| 70% | 125 | 56 | 44.8% | $+356.74 | $+254.62 | $+102.12 |
| 85% | 125 | 43 | 34.4% | $+408.16 | $+252.73 | $+155.43 |

## 3. Divergence replay

Replay DIVERGENCE skips as fixed-size paper entries with corrected order-level fees.

- Replayable divergence skips: 618
- Win rate: 18.0%
- Net P&L: $-5,062.13

| station | side | divergence band | n | win rate | net P&L | P&L/trade |
|---|---|---|---:|---:|---:|---:|
| KMDW | NO | 50-60pp | 7 | 0.0% | $-146.46 | $-20.92 |
| KMDW | NO | 60-70pp | 12 | 0.0% | $-253.59 | $-21.13 |
| KMDW | NO | 70pp+ | 2 | 0.0% | $-42.62 | $-21.31 |
| KMDW | YES | 50-60pp | 14 | 0.0% | $-287.46 | $-20.53 |
| KMDW | YES | 60-70pp | 7 | 0.0% | $-145.10 | $-20.73 |
| KMDW | YES | 70pp+ | 14 | 0.0% | $-297.81 | $-21.27 |
| KMIA | NO | 50-60pp | 8 | 0.0% | $-167.10 | $-20.89 |
| KMIA | NO | 60-70pp | 7 | 0.0% | $-147.91 | $-21.13 |
| KMIA | NO | 70pp+ | 5 | 0.0% | $-106.42 | $-21.28 |
| KMIA | YES | 50-60pp | 3 | 33.3% | $+31.29 | $+10.43 |
| KMIA | YES | 60-70pp | 5 | 0.0% | $-106.80 | $-21.36 |
| KMIA | YES | 70pp+ | 5 | 0.0% | $-106.72 | $-21.34 |
| KNYC | NO | 50-60pp | 103 | 30.1% | $-805.50 | $-7.82 |
| KNYC | NO | 60-70pp | 25 | 4.0% | $-460.69 | $-18.43 |
| KNYC | NO | 70pp+ | 11 | 9.1% | $-159.19 | $-14.47 |
| KNYC | YES | 50-60pp | 42 | 14.3% | $-587.04 | $-13.98 |
| KNYC | YES | 60-70pp | 21 | 14.3% | $-262.95 | $-12.52 |
| KNYC | YES | 70pp+ | 327 | 20.8% | $-1,010.06 | $-3.09 |

## Suggested interpretation

- Ship maker-first only if savings are positive after accounting for missed-fill P&L and fill rate stays acceptable.
- Ship early exits only if the exit delta remains positive across multiple thresholds.
- Never enable all DIVERGENCE automatically; use grouped results to identify a narrow station/side/band exception.
