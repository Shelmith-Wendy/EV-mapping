"""Spiro adapter: parses saved browser JSON snapshots with zero network calls."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from scripts.ingestion.core import (
    IngestionReport,
    is_in_kenya_box,
    parse_coordinates,
)

SPIRO_SOURCE_LABEL = "spironet.com map payload (saved from browser)"


def adapt_spiro(
    file_path_or_payload: Path | str | dict[str, Any],
    retrieved_on: str | None = None,
    report: IngestionReport | None = None,
) -> list[dict[str, Any]]:
    """Transform saved Spiro browser payload into normalized station records.

    Rules & Constraints:
    - Input is saved JSON file or parsed payload only (no network code allowed per Rule 2).
    - Keeps markers where categoryId is 'swap-station' in cities whose country is 'KE'.
    - Resolves country code via the payload's 'cities' list.
    - Parses coordinates and drops invalid or outside-Kenya points.
    - Removes exact duplicates only based on (name, lat_6dp, lng_6dp); does NOT merge nearby stations.
    - cabinet_count is strictly empty string "" (never 0 or 1).
    - Operator is 'Spiro', source is browser snapshot description.
    - retrieved_on is derived from file modification date unless explicitly supplied.
    """
    if report is None:
        report = IngestionReport()

    file_path: Path | None = None
    if isinstance(file_path_or_payload, (str, Path)):
        file_path = Path(file_path_or_payload)
        if not file_path.is_file():
            raise FileNotFoundError(f"Spiro payload file not found: {file_path}")
        with open(file_path, "r", encoding="utf-8-sig") as f:
            payload = json.load(f)
    elif isinstance(file_path_or_payload, dict):
        payload = file_path_or_payload
    else:
        raise ValueError(f"Expected file path or dict for Spiro payload, got {type(file_path_or_payload).__name__}")

    # Determine retrieved_on date
    if retrieved_on:
        snapshot_date = retrieved_on
    elif file_path is not None:
        snapshot_date = date.fromtimestamp(os.path.getmtime(file_path)).isoformat()
    else:
        snapshot_date = date.today().isoformat()

    cities = payload.get("cities", [])
    city_by_id = {city.get("id"): city for city in cities if isinstance(city, dict)}

    stations: list[dict[str, Any]] = []
    seen_station_keys: set[tuple[str | None, float, float]] = set()

    markers = payload.get("markers", [])
    for marker in markers:
        if not isinstance(marker, dict):
            report.record_dropped("malformed_marker")
            continue

        city = city_by_id.get(marker.get("cityId"), {})
        if marker.get("categoryId") != "swap-station" or city.get("country") != "KE":
            report.record_dropped("ignored_category_or_country")
            continue

        coords = parse_coordinates(marker.get("coordinates"))
        if coords is None:
            report.record_dropped("invalid_coordinates")
            continue

        latitude, longitude = coords
        if not is_in_kenya_box(latitude, longitude):
            report.record_dropped("outside_kenya")
            marker_name = (marker.get("name") or "").strip()
            report.record_outside_kenya(marker_name, latitude, longitude)
            continue

        # Exact duplicate detection: (raw name, lat rounded to 6dp, lng rounded to 6dp)
        station_key = (marker.get("name"), round(latitude, 6), round(longitude, 6))
        if station_key in seen_station_keys:
            report.record_dropped("exact_duplicate")
            continue

        seen_station_keys.add(station_key)

        station_record = {
            "operator": "Spiro",
            "name": (marker.get("name") or "").strip(),
            "address": (marker.get("address") or "").strip(),
            "latitude": latitude,
            "longitude": longitude,
            "cabinet_count": "",  # Spiro cabinet_count stays strictly blank per Rule
            "source": SPIRO_SOURCE_LABEL,
            "retrieved_on": snapshot_date,
        }
        stations.append(station_record)
        report.record_kept()

    return stations
