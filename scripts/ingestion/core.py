"""Shared core business logic, spatial calculations, normalization, clustering, and atomic I/O.

Contains zero operator-specific code and uses only the Python standard library.
"""

from __future__ import annotations

import csv
import json
import math
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.ingestion.config import (
    ALL_COLUMNS,
    BASE_COLUMNS,
    DEFAULT_MERGE_DISTANCE_METERS,
    FORBIDDEN_IDENTIFIER_COLUMNS,
    KENYA_MAX_LAT,
    KENYA_MAX_LNG,
    KENYA_MIN_LAT,
    KENYA_MIN_LNG,
)

# Mean Earth radius in meters (WGS84 spherical approximation)
EARTH_RADIUS_METERS: float = 6_371_000.0


# --- Coordinate Parsing and Bounds Checking ---

def parse_coordinates(
    raw_lat_or_str: Any,
    raw_lng: Any = None,
) -> tuple[float, float] | None:
    """Parse coordinates from either two arguments or a single 'lat, lng' text string.

    CRITICAL RULE: Zero (0.0) is a valid coordinate on the equator. Truthiness checks
    (such as `if not lat:`) are strictly forbidden.

    Returns:
        tuple[float, float] of (latitude, longitude) or None if invalid/malformed.
    """
    if raw_lng is None:
        if raw_lat_or_str is None:
            return None
        text = str(raw_lat_or_str).strip()
        if "," not in text:
            return None
        parts = text.split(",")
        if len(parts) != 2:
            return None
        lat_part, lng_part = parts[0].strip(), parts[1].strip()
    else:
        if raw_lat_or_str is None or raw_lng is None:
            return None
        lat_part, lng_part = raw_lat_or_str, raw_lng

    try:
        lat = float(lat_part)
        lng = float(lng_part)
    except (ValueError, TypeError):
        return None

    if math.isnan(lat) or math.isnan(lng) or math.isinf(lat) or math.isinf(lng):
        return None

    # WGS84 range validation
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None

    return lat, lng


def is_in_kenya_box(
    latitude: float,
    longitude: float,
    bounds: tuple[float, float, float, float] | None = None,
) -> bool:
    """Check if coordinates fall strictly within Kenya's bounding box.

    Zero (0.0) is a valid latitude on the equator.
    """
    if bounds is not None:
        min_lat, max_lat, min_lng, max_lng = bounds
    else:
        min_lat, max_lat, min_lng, max_lng = (
            KENYA_MIN_LAT,
            KENYA_MAX_LAT,
            KENYA_MIN_LNG,
            KENYA_MAX_LNG,
        )

    return (min_lat <= latitude <= max_lat) and (min_lng <= longitude <= max_lng)


def validate_records(
    records: list[dict[str, Any]],
    operator: str,
    require_registry: bool = False,
) -> None:
    """Validate normalized records against common schema, operator rules, and identifier guards.

    Fails loudly with descriptive error messages.
    """
    if not records:
        raise ValueError(f"Ingestion produced 0 valid stations for operator {operator!r}.")

    columns_to_check = ALL_COLUMNS if require_registry else BASE_COLUMNS

    for idx, row in enumerate(records):
        # 1. Column presence
        for col in columns_to_check:
            if col not in row:
                raise ValueError(f"Row {idx} missing required column {col!r}")

        # 1b. Forbidden identifier columns (GEMINI.md Rule 1)
        forbidden_cols = row.keys() & FORBIDDEN_IDENTIFIER_COLUMNS
        if forbidden_cols:
            raise ValueError(
                f"Row {idx} contains forbidden device identifier column(s): {sorted(forbidden_cols)}"
            )

        # 2. Operator matching
        if row["operator"] != operator:
            raise ValueError(
                f"Row {idx} operator mismatch: expected {operator!r}, found {row['operator']!r}"
            )

        # 3. Name non-empty
        if not str(row["name"]).strip():
            raise ValueError(f"Row {idx} has an empty station name")

        # 4. Coordinates valid and in Kenya
        try:
            lat = float(row["latitude"])
            lng = float(row["longitude"])
        except (ValueError, TypeError) as err:
            raise ValueError(f"Row {idx} has invalid coordinate types: {err}") from err

        if not is_in_kenya_box(lat, lng):
            raise ValueError(f"Row {idx} coordinates ({lat}, {lng}) fall outside Kenya bounds")

        # 5. Cabinet count semantics
        cab_val = row["cabinet_count"]
        if operator == "ARC Ride":
            try:
                cab_count = int(cab_val)
            except (ValueError, TypeError) as err:
                raise ValueError(
                    f"Row {idx} ARC Ride cabinet_count must be an integer, got {cab_val!r}"
                ) from err
            if cab_count < 1:
                raise ValueError(f"Row {idx} ARC Ride cabinet_count must be >= 1, got {cab_count}")
        elif operator == "Spiro":
            if cab_val != "":
                raise ValueError(
                    f"Row {idx} Spiro cabinet_count must be strictly empty string '', got {cab_val!r}"
                )

        # 6. Metadata non-empty
        if not str(row["source"]).strip():
            raise ValueError(f"Row {idx} has an empty source field")
        if not str(row["retrieved_on"]).strip():
            raise ValueError(f"Row {idx} has an empty retrieved_on date")

        # 7. Registry columns if required
        if require_registry:
            if not str(row.get("station_id", "")).strip():
                raise ValueError(f"Row {idx} has an empty station_id")
            if not str(row.get("first_seen", "")).strip():
                raise ValueError(f"Row {idx} has an empty first_seen date")
            if not str(row.get("last_seen", "")).strip():
                raise ValueError(f"Row {idx} has an empty last_seen date")

    # 8. Uniqueness of station_id across registry records
    if require_registry:
        seen_station_ids: set[str] = set()
        for idx, row in enumerate(records):
            sid = str(row.get("station_id", "")).strip()
            if sid in seen_station_ids:
                raise ValueError(f"Duplicate station_id detected in dataset at row {idx}: {sid!r}")
            seen_station_ids.add(sid)


