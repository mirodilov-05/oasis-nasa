# TerraShift

**The climate has already moved. Your crop rotation should move too.**

TerraShift turns 35 years of NASA climate data into a decision for one field: what
changed at this place, which crop rotation now wastes the least water, and how sure
we are. Pick a place on the map or search for it, and the analysis runs live — no
sign-in needed. The first visit opens with a programmatic cinematic intro (three.js
Earth, satellite pass, field scan) that can be skipped at any time.

## What is in this repository

One Python service: a **FastAPI** backend that also serves the **static frontend**.

| Path | Purpose |
|---|---|
| `backend/main.py` | API routes and static-site mount |
| `backend/` | engine: FAO-56 physics, climate trends, crop and rotation simulation, water balance, providers (NASA POWER, GIBS, Earthdata, SRTM, SoilGrids, Open-Meteo, FAOSTAT, MapSPAM, USDA) |
| `frontend/` | static pages: field (`index.html` + intro), rotations, climate, crops, current conditions, sources |
| `tests/` | pytest suite — no network needed |

Earlier documentation described a Next.js/Prisma app under `src/`; that code is not
part of this repository, and its leftover config files have been removed.

## Run it

Python 3.11+.

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt     # Windows: .venv\Scripts\pip
.venv/bin/uvicorn backend.main:app --port 8000
```

- Site: http://localhost:8000 (replay the intro: http://localhost:8000/?intro=1)
- API docs (OpenAPI): http://localhost:8000/docs
- Tests: `pip install pytest && python -m pytest tests -q`

Optional keys go in `.env` (copy `.env.example`; never commit `.env`):
`EARTHDATA_TOKEN` (NASA Earthdata observations), `NASS_API_KEY` (US county yields),
`OPENET_API_KEY` (western-US ET), `ADMIN_TOKEN` (editing data sources in the site).
Without them the corresponding sources report themselves as unavailable.

### Docker

```bash
docker build -t terrashift .
docker run -p 8000:8000 --env-file .env terrashift
```

## API

| Method | Path | What |
|---|---|---|
| GET | `/api/health` | service health |
| GET | `/api/presets`, `/api/options` | crops, rotations, soils, irrigation methods |
| GET | `/api/layers` | NASA GIBS map layers |
| GET | `/api/places` | place search (Nominatim, Photon backup) |
| GET | `/api/providers` | data-source status |
| POST / PATCH / DELETE | `/api/providers[/{id}]` | edit data sources (`ADMIN_TOKEN`) |
| GET | `/api/analyze` | full field analysis: climate shift, crops, rotations, advice, provenance, warnings |
| GET | `/api/water` | 16-day soil-water forecast with ensemble range |
| GET | `/api/observations` | per-point NASA observations (SMAP, IMERG, GLDAS, MODIS, HLS, ECOSTRESS) |
| GET | `/api/data-sources` | every data source: resolution, licence, live health, request policy |
| GET | `/api/sensitivity` | which uncertain input moves a crop's irrigation need most (tornado) |

Full schemas: `/docs`. Method and data flow: [ARCHITECTURE.md](ARCHITECTURE.md). Formula validation and change log: [SCIENCE.md](SCIENCE.md).

## Honest limits

- NASA POWER is a ≈50 km grid: neighbouring fields share one climate signal.
- Soil is a ≈250 m SoilGrids model estimate (or the farmer's chosen class), not a measurement.
- Crop thresholds, calendars and revenue per hectare are **draft** parameters.
- The intro is illustrative: the Earth, satellite and parcels are procedural, not
  real imagery; the analysis itself uses only real provider data, and failed
  sources are reported, never filled with invented values.
- Decision support for comparing options — not a yield forecast or agronomic advice.

## Roadmap (from the TerraShift master specification, not yet implemented)

Accounts, organizations and role-based access; farms with versioned field
boundaries and monitoring history; decisions, outcomes and report export;
PostgreSQL/PostGIS and Redis; request IDs, rate limits and structured logs;
browser E2E, performance and security test suites; the redesigned 14-section
application shell.

## Credits

NASA POWER (LaRC, Earth Science/Applied Science Program), NASA GIBS / Worldview,
NASA Earthdata, NASA SRTM, ISRIC SoilGrids, Open-Meteo, FAOSTAT, MapSPAM (IFPRI),
three.js (MIT). Method: FAO Irrigation and Drainage Paper 56 (Allen et al., 1998).
