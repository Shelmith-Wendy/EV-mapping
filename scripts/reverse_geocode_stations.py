#!/usr/bin/env python3
"""Reverse-geocode station coordinates to derive a town label via Nominatim."""

from __future__ import annotations

import re
import sys
from collections import Counter
from functools import lru_cache

from geopy.exc import GeocoderTimedOut

from common import (
    ROOT,
    STATIONS_WITH_COUNTY,
    TOWNS_REVERSE_GEOCODED,
    data_dir,
    nominatim_reverse,
    read_json,
    write_json,
)

PROCESSED = data_dir()
STATIONS_PATH = PROCESSED / STATIONS_WITH_COUNTY
CACHE_PATH = PROCESSED / TOWNS_REVERSE_GEOCODED
NAIROBI_COUNTY = "Nairobi"

# --- Town label normalization (formerly town_names.py) ---

ADMIN_MARKERS = ("ward", "location", "division", "sublocation", "township")
ADMIN_SUFFIXES = (" sublocation", " location", " division", " ward", " township", " town")
KEEP_UPPER = {"CBD"}
NEW_PREFIX = re.compile(r"^new\s+", re.I)
OVERRIDES_PATH = ROOT / "data" / "raw" / "town_overrides.json"


@lru_cache(maxsize=1)
def load_overrides() -> tuple[dict[str, str], dict[str, str]]:
    if not OVERRIDES_PATH.exists():
        return {}, {}
    data = read_json(OVERRIDES_PATH)
    aliases = {str(k): str(v) for k, v in data.get("aliases", {}).items()}
    stations = {str(k): str(v) for k, v in data.get("stations", {}).items()}
    return aliases, stations


def title_words(name: str) -> str:
    upper = name.upper()
    if upper in KEEP_UPPER:
        return upper
    return " ".join(word[:1].upper() + word[1:] for word in name.split())


def strip_admin_suffixes(name: str) -> str:
    cleaned = name.strip()
    changed = True
    while changed:
        changed = False
        for suffix in ADMIN_SUFFIXES:
            if cleaned.lower().endswith(suffix):
                cleaned = cleaned[: -len(suffix)].strip()
                changed = True
    return cleaned


def is_admin_label(name: str) -> bool:
    lower = name.lower()
    return any(marker in lower for marker in ADMIN_MARKERS)


def clean_town(name: str | None, county: str | None = None, station_id: str | None = None) -> str | None:
    if not name:
        return None

    aliases, station_overrides = load_overrides()
    if station_id and station_id in station_overrides:
        return station_overrides[station_id]

    cleaned = name.strip()
    if "/" in cleaned:
        cleaned = cleaned.split("/", 1)[0].strip()
    cleaned = NEW_PREFIX.sub("", cleaned)
    cleaned = strip_admin_suffixes(cleaned)
    cleaned = title_words(cleaned)
    if cleaned in aliases:
        cleaned = aliases[cleaned]
    return cleaned or name.strip()


# --- Reverse geocode ---

def town_from_display(display_name: str | None, county: str) -> str | None:
    if not display_name:
        return None
    for part in display_name.split(","):
        candidate = clean_town(part.strip(), county)
        if not candidate or candidate == county:
            continue
        if candidate.endswith("County") or candidate == "Kenya":
            continue
        if not is_admin_label(candidate):
            return candidate
    return None


def pick_town(address: dict, county: str, display_name: str | None) -> str | None:
    if not address:
        return None
    if county == NAIROBI_COUNTY:
        keys = ("suburb", "neighbourhood", "quarter", "town", "city", "municipality")
    else:
        keys = ("town", "city", "municipality", "village", "suburb")
    for key in keys:
        raw = address.get(key)
        if not raw:
            continue
        candidate = clean_town(strip_admin_suffixes(raw), county)
        if candidate and not is_admin_label(candidate):
            return candidate
    return town_from_display(display_name, county)


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
    sources = Counter(row["town_source"] for row in stations)
    zeno_diff = sum(1 for row in stations if row["town"] != row["locality"])
    print(f"town_source: {dict(sources)}")
    print(f"town differs from zeno locality: {zeno_diff}")
    print("top 5 towns:")
    counts = Counter(row["town"] for row in stations)
    for town, n in counts.most_common(5):
        print(f"  {town}: {n}")


def apply_clean_towns(stations: list[dict], cache: dict[str, dict]) -> None:
    for station in stations:
        station["town"] = clean_town(
            station.get("town") or station.get("locality"),
            station.get("county"),
            station.get("station_id"),
        )
    for row in cache.values():
        row["town"] = clean_town(row.get("town"), row.get("county"))


def main() -> int:
    stations = read_json(STATIONS_PATH)
    cache = load_cache()

    reverse = nominatim_reverse()

    for station in stations:
        lat = float(station["final_latitude"])
        lng = float(station["final_longitude"])
        key = coord_key(lat, lng)
        county = station["county"]

        if key in cache:
            cached = cache[key]
            station["town"] = clean_town(cached["town"], county, station.get("station_id"))
            station["town_source"] = cached["town_source"]
            continue

        location = reverse_lookup(reverse, lat, lng)
        address = location.raw.get("address", {}) if location else {}
        display_name = location.address if location else None
        town = pick_town(address, county, display_name)
        if town:
            town_source = "reverse_geocode"
        else:
            town = station["locality"]
            town_source = "zeno_fallback"

        town = clean_town(town, county, station.get("station_id"))
        station["town"] = town
        station["town_source"] = town_source
        cache[key] = {
            "coord_key": key,
            "latitude": lat,
            "longitude": lng,
            "county": county,
            "town": town,
            "town_source": town_source,
            "display_name": display_name,
        }

    apply_clean_towns(stations, cache)
    write_outputs(stations, cache)
    print_summary(stations)
    return 0


if __name__ == "__main__":
    sys.exit(main())
