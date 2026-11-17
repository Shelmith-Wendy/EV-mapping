"""Build and publish dashboard-ready public assets for Kenya battery-swap stations.

Converts canonical active records from data/current/kenya_swap_stations.json (or csv)
into data/public/kenya_swap/ for the Leaflet dashboard, following conventions in
docs/project-recon.md and GEMINI.md:
- One pin per site.
- Operator tier summary with per-operator counts and retrieved_on dates.
- Zero-coordinate safe (equator friendly).
- Excludes removed stations (is_removed == True).
- Never writes or exposes device identifiers.
- Atomic file writes using standard library only.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.ingestion.config import (
    KENYA_MAX_LAT,
    KENYA_MAX_LNG,
    KENYA_MIN_LAT,
    KENYA_MIN_LNG,
    load_config,
)
from scripts.ingestion.core import (
    atomic_write_json,
    clean_display_text,
    is_in_kenya_box,
    parse_coordinates,
)


def compute_file_sha256(path: Path) -> tuple[int, str]:
    """Compute byte size and sha256 hash of a file."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
            size += len(chunk)
    return size, h.hexdigest()


def prepare_swap_stations(
    raw_stations: list[dict[str, Any]],
    kenya_bounds: tuple[float, float, float, float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter and prepare public station records and operator summaries.

    CRITICAL RULES:
    1. Zero (0.0) is a valid coordinate; never test via truthiness.
    2. Exclude removed stations (is_removed == True).
    3. Exclude any device identifiers (never expose in public assets).
    4. Validate coordinates are within Kenya bounds.

    Returns:
        (active_stations, operator_summaries)
    """
    bounds = kenya_bounds or (KENYA_MIN_LAT, KENYA_MAX_LAT, KENYA_MIN_LNG, KENYA_MAX_LNG)
    active_stations: list[dict[str, Any]] = []

    # Map operators to station list, excluded coords, and cabinet totals
    op_stations: dict[str, list[dict[str, Any]]] = {}
    op_excluded_coords: dict[str, int] = {}
    op_cabinet_totals: dict[str, int] = {}

    for row in raw_stations:
        # Filter removed stations
        is_removed = row.get("is_removed")
        if is_removed is True or str(is_removed).strip().lower() in ("true", "1"):
            continue

        operator = clean_display_text(str(row.get("operator", "")))
        if not operator:
            continue

        raw_lat = row.get("latitude")
        raw_lng = row.get("longitude")
        coords = parse_coordinates(raw_lat, raw_lng)
        if coords is None:
            op_excluded_coords[operator] = op_excluded_coords.get(operator, 0) + 1
            continue

        lat, lng = coords
        if not is_in_kenya_box(lat, lng, bounds):
            op_excluded_coords[operator] = op_excluded_coords.get(operator, 0) + 1
            continue

        name = clean_display_text(str(row.get("name", "")))
        address = clean_display_text(str(row.get("address", "")))
        retrieved_on = str(row.get("retrieved_on", "")).strip()
        last_verified = str(row.get("last_verified", "")).strip()

        # Handle cabinet_count: ARC Ride has counts; Spiro stays blank/None
        raw_cab = row.get("cabinet_count")
        cabinet_count: str | None = None
        if raw_cab is not None:
            cab_str = str(raw_cab).strip()
            if cab_str:
                cabinet_count = cab_str
                try:
                    op_cabinet_totals[operator] = op_cabinet_totals.get(operator, 0) + int(cab_str)
                except ValueError:
                    pass

        # Public station record (strictly no device identifiers)
        station_record: dict[str, Any] = {
            "station_id": str(row.get("station_id", "")).strip(),
            "name": name,
            "operator": operator,
            "address": address,
            "final_latitude": lat,
            "final_longitude": lng,
            "latitude": lat,
            "longitude": lng,
            "station_type": "swap_station",
            "cabinet_count": cabinet_count,
            "retrieved_on": retrieved_on,
            "last_verified": last_verified if last_verified else None,
            "co_located": bool(row.get("co_located", False)),
            "co_located_with": str(row.get("co_located_with", "")).strip(),
            "source": clean_display_text(str(row.get("source", ""))),
        }

        active_stations.append(station_record)
        op_stations.setdefault(operator, []).append(station_record)

    # Build operator summaries sorted alphabetically
    operator_summaries: list[dict[str, Any]] = []
    for op_name in sorted(op_stations.keys()):
        stations = op_stations[op_name]
        dates = [s["retrieved_on"] for s in stations if s.get("retrieved_on")]
        latest_date = max(dates) if dates else ""
        cab_total = op_cabinet_totals.get(op_name) if op_name == "ARC Ride" else None
        operator_summaries.append({
            "operator": op_name,
            "swap_station_count": len(stations),
            "total_count": len(stations),
            "cabinet_count": cab_total,
            "excluded_invalid_coordinates": op_excluded_coords.get(op_name, 0),
            "retrieved_on": latest_date,
        })

    return active_stations, operator_summaries


def build_swap_public(
    source_json_path: Path,
    public_dir: Path,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build all assets in data/public/kenya_swap/ and update regions.json.

    Returns:
        Summary dict of generated assets.
    """
    root = repo_root or _REPO_ROOT
    cfg = load_config()

    if not source_json_path.is_file():
        raise FileNotFoundError(f"Source stations dataset not found: {source_json_path}")

    with open(source_json_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    if not isinstance(raw_data, list):
        raise ValueError(f"Expected list of stations in {source_json_path}")

    active_stations, operator_summaries = prepare_swap_stations(
        raw_data, kenya_bounds=cfg.kenya_bounds
    )

    public_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write stations_with_county.json
    stations_path = public_dir / "stations_with_county.json"
    atomic_write_json(stations_path, active_stations)

    # 2. Write operator_summary.json (matches tiers: ["operator"])
    operator_summary_path = public_dir / "operator_summary.json"
    atomic_write_json(operator_summary_path, operator_summaries)

    # 3. Copy kenya_counties_display.geojson if source exists
    source_geojson = root / "data" / "public" / "kenya" / "kenya_counties_display.geojson"
    target_geojson = public_dir / "kenya_counties_display.geojson"
    if source_geojson.is_file():
        shutil.copyfile(source_geojson, target_geojson)
    else:
        # Fallback to minimal FeatureCollection
        atomic_write_json(target_geojson, {"type": "FeatureCollection", "features": []})

    # 4. Write manifest.json
    all_dates = [s["retrieved_on"] for s in active_stations if s.get("retrieved_on")]
    latest_retrieved = max(all_dates) if all_dates else datetime.date.today().isoformat()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    run_id = uuid.uuid4().hex

    files_manifest: dict[str, dict[str, Any]] = {}
    for filename in ["operator_summary.json", "stations_with_county.json", "kenya_counties_display.geojson"]:
        fpath = public_dir / filename
        if fpath.is_file():
            size, sha = compute_file_sha256(fpath)
            files_manifest[filename] = {"bytes": size, "sha256": sha}

    manifest = {
        "schema_version": 1,
        "generated_at": now_iso,
        "pipeline_run_id": run_id,
        "source_name": "arcride_spiro",
        "station_count": len(active_stations),
        "retrieved_at": f"{latest_retrieved}T00:00:00+00:00",
        "files": files_manifest,
    }
    manifest_path = public_dir / "manifest.json"
    atomic_write_json(manifest_path, manifest)

    # 5. Update data/public/regions.json
    regions_json_path = root / "data" / "public" / "regions.json"
    update_regions_json(regions_json_path)

    return {
        "station_count": len(active_stations),
        "operator_counts": {op["operator"]: op["total_count"] for op in operator_summaries},
        "public_dir": str(public_dir),
    }


def update_regions_json(regions_json_path: Path) -> None:
    """Ensure kenya_swap is present in regions.json."""
    swap_region_entry = {
        "region_id": "kenya_swap",
        "display_name": "Kenya Swap Stations",
        "cluster": "east_africa",
        "bounds": [
            [-5.0, 33.5],
            [5.6, 42.2],
        ],
        "tiers": ["operator"],
        "station_types": ["swap_station"],
        "data_path": "kenya_swap",
        "dataset_source": "arcride_spiro",
        "dataset_source_label": "ARC Ride & Spiro",
        "primary_chart_label": "mapped locations",
        "capacity_note": "Spiro locations and ARC Ride sites are not capacity-comparable.",
        "spiro_map_listed": {
            "count": "[N]",
            "date": "[date]",
        },
    }

    if regions_json_path.is_file():
        try:
            with open(regions_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"regions": []}
    else:
        data = {"regions": []}

    regions_list = data.get("regions", [])
    # Replace or prepend kenya_swap, preserving user-customized config fields
    existing_idx = next(
        (i for i, r in enumerate(regions_list) if r.get("region_id") == "kenya_swap"),
        None,
    )
    if existing_idx is not None:
        existing = regions_list[existing_idx]
        merged_entry = dict(swap_region_entry)
        if "spiro_map_listed" in existing:
            merged_entry["spiro_map_listed"] = existing["spiro_map_listed"]
        if "primary_chart_label" in existing:
            merged_entry["primary_chart_label"] = existing["primary_chart_label"]
        if "capacity_note" in existing:
            merged_entry["capacity_note"] = existing["capacity_note"]
        regions_list[existing_idx] = merged_entry
    else:
        # Insert as first item for default focus
        regions_list.insert(0, swap_region_entry)

    data["regions"] = regions_list
    atomic_write_json(regions_json_path, data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish battery swap dataset to dashboard public directory.")
    parser.add_argument(
        "--source",
        type=Path,
        default=_REPO_ROOT / "data" / "current" / "kenya_swap_stations.json",
        help="Path to merged current dataset JSON",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=_REPO_ROOT / "data" / "public" / "kenya_swap",
        help="Destination public directory for kenya_swap",
    )
    args = parser.parse_args()

    summary = build_swap_public(args.source, args.dest, repo_root=_REPO_ROOT)
    print("Successfully built swap public dataset:")
    print(f"  Active Stations : {summary['station_count']}")
    print(f"  Operators       : {summary['operator_counts']}")
    print(f"  Destination     : {summary['public_dir']}")


if __name__ == "__main__":
    main()
