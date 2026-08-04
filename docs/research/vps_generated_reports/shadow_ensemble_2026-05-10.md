# Shadow Ensemble Replay — 2026-05-10

_generated 2026-05-10 19:47 UTC_

Window: last 14 completed valid dates. This is research-only; it does not affect trading.
Shadow P&L is an unconstrained replay of every eligible signal, so use Brier/calibration first and dollars only as a rough stress test.

## Overall

| n | original Brier | shadow Brier | delta | shadow opens | shadow P&L |
|---:|---:|---:|---:|---:|---:|
| 300 | 0.1017 | 0.1465 | +0.0448 | 273 | $-1211.50 |

## By station / lead

| station | lead | n | original Brier | shadow Brier | delta | shadow P&L |
|---|---:|---:|---:|---:|---:|---:|
| KMDW | 0 | 50 | 0.0867 | 0.1663 | +0.0797 | $-954.86 |
| KMDW | 1 | 50 | 0.1540 | 0.1318 | -0.0222 | $+1960.91 |
| KMIA | 0 | 50 | 0.0681 | 0.1741 | +0.1060 | $-886.54 |
| KMIA | 1 | 50 | 0.1133 | 0.1164 | +0.0031 | $-555.02 |
| KNYC | 0 | 50 | 0.0510 | 0.1777 | +0.1267 | $-961.52 |
| KNYC | 1 | 50 | 0.1371 | 0.1128 | -0.0243 | $+185.53 |

## Promotion rule

- Keep this shadow-only until it beats original Brier on at least 50 settled signals.
- Then replay fixed-size P&L and reliability bins before using it in `main.py`.
