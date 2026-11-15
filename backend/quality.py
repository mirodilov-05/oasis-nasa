"""Unit checks, plausibility limits and missing-data accounting for provider data.

Rules:
  * a value outside its physical range is set to missing (None) and counted as "rejected";
    it is never clipped, guessed or replaced here
  * every parameter reports how many values are present, missing and rejected, and the
    first and last day with data, so the analysis can say how complete its input was
  * a provider whose declared unit differs from the unit the engine expects is rejected
    for that parameter (the values are not converted on a guess)
"""
from __future__ import annotations

from datetime import date, timedelta

# NASA POWER, community AG: expected unit (as POWER writes it) and physical range
POWER_LIMITS = {
    "T2M": ("C", -90.0, 60.0),
    "T2M_MAX": ("C", -90.0, 65.0),
    "T2M_MIN": ("C", -95.0, 50.0),
    "PRECTOTCORR": ("mm/day", 0.0, 1000.0),
    "RH2M": ("%", 0.0, 100.0),
    "WS2M": ("m/s", 0.0, 60.0),
    "ALLSKY_SFC_SW_DWN": ("MJ/m^2/day", 0.0, 45.0),
    "GWETROOT": ("1", 0.0, 1.0),
}
# Units POWER has used for the same quantity in different API versions
UNIT_ALIASES = {"C": {"C", "°C", "degC"}, "mm/day": {"mm/day", "mm day-1"}, "%": {"%"},
                "m/s": {"m/s", "m s-1"}, "MJ/m^2/day": {"MJ/m^2/day", "MJ/m2/day"}, "1": {"1", "-", ""}}


def unit_ok(expected: str, declared: str | None) -> bool:
    """Missing declarations are accepted (older cached files); a different declared unit is not."""
    return declared is None or declared.strip() in UNIT_ALIASES.get(expected, {expected})


def check_power(params: dict, start: str, units: dict | None = None) -> dict:
    """Validate a NASA POWER record in place: out-of-range values -> None.

    params: {name: [value or None per day]}; units: {name: declared unit} from the response.
    -> {"parameters": {name: {...counts...}}, "rejected_units": [names], "complete_share": float}
    """
    first_day = date.fromisoformat(start)
    report, bad_units = {}, []
    for name, series in params.items():
        unit, lo, hi = POWER_LIMITS.get(name, (None, None, None))
        if unit is not None and units and not unit_ok(unit, units.get(name)):
            bad_units.append(name)
            params[name] = series = [None] * len(series)
        rejected = 0
        if lo is not None:
            for i, v in enumerate(series):
                if v is not None and not lo <= v <= hi:
                    series[i] = None
                    rejected += 1
        present = [i for i, v in enumerate(series) if v is not None]
        # the newest days are usually not published yet: they are "pending", not missing
        last = present[-1] if present else -1
        missing_inside = sum(1 for v in series[: last + 1] if v is None)
        report[name] = {
            "unit": unit, "present": len(present), "missing": missing_inside, "rejected": rejected,
            "pending_recent_days": len(series) - last - 1,
            "first": (first_day + timedelta(days=present[0])).isoformat() if present else None,
            "last": (first_day + timedelta(days=last)).isoformat() if present else None,
        }
    core = [report[n] for n in ("T2M", "PRECTOTCORR") if n in report]
    share = (min(r["present"] / max(1, r["present"] + r["missing"]) for r in core) if core else 0.0)
    return {"parameters": report, "rejected_units": bad_units, "complete_share": round(share, 4)}


SOIL_LIMITS = {"clay_pct": (0, 100), "sand_pct": (0, 100), "silt_pct": (0, 100),
               "organic_carbon_g_kg": (0, 600), "ph": (2.0, 12.0)}


def check_soil(values: dict) -> list[str]:
    """Problems of a parsed SoilGrids answer; [] when it is plausible."""
    problems = [f"{k} {values[k]} outside {lo}-{hi}" for k, (lo, hi) in SOIL_LIMITS.items()
                if values.get(k) is not None and not lo <= values[k] <= hi]
    total = sum(values.get(k) or 0 for k in ("clay_pct", "sand_pct", "silt_pct"))
    if values.get("clay_pct") is not None and values.get("sand_pct") is not None and not 90 <= total <= 110:
        problems.append(f"clay+sand+silt = {total:.0f} %, expected about 100 %")
    return problems


OPEN_METEO_UNITS = {"precipitation_sum": "mm", "et0_fao_evapotranspiration": "mm",
                    "temperature_2m_max": "°C", "temperature_2m_min": "°C",
                    "shortwave_radiation_sum": "MJ/m²", "precipitation_probability_max": "%"}
FORECAST_LIMITS = {"rain_mm": (0, 1000), "et0_mm": (0, 25), "t_max": (-90, 65), "t_min": (-95, 50),
                   "rain_probability": (0, 100)}


def check_open_meteo_units(daily_units: dict | None) -> list[str]:
    """Daily variables whose declared unit is not the one the engine computes with."""
    if not daily_units:
        return []
    return [k for k, unit in OPEN_METEO_UNITS.items() if k in daily_units and daily_units[k] != unit]


def clean_forecast_days(days: list[dict]) -> int:
    """Set implausible forecast values to None in place; returns how many were rejected."""
    rejected = 0
    for d in days:
        for k, (lo, hi) in FORECAST_LIMITS.items():
            v = d.get(k)
            if v is not None and not lo <= v <= hi:
                d[k] = None
                rejected += 1
    return rejected
