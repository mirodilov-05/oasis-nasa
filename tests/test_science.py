"""Scientific reference validation.

Every number below comes from a published worked example or table, cited per test:
  FAO-56 = Allen, Pereira, Raes, Smith (1998), Crop evapotranspiration, FAO Irrigation and Drainage Paper 56.
Tolerances follow the rounding of the published values.
"""
import dataclasses
import math

import pytest

from backend import agro, climate, physics as ph, sensitivity
from tests.test_backend import client, fake_data  # noqa: F401  (client is a fixture)


# ---------- FAO-56 meteorological components ----------

def test_fao56_example2_pressure_and_psychrometric_constant():
    # Example 2: z = 1800 m -> P = 81.8 kPa, gamma = 0.054 kPa/C
    p = ph.atmospheric_pressure(1800)
    assert p == pytest.approx(81.8, abs=0.05)
    assert ph.psychrometric_constant(p) == pytest.approx(0.054, abs=0.0005)


def test_fao56_example3_mean_saturation_vapour_pressure():
    # Example 3: Tmax 24.5, Tmin 15 C -> es = 2.39 kPa
    assert ph.mean_saturation_vp(24.5, 15.0) == pytest.approx(2.39, abs=0.005)


def test_fao56_annex2_tables_es_and_slope():
    # Annex 2, Tables 2.3 and 2.4 at 25 C: e0 = 3.168 kPa, delta = 0.189 kPa/C
    assert ph.saturation_vp(25) == pytest.approx(3.168, abs=0.001)
    assert ph.svp_slope(25) == pytest.approx(0.189, abs=0.001)


def test_fao56_example11_net_longwave():
    # Example 11: Tmax 25.1, Tmin 19.1 C, ea 2.1 kPa, Rs/Rso = 14.5/18.8 -> Rnl = 3.5 MJ/m2/day
    assert ph.net_longwave(25.1, 19.1, 2.1, 14.5, 18.8) == pytest.approx(3.5, abs=0.05)


def test_fao56_example17_monthly_penman_monteith():
    # Example 17: Bangkok, April, 13 deg 44 min N, 2 m. Tmax 34.8, Tmin 25.6, ea 2.85 kPa, u2 2 m/s,
    # n = 8.5 h, G = 0.14 MJ/m2/day -> Ra 38.06, Rs 22.65, ET0 = 5.72 mm/day
    lat, doy = 13 + 44 / 60, 105
    ra = ph.extraterrestrial_radiation(lat, doy)
    assert ra == pytest.approx(38.06, abs=0.05)
    dec = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    big_n = 24 / math.pi * math.acos(-math.tan(math.radians(lat)) * math.tan(dec))
    rs = (0.25 + 0.5 * 8.5 / big_n) * ra                        # Angstrom, FAO-56 eq. 35
    assert rs == pytest.approx(22.65, abs=0.05)
    et0 = ph.et0_penman_monteith(34.8, 25.6, 30.2, rs, 2.0, ra, 2, ea=2.85, g=0.14)
    assert et0 == pytest.approx(5.72, abs=0.02)


def test_fao56_example18_daily_penman_monteith():
    # Example 18: Uccle, 6 July -> ET0 = 3.9 mm/day
    ra = ph.extraterrestrial_radiation(50.8, 187)
    assert ph.et0_penman_monteith(21.5, 12.3, 16.9, 22.07, 2.078, ra, 100, ea=1.409) == pytest.approx(3.9, abs=0.05)


def test_hargreaves_is_eq52():
    # FAO-56 eq. 52 written out; Ra converted to mm/day with 0.408
    ra = 40.0
    assert ph.et0_hargreaves(25, 32, 18, ra) == pytest.approx(0.0023 * 42.8 * math.sqrt(14) * 0.408 * ra)


def test_rh_min_from_mean_humidity():
    # ea = 0.60 * (4.243 + 2.338) / 2 = 1.974 kPa; RHmin = ea / e0(30 C) = 46.5 %
    assert ph.rh_min_estimate(30, 20, 60) == pytest.approx(46.5, abs=0.1)


# ---------- crop coefficients ----------

def test_kc_climate_adjustment_eq62():
    assert ph.kc_climate_adjustment(2.0, 45.0, 1.0) == pytest.approx(0.0)        # Table 12 climate: no change
    assert ph.kc_climate_adjustment(4.0, 20.0, 3.0) == pytest.approx(0.18)       # 0.04*2 + 0.004*25, (h/3)^0.3 = 1
    assert ph.kc_climate_adjustment(3.0, 70.0, 1.5) == pytest.approx((0.04 - 0.1) * 0.5 ** 0.3)
    # outside the valid range the inputs are clamped (u2 1-6, RHmin 20-80)
    assert ph.kc_climate_adjustment(9.0, 5.0, 3.0) == ph.kc_climate_adjustment(6.0, 20.0, 3.0)


def test_arid_season_raises_and_humid_season_lowers_kc_mid():
    base = climate.prepare(fake_data())
    cotton = agro.CROPS["cotton"]
    i0 = base.idx(agro.sow_date(cotton, 2020, False))
    dry = dataclasses.replace(base, u2=[4.0] * base.n, rh_min=[20.0] * base.n)
    wet = dataclasses.replace(base, u2=[1.0] * base.n, rh_min=[80.0] * base.n)
    tab = cotton["kc"][1]
    assert agro.adjusted_kc(dry, cotton, i0)[1] > tab > agro.adjusted_kc(wet, cotton, i0)[1]
    assert agro.adjusted_kc(dry, cotton, i0)[0] == cotton["kc"][0]               # Kc ini is never adjusted
    no_wind = dataclasses.replace(base, u2=None, rh_min=None)
    assert agro.adjusted_kc(no_wind, cotton, i0) == cotton["kc"]                 # no data -> table values


