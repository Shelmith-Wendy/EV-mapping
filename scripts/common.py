"""Shared paths, I/O, Nominatim setup, and pipeline schema constants."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- Artifact filenames ---

STATIONS_STANDARD = "stations_standard.json"
LOCALITIES_GEOCODED = "localities_geocoded.json"
STATIONS_GEOCODED = "stations_geocoded.json"
COUNTIES_GEOJSON = "kenya_counties.geojson"
COUNTIES_DISPLAY_GEOJSON = "kenya_counties_display.geojson"
STATIONS_WITH_COUNTY = "stations_with_county.json"
TOWNS_REVERSE_GEOCODED = "towns_reverse_geocoded.json"
COUNTY_SUMMARY = "county_summary.json"
TOWN_SUMMARY = "town_summary.json"

# --- Field / type constants ---

STATION_FIELDS = frozenset({
    "station_id",
    "name",
    "station_type",
    "locality",
    "landmark",
    "address_raw",
    "phone",
    "hours",
    "source_maps_url",
    "source_name",
    "retrieved_at",
})

STATION_TYPE_VALUES = frozenset({"fast_charger", "swap_station"})

ZENO_STATION_TYPES = {
    "Fast Charger": "fast_charger",
    "Swap Station": "swap_station",
}

COUNT_COLS = ("fast_charger_count", "swap_station_count", "total_count")

KENYA_COUNTY_COUNT = 47

CACHE_FILES = (
    STATIONS_STANDARD,
    LOCALITIES_GEOCODED,
    STATIONS_GEOCODED,
    COUNTIES_GEOJSON,
    COUNTIES_DISPLAY_GEOJSON,
    STATIONS_WITH_COUNTY,
    TOWNS_REVERSE_GEOCODED,
    COUNTY_SUMMARY,
    TOWN_SUMMARY,
)

REQUIRED_FILES = (
    STATIONS_STANDARD,
    LOCALITIES_GEOCODED,
    STATIONS_GEOCODED,
    COUNTIES_GEOJSON,
    COUNTIES_DISPLAY_GEOJSON,
    STATIONS_WITH_COUNTY,
    COUNTY_SUMMARY,
    TOWN_SUMMARY,
)

DASHBOARD_FILES = (
    COUNTY_SUMMARY,
    TOWN_SUMMARY,
    STATIONS_WITH_COUNTY,
    COUNTIES_DISPLAY_GEOJSON,
)

# Phase 2 Volume contract: dashboard artifacts + freshness manifest under /Volumes/.../public/
MANIFEST_FILE = "manifest.json"
VOLUME_PUBLIC_FILES = DASHBOARD_FILES + (MANIFEST_FILE,)

PIPELINE_STEPS = (
    "ingest.py",
    "geocode_localities.py",
    "decode_station_coordinates.py",
    "build_county_boundaries.py",
    "assign_counties.py",
    "reverse_geocode_stations.py",
    "aggregate_stations.py",
)

# --- Paths ---

def data_dir() -> Path:
    override = os.environ.get("KENYA_EV_DATA_DIR")
    if override:
        return Path(override)
    return ROOT / "data" / "staging"


# --- JSON I/O ---

def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data, *, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    if indent is not None:
        text += "\n"
    path.write_text(text, encoding="utf-8")


# --- Nominatim (lazy imports so non-geocode scripts skip geopy) ---

_NOMINATIM_USER_AGENT = "kenya-ev-map/0.1 (Kenya EV charging station mapping)"
_NOMINATIM_TIMEOUT_SECONDS = 10
_NOMINATIM_MIN_DELAY_SECONDS = 1


def _geolocator():
    from geopy.geocoders import Nominatim

    return Nominatim(user_agent=_NOMINATIM_USER_AGENT, timeout=_NOMINATIM_TIMEOUT_SECONDS)


def nominatim_geocode():
    from geopy.extra.rate_limiter import RateLimiter

    return RateLimiter(
        _geolocator().geocode,
        min_delay_seconds=_NOMINATIM_MIN_DELAY_SECONDS,
        max_retries=0,
        swallow_exceptions=False,
    )


def nominatim_reverse():
    from geopy.extra.rate_limiter import RateLimiter

    return RateLimiter(
        _geolocator().reverse,
        min_delay_seconds=_NOMINATIM_MIN_DELAY_SECONDS,
        max_retries=0,
        swallow_exceptions=False,
    )
