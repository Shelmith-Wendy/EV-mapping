#!/usr/bin/env python3
"""Publish the TVS placement sheet into the active data directory.

Reads data/raw/taipei/ev_optimal_locations_taipei.csv and the region's
boundary file (staging during a pipeline run) and writes tvs_candidates.json
beside the other Taipei outputs. A missing sheet fails the run.
"""

from __future__ import annotations

import csv
import sys

from shapely.geometry import Point, shape

from common import COUNTIES_GEOJSON, ROOT, TVS_CANDIDATES, data_dir, read_json, write_json

CSV_PATH = ROOT / "data" / "raw" / "taipei" / "ev_optimal_locations_taipei.csv"


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
    write_json(out_path, records)
    assigned = sum(1 for row in records if row["district"])
    print(f"districts {districts_path}")
    print(f"wrote {len(records)} candidates ({assigned} inside a district) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
