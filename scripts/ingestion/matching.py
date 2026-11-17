"""Spatial matching, stable ID generation, registry lifecycle tracking, and co-location detection."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.ingestion.config import (
    DEFAULT_COLOCATION_DISTANCE_METERS,
    DEFAULT_MERGE_DISTANCE_METERS,
    DEFAULT_MOVED_DISTANCE_METERS,
    IngestionConfig,
    get_operator_prefix,
)
from scripts.ingestion.core import (
    haversine_distance_meters,
    make_comparison_key,
    parse_coordinates,
)


@dataclass
class RegistryDiff:
    """Detailed diff of station changes between ingestion runs."""

    new_stations: list[dict[str, Any]] = field(default_factory=list)
    moved_stations: list[dict[str, Any]] = field(default_factory=list)
    removed_stations: list[dict[str, Any]] = field(default_factory=list)
    unchanged_stations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def new_count(self) -> int:
        return len(self.new_stations)

    @property
    def moved_count(self) -> int:
        return len(self.moved_stations)

    @property
    def removed_count(self) -> int:
        return len(self.removed_stations)

    @property
    def unchanged_count(self) -> int:
        return len(self.unchanged_stations)


def canonical_station_sort_key(site: dict[str, Any]) -> tuple:
    """Deterministic tie-breaking key for station ordering across all runs.

    Sorts by:
    1. Rounded latitude (6 decimal places)
    2. Rounded longitude (6 decimal places)
    3. Normalized station name
    4. Normalized station address
    """
    lat = float(site.get("latitude", 0.0))
    lng = float(site.get("longitude", 0.0))
    name = str(site.get("name", "")).strip().lower()
    address = str(site.get("address", "")).strip().lower()
    return (round(lat, 6), round(lng, 6), name, address)


def generate_stable_id(
    operator: str,
    latitude: float,
    longitude: float,
    existing_ids: set[str] | None = None,
) -> str:
    """Generate a deterministic, human-readable, geodetic stable ID for a new station.

    Collision Rule:
    - Base format: {OP}_{lat_grid}_{lng_grid} with 4-decimal precision (~11m cell).
      Example: SPIRO_01.2353S_036.9333E
    - When two distinct stations fall within the same 4-decimal grid cell (closer than ~11m),
      the collision rule appends a deterministic incremental suffix: '-2', '-3', etc.
    - Incoming candidates are processed in canonical sort order (latitude, longitude, name, address)
      so the assignment of base ID and suffix is strictly deterministic.
    """
    prefix = get_operator_prefix(operator)
    lat_dir = "N" if latitude >= 0.0 else "S"
    lng_dir = "E" if longitude >= 0.0 else "W"
    lat_str = f"{abs(latitude):07.4f}{lat_dir}"
    lng_str = f"{abs(longitude):08.4f}{lng_dir}"

    base_id = f"{prefix}_{lat_str}_{lng_str}"
    if existing_ids is None:
        return base_id

    candidate = base_id
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base_id}-{suffix}"
        suffix += 1

    existing_ids.add(candidate)
    return candidate


def match_and_update_registry(
    incoming_sites: list[dict[str, Any]],
    previous_registry: list[dict[str, Any]] | None,
    run_date: str,
    merge_distance_meters: float = DEFAULT_MERGE_DISTANCE_METERS,
    moved_distance_meters: float = DEFAULT_MOVED_DISTANCE_METERS,
    peer_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], RegistryDiff]:
    """Correlate incoming sites against previous registry snapshot.

    Rules & Guarantees:
    - Distance <= merge_distance_meters: matched as UNCHANGED (preserves station_id & first_seen, updates last_seen).
    - Distance <= moved_distance_meters with matching normalized name key: matched as MOVED.
    - No match found: assigned new stable ID, marked as NEW with first_seen = run_date.
    - Previous active stations missing in current run: marked as REMOVED (is_removed = True).
    - Previous removed stations: preserved in registry.
    """
    diff = RegistryDiff()
    existing_ids: set[str] = set(peer_ids or set())

    # Sort incoming sites deterministically by canonical tie-breaking key
    sorted_incoming = sorted(incoming_sites, key=canonical_station_sort_key)

    if not previous_registry:
        # First run: all incoming stations are new
        new_registry: list[dict[str, Any]] = []
        for site in sorted_incoming:
            station_id = generate_stable_id(
                site["operator"],
                site["latitude"],
                site["longitude"],
                existing_ids=existing_ids,
            )
            updated_site = dict(site)
            updated_site["station_id"] = station_id
            updated_site["first_seen"] = run_date
            updated_site["last_seen"] = run_date
            updated_site["is_removed"] = False
            updated_site["co_located"] = False
            updated_site["co_located_with"] = ""
            updated_site["last_verified"] = site.get("last_verified") or ""
            new_registry.append(updated_site)
            diff.new_stations.append(updated_site)
        return new_registry, diff

    # Populate existing IDs from previous registry
    for prev in previous_registry:
        sid = prev.get("station_id")
        if sid:
            existing_ids.add(sid)

    # Separate previous registry into active candidates and historical removed stations
    unmatched_prev_active: list[dict[str, Any]] = []
    historical_removed: list[dict[str, Any]] = []

    for prev in previous_registry:
        is_rem = str(prev.get("is_removed", "")).strip().lower() in ("true", "1")
        if is_rem:
            historical_removed.append(dict(prev))
        else:
            unmatched_prev_active.append(dict(prev))

    final_registry: list[dict[str, Any]] = []
    unmatched_incoming: list[dict[str, Any]] = []

    # Pass 1: Proximity match within merge distance (<= 30m)
    for inc in sorted_incoming:
        inc_lat = inc["latitude"]
        inc_lng = inc["longitude"]
        inc_name_key = make_comparison_key(inc.get("name"))

        best_idx: int | None = None
        best_score: tuple | None = None

        for idx, prev in enumerate(unmatched_prev_active):
            dist = haversine_distance_meters(
                inc_lat, inc_lng, float(prev["latitude"]), float(prev["longitude"])
            )
            if dist <= merge_distance_meters:
                name_match = (
                    inc_name_key == make_comparison_key(prev.get("name"))
                    if inc_name_key else False
                )
                # Prioritize: exact name match first, then geometric distance, then stable ID tiebreaker
                score = (0 if name_match else 1, dist, str(prev.get("station_id", "")))
                if best_score is None or score < best_score:
                    best_score = score
                    best_idx = idx

        if best_idx is not None:
            matched_prev = unmatched_prev_active.pop(best_idx)
            updated_site = dict(inc)
            prev_id = matched_prev.get("station_id") or generate_stable_id(
                matched_prev["operator"],
                float(matched_prev["latitude"]),
                float(matched_prev["longitude"]),
                existing_ids=existing_ids,
            )
            updated_site["station_id"] = prev_id
            updated_site["first_seen"] = matched_prev.get("first_seen", run_date)
            updated_site["last_seen"] = run_date
            updated_site["is_removed"] = False
            updated_site["co_located"] = False
            updated_site["co_located_with"] = ""
            updated_site["last_verified"] = matched_prev.get("last_verified") or ""
            final_registry.append(updated_site)
            diff.unchanged_stations.append(updated_site)
        else:
            unmatched_incoming.append(inc)

    # Pass 2: Moved station detection (<= moved_distance_meters with matching name)
    still_unmatched_incoming: list[dict[str, Any]] = []
    for inc in unmatched_incoming:
        inc_lat = inc["latitude"]
        inc_lng = inc["longitude"]
        inc_name_key = make_comparison_key(inc.get("name"))

        moved_idx: int | None = None
        min_dist = float("inf")

        for idx, prev in enumerate(unmatched_prev_active):
            prev_name_key = make_comparison_key(prev.get("name"))
            if inc_name_key and inc_name_key == prev_name_key:
                dist = haversine_distance_meters(
                    inc_lat, inc_lng, float(prev["latitude"]), float(prev["longitude"])
                )
                if dist <= moved_distance_meters and dist < min_dist:
                    min_dist = dist
                    moved_idx = idx

        if moved_idx is not None:
            matched_prev = unmatched_prev_active.pop(moved_idx)
            updated_site = dict(inc)
            prev_id = matched_prev.get("station_id") or generate_stable_id(
                matched_prev["operator"],
                float(matched_prev["latitude"]),
                float(matched_prev["longitude"]),
                existing_ids=existing_ids,
            )
            updated_site["station_id"] = prev_id
            updated_site["first_seen"] = matched_prev.get("first_seen", run_date)
            updated_site["last_seen"] = run_date
            updated_site["is_removed"] = False
            updated_site["co_located"] = False
            updated_site["co_located_with"] = ""
            updated_site["last_verified"] = matched_prev.get("last_verified") or ""

            # Track movement metrics
            moved_info = dict(updated_site)
            moved_info["old_latitude"] = float(matched_prev["latitude"])
            moved_info["old_longitude"] = float(matched_prev["longitude"])
            moved_info["distance_moved"] = min_dist

            final_registry.append(updated_site)
            diff.moved_stations.append(moved_info)
        else:
            still_unmatched_incoming.append(inc)

    # Pass 3: Brand new stations
    for inc in sorted(still_unmatched_incoming, key=canonical_station_sort_key):
        station_id = generate_stable_id(
            inc["operator"],
            inc["latitude"],
            inc["longitude"],
            existing_ids=existing_ids,
        )
        new_site = dict(inc)
        new_site["station_id"] = station_id
        new_site["first_seen"] = run_date
        new_site["last_seen"] = run_date
        new_site["is_removed"] = False
        new_site["co_located"] = False
        new_site["co_located_with"] = ""
        new_site["last_verified"] = inc.get("last_verified") or ""
        final_registry.append(new_site)
        diff.new_stations.append(new_site)

    # Pass 4: Previously active stations no longer seen -> marked as REMOVED
    for removed_prev in unmatched_prev_active:
        removed_site = dict(removed_prev)
        if not removed_site.get("station_id"):
            removed_site["station_id"] = generate_stable_id(
                removed_site.get("operator", "ARC Ride"),
                float(removed_site["latitude"]),
                float(removed_site["longitude"]),
                existing_ids=existing_ids,
            )
        if not removed_site.get("first_seen"):
            removed_site["first_seen"] = run_date
        if not removed_site.get("last_seen"):
            removed_site["last_seen"] = run_date
        removed_site["is_removed"] = True
        final_registry.append(removed_site)
        diff.removed_stations.append(removed_site)

    # Pass 5: Re-add historical removed stations
    for hist in historical_removed:
        hist_site = dict(hist)
        if not hist_site.get("station_id"):
            hist_site["station_id"] = generate_stable_id(
                hist_site.get("operator", "ARC Ride"),
                float(hist_site["latitude"]),
                float(hist_site["longitude"]),
                existing_ids=existing_ids,
            )
        if not hist_site.get("first_seen"):
            hist_site["first_seen"] = run_date
        if not hist_site.get("last_seen"):
            hist_site["last_seen"] = run_date
        final_registry.append(hist_site)

    return final_registry, diff


def apply_verification_overlay(
    records: list[dict[str, Any]],
    overlay_path: Path | str | None,
) -> list[dict[str, Any]]:
    """Apply human verification overrides to station records.

    Rules & Guarantees:
    - Ingestion NEVER writes to or modifies the overlay file (opened in read-only mode).
    - If station_id matches an entry in the overlay:
      - verified coordinates take precedence over ingested coordinates.
      - last_verified is set from overlay.
    - If station_id is not in overlay, last_verified remains unverified ("").
    """
    if overlay_path is None:
        return records

    path = Path(overlay_path)
    if not path.is_file():
        return records

    overlay_by_id: dict[str, dict[str, str]] = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = (row.get("station_id") or row.get("id") or "").strip()
            if sid:
                overlay_by_id[sid] = row

    for record in records:
        sid = record.get("station_id")
        if sid and sid in overlay_by_id:
            overlay_entry = overlay_by_id[sid]
            raw_lat = overlay_entry.get("verified_latitude")
            raw_lng = overlay_entry.get("verified_longitude")
            coords = parse_coordinates(raw_lat, raw_lng)
            if coords is not None:
                record["latitude"] = coords[0]
                record["longitude"] = coords[1]
            record["last_verified"] = overlay_entry.get("last_verified", "").strip()
        else:
            if "last_verified" not in record or record["last_verified"] is None:
                record["last_verified"] = ""

    return records


def detect_colocation(
    stations_a: list[dict[str, Any]],
    stations_b: list[dict[str, Any]],
    colocation_distance_meters: float = DEFAULT_COLOCATION_DISTANCE_METERS,
) -> None:
    """Flag co-located stations between two operators within a configurable distance.

    Updates 'co_located' (bool) and 'co_located_with' (station_id) in-place on both lists.
    Reuses the core's haversine_distance_meters.
    """
    active_b = [
        s for s in stations_b
        if not str(s.get("is_removed", "")).strip().lower() in ("true", "1")
    ]
    active_a = [
        s for s in stations_a
        if not str(s.get("is_removed", "")).strip().lower() in ("true", "1")
    ]

    # Evaluate A against B
    for sa in active_a:
        matches = []
        for sb in active_b:
            dist = haversine_distance_meters(
                float(sa["latitude"]), float(sa["longitude"]),
                float(sb["latitude"]), float(sb["longitude"]),
            )
            if dist <= colocation_distance_meters:
                matches.append(sb["station_id"])
        if matches:
            sa["co_located"] = True
            sa["co_located_with"] = ";".join(matches)
        else:
            sa["co_located"] = False
            sa["co_located_with"] = ""

    # Evaluate B against A
    for sb in active_b:
        matches = []
        for sa in active_a:
            dist = haversine_distance_meters(
                float(sb["latitude"]), float(sb["longitude"]),
                float(sa["latitude"]), float(sa["longitude"]),
            )
            if dist <= colocation_distance_meters:
                matches.append(sa["station_id"])
        if matches:
            sb["co_located"] = True
            sb["co_located_with"] = ";".join(matches)
        else:
            sb["co_located"] = False
            sb["co_located_with"] = ""


def evaluate_safety_gates(
    incoming_count: int,
    previous_active_count: int,
    config: IngestionConfig,
    force: bool = False,
) -> tuple[bool, str | None, list[str]]:
    """Evaluate pipeline safety gates based on configured thresholds.

    Returns:
        (is_blocked, block_reason_or_none, list_of_warning_messages)
    """
    warnings: list[str] = []

    # 1. Empty run reject gate
    if incoming_count == 0 and config.empty_run_reject:
        return True, "Gate blocked: incoming dataset contains 0 valid stations.", []

    if previous_active_count > 0:
        drop_count = previous_active_count - incoming_count
        drop_pct = drop_count / float(previous_active_count)

        # 2. Block drops exceeding threshold
        if drop_pct > config.pct_drop_block:
            msg = (
                f"Gate blocked: station drop of {drop_pct:.1%} ({incoming_count} vs {previous_active_count}) "
                f"exceeds allowed threshold of {config.pct_drop_block:.1%}. Use --force to proceed."
            )
            if not force:
                return True, msg, []
            warnings.append(f"FORCE OVERRIDE: {msg}")

        # 3. Warn on changes exceeding threshold
        change_pct = abs(incoming_count - previous_active_count) / float(previous_active_count)
        if change_pct > config.pct_change_warn:
            warnings.append(
                f"Gate warning: Station count changed by {change_pct:.1%} "
                f"(previous: {previous_active_count}, incoming: {incoming_count}, threshold: {config.pct_change_warn:.1%})."
            )

    return False, None, warnings
