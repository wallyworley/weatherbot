# Forecast Update Lag Report - 2026-05-17

_generated 2026-05-17 00:38 UTC_

Window: last 30 days. Signed movement is positive when the YES market moves toward our fair probability.

## Overall Signals

| cohort | n | avg abs prob edge | avg z | signed 15m | signed 30m | signed 60m |
|---|---:|---:|---:|---:|---:|---:|
| all | 2500 | 0.145 | 2.91 | 0.0027 | 0.0038 | 0.0037 |
| OPEN only | 239 | 0.180 | 3.60 | -0.0047 | -0.0063 | -0.0114 |

## By Probability-Edge Z

| z bucket | n | avg abs prob edge | avg z | signed 15m | signed 30m | signed 60m |
|---|---:|---:|---:|---:|---:|---:|
| <1 | 416 | 0.028 | 0.55 | 0.0047 | 0.0041 | -0.0004 |
| 1-2 | 579 | 0.078 | 1.55 | 0.0017 | 0.0042 | 0.0071 |
| 2-3 | 572 | 0.124 | 2.48 | 0.0032 | 0.0056 | 0.0027 |
| 3+ | 933 | 0.253 | 5.06 | 0.0023 | 0.0024 | 0.0041 |

## By Freshest Forecast Age

| age bucket | n | avg abs prob edge | avg z | signed 15m | signed 30m | signed 60m |
|---|---:|---:|---:|---:|---:|---:|
| <15m | 642 | 0.149 | 2.98 | -0.0029 | -0.0019 | 0.0084 |
| 15-60m | 1195 | 0.158 | 3.15 | 0.0082 | 0.0111 | 0.0068 |
| 1-3h | 518 | 0.120 | 2.40 | -0.0014 | -0.0034 | -0.0046 |
| 3-6h | 145 | 0.118 | 2.35 | -0.0013 | -0.0042 | -0.0074 |

## By Station / Lead

| station/lead | n | avg abs prob edge | avg z | signed 15m | signed 30m | signed 60m |
|---|---:|---:|---:|---:|---:|---:|
| KMDW L0 | 343 | 0.176 | 3.52 | 0.0027 | 0.0035 | -0.0057 |
| KMDW L1 | 571 | 0.131 | 2.62 | -0.0010 | -0.0015 | -0.0017 |
| KMIA L0 | 91 | 0.224 | 4.47 | -0.0249 | -0.0232 | -0.0466 |
| KMIA L1 | 542 | 0.169 | 3.38 | -0.0008 | -0.0027 | -0.0049 |
| KNYC L0 | 305 | 0.169 | 3.38 | 0.0317 | 0.0442 | 0.0687 |
| KNYC L1 | 648 | 0.100 | 2.00 | -0.0010 | -0.0022 | -0.0044 |

## Interpretation

- If high-z rows have positive signed movement, a probability-edge z gate has evidence.
- If fresh-age rows have stronger signed movement than stale rows, forecast-update lag is actionable.
- This is market movement, not realized P&L. It should inform order timing and TTL/reprice rules before it informs sizing.
