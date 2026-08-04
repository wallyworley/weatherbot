# Shadow Ensemble Replay — 2026-05-16

_generated 2026-05-16 22:14 UTC_

Window: last 30 completed valid dates. This is research-only; it does not affect trading.
Shadow P&L is an unconstrained replay of every eligible signal, so use Brier/calibration first and dollars only as a rough stress test.
True ensemble rows: 1200; point-blend fallback rows: 0.

## Overall

| n | original Brier | shadow Brier | delta | shadow opens | shadow P&L |
|---:|---:|---:|---:|---:|---:|
| 1200 | 0.0947 | 0.1925 | +0.0978 | 839 | $-6061.76 |

## By station / lead

| station | lead | n | original Brier | shadow Brier | delta | shadow P&L |
|---|---:|---:|---:|---:|---:|---:|
| KMDW | 0 | 200 | 0.0504 | 0.1475 | +0.0971 | $-2684.98 |
| KMDW | 1 | 200 | 0.1558 | 0.1445 | -0.0113 | $+3362.75 |
| KMIA | 0 | 200 | 0.0052 | 0.2212 | +0.2160 | $-3012.62 |
| KMIA | 1 | 200 | 0.1469 | 0.2486 | +0.1017 | $-1341.69 |
| KNYC | 0 | 200 | 0.0743 | 0.1500 | +0.0757 | $-2155.28 |
| KNYC | 1 | 200 | 0.1354 | 0.2429 | +0.1075 | $-229.94 |

## Promotion rule

- Keep this shadow-only until it beats original Brier on at least 50 settled signals.
- Then replay fixed-size P&L and reliability bins before using it in `main.py`.
