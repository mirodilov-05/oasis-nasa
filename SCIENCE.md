# TerraShift scientific engine — validation and change log

This file records how each calculation is checked and every change to a formula.
Tests: `tests/test_science.py` (reference values), `tests/test_regression.py` (locks all outputs).

## Validation against published references

| Quantity | Code | Reference | Expected | Engine |
|---|---|---|---|---|
| Atmospheric pressure, 1800 m | `physics.atmospheric_pressure` | FAO-56 Example 2 | 81.8 kPa | 81.76 |
| Psychrometric constant | `physics.psychrometric_constant` | FAO-56 Example 2 | 0.054 kPa/°C | 0.0544 |
| Mean saturation vapour pressure | `physics.mean_saturation_vp` | FAO-56 Example 3 | 2.39 kPa | 2.390 |
| e°(25 °C), Δ(25 °C) | `saturation_vp`, `svp_slope` | FAO-56 Annex 2, Tables 2.3–2.4 | 3.168 kPa, 0.189 kPa/°C | 3.168, 0.189 |
| Extraterrestrial radiation | `extraterrestrial_radiation` | FAO-56 Example 8 | 32.2 MJ/m²/d | 32.2 |
| Net longwave radiation | `net_longwave` | FAO-56 Example 11 | 3.5 MJ/m²/d | 3.53 |
| ET0, monthly (Bangkok, April) | `et0_penman_monteith` | FAO-56 Example 17 | 5.72 mm/d | 5.72 |
| ET0, daily (Uccle, 6 July) | `et0_penman_monteith` | FAO-56 Example 18 | 3.9 mm/d | 3.88 |
| Hargreaves ET0 | `et0_hargreaves` | FAO-56 eq. 52 | formula | exact |
| Kc climatic adjustment | `kc_climate_adjustment` | FAO-56 eq. 62/65 | hand-computed cases | exact |
| FC / WP of loam and sand | `saxton_rawls` | FAO-56 Table 19 ranges | within range | within range |
| Mann-Kendall with ties | `mann_kendall` | Kendall (1975) variance, hand-computed | S = 24, var = 60.667 | same |
| Student t quantiles | `t_quantile` | statistical tables | 1.308 (0.90, 33), 2.228 (0.975, 10) | same |
| Normal quantile | `normal_quantile` | 1.95996 at 0.975 | | same |
| Water balance closure | `agro.simulate` | mass balance | ETc ≤ rain + irrigation + TAW | holds |

## Change log

### 2026-11-15 — scientific engine audit

Audit result: the FAO-56 Penman-Monteith implementation, Ra, Hargreaves, Saxton-Rawls and the
single-Kc water balance were found correct against the references above; they are unchanged
in their numbers. Penman-Monteith was split into its documented components (eq. 7, 8, 12, 13,
37–40) so each can be tested; the regression lock confirmed identical outputs after the split.

Intended changes (the regression baseline was re-recorded after each, on purpose):

1. **Climatic adjustment of Kc mid and Kc end (FAO-56 eq. 62 and 65).** The crop table values
   assume RHmin 45 % and u2 2 m/s. Each simulated season now corrects Kc mid with the mean wind
   and minimum humidity of its mid-season stage, and Kc end (when ≥ 0.45) with the late stage.
   RHmin is estimated from NASA POWER RH2M and the day's Tmax (ea from RHmean, eq. 19).
   Crop heights from FAO-56 Table 12 were added to `crops.json` (`height_m`).
   Effect on the recent-period crop water need (NASA POWER cache):

   | Place | Mean u2 / RHmin | Cotton | Maize | Winter wheat | Alfalfa |
   |---|---|---|---|---|---|
   | Tashkent (arid) | 1.8 m/s / 37 % | 1011 → 1086 mm (+7.4 %) | +7.6 % | −0.7 % | +5.6 % |
   | Iowa (humid continental) | 3.3 m/s / 56 % | −0.5 % | −1.1 % | +2.8 % | +0.6 % |
   | Moscow (humid) | 3.1 m/s / 69 % | −3.7 % | −3.8 % | −1.1 % | −3.0 % |

   The best rotation did not change at these places. Outputs now carry `kc_mid` / `kc_end` per season.

2. **Mann-Kendall tie correction.** The variance of S now subtracts Σ t(t−1)(2t+5) for tied
   groups (Kendall 1975). Without ties the result is identical. Annual counts (hot days, frost
   days, dry-spell length) often tie; their p-values were slightly too small before.

3. **Prediction interval t value.** `predict()` takes the t quantile from the series length
   (n − 2 degrees of freedom) instead of the fixed 1.31. For 35 years: 1.308 (−0.2 % width).

Additions (new keys only; existing API keys unchanged):

- `trend_sen_per_decade` — Theil-Sen slope (Sen 1968), robust to single extreme years.
- `trend_test` — S, Z, ties, n, lag-1 autocorrelation of the detrended series and
  `serially_correlated` (|r1| > 2/√n: the Mann-Kendall p-value is then optimistic).
- `GET /api/sensitivity` — one-at-a-time irrigation sensitivity ("tornado") for one crop:
  ET0 ±10 %, rain ±20 %, AWC ±20 %, Kc mid ±0.10, irrigation efficiency ±0.10.
  Typed response schema (`SensitivityResult` in `backend/main.py`, visible at `/docs`).
  Example, cotton at Tashkent, furrow: efficiency ±34 %, ET0 ±22 % (elasticity 1.12),
  Kc mid ±18 %, rain ±2.5 %, AWC ±1.1 % of the seasonal irrigation need.

## Known scientific limitations

- **Serial correlation** is reported, not removed (no pre-whitening / Hamed-Rao correction).
- **Projection** to 2040 is the linear trend continued with its prediction interval, not a
  climate-model projection.
- **Single-Kc balance** (`agro.simulate`): constant root depth over the season, no runoff,
  no Ks reduction of ETc in the last 10 days before harvest (when irrigation stops).
  The 16-day forecast balance (`water.py`) uses the dual Kc method with growing roots but does
  **not** yet apply the eq. 62 climatic adjustment — the two modules can differ by the
  amounts in the table above.
- **Heat and frost stress** are threshold counts with rule-of-thumb limits from `crops.json`
  (marked draft), not crop-model yield responses.
- **Crop suitability** combines land cover, terrain and climate limits; it is a screening
  rule, not an ecological niche model.
- **Sensitivity** is one-at-a-time with independent, assumed error ranges; it ranks inputs but
  is not a probabilistic uncertainty estimate. The weather-year spread (p10/p50/p90 over
  2011–2025) remains the main uncertainty shown to the farmer.
- **ET0 from reanalysis** (NASA POWER, ~50 km) over irrigated land is usually higher than the
  local value (FAO-56 Annex 6, aridity effect); absolute water numbers are best used to compare.

## References

- Allen, R.G., Pereira, L.S., Raes, D., Smith, M. (1998). Crop evapotranspiration. FAO Irrigation and Drainage Paper 56.
- Saxton, K.E., Rawls, W.J. (2006). Soil water characteristic estimates by texture and organic matter. SSSAJ 70:1569–1578.
- Kendall, M.G. (1975). Rank Correlation Methods. Griffin, London.
- Sen, P.K. (1968). Estimates of the regression coefficient based on Kendall's tau. JASA 63:1379–1389.
- Abramowitz, M., Stegun, I.A. (1964). Handbook of Mathematical Functions, 26.7.5.
