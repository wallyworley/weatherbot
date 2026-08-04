# Shadow Ensemble Replay — 2026-05-15

_generated 2026-05-15 19:29 UTC_

Window: last 30 completed valid dates. This is research-only; it does not affect trading.
Shadow P&L is an unconstrained replay of every eligible signal, so use Brier/calibration first and dollars only as a rough stress test.
True ensemble rows: 0; point-blend fallback rows: 1200.

## Overall

| n | original Brier | shadow Brier | delta | shadow opens | shadow P&L |
|---:|---:|---:|---:|---:|---:|
| 1200 | 0.0991 | 0.1399 | +0.0409 | 1002 | $-14335.75 |

## By station / lead

| station | lead | n | original Brier | shadow Brier | delta | shadow P&L |
|---|---:|---:|---:|---:|---:|---:|
| KMDW | 0 | 200 | 0.0773 | 0.1182 | +0.0409 | $-3104.28 |
| KMDW | 1 | 200 | 0.1432 | 0.1136 | -0.0296 | $-1309.91 |
| KMIA | 0 | 200 | 0.0049 | 0.1565 | +0.1516 | $-3959.48 |
| KMIA | 1 | 200 | 0.1580 | 0.1667 | +0.0087 | $-1502.22 |
| KNYC | 0 | 200 | 0.0497 | 0.1298 | +0.0802 | $-3391.04 |
| KNYC | 1 | 200 | 0.1613 | 0.1548 | -0.0065 | $-1068.82 |

## Promotion rule

- Keep this shadow-only until it beats original Brier on at least 50 settled signals.
- Then replay fixed-size P&L and reliability bins before using it in `main.py`.
