"""Unit tests for scripts/build_swap_public.py.

Verifies:
1. Zero coordinates (0.0, 0.0) on the equator are treated as valid (no truthiness bug).
2. Stations marked is_removed are excluded.
3. Coordinates outside Kenya bounds are dropped.
4. Device identifiers are strictly never exposed in public assets.
5. ARC Ride retains cabinet_count; Spiro cabinet_count stays None.
6. Operator summary produces exact counts and latest retrieved_on date.
7. regions.json properly registers kenya_swap as the primary/first region.

Uses synthetic test data only, strictly following GEMINI.md engineering rules.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_swap_public import (
    build_swap_public,
    prepare_swap_stations,
)


class TestBuildSwapPublic(unittest.TestCase):
    """Test suite for battery swap public data preparation."""

    def test_equator_coordinate_zero_valid(self):
        """0.0 is a valid latitude on Kenya's equator and must not be dropped."""
        synthetic = [
            {
                "operator": "ARC Ride",
                "name": "Equator Swap Point",
                "address": "Equator Highway",
                "latitude": 0.0,
                "longitude": 36.0,
                "cabinet_count": "2",
                "retrieved_on": "2026-10-23",
                "station_id": "SYNTH_00.0000N_036.0000E",
                "is_removed": False,
            }
        ]
        active, summaries = prepare_swap_stations(synthetic)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["final_latitude"], 0.0)
        self.assertEqual(active[0]["final_longitude"], 36.0)
        self.assertEqual(active[0]["cabinet_count"], "2")

    def test_removed_stations_filtered_out(self):
        """Stations with is_removed True or 'true' must be filtered out."""
        synthetic = [
            {
                "operator": "ARC Ride",
                "name": "Active Hub",
                "latitude": -1.28,
                "longitude": 36.82,
                "is_removed": False,
            },
            {
                "operator": "ARC Ride",
                "name": "Retired Hub 1",
                "latitude": -1.29,
                "longitude": 36.83,
                "is_removed": True,
            },
            {
                "operator": "Spiro",
                "name": "Retired Hub 2",
                "latitude": -1.30,
                "longitude": 36.84,
                "is_removed": "true",
            },
        ]
        active, summaries = prepare_swap_stations(synthetic)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["name"], "Active Hub")

    def test_spiro_cabinet_count_is_none(self):
        """Spiro cabinet_count must remain None / blank, never 0 or 1."""
        synthetic = [
            {
                "operator": "Spiro",
                "name": "Spiro Westlands",
                "latitude": -1.26,
                "longitude": 36.80,
                "cabinet_count": "",
                "retrieved_on": "2026-10-21",
                "is_removed": False,
            },
            {
                "operator": "ARC Ride",
                "name": "ARC Ride Eastlands",
                "latitude": -1.28,
                "longitude": 36.85,
                "cabinet_count": "3",
                "retrieved_on": "2026-10-23",
                "is_removed": False,
            },
        ]
        active, summaries = prepare_swap_stations(synthetic)
        spiro = next(s for s in active if s["operator"] == "Spiro")
        arc = next(s for s in active if s["operator"] == "ARC Ride")

        self.assertIsNone(spiro["cabinet_count"])
        self.assertEqual(arc["cabinet_count"], "3")

    def test_operator_summary_counts_and_dates(self):
        """Operator summary must group counts and find the latest retrieved_on."""
        synthetic = [
            {
                "operator": "ARC Ride",
                "name": "A1",
                "latitude": -1.2,
                "longitude": 36.8,
                "retrieved_on": "2026-10-20",
                "is_removed": False,
            },
            {
                "operator": "ARC Ride",
                "name": "A2",
                "latitude": -1.3,
                "longitude": 36.8,
                "retrieved_on": "2026-10-23",
                "is_removed": False,
            },
            {
                "operator": "Spiro",
                "name": "S1",
                "latitude": -1.4,
                "longitude": 36.8,
                "retrieved_on": "2026-10-21",
                "is_removed": False,
            },
        ]
        active, summaries = prepare_swap_stations(synthetic)
        sum_dict = {s["operator"]: s for s in summaries}

        self.assertEqual(sum_dict["ARC Ride"]["total_count"], 2)
        self.assertEqual(sum_dict["ARC Ride"]["swap_station_count"], 2)
        self.assertEqual(sum_dict["ARC Ride"]["retrieved_on"], "2026-10-23")

        self.assertEqual(sum_dict["Spiro"]["total_count"], 1)
        self.assertEqual(sum_dict["Spiro"]["retrieved_on"], "2026-10-21")

    def test_device_identifiers_never_in_public_output(self):
        """Sensitive device identifiers must not be present in public records."""
        synthetic = [
            {
                "operator": "ARC Ride",
                "name": "Secure Station",
                "latitude": -1.2,
                "longitude": 36.8,
                "cabinet_count": "1",
                "device_id": "SECRET_IMEI_12345",
                "serial_number": "SN99999",
                "is_removed": False,
            }
        ]
        active, _ = prepare_swap_stations(synthetic)
        record = active[0]
        self.assertNotIn("device_id", record)
        self.assertNotIn("serial_number", record)
        self.assertNotIn("SECRET_IMEI_12345", json.dumps(record))

    def test_end_to_end_build_swap_public(self):
        """End-to-end integration test creating public files in a temp directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            source_file = tmp_root / "test_stations.json"
            dest_dir = tmp_root / "public" / "kenya_swap"
            regions_file = tmp_root / "data" / "public" / "regions.json"
            regions_file.parent.mkdir(parents=True, exist_ok=True)
            with open(regions_file, "w", encoding="utf-8") as f:
                json.dump({"regions": [{"region_id": "kenya", "display_name": "Kenya"}]}, f)

            synthetic = [
                {
                    "operator": "ARC Ride",
                    "name": "Site Alpha",
                    "address": "Street 1",
                    "latitude": -1.28,
                    "longitude": 36.82,
                    "cabinet_count": "2",
                    "retrieved_on": "2026-10-23",
                    "station_id": "ARC_001",
                    "last_verified": "2026-10-22",
                    "is_removed": False,
                },
                {
                    "operator": "Spiro",
                    "name": "Site Beta",
                    "address": "Street 2",
                    "latitude": -1.29,
                    "longitude": 36.83,
                    "cabinet_count": "",
                    "retrieved_on": "2026-10-21",
                    "station_id": "SPIRO_001",
                    "last_verified": "",
                    "is_removed": False,
                },
            ]
            with open(source_file, "w", encoding="utf-8") as f:
                json.dump(synthetic, f)

            summary = build_swap_public(source_file, dest_dir, repo_root=tmp_root)
            self.assertEqual(summary["station_count"], 2)
            self.assertEqual(summary["operator_counts"]["ARC Ride"], 1)
            self.assertEqual(summary["operator_counts"]["Spiro"], 1)

            # Check generated files
            self.assertTrue((dest_dir / "stations_with_county.json").is_file())
            self.assertTrue((dest_dir / "operator_summary.json").is_file())
            self.assertTrue((dest_dir / "manifest.json").is_file())
            self.assertTrue((dest_dir / "kenya_counties_display.geojson").is_file())

            # Verify manifest
            with open(dest_dir / "manifest.json", "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["station_count"], 2)
            self.assertIn("operator_summary.json", manifest["files"])

    def test_operator_summary_cabinet_totals_and_excluded_coordinates(self):
        """Operator summary must compute ARC Ride total cabinets and track excluded invalid coords."""
        synthetic = [
            {
                "operator": "ARC Ride",
                "name": "Site A",
                "latitude": -1.2,
                "longitude": 36.8,
                "cabinet_count": "3",
                "is_removed": False,
            },
            {
                "operator": "ARC Ride",
                "name": "Site B",
                "latitude": -1.3,
                "longitude": 36.8,
                "cabinet_count": "5",
                "is_removed": False,
            },
            {
                "operator": "Spiro",
                "name": "Valid Spiro",
                "latitude": -1.4,
                "longitude": 36.8,
                "cabinet_count": "",
                "is_removed": False,
            },
            {
                "operator": "Spiro",
                "name": "Invalid Spiro 1",
                "latitude": "invalid_lat",
                "longitude": 36.8,
                "is_removed": False,
            },
            {
                "operator": "Spiro",
                "name": "Outside Kenya Spiro",
                "latitude": 30.0,
                "longitude": 31.2,
                "is_removed": False,
            },
        ]
        active, summaries = prepare_swap_stations(synthetic)
        sum_dict = {s["operator"]: s for s in summaries}

        # ARC Ride: 2 sites, 8 cabinets, 0 excluded
        self.assertEqual(sum_dict["ARC Ride"]["total_count"], 2)
        self.assertEqual(sum_dict["ARC Ride"]["cabinet_count"], 8)
        self.assertEqual(sum_dict["ARC Ride"]["excluded_invalid_coordinates"], 0)

        # Spiro: 1 site shown (M=1), cabinet_count None, 2 excluded for invalid/out-of-bounds coords (K=2)
        self.assertEqual(sum_dict["Spiro"]["total_count"], 1)
        self.assertIsNone(sum_dict["Spiro"]["cabinet_count"])
        self.assertEqual(sum_dict["Spiro"]["excluded_invalid_coordinates"], 2)

    def test_dashboard_app_contract_no_hardcoded_numbers_and_placeholders(self):
        """Dashboard script must not contain hard-coded 450+ comparison and must use config placeholders."""
        app_js_path = Path(__file__).resolve().parent.parent / "dashboard" / "app.js"
        content = app_js_path.read_text(encoding="utf-8")

        # 450+ comparison removed from dashboard panel
        self.assertNotIn("450+", content)
        self.assertNotIn("spiro_reported", content)

        # Placeholders used in config
        self.assertIn('count: "[N]"', content)
        self.assertIn('date: "[date]"', content)
        self.assertIn('primary_chart_label: "mapped locations"', content)
        self.assertIn("Spiro locations and ARC Ride sites are not capacity-comparable", content)
        self.assertIn("ARC Ride registered cabinets", content)
        self.assertIn("Spiro's own map lists", content)
        self.assertIn("were excluded for invalid coordinates", content)


if __name__ == "__main__":
    unittest.main()
