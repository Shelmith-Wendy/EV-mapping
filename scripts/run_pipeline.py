#!/usr/bin/env python3
"""Run the full data pipeline into staging, validate, then promote to public.

Optional: set KENYA_EV_VOLUME_ROOT=/Volumes/<catalog>/<schema>/<volume> to also
publish the Volume contract files after a successful promote.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid

from common import CACHE_FILES, PIPELINE_STEPS, ROOT
from volume_contract import publish_public_dir, write_manifest

STAGING = ROOT / "data" / "staging"
PUBLIC = ROOT / "data" / "public"
SCRIPTS = ROOT / "scripts"

STEPS = list(PIPELINE_STEPS)


def seed_staging() -> None:
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)

    if not PUBLIC.is_dir():
        return

    for name in CACHE_FILES:
        path = PUBLIC / name
        if path.exists():
            shutil.copy2(path, STAGING / name)


def run_step(script: str) -> None:
    print(f"\n==> {script}")
    env = {**os.environ, "KENYA_EV_DATA_DIR": str(STAGING)}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{script} exited with code {result.returncode}")


def validate_staging() -> None:
    print("\n==> validate_publish.py")
    env = {**os.environ, "KENYA_EV_DATA_DIR": str(STAGING)}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_publish.py")],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("validation failed")


def promote_to_public() -> str:
    run_id = uuid.uuid4().hex
    tmp = PUBLIC.parent / f".public.{run_id}"
    shutil.copytree(STAGING, tmp)
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    tmp.rename(PUBLIC)
    write_manifest(PUBLIC, pipeline_run_id=run_id)
    print(f"\nPromoted staging -> {PUBLIC}")
    print(f"Wrote manifest pipeline_run_id={run_id}")
    return run_id


def publish_to_volume_if_configured() -> None:
    volume_root_env = os.environ.get("KENYA_EV_VOLUME_ROOT", "").strip()
    if not volume_root_env:
        print("\nKENYA_EV_VOLUME_ROOT unset — skipping Volume publish")
        return

    print(f"\n==> publish to {volume_root_env}")
    published = publish_public_dir(PUBLIC, volume_root_env)
    print(f"Published contract files to {published}/")


def main() -> int:
    try:
        seed_staging()
        for script in STEPS:
            run_step(script)
        validate_staging()
        promote_to_public()
        publish_to_volume_if_configured()
    except (RuntimeError, ValueError, OSError) as error:
        print(f"\nPipeline aborted: {error}")
        print(f"Check staging/public under {ROOT / 'data'}")
        return 1

    print("Pipeline complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
