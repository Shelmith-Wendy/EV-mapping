#!/usr/bin/env python3
"""Validate pipeline outputs before promoting staging to public."""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

from common import (
    COUNTIES_DISPLAY_GEOJSON,
    COUNTIES_GEOJSON,
    LOCALITIES_GEOCODED,
    REQUIRED_STATION_FIELDS,
    STATION_TYPE_VALUES,
    STATIONS_GEOCODED,
    STATIONS_STANDARD,
    STATIONS_WITH_COUNTY,
    current_region,
    dashboard_files_for,
    data_dir,
    read_json,
    required_files_for,
)


def fail(message: str) -> None:
    print(f"FAIL: {message}")


def validate(data: Path) -> bool:
    cfg = current_region()
    tiers = cfg.tiers
    primary_tier = tiers[0]
    ok = True

    for name in required_files_for(cfg):
        if not (data / name).exists():
            fail(f"missing {name}")
            ok = False
    if not ok:
        return False

    standard = read_json(data / STATIONS_STANDARD)
    geocoded = read_json(data / STATIONS_GEOCODED)
    with_county = read_json(data / STATIONS_WITH_COUNTY)
    counties_geo = read_json(data / COUNTIES_GEOJSON)
    display_geo = read_json(data / COUNTIES_DISPLAY_GEOJSON)
    localities = read_json(data / LOCALITIES_GEOCODED)

    tier_summaries = {}
    for tier in tiers:
        path = data / f"{tier}_summary.json"
        if not path.exists():
            fail(f"missing {tier}_summary.json")
            ok = False
        else:
            tier_summaries[tier] = read_json(path)
    if not ok:
        return False

    if not isinstance(standard, list) or not standard:
        fail(f"{STATIONS_STANDARD} must be a non-empty list")
        ok = False

    station_ids = set()
    for row in standard:
        missing = REQUIRED_STATION_FIELDS - row.keys()
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
        for tier in tiers:
            if not row.get(tier):
                fail(f"missing {tier} for {row.get('station_id')}")
                ok = False
        if not row.get("maps_navigation_url"):
            fail(f"missing maps_navigation_url for {row.get('station_id')}")
            ok = False

    county_features = counties_geo.get("features", [])
    display_features = display_geo.get("features", [])
    expected_units = cfg.expected_unit_count
    if len(county_features) != expected_units:
        fail(f"{COUNTIES_GEOJSON} expected {expected_units} features, got {len(county_features)}")
        ok = False
    if len(display_features) != expected_units:
        fail(f"{COUNTIES_DISPLAY_GEOJSON} expected {expected_units} features, got {len(display_features)}")
        ok = False

    station_total = len(standard)
    for tier, summary in tier_summaries.items():
        tier_total = sum(row["total_count"] for row in summary)
        if tier_total != station_total:
            fail(f"{tier}_summary.json total {tier_total} != station count {station_total}")
            ok = False
        for row in summary:
            type_cols = [key for key in row if key.endswith("_count") and key != "total_count"]
            if sum(row[col] for col in type_cols) != row["total_count"]:
                fail(f"{tier}_summary.json row {row} counts do not add up")
                ok = False

    if len(tiers) >= 2:
        secondary = tiers[1]
        station_pairs = Counter(
            tuple(row[t] for t in reversed(tiers[:2])) for row in with_county
        )
        summary_pairs = {
            tuple(row[t] for t in reversed(tiers[:2])): row["total_count"]
            for row in tier_summaries[secondary]
        }
        if set(station_pairs) != set(summary_pairs):
            fail(f"{secondary}_summary.json {secondary}/{primary_tier} pairs do not match stations")
            ok = False
        for pair, count in station_pairs.items():
            if summary_pairs.get(pair) != count:
                fail(
                    f"{secondary}_summary.json {pair} count "
                    f"{summary_pairs.get(pair)} != stations {count}"
                )
                ok = False

        by_primary: dict[str, int] = defaultdict(int)
        for row in tier_summaries[secondary]:
            by_primary[row[primary_tier]] += row["total_count"]
        for row in tier_summaries[primary_tier]:
            child_sum = by_primary.get(row[primary_tier], 0)
            if child_sum != row["total_count"]:
                fail(
                    f"{secondary}s in {row[primary_tier]} sum to {child_sum} != "
                    f"{primary_tier}_summary.json total {row['total_count']}"
                )
                ok = False

    locality_names = {row["locality"] for row in localities}
    station_localities = {row["locality"] for row in standard}
    missing_localities = station_localities - locality_names
    if missing_localities:
        fail(f"{LOCALITIES_GEOCODED} missing: {sorted(missing_localities)}")
        ok = False

    for name in dashboard_files_for(cfg):
        if not (data / name).exists():
            fail(f"dashboard artifact missing: {name}")
            ok = False

    if ok:
        review_count = sum(1 for row in geocoded if row.get("needs_review"))
        unit_count = len({row[primary_tier] for row in with_county})
        print(f"PASS: {station_total} stations, {unit_count} {primary_tier}, {review_count} need review")
    return ok


def main() -> int:
    target = data_dir()
    if not target.is_dir():
        fail(f"data directory does not exist: {target}")
        return 1
    return 0 if validate(target) else 1


if __name__ == "__main__":
    sys.exit(main())
