"""Comprehensive unit tests for the shared ingestion core and config.

Verifies all functions using synthetic data only, with zero device identifiers,
strictly adhering to GEMINI.md engineering rules.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ingestion.config import (
    ALL_COLUMNS,
    BASE_COLUMNS,
    DEFAULT_MERGE_DISTANCE_METERS,
    KENYA_MAX_LAT,
    KENYA_MAX_LNG,
    KENYA_MIN_LAT,
    KENYA_MIN_LNG,
    REGISTRY_COLUMNS,
    load_config,
)
from scripts.ingestion.core import (
    IngestionReport,
    atomic_write_csv,
    atomic_write_json,
    atomic_write_text,
    clean_display_text,
    cluster_sites,
    haversine_distance_meters,
    is_in_kenya_box,
    make_comparison_key,
    normalize_text,
    parse_coordinates,
)


class TestSchemaAndConfig(unittest.TestCase):
    """Test schema column definitions, constants, and config loading."""

    def test_schema_column_order(self):
        expected_base = (
            "operator",
            "name",
            "address",
            "latitude",
            "longitude",
            "cabinet_count",
            "source",
            "retrieved_on",
        )
        self.assertEqual(BASE_COLUMNS, expected_base)

        expected_registry = (
            "station_id",
            "first_seen",
            "last_seen",
            "is_removed",
            "co_located",
            "co_located_with",
            "last_verified",
        )
        self.assertEqual(REGISTRY_COLUMNS, expected_registry)
        self.assertEqual(ALL_COLUMNS, expected_base + expected_registry)

    def test_default_config(self):
        cfg = load_config(config_path="/nonexistent_untracked_config.json")
        self.assertEqual(cfg.min_lat, KENYA_MIN_LAT)
        self.assertEqual(cfg.max_lat, KENYA_MAX_LAT)
        self.assertEqual(cfg.min_lng, KENYA_MIN_LNG)
        self.assertEqual(cfg.max_lng, KENYA_MAX_LNG)
        self.assertEqual(cfg.merge_distance_meters, DEFAULT_MERGE_DISTANCE_METERS)
        self.assertEqual(cfg.pct_change_warn, 0.15)
        self.assertEqual(cfg.pct_drop_block, 0.40)
        self.assertTrue(cfg.empty_run_reject)
        self.assertEqual(cfg.contact_email, "")
        self.assertIn("EV-Mapping-Ingestion/1.0", cfg.user_agent)

    def test_config_from_untracked_file_and_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "config.local.json"
            cfg_data = {
                "merge_distance_meters": 45.0,
                "pct_change_warn": 0.20,
                "contact_email": "ops@example.com",
            }
            cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")

            with patch.dict(os.environ, {"EV_MAP_CONTACT_EMAIL": "env_override@example.com"}):
                cfg = load_config(config_path=cfg_file)
                self.assertEqual(cfg.merge_distance_meters, 45.0)
                self.assertEqual(cfg.pct_change_warn, 0.20)
                # Env var overrides file
                self.assertEqual(cfg.contact_email, "env_override@example.com")
                self.assertIn("env_override@example.com", cfg.user_agent)


class TestCoordinatesAndBounds(unittest.TestCase):
    """Test coordinate parsing and Kenya bounding box checks."""

    def test_equator_coordinates(self):
        """Zero (0.0) is a valid latitude on the equator."""
        # Numbers directly
        coords_num = parse_coordinates(0.0, 36.0)
        self.assertIsNotNone(coords_num)
        self.assertEqual(coords_num, (0.0, 36.0))

        # "lat, lng" text string
        coords_str = parse_coordinates("0.0, 36.0")
        self.assertIsNotNone(coords_str)
        self.assertEqual(coords_str, (0.0, 36.0))

        # Equator point in Kenya bounding box
        self.assertTrue(is_in_kenya_box(0.0, 36.0))

    def test_valid_coordinate_formats(self):
        # Two float arguments
        self.assertEqual(parse_coordinates(-1.286389, 36.817223), (-1.286389, 36.817223))
        # Two string arguments
        self.assertEqual(parse_coordinates("-1.286", "36.817"), (-1.286, 36.817))
        # Single comma-separated text string with spaces
        self.assertEqual(
            parse_coordinates("  -1.286389  ,   36.817223  "),
            (-1.286389, 36.817223),
        )

    def test_malformed_coordinates(self):
        """Malformed or out-of-range coordinates must return None without raising."""
        malformed_inputs = [
            (None, None),
            (None,),
            ("",),
            ("not_a_coordinate",),
            ("1.23",),  # missing second part
            ("1.23, 4.56, 7.89",),  # too many parts
            ("abc, 36.0",),
            ("0.0, def",),
            ("nan, 36.0",),
            ("0.0, inf",),
            (float("nan"), 36.0),
            (0.0, float("inf")),
            (95.0, 36.0),  # lat > 90
            (-95.0, 36.0),  # lat < -90
            (0.0, 190.0),  # lng > 180
            (0.0, -190.0),  # lng < -180
        ]
        for item in malformed_inputs:
            with self.subTest(item=item):
                if len(item) == 1:
                    result = parse_coordinates(item[0])
                else:
                    result = parse_coordinates(item[0], item[1])
                self.assertIsNone(result)

    def test_kenya_bounds_extremes(self):
        # Inside Kenya
        self.assertTrue(is_in_kenya_box(-1.29, 36.82))
        self.assertTrue(is_in_kenya_box(-4.5, 39.5))
        self.assertTrue(is_in_kenya_box(4.5, 35.5))

        # Outside Kenya
        self.assertFalse(is_in_kenya_box(-5.0, 36.0))  # too far south
        self.assertFalse(is_in_kenya_box(6.0, 36.0))  # too far north
        self.assertFalse(is_in_kenya_box(0.0, 32.0))  # too far west (Uganda)
        self.assertFalse(is_in_kenya_box(0.0, 43.0))  # too far east (Somalia)


class TestTextNormalization(unittest.TestCase):
    """Test name and address normalization, collapsing spaces, and comparison keys."""

    def test_casing_and_spacing_variants_of_one_name(self):
        variants = [
            "  Nairobi   West   Station  ",
            "nairobi west station",
            "NAIROBI WEST STATION",
            "Nairobi \t West \n Station",
        ]
        expected_display = "Nairobi West Station"
        expected_key = "nairobi west station"

        for variant in variants:
            with self.subTest(variant=variant):
                display, key = normalize_text(variant)
                self.assertEqual(key, expected_key)
                # Trim and collapse spaces for all variants
                self.assertEqual(" ".join(display.split()), display)

        # Direct function tests
        self.assertEqual(clean_display_text(variants[0]), expected_display)
        self.assertEqual(make_comparison_key(variants[2]), expected_key)

    def test_empty_or_none_text(self):
        self.assertEqual(normalize_text(None), ("", ""))
        self.assertEqual(normalize_text("   "), ("", ""))
        self.assertEqual(clean_display_text(None), "")
        self.assertEqual(make_comparison_key(""), "")


class TestDistanceCalculations(unittest.TestCase):
    """Test Haversine distance calculations in meters."""

    def test_identical_coordinates(self):
        self.assertEqual(haversine_distance_meters(-1.286389, 36.817223, -1.286389, 36.817223), 0.0)
        self.assertEqual(haversine_distance_meters(0.0, 36.0, 0.0, 36.0), 0.0)

    def test_two_records_one_metre_apart(self):
        """1 meter difference in latitude corresponds to roughly 1 / 111195 degrees."""
        lat1, lng1 = -1.286389, 36.817223
        delta_deg = 1.0 / 111_195.0
        lat2, lng2 = lat1 + delta_deg, lng1

        dist = haversine_distance_meters(lat1, lng1, lat2, lng2)
        # Expected to be within 5% of 1.0 meter
        self.assertAlmostEqual(dist, 1.0, delta=0.05)

    def test_equator_distance(self):
        lat1, lng1 = 0.0, 36.0
        delta_deg = 100.0 / 111_195.0
        lat2, lng2 = 0.0, lng1 + delta_deg
        dist = haversine_distance_meters(lat1, lng1, lat2, lng2)
        self.assertAlmostEqual(dist, 100.0, delta=2.0)


class TestSiteClustering(unittest.TestCase):
    """Test clustering sites by location only, unit key counting in memory, and performance."""

    def test_two_records_one_metre_apart_clustering(self):
        delta_deg = 1.0 / 111_195.0
        records = [
            {
                "operator": "ARC Ride",
                "name": "Station Alpha",
                "address": "Street 1",
                "latitude": -1.286389,
                "longitude": 36.817223,
                "source": "https://api.test",
                "retrieved_on": "2026-10-23",
            },
            {
                "operator": "ARC Ride",
                "name": "Station Alpha",
                "address": "Street 1",
                "latitude": -1.286389 + delta_deg,
                "longitude": 36.817223,
                "source": "https://api.test",
                "retrieved_on": "2026-10-23",
            },
        ]
        # With default 30m merge distance, they should merge into 1 site
        merged = cluster_sites(records, merge_distance_meters=30.0)
        self.assertEqual(len(merged), 1)

        # With tight 0.5m merge distance, they should remain 2 distinct sites
        unmerged = cluster_sites(records, merge_distance_meters=0.5)
        self.assertEqual(len(unmerged), 2)

    def test_two_far_apart_records_with_same_name(self):
        """Far-apart stations with the exact same name must NOT be merged (location-only clustering)."""
        records = [
            {
                "operator": "Spiro",
                "name": "TotalEnergies Hub",
                "address": "Nairobi CBD",
                "latitude": -1.2921,
                "longitude": 36.8219,
                "source": "spironet",
                "retrieved_on": "2026-10-23",
            },
            {
                "operator": "Spiro",
                "name": "TotalEnergies Hub",
                "address": "Mombasa Island",
                "latitude": -4.0435,
                "longitude": 39.6682,
                "source": "spironet",
                "retrieved_on": "2026-10-23",
            },
        ]
        sites = cluster_sites(records, merge_distance_meters=30.0)
        self.assertEqual(len(sites), 2)
        self.assertEqual(sites[0]["name"], "TotalEnergies Hub")
        self.assertEqual(sites[1]["name"], "TotalEnergies Hub")
        self.assertNotEqual(sites[0]["latitude"], sites[1]["latitude"])

    def test_most_common_display_name_selection(self):
        """Verify the most frequent display form of a name is selected for the site."""
        records = [
            {
                "operator": "ARC Ride",
                "name": "Nairobi West Station",
                "latitude": -1.3000,
                "longitude": 36.8000,
            },
            {
                "operator": "ARC Ride",
                "name": "NAIROBI WEST STATION",
                "latitude": -1.3001,
                "longitude": 36.8001,
            },
            {
                "operator": "ARC Ride",
                "name": "Nairobi West Station",
                "latitude": -1.30005,
                "longitude": 36.80005,
            },
        ]
        sites = cluster_sites(records, merge_distance_meters=30.0)
        self.assertEqual(len(sites), 1)
        # 2x "Nairobi West Station" vs 1x "NAIROBI WEST STATION"
        self.assertEqual(sites[0]["name"], "Nairobi West Station")

    def test_in_memory_unit_key_counting_and_isolation(self):
        """Hard Rule 1: Device identifiers held in memory only to count cabinets, never emitted."""
        records = [
            {
                "operator": "ARC Ride",
                "name": "Site Beta",
                "latitude": -1.2500,
                "longitude": 36.8500,
                "synthetic_unit_id": "synthetic-unit-1",
            },
            {
                "operator": "ARC Ride",
                "name": "Site Beta",
                "latitude": -1.2501,
                "longitude": 36.8501,
                "synthetic_unit_id": "synthetic-unit-2",
            },
            {
                "operator": "ARC Ride",
                "name": "Site Beta",
                "latitude": -1.25005,
                "longitude": 36.85005,
                "synthetic_unit_id": "synthetic-unit-1",  # duplicate unit ID
            },
        ]
        sites = cluster_sites(
            records,
            merge_distance_meters=30.0,
            unit_key_fn=lambda r: r.get("synthetic_unit_id"),
        )
        self.assertEqual(len(sites), 1)
        # Distinct synthetic unit keys: unit-1, unit-2 -> count 2
        self.assertEqual(sites[0]["cabinet_count"], 2)

        # Critical: No device identifier keys or set objects in the output site record
        for key in sites[0].keys():
            self.assertFalse(key.startswith("_"))
            self.assertNotIn("unit", key.lower())
            self.assertNotIn("id", key.lower())
            self.assertNotIn("imei", key.lower())
            self.assertNotIn("serial", key.lower())

    def test_spiro_blank_cabinet_count(self):
        """When unit keys are not tracked (Spiro), cabinet_count must be strictly empty string."""
        records = [
            {
                "operator": "Spiro",
                "name": "Spiro Station 1",
                "latitude": -1.2000,
                "longitude": 36.8000,
            }
        ]
        sites = cluster_sites(records, merge_distance_meters=30.0, unit_key_fn=None)
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0]["cabinet_count"], "")

    def test_performance_with_thousands_of_records(self):
        """Clustering several thousand records must complete rapidly (< 1.0 second)."""
        import random

        rng = random.Random(42)
        synthetic_records = []
        base_lat, base_lng = -1.286, 36.817
        for i in range(2500):
            # Cluster around 50 neighborhood centers
            center_lat = base_lat + (i % 50) * 0.01
            center_lng = base_lng + (i % 50) * 0.01
            lat = center_lat + rng.uniform(-0.0001, 0.0001)
            lng = center_lng + rng.uniform(-0.0001, 0.0001)
            synthetic_records.append({
                "operator": "ARC Ride",
                "name": f"Station {i % 50}",
                "latitude": lat,
                "longitude": lng,
            })

        start = time.perf_counter()
        sites = cluster_sites(synthetic_records, merge_distance_meters=30.0)
        duration = time.perf_counter() - start

        self.assertLess(duration, 1.0, f"Clustering took too long: {duration:.2f}s")
        self.assertGreater(len(sites), 0)


class TestAtomicFileWriting(unittest.TestCase):
    """Test atomic file writing that leaves target file intact on failure."""

    def test_successful_atomic_write_text_json_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "sub"

            # Text
            txt_file = base_dir / "test.txt"
            atomic_write_text(txt_file, "hello atomic")
            self.assertEqual(txt_file.read_text(encoding="utf-8"), "hello atomic")

            # JSON
            json_file = base_dir / "test.json"
            atomic_write_json(json_file, {"key": "value"})
            data = json.loads(json_file.read_text(encoding="utf-8"))
            self.assertEqual(data, {"key": "value"})

            # CSV
            csv_file = base_dir / "test.csv"
            rows = [{"operator": "ARC Ride", "name": "Station 1"}]
            atomic_write_csv(csv_file, rows, fieldnames=("operator", "name"))
            with open(csv_file, "r", encoding="utf-8") as f:
                reader = list(csv.DictReader(f))
                self.assertEqual(len(reader), 1)
                self.assertEqual(reader[0]["name"], "Station 1")

    def test_interrupted_write_leaves_previous_file_intact(self):
        """When a write fails midway, the existing file must be completely untouched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target_file = Path(tmpdir) / "critical_data.txt"
            original_content = "ORIGINAL_SAFE_CONTENT"
            target_file.write_text(original_content, encoding="utf-8")

            # Attempt a write where serialization or os.replace fails
            with patch("os.fsync", side_effect=IOError("Simulated disk failure during fsync")):
                with self.assertRaises(IOError):
                    atomic_write_text(target_file, "CORRUPTED_NEW_CONTENT")

            # Assert destination file still exists with 100% intact original content
            self.assertTrue(target_file.exists())
            self.assertEqual(target_file.read_text(encoding="utf-8"), original_content)

            # Assert no stray temporary files were left in the folder
            leftover_tmps = list(Path(tmpdir).glob(".*.tmp"))
            self.assertEqual(len(leftover_tmps), 0)


