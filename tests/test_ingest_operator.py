"""End-to-end tests for the single ingest_operator entry point."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.ingest_operator import ingest_operator, validate_records
from scripts.ingestion.config import ALL_COLUMNS


class TestIngestOperator(unittest.TestCase):
    """Test ingest_operator CLI/API workflow, validation errors, and atomic writing."""

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.synthetic_arc_path = Path(__file__).parent / "fixtures" / "synthetic_arcride.json"
        self.spiro_payload_path = self.repo_root / "reference" / "spiro_map_payload.json"

    def test_ingest_operator_arcride_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "arcride_current.csv"
            records, report, diff = ingest_operator(
                operator="arcride",
                input_path=self.synthetic_arc_path,
                output_path=out_file,
                retrieved_on="2026-10-23",
                base_dir=Path(tmpdir),
            )
            self.assertEqual(len(records), 2)
            self.assertTrue(out_file.is_file())

            # Read back generated CSV
            with open(out_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self.assertEqual(tuple(reader.fieldnames or []), ALL_COLUMNS)
                rows = list(reader)
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["operator"], "ARC Ride")

    def test_ingest_operator_spiro_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "spiro_current.csv"
            records, report, diff = ingest_operator(
                operator="spiro",
                input_path=self.spiro_payload_path,
                output_path=out_file,
                retrieved_on="2026-10-21",
                base_dir=Path(tmpdir),
            )
            self.assertEqual(len(records), 503)
            self.assertTrue(out_file.is_file())

            with open(out_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self.assertEqual(tuple(reader.fieldnames or []), ALL_COLUMNS)
                rows = list(reader)
                self.assertEqual(len(rows), 503)
                self.assertEqual(rows[0]["operator"], "Spiro")
                self.assertEqual(rows[0]["cabinet_count"], "")

    def test_spiro_live_fetch_forbidden(self):
        """Rule 2: Spiro network access is strictly prohibited."""
        with self.assertRaises(PermissionError) as ctx:
            ingest_operator(operator="spiro", live_fetch=True)
        self.assertIn("strictly forbidden", str(ctx.exception))

    def test_validation_errors(self):
        # Empty name
        with self.assertRaises(ValueError) as ctx:
            validate_records([{
                "operator": "ARC Ride",
                "name": "",
                "address": "Kilimani",
                "latitude": -1.29,
                "longitude": 36.79,
                "cabinet_count": 1,
                "source": "api",
                "retrieved_on": "2026-10-23",
            }], operator="ARC Ride")
        self.assertIn("empty station name", str(ctx.exception))

        # Spiro non-empty cabinet count
        with self.assertRaises(ValueError) as ctx:
            validate_records([{
                "operator": "Spiro",
                "name": "Station A",
                "address": "Kilimani",
                "latitude": -1.29,
                "longitude": 36.79,
                "cabinet_count": "1",  # must be empty string
                "source": "browser",
                "retrieved_on": "2026-10-23",
            }], operator="Spiro")
        self.assertIn("Spiro cabinet_count must be strictly empty string", str(ctx.exception))

        # ARC Ride zero cabinet count
        with self.assertRaises(ValueError) as ctx:
            validate_records([{
                "operator": "ARC Ride",
                "name": "Station B",
                "address": "Kilimani",
                "latitude": -1.29,
                "longitude": 36.79,
                "cabinet_count": 0,
                "source": "api",
                "retrieved_on": "2026-10-23",
            }], operator="ARC Ride")
        self.assertIn("cabinet_count must be >= 1", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
