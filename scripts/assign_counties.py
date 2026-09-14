#!/usr/bin/env python3
"""Assign each station to a Kenyan county via point-in-polygon."""

from __future__ import annotations

import sys
from collections import Counter

from shapely.geometry import Point, shape

from common import (
    COUNTIES_GEOJSON,
    STATIONS_GEOCODED,
    STATIONS_WITH_COUNTY,
    data_dir,
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
    stations = read_json(STATIONS_PATH)
    collection = read_json(COUNTIES_PATH)

    counties = []
    for feature in collection["features"]:
        geom = shape(feature["geometry"])
        if geom.geom_type == "GeometryCollection":
            geom = geom.geoms[0]
        counties.append((feature["properties"]["county"], geom))

    records = []
    for station in stations:
        point = Point(station["final_longitude"], station["final_latitude"])
        matched = [name for name, geom in counties if geom.contains(point)]

        matched_counties = None
        if len(matched) == 1:
            county = matched[0]
            method = "contains"
        elif len(matched) > 1:
            county = None
            method = "ambiguous_multiple"
            matched_counties = matched
        else:
            nearest_name, nearest_geom = min(counties, key=lambda item: item[1].distance(point))
            nearest_distance = nearest_geom.distance(point)
            if nearest_distance < NEAREST_FALLBACK_DEG:
                county = nearest_name
                method = "nearest_fallback"
            else:
                county = None
                method = "no_match_too_far"

        record = {
            **station,
            "county": county,
            "county_match_method": method,
            "matched_counties": matched_counties,
        }
        records.append(record)

    write_outputs(records)

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

    methods = Counter(row["county_match_method"] for row in records)
    print(f"by match method: {dict(methods)}")
    assigned = {row["county"] for row in records if row["county"]}
    empty = [name for name, _ in counties if name not in assigned]
    print(f"counties with zero stations ({len(empty)}): {empty}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
