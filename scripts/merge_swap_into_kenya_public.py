#!/usr/bin/env python3
"""Merge ARC Ride & Spiro swap stations into the Kenya public dataset.

Reads  : data/public/kenya_swap/stations_with_county.json  (ARC Ride + Spiro, 666 stations)
         data/public/kenya/stations_with_county.json       (Zeno Moto, 114 stations)
         data/public/kenya/kenya_counties_display.geojson  (47 Kenya county polygons)
Writes : data/public/kenya/stations_with_county.json       (merged 780 stations)
         data/public/kenya/county_summary.json             (rebuilt 47 counties)
         data/public/kenya/town_summary.json               (rebuilt)
         data/public/kenya/manifest.json                   (rebuilt with hashes)
         data/public/regions.json                          (kenya_swap removed)
And mirrors all outputs to app/static/data/public/ for the Streamlit / Databricks app.

All logic uses only the Python standard library (no numpy, pandas, scipy, shapely)
in accordance with GEMINI.md environment and dependency constraints.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_PUBLIC = ROOT / "data" / "public"
APP_STATIC_PUBLIC = ROOT / "app" / "static" / "data" / "public"

KENYA_DIR = DATA_PUBLIC / "kenya"
SWAP_DIR = DATA_PUBLIC / "kenya_swap"

GEOJSON_PATH = KENYA_DIR / "kenya_counties_display.geojson"
SWAP_STATIONS_PATH = SWAP_DIR / "stations_with_county.json"
ZENO_STATIONS_PATH = KENYA_DIR / "stations_with_county.json"

FALLBACK_DISTANCE_DEG = 0.06
ZENO_OPERATOR = "Zeno Moto"


# ---------------------------------------------------------------------------
# Pure Python geometry helpers (Ray-Casting PIP + Line Segment Distance)
# ---------------------------------------------------------------------------

def point_in_poly(x: float, y: float, poly: list[list[float]]) -> bool:
    """Ray-casting algorithm to test if point (x, y) is inside polygon."""
    n = len(poly)
    inside = False
    p1x, p1y = poly[0]
    for i in range(n + 1):
        p2x, p2y = poly[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    """Check if point is within GeoJSON Polygon or MultiPolygon with holes."""
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    if gtype == "Polygon":
        if not point_in_poly(lon, lat, coords[0]):
            return False
        for hole in coords[1:]:
            if point_in_poly(lon, lat, hole):
                return False
        return True
    if gtype == "MultiPolygon":
        for poly in coords:
            if point_in_poly(lon, lat, poly[0]):
                in_hole = False
                for hole in poly[1:]:
                    if point_in_poly(lon, lat, hole):
                        in_hole = True
                        break
                if not in_hole:
                    return True
        return False
    return False


def point_to_segment_dist_sq(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """Squared distance from point (px, py) to line segment (x1, y1)-(x2, y2)."""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return (px - x1) ** 2 + (py - y1) ** 2
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return (px - (x1 + t * dx)) ** 2 + (py - (y1 + t * dy)) ** 2


def min_dist_to_geometry(lon: float, lat: float, geometry: dict) -> float:
    """Minimum Euclidean degree distance from point to geometry exterior boundary."""
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    min_d_sq = float("inf")
    rings = []
    if gtype == "Polygon":
        rings.append(coords[0])
    elif gtype == "MultiPolygon":
        for poly in coords:
            rings.append(poly[0])
    for ring in rings:
        n = len(ring)
        for i in range(n - 1):
            d_sq = point_to_segment_dist_sq(lon, lat, ring[i][0], ring[i][1], ring[i + 1][0], ring[i + 1][1])
            if d_sq < min_d_sq:
                min_d_sq = d_sq
    return math.sqrt(min_d_sq)


# ---------------------------------------------------------------------------
# County assignment
# ---------------------------------------------------------------------------

def assign_county(
    lat: float | None,
    lng: float | None,
    features: list[dict],
) -> tuple[str | None, str]:
    """Assign lat/lng to a Kenya county using contains with nearest fallback.

    Explicitly avoids truthiness checks for coordinates (GEMINI.md Rule 5).
    """
    if lat is None or lng is None:
        return None, "missing_coordinates"

    # Step 1: Exact contains test
    matches = []
    for feat in features:
        if point_in_geometry(lng, lat, feat["geometry"]):
            matches.append(feat["properties"]["county"])

    if len(matches) == 1:
        return matches[0], "contains"
    if len(matches) > 1:
        return matches[0], "contains"

    # Step 2: Nearest boundary fallback for edge/shore stations
    best_county = None
    best_dist = float("inf")
    for feat in features:
        d = min_dist_to_geometry(lng, lat, feat["geometry"])
        if d < best_dist:
            best_dist = d
            best_county = feat["properties"]["county"]

    if best_dist <= FALLBACK_DISTANCE_DEG:
        return best_county, "nearest_fallback"

    return None, "no_match_too_far"


# ---------------------------------------------------------------------------
# Aggregators
# ---------------------------------------------------------------------------

def build_county_summary(stations: list[dict], all_counties: list[str]) -> list[dict]:
    """Produce county_summary.json rows for all 47 Kenya counties."""
    by_county = {
        name: {
            "county": name,
            "fast_charger_count": 0,
            "swap_station_count": 0,
            "total_count": 0,
        }
        for name in all_counties
    }
    for st in stations:
        county = st.get("county")
        if not county or county not in by_county:
            continue
        row = by_county[county]
        stype = st.get("station_type", "")
        if stype == "fast_charger":
            row["fast_charger_count"] += 1
        elif stype == "swap_station":
            row["swap_station_count"] += 1
        row["total_count"] += 1

    return sorted(by_county.values(), key=lambda r: r["county"])


def build_town_summary(stations: list[dict]) -> list[dict]:
    """Produce town_summary.json rows grouped by (county, town)."""
    by_key = {}
    coords_acc = {}

    for st in stations:
        county = st.get("county") or ""
        town = st.get("town") or ""
        if not county or not town:
            continue
        key = (county, town)
        if key not in by_key:
            by_key[key] = {
                "town": town,
                "county": county,
                "fast_charger_count": 0,
                "swap_station_count": 0,
                "total_count": 0,
                "latitude": 0.0,
                "longitude": 0.0,
            }
            coords_acc[key] = [0.0, 0.0, 0]

        row = by_key[key]
        stype = st.get("station_type", "")
        if stype == "fast_charger":
            row["fast_charger_count"] += 1
        elif stype == "swap_station":
            row["swap_station_count"] += 1
        row["total_count"] += 1

        lat = st.get("final_latitude")
        lng = st.get("final_longitude")
        if lat is not None and lng is not None:
            coords_acc[key][0] += lat
            coords_acc[key][1] += lng
            coords_acc[key][2] += 1

    rows = []
    for key, row in by_key.items():
        lat_sum, lng_sum, count = coords_acc[key]
        if count > 0:
            row["latitude"] = round(lat_sum / count, 7)
            row["longitude"] = round(lng_sum / count, 7)
        rows.append(row)

    rows.sort(key=lambda r: (r["county"], r["town"]))
    return rows


# ---------------------------------------------------------------------------
# File I/O & Manifest
# ---------------------------------------------------------------------------

def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def sha256_of_file(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def update_manifest(target_dir: Path, station_count: int) -> None:
    manifest_path = target_dir / "manifest.json"
    files = {}
    for filename in [
        "county_summary.json",
        "town_summary.json",
        "stations_with_county.json",
        "kenya_counties_display.geojson",
    ]:
        fpath = target_dir / filename
        if fpath.exists():
            bytes_len, sha = sha256_of_file(fpath)
            files[filename] = {"bytes": bytes_len, "sha256": sha}

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "pipeline_run_id": "merged_zeno_arcride_spiro",
        "source_name": "zeno_arcride_spiro",
        "station_count": station_count,
        "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "files": files,
    }
    write_json(manifest_path, manifest)


# ---------------------------------------------------------------------------
# Main Merge Pipeline
# ---------------------------------------------------------------------------

def main() -> int:
    print("=== Merging ARC Ride & Spiro swap stations into Kenya Public Dataset ===")

    # 1. Load GeoJSON county polygons
    if not GEOJSON_PATH.exists():
        print(f"FAIL: GeoJSON not found at {GEOJSON_PATH}")
        return 1
    geojson = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
    features = geojson["features"]
    all_counties = sorted(f["properties"]["county"] for f in features)
    print(f"Loaded {len(all_counties)} county polygons from {GEOJSON_PATH.name}")

    # 2. Load Zeno stations (extract only Zeno stations if previously merged)
    zeno_raw = json.loads(ZENO_STATIONS_PATH.read_text(encoding="utf-8"))
    zeno_stations = [s for s in zeno_raw if s.get("operator") not in ("ARC Ride", "Spiro")]
    print(f"Loaded {len(zeno_stations)} pristine Zeno Moto stations")

    zeno_out = []
    for s in zeno_stations:
        rec = dict(s)
        rec["operator"] = ZENO_OPERATOR
        zeno_out.append(rec)

    # 3. Load ARC Ride & Spiro swap stations
    if not SWAP_STATIONS_PATH.exists():
        print(f"FAIL: Swap stations not found at {SWAP_STATIONS_PATH}")
        return 1
    swap_raw = json.loads(SWAP_STATIONS_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(swap_raw)} swap stations from {SWAP_STATIONS_PATH.name}")

    swap_out = []
    assigned_count = 0
    fallback_count = 0

    for s in swap_raw:
        lat = s.get("final_latitude")
        lng = s.get("final_longitude")
        county_name, match_method = assign_county(lat, lng, features)

        if county_name:
            assigned_count += 1
            if match_method == "nearest_fallback":
                fallback_count += 1
        else:
            print(f"WARNING: No county match for {s.get('operator')} - {s.get('name')} at ({lat}, {lng})")

        rec = {
            "station_id": s["station_id"],
            "name": s["name"],
            "operator": s.get("operator", ""),
            "station_type": "swap_station",
            "locality": s.get("locality"),
            "landmark": s.get("landmark"),
            "address_raw": s.get("address") or s.get("address_raw"),
            "phone": None,
            "hours": None,
            "source_maps_url": None,
            "source_name": s.get("source"),
            "retrieved_at": s.get("retrieved_on"),
            "final_latitude": lat,
            "final_longitude": lng,
            "latitude": lat,
            "longitude": lng,
            "county": county_name,
            "county_match_method": match_method,
            "matched_counties": None,
            "town": None,
            "town_source": None,
            "cabinet_count": s.get("cabinet_count"),
            "retrieved_on": s.get("retrieved_on"),
            "last_verified": s.get("last_verified"),
        }
        swap_out.append(rec)

    print(f"Assigned {assigned_count}/{len(swap_raw)} swap stations to counties ({fallback_count} via fallback)")

    # 4. Merge stations
    merged_stations = zeno_out + swap_out
    total_count = len(merged_stations)
    print(f"Total merged stations: {total_count} (Zeno: {len(zeno_out)}, ARC Ride: {sum(1 for s in swap_out if s['operator'] == 'ARC Ride')}, Spiro: {sum(1 for s in swap_out if s['operator'] == 'Spiro')})")

    # 5. Build county and town summaries
    county_summary_data = build_county_summary(merged_stations, all_counties)
    town_summary_data = build_town_summary(merged_stations)

    # 6. Write outputs to data/public/kenya/
    write_json(KENYA_DIR / "stations_with_county.json", merged_stations)
    write_json(KENYA_DIR / "county_summary.json", county_summary_data)
    write_json(KENYA_DIR / "town_summary.json", town_summary_data)
    update_manifest(KENYA_DIR, total_count)
    print(f"Updated all dataset files in {KENYA_DIR}")

    # 7. Mirror outputs to app/static/data/public/kenya/
    APP_KENYA = APP_STATIC_PUBLIC / "kenya"
    write_json(APP_KENYA / "stations_with_county.json", merged_stations)
    write_json(APP_KENYA / "county_summary.json", county_summary_data)
    write_json(APP_KENYA / "town_summary.json", town_summary_data)
    # Ensure geojson is also in app/static
    if (KENYA_DIR / "kenya_counties_display.geojson").exists():
        (APP_KENYA / "kenya_counties_display.geojson").write_bytes(
            (KENYA_DIR / "kenya_counties_display.geojson").read_bytes()
        )
    update_manifest(APP_KENYA, total_count)
    print(f"Mirrored all dataset files to {APP_KENYA}")

    # 8. Update regions.json in both locations: remove kenya_swap
    regions_config = {
        "regions": [
            {
                "region_id": "kenya",
                "display_name": "Kenya",
                "cluster": "east_africa",
                "bounds": [
                    [-5.0, 33.5],
                    [5.6, 42.2],
                ],
                "tiers": ["county", "town"],
                "station_types": ["fast_charger", "swap_station"],
                "data_path": "kenya",
                "dataset_source": "zeno_arcride_spiro",
                "dataset_source_label": "Zeno, ARC Ride & Spiro",
            },
            {
                "region_id": "taipei",
                "display_name": "Taipei",
                "cluster": "east_asia",
                "bounds": [
                    [24.96, 121.45],
                    [25.21, 121.67],
                ],
                "tiers": ["district"],
                "station_types": ["standard_charger", "swap_station"],
                "data_path": "taipei",
                "dataset_source": "taipei_open_data",
                "dataset_source_label": "Taipei Open Data",
            },
        ]
    }
    write_json(DATA_PUBLIC / "regions.json", regions_config)
    write_json(APP_STATIC_PUBLIC / "regions.json", regions_config)
    print("Updated regions.json in data/public and app/static/data/public (kenya_swap removed).")

    print("=== Merge complete successfully ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
