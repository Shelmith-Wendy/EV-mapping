#!/usr/bin/env python3
"""Databricks Job entry: run the Kenya EV pipeline and publish to a UC Volume.

Expected environment: serverless (or classic) Job task with outbound HTTPS for
Zeno + Nominatim, WRITE on the target Volume, and bundle sync of scripts/ + data/raw/.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from volume_contract import volume_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=os.environ.get("KENYA_EV_CATALOG", "main"))
    parser.add_argument("--schema", default=os.environ.get("KENYA_EV_SCHEMA", "zeno_ev_map"))
    parser.add_argument("--volume", default=os.environ.get("KENYA_EV_VOLUME", "public"))
    parser.add_argument(
        "--force-fetch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Re-download Zeno HTML instead of using synced data/raw cache (default: true)",
    )
    args = parser.parse_args()

    dest = volume_root(args.catalog, args.schema, args.volume)
    os.environ["KENYA_EV_VOLUME_ROOT"] = dest
    if args.force_fetch:
        os.environ["KENYA_EV_FORCE_FETCH"] = "1"

    print(f"Publishing contract to {dest}/")
    print(f"KENYA_EV_FORCE_FETCH={os.environ.get('KENYA_EV_FORCE_FETCH', '')}")

    from run_pipeline import main as pipeline_main

    return pipeline_main()


if __name__ == "__main__":
    sys.exit(main())
