#!/usr/bin/env python3
"""Publish the TVS placement sheet into the active data directory.

Reads data/raw/taipei/ev_optimal_locations_taipei.csv and the region's
boundary file (staging during a pipeline run) and writes tvs_candidates.json
beside the other Taipei outputs. A missing sheet fails the run.

If data/raw/taipei/tvs_road_distances.json exists and its input_hash matches
the current candidate list, road-distance fields and polyline6 routes are
merged into each record. Where no distance was computed (e.g. the site was
unreachable), the field is omitted. On a hash mismatch the file is ignored
and a warning is printed. No network calls are made here.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys

from shapely.geometry import Point, shape

from common import COUNTIES_GEOJSON, ROOT, TVS_CANDIDATES, data_dir, read_json, write_json

CSV_PATH = ROOT / "data" / "raw" / "taipei" / "ev_optimal_locations_taipei.csv"
ROAD_DISTANCES_PATH = ROOT / "data" / "raw" / "taipei" / "tvs_road_distances.json"


def station_id(latitude: float, longitude: float) -> str:
    return f"tvs_{latitude:.6f}_{longitude:.6f}"


def load_districts(path) -> list[tuple[str, object]]:
    collection = read_json(path)
    units = []
    for feature in collection["features"]:
        geom = shape(feature["geometry"])
        if geom.geom_type == "GeometryCollection":
            geom = geom.geoms[0]
        units.append((feature["properties"]["district"], geom))
    return units


def district_for(latitude: float, longitude: float, units: list[tuple[str, object]]) -> str | None:
    point = Point(longitude, latitude)
    matched = [name for name, geom in units if geom.covers(point)]
    if len(matched) == 1:
        return matched[0]
    return None


def _candidate_hash(records: list[dict]) -> str:
    """Stable SHA-256 of all candidate ids and coordinates, matching compute_tvs_road_distances.py."""
    payload = json.dumps(
        [
            {
                "station_id": s["station_id"],
                "final_latitude": s["final_latitude"],
                "final_longitude": s["final_longitude"],
            }
            for s in sorted(records, key=lambda s: s["station_id"])
        ],
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def load_road_distances(records: list[dict]) -> dict[str, dict]:
    """Load tvs_road_distances.json and return the per-site dict if the hash matches."""
    if not ROAD_DISTANCES_PATH.is_file():
        return {}
    try:
        raw = json.loads(ROAD_DISTANCES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"warning: could not read {ROAD_DISTANCES_PATH}: {exc}")
        return {}

    saved_hash = raw.get("_meta", {}).get("input_hash", "")
    current_hash = _candidate_hash(records)
    if saved_hash != current_hash:
        print(
            f"warning: {ROAD_DISTANCES_PATH.name} hash mismatch "
            f"(saved={saved_hash[:12]}… current={current_hash[:12]}…) — "
            "road distances are stale and will be omitted. "
            "Re-run compute_tvs_road_distances.py."
        )
        return {}

    print(f"road distances: hash matched ({current_hash[:12]}…), merging.")
    return raw.get("sites", {})


def load_rows(units: list[tuple[str, object]]) -> list[dict]:
    text = CSV_PATH.read_text(encoding="utf-8-sig")
    records = []
    seen = set()
    for row in csv.DictReader(text.splitlines()):
        latitude = float(row["latitude"])
        longitude = float(row["longitude"])
        candidate_id = station_id(latitude, longitude)
        if candidate_id in seen:
            raise SystemExit(f"duplicate candidate id {candidate_id}")
        seen.add(candidate_id)
        locality = (row.get("locality") or "").strip()
        records.append({
            "station_id": candidate_id,
            "name": locality or "TVS",
            "station_type": "swap_station",
            "operator": "TVS",
            "status": "candidate",
            "locality": locality,
            "latitude": latitude,
            "longitude": longitude,
            "final_latitude": latitude,
            "final_longitude": longitude,
            "district": district_for(latitude, longitude, units),
            "maps_navigation_url": (
                "https://www.google.com/maps/search/?api=1&query="
                f"{latitude},{longitude}"
            ),
        })
    return records


def main() -> int:
    if not CSV_PATH.is_file():
        print(f"missing {CSV_PATH}")
        return 1
    districts_path = data_dir() / COUNTIES_GEOJSON
    if not districts_path.is_file():
        print(f"missing boundary file {districts_path}")
        return 1
    out_path = data_dir() / TVS_CANDIDATES
    records = load_rows(load_districts(districts_path))

    road_distances = load_road_distances(records)
    merged = 0
    for record in records:
        dist = road_distances.get(record["station_id"])
        if dist:
            record["nearest_tvs_id"] = dist["nearest_id"]
            record["nearest_tvs_name"] = dist["nearest_name"]
            record["nearest_tvs_road_m"] = dist["road_distance_m"]
            record["nearest_tvs_straight_m"] = dist["straight_line_m"]
            if dist.get("polyline6"):
                record["nearest_tvs_polyline6"] = dist["polyline6"]
            merged += 1

    write_json(out_path, records)
    assigned = sum(1 for row in records if row["district"])
    print(f"districts {districts_path}")
    print(
        f"wrote {len(records)} candidates ({assigned} inside a district, "
        f"{merged} with road distances) to {out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
