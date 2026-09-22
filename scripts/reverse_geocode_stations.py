#!/usr/bin/env python3
"""Reverse-geocode station coordinates to derive a town label via Nominatim.

Skips entirely when the region has no town tier (e.g. Taipei's district-only config).
"""

from __future__ import annotations

import sys
from collections import Counter

from geopy.exc import GeocoderTimedOut

from common import (
    STATIONS_WITH_COUNTY,
    TOWNS_REVERSE_GEOCODED,
    current_region,
    data_dir,
    nominatim_reverse,
    read_json,
    write_json,
)
from regions.kenya import clean_town, pick_town

PROCESSED = data_dir()
STATIONS_PATH = PROCESSED / STATIONS_WITH_COUNTY
CACHE_PATH = PROCESSED / TOWNS_REVERSE_GEOCODED


def coord_key(lat: float, lng: float) -> str:
    return f"{round(lat, 5)},{round(lng, 5)}"


def load_cache() -> dict[str, dict]:
    if not CACHE_PATH.exists():
        return {}
    return {row["coord_key"]: row for row in read_json(CACHE_PATH)}


def reverse_lookup(reverse, lat: float, lng: float):
    for _ in (1, 2):
        try:
            return reverse((lat, lng), exactly_one=True, language="en")
        except GeocoderTimedOut:
            continue
    return None


def write_outputs(stations: list[dict], cache: dict[str, dict]) -> None:
    write_json(STATIONS_PATH, stations)
    cache_rows = sorted(cache.values(), key=lambda row: row["coord_key"])
    write_json(CACHE_PATH, cache_rows)


def print_summary(stations: list[dict]) -> None:
    sources = Counter(row.get("town_source") for row in stations)
    zeno_diff = sum(1 for row in stations if row.get("town") != row.get("locality"))
    print(f"town_source: {dict(sources)}")
    print(f"town differs from zeno locality: {zeno_diff}")
    print("top 5 towns:")
    counts = Counter(row.get("town") for row in stations)
    for town, n in counts.most_common(5):
        print(f"  {town}: {n}")


def apply_clean_towns(stations: list[dict], cache: dict[str, dict], primary_tier: str) -> None:
    for station in stations:
        if station.get("_town_from_source"):
            continue
        station["town"] = clean_town(
            station.get("town") or station.get("locality"),
            station.get(primary_tier),
            station.get("station_id"),
        )
    for row in cache.values():
        row["town"] = clean_town(row.get("town"), row.get(primary_tier) or row.get("county"))


def main() -> int:
    cfg = current_region()
    stations = read_json(STATIONS_PATH)

    if "town" not in cfg.tiers:
        print(f"skip reverse-geocode: region tiers={cfg.tiers} (no town)")
        write_json(STATIONS_PATH, stations)
        write_json(CACHE_PATH, [])
        return 0

    primary_tier = cfg.tiers[0]
    cache = load_cache()
    reverse = nominatim_reverse()
    skipped = 0

    for station in stations:
        existing_town = station.get("town")
        if isinstance(existing_town, str) and existing_town.strip():
            station["town"] = existing_town.strip()
            station["town_source"] = station.get("town_source") or "source"
            station["_town_from_source"] = True
            skipped += 1
            continue

        lat = float(station["final_latitude"])
        lng = float(station["final_longitude"])
        key = coord_key(lat, lng)
        unit_name = station[primary_tier]

        if key in cache:
            cached = cache[key]
            station["town"] = clean_town(cached["town"], unit_name, station.get("station_id"))
            station["town_source"] = cached["town_source"]
            continue

        location = reverse_lookup(reverse, lat, lng)
        address = location.raw.get("address", {}) if location else {}
        display_name = location.address if location else None
        town = pick_town(address, unit_name, display_name)
        if town:
            town_source = "reverse_geocode"
        else:
            town = station["locality"]
            town_source = "zeno_fallback"

        town = clean_town(town, unit_name, station.get("station_id"))
        station["town"] = town
        station["town_source"] = town_source
        cache[key] = {
            "coord_key": key,
            "latitude": lat,
            "longitude": lng,
            "county": unit_name,
            "town": town,
            "town_source": town_source,
            "display_name": display_name,
        }

    apply_clean_towns(stations, cache, primary_tier)
    for station in stations:
        station.pop("_town_from_source", None)

    write_outputs(stations, cache)
    print(f"skipped (source town): {skipped}")
    print_summary(stations)
    return 0


if __name__ == "__main__":
    sys.exit(main())
