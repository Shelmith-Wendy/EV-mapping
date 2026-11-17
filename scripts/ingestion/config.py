"""Central configuration, thresholds, Kenya bounds, and schema constants for station ingestion."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --- Schema and Column Order ---

# Canonical station columns common to all operators
BASE_COLUMNS: tuple[str, ...] = (
    "operator",
    "name",
    "address",
    "latitude",
    "longitude",
    "cabinet_count",
    "source",
    "retrieved_on",
)

# Registry and lifecycle metadata columns (tracked across ingestion runs)
REGISTRY_COLUMNS: tuple[str, ...] = (
    "station_id",
    "first_seen",
    "last_seen",
    "is_removed",
    "co_located",
    "co_located_with",
    "last_verified",
)

# Combined station record schema
ALL_COLUMNS: tuple[str, ...] = BASE_COLUMNS + REGISTRY_COLUMNS

# Supported operator names
KNOWN_OPERATORS: frozenset[str] = frozenset({"ARC Ride", "Spiro"})

# Forbidden identifier columns that must never appear in persisted datasets
FORBIDDEN_IDENTIFIER_COLUMNS: frozenset[str] = frozenset({
    "imei",
    "imei_number",
    "serial_number",
    "serial_no",
    "device_id",
    "unit_id",
    "unit_key",
    "mac_address",
    "hardware_id",
})

# Network configuration defaults
ARCRIDE_DEFAULT_URL: str = "https://arcrideglobal.com/api/cabinets"
ARCRIDE_DEFAULT_COOLDOWN_SECONDS: int = 43200  # 12-hour minimum cooldown between fetches
ARCRIDE_DEFAULT_TIMEOUT_SECONDS: int = 30


def normalize_operator_name(raw_name: str) -> str:
    """Normalize user input operator string to canonical operator name ('ARC Ride' or 'Spiro')."""
    cleaned = raw_name.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    if cleaned in ("arcride", "arc"):
        return "ARC Ride"
    if cleaned in ("spiro", "spironet"):
        return "Spiro"
    raise ValueError(
        f"Unknown operator {raw_name!r}. Supported operators: 'ARC Ride', 'Spiro'."
    )


def get_operator_slug(operator: str) -> str:
    """Return filesystem slug ('arcride' or 'spiro') for an operator name."""
    canonical = normalize_operator_name(operator)
    return "arcride" if canonical == "ARC Ride" else "spiro"


def get_operator_prefix(operator: str) -> str:
    """Return stable ID prefix ('ARC' or 'SPIRO') for an operator name."""
    canonical = normalize_operator_name(operator)
    return "ARC" if canonical == "ARC Ride" else "SPIRO"

# --- Default Spatial & Geographic Constants ---

# Kenya WGS84 Bounding Box (latitude crosses the equator: zero is valid)
KENYA_MIN_LAT: float = -4.8
KENYA_MAX_LAT: float = 5.2
KENYA_MIN_LNG: float = 33.8
KENYA_MAX_LNG: float = 42.0

# Distance tolerances in meters
DEFAULT_MERGE_DISTANCE_METERS: float = 30.0  # Same-site cabinet clustering radius
DEFAULT_MATCH_DISTANCE_METERS: float = 50.0  # Station identity matching radius
DEFAULT_MOVED_DISTANCE_METERS: float = 200.0  # Distance threshold for moved vs new station
DEFAULT_COLOCATION_DISTANCE_METERS: float = 100.0  # Multi-operator co-location radius

# Safety Gate Thresholds
DEFAULT_PCT_CHANGE_WARN: float = 0.15  # Warn if station count changes by > 15%
DEFAULT_PCT_DROP_BLOCK: float = 0.40  # Block if station count drops by > 40%
DEFAULT_EMPTY_RUN_REJECT: bool = True  # Reject runs that yield zero valid stations


@dataclass(frozen=True)
class IngestionConfig:
    """Immutable configuration parameters for ingestion."""

    min_lat: float = KENYA_MIN_LAT
    max_lat: float = KENYA_MAX_LAT
    min_lng: float = KENYA_MIN_LNG
    max_lng: float = KENYA_MAX_LNG
    merge_distance_meters: float = DEFAULT_MERGE_DISTANCE_METERS
    match_distance_meters: float = DEFAULT_MATCH_DISTANCE_METERS
    moved_distance_meters: float = DEFAULT_MOVED_DISTANCE_METERS
    colocation_distance_meters: float = DEFAULT_COLOCATION_DISTANCE_METERS
    pct_change_warn: float = DEFAULT_PCT_CHANGE_WARN
    pct_drop_block: float = DEFAULT_PCT_DROP_BLOCK
    empty_run_reject: bool = DEFAULT_EMPTY_RUN_REJECT
    contact_email: str = ""
    arcride_url: str = ARCRIDE_DEFAULT_URL
    arcride_cooldown_seconds: int = ARCRIDE_DEFAULT_COOLDOWN_SECONDS
    arcride_timeout_seconds: int = ARCRIDE_DEFAULT_TIMEOUT_SECONDS

    @property
    def kenya_bounds(self) -> tuple[float, float, float, float]:
        """Return (min_lat, max_lat, min_lng, max_lng)."""
        return (self.min_lat, self.max_lat, self.min_lng, self.max_lng)

    @property
    def user_agent(self) -> str:
        """Construct an honest user agent string including contact email if configured."""
        if self.contact_email:
            return f"EV-Mapping-Ingestion/1.0 (+https://github.com/; {self.contact_email})"
        return "EV-Mapping-Ingestion/1.0 (+https://github.com/)"


def load_config(
    config_path: Path | str | None = None,
    **overrides: Any,
) -> IngestionConfig:
    """Load configuration from a local untracked config file and environment variables.

    Loading order:
    1. Built-in defaults.
    2. Local untracked JSON file (if path provided or config.local.json exists).
    3. Environment variables (e.g. EV_MAP_CONTACT_EMAIL, EV_MAP_MERGE_DISTANCE).
    4. Explicit overrides passed as keyword arguments.
    """
    params: dict[str, Any] = {
        "min_lat": KENYA_MIN_LAT,
        "max_lat": KENYA_MAX_LAT,
        "min_lng": KENYA_MIN_LNG,
        "max_lng": KENYA_MAX_LNG,
        "merge_distance_meters": DEFAULT_MERGE_DISTANCE_METERS,
        "match_distance_meters": DEFAULT_MATCH_DISTANCE_METERS,
        "moved_distance_meters": DEFAULT_MOVED_DISTANCE_METERS,
        "colocation_distance_meters": DEFAULT_COLOCATION_DISTANCE_METERS,
        "pct_change_warn": DEFAULT_PCT_CHANGE_WARN,
        "pct_drop_block": DEFAULT_PCT_DROP_BLOCK,
        "empty_run_reject": DEFAULT_EMPTY_RUN_REJECT,
        "contact_email": "",
        "arcride_url": ARCRIDE_DEFAULT_URL,
        "arcride_cooldown_seconds": ARCRIDE_DEFAULT_COOLDOWN_SECONDS,
        "arcride_timeout_seconds": ARCRIDE_DEFAULT_TIMEOUT_SECONDS,
    }

    # Determine local config file location
    file_to_check: Path | None = None
    if config_path is not None:
        file_to_check = Path(config_path)
    else:
        # Check standard untracked config in repo root
        repo_root = Path(__file__).resolve().parent.parent.parent
        candidate = repo_root / "config.local.json"
        if candidate.is_file():
            file_to_check = candidate

    if file_to_check is not None and file_to_check.is_file():
        try:
            with open(file_to_check, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for key, val in data.items():
                        if key in params:
                            params[key] = val
        except (json.JSONDecodeError, OSError) as err:
            raise ValueError(f"Failed to read local config from {file_to_check}: {err}") from err

    # Environment variables
    env_email = os.environ.get("EV_MAP_CONTACT_EMAIL") or os.environ.get("CONTACT_EMAIL")
    if env_email:
        params["contact_email"] = env_email.strip()

    env_merge = os.environ.get("EV_MAP_MERGE_DISTANCE")
    if env_merge:
        try:
            params["merge_distance_meters"] = float(env_merge)
        except ValueError:
            pass

    # Keyword overrides
    for key, val in overrides.items():
        if key in params and val is not None:
            params[key] = val

    return IngestionConfig(**params)