# --- Text Normalization ---

def clean_display_text(text: str | None) -> str:
    """Trim leading/trailing whitespace and collapse internal consecutive spaces to one."""
    if text is None:
        return ""
    return " ".join(str(text).split())


def make_comparison_key(text: str | None) -> str:
    """Create a normalized, case-insensitive comparison key for matching names/addresses."""
    return clean_display_text(text).lower()


def normalize_text(text: str | None) -> tuple[str, str]:
    """Normalize text returning both (display_text, comparison_key)."""
    display = clean_display_text(text)
    return display, display.lower()


# --- Distance Calculations ---

def haversine_distance_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Compute the great-circle distance between two points in meters using the Haversine formula."""
    if lat1 == lat2 and lng1 == lng2:
        return 0.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    )
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_METERS * c


# --- Spatial Site Clustering ---

def _spatial_grid_cell(
    lat: float,
    lng: float,
    cell_size_deg: float,
) -> tuple[int, int]:
    """Compute grid bucket coordinates for spatial indexing."""
    return (
        math.floor(lat / cell_size_deg),
        math.floor(lng / cell_size_deg),
    )


def cluster_sites(
    records: list[dict[str, Any]],
    merge_distance_meters: float = DEFAULT_MERGE_DISTANCE_METERS,
    unit_key_fn: Callable[[dict[str, Any]], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Cluster individual station observations into sites by geographical location only.

    Rules & Guarantees:
    - Sites are merged strictly by geographic distance <= merge_distance_meters.
    - Names/addresses are not used for spatial merging (far-apart sites with identical
      names remain distinct).
    - Uses an O(N) spatial grid index to remain fast for thousands of records.
    - Selects the most common display name observed across merged records.
    - Selects the most common display address observed across merged records.
    - Device identifiers / unit keys are held in memory ONLY to count distinct units.
      They are NEVER serialized or exposed in the returned dictionaries.
    """
    if not records:
        return []

    # Calculate grid cell size in degrees roughly equal to max(merge_distance, 50m)
    effective_cell_m = max(merge_distance_meters, 50.0)
    meters_per_deg = 111_000.0
    cell_size_deg = effective_cell_m / meters_per_deg

    spatial_grid: dict[tuple[int, int], list[int]] = {}
    sites: list[dict[str, Any]] = []

    for record in records:
        lat = record.get("latitude")
        lng = record.get("longitude")
        if lat is None or lng is None:
            continue

        raw_name = record.get("name")
        clean_name = clean_display_text(raw_name)

        raw_addr = record.get("address")
        clean_addr = clean_display_text(raw_addr)

        unit_key: str | None = None
        if unit_key_fn is not None:
            unit_key = unit_key_fn(record)

        target_cell = _spatial_grid_cell(lat, lng, cell_size_deg)

        matched_site_idx: int | None = None
        min_dist = float("inf")

        # Search the 3x3 neighboring cells in the spatial index
        for d_lat in (-1, 0, 1):
            for d_lng in (-1, 0, 1):
                neighbor_cell = (target_cell[0] + d_lat, target_cell[1] + d_lng)
                candidate_indices = spatial_grid.get(neighbor_cell)
                if not candidate_indices:
                    continue

                for site_idx in candidate_indices:
                    site = sites[site_idx]
                    dist = haversine_distance_meters(
                        lat, lng, site["latitude"], site["longitude"]
                    )
                    if dist <= merge_distance_meters and dist < min_dist:
                        min_dist = dist
                        matched_site_idx = site_idx

        if matched_site_idx is not None:
            # Merge into existing site
            site = sites[matched_site_idx]
            if clean_name:
                site["_names_counter"][clean_name] += 1
            if clean_addr:
                site["_addresses_counter"][clean_addr] += 1
            if unit_key:
                site["_unit_keys"].add(unit_key)
            site["_record_count"] += 1
        else:
            # Create a new site entry
            new_site_idx = len(sites)
            names_counter = Counter()
            if clean_name:
                names_counter[clean_name] += 1

            addr_counter = Counter()
            if clean_addr:
                addr_counter[clean_addr] += 1

            unit_keys_set: set[str] = set()
            if unit_key:
                unit_keys_set.add(unit_key)

            new_site = {
                "operator": record.get("operator", ""),
                "latitude": lat,
                "longitude": lng,
                "source": record.get("source", ""),
                "retrieved_on": record.get("retrieved_on", ""),
                "_names_counter": names_counter,
                "_addresses_counter": addr_counter,
                "_unit_keys": unit_keys_set,
                "_has_unit_keys_tracked": unit_key_fn is not None,
                "_record_count": 1,
            }
            sites.append(new_site)

            # Register in spatial index
            if target_cell not in spatial_grid:
                spatial_grid[target_cell] = []
            spatial_grid[target_cell].append(new_site_idx)

    # Finalize site representations and sanitize in-memory tracking structures
    finalized_sites: list[dict[str, Any]] = []
    for site in sites:
        # Pick most common display name
        names_counter = site["_names_counter"]
        canonical_name = names_counter.most_common(1)[0][0] if names_counter else ""

        # Pick most common display address
        addresses_counter = site["_addresses_counter"]
        canonical_addr = (
            addresses_counter.most_common(1)[0][0] if addresses_counter else ""
        )

        # Determine cabinet count semantics
        if site["_has_unit_keys_tracked"]:
            cabinet_count: Any = len(site["_unit_keys"])
        else:
            # When unit keys are not tracked (e.g. Spiro), cabinet_count stays strictly blank
            cabinet_count = ""

        finalized = {
            "operator": site["operator"],
            "name": canonical_name,
            "address": canonical_addr,
            "latitude": site["latitude"],
            "longitude": site["longitude"],
            "cabinet_count": cabinet_count,
            "source": site["source"],
            "retrieved_on": site["retrieved_on"],
        }
        # Hard Rule: No device identifiers or temporary set attributes are ever leaked
        finalized_sites.append(finalized)

    return finalized_sites


