# EV-Charger-Map

Interactive map of EV charging and battery-swap coverage by region. Pull public station lists, geocode and assign them to admin boundaries, then publish JSON the dashboard can trust.

Kenya (Zeno Moto) is the primary product view. Taipei is a second region that proves the same pipeline works for a different geography, language, and set of operators.

## What you get

- **Choropleth map** — admin units shaded by station density, with per-station markers
- **Type filters** — fast charger, swap station, and (where present) standard charger
- **Manufacturer filter** — shown when the selected region has more than one operator (Taipei)
- **Drill-down** — Kenya: county → town; Taipei: district
- **One region at a time** — Kenya is the default; the dashboard picker switches region
- **Updated time** — `retrieved_at` from that region’s `manifest.json`, shown under the title
- **Validated publish** — totals and contract files must pass before promotion; a failed region keeps last-good public data

## How it works

```text
Sources                    Pipeline                         Outputs
───────                    ────────                         ───────
Zeno HTML ─┐
Taipei CSVs┤──► ingest → geocode → assign → aggregate → validate
Admin GeoJSON┘                                              │
                                                            ▼
                                              data/public/<region>/
                                                     │
                          ┌──────────────────────────┼──────────────────────────┐
                          ▼                          ▼                          ▼
                    dashboard/ (:8000)     app/static/ (baked)          UC Volume (opt.)
                    reads ../data/public   Streamlit iframe             contract files
```

Each region is a plug-in under `regions/`. Shared stages in `scripts/` look up the active `RegionConfig` and run the same step list — they never hard-code “Kenya” or “Taipei”.

```text
regions/kenya.py  ──┐
regions/taipei.py ──┼──► RegionConfig ──► shared pipeline ──► data/public/
                    ┘
```

### Pipeline stages

| Step | Script | Role |
|------|--------|------|
| 1 | `ingest.py` | Fetch and normalize stations |
| 2 | `geocode_localities.py` | Nominatim anchors for localities (skipped if coords already exist) |
| 3 | `decode_station_coordinates.py` | Plus Codes + Maps URL resolution (Kenya-heavy) |
| 4 | `build_county_boundaries.py` | Admin polygons for choropleth |
| 5 | `assign_counties.py` | Point-in-polygon (+ small nearest fallback) |
| 6 | `reverse_geocode_stations.py` | Town labels when the region has a town tier |
| 7 | `aggregate_stations.py` | Per-tier summaries with type counts |
| — | `validate_publish.py` | Contract checks before promote |

Run all regions with `uv run python scripts/run_pipeline.py`. Regions fail independently.

## Regions

