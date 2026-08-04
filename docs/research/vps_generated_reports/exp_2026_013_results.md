# EXP-2026-013 — Shadow-Ensemble Market-Relative Benchmark — 2026-06-09

_generated 2026-06-09 22:40 UTC_

Locked prereg: `EXP_2026_013_ENSEMBLE_MARKET_BENCHMARK.md`. Negative dBrier/dRPS
= variant beats the market. Pass bar: BOTH negative with paired CI excluding 0,
n>=100, >=2 stations beating. Run selection: `ingested_at <= snapshot_ts`.

Cohort: lead-0 = 298, lead-1 = 280 events with >=1 ensemble model; 97 events skipped (no qualifying ensemble run / no NBM).

## Lead 1 — vs market

| variant | n | st | st beating mkt | dBrier_vs_mkt | 95% CI | dRPS_vs_mkt | 95% CI | dCRPS | dCenterMAE |
|---|---:|---:|---:|---:|---|---:|---|---:|---:|
| nbm_only | 280 | 19 | 4 | +0.0231 | [+0.0141, +0.0321] | +0.0335 | [+0.0228, +0.0442] | +0.118 | +0.12 |
| WEATHERNEXT2_center | 277 | 19 | 0 | +0.1253 | [+0.1108, +0.1398] | +0.2045 | [+0.1819, +0.2270] | +1.263 | +1.28 |
| WEATHERNEXT2_center_bc | 182 | 19 | 6 | +0.0127 | [+0.0022, +0.0233] | +0.0149 | [+0.0023, +0.0276] | +0.039 | -0.01 |
| WEATHERNEXT2_dist | 277 | 19 | 0 | +0.1438 | [+0.1289, +0.1586] | +0.2297 | [+0.2065, +0.2530] | +1.455 | +1.39 |
| ECMWF_AIFS_ENS_center | 280 | 19 | 0 | +0.0672 | [+0.0549, +0.0796] | +0.1177 | [+0.0982, +0.1371] | +0.675 | +0.72 |
| ECMWF_AIFS_ENS_center_bc | 185 | 19 | 3 | +0.0223 | [+0.0116, +0.0329] | +0.0347 | [+0.0205, +0.0489] | +0.173 | +0.20 |
| ECMWF_AIFS_ENS_dist | 280 | 19 | 1 | +0.0741 | [+0.0603, +0.0878] | +0.1285 | [+0.1075, +0.1495] | +0.745 | +0.78 |
| ECMWF_IFS_ENS_center | 280 | 19 | 0 | +0.0718 | [+0.0591, +0.0845] | +0.1337 | [+0.1124, +0.1551] | +0.877 | +0.96 |
| ECMWF_IFS_ENS_center_bc | 185 | 19 | 4 | +0.0259 | [+0.0151, +0.0368] | +0.0499 | [+0.0333, +0.0666] | +0.388 | +0.43 |
| ECMWF_IFS_ENS_dist | 280 | 19 | 1 | +0.0915 | [+0.0755, +0.1074] | +0.1613 | [+0.1372, +0.1854] | +1.073 | +1.11 |
| GFS_ENS_center | 280 | 19 | 0 | +0.0707 | [+0.0572, +0.0842] | +0.1027 | [+0.0842, +0.1212] | +0.603 | +0.63 |
| GFS_ENS_center_bc | 185 | 19 | 1 | +0.0318 | [+0.0199, +0.0437] | +0.0487 | [+0.0322, +0.0652] | +0.279 | +0.30 |
| GFS_ENS_dist | 280 | 19 | 2 | +0.0798 | [+0.0645, +0.0952] | +0.1129 | [+0.0925, +0.1333] | +0.678 | +0.71 |

### Lead 1 — paired vs nbm_only (negative = better than NBM baseline)

| variant − nbm_only | n | ΔBrier | 95% CI | ΔRPS | 95% CI |
|---|---:|---:|---|---:|---|
| WEATHERNEXT2_center − nbm_only | 277 | +0.1028 | [+0.0909, +0.1147] | +0.1719 | [+0.1529, +0.1908] |
| WEATHERNEXT2_center_bc − nbm_only | 182 | -0.0086 | [-0.0180, +0.0008] | -0.0152 | [-0.0287, -0.0017] |
| WEATHERNEXT2_dist − nbm_only | 277 | +0.1212 | [+0.1085, +0.1339] | +0.1971 | [+0.1771, +0.2172] |
| ECMWF_AIFS_ENS_center − nbm_only | 280 | +0.0442 | [+0.0346, +0.0537] | +0.0842 | [+0.0677, +0.1007] |
| ECMWF_AIFS_ENS_center_bc − nbm_only | 185 | +0.0000 | [-0.0096, +0.0097] | +0.0034 | [-0.0117, +0.0185] |
| ECMWF_AIFS_ENS_dist − nbm_only | 280 | +0.0510 | [+0.0397, +0.0623] | +0.0951 | [+0.0767, +0.1134] |
| ECMWF_IFS_ENS_center − nbm_only | 280 | +0.0488 | [+0.0386, +0.0590] | +0.1002 | [+0.0806, +0.1198] |
| ECMWF_IFS_ENS_center_bc − nbm_only | 185 | +0.0037 | [-0.0068, +0.0142] | +0.0187 | [-0.0005, +0.0378] |
| ECMWF_IFS_ENS_dist − nbm_only | 280 | +0.0684 | [+0.0547, +0.0821] | +0.1278 | [+0.1056, +0.1500] |
| GFS_ENS_center − nbm_only | 280 | +0.0477 | [+0.0363, +0.0591] | +0.0692 | [+0.0524, +0.0860] |
| GFS_ENS_center_bc − nbm_only | 185 | +0.0095 | [-0.0011, +0.0202] | +0.0174 | [+0.0008, +0.0341] |
| GFS_ENS_dist − nbm_only | 280 | +0.0568 | [+0.0435, +0.0701] | +0.0794 | [+0.0611, +0.0978] |

