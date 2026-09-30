"""Thin Streamlit shell: the standalone dashboard fills the viewport."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlencode

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = APP_DIR / "static" / "dashboard"
INDEX_PATH = DASHBOARD_DIR / "index.html"
APP_JS_PATH = DASHBOARD_DIR / "app.js"
MAP_PATH = "/app/static/dashboard/index.html"

SHELL_CSS = """
<style>
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
  height: 100%;
  background: #070b14;
  overflow: hidden;
}
header[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
footer,
[data-testid="stStatusWidget"] {
  display: none !important;
}
[data-testid="stMainBlockContainer"],
.block-container {
  padding: 0 !important;
  max-width: 100% !important;
  height: 100%;
}
[data-testid="stVerticalBlock"],
[data-testid="stElementContainer"] {
  height: 100%;
  gap: 0 !important;
}
iframe {
  border: 0 !important;
  border-radius: 0 !important;
  display: block;
  width: 100%;
  height: 100% !important;
  min-height: 100dvh;
}
</style>
"""


def dashboard_asset_version() -> str:
    return hashlib.sha256(INDEX_PATH.read_bytes()).hexdigest()[:12]


st.set_page_config(
    page_title="EV Map",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(SHELL_CSS, unsafe_allow_html=True)

if not INDEX_PATH.is_file() or not APP_JS_PATH.is_file():
    st.error(
        "Missing app/static/dashboard/index.html. "
        "Run ./scripts/prepare_databricks_app.sh before starting the app."
    )
    st.stop()

iframe_query = {}
for key in ("region", "type", "manufacturer"):
    value = st.query_params.get(key)
    if value:
        iframe_query[key] = value
iframe_query["v"] = dashboard_asset_version()
st.iframe(f"{MAP_PATH}?{urlencode(iframe_query)}", height="stretch")
