"""Phase 2 Unity Catalog Volume contract for published dashboard files."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from common import (
    DASHBOARD_FILES,
    MANIFEST_FILE,
    STATIONS_WITH_COUNTY,
    current_region,
    dashboard_files_for,
    read_json,
    write_json,
)

MANIFEST_SCHEMA_VERSION = 1


def volume_root(catalog: str, schema: str, volume: str = "public") -> str:
    """Logical Volume path used by Jobs (write) and the App (read)."""
    return f"/Volumes/{catalog}/{schema}/{volume}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_retrieved_at(stations: list[dict]) -> str | None:
    values = [row.get("retrieved_at") for row in stations if row.get("retrieved_at")]
    return max(values) if values else None


def _dashboard_files(region_id: str | None = None) -> tuple[str, ...]:
    if region_id is None:
        try:
            return dashboard_files_for(current_region())
        except KeyError:
            return DASHBOARD_FILES
    from regions import REGIONS

    return dashboard_files_for(REGIONS[region_id])


def contract_files(region_id: str | None = None) -> tuple[str, ...]:
    """Dashboard artifacts + manifest for a region (Kenya-shaped default)."""
    return _dashboard_files(region_id) + (MANIFEST_FILE,)


def region_id_from_public_dir(public_dir: Path) -> str | None:
    from regions import REGIONS

    name = Path(public_dir).name
    return name if name in REGIONS else None


def build_manifest(
    public_dir: Path,
    *,
    pipeline_run_id: str | None = None,
    source_name: str = "zeno_earth",
    region_id: str | None = None,
) -> dict:
    files = {}
    for name in _dashboard_files(region_id):
        path = public_dir / name
        files[name] = {
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }

    stations = read_json(public_dir / STATIONS_WITH_COUNTY)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "pipeline_run_id": pipeline_run_id or uuid.uuid4().hex,
        "source_name": source_name,
        "station_count": len(stations),
        "retrieved_at": latest_retrieved_at(stations),
        "files": files,
    }


def write_manifest(
    public_dir: Path,
    *,
    pipeline_run_id: str | None = None,
    region_id: str | None = None,
    source_name: str | None = None,
) -> Path:
    public_dir = Path(public_dir)
    dash_files = _dashboard_files(region_id)
    missing = [name for name in dash_files if not (public_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing dashboard files in {public_dir}: {missing}")

    stations = read_json(public_dir / STATIONS_WITH_COUNTY)
    if source_name is None:
        names = [row.get("source_name") for row in stations if row.get("source_name")]
        source_name = max(set(names), key=names.count) if names else "unknown"

    path = public_dir / MANIFEST_FILE
    write_json(
        path,
        build_manifest(
            public_dir,
            pipeline_run_id=pipeline_run_id,
            source_name=source_name,
            region_id=region_id,
        ),
    )
    return path


def validate_public_dir(public_dir: Path, *, region_id: str | None = None) -> list[str]:
    """Return human-readable problems; empty list means the contract is satisfied."""
    public_dir = Path(public_dir)
    problems: list[str] = []
    dash_files = _dashboard_files(region_id)
    volume_files = contract_files(region_id)

    for name in volume_files:
        path = public_dir / name
        if not path.is_file():
            problems.append(f"missing required file: {name}")

    manifest_path = public_dir / MANIFEST_FILE
    if not manifest_path.is_file():
        return problems

    try:
        manifest = read_json(manifest_path)
    except json.JSONDecodeError as error:
        problems.append(f"{MANIFEST_FILE} is not valid JSON: {error}")
        return problems

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        problems.append(
            f"{MANIFEST_FILE} schema_version "
            f"{manifest.get('schema_version')!r} != {MANIFEST_SCHEMA_VERSION}"
        )

    for name in dash_files:
        path = public_dir / name
        if not path.is_file():
            continue
        meta = (manifest.get("files") or {}).get(name) or {}
        expected_sha = meta.get("sha256")
        expected_bytes = meta.get("bytes")
        actual_sha = file_sha256(path)
        actual_bytes = path.stat().st_size
        if expected_sha and expected_sha != actual_sha:
            problems.append(f"{name} sha256 mismatch (manifest stale?)")
        if expected_bytes is not None and int(expected_bytes) != actual_bytes:
            problems.append(f"{name} size mismatch (manifest stale?)")

    stations_path = public_dir / STATIONS_WITH_COUNTY
    if stations_path.is_file() and "station_count" in manifest:
        station_count = len(read_json(stations_path))
        if int(manifest["station_count"]) != station_count:
            problems.append(
                f"station_count {manifest['station_count']} != {station_count} stations in file"
            )

    return problems


def publish_public_dir(
    public_dir: Path,
    volume_root_path: str | Path,
    *,
    region_id: str | None = None,
) -> Path:
    """Copy validated contract files from public_dir into a UC Volume directory.

    When region_id is set, files land under {volume_root}/{region_id}/ so regions
    do not overwrite each other. On Databricks the Volume is FUSE-mounted under
    /Volumes/... so normal filesystem copies work. Callers should validate first.
    """
    public_dir = Path(public_dir)
    rid = region_id or region_id_from_public_dir(public_dir)
    dest_root = Path(volume_root_path)
    if rid:
        dest_root = dest_root / rid
    volume_files = contract_files(rid)
    problems = validate_public_dir(public_dir, region_id=rid)
    if problems:
        raise ValueError("Refusing to publish; contract problems: " + "; ".join(problems))

    dest_root.mkdir(parents=True, exist_ok=True)
    staging = dest_root / f".staging.{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        for name in volume_files:
            shutil.copy2(public_dir / name, staging / name)
        for name in volume_files:
            src = staging / name
            final = dest_root / name
            tmp = dest_root / f".{name}.{uuid.uuid4().hex}.tmp"
            shutil.copy2(src, tmp)
            tmp.replace(final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return dest_root
