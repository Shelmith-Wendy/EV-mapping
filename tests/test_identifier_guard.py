"""Device identifier guard tests enforcing GEMINI.md Hard Rule 1.

Verifies that no output, file, log, or report contains device identifiers
(such as IMEI, serial numbers, device IDs, or MAC addresses).
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from scripts.ingest_operator import GateFailureError, ingest_operator
from scripts.ingestion.adapters.arcride import adapt_arcride, make_cabinet_token, redact_arcride_payload
from scripts.ingestion.adapters.spiro import adapt_spiro
from scripts.ingestion.config import FORBIDDEN_IDENTIFIER_COLUMNS, IngestionConfig
from scripts.ingestion.core import IngestionReport, validate_records

# Prohibited key patterns that indicate device identifiers
FORBIDDEN_KEY_PATTERNS = re.compile(
    r"(^|_)(imei|serial_number|serial_no|device_id|unit_id|unit_key|mac_address|hardware_id)($|_)",
    re.IGNORECASE,
)

# Value patterns that look like real device identifiers (e.g. 15-digit IMEI or standard MAC)
IMEI_VALUE_PATTERN = re.compile(r"^\d{14,16}$")
MAC_VALUE_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")


def assert_no_device_identifiers(data: Any, context: str = "") -> None:
    """Recursively inspect data structures and strings to ensure no device identifiers exist."""
    if isinstance(data, dict):
        for key, value in data.items():
            key_str = str(key)
            if FORBIDDEN_KEY_PATTERNS.search(key_str):
                raise AssertionError(
                    f"Device identifier key detected in {context}: {key_str!r}"
                )
            assert_no_device_identifiers(value, context=f"{context}.{key_str}")

    elif isinstance(data, (list, tuple, set)):
        for idx, item in enumerate(data):
            assert_no_device_identifiers(item, context=f"{context}[{idx}]")

    elif isinstance(data, str):
        # Check string values for sensitive hardware identifier formats
        trimmed = data.strip()
        if IMEI_VALUE_PATTERN.match(trimmed):
            raise AssertionError(
                f"Value resembles a hardware IMEI in {context}: {trimmed!r}"
            )
        if MAC_VALUE_PATTERN.match(trimmed):
            raise AssertionError(
                f"Value resembles a MAC address in {context}: {trimmed!r}"
            )


def audit_path_for_identifiers(target_path: Path) -> int:
    """Audit all CSV and JSON files under target_path using assert_no_device_identifiers.

    Returns the count of audited files.
    """
    checked = 0
    if not target_path.exists():
        return 0
    paths = [target_path] if target_path.is_file() else list(target_path.rglob("*"))
    for fpath in paths:
        if not fpath.is_file():
            continue
        if fpath.suffix.lower() == ".csv":
            with open(fpath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for col in reader.fieldnames or []:
                    if FORBIDDEN_KEY_PATTERNS.search(col):
                        raise AssertionError(
                            f"Forbidden identifier column {col!r} found in {fpath}"
                        )
                for row in reader:
                    assert_no_device_identifiers(row, context=str(fpath))
            checked += 1
        elif fpath.suffix.lower() == ".json":
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert_no_device_identifiers(data, context=str(fpath))
            checked += 1
    return checked


class TestIdentifierGuard(unittest.TestCase):
    """Test that adapter outputs and logs pass the identifier guard, and that the guard catches leaks."""

    def test_arcride_adapter_output_is_clean(self):
        fixture_path = Path(__file__).parent / "fixtures" / "synthetic_arcride.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            raw_payload = json.load(f)

        report = IngestionReport()
        sites = adapt_arcride(raw_payload, report=report)

        # Output dictionaries must not contain any device identifier keys or values
        assert_no_device_identifiers(sites, context="arcride_sites")

        # Report text must not contain device identifiers
        summary = report.summary_text()
        self.assertNotIn("imei", summary.lower())
        self.assertNotIn("serial", summary.lower())

    def test_spiro_adapter_output_is_clean(self):
        repo_root = Path(__file__).resolve().parent.parent
        payload_path = repo_root / "reference" / "spiro_map_payload.json"
        report = IngestionReport()
        sites = adapt_spiro(payload_path, retrieved_on="2026-10-21", report=report)

        assert_no_device_identifiers(sites, context="spiro_sites")
        summary = report.summary_text()
        self.assertNotIn("imei", summary.lower())
        self.assertNotIn("serial", summary.lower())

    def test_guard_fails_on_forbidden_keys(self):
        """Guard must raise AssertionError if a device identifier key is present."""
        leak_cases = [
            {"operator": "ARC Ride", "imei_number": "1234"},
            {"operator": "ARC Ride", "device_id": "ABC-99"},
            {"operator": "ARC Ride", "serial_number": "SN-001"},
            {"operator": "ARC Ride", "unit_id": "UNIT-7"},
        ]
        for leaky_dict in leak_cases:
            with self.subTest(leaky_dict=leaky_dict):
                with self.assertRaises(AssertionError) as ctx:
                    assert_no_device_identifiers(leaky_dict)
                self.assertIn("Device identifier key detected", str(ctx.exception))

    def test_guard_fails_on_identifier_like_values(self):
        """Guard must raise AssertionError if a value looks like an IMEI or MAC address."""
        leaky_values = [
            {"operator": "ARC Ride", "name": "354892091234567"},  # 15-digit IMEI in value
            {"operator": "ARC Ride", "address": "00:1A:2B:3C:4D:5E"},  # MAC address in value
        ]
        for leaky_record in leaky_values:
            with self.subTest(leaky_record=leaky_record):
                with self.assertRaises(AssertionError) as ctx:
                    assert_no_device_identifiers(leaky_record)
                self.assertIn("Value resembles a", str(ctx.exception))

    def test_validate_records_fails_when_identifier_column_introduced(self):
        """Pipeline schema validation must fail loudly if an identifier column is introduced."""
        base_record = {
            "operator": "ARC Ride",
            "name": "Clean Station",
            "address": "Kileleshwa",
            "latitude": -1.28,
            "longitude": 36.80,
            "cabinet_count": 2,
            "source": "https://arcrideglobal.com/api/cabinets",
            "retrieved_on": "2026-10-23",
        }
        for forbidden_col in sorted(FORBIDDEN_IDENTIFIER_COLUMNS):
            with self.subTest(forbidden_col=forbidden_col):
                leaky_record = dict(base_record)
                leaky_record[forbidden_col] = "ID-12345"
                with self.assertRaises(ValueError) as ctx:
                    validate_records([leaky_record], operator="ARC Ride")
                self.assertIn("contains forbidden device identifier column", str(ctx.exception))

    def test_persisted_datasets_contain_no_identifiers(self):
        """All persisted CSV and JSON files in data/ (including inbox and archive) must be completely free of identifiers."""
        repo_root = Path(__file__).resolve().parent.parent
        target_dirs = [
            repo_root / "data" / "inbox",
            repo_root / "data" / "archive",
            repo_root / "data" / "current",
            repo_root / "data" / "snapshots",
            repo_root / "data" / "public" / "kenya_swap",
            repo_root / "data" / "verification",
        ]
        checked_count = 0
        for tdir in target_dirs:
            checked_count += audit_path_for_identifiers(tdir)

        self.assertGreater(checked_count, 0, "Expected to audit at least one persisted dataset file.")

    def test_raw_arc_file_in_inbox_or_archive_fails_guard(self):
        """Guard must fail loudly if an unredacted raw ARC file exists in inbox or archive."""
        raw_payload = {
            "results": [
                {
                    "name": "Leaky Station",
                    "address": "Leaky Road",
                    "lat": -1.290,
                    "lng": 36.790,
                    "imei_number": "synthetic-dev-raw",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir = Path(tmpdir) / "data" / "inbox" / "arcride"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            raw_inbox_file = inbox_dir / "raw_cabinets.json"
            with open(raw_inbox_file, "w", encoding="utf-8") as f:
                json.dump(raw_payload, f)

            with self.assertRaises(AssertionError) as ctx:
                audit_path_for_identifiers(inbox_dir)
            self.assertIn("Device identifier key detected", str(ctx.exception))

            archive_dir = Path(tmpdir) / "data" / "archive" / "arcride"
            archive_dir.mkdir(parents=True, exist_ok=True)
            raw_archive_file = archive_dir / "raw_cabinets.json"
            with open(raw_archive_file, "w", encoding="utf-8") as f:
                json.dump(raw_payload, f)

            with self.assertRaises(AssertionError) as ctx:
                audit_path_for_identifiers(archive_dir)
            self.assertIn("Device identifier key detected", str(ctx.exception))

    def test_ingest_operator_redacts_arcride_inbox_and_archive_and_passes_guard(self):
        """Successful ARC Ride ingest archives redacted copy, replaces inbox file, and passes guard."""
        raw_payload = {
            "results": [
                {
                    "name": "Solar Hub",
                    "address": "Kilimani",
                    "lat": -1.29000,
                    "lng": 36.79000,
                    "imei_number": "synthetic-dev-001",
                },
                {
                    "name": "Solar Hub",
                    "address": "Kilimani",
                    "lat": -1.29005,
                    "lng": 36.79005,
                    "imei_number": "synthetic-dev-002",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            inbox_dir = base_dir / "data" / "inbox" / "arcride"
            archive_dir = base_dir / "data" / "archive" / "arcride"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            inbox_file = inbox_dir / "2026-10-23_cabinets.json"
            with open(inbox_file, "w", encoding="utf-8") as f:
                json.dump(raw_payload, f)

            # Before run: guard fails on raw inbox file
            with self.assertRaises(AssertionError):
                audit_path_for_identifiers(inbox_dir)

            # Execute ingestion
            records, report, diff = ingest_operator(
                operator="arcride",
                input_path=inbox_file,
                retrieved_on="2026-10-23",
                base_dir=base_dir,
            )

            # Assert run succeeded and clustered 2 distinct cabinets
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["cabinet_count"], 2)

            # Check that archive file exists
            archive_file = archive_dir / "2026-10-23_cabinets.json"
            self.assertTrue(archive_file.is_file())

            # Read both files and verify they are redacted
            for checked_file in (inbox_file, archive_file):
                with open(checked_file, "r", encoding="utf-8") as f:
                    content = json.load(f)
                for rec in content["results"]:
                    self.assertNotIn("imei_number", rec)
                    self.assertNotIn("serial_number", rec)
                    self.assertIn("cabinet_token", rec)
                    self.assertTrue(rec["cabinet_token"].startswith("cab_"))

            # Guard passes on both inbox and archive
            checked_count = audit_path_for_identifiers(base_dir / "data" / "inbox")
            self.assertGreater(checked_count, 0)
            checked_archive = audit_path_for_identifiers(base_dir / "data" / "archive")
            self.assertGreater(checked_archive, 0)

    def test_failed_run_preserves_raw_inbox_file_causing_guard_failure(self):
        """Failed run preserves original inbox file and fails identifier guard because raw file survives."""
        # 1 valid station incoming
        raw_payload = {
            "results": [
                {
                    "name": "One Site",
                    "address": "Kilimani",
                    "lat": -1.290,
                    "lng": 36.790,
                    "imei_number": "synthetic-dev-unredacted",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            inbox_dir = base_dir / "data" / "inbox" / "arcride"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            inbox_file = inbox_dir / "failing_cabinets.json"
            with open(inbox_file, "w", encoding="utf-8") as f:
                json.dump(raw_payload, f)

            # Setup previous snapshot with 10 stations (incoming 1 is 90% drop -> blocks)
            snapshots_dir = base_dir / "data" / "snapshots" / "arcride"
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            prev_snapshot = snapshots_dir / "2026-10-20_stations.csv"
            with open(prev_snapshot, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["station_id", "operator", "name", "address", "latitude", "longitude", "cabinet_count", "source", "retrieved_on", "is_removed"])
                for i in range(10):
                    writer.writerow([f"ARC_{i}", "ARC Ride", f"Site {i}", "Addr", -1.29, 36.79, 1, "api", "2026-10-20", "False"])

            # Ingest should block on safety gate
            with self.assertRaises(GateFailureError):
                ingest_operator(
                    operator="arcride",
                    input_path=inbox_file,
                    retrieved_on="2026-10-23",
                    base_dir=base_dir,
                )

            # Archive file was NOT created
            archive_file = base_dir / "data" / "archive" / "arcride" / "failing_cabinets.json"
            self.assertFalse(archive_file.exists())

            # Original inbox file was preserved with original raw contents
            with open(inbox_file, "r", encoding="utf-8") as f:
                preserved_data = json.load(f)
            self.assertIn("imei_number", preserved_data["results"][0])

            # The raw ARC file survived the run, so the identifier guard MUST fail!
            with self.assertRaises(AssertionError) as ctx:
                audit_path_for_identifiers(inbox_dir)
            self.assertIn("Device identifier key detected", str(ctx.exception))

    def test_live_fetch_saves_only_redacted_copy(self):
        """Manual ARC Ride live fetch saves only redacted copy to disk."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {
            "results": [
                {
                    "name": "Live Station",
                    "address": "Live Addr",
                    "lat": -1.290,
                    "lng": 36.790,
                    "imei_number": "synthetic-live-dev",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            cooldown_file = base_dir / ".cooldown"
            config = IngestionConfig(contact_email="test@example.com")

            with patch("requests.get", return_value=mock_response):
                records, report, diff = ingest_operator(
                    operator="arcride",
                    live_fetch=True,
                    retrieved_on="2026-10-23",
                    base_dir=base_dir,
                    config=config,
                )

            self.assertEqual(len(records), 1)

            # Check that both archive and inbox files exist and are clean
            archive_file = base_dir / "data" / "archive" / "arcride" / "2026-10-23_cabinets.json"
            inbox_file = base_dir / "data" / "inbox" / "arcride" / "2026-10-23_cabinets.json"
            self.assertTrue(archive_file.is_file())
            self.assertTrue(inbox_file.is_file())

            for target_file in (archive_file, inbox_file):
                with open(target_file, "r", encoding="utf-8") as f:
                    content = json.load(f)
                for rec in content["results"]:
                    self.assertNotIn("imei_number", rec)
                    self.assertIn("cabinet_token", rec)

            # Audit entire data dir in tmpdir - no identifiers anywhere
            audit_path_for_identifiers(base_dir / "data")

    def test_spiro_raw_file_archived_as_is(self):
        """Spiro files contain no identifiers and are archived as they are."""
        repo_root = Path(__file__).resolve().parent.parent
        spiro_ref = repo_root / "reference" / "spiro_map_payload.json"
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            inbox_dir = base_dir / "data" / "inbox" / "spiro"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            inbox_file = inbox_dir / "2026-10-21_spiro_payload.json"
            shutil.copy2(spiro_ref, inbox_file)

            records, report, diff = ingest_operator(
                operator="spiro",
                input_path=inbox_file,
                retrieved_on="2026-10-21",
                base_dir=base_dir,
            )
            self.assertEqual(len(records), 503)

            # Verify archive copy exists and is identical
            archive_file = base_dir / "data" / "archive" / "spiro" / "2026-10-21_spiro_payload.json"
            self.assertTrue(archive_file.is_file())
            self.assertEqual(inbox_file.read_bytes(), archive_file.read_bytes())

            # Guard passes on both inbox and archive
            audit_path_for_identifiers(base_dir / "data" / "inbox")
            audit_path_for_identifiers(base_dir / "data" / "archive")


if __name__ == "__main__":
    unittest.main()
