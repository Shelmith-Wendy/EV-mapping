#!/usr/bin/env python3
"""Geocode unique station localities via Nominatim (best-effort anchor hints)."""

from __future__ import annotations

import sys
from collections import Counter, defaultdict

from geopy.exc import GeocoderTimedOut

from common import (
    LOCALITIES_GEOCODED,
    STATIONS_STANDARD,
    current_region,
    data_dir,
    nominatim_geocode,
    read_json,
    write_json,
)

PROCESSED = data_dir()
STATIONS_PATH = PROCESSED / STATIONS_STANDARD


def pick_landmark(landmarks: list[str]) -> str | None:
    if not landmarks:
        return None
    counts = Counter(landmarks)
    top = max(counts.values())
    return sorted(name for name, n in counts.items() if n == top)[0]


def geocode_query(geocode, query: str, viewbox):
    for _ in (1, 2):
        try:
            return geocode(query, viewbox=viewbox, bounded=True)
        except GeocoderTimedOut:
            continue
    return None


def write_outputs(records: list[dict]) -> None:
    write_json(PROCESSED / LOCALITIES_GEOCODED, records)


def print_summary(records: list[dict], country_hint: str) -> None:
    succeeded = [row for row in records if row["latitude"] is not None]
    failed = [row for row in records if row["latitude"] is None]
    hint_lower = country_hint.casefold()
    missing_country = [
        row for row in records
        if row["source"] == "geocoded"
        and row["display_name"]
        and hint_lower not in row["display_name"].casefold()
    ]

    print(f"total unique localities: {len(records)}")
    print(f"succeeded: {len(succeeded)}")
    print(f"failed: {len(failed)}")

    print("\nGeocoding returned nothing (decode step may bootstrap from Maps URLs):")
    if failed:
        for row in failed:
            print(f"  - {row['locality']} (query: {row['query_used']})")
    else:
        print("  (none)")

    print(f"\ndisplay_name does not contain {country_hint!r}:")
    if missing_country:
        for row in missing_country:
            print(f"  - {row['locality']}: {row['display_name']}")
    else:
        print("  (none)")


def main() -> int:
    cfg = current_region()
    country_hint = cfg.geocode_country_hint
    viewbox = list(cfg.bounds)

    stations = read_json(STATIONS_PATH)
    landmarks_by_locality = defaultdict(list)
    for station in stations:
        if station.get("landmark"):
            landmarks_by_locality[station["locality"]].append(station["landmark"])
    localities = sorted({station["locality"] for station in stations})

    # Sources that already ship coordinates don't need locality anchors.
    if stations and all(
        station.get("latitude") is not None and station.get("longitude") is not None
        for station in stations
    ):
        records = []
        for locality in localities:
            landmark = pick_landmark(landmarks_by_locality[locality])
            records.append({
                "locality": locality,
                "landmark": landmark,
                "query_used": f"{landmark}, {country_hint}" if landmark else f"{locality}, {country_hint}",
                "latitude": None,
                "longitude": None,
                "display_name": None,
                "source": "skipped_source_coords",
            })
        write_outputs(records)
        print(f"skip Nominatim: all {len(stations)} stations already have coordinates")
        print_summary(records, country_hint)
        return 0

    previous_path = PROCESSED / LOCALITIES_GEOCODED
    previous = {}
    if previous_path.exists():
        for row in read_json(previous_path):
            if row.get("latitude") is not None and row.get("source") in {"geocoded", "maps_url"}:
                previous[row["locality"]] = row

    geocode = nominatim_geocode()

    records = []
    for locality in localities:
        landmark = pick_landmark(landmarks_by_locality[locality])
        query = f"{landmark}, {country_hint}" if landmark else f"{locality}, {country_hint}"
        if locality in previous and previous[locality]["query_used"] == query:
            records.append(previous[locality])
            continue
        if locality in previous and previous[locality].get("source") == "maps_url":
            records.append(previous[locality])
            continue

        location = geocode_query(geocode, query, viewbox)
        records.append({
            "locality": locality,
            "landmark": landmark,
            "query_used": query,
            "latitude": location.latitude if location else None,
            "longitude": location.longitude if location else None,
            "display_name": location.address if location else None,
            "source": "geocoded",
        })

    write_outputs(records)
    print_summary(records, country_hint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