# --- Atomic File Writing ---

def atomic_write_text(
    path: Path | str,
    content: str,
    encoding: str = "utf-8",
) -> None:
    """Atomically write text content to path, leaving existing target intact on failure."""
    target_path = Path(path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
            encoding=encoding,
            newline="",
        ) as tmp:
            temp_path = Path(tmp.name)
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())

        os.replace(temp_path, target_path)
    except Exception:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def atomic_write_json(
    path: Path | str,
    data: Any,
    indent: int = 2,
) -> None:
    """Atomically serialize data to JSON at path, leaving existing file intact on failure."""
    content = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
    atomic_write_text(path, content)


def atomic_write_csv(
    path: Path | str,
    rows: Sequence[dict[str, Any]],
    fieldnames: Sequence[str] = BASE_COLUMNS,
    extrasaction: str = "ignore",
) -> None:
    """Atomically write rows to CSV at path, leaving existing file intact on failure."""
    target_path = Path(path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
            newline="",
        ) as tmp:
            temp_path = Path(tmp.name)
            writer = csv.DictWriter(
                tmp,
                fieldnames=fieldnames,
                extrasaction=extrasaction,
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            tmp.flush()
            os.fsync(tmp.fileno())

        os.replace(temp_path, target_path)
    except Exception:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


# --- Ingestion Run Reporting ---

@dataclass
class IngestionReport:
    """Tracks and formats run statistics, counting kept and dropped records by reason."""

    total_input: int = 0
    kept_count: int = 0
    dropped_by_reason: Counter[str] = field(default_factory=Counter)
    outside_kenya_dropped: list[dict[str, Any]] = field(default_factory=list)

    def record_kept(self, count: int = 1) -> None:
        """Register kept record(s)."""
        self.total_input += count
        self.kept_count += count

    def record_dropped(self, reason: str, count: int = 1) -> None:
        """Register dropped record(s) under a specific categorized reason."""
        self.total_input += count
        self.dropped_by_reason[reason] += count

    def record_outside_kenya(self, name: str, latitude: float, longitude: float) -> None:
        """Track records dropped for falling outside Kenya bounds (no rescue logic)."""
        self.outside_kenya_dropped.append({
            "name": name or "(Unnamed)",
            "latitude": latitude,
            "longitude": longitude,
        })

    @property
    def total_dropped(self) -> int:
        """Total number of records dropped."""
        return sum(self.dropped_by_reason.values())

    def to_dict(self) -> dict[str, Any]:
        """Convert report metrics into a dictionary representation."""
        return {
            "total_input": self.total_input,
            "kept": self.kept_count,
            "dropped_total": self.total_dropped,
            "dropped_by_reason": dict(self.dropped_by_reason),
        }

    def summary_text(self) -> str:
        """Format a human-readable multi-line summary."""
        lines = [
            "Ingestion Run Report:",
            f"  Total input records : {self.total_input}",
            f"  Kept records        : {self.kept_count}",
            f"  Dropped records     : {self.total_dropped}",
        ]
        if self.dropped_by_reason:
            lines.append("  Drop breakdown by reason:")
            for reason, count in sorted(self.dropped_by_reason.items()):
                lines.append(f"    - {reason}: {count}")
        return "\n".join(lines)
