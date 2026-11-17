"""Single unified entry point for operator station ingestion.

Performs:
1. Adapt input payload.
2. Load previous accepted snapshot for operator.
3. Apply safety gates from config (with --force bypass option).
4. Correlate with previous snapshot to assign stable IDs and detect new, moved, removed stations.
5. Apply human verification overlay (read-only, verified coordinates win).
6. Detect cross-operator co-location within configurable threshold.
7. Atomically write dated snapshot and update current datasets.
8. Generate comprehensive run report.
"""

from __future__ import annotations

import argparse
import datetime
import secrets
import shutil
import sys
from pathlib import Path

# Ensure repository root is on sys.path for direct CLI script execution
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from typing import Any

from scripts.ingestion.adapters.arcride import (
    adapt_arcride,
    fetch_arcride_cabinets,
    redact_arcride_payload,
)
from scripts.ingestion.adapters.spiro import adapt_spiro
from scripts.ingestion.config import (
    ALL_COLUMNS,
    IngestionConfig,
    load_config,
    normalize_operator_name,
)
from scripts.ingestion.core import (
    IngestionReport,
    atomic_write_csv,
    atomic_write_json,
    validate_records,
)
from scripts.ingestion.matching import (
    RegistryDiff,
    apply_verification_overlay,
    detect_colocation,
    evaluate_safety_gates,
    match_and_update_registry,
)
from scripts.ingestion.storage import (
    get_archive_dir,
    get_inbox_dir,
    get_verification_overlay_path,
    is_inbox_path,
    load_latest_snapshot,
    update_current_dataset,
    update_merged_dataset,
    write_dated_snapshot,
)


class GateFailureError(RuntimeError):
    """Raised when safety gate thresholds block an ingestion run."""
    pass


def format_full_run_report(
    operator: str,
    run_date: str,
    execution_time: str,
    report: IngestionReport,
    diff: RegistryDiff,
    total_registry_count: int,
    active_count: int,
    removed_count: int,
    colocated_count: int,
    gate_status: str,
    warnings: list[str],
    archival_status: str | None = None,
) -> str:
    """Format comprehensive human-readable run report."""
    lines = [
        "=" * 80,
        "EV-MAPPING INGESTION & REGISTRY REPORT",
        "=" * 80,
        f"Operator            : {operator}",
        f"As-of Date          : {run_date}",
        f"Execution Timestamp : {execution_time}",
        f"Gate Status         : {gate_status}",
    ]

    if warnings:
        lines.append("Warnings / Alerts   :")
        for w in warnings:
            lines.append(f"  * {w}")

    lines.extend([
        "",
        "-- Adapter Parsing & Normalization --",
        f"Total Raw Inputs    : {report.total_input}",
        f"Kept Valid Records  : {report.kept_count}",
        f"Dropped Records     : {report.total_dropped}",
    ])

    if report.dropped_by_reason:
        lines.append("Drop Breakdown by Reason:")
        for reason, count in sorted(report.dropped_by_reason.items()):
            lines.append(f"  - {reason}: {count}")

    if report.outside_kenya_dropped:
        lines.append("Records Dropped Outside Kenya (at most 20):")
        for rec in report.outside_kenya_dropped[:20]:
            lines.append(f"  ! {rec['name']} ({rec['latitude']:.4f}, {rec['longitude']:.4f})")
        if len(report.outside_kenya_dropped) > 20:
            lines.append(f"  ... and {len(report.outside_kenya_dropped) - 20} more")

    lines.extend([
        "",
        "-- Registry & Lifecycle Status --",
        f"Active Stations     : {active_count}",
        f"Removed Stations    : {removed_count}",
        f"Total In Registry   : {total_registry_count}",
        f"Co-Located Stations : {colocated_count}",
        "",
        "-- Snapshot Differences vs Previous --",
        f"New Stations        : {diff.new_count}",
    ])

    for s in diff.new_stations:
        lines.append(
            f"  + [{s['station_id']}] {s['name']} ({s['latitude']:.4f}, {s['longitude']:.4f})"
        )

    lines.append(f"Moved Stations      : {diff.moved_count}")
    for s in diff.moved_stations:
        old_lat = s.get("old_latitude", 0.0)
        old_lng = s.get("old_longitude", 0.0)
        dist = s.get("distance_moved", 0.0)
        lines.append(
            f"  ~ [{s['station_id']}] {s['name']}: ({old_lat:.4f}, {old_lng:.4f}) -> "
            f"({s['latitude']:.4f}, {s['longitude']:.4f}) [{dist:.1f}m]"
        )

    lines.append(f"Removed Stations    : {diff.removed_count}")
    for s in diff.removed_stations:
        lines.append(
            f"  - [{s['station_id']}] {s['name']} (last seen: {s.get('last_seen', '')})"
        )

    lines.append(f"Unchanged Stations  : {diff.unchanged_count}")

    if archival_status:
        lines.extend([
            "",
            "-- Identifier Hygiene & Archival --",
            f"Archival Status     : {archival_status}",
        ])

    lines.append("=" * 80)
    return "\n".join(lines)


