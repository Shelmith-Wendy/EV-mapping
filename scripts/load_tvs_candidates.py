#!/usr/bin/env python3
"""Publish the TVS placement sheet as a dashboard overlay.

Reads data/raw/taipei/ev_optimal_locations_taipei.csv and writes
data/public/taipei/tvs_candidates.json. Re-run when the sheet changes.
Does not touch station counts or district summaries.
"""

from __future__ import annotations

import csv
import sys

from shapely.geometry import Point, shape

from common import ROOT, read_json, write_json

CSV_PATH = ROOT / "data" / "raw" / "taipei" / "ev_optimal_locations_taipei.csv"
DISTRICTS_PATH = ROOT / "data" / "public" / "taipei" / "kenya_counties.geojson"
OUT_PATH = ROOT / "data" / "public" / "taipei" / "tvs_candidates.json"


def station_id(latitude: float, longitude: float) -> str:
    return f"tvs_{latitude:.6f}_{longitude:.6f}"


def load_districts() -> list[tuple[str, object]]:
    collection = read_json(DISTRICTS_PATH)
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
    records = load_rows(load_districts())
    write_json(OUT_PATH, records)
    assigned = sum(1 for row in records if row["district"])
    print(f"wrote {len(records)} candidates ({assigned} inside a district) to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
