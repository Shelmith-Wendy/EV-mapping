#!/usr/bin/env python3
"""CLI for the Phase 2 Unity Catalog Volume public-file contract.

Subcommands:
  write-manifest  Build data/public/manifest.json
  validate        Check contract files + manifest integrity
  publish         Validate then copy contract files to /Volumes/...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import ROOT
from volume_contract import (
    contract_files,
    publish_public_dir,
    region_id_from_public_dir,
    validate_public_dir,
    volume_root,
    write_manifest,
)

DEFAULT_CATALOG = "main"
DEFAULT_SCHEMA = "zeno_ev_map"
DEFAULT_VOLUME = "public"
DEFAULT_PUBLIC = ROOT / "data" / "public" / "kenya"


def _files_for(public_dir: Path) -> tuple[str, ...]:
    return contract_files(region_id_from_public_dir(public_dir))


def _add_public_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--public-dir",
        type=Path,
        default=DEFAULT_PUBLIC,
        help="Directory with dashboard artifacts (default: data/public/kenya)",
    )


def _add_volume_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--volume", default=DEFAULT_VOLUME)


def cmd_write_manifest(args: argparse.Namespace) -> int:
    try:
        path = write_manifest(
            args.public_dir,
            pipeline_run_id=args.pipeline_run_id,
            region_id=region_id_from_public_dir(args.public_dir),
        )
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Wrote {path}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    region_id = region_id_from_public_dir(args.public_dir)
    files = _files_for(args.public_dir)
    problems = validate_public_dir(args.public_dir, region_id=region_id)
    target = volume_root(args.catalog, args.schema, args.volume)
    print(f"Contract target: {target}/")
    print(f"Required files: {', '.join(files)}")
    print(f"Checked: {args.public_dir}")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    print("OK: volume public contract satisfied")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    region_id = region_id_from_public_dir(args.public_dir)
    files = _files_for(args.public_dir)
    dest = args.volume_root or volume_root(args.catalog, args.schema, args.volume)
    problems = validate_public_dir(args.public_dir, region_id=region_id)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    dest_display = f"{dest}/{region_id}" if region_id else dest
    print(f"Source: {args.public_dir}")
    print(f"Dest:   {dest_display}/")
    print(f"Files:  {', '.join(files)}")

    if args.dry_run:
        print("Dry run: contract OK, skipping publish")
        return 0

    try:
        published = publish_public_dir(args.public_dir, dest, region_id=region_id)
    except OSError as error:
        print(
            f"Cannot write {dest} ({error}). "
            "On a laptop use ./scripts/upload_public_to_volume.sh instead.",
            file=sys.stderr,
        )
        return 1
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    print(f"Published to {published}/")
    for name in files:
        path = Path(published) / name
        print(f"  {name} ({path.stat().st_size} bytes)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_write = sub.add_parser("write-manifest", help="Write manifest.json for the contract")
    _add_public_dir(p_write)
    p_write.add_argument("--pipeline-run-id", default=None)
    p_write.set_defaults(func=cmd_write_manifest)

    p_validate = sub.add_parser("validate", help="Validate local contract directory")
    _add_public_dir(p_validate)
    _add_volume_target(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    p_publish = sub.add_parser("publish", help="Validate and publish contract to a Volume path")
    _add_public_dir(p_publish)
    _add_volume_target(p_publish)
    p_publish.add_argument(
        "--volume-root",
        default=None,
        help="Override destination (default: /Volumes/<catalog>/<schema>/<volume>)",
    )
    p_publish.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only; do not write to the Volume",
    )
    p_publish.set_defaults(func=cmd_publish)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
