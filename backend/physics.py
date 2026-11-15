"""Physics and statistics helpers. Pure Python.

Evapotranspiration follows FAO Irrigation and Drainage Paper 56 (Allen et al., 1998):
  * Penman-Monteith reference ET0 (eq. 6) with net radiation from eq. 37-40
  * Hargreaves ET0 (eq. 52) when radiation, humidity or wind are missing
  * extraterrestrial radiation Ra (eq. 21)
"""
from __future__ import annotations

import math


# ---------- statistics ----------

def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def percentile(values, q: float):
    """Linear-interpolated percentile, q in 0..100."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    pos = (len(values) - 1) * q / 100
    lo = math.floor(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def rnd(value, digits: int = 2):
    if value is None:
        return None
    return round(value, digits) if digits else int(round(value))


def spread(values, digits: int = 0) -> dict | None:
    """Year-to-year spread: 10th, 50th and 90th percentile."""
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {f"p{q}": rnd(percentile(values, q), digits) for q in (10, 50, 90)}


def ols(xs, ys) -> dict | None:
    """Least-squares line y = a + b*x with what is needed for a prediction interval."""
    pairs = [(x, y) for x, y in zip(xs, ys) if y is not None]
    n = len(pairs)
    if n < 5:
        return None
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    if not sxx:
        return None
    slope = sum((x - mx) * (y - my) for x, y in pairs) / sxx
    intercept = my - slope * mx
    sse = sum((y - intercept - slope * x) ** 2 for x, y in pairs)
    return {"slope": slope, "intercept": intercept, "n": n, "mx": mx, "sxx": sxx,
            "sd": math.sqrt(sse / (n - 2))}


def normal_quantile(prob: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation, relative error < 1.2e-9)."""
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    if not 0 < prob < 1:
        raise ValueError("prob must be in (0, 1)")
    if prob < 0.02425:
        q = math.sqrt(-2 * math.log(prob))
        return ((((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
                / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))
    if prob > 1 - 0.02425:
        return -normal_quantile(1 - prob)
    q = prob - 0.5
    r = q * q
    return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1))


def t_quantile(prob: float, df: float) -> float:
    """Student's t quantile by the Cornish-Fisher expansion (Abramowitz & Stegun 26.7.5).

    Error below 0.001 for df >= 5, which covers every series here.
    """
    z = normal_quantile(prob)
    v = max(df, 1.0)
    return (z + (z ** 3 + z) / (4 * v) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * v ** 2)
            + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * v ** 3))


def predict(fit: dict, x: float, t_value: float | None = None, level: float = 0.80) -> tuple[float, float, float]:
    """Trend value at x with a two-sided prediction interval (default 80 %).

    Since 2026-11 the t value follows the series length (n - 2 degrees of freedom) instead of a
    fixed 1.31; for the usual 35-year record it is 1.308, so intervals change by < 0.2 %.
    """
    if t_value is None:
        t_value = t_quantile(0.5 + level / 2, fit["n"] - 2)
    y = fit["intercept"] + fit["slope"] * x
    half = t_value * fit["sd"] * math.sqrt(1 + 1 / fit["n"] + (x - fit["mx"]) ** 2 / fit["sxx"])
    return y, y - half, y + half


def mann_kendall(ys) -> dict | None:
    """Mann-Kendall trend test with the tie-corrected variance (Kendall 1975) and continuity correction.

    -> {"s", "var_s", "z", "p" (two-sided), "n", "ties"}; None below 8 values.
    Identical to the earlier test when no two values are equal. Serial correlation is not
    removed; climate.shift reports it via lag1_autocorrelation().
    """
    ys = [y for y in ys if y is not None]
    n = len(ys)
    if n < 8:
        return None
    s = sum((ys[j] > ys[i]) - (ys[j] < ys[i]) for i in range(n - 1) for j in range(i + 1, n))
    counts: dict = {}
    for y in ys:
        counts[y] = counts.get(y, 0) + 1
    tie_term = sum(t * (t - 1) * (2 * t + 5) for t in counts.values() if t > 1)
    var = (n * (n - 1) * (2 * n + 5) - tie_term) / 18
    z = 0.0 if s == 0 or var <= 0 else (s - 1 if s > 0 else s + 1) / math.sqrt(var)
    return {"s": s, "var_s": var, "z": z, "p": math.erfc(abs(z) / math.sqrt(2)), "n": n,
            "ties": sum(t for t in counts.values() if t > 1)}


