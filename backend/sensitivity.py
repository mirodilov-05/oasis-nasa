"""One-at-a-time sensitivity of a crop's irrigation need to its uncertain inputs.

Each input is moved to a low and a high value while the others stay at their base value,
and the crop is replayed over the weather of the recent period (2011-2025). The result is
a "tornado": which input moves the answer most. Ranges are deliberately simple and stated
in the output, so they can be challenged:

  ET0       +-10 %   reanalysis radiation/humidity/wind errors (POWER validation studies)
  rain      +-20 %   gridded precipitation error at ~50 km
  AWC       +-20 %   SoilGrids / class estimate of available water capacity
  Kc mid    +-0.10   spread between FAO-56 table values and local measurements
  efficiency +-0.10  field application efficiency of the chosen method (gross water only)

This is not a probabilistic uncertainty analysis: inputs are varied one at a time and
their errors are assumed independent.
"""
from __future__ import annotations

import dataclasses

from .agro import simulate
from .climate import RECENT, Days
from .physics import mean, rnd

RANGES = {
    "et0": ("relative", 0.10),
    "rain": ("relative", 0.20),
    "awc": ("relative", 0.20),
    "kc_mid": ("absolute", 0.10),
}


def _mean_irrigation(d: Days, crop: dict, awc: float) -> tuple[float | None, int]:
    rows = [r for y in range(RECENT[0], RECENT[1] + 1) if (r := simulate(d, crop, y, awc))]
    return (mean(r["irrigation"] for r in rows) if rows else None), len(rows)


def _variant(d: Days, crop: dict, awc: float, name: str, sign: int):
    kind, size = RANGES[name]
    if name == "et0":
        return dataclasses.replace(d, et0=[v * (1 + sign * size) for v in d.et0]), crop, awc
    if name == "rain":
        return dataclasses.replace(d, rain=[v * (1 + sign * size) for v in d.rain]), crop, awc
    if name == "awc":
        return d, crop, awc * (1 + sign * size)
    ini, mid, end = crop["kc"]
    # the climatic adjustment is added on top of the moved table value, as for the base run
    return d, {**crop, "kc": [ini, max(0.1, mid + sign * size), end]}, awc


def irrigation_sensitivity(d: Days, crop: dict, awc_mm_per_m: float, efficiency: float) -> dict | None:
    """-> {"base_mm", "base_m3_ha", "years", "inputs": [...sorted by swing], "method", "limits"}"""
    base, years = _mean_irrigation(d, crop, awc_mm_per_m)
    if base is None:
        return None
    gross = lambda mm, eff: mm * 10 / eff  # noqa: E731  mm -> m3/ha delivered
    inputs = []
    for name, (kind, size) in RANGES.items():
        lo = _mean_irrigation(*_variant(d, crop, awc_mm_per_m, name, -1))[0]
        hi = _mean_irrigation(*_variant(d, crop, awc_mm_per_m, name, +1))[0]
        # the balance refills in steps, so the response can be non-monotonic: take the full span
        swing = max(lo, hi, base) - min(lo, hi, base)
        # elasticity: % change of irrigation per % change of the input (relative ranges only)
        elasticity = ((hi - lo) / base) / (2 * size) if kind == "relative" and base else None
        inputs.append({"input": name, "range": {"kind": kind, "size": size},
                       "low_mm": rnd(lo, 0), "high_mm": rnd(hi, 0), "swing_mm": rnd(swing, 0),
                       "swing_pct": rnd(100 * swing / base, 1) if base else None,
                       "elasticity": rnd(elasticity, 2)})
    lo_eff, hi_eff = max(0.3, efficiency - 0.10), min(0.95, efficiency + 0.10)
    eff_lo, eff_hi = gross(base, hi_eff), gross(base, lo_eff)
    inputs.append({"input": "efficiency", "range": {"kind": "absolute", "size": 0.10},
                   "low_m3_ha": rnd(eff_lo, 0), "high_m3_ha": rnd(eff_hi, 0),
                   "swing_mm": None, "swing_pct": rnd(100 * (eff_hi - eff_lo) / gross(base, efficiency), 1),
                   "elasticity": None, "affects": "gross water only"})
    inputs.sort(key=lambda r: -(r["swing_pct"] or 0))
    return {
        "crop": crop["id"], "years": years, "period": list(RECENT),
        "base_mm": rnd(base, 0), "base_m3_ha": rnd(gross(base, efficiency), 0),
        "inputs": inputs,
        "method": "one-at-a-time, FAO-56 single-Kc daily root-zone balance replayed over the recent period",
        "limits": "Inputs varied one at a time; interactions and correlated errors are not captured.",
    }
