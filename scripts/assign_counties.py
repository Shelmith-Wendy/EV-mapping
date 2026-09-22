#!/usr/bin/env python3
"""Assign each station to the region's primary admin unit via point-in-polygon."""

from __future__ import annotations

import sys
from collections import Counter

from shapely.geometry import Point, shape

from common import (
    COUNTIES_GEOJSON,
    STATIONS_GEOCODED,
    STATIONS_WITH_COUNTY,
    current_region,
    data_dir,
    region_id,
    read_json,
    write_json,
)

PROCESSED = data_dir()
STATIONS_PATH = PROCESSED / STATIONS_GEOCODED
COUNTIES_PATH = PROCESSED / COUNTIES_GEOJSON

NEAREST_FALLBACK_DEG = 0.01


def write_outputs(records: list[dict]) -> None:
    write_json(PROCESSED / STATIONS_WITH_COUNTY, records)


def main() -> int:
    cfg = current_region()
    unit_key = cfg.tiers[0]
    stations = read_json(STATIONS_PATH)
    collection = read_json(COUNTIES_PATH)

    units = []
    for feature in collection["features"]:
        geom = shape(feature["geometry"])
        if geom.geom_type == "GeometryCollection":
            geom = geom.geoms[0]
        units.append((feature["properties"][unit_key], geom))

    records = []
    for station in stations:
        point = Point(station["final_longitude"], station["final_latitude"])
        matched = [name for name, geom in units if geom.contains(point)]

        matched_units = None
        if len(matched) == 1:
            unit_name = matched[0]
            method = "contains"
        elif len(matched) > 1:
            unit_name = None
            method = "ambiguous_multiple"
            matched_units = matched
        else:
            nearest_name, nearest_geom = min(units, key=lambda item: item[1].distance(point))
            nearest_distance = nearest_geom.distance(point)
            if nearest_distance < NEAREST_FALLBACK_DEG:
                unit_name = nearest_name
                method = "nearest_fallback"
            else:
                unit_name = None
                method = "no_match_too_far"

        record = {
            **station,
            unit_key: unit_name,
            f"{unit_key}_match_method": method,
            # Field name kept for Kenya output identity (Phase 1 snapshot).
            "matched_counties": matched_units,
        }
        records.append(record)

    write_outputs(records)

    if region_id() == "kenya":
        rongai_counties = {row["county"] for row in records if row["locality"] == "Rongai"}
        if rongai_counties == {"Nakuru"}:
            print("PASS: Rongai -> Nakuru")
        else:
            print(f"FAIL: Rongai expected Nakuru, got {rongai_counties or 'no rows'}")

        belle_vue_counties = {row["county"] for row in records if row["locality"] == "Belle Vue"}
        if belle_vue_counties == {"Nyeri"}:
            print("PASS: Belle Vue -> Nyeri")
        else:
            print(f"FAIL: Belle Vue expected Nyeri, got {belle_vue_counties or 'no rows'}")

    methods = Counter(row[f"{unit_key}_match_method"] for row in records)
    print(f"by match method: {dict(methods)}")
    assigned = {row[unit_key] for row in records if row[unit_key]}
    empty = [name for name, _ in units if name not in assigned]
    print(f"{unit_key} with zero stations ({len(empty)}): {empty}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