class TestIngestionReport(unittest.TestCase):
    """Test run report counting records kept and dropped by reason."""

    def test_report_accumulation_and_formatting(self):
        report = IngestionReport()
        report.record_kept()
        report.record_kept(count=4)
        report.record_dropped("invalid_coordinates")
        report.record_dropped("outside_kenya", count=2)
        report.record_dropped("invalid_coordinates")

        self.assertEqual(report.total_input, 9)
        self.assertEqual(report.kept_count, 5)
        self.assertEqual(report.total_dropped, 4)
        self.assertEqual(report.dropped_by_reason["invalid_coordinates"], 2)
        self.assertEqual(report.dropped_by_reason["outside_kenya"], 2)

        summary_dict = report.to_dict()
        self.assertEqual(summary_dict["total_input"], 9)
        self.assertEqual(summary_dict["kept"], 5)
        self.assertEqual(summary_dict["dropped_total"], 4)

        summary_text = report.summary_text()
        self.assertIn("Total input records : 9", summary_text)
        self.assertIn("Kept records        : 5", summary_text)
        self.assertIn("Dropped records     : 4", summary_text)
        self.assertIn("- invalid_coordinates: 2", summary_text)
        self.assertIn("- outside_kenya: 2", summary_text)


if __name__ == "__main__":
    unittest.main()
