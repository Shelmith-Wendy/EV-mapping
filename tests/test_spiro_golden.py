"""Golden-file regression test for the Spiro adapter.

Verifies that the adapter exactly reproduces the existing reference/spiro_stations.csv
from reference/spiro_map_payload.json.
"""

from __future__ import annotations

import csv
import unittest
from pathlib import Path

from scripts.ingestion.adapters.spiro import adapt_spiro
from scripts.ingestion.config import BASE_COLUMNS
from scripts.ingestion.core import IngestionReport


class TestSpiroGolden(unittest.TestCase):
    """Verify Spiro adapter output against the accepted reference CSV snapshot."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.payload_path = cls.repo_root / "reference" / "spiro_map_payload.json"
        cls.reference_csv_path = cls.repo_root / "reference" / "spiro_stations.csv"

        if not cls.payload_path.is_file() or not cls.reference_csv_path.is_file():
            raise unittest.SkipTest(
                f"Skipping Spiro golden test: local-only reference files absent "
                f"({cls.payload_path.name} or {cls.reference_csv_path.name} not found)."
            )

        with open(cls.reference_csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            cls.reference_fieldnames = tuple(reader.fieldnames or [])
            cls.reference_rows = list(reader)

    def test_reproduces_existing_spiro_csv_exactly(self):
        report = IngestionReport()
        # The reference CSV used retrieved_on = 2026-10-21
        adapted_records = adapt_spiro(
            self.payload_path,
            retrieved_on="2026-10-21",
            report=report,
        )

        # 1. Header match
        self.assertEqual(self.reference_fieldnames, BASE_COLUMNS)

        # 2. Row count match: exactly 503 Kenyan swap stations
        self.assertEqual(
            len(adapted_records),
            len(self.reference_rows),
            f"Row count mismatch: adapted {len(adapted_records)} vs reference {len(self.reference_rows)}",
        )
        self.assertEqual(len(adapted_records), 503)

        # 3. Exact field-by-field equality across all 503 rows
        mismatches: list[str] = []
        for row_idx, (adapted, ref) in enumerate(zip(adapted_records, self.reference_rows)):
            for col in BASE_COLUMNS:
                adapted_val = str(adapted.get(col, ""))
                ref_val = str(ref.get(col, ""))
                if adapted_val != ref_val:
                    mismatches.append(
                        f"Row {row_idx} col {col!r}: adapted {adapted_val!r} != reference {ref_val!r}"
                    )

        self.assertEqual(
            len(mismatches),
            0,
            f"Encountered {len(mismatches)} discrepancies against reference CSV:\n"
            + "\n".join(mismatches[:10]),
        )

        # 4. Confirm cabinet_count is strictly blank for all records
        for site in adapted_records:
            self.assertEqual(site["cabinet_count"], "")

        # 5. Confirm report metrics exactly matching reference/parse_spiro.py
        self.assertEqual(report.kept_count, 503)
        self.assertEqual(report.dropped_by_reason["outside_kenya"], 1)
        self.assertEqual(report.dropped_by_reason["exact_duplicate"], 0)
        self.assertEqual(report.dropped_by_reason["ignored_category_or_country"], 1933)

    def test_spiro_exact_duplicate_is_dropped(self):
        """Verify that exact duplicate markers in Spiro are dropped without merging."""
        synthetic_payload = {
            "cities": [{"id": "city-1", "name": "Nairobi", "country": "KE"}],
            "markers": [
                {
                    "name": "Station A",
                    "address": "Street 1",
                    "coordinates": "-1.29, 36.82",
                    "categoryId": "swap-station",
                    "cityId": "city-1",
                },
                {
                    "name": "Station A",
                    "address": "Street 1",
                    "coordinates": "-1.29, 36.82",
                    "categoryId": "swap-station",
                    "cityId": "city-1",
                },
            ],
        }
        report = IngestionReport()
        records = adapt_spiro(synthetic_payload, report=report)
        self.assertEqual(len(records), 1)
        self.assertEqual(report.kept_count, 1)
        self.assertEqual(report.dropped_by_reason["exact_duplicate"], 1)


if __name__ == "__main__":
    unittest.main()
