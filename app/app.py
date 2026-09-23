"""Thin Streamlit shell: region picker + metrics + type filter around the Leaflet dashboard."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent

# Databricks Apps only ship `source_code_path` (./app). Prefer in-app snapshot
# from `scripts/prepare_databricks_app.sh`; fall back to repo layout for local Phase 0.
if (APP_DIR / "dashboard").is_dir():
    DASHBOARD_SRC = APP_DIR / "dashboard"
else:
    DASHBOARD_SRC = REPO_ROOT / "dashboard"


def _resolve_public_root() -> Path:
    candidates = (
        APP_DIR / "data" / "public",
        REPO_ROOT / "data" / "public",
    )
    for path in candidates:
        if (path / "regions.json").is_file() or (path / "kenya" / "stations_with_county.json").is_file():
            return path
    return REPO_ROOT / "data" / "public"


PUBLIC_ROOT = _resolve_public_root()
STATIC_DIR = APP_DIR / "static"
STATIC_DASHBOARD = STATIC_DIR / "dashboard"
STATIC_PUBLIC_ROOT = STATIC_DIR / "data" / "public"
MAP_PATH = "/app/static/dashboard/index.html"

TYPE_LABELS = {
    "all": "All",
    "fast_charger": "Fast charger",
    "swap_station": "Swap station",
    "standard_charger": "Standard charger",
}

TIER_PLURALS = {
    "county": "Counties",
    "town": "Towns",
    "district": "Districts",
}

SHELL_CSS = """
<style>
@import url("https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=IBM+Plex+Sans:wght@400;500;600&display=swap");

