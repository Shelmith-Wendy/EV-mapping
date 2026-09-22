"""Shared paths, I/O, Nominatim setup, and pipeline schema constants."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Artifact filenames ---

STATIONS_STANDARD = "stations_standard.json"
LOCALITIES_GEOCODED = "localities_geocoded.json"
STATIONS_GEOCODED = "stations_geocoded.json"
COUNTIES_GEOJSON = "kenya_counties.geojson"
COUNTIES_DISPLAY_GEOJSON = "kenya_counties_display.geojson"
STATIONS_WITH_COUNTY = "stations_with_county.json"
TOWNS_REVERSE_GEOCODED = "towns_reverse_geocoded.json"

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
    "latitude",
    "longitude",
})

# Optional at ingest: coords may arrive later; landmark/phone/hours/maps URL are
# often absent from open-data CSVs (e.g. Taipei).
REQUIRED_STATION_FIELDS = STATION_FIELDS - {
    "latitude",
    "longitude",
    "landmark",
    "phone",
    "hours",
    "source_maps_url",
}

STATION_TYPE_VALUES = frozenset({"fast_charger", "swap_station", "standard_charger"})

PIPELINE_STEPS = (
    "ingest.py",
    "geocode_localities.py",
    "decode_station_coordinates.py",
    "build_county_boundaries.py",
    "assign_counties.py",
    "reverse_geocode_stations.py",
    "aggregate_stations.py",
)

MANIFEST_FILE = "manifest.json"
REGIONS_INDEX = "regions.json"

# --- Paths ---

def region_id() -> str:
    return os.environ.get("KENYA_EV_REGION", "kenya").strip() or "kenya"


def data_dir() -> Path:
    override = os.environ.get("KENYA_EV_DATA_DIR")
    if override:
        return Path(override)
    return ROOT / "data" / "staging" / region_id()


def public_dir(region: str | None = None) -> Path:
    return ROOT / "data" / "public" / (region or region_id())


def current_region():
    from regions import REGIONS

    rid = region_id()
    if rid not in REGIONS:
        raise KeyError(f"Unknown region {rid!r}; known: {sorted(REGIONS)}")
    return REGIONS[rid]


def summary_files(tiers: list[str]) -> tuple[str, ...]:
    return tuple(f"{tier}_summary.json" for tier in tiers)


def type_count_cols(station_types: list[str] | set[str] | frozenset[str]) -> list[str]:
    return [f"{name}_count" for name in sorted(station_types)]


def cache_files_for(cfg) -> tuple[str, ...]:
    return (
        STATIONS_STANDARD,
        LOCALITIES_GEOCODED,
        STATIONS_GEOCODED,
        COUNTIES_GEOJSON,
        COUNTIES_DISPLAY_GEOJSON,
        STATIONS_WITH_COUNTY,
        TOWNS_REVERSE_GEOCODED,
        *summary_files(cfg.tiers),
    )


def required_files_for(cfg) -> tuple[str, ...]:
    return (
        STATIONS_STANDARD,
        LOCALITIES_GEOCODED,
        STATIONS_GEOCODED,
        COUNTIES_GEOJSON,
        COUNTIES_DISPLAY_GEOJSON,
        STATIONS_WITH_COUNTY,
        *summary_files(cfg.tiers),
    )


def dashboard_files_for(cfg) -> tuple[str, ...]:
    return (*summary_files(cfg.tiers), STATIONS_WITH_COUNTY, COUNTIES_DISPLAY_GEOJSON)


def write_regions_index(public_root: Path | None = None) -> Path:
    """Write data/public/regions.json from the live RegionConfig registry."""
    from regions import REGIONS

    root = public_root or (ROOT / "data" / "public")
    entries = []
    for cfg in REGIONS.values():
        entries.append({
            "region_id": cfg.region_id,
            "display_name": cfg.display_name,
            "cluster": cfg.cluster,
            "bounds": [list(cfg.bounds[0]), list(cfg.bounds[1])],
            "tiers": list(cfg.tiers),
            "station_types": sorted(set(cfg.station_type_map.values())),
            "data_path": cfg.region_id,
        })
    path = root / REGIONS_INDEX
    write_json(path, {"regions": entries})
    return path


# Kenya-shaped defaults for volume CLI helpers that still target data/public/kenya.
DASHBOARD_FILES = dashboard_files_for(
    type("Cfg", (), {"tiers": ["county", "town"]})()
)
VOLUME_PUBLIC_FILES = DASHBOARD_FILES + (MANIFEST_FILE,)


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
