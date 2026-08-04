# Point-in-Time Replay — 2026-05-24 03:00 UTC

Replayed `225` settled paper fills from the last `45` days using forecasts with `run_time <= fill.ts`. Calibrator `ON`. Bias lookup uses `station_bias_history` (PIT) with current-table fallback.

## PIT vs Original Signal Brier

- Paired fills: `225`
- Brier (PIT replay): `0.1640`
- Brier (original signal): `0.1770`
- Delta: `-0.0131` (PIT better)
- Mean p_side PIT: `0.505` | original: `0.574`

## Overall

| Stratum | n | Brier | LogLoss | Pred win | Obs win | Calib err | Realized P&L | Expected P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `all` | 225 | 0.1640 | 0.4988 | 0.505 | 0.387 | +0.118 | $-3595.98 | $+3315.86 |

## By station

| Stratum | n | Brier | LogLoss | Pred win | Obs win | Calib err | Realized P&L | Expected P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `KMDW` | 33 | 0.1469 | 0.4558 | 0.488 | 0.273 | +0.215 | $-534.29 | $+489.08 |
| `KMIA` | 57 | 0.1905 | 0.5661 | 0.496 | 0.281 | +0.215 | $-973.39 | $+1569.39 |
| `KNYC` | 135 | 0.1570 | 0.4810 | 0.512 | 0.459 | +0.053 | $-2088.30 | $+1257.38 |

## By lead day

| Stratum | n | Brier | LogLoss | Pred win | Obs win | Calib err | Realized P&L | Expected P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `L0` | 69 | 0.1770 | 0.5149 | 0.417 | 0.261 | +0.156 | $-1041.90 | $+1764.60 |
| `L1` | 156 | 0.1582 | 0.4917 | 0.543 | 0.442 | +0.101 | $-2554.08 | $+1551.26 |

## By NBM cycle hour

| Stratum | n | Brier | LogLoss | Pred win | Obs win | Calib err | Realized P&L | Expected P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `00Z` | 49 | 0.1603 | 0.4994 | 0.491 | 0.449 | +0.042 | $-779.64 | $+681.48 |
| `06Z` | 5 | 0.3523 | 0.9105 | 0.476 | 0.600 | -0.124 | $-65.37 | $+8.68 |
| `12Z` | 137 | 0.1677 | 0.5056 | 0.505 | 0.343 | +0.162 | $-2239.17 | $+2430.13 |
| `18Z` | 34 | 0.1267 | 0.4102 | 0.525 | 0.441 | +0.084 | $-511.80 | $+195.56 |

## By station × lead

| Stratum | n | Brier | LogLoss | Pred win | Obs win | Calib err | Realized P&L | Expected P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `KMDW/L0` | 13 | 0.1229 | 0.3989 | 0.373 | 0.154 | +0.219 | $-184.63 | $+386.12 |
| `KMDW/L1` | 20 | 0.1624 | 0.4928 | 0.562 | 0.350 | +0.212 | $-349.66 | $+102.96 |
| `KMIA/L0` | 15 | 0.2496 | 0.6922 | 0.503 | 0.200 | +0.303 | $-215.71 | $+383.97 |
| `KMIA/L1` | 42 | 0.1694 | 0.5210 | 0.493 | 0.310 | +0.184 | $-757.68 | $+1185.43 |
| `KNYC/L0` | 41 | 0.1676 | 0.4868 | 0.399 | 0.317 | +0.082 | $-641.56 | $+994.51 |
| `KNYC/L1` | 94 | 0.1523 | 0.4784 | 0.562 | 0.521 | +0.040 | $-1446.74 | $+262.87 |
