#!/usr/bin/env python3
"""Run the full data pipeline into staging, validate, then promote to public.

Regions in REGIONS run independently: one region's failure does not block the
others, and a failed region never promotes (last-good public/Volume data stays).

Optional: set KENYA_EV_VOLUME_ROOT=/Volumes/<catalog>/<schema>/<volume> to also
publish contract files after a successful promote, and to persist diagnostic
logs (e.g. geocode_failures.json) under _pipeline_logs/<region>/.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from common import (
    PIPELINE_STEPS,
    REGIONS_INDEX,
    ROOT,
    cache_files_for,
    public_dir,
    write_regions_index,
)
from regions import REGIONS
from volume_contract import publish_public_dir, write_manifest

SCRIPTS = ROOT / "scripts"
STEPS = list(PIPELINE_STEPS)
GEOCODE_FAILURES = "geocode_failures.json"


def seed_staging(region_id: str, staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    public = public_dir(region_id)
    if not public.is_dir():
        return

    for name in cache_files_for(REGIONS[region_id]):
        path = public / name
        if path.exists():
            shutil.copy2(path, staging / name)


def run_step(script: str, staging: Path, region_id: str) -> None:
    print(f"\n==> {script}", flush=True)
    env = {
        **os.environ,
        "KENYA_EV_DATA_DIR": str(staging),
        "KENYA_EV_REGION": region_id,
        "PYTHONPATH": str(ROOT) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
        "PYTHONUNBUFFERED": "1",
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{script} exited with code {result.returncode}")


def validate_staging(staging: Path, region_id: str) -> None:
    print("\n==> validate_publish.py")
    env = {
        **os.environ,
        "KENYA_EV_DATA_DIR": str(staging),
        "KENYA_EV_REGION": region_id,
        "PYTHONPATH": str(ROOT) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
        "PYTHONUNBUFFERED": "1",
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_publish.py")],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("validation failed")


def promote_to_public(staging: Path, region_id: str) -> str:
    public = public_dir(region_id)
    run_id = uuid.uuid4().hex
    tmp = public.parent / f".public.{region_id}.{run_id}"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(staging, tmp)
    if public.exists():
        shutil.rmtree(public)
    public.parent.mkdir(parents=True, exist_ok=True)
    tmp.rename(public)
    write_manifest(public, pipeline_run_id=run_id, region_id=region_id)
    print(f"\nPromoted staging -> {public}")
    print(f"Wrote manifest pipeline_run_id={run_id}")
    return run_id


def publish_to_volume_if_configured(public: Path, region_id: str) -> None:
    volume_root_env = os.environ.get("KENYA_EV_VOLUME_ROOT", "").strip()
    if not volume_root_env:
        print("\nKENYA_EV_VOLUME_ROOT unset — skipping Volume publish")
        return

    print(f"\n==> publish to {volume_root_env}/{region_id}")
    published = publish_public_dir(public, volume_root_env, region_id=region_id)
    print(f"Published contract files to {published}/")


def persist_region_diagnostics(region_id: str) -> None:
    """Copy durable diagnostic logs to the Volume when configured (even on failure)."""
    volume_root_env = os.environ.get("KENYA_EV_VOLUME_ROOT", "").strip()
    if not volume_root_env:
        return

    src = ROOT / "data" / "raw" / region_id / GEOCODE_FAILURES
    if not src.is_file():
        return

    dest_dir = Path(volume_root_env) / "_pipeline_logs" / region_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / GEOCODE_FAILURES
    shutil.copy2(src, dest)
    print(f"Persisted {GEOCODE_FAILURES} -> {dest}")


def publish_regions_index_if_configured(index_path: Path) -> None:
    volume_root_env = os.environ.get("KENYA_EV_VOLUME_ROOT", "").strip()
    if not volume_root_env:
        return

    dest = Path(volume_root_env) / REGIONS_INDEX
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(index_path, dest)
    print(f"Published {REGIONS_INDEX} -> {dest}")


def run_region(region_id: str) -> None:
    staging = ROOT / "data" / "staging" / region_id
    print(f"\n######## region={region_id} ########", flush=True)
    seed_staging(region_id, staging)
    for script in STEPS:
        run_step(script, staging, region_id)
    validate_staging(staging, region_id)
    promote_to_public(staging, region_id)
    publish_to_volume_if_configured(public_dir(region_id), region_id)


def main() -> int:
    failures: list[str] = []
    for region_id in REGIONS:
        try:
            run_region(region_id)
        except (RuntimeError, ValueError, OSError, KeyError) as error:
            print(f"\nRegion {region_id} FAILED: {error}", flush=True)
            failures.append(f"{region_id}: {error}")
        persist_region_diagnostics(region_id)

    index_path = write_regions_index()
    print(f"\nWrote regions index -> {index_path}")
    publish_regions_index_if_configured(index_path)

    if failures:
        print("\nPipeline finished with region failures:", flush=True)
        for item in failures:
            print(f"  - {item}", flush=True)
        print(f"Last-good public data left untouched for failed regions under {ROOT / 'data' / 'public'}")
        return 1

    print("Pipeline complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