def mann_kendall_p(ys):
    """Two-sided p-value of a monotonic trend (see mann_kendall)."""
    mk = mann_kendall(ys)
    return None if mk is None else mk["p"]


def sen_slope(xs, ys) -> float | None:
    """Theil-Sen slope: median of all pairwise slopes, robust to outliers (Sen 1968)."""
    pairs = [(x, y) for x, y in zip(xs, ys) if y is not None]
    slopes = [(y2 - y1) / (x2 - x1) for k, (x1, y1) in enumerate(pairs) for x2, y2 in pairs[k + 1:] if x2 != x1]
    return percentile(slopes, 50) if slopes else None


def lag1_autocorrelation(ys) -> float | None:
    """Lag-1 autocorrelation of the detrended series (residuals of the OLS line)."""
    ys = [y for y in ys if y is not None]
    fit = ols(list(range(len(ys))), ys)
    if not fit:
        return None
    r = [y - fit["intercept"] - fit["slope"] * i for i, y in enumerate(ys)]
    den = sum(v * v for v in r)
    return sum(r[i] * r[i + 1] for i in range(len(r) - 1)) / den if den else None


# ---------- evapotranspiration ----------

def saturation_vp(t: float) -> float:
    """Saturation vapour pressure, kPa (FAO-56 eq. 11)."""
    return 0.6108 * math.exp(17.27 * t / (t + 237.3))


def extraterrestrial_radiation(lat_deg: float, day_of_year: int) -> float:
    """Ra in MJ/m2/day (FAO-56 eq. 21)."""
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * day_of_year / 365)
    delta = 0.409 * math.sin(2 * math.pi * day_of_year / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    ra = (24 * 60 / math.pi) * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws)
    )
    return max(ra, 0.0)


def et0_hargreaves(t_mean: float, t_max: float, t_min: float, ra: float) -> float:
    """Reference ET in mm/day (FAO-56 eq. 52)."""
    return max(0.0, 0.0023 * (t_mean + 17.8) * math.sqrt(max(t_max - t_min, 0.0)) * 0.408 * ra)


def et0_penman_monteith(t_max: float, t_min: float, t_mean: float, rs: float, u2: float,
                        ra: float, elevation: float, rh: float | None = None,
                        ea: float | None = None, g: float = 0.0) -> float:
    """FAO-56 Penman-Monteith reference ET in mm/day (eq. 6).

    rs: solar radiation MJ/m2/day, u2: wind at 2 m in m/s, rh: mean relative humidity in %,
    ea: actual vapour pressure kPa (used instead of rh when given).
    """
    es = mean_saturation_vp(t_max, t_min)
    if ea is None:
        ea = es * max(0.0, min(rh, 100.0)) / 100
    delta = svp_slope(t_mean)
    gamma = psychrometric_constant(atmospheric_pressure(elevation))
    rn = net_radiation(rs, ra, t_max, t_min, ea, elevation)
    et0 = (0.408 * delta * (rn - g) + gamma * 900 / (t_mean + 273) * u2 * (es - ea)) / (
        delta + gamma * (1 + 0.34 * u2))
    return max(et0, 0.0)


def atmospheric_pressure(elevation_m: float) -> float:
    """kPa (FAO-56 eq. 7)."""
    return 101.3 * ((293 - 0.0065 * elevation_m) / 293) ** 5.26


def psychrometric_constant(pressure_kpa: float) -> float:
    """kPa/degC (FAO-56 eq. 8)."""
    return 0.000665 * pressure_kpa