def ingest_operator(
    operator: str,
    input_path: str | Path | None = None,
    output_path: str | Path | None = None,
    retrieved_on: str | None = None,
    live_fetch: bool = False,
    force: bool = False,
    config: IngestionConfig | None = None,
    base_dir: Path | None = None,
    overlay_path: Path | str | None = None,
) -> tuple[list[dict[str, Any]], IngestionReport, RegistryDiff]:
    """Execute end-to-end repeatable ingestion pipeline.

    Flow:
    1. Adapt input payload.
    2. Load previous accepted snapshot for operator.
    3. Evaluate safety gates (halts if thresholds breached, unless force=True).
    4. Match against previous snapshot to track new, moved, removed stations.
    5. Apply human verification overlay (read-only, verified coordinates win).
    6. Flag cross-operator co-location within configurable radius.
    7. Atomically write dated snapshot and update current dataset(s).
    8. Print comprehensive run report.

    Returns:
        tuple of (final_registry_records, ingestion_report, registry_diff)
    """
    canonical_operator = normalize_operator_name(operator)
    cfg = config or load_config()
    report = IngestionReport()
    execution_time = datetime.datetime.now().isoformat(timespec="seconds")
    run_date = retrieved_on or datetime.date.today().isoformat()

    inbox_file_to_preserve: Path | None = None
    redacted_arc_payload: Any = None
    src_file: Path | None = None
    archival_status: str | None = None

    if input_path is not None:
        src_file = Path(input_path).resolve()
        if not src_file.is_file():
            raise FileNotFoundError(f"Input file not found: {src_file}")
        if is_inbox_path(src_file, base_dir=base_dir):
            inbox_file_to_preserve = src_file

    try:
        # Step 1: Adapt input payload
        if canonical_operator == "ARC Ride":
            if live_fetch:
                archive_dir = get_archive_dir("ARC Ride", base_dir=base_dir)
                inbox_dir = get_inbox_dir("ARC Ride", base_dir=base_dir)
                archive_file = archive_dir / f"{run_date}_cabinets.json"
                inbox_file = inbox_dir / f"{run_date}_cabinets.json"
                cooldown_file = inbox_dir / ".last_fetch_timestamp"

                # fetch_arcride_cabinets applies per-run salt redaction before saving/returning
                raw_data = fetch_arcride_cabinets(
                    config=cfg,
                    save_path=archive_file,
                    cooldown_path=cooldown_file,
                    force=force,
                )
                atomic_write_json(inbox_file, {"results": raw_data})
                archival_status = (
                    f"Saved redacted copy to {archive_file.name} in archive and inbox "
                    f"(0 device identifiers reach disk)."
                )
                adapted_sites = adapt_arcride(raw_data, retrieved_on=run_date, report=report)
            else:
                if src_file is None:
                    raise ValueError("Must specify --input path when --live-fetch is not used for ARC Ride.")
                if src_file.suffix.lower() == ".csv":
                    import csv
                    with open(src_file, "r", encoding="utf-8") as f:
                        raw_data = list(csv.DictReader(f))
                else:
                    import json
                    with open(src_file, "r", encoding="utf-8-sig") as f:
                        raw_data = json.load(f)

                # Redact payload with one-way token using a random per-run salt in memory only
                per_run_salt = secrets.token_hex(16)
                redacted_arc_payload, _ = redact_arcride_payload(raw_data, salt=per_run_salt)
                adapted_sites = adapt_arcride(redacted_arc_payload, retrieved_on=run_date, report=report)

        elif canonical_operator == "Spiro":
            if live_fetch:
                raise PermissionError(
                    "Network access is strictly forbidden for Spiro per GEMINI.md Rule 2. Use manual input file."
                )
            if src_file is None:
                raise ValueError("Must specify --input path for Spiro ingestion.")

            adapted_sites = adapt_spiro(src_file, retrieved_on=run_date, report=report)
        else:
            raise ValueError(f"Unsupported operator: {canonical_operator}")

        # Validate adapted base fields
        validate_records(adapted_sites, canonical_operator, require_registry=False)

        # Step 2: Load previous accepted snapshot
        previous_registry, prev_snapshot_path = load_latest_snapshot(
            canonical_operator, base_dir=base_dir
        )
        previous_active_count = sum(
            1 for r in previous_registry
            if not str(r.get("is_removed", "")).strip().lower() in ("true", "1")
        )

        # Step 3: Evaluate Safety Gates
        is_blocked, block_reason, gate_warnings = evaluate_safety_gates(
            incoming_count=len(adapted_sites),
            previous_active_count=previous_active_count,
            config=cfg,
            force=force,
        )

        if is_blocked:
            # Output failure report and halt without touching any current files
            failure_report = (
                f"SAFETY GATE BLOCKED: {block_reason}\n"
                f"Run aborted. Existing datasets left untouched.\n"
                f"Supply --force if this drop is intentional."
            )
            if inbox_file_to_preserve:
                failure_report += f"\nOriginal inbox file preserved at {inbox_file_to_preserve}."
            print(failure_report, file=sys.stderr)
            raise GateFailureError(block_reason)

        gate_status = "WARNING" if gate_warnings else "PASSED"

        # Cross-operator awareness: load peer snapshot to prevent cross-operator ID collision
        peer_operator = "Spiro" if canonical_operator == "ARC Ride" else "ARC Ride"
        peer_records, _ = load_latest_snapshot(peer_operator, base_dir=base_dir)
        peer_ids = {str(r.get("station_id")).strip() for r in peer_records if r.get("station_id")}

        # Step 4: Registry matching & stable ID assignment
        registry_records, diff = match_and_update_registry(
            incoming_sites=adapted_sites,
            previous_registry=previous_registry,
            run_date=run_date,
            merge_distance_meters=cfg.merge_distance_meters,
            moved_distance_meters=cfg.moved_distance_meters,
            peer_ids=peer_ids,
        )

        # Step 5: Apply human verification overlay (read-only)
        resolved_overlay_path = overlay_path or get_verification_overlay_path(base_dir=base_dir)
        registry_records = apply_verification_overlay(
            registry_records,
            overlay_path=resolved_overlay_path,
        )

        # Step 6: Cross-operator co-location detection
        if peer_records:
            detect_colocation(
                stations_a=registry_records,
                stations_b=peer_records,
                colocation_distance_meters=cfg.colocation_distance_meters,
            )
            # Atomically re-save peer current dataset with updated co-location tags
            update_current_dataset(peer_operator, peer_records, base_dir=base_dir)

        # Validate final registry schema
        validate_records(registry_records, canonical_operator, require_registry=True)

        # Step 7: Atomic file writing
        # 7a. Dated accepted snapshot
        dated_snapshot_file = write_dated_snapshot(
            canonical_operator,
            registry_records,
            run_date=run_date,
            base_dir=base_dir,
        )

        # 7b. Current operator dataset
        if output_path is not None:
            atomic_write_csv(output_path, registry_records, fieldnames=ALL_COLUMNS)
        else:
            update_current_dataset(canonical_operator, registry_records, base_dir=base_dir)

        # 7c. Merged multi-operator dataset
        all_current_datasets = [registry_records]
        if peer_records:
            all_current_datasets.append(peer_records)
        update_merged_dataset(all_current_datasets, base_dir=base_dir)

        # Step 7d: Archival and inbox hygiene
        if canonical_operator == "ARC Ride" and not live_fetch and src_file is not None:
            archive_dir = get_archive_dir("ARC Ride", base_dir=base_dir)
            archive_path = archive_dir / src_file.name
            if src_file.suffix.lower() == ".csv":
                if isinstance(redacted_arc_payload, list) and redacted_arc_payload:
                    fieldnames = list(redacted_arc_payload[0].keys())
                    atomic_write_csv(archive_path, redacted_arc_payload, fieldnames=fieldnames)
                    if inbox_file_to_preserve:
                        atomic_write_csv(src_file, redacted_arc_payload, fieldnames=fieldnames)
            else:
                atomic_write_json(archive_path, redacted_arc_payload)
                if inbox_file_to_preserve:
                    atomic_write_json(src_file, redacted_arc_payload)

            if inbox_file_to_preserve:
                archival_status = (
                    f"Archived redacted copy to {archive_path.name}; "
                    f"replaced original inbox file with redacted copy (0 device identifiers on disk)."
                )
            else:
                archival_status = (
                    f"Archived redacted copy to {archive_path.name} (0 device identifiers on disk)."
                )

        elif canonical_operator == "Spiro" and src_file is not None:
            archive_dir = get_archive_dir("Spiro", base_dir=base_dir)
            archive_path = archive_dir / src_file.name
            shutil.copy2(src_file, archive_path)
            archival_status = (
                f"Archived Spiro source payload as-is to {archive_path.name}."
            )

    except Exception:
        if inbox_file_to_preserve:
            sys.stderr.write(
                f"Run failed. Original inbox file preserved at {inbox_file_to_preserve}.\n"
            )
        raise

    # Step 8: Comprehensive Run Report
    active_count = sum(
        1 for r in registry_records
        if not str(r.get("is_removed", "")).strip().lower() in ("true", "1")
    )
    removed_count = sum(
        1 for r in registry_records
        if str(r.get("is_removed", "")).strip().lower() in ("true", "1")
    )
    colocated_count = sum(
        1 for r in registry_records
        if str(r.get("co_located", "")).strip().lower() in ("true", "1")
    )

    full_report = format_full_run_report(
        operator=canonical_operator,
        run_date=run_date,
        execution_time=execution_time,
        report=report,
        diff=diff,
        total_registry_count=len(registry_records),
        active_count=active_count,
        removed_count=removed_count,
        colocated_count=colocated_count,
        gate_status=gate_status,
        warnings=gate_warnings,
        archival_status=archival_status,
    )
    print(full_report)
    print(f"Accepted snapshot written to {dated_snapshot_file}")

    return registry_records, report, diff


