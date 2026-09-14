# kenya-ev-map

kenya-ev-map maps Zeno Moto's EV charging stations across Kenya, organizing them by county and city so coverage, gaps, and local access are easy to see. Raw inputs live in `data/raw/`. The pipeline writes to `data/staging/`, validates, then promotes to `data/public/` (what the dashboard reads). Each stage lives as a single script in `scripts/`; run them all with `python scripts/run_pipeline.py`.

## Streamlit shell (Phase 0 baseline)

Thin wrapper: Streamlit metrics + station-type filter, existing Leaflet dashboard in an iframe.
On startup the app copies `dashboard/` + `data/public/` into `app/static/` (Streamlit cannot serve out-of-tree symlinks).

```bash
cd app
../.venv/bin/streamlit run app.py --server.enableStaticServing=true
```

Open http://localhost:8501 — confirm map, charts, type filter, and Clear. Smoke check (with the app running):

```bash
.venv/bin/python scripts/smoke_streamlit_app.py
```

## Phase 1 — Databricks App (baked snapshot)

Databricks Apps only ship `app/`. Before deploy, bake `dashboard/` + `data/public/` into the app package:

```bash
./scripts/prepare_databricks_app.sh
```

Set your workspace host in `databricks.yml` (`variables.workspace_host.default`) or pass it on the CLI, then:

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run kenya_ev_map -t dev
```

`bundle deploy` creates/updates the App resource; `bundle run kenya_ev_map` deploys code and starts it. Data is a snapshot until Phase 3–4 (Job → Volume refresh).

## Phase 2 — Volume contract

Published dashboard files live on a Unity Catalog Volume (Job writes in Phase 3; App reads in Phase 4):

```text
/Volumes/<catalog>/<schema>/<volume>/
  stations_with_county.json
  county_summary.json
  town_summary.json
  kenya_counties_display.geojson
  manifest.json
```

Defaults: `main.zeno_ev_map.public` → `/Volumes/main/zeno_ev_map/public/`.

`manifest.json` records `generated_at`, `pipeline_run_id`, `retrieved_at`, `station_count`, and per-file `sha256` / size.

```bash
# Write + validate local contract
python scripts/volume_cli.py write-manifest
python scripts/volume_cli.py validate

# Deploy schema + managed Volume (catalog must already exist)
# Set variables.catalog / workspace_host in databricks.yml as needed
databricks bundle deploy -t dev

# Seed the Volume manually (Phase 2 exit check)
./scripts/upload_public_to_volume.sh main zeno_ev_map public
```

**Identities:** Job needs write on the Volume; App service principal needs read (wired in Phase 4–5). Until then, upload with your user CLI.