| Region | Source | Station types | Admin tiers |
|--------|--------|---------------|-------------|
| **Kenya** | [Zeno locations page](https://zeno.earth/locations/locations.html) | `fast_charger`, `swap_station` | county → town |
| **Taipei** | [Battery-swap registry](https://data.taipei/api/frontstage/tpeod/dataset/resource.download?rid=c0f07b6c-ff55-4fae-a390-ddfe9374d4d7) · [Charging registry](https://data.taipei/api/frontstage/tpeod/dataset/resource.download?rid=c940eb09-c131-46e5-837c-5c96e7897253) | `swap_station`, `standard_charger` | district |

Published snapshots live under `data/public/<region_id>/` plus a top-level `data/public/regions.json` index.

## Project layout

```text
regions/          # Adapters (kenya, taipei) + RegionConfig contract
scripts/          # Pipeline, validation, Volume helpers, Databricks Job entry
data/
  raw/            # Cached sources, boundary repos, town overrides
  staging/        # Per-run working copy (gitignored)
  public/         # Promoted artifacts the UI reads
dashboard/        # Leaflet + Chart.js UI (region, type, manufacturer)
app/              # Streamlit viewport for Databricks Apps
app/static/       # Bake from prepare_databricks_app.sh (gitignored)
databricks.yml    # Asset Bundle: schema, Volume, App
```

## Requirements

- Python **≥ 3.13** (see `.python-version`)
- [uv](https://docs.astral.sh/uv/) for install and running

```bash
uv sync
```

That creates `.venv` and installs everything in `pyproject.toml` (pipeline + Streamlit).  
Pipeline-only deps (no Streamlit): `requirements-pipeline.txt`.

Prefer `uv run …` so you don’t need a global `python` on PATH:

```bash
uv run python scripts/run_pipeline.py
```

## Local usage

### Refresh data

```bash
uv run python scripts/run_pipeline.py
```

Force re-download of sources:

```bash
# Git Bash / macOS / Linux
KENYA_EV_FORCE_FETCH=1 uv run python scripts/run_pipeline.py

# PowerShell
$env:KENYA_EV_FORCE_FETCH=1; uv run python scripts/run_pipeline.py
```

Optional: publish contract files after promote by setting  
`KENYA_EV_VOLUME_ROOT=/Volumes/<catalog>/<schema>/<volume>`.

### Dashboard

Serve the repo root and open the dashboard:

```bash
uv run python -m http.server 8000
# → http://localhost:8000/dashboard/
```

The map loads `../data/public/regions.json` and each region’s JSON/GeoJSON.

### Streamlit shell

A full-viewport iframe around that same dashboard. Streamlit does not draw the filters; it only forwards `region`, `type`, and `manufacturer` from its own URL into the iframe on load. Example: `http://127.0.0.1:8501/?region=taipei&type=swap_station&manufacturer=Gogoro%20Network`.

The app reads `app/static/`, not `dashboard/` or `data/public/` directly. Bake first:

```bash
./scripts/prepare_databricks_app.sh
cd app
uv run streamlit run app.py --server.enableStaticServing=true
```

The bake copies `dashboard/` into `app/static/dashboard/` (with cache-bust stamps) and copies `data/public/regions.json` plus each region’s dashboard files and `manifest.json`. Pipeline intermediates stay out of the snapshot.

Smoke check:

```bash
uv run python scripts/smoke_streamlit_app.py --skip-http   # files only
uv run python scripts/smoke_streamlit_app.py               # also the app on :8501
```

## Databricks

The Asset Bundle (`databricks.yml`) deploys:

- Unity Catalog **schema** `zeno_ev_map` (default catalog `main`)
- Managed **Volume** `public` for published dashboard files
- **App** `kenya-ev-map` from `./app`

Set `workspace_host` (and catalog if needed), then:

```bash
./scripts/prepare_databricks_app.sh   # bake dashboard + contract files into app/static
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run kenya_ev_map -t dev
```

`app/static/` is gitignored. `databricks.yml` lists `sync.include` for `app/static/dashboard/**` and `app/static/data/**` so the bake is still uploaded.

### Volume layout

```text
/Volumes/<catalog>/<schema>/<volume>/
  regions.json
  kenya/
    stations_with_county.json
    county_summary.json
    town_summary.json
    kenya_counties_display.geojson
    manifest.json
  taipei/
    stations_with_county.json
    district_summary.json
    kenya_counties_display.geojson   # shared filename; district polygons
    manifest.json
```

Defaults: `/Volumes/main/zeno_ev_map/public/`.

Seed from a machine with CLI auth:

```bash
./scripts/upload_public_to_volume.sh main zeno_ev_map public
```

Validate locally:

```bash
uv run python scripts/volume_cli.py write-manifest --public-dir data/public/kenya
uv run python scripts/volume_cli.py validate --public-dir data/public/kenya
```

### Scheduled refresh

`scripts/databricks_pipeline_job.py` is the Job entry point: force-fetch sources, run the full pipeline for every registered region, and publish to the Volume. Wire it as a `spark_python_task` in the bundle when you enable automated refresh. The App today serves a baked snapshot from `prepare_databricks_app.sh`; live Volume reads are a follow-on.

## Adding a region

1. Implement a `RegionConfig` in `regions/` (station source, boundary source, tiers, types, bounds, cluster).
2. Register it in `regions/__init__.py`.
3. Run `uv run python scripts/run_pipeline.py` and confirm `data/public/<id>/` + `regions.json`.
4. The standalone dashboard picks up new entries from `regions.json`. Re-run `./scripts/prepare_databricks_app.sh` before starting Streamlit so the bake includes the new region.

Finding usable public data for a new place is still manual work; the shared pipeline is the part that should not need rewriting.

## Tech stack

| Layer | Stack |
|-------|--------|
| Pipeline | Python 3.13, pandas, shapely, geopy/Nominatim, BeautifulSoup, openlocationcode |
| UI | Leaflet, MapLibre GL, Chart.js |
| Hosting | Streamlit · Databricks Apps · Unity Catalog Volumes |