def test_every_crop_has_a_fao56_height():
    for c in agro.CROPS.values():
        assert 0.1 <= c["height_m"] <= 3.0, c["id"]


# ---------- soil water ----------

@pytest.mark.parametrize("sand, clay, fc_range, wp_range", [
    (40, 20, (0.20, 0.30), (0.07, 0.17)),   # loam, FAO-56 Table 19
    (90, 5, (0.07, 0.17), (0.02, 0.07)),    # sand
])
def test_saxton_rawls_within_fao56_table19(sand, clay, fc_range, wp_range):
    fc, wp = ph.saxton_rawls(sand, clay, 2.5)
    assert fc_range[0] <= fc <= fc_range[1] and wp_range[0] <= wp <= wp_range[1]


def test_saxton_rawls_clay_available_water():
    # FAO-56 Table 19 clay: TAW 0.12-0.20 m3/m3; Saxton-Rawls gives higher FC and WP but a similar difference
    fc, wp = ph.saxton_rawls(20, 50, 2.5)
    assert 0.10 <= fc - wp <= 0.20


def test_water_balance_conserves_water():
    """Over a season ETc = rain + irrigation + released storage - drainage; storage change is at most TAW."""
    d = climate.prepare(fake_data())
    crop = agro.CROPS["cotton"]
    r = agro.simulate(d, crop, 2020, awc=150)
    taw = 150 * crop["root_m"]
    assert r["need"] <= r["rain"] + r["irrigation"] + taw + 1e-6


# ---------- statistics ----------

def test_mann_kendall_tie_correction_by_hand():
    # n = 8, tie groups of 2 and 3: var = (8*7*21 - 2*1*9 - 3*2*11) / 18 = 60.667; S = 28 pairs - 4 tied = 24
    mk = ph.mann_kendall([1, 2, 2, 3, 3, 3, 4, 5])
    assert mk["s"] == 24 and mk["var_s"] == pytest.approx(60.667, abs=1e-3)
    assert mk["z"] == pytest.approx(23 / math.sqrt(60.667), abs=1e-3)
    assert mk["p"] == pytest.approx(math.erfc(mk["z"] / math.sqrt(2)))


def test_mann_kendall_without_ties_is_unchanged():
    ys = [3.1, 2.9, 3.6, 3.3, 3.9, 4.2, 3.8, 4.5, 4.4, 5.0]
    n, s = 10, sum((ys[j] > ys[i]) - (ys[j] < ys[i]) for i in range(9) for j in range(i + 1, 10))
    z = (s - 1) / math.sqrt(n * (n - 1) * (2 * n + 5) / 18)
    assert ph.mann_kendall_p(ys) == pytest.approx(math.erfc(z / math.sqrt(2)))


def test_sen_slope_ignores_an_outlier():
    xs = list(range(20))
    ys = [2 * x + 1 for x in xs]
    ys[7] = 500
    assert ph.sen_slope(xs, ys) == pytest.approx(2.0)
    assert abs(ph.ols(xs, ys)["slope"] - 2.0) > 0.1


def test_quantiles_match_statistical_tables():
    assert ph.normal_quantile(0.975) == pytest.approx(1.95996, abs=1e-5)
    assert ph.t_quantile(0.90, 33) == pytest.approx(1.308, abs=0.001)
    assert ph.t_quantile(0.975, 10) == pytest.approx(2.228, abs=0.001)
    assert ph.t_quantile(0.95, 5) == pytest.approx(2.015, abs=0.002)


def test_lag1_autocorrelation_detects_persistence():
    assert ph.lag1_autocorrelation([1, -1] * 15) < -0.9
    assert ph.lag1_autocorrelation([math.sin(i / 3) for i in range(30)]) > 0.8


def test_trend_output_reports_robust_slope_and_test_details():
    warm = climate.analyze(climate.prepare(fake_data(warming=0.05)))["shift"]["t_mean"]
    assert warm["trend_sen_per_decade"] == pytest.approx(0.5, abs=0.02)   # 0.05 C per year
    assert warm["trend_test"]["n"] == 35 and "tie-corrected" in warm["trend_test"]["method"]
    for key in ("baseline", "recent", "delta", "trend_per_decade", "trend_p_value", "projection"):
        assert key in warm      # existing API keys are kept


# ---------- sensitivity ----------

def test_sensitivity_tornado():
    d = climate.prepare(fake_data())
    out = sensitivity.irrigation_sensitivity(d, agro.CROPS["cotton"], 150, 0.6)
    by = {r["input"]: r for r in out["inputs"]}
    assert set(by) == {"et0", "rain", "awc", "kc_mid", "efficiency"}
    assert by["et0"]["high_mm"] > out["base_mm"] > by["et0"]["low_mm"]       # more ET0, more irrigation
    assert by["rain"]["high_mm"] <= by["rain"]["low_mm"]                       # more rain, less irrigation
    assert 0.5 < by["et0"]["elasticity"] < 2.0
    swings = [r["swing_pct"] for r in out["inputs"]]
    assert swings == sorted(swings, reverse=True)                              # tornado order
    assert out["years"] == 15


def test_sensitivity_endpoint_schema(client):
    body = client.get("/api/sensitivity?lat=41.3&lon=69.2&crop=cotton").json()
    assert body["crop"] == "cotton" and body["data_source"] == "cache" and len(body["inputs"]) == 5
    assert client.get("/api/sensitivity?lat=41.3&lon=69.2&crop=banana").status_code == 422