html, body, [class*="css"] {
  font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
}
.stApp { background: #070b14; }
[data-testid="stHeader"] { background: #070b14; }
.block-container { padding-top: 1.25rem; padding-bottom: 0.5rem; }

.kem-eyebrow {
  color: #4ade80;
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  margin: 0 0 0.25rem;
}
.kem-title {
  font-family: "Barlow Condensed", sans-serif;
  font-size: clamp(1.6rem, 3vw, 2.1rem);
  font-weight: 800;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  color: #f8fafc;
  margin: 0 0 0.25rem;
  line-height: 1;
}
.kem-updated {
  color: #64748b;
  font-size: 0.78rem;
  margin: 0 0 1rem;
}

div[role="radiogroup"] {
  background: rgba(2, 6, 23, 0.45);
  border: 1px solid #1e293b;
  border-radius: 999px;
  padding: 4px;
  gap: 3px !important;
  width: fit-content;
}
div[role="radiogroup"] label {
  background: transparent !important;
  border-radius: 999px !important;
  padding: 0.45rem 1rem !important;
}
div[role="radiogroup"] label[data-checked="true"],
div[role="radiogroup"] label:has(input:checked) {
  background: #4ade80 !important;
  color: #070b14 !important;
}
div[role="radiogroup"] label p { font-weight: 600 !important; }
div[role="radiogroup"] svg { display: none; }

[data-testid="stMetric"] {
  background: rgba(2, 6, 23, 0.45);
  border: 1px solid #1e293b;
  border-radius: 10px;
  padding: 0.65rem 0.9rem;
}
[data-testid="stMetricLabel"] { color: #94a3b8 !important; }
[data-testid="stMetricValue"] {
  font-family: "Barlow Condensed", sans-serif !important;
  font-weight: 800 !important;
  font-size: 1.75rem !important;
  color: #f8fafc !important;
}

iframe { border: 1px solid #1e293b; border-radius: 12px; }
</style>
"""


def _stale(src: Path, dest: Path) -> bool:
    return not dest.exists() or src.stat().st_mtime > dest.stat().st_mtime


def _newest_mtime(folder: Path) -> float:
    if not folder.is_dir():
        return 0.0
    return max((path.stat().st_mtime for path in folder.rglob("*") if path.is_file()), default=0.0)


def _stamp_dashboard_cache_bust() -> str:
    """Rewrite static index.html asset query strings from file mtimes; return iframe bust token."""
    index_path = STATIC_DASHBOARD / "index.html"
    app_js = STATIC_DASHBOARD / "app.js"
    styles = STATIC_DASHBOARD / "styles.css"
    if not index_path.exists():
        return "0"
    app_v = str(int(app_js.stat().st_mtime)) if app_js.exists() else "0"
    css_v = str(int(styles.stat().st_mtime)) if styles.exists() else "0"
    original = index_path.read_text(encoding="utf-8")
    stamped = re.sub(r"styles\.css(?:\?v=[^\"']*)?", f"styles.css?v={css_v}", original)
    stamped = re.sub(r"app\.js(?:\?v=[^\"']*)?", f"app.js?v={app_v}", stamped)
    if stamped != original:
        index_path.write_text(stamped, encoding="utf-8")
    return app_v


def copy_static_assets() -> bool:
    """Copy dashboard + public data into app/static (Streamlit rejects out-of-tree symlinks)."""
    needs_dashboard = DASHBOARD_SRC.is_dir() and (
        not (STATIC_DASHBOARD / "index.html").exists()
        or _newest_mtime(DASHBOARD_SRC) > _newest_mtime(STATIC_DASHBOARD)
    )
    if needs_dashboard:
        if STATIC_DASHBOARD.exists():
            shutil.rmtree(STATIC_DASHBOARD)
        shutil.copytree(DASHBOARD_SRC, STATIC_DASHBOARD)

    if PUBLIC_ROOT.is_dir():
        STATIC_PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)
        regions_index = PUBLIC_ROOT / "regions.json"
        if regions_index.exists() and _stale(regions_index, STATIC_PUBLIC_ROOT / "regions.json"):
            shutil.copy2(regions_index, STATIC_PUBLIC_ROOT / "regions.json")
        for region_dir in PUBLIC_ROOT.iterdir():
            if not region_dir.is_dir():
                continue
            dest_dir = STATIC_PUBLIC_ROOT / region_dir.name
            dest_dir.mkdir(parents=True, exist_ok=True)
            for src in region_dir.glob("*"):
                if src.is_file() and _stale(src, dest_dir / src.name):
                    shutil.copy2(src, dest_dir / src.name)

    return (STATIC_DASHBOARD / "index.html").exists()


def load_regions_index() -> list[dict]:
    path = PUBLIC_ROOT / "regions.json"
    if not path.exists():
        return []
    regions = json.loads(path.read_text(encoding="utf-8")).get("regions") or []
    return sorted(
        regions,
        key=lambda region: (0 if region.get("region_id") == "kenya" else 1, region.get("display_name", "")),
    )


def tier_plural(tier: str) -> str:
    return TIER_PLURALS.get(tier, f"{tier.title()}s")


STATIC_READY = copy_static_assets()
ASSET_VERSION = _stamp_dashboard_cache_bust() if STATIC_READY else "0"


@st.cache_data
def load_stations(path: str) -> pd.DataFrame:
    return pd.read_json(path)


regions = load_regions_index()
if not regions:
    st.set_page_config(page_title="EV Map", layout="wide")
    st.markdown(SHELL_CSS, unsafe_allow_html=True)
    st.error("Missing data/public/regions.json. Run: python scripts/run_pipeline.py")
    st.stop()

region_by_id = {region["region_id"]: region for region in regions}
default_region_id = "kenya" if "kenya" in region_by_id else regions[0]["region_id"]

st.set_page_config(page_title="EV Map", layout="wide")
st.markdown(SHELL_CSS, unsafe_allow_html=True)

region_id = st.selectbox(
    "Region",
    [region["region_id"] for region in regions],
    index=[region["region_id"] for region in regions].index(default_region_id),
    format_func=lambda rid: region_by_id[rid]["display_name"],
)
region = region_by_id[region_id]
public_dir = PUBLIC_ROOT / region["data_path"]
stations_file = public_dir / "stations_with_county.json"
manifest_file = public_dir / "manifest.json"

manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else {}
generated_at = manifest.get("generated_at")
updated_html = (
    f'<p class="kem-updated">Updated {datetime.fromisoformat(generated_at):%b %d, %Y · %H:%M} UTC</p>'
    if generated_at
    else ""
)
source = region.get("dataset_source_label") or None
eyebrow = f"{source} · {region['display_name']}" if source else region["display_name"]

st.markdown(
    f'<p class="kem-eyebrow">{eyebrow}</p>'
    '<h1 class="kem-title">EV charging coverage</h1>'
    f"{updated_html}",
    unsafe_allow_html=True,
)

if not stations_file.exists():
    st.error(f"Missing data/public/{region['data_path']}/. Run: python scripts/run_pipeline.py")
    st.stop()

if not STATIC_READY:
    st.error("Dashboard assets missing under app/static/dashboard/.")
    st.stop()

stations = load_stations(str(stations_file))
station_types = sorted(region.get("station_types") or stations["station_type"].dropna().unique().tolist())
type_choices = ["all", *station_types]
station_type = st.radio(
    "Station type",
    type_choices,
    horizontal=True,
    format_func=lambda key: TYPE_LABELS.get(key, key.replace("_", " ").title()),
    label_visibility="collapsed",
    key=f"station_type_{region_id}",
)

visible = stations if station_type == "all" else stations[stations["station_type"] == station_type]
primary_tier = region["tiers"][0]
metric_cols = st.columns(2 + len(station_types))
metric_cols[0].metric("Stations", len(visible))
for index, type_name in enumerate(station_types, start=1):
    label = TYPE_LABELS.get(type_name, type_name.replace("_", " ").title())
    metric_cols[index].metric(label, int((visible["station_type"] == type_name).sum()))
metric_cols[-1].metric(
    tier_plural(primary_tier),
    int(visible[primary_tier].nunique()) if primary_tier in visible.columns else 0,
)

st.iframe(
    f"{MAP_PATH}?embed=1&region={region_id}&type={station_type}&v={ASSET_VERSION}",
    height=780,
)