## Lead 0 — vs market

| variant | n | st | st beating mkt | dBrier_vs_mkt | 95% CI | dRPS_vs_mkt | 95% CI | dCRPS | dCenterMAE |
|---|---:|---:|---:|---:|---|---:|---|---:|---:|
| nbm_only | 298 | 19 | 1 | +0.0942 | [+0.0752, +0.1133] | +0.0946 | [+0.0754, +0.1138] | +0.430 | +0.66 |
| WEATHERNEXT2_center | 294 | 19 | 1 | +0.0895 | [+0.0714, +0.1077] | +0.0855 | [+0.0680, +0.1030] | +0.348 | +0.56 |
| WEATHERNEXT2_center_bc | 199 | 19 | 1 | +0.0947 | [+0.0710, +0.1184] | +0.0980 | [+0.0730, +0.1230] | +0.454 | +0.69 |
| WEATHERNEXT2_dist | 294 | 19 | 0 | +0.1698 | [+0.1461, +0.1935] | +0.1545 | [+0.1341, +0.1749] | +0.432 | +0.54 |
| ECMWF_AIFS_ENS_center | 298 | 19 | 1 | +0.0824 | [+0.0644, +0.1004] | +0.0800 | [+0.0620, +0.0979] | +0.332 | +0.53 |
| ECMWF_AIFS_ENS_center_bc | 203 | 19 | 1 | +0.0985 | [+0.0741, +0.1230] | +0.1051 | [+0.0781, +0.1321] | +0.475 | +0.69 |
| ECMWF_AIFS_ENS_dist | 298 | 19 | 0 | +0.1361 | [+0.1145, +0.1577] | +0.1238 | [+0.1047, +0.1428] | +0.283 | +0.36 |
| ECMWF_IFS_ENS_center | 298 | 19 | 1 | +0.1126 | [+0.0906, +0.1346] | +0.1197 | [+0.0956, +0.1438] | +0.543 | +0.77 |
| ECMWF_IFS_ENS_center_bc | 203 | 19 | 2 | +0.1152 | [+0.0871, +0.1433] | +0.1306 | [+0.0980, +0.1633] | +0.600 | +0.82 |
| ECMWF_IFS_ENS_dist | 298 | 19 | 0 | +0.1668 | [+0.1423, +0.1913] | +0.1590 | [+0.1356, +0.1824] | +0.509 | +0.63 |
| GFS_ENS_center | 298 | 19 | 0 | +0.1160 | [+0.0959, +0.1360] | +0.1169 | [+0.0960, +0.1378] | +0.515 | +0.75 |
| GFS_ENS_center_bc | 203 | 19 | 2 | +0.1000 | [+0.0738, +0.1263] | +0.1117 | [+0.0824, +0.1409] | +0.495 | +0.70 |
| GFS_ENS_dist | 298 | 19 | 0 | +0.1595 | [+0.1370, +0.1820] | +0.1489 | [+0.1277, +0.1701] | +0.475 | +0.62 |

### Lead 0 — paired vs nbm_only (negative = better than NBM baseline)

| variant − nbm_only | n | ΔBrier | 95% CI | ΔRPS | 95% CI |
|---|---:|---:|---|---:|---|
| WEATHERNEXT2_center − nbm_only | 294 | -0.0030 | [-0.0181, +0.0120] | -0.0075 | [-0.0220, +0.0070] |
| WEATHERNEXT2_center_bc − nbm_only | 199 | +0.0092 | [-0.0051, +0.0234] | +0.0074 | [-0.0080, +0.0228] |
| WEATHERNEXT2_dist − nbm_only | 294 | +0.0772 | [+0.0479, +0.1066] | +0.0615 | [+0.0327, +0.0902] |
| ECMWF_AIFS_ENS_center − nbm_only | 298 | -0.0118 | [-0.0248, +0.0012] | -0.0146 | [-0.0275, -0.0018] |
| ECMWF_AIFS_ENS_center_bc − nbm_only | 203 | +0.0107 | [-0.0054, +0.0267] | +0.0128 | [-0.0051, +0.0307] |
| ECMWF_AIFS_ENS_dist − nbm_only | 298 | +0.0419 | [+0.0159, +0.0679] | +0.0291 | [+0.0026, +0.0557] |
| ECMWF_IFS_ENS_center − nbm_only | 298 | +0.0184 | [+0.0025, +0.0343] | +0.0251 | [+0.0071, +0.0431] |
| ECMWF_IFS_ENS_center_bc − nbm_only | 203 | +0.0273 | [+0.0085, +0.0462] | +0.0384 | [+0.0156, +0.0612] |
| ECMWF_IFS_ENS_dist − nbm_only | 298 | +0.0726 | [+0.0452, +0.0999] | +0.0644 | [+0.0364, +0.0924] |
| GFS_ENS_center − nbm_only | 298 | +0.0218 | [+0.0085, +0.0351] | +0.0223 | [+0.0094, +0.0351] |
| GFS_ENS_center_bc − nbm_only | 203 | +0.0122 | [-0.0044, +0.0287] | +0.0194 | [+0.0020, +0.0369] |
| GFS_ENS_dist − nbm_only | 298 | +0.0653 | [+0.0400, +0.0906] | +0.0543 | [+0.0301, +0.0785] |

Limitations per prereg §8: NBM bias-corrected vs ensembles raw (`_center_bc` is the
fair read, esp. WEATHERNEXT2 whose 6-hourly sampling biases raw daily-max cold);
one summer month; `_dist` spread uncalibrated by design. No production change.
