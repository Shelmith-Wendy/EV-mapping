"""Storage management: snapshot archival, current dataset promotion, and atomic persistence."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from scripts.ingestion.config import ALL_COLUMNS, get_operator_slug
from scripts.ingestion.core import atomic_write_csv, atomic_write_json


def get_repo_root() -> Path:
    """Return repository root path."""
    return Path(__file__).resolve().parent.parent.parent


def get_inbox_dir(operator: str, base_dir: Path | None = None) -> Path:
    """Directory for inbox payloads of an operator."""
    root = base_dir or get_repo_root()
    slug = get_operator_slug(operator)
    path = root / "data" / "inbox" / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_archive_dir(operator: str, base_dir: Path | None = None) -> Path:
    """Directory for archived source payloads of an operator."""
    root = base_dir or get_repo_root()
    slug = get_operator_slug(operator)
    path = root / "data" / "archive" / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_inbox_path(path: Path | str, base_dir: Path | None = None) -> bool:
    """Check if a given path resides inside data/inbox/."""
    p = Path(path).resolve()
    root = base_dir or get_repo_root()
    inbox_root = (root / "data" / "inbox").resolve()
    try:
        p.relative_to(inbox_root)
        return True
    except ValueError:
        parts = p.parts
        return "inbox" in parts and "data" in parts


def get_snapshots_dir(operator: str, base_dir: Path | None = None) -> Path:
    """Directory for accepted dated snapshots of an operator."""
    root = base_dir or get_repo_root()
    slug = get_operator_slug(operator)
    path = root / "data" / "snapshots" / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_current_dataset_path(operator: str, base_dir: Path | None = None) -> Path:
    """Path to the canonical current dataset consumed by the dashboard."""
    root = base_dir or get_repo_root()
    slug = get_operator_slug(operator)
    current_dir = root / "data" / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    return current_dir / f"{slug}_stations.csv"


def get_merged_dataset_path(base_dir: Path | None = None) -> Path:
    """Path to the multi-operator merged current dataset."""
    root = base_dir or get_repo_root()
    current_dir = root / "data" / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    return current_dir / "kenya_swap_stations.csv"


def get_verification_overlay_path(base_dir: Path | None = None) -> Path:
    """Path to the human verification overlay file."""
    root = base_dir or get_repo_root()
    return root / "data" / "verification" / "human_verification_overlay.csv"


def load_latest_snapshot(
    operator: str,
    base_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], Path | None]:
    """Find and load the most recent accepted snapshot for an operator.

    First inspects data/snapshots/<operator_slug>/ sorted by filename descending.
    If none found, checks data/current/<operator_slug>_stations.csv.

    Returns:
        (records, snapshot_path_or_none)
    """
    snapshots_dir = get_snapshots_dir(operator, base_dir=base_dir)
    snapshot_files = sorted(snapshots_dir.glob("*_stations.csv"), reverse=True)

    target_file: Path | None = None
    if snapshot_files:
        target_file = snapshot_files[0]
    else:
        current_file = get_current_dataset_path(operator, base_dir=base_dir)
        if current_file.is_file():
            target_file = current_file

    if target_file is None or not target_file.is_file():
        return [], None

    records: list[dict[str, Any]] = []
    with open(target_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = dict(row)
            try:
                parsed["latitude"] = float(parsed["latitude"])
                parsed["longitude"] = float(parsed["longitude"])
            except (ValueError, TypeError):
                pass
            if "is_removed" in parsed:
                parsed["is_removed"] = str(parsed["is_removed"]).strip().lower() in ("true", "1")
            if "co_located" in parsed:
                parsed["co_located"] = str(parsed["co_located"]).strip().lower() in ("true", "1")
            records.append(parsed)

    return records, target_file


def write_dated_snapshot(
    operator: str,
    records: list[dict[str, Any]],
    run_date: str,
    base_dir: Path | None = None,
) -> Path:
    """Atomically write a dated accepted snapshot to data/snapshots/<operator>/."""
    snapshots_dir = get_snapshots_dir(operator, base_dir=base_dir)
    target_path = snapshots_dir / f"{run_date}_stations.csv"
    atomic_write_csv(target_path, records, fieldnames=ALL_COLUMNS)
    return target_path


def update_current_dataset(
    operator: str,
    records: list[dict[str, Any]],
    base_dir: Path | None = None,
) -> Path:
    """Atomically update the operator's current dataset in data/current/."""
    target_path = get_current_dataset_path(operator, base_dir=base_dir)
    atomic_write_csv(target_path, records, fieldnames=ALL_COLUMNS)
    return target_path


def update_merged_dataset(
    operator_datasets: list[list[dict[str, Any]]],
    base_dir: Path | None = None,
) -> Path:
    """Atomically update the merged multi-operator dataset in data/current/ (CSV and JSON)."""
    target_path = get_merged_dataset_path(base_dir=base_dir)
    merged_records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for dataset in operator_datasets:
        for r in dataset:
            sid = str(r.get("station_id", "")).strip()
            if sid:
                if sid in seen_ids:
                    raise ValueError(f"Cross-operator station_id collision detected in merged dataset: {sid!r}")
                seen_ids.add(sid)
            merged_records.append(r)
    atomic_write_csv(target_path, merged_records, fieldnames=ALL_COLUMNS)
    atomic_write_json(target_path.with_suffix(".json"), merged_records)
    return target_path
