# Profitability Report — 2026-05-17

Window: last 45 days.

## 1. Maker / wait-for-better-entry replay

Conservative proxy: count a fill only if a later snapshot crossed better than the actual entry price.

| improvement | reviewed | filled | fill rate | missed | gross savings | missed-fill P&L |
|---:|---:|---:|---:|---:|---:|---:|
| 1c | 173 | 139 | 80.3% | 34 | $+141.62 | $+88.91 |
| 2c | 173 | 132 | 76.3% | 41 | $+273.92 | $+187.37 |
| 3c | 173 | 130 | 75.1% | 43 | $+409.47 | $+195.28 |

## 2. Early-exit replay

Exit rule: first snapshot where mark-to-market reaches a fraction of max gain.

| threshold | reviewed | hits | hit rate | exit P&L | held P&L for hits | delta |
|---:|---:|---:|---:|---:|---:|---:|
| 50% | 173 | 79 | 45.7% | $+416.00 | $+207.82 | $+208.18 |
| 70% | 173 | 70 | 40.5% | $+469.45 | $+303.13 | $+166.32 |
| 85% | 173 | 56 | 32.4% | $+470.99 | $+306.20 | $+164.79 |

## 3. Divergence replay

Replay DIVERGENCE skips as fixed-size paper entries with corrected order-level fees.

- Replayable divergence skips: 1035
- Win rate: 11.9%
- Net P&L: $-13,051.43

| station | side | divergence band | n | win rate | net P&L | P&L/trade |
|---|---|---|---:|---:|---:|---:|
| KMDW | NO | 50-60pp | 97 | 5.2% | $-1,633.10 | $-16.84 |
| KMDW | NO | 60-70pp | 62 | 0.0% | $-1,312.85 | $-21.18 |
| KMDW | NO | 70pp+ | 53 | 0.0% | $-1,120.38 | $-21.14 |
| KMDW | YES | 50-60pp | 34 | 0.0% | $-712.98 | $-20.97 |
| KMDW | YES | 60-70pp | 8 | 0.0% | $-166.46 | $-20.81 |
| KMDW | YES | 70pp+ | 14 | 0.0% | $-297.81 | $-21.27 |
| KMIA | NO | 50-60pp | 29 | 10.3% | $-400.51 | $-13.81 |
| KMIA | NO | 60-70pp | 19 | 0.0% | $-402.66 | $-21.19 |
| KMIA | NO | 70pp+ | 14 | 0.0% | $-298.27 | $-21.31 |
| KMIA | YES | 50-60pp | 8 | 12.5% | $-74.93 | $-9.37 |
| KMIA | YES | 60-70pp | 5 | 0.0% | $-106.80 | $-21.36 |
| KMIA | YES | 70pp+ | 5 | 0.0% | $-106.72 | $-21.34 |
| KNYC | NO | 50-60pp | 140 | 25.0% | $-1,374.14 | $-9.82 |
| KNYC | NO | 60-70pp | 38 | 2.6% | $-732.44 | $-19.27 |
| KNYC | NO | 70pp+ | 77 | 1.3% | $-1,558.30 | $-20.24 |
| KNYC | YES | 50-60pp | 65 | 9.2% | $-1,075.71 | $-16.55 |
| KNYC | YES | 60-70pp | 34 | 8.8% | $-539.39 | $-15.86 |
| KNYC | YES | 70pp+ | 333 | 20.4% | $-1,137.98 | $-3.42 |

## Suggested interpretation

- Ship maker-first only if savings are positive after accounting for missed-fill P&L and fill rate stays acceptable.
- Ship early exits only if the exit delta remains positive across multiple thresholds.
- Never enable all DIVERGENCE automatically; use grouped results to identify a narrow station/side/band exception.
