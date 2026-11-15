"""Catalogue of data sources and the provenance record of one analysis.

Every number the engine shows can be traced to an entry here: which source, how it was
obtained (live, stored copy, nearest stored point, monthly approximation, default, the
farmer's own choice, or not available), when the provider produced it, and how complete it was.

Status values (provenance[*].status):
  live            fetched from the provider during this request (or within its cache lifetime)
  cache           stored copy of an earlier answer of the provider for this point
  fallback        stored data of the nearest point (distance given), the provider failed
  approximate     a coarser product of the same provider (NASA POWER monthly means)
  default         the provider failed and a documented default class is used instead
  chosen          the farmer chose the value; the provider was not asked
  replaced        a better source for this place was used instead (e.g. USDA SSURGO)
  unavailable     the provider failed and the part that needs it is skipped
  disabled        switched off in the provider settings
  offline_dataset a bundled, versioned file (FAOSTAT, MapSPAM), not fetched at request time
  not_used        available, but this analysis does not need it
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from . import net

_DATA = Path(__file__).parent / "data"

CATALOGUE = [
    {"id": "nasa_power", "name": "NASA POWER (LaRC)", "nasa": True, "access": "api", "net": "nasa_power",
     "provides": "daily temperature, precipitation, humidity, wind, solar radiation, root-zone wetness since 1991",
     "resolution": "0.5 x 0.625 deg (MERRA-2 meteorology), 1 deg (CERES solar)", "latency": "1-3 days",
     "url": "https://power.larc.nasa.gov/", "license": "NASA open data; cite the POWER Project"},
    {"id": "nasa_gibs", "name": "NASA GIBS", "nasa": True, "access": "api", "net": "nasa_gibs",
     "provides": "map imagery: MODIS/VIIRS NDVI, land temperature, IMERG rain, SMAP/GLDAS soil moisture, GRACE, SRTM",
     "resolution": "250 m - 0.25 deg depending on layer", "latency": "hours to weeks per layer",
     "url": "https://nasa-gibs.github.io/gibs-api-docs/", "license": "NASA open data"},
    {"id": "nasa_earthdata", "name": "NASA Earthdata (CMR, OPeNDAP, LP DAAC)", "nasa": True, "access": "api",
     "net": "earthdata_cmr", "net_also": ["earthdata_data"], "needs_key": "EARTHDATA_TOKEN",
     "provides": "SMAP L4 soil moisture, GPM IMERG rain, GLDAS, MODIS and HLS NDVI, ECOSTRESS (see /api/observations)",
     "resolution": "30 m (HLS) - 0.25 deg (GLDAS)", "latency": "hours to days",
     "url": "https://www.earthdata.nasa.gov/", "license": "NASA open data; free Earthdata login"},
    {"id": "nasa_srtm", "name": "NASA SRTM GL1 (via OpenTopoData)", "nasa": True, "access": "api", "net": "srtm",
     "provides": "elevation, slope and aspect of the field", "resolution": "30 m", "latency": "static (2000)",
     "url": "https://www.earthdata.nasa.gov/data/instruments/srtm", "license": "public domain"},
    {"id": "soilgrids", "name": "ISRIC SoilGrids 2.0", "nasa": False, "access": "api", "net": "soilgrids",
     "provides": "topsoil clay, sand, silt, organic carbon, pH (0-30 cm)", "resolution": "250 m", "latency": "static (model)",
     "url": "https://www.isric.org/explore/soilgrids", "license": "CC BY 4.0"},
    {"id": "open_meteo", "name": "Open-Meteo (forecast, ERA5, ensembles)", "nasa": False, "access": "api",
     "net": "open_meteo", "net_also": ["open_meteo_ensemble"],
     "provides": "16-day forecast, ECMWF/GFS ensembles, ERA5 recent past", "resolution": "9-25 km", "latency": "hourly",
     "url": "https://open-meteo.com/", "license": "CC BY 4.0"},
    {"id": "faostat", "name": "FAOSTAT QCL + PP (FAO)", "nasa": False, "access": "offline",
     "file": "country_crops.json", "provides": "national crop mix, yield and producer price",
     "resolution": "country, mean of the last 5 years with data", "latency": "annual",
     "url": "https://www.fao.org/faostat/", "license": "CC BY-NC-SA 3.0 IGO"},
    {"id": "mapspam", "name": "MapSPAM 2020 v2r2 (IFPRI)", "nasa": False, "access": "offline",
     "file": "local_crops.json.gz", "provides": "harvested area of 46 crops by province",
     "resolution": "5 arc-min aggregated to 0.25 deg", "latency": "static (2020)",
     "url": "https://doi.org/10.7910/DVN/SWPENT", "license": "CC BY 4.0"},
]
BY_ID = {c["id"]: c for c in CATALOGUE}
_file_info: dict[str, dict] = {}


def file_info(name: str) -> dict:
    """Version fingerprint of a bundled dataset: size, SHA-256 (first 12 hex), modification time."""
    if name not in _file_info:
        path = _DATA / name
        try:
            raw = path.read_bytes()
            _file_info[name] = {
                "file": f"backend/data/{name}", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()[:12],
                "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            }
        except OSError:
            _file_info[name] = {"file": f"backend/data/{name}", "missing": True}
    return _file_info[name]


def catalogue() -> list[dict]:
    """Every source with its live network health (or dataset fingerprint)."""
    health = net.status()
    out = []
    for c in CATALOGUE:
        item = {k: v for k, v in c.items() if k not in ("net", "net_also", "file")}
        if c["access"] == "offline":
            info = file_info(c["file"])
            item.update({"state": "missing" if info.get("missing") else "offline_dataset", "dataset": info})
        else:
            channels = [c["net"], *c.get("net_also", [])]
            item["channels"] = {ch: health[ch] for ch in channels if ch in health}
            states = [health[ch]["state"] for ch in channels if ch in health]
            item["state"] = ("down" if "down" in states else "degraded" if "degraded" in states
                             else "ok" if "ok" in states else "unknown")
            if c.get("needs_key"):
                item["key_configured"] = bool(os.environ.get(c["needs_key"]))
                if not item["key_configured"]:
                    item["state"] = "not_configured"
        out.append(item)
    return out


def _age_days(stamp: str | None) -> float | None:
    if not stamp or stamp.startswith("test"):
        return None
    try:
        t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - t).total_seconds() / 86400, 1)


def power_record(data: dict | None, source: str, fallback: dict | None, error: str | None) -> dict:
    status = {"live": "live", "cache": "cache", "fallback": "fallback", "monthly": "approximate",
              "failed": "unavailable", "disabled": "disabled"}.get(source, source)
    rec = {"id": "nasa_power", "name": BY_ID["nasa_power"]["name"], "status": status,
           "feeds": ["climate", "crops", "rotations", "advice"], "error": error}
    if data:
        q = data.get("quality") or {}
        params = q.get("parameters", {})
        rec.update({
            "fetched_at": data.get("fetched_at"), "age_days": _age_days(data.get("fetched_at")),
            "resolution": data.get("resolution"), "api_version": data.get("api_version"),
            "upstream_sources": data.get("sources", []),
            "point": {"lat": data.get("lat"), "lon": data.get("lon"), "elevation_m": data.get("elevation")},
            "period": {"start": data.get("start"), "last_day": (params.get("T2M") or {}).get("last")},
            "completeness": q.get("complete_share"),
            "missing_days": {k: v["missing"] for k, v in params.items() if v.get("missing")},
            "rejected_values": {k: v["rejected"] for k, v in params.items() if v.get("rejected")},
            "rejected_units": q.get("rejected_units", []),
        })
    if fallback:
        rec["fallback"] = fallback
    return rec


def simple_record(source_id: str, status: str, feeds: list[str], **extra) -> dict:
    return {"id": source_id, "name": BY_ID[source_id]["name"], "status": status, "feeds": feeds,
            **{k: v for k, v in extra.items() if v is not None}}


def offline_record(source_id: str, used: bool, feeds: list[str], detail: str | None = None) -> dict:
    c = BY_ID[source_id]
    return {"id": source_id, "name": c["name"], "status": "offline_dataset" if used else "not_used",
            "feeds": feeds, "dataset": file_info(c["file"]), "resolution": c["resolution"], "detail": detail}
