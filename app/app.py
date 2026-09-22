"""Thin Streamlit shell: metrics + type filter around the existing Leaflet dashboard."""

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
PUBLIC = PUBLIC_ROOT / "kenya"
STATIC_DIR = APP_DIR / "static"
STATIC_DASHBOARD = STATIC_DIR / "dashboard"
STATIC_PUBLIC_ROOT = STATIC_DIR / "data" / "public"
STATIONS_FILE = PUBLIC / "stations_with_county.json"
MANIFEST_FILE = PUBLIC / "manifest.json"
MAP_PATH = "/app/static/dashboard/index.html"

TYPE_LABELS = {
    "all": "All",
    "fast_charger": "Fast charger",
    "swap_station": "Swap station",
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
[data-testid="stHorizontalBlock"] > div:nth-child(2) [data-testid="stMetricValue"] {
  color: #4ade80 !important;
}
[data-testid="stHorizontalBlock"] > div:nth-child(3) [data-testid="stMetricValue"] {
  color: #fb923c !important;
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


STATIC_READY = copy_static_assets()
ASSET_VERSION = _stamp_dashboard_cache_bust() if STATIC_READY else "0"


@st.cache_data
def load_stations() -> pd.DataFrame:
    if not STATIONS_FILE.exists():
        return pd.DataFrame(columns=["station_type", "county"])
    return pd.read_json(STATIONS_FILE)


manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8")) if MANIFEST_FILE.exists() else {}
generated_at = manifest.get("generated_at")
updated_html = (
    f'<p class="kem-updated">Updated {datetime.fromisoformat(generated_at):%b %d, %Y · %H:%M} UTC</p>'
    if generated_at
    else ""
)

st.set_page_config(page_title="Kenya EV Map", layout="wide")
st.markdown(SHELL_CSS, unsafe_allow_html=True)
st.markdown(
    '<p class="kem-eyebrow">Zeno Moto · Kenya</p>'
    '<h1 class="kem-title">EV charging coverage</h1>'
    f"{updated_html}",
    unsafe_allow_html=True,
)

if not STATIONS_FILE.exists():
    st.error("Missing data/public/kenya/. Run: python scripts/run_pipeline.py")
    st.stop()

if not STATIC_READY:
    st.error("Dashboard assets missing under app/static/dashboard/.")
    st.stop()

stations = load_stations()
station_type = st.radio(
    "Station type",
    list(TYPE_LABELS),
    horizontal=True,
    format_func=TYPE_LABELS.get,
    label_visibility="collapsed",
)

visible = stations if station_type == "all" else stations[stations["station_type"] == station_type]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Stations", len(visible))
c2.metric("Fast", int((visible["station_type"] == "fast_charger").sum()))
c3.metric("Swap", int((visible["station_type"] == "swap_station").sum()))
c4.metric("Counties", int(visible["county"].nunique()))

# Keep a pixel height fallback: stretch can collapse on some Streamlit layouts.
st.iframe(f"{MAP_PATH}?embed=1&type={station_type}&v={ASSET_VERSION}", height=780)