def main() -> None:
    """Command-line interface entry point."""
    parser = argparse.ArgumentParser(
        description="Ingest EV battery-swap station data for an operator with registry & gate controls."
    )
    parser.add_argument(
        "--operator",
        required=True,
        help="Operator name ('arcride' or 'spiro')",
    )
    parser.add_argument(
        "--input",
        dest="input_path",
        help="Path to the input JSON file (required for Spiro; required for ARC Ride if not live-fetching)",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        help="Optional path to write the output CSV",
    )
    parser.add_argument(
        "--retrieved-on",
        dest="retrieved_on",
        help="Date string (YYYY-MM-DD) for retrieved_on column",
    )
    parser.add_argument(
        "--live-fetch",
        action="store_true",
        help="Execute single-shot network fetch (ARC Ride only; forbidden for Spiro)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force execution even if safety gates (e.g. >40%% station drop) trigger a block",
    )
    parser.add_argument(
        "--overlay",
        dest="overlay_path",
        help="Path to human verification overlay CSV",
    )

    args = parser.parse_args()

    try:
        ingest_operator(
            operator=args.operator,
            input_path=args.input_path,
            output_path=args.output_path,
            retrieved_on=args.retrieved_on,
            live_fetch=args.live_fetch,
            force=args.force,
            overlay_path=args.overlay_path,
        )
    except GateFailureError as err:
        sys.stderr.write(f"Pipeline stopped by safety gate: {err}\n")
        sys.exit(2)
    except Exception as err:
        sys.stderr.write(f"Ingestion failed: {err}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
