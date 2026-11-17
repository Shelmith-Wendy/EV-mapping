# EV-Charger-Map

Interactive map of EV charging and battery-swap coverage across **Kenya** and **Taipei**. Ingests and normalizes station data from public registries and operators, maps coordinates to administrative boundaries, and publishes validated datasets for web and dashboard consumption.

Primary coverage:
- **Kenya** — EV charging and battery-swap networks across operators (ARC Ride, Spiro, Zeno Moto) with county- and town-level drill-down.
- **Taipei** — Open-data charging and battery-swap registries alongside the **TVS candidate placement sheet** with road-distance routing, and district-level drill-down.

---

## Features

- **Choropleth Map** — Administrative boundaries shaded by station density, with interactive station markers.
- **Station Type & Operator Filters** — Filter by type (charging or swap) and operator (e.g. ARC Ride, Spiro, Zeno Moto in Kenya; Gogoro Network and others in Taipei).
- **TVS Candidate Spacing & Road Routing** — Taipei placement candidates display nearest-neighbor road distance calculated via OSRM, average candidate spacing, and solid route polylines on selection.
- **Administrative Drill-down** — Kenya: county → town; Taipei: district.

---

## Architecture & Pipeline

```text
Sources                                 Pipeline                                Outputs
───────                                 ────────                                ───────
Kenya Operators (ARC, Spiro, Zeno) ┐
Taipei Open Data CSVs ─────────────┼──► ingest → geocode → assign → aggregate ──► data/public/<region>/
Admin GeoJSON ─────────────────────┘                                                      │
TVS Placement CSV ─────────────────┐                                                      │
OSRM Road Routing ─────────────────┴──► compute_tvs_road_distances ───────────────────────┤
                                                                                          │
                                ┌─────────────────────────────────────────────────────────┴─────────┐
                                ▼                                                                   ▼
                      dashboard/ (:8000)                                                   app/static/ (baked)
                      reads ../data/public                                                 Streamlit / Databricks Apps
```

### Pipeline Stages

| Step | Script | Role |
|------|--------|------|
| 1 | `ingest.py` | Fetch and normalize station records into common schema |
| 2 | `geocode_localities.py` | Geocode localities via Nominatim (cached) |
| 3 | `decode_station_coordinates.py` | Plus Codes and Google Maps URL resolution |
| 4 | `build_county_boundaries.py` | Process admin boundary GeoJSON for choropleth rendering |
| 5 | `assign_counties.py` | Point-in-polygon matching (+ small nearest fallback) |
| 6 | `reverse_geocode_stations.py` | Town-level labels for regions with secondary administrative tiers |
| 7 | `aggregate_stations.py` | Per-tier station count summaries by type |
| Kenya | `scripts/ingest_operator.py` | Ingest ARC Ride / Spiro station snapshots |
| Kenya | `scripts/merge_swap_into_kenya_public.py` | Merge multi-operator swap stations into Kenya public data |
| Taipei | `scripts/compute_tvs_road_distances.py` | OSRM road distance and polyline route calculator |
| Taipei | `scripts/load_tvs_candidates.py` | Placement sheet + road distance merger → `tvs_candidates.json` |
| — | `validate_publish.py` | Pre-promote contract validation and checksum enforcement |

Run the standard pipelines:
```bash
uv run python scripts/run_pipeline.py
```

---

## Regions

| Region | Identifier | Primary Sources | Station Types | Administrative Tiers |
|--------|------------|-----------------|---------------|----------------------|
| **Kenya** | `kenya` | Operator networks (ARC Ride, Spiro, Zeno Moto) | `swap_station`, `fast_charger` | county → town |
| **Taipei** | `taipei` | Taipei Open Data (Swap & Charging registries) | `swap_station`, `standard_charger` | district |

---

## TVS Candidate Spacing & Routing (Taipei)

The TVS optimal placement sheet (`data/raw/taipei/ev_optimal_locations_taipei.csv`) evaluates candidate sites across Taipei. Nearest-neighbor distance is computed along the drivable road network using OSRM:
$$\text{road\_distance\_m} = \text{route\_distance} + \text{snap\_from\_m} + \text{snap\_to\_m}$$

1. **Calculate road distances & route geometries:**
   ```bash
   uv run python scripts/compute_tvs_road_distances.py
   ```
   Queries OSRM driving profile (`OSRM_BASE_URL`, default `https://router.project-osrm.org`), computes all-pairs nearest road distance, and fetches `polyline6` route geometry. Saves results to `data/raw/taipei/tvs_road_distances.json` with an input SHA-256 hash.

2. **Merge into candidate records:**
   ```bash
   $env:KENYA_EV_DATA_DIR="data/public/taipei"; uv run python scripts/load_tvs_candidates.py
   ```
   Verifies the candidate hash and merges road distances and polyline routes into `data/public/taipei/tvs_candidates.json`.

3. **Dashboard display:**
   - Enabling **TVS candidates** displays the candidate list with average nearest road spacing.
   - Clicking a candidate draws the solid road route directly on the map.

---

## Kenya Station Ingestion & Security Rules

The `scripts/ingestion/` module provides reproducible snapshot ingestion for Kenya swap operators (ARC Ride, Spiro):

- **Safety Gates**: Blocks ingestion if station drops exceed 40% (override with `--force`).
- **Co-Location Handling**: Identifies multiple cabinets sharing identical coordinates and assigns stable IDs.
- **Device Identifier Confidentiality**: Serial numbers and hardware IDs are held in memory only to count unique cabinets; they are **never written to disk, reports, or commits**.
- **Network Controls**: Spiro is manual file input only (no automated scraping). ARC Ride network access is **off by default** (requires `--online`).

```bash
# Ingest operator snapshots
uv run python scripts/ingest_operator.py arcride --input data/inbox/arcride/latest.json
uv run python scripts/ingest_operator.py spiro --input data/inbox/spiro/latest.json

# Build public datasets and merge into Kenya view
uv run python scripts/build_swap_public.py
uv run python scripts/merge_swap_into_kenya_public.py
```

---

## Project Layout

```text
dashboard/        # Leaflet + MapLibre GL + Chart.js web application
app/              # Streamlit shell for Databricks Apps iframe embedding
app/static/       # Baked static assets (dashboard + public data)
data/
  raw/            # Raw CSVs, placement sheets, cached road distances
  staging/        # Per-run intermediate pipeline stages (gitignored)
  public/         # Published region JSON and GeoJSON outputs
  snapshots/      # Versioned operator station snapshots
regions/          # RegionConfig definitions (kenya, taipei)
scripts/          # Pipeline processing, TVS distance routing, operator ingestion
  ingestion/      # Swap operator registry, normalization adapters, safety gates
tests/            # Unit tests for ingestion core, adapters, and volume contracts
```

---

## Setup & Testing

### Requirements
- Python **≥ 3.13** (see `.python-version`)
- [uv](https://docs.astral.sh/uv/) package manager

```bash
# Install dependencies
uv sync

# Run test suite
uv run python -m unittest discover -s tests -p "test_*.py"
```

---

## Local Dashboard

Start a local static server from the repository root:
```bash
uv run python -m http.server 8000
```
Open **`http://localhost:8000/dashboard/?region=taipei`** (or `?region=kenya`) in your browser.

> **Note on `app/static/`:** If running a test server rooted in `app/static/`, open `http://localhost:8000/dashboard/` directly. Always sync changes from `dashboard/` to `app/static/dashboard/` after making frontend edits.