def svp_slope(t: float) -> float:
    """Slope of the saturation vapour pressure curve, kPa/degC (FAO-56 eq. 13)."""
    return 4098 * saturation_vp(t) / (t + 237.3) ** 2


def mean_saturation_vp(t_max: float, t_min: float) -> float:
    """es, kPa (FAO-56 eq. 12): mean of e0(Tmax) and e0(Tmin), not e0(Tmean)."""
    return (saturation_vp(t_max) + saturation_vp(t_min)) / 2


def clear_sky_radiation(ra: float, elevation_m: float) -> float:
    """Rso, MJ/m2/day (FAO-56 eq. 37)."""
    return (0.75 + 2e-5 * elevation_m) * ra


def net_longwave(t_max: float, t_min: float, ea: float, rs: float, rso: float) -> float:
    """Rnl, MJ/m2/day (FAO-56 eq. 39). Rs/Rso is limited to 1; 0.5 is used when Rso is 0 (polar night)."""
    rel = min(rs / rso, 1.0) if rso > 0 else 0.5
    return (4.903e-9 * ((t_max + 273.16) ** 4 + (t_min + 273.16) ** 4) / 2
            * (0.34 - 0.14 * math.sqrt(max(ea, 0.0))) * (1.35 * rel - 0.35))


def net_radiation(rs: float, ra: float, t_max: float, t_min: float, ea: float, elevation_m: float) -> float:
    """Rn = Rns - Rnl, MJ/m2/day (FAO-56 eq. 38 with albedo 0.23, eq. 40)."""
    return 0.77 * rs - net_longwave(t_max, t_min, ea, rs, clear_sky_radiation(ra, elevation_m))


def rh_min_estimate(t_max: float, t_min: float, rh_mean: float) -> float:
    """Daily minimum relative humidity, %, when only the mean is known: ea from RHmean
    (FAO-56 eq. 19), then RHmin = 100 ea / e0(Tmax)."""
    ea = mean_saturation_vp(t_max, t_min) * max(0.0, min(rh_mean, 100.0)) / 100
    return min(100.0, 100 * ea / saturation_vp(t_max))


def kc_climate_adjustment(u2: float, rh_min: float, height_m: float) -> float:
    """Change of the tabulated Kc mid / Kc end for the local climate (FAO-56 eq. 62 and 65).

    FAO-56 Table 12 assumes a sub-humid climate (RHmin 45 %, u2 2 m/s). Inputs are limited to the
    range the equation is valid for: u2 1-6 m/s, RHmin 20-80 %, crop height 0.1-10 m.
    """
    u2 = max(1.0, min(u2, 6.0))
    rh_min = max(20.0, min(rh_min, 80.0))
    h = max(0.1, min(height_m, 10.0))
    return (0.04 * (u2 - 2) - 0.004 * (rh_min - 45)) * (h / 3) ** 0.3


# ---------- soil water ----------

def saxton_rawls(sand_pct: float, clay_pct: float, organic_matter_pct: float = 1.5) -> tuple[float, float]:
    """Volumetric water content at field capacity (33 kPa) and wilting point (1500 kPa), m3/m3.

    Saxton & Rawls (2006), Soil Sci. Soc. Am. J. 70:1569-1578, equations 1 and 2.
    Valid for clay below 60 % and organic matter below 8 %; inputs are clamped to that range.
    """
    s = max(0.0, min(sand_pct, 100.0)) / 100
    c = max(0.0, min(clay_pct, 60.0)) / 100
    om = max(0.0, min(organic_matter_pct, 8.0))
    t1500 = -0.024 * s + 0.487 * c + 0.006 * om + 0.005 * s * om - 0.013 * c * om + 0.068 * s * c + 0.031
    wp = t1500 + (0.14 * t1500 - 0.02)
    t33 = -0.251 * s + 0.195 * c + 0.011 * om + 0.006 * s * om - 0.027 * c * om + 0.452 * s * c + 0.299
    fc = t33 + (1.283 * t33 * t33 - 0.374 * t33 - 0.015)
    return fc, wp
