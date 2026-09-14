#!/usr/bin/env python3
"""Validate pipeline outputs before promoting staging to public."""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

from common import (
    COUNT_COLS,
    COUNTIES_DISPLAY_GEOJSON,
    COUNTIES_GEOJSON,
    COUNTY_SUMMARY,
    DASHBOARD_FILES,
    KENYA_COUNTY_COUNT,
    LOCALITIES_GEOCODED,
    REQUIRED_FILES,
    STATION_FIELDS,
    STATION_TYPE_VALUES,
    STATIONS_GEOCODED,
    STATIONS_STANDARD,
    STATIONS_WITH_COUNTY,
    TOWN_SUMMARY,
    data_dir,
    read_json,
)


def fail(message: str) -> None:
    print(f"FAIL: {message}")


def validate(data: Path) -> bool:
    ok = True

    for name in REQUIRED_FILES:
        if not (data / name).exists():
            fail(f"missing {name}")
            ok = False
    if not ok:
        return False

    standard = read_json(data / STATIONS_STANDARD)
    geocoded = read_json(data / STATIONS_GEOCODED)
    with_county = read_json(data / STATIONS_WITH_COUNTY)
    county_summary = read_json(data / COUNTY_SUMMARY)
    town_summary = read_json(data / TOWN_SUMMARY)
    counties_geo = read_json(data / COUNTIES_GEOJSON)
    display_geo = read_json(data / COUNTIES_DISPLAY_GEOJSON)
    localities = read_json(data / LOCALITIES_GEOCODED)

    if not isinstance(standard, list) or not standard:
        fail(f"{STATIONS_STANDARD} must be a non-empty list")
        ok = False

    station_ids = set()
    for row in standard:
        missing = STATION_FIELDS - row.keys()
        if missing:
            fail(f"{STATIONS_STANDARD} missing fields {sorted(missing)} on {row.get('station_id')}")
            ok = False
        if row.get("station_type") not in STATION_TYPE_VALUES:
            fail(f"unknown station_type {row.get('station_type')!r} on {row.get('station_id')}")
            ok = False
        station_ids.add(row["station_id"])

    if len(station_ids) != len(standard):
        fail(f"duplicate station_id values in {STATIONS_STANDARD}")
        ok = False

    if len(geocoded) != len(standard):
        fail(f"{STATIONS_GEOCODED} count {len(geocoded)} != standard {len(standard)}")
        ok = False

    geocoded_ids = {row["station_id"] for row in geocoded}
    if geocoded_ids != station_ids:
        fail("station_id set mismatch between standard and geocoded")
        ok = False

    for row in geocoded:
        if row.get("final_latitude") is None or row.get("final_longitude") is None:
            fail(f"missing final coordinates for {row.get('station_id')}")
            ok = False
        if not row.get("maps_navigation_url"):
            fail(f"missing maps_navigation_url for {row.get('station_id')}")
            ok = False

    if len(with_county) != len(standard):
        fail(f"{STATIONS_WITH_COUNTY} count {len(with_county)} != standard {len(standard)}")
        ok = False

    for row in with_county:
        if not row.get("county"):
            fail(f"missing county for {row.get('station_id')}")
            ok = False
        if not row.get("maps_navigation_url"):
            fail(f"missing maps_navigation_url for {row.get('station_id')}")
            ok = False
        if not row.get("town"):
            fail(f"missing town for {row.get('station_id')}")
            ok = False

    county_features = counties_geo.get("features", [])
    display_features = display_geo.get("features", [])
    if len(county_features) != KENYA_COUNTY_COUNT:
        fail(f"{COUNTIES_GEOJSON} expected {KENYA_COUNTY_COUNT} features, got {len(county_features)}")
        ok = False
    if len(display_features) != KENYA_COUNTY_COUNT:
        fail(f"{COUNTIES_DISPLAY_GEOJSON} expected {KENYA_COUNTY_COUNT} features, got {len(display_features)}")
        ok = False

    station_total = len(standard)
    county_total = sum(row["total_count"] for row in county_summary)
    town_total = sum(row["total_count"] for row in town_summary)
    if county_total != station_total:
        fail(f"{COUNTY_SUMMARY} total {county_total} != station count {station_total}")
        ok = False
    if town_total != station_total:
        fail(f"{TOWN_SUMMARY} total {town_total} != station count {station_total}")
        ok = False

    for table, label in ((county_summary, COUNTY_SUMMARY), (town_summary, TOWN_SUMMARY)):
        for row in table:
            if sum(row[col] for col in COUNT_COLS[:2]) != row["total_count"]:
                fail(f"{label} row {row} counts do not add up")
                ok = False

    station_town_pairs = Counter((row["town"], row["county"]) for row in with_county)
    summary_town_pairs = {(row["town"], row["county"]): row["total_count"] for row in town_summary}
    if set(station_town_pairs) != set(summary_town_pairs):
        fail(f"{TOWN_SUMMARY} town/county pairs do not match stations")
        ok = False
    for pair, count in station_town_pairs.items():
        if summary_town_pairs.get(pair) != count:
            fail(f"{TOWN_SUMMARY} {pair} count {summary_town_pairs.get(pair)} != stations {count}")
            ok = False

    towns_by_county: dict[str, int] = defaultdict(int)
    for row in town_summary:
        towns_by_county[row["county"]] += row["total_count"]
    for row in county_summary:
        town_sum = towns_by_county.get(row["county"], 0)
        if town_sum != row["total_count"]:
            fail(
                f"towns in {row['county']} sum to {town_sum} != "
                f"{COUNTY_SUMMARY} total {row['total_count']}"
            )
            ok = False

    locality_names = {row["locality"] for row in localities}
    station_localities = {row["locality"] for row in standard}
    missing_localities = station_localities - locality_names
    if missing_localities:
        fail(f"{LOCALITIES_GEOCODED} missing: {sorted(missing_localities)}")
        ok = False

    for name in DASHBOARD_FILES:
        if not (data / name).exists():
            fail(f"dashboard artifact missing: {name}")
            ok = False

    if ok:
        review_count = sum(1 for row in geocoded if row.get("needs_review"))
        town_count = len({row["town"] for row in with_county})
        print(f"PASS: {station_total} stations, {town_count} towns, {review_count} need review")
    return ok


def main() -> int:
    target = data_dir()
    if not target.is_dir():
        fail(f"data directory does not exist: {target}")
        return 1
    return 0 if validate(target) else 1


if __name__ == "__main__":
    sys.exit(main())
