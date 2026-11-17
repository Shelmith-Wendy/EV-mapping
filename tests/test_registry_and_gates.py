"""Unit and integration tests for registry lifecycle tracking, safety gates, overlay, and co-location."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.ingest_operator import GateFailureError, ingest_operator
from scripts.ingestion.config import IngestionConfig
from scripts.ingestion.matching import (
    apply_verification_overlay,
    detect_colocation,
    match_and_update_registry,
)
from scripts.ingestion.storage import (
    get_current_dataset_path,
    get_snapshots_dir,
)


class TestRegistryLifecycleAndGates(unittest.TestCase):
    """Test registry matching, stable IDs, lifecycle transitions, safety gates, overlay, and co-location."""

    def test_first_run_creates_registry_and_snapshots(self):
        """First run: all stations marked as new, stable IDs assigned, snapshot and current dataset written."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            input_file = base_dir / "spiro_input.json"
            input_data = {
                "cities": [{"id": "c1", "name": "Nairobi", "country": "KE"}],
                "markers": [
                    {
                        "name": "Station A",
                        "address": "Street 1",
                        "coordinates": "-1.2900, 36.8000",
                        "categoryId": "swap-station",
                        "cityId": "c1",
                    },
                    {
                        "name": "Station B",
                        "address": "Street 2",
                        "coordinates": "-1.3000, 36.8100",
                        "categoryId": "swap-station",
                        "cityId": "c1",
                    },
                ],
            }
            input_file.write_text(json.dumps(input_data), encoding="utf-8")

            records, report, diff = ingest_operator(
                operator="spiro",
                input_path=input_file,
                retrieved_on="2026-10-21",
                base_dir=base_dir,
            )

            # Assert diff
            self.assertEqual(diff.new_count, 2)
            self.assertEqual(diff.unchanged_count, 0)
            self.assertEqual(diff.moved_count, 0)
            self.assertEqual(diff.removed_count, 0)

            # Assert stable IDs & dates
            for r in records:
                self.assertTrue(r["station_id"].startswith("SPIRO_"))
                self.assertEqual(r["first_seen"], "2026-10-21")
                self.assertEqual(r["last_seen"], "2026-10-21")
                self.assertFalse(r["is_removed"])

            # Assert filesystem persistence
            current_path = get_current_dataset_path("Spiro", base_dir=base_dir)
            self.assertTrue(current_path.is_file())

            snapshots = list(get_snapshots_dir("Spiro", base_dir=base_dir).glob("*.csv"))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].name, "2026-10-21_stations.csv")

    def test_identical_second_run_preserves_stable_ids(self):
        """Identical second run: no changes, stable IDs preserved, first_seen preserved, last_seen updated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            input_file = base_dir / "input.json"
            input_data = {
                "cities": [{"id": "c1", "name": "Nairobi", "country": "KE"}],
                "markers": [
                    {
                        "name": "Station Alpha",
                        "address": "Westlands",
                        "coordinates": "-1.2600, 36.8000",
                        "categoryId": "swap-station",
                        "cityId": "c1",
                    }
                ],
            }
            input_file.write_text(json.dumps(input_data), encoding="utf-8")

            # Run 1
            records1, _, diff1 = ingest_operator(
                operator="spiro",
                input_path=input_file,
                retrieved_on="2026-10-21",
                base_dir=base_dir,
            )
            id_run1 = records1[0]["station_id"]
            self.assertEqual(diff1.new_count, 1)

            # Run 2 on next day with same data
            records2, _, diff2 = ingest_operator(
                operator="spiro",
                input_path=input_file,
                retrieved_on="2026-10-22",
                base_dir=base_dir,
            )
            self.assertEqual(len(records2), 1)
            self.assertEqual(records2[0]["station_id"], id_run1)
            self.assertEqual(records2[0]["first_seen"], "2026-10-21")
            self.assertEqual(records2[0]["last_seen"], "2026-10-22")
            self.assertFalse(records2[0]["is_removed"])

            # Diff metrics for identical run
            self.assertEqual(diff2.new_count, 0)
            self.assertEqual(diff2.unchanged_count, 1)
            self.assertEqual(diff2.moved_count, 0)
            self.assertEqual(diff2.removed_count, 0)

    def test_station_added_removed_and_moved(self):
        """Test transitions: one station unchanged, one moved, one removed, and one added."""
        previous_registry = [
            {
                "operator": "ARC Ride",
                "station_id": "ARC_01.2900S_036.8000E",
                "name": "Station One",
                "address": "Loc 1",
                "latitude": -1.2900,
                "longitude": 36.8000,
                "cabinet_count": 2,
                "source": "api",
                "retrieved_on": "2026-10-20",
                "first_seen": "2026-10-01",
                "last_seen": "2026-10-20",
                "is_removed": False,
            },
            {
                "operator": "ARC Ride",
                "station_id": "ARC_01.3000S_036.8100E",
                "name": "Station Two",
                "address": "Loc 2",
                "latitude": -1.3000,
                "longitude": 36.8100,
                "cabinet_count": 1,
                "source": "api",
                "retrieved_on": "2026-10-20",
                "first_seen": "2026-10-01",
                "last_seen": "2026-10-20",
                "is_removed": False,
            },
            {
                "operator": "ARC Ride",
                "station_id": "ARC_01.3100S_036.8200E",
                "name": "Station Three",
                "address": "Loc 3",
                "latitude": -1.3100,
                "longitude": 36.8200,
                "cabinet_count": 1,
                "source": "api",
                "retrieved_on": "2026-10-20",
                "first_seen": "2026-10-01",
                "last_seen": "2026-10-20",
                "is_removed": False,
            },
        ]

        # Incoming has:
        # 1. Station One: exact same spot (-1.2900, 36.8000) -> unchanged
        # 2. Station Two: moved ~94m (-1.3006, 36.8106), same name -> moved
        # 3. Station Three is missing -> removed
        # 4. Station Four: brand new at (-1.3500, 36.8500) -> new
        incoming_sites = [
            {
                "operator": "ARC Ride",
                "name": "Station One",
                "address": "Loc 1",
                "latitude": -1.2900,
                "longitude": 36.8000,
                "cabinet_count": 2,
                "source": "api",
                "retrieved_on": "2026-10-23",
            },
            {
                "operator": "ARC Ride",
                "name": "Station Two",
                "address": "Loc 2 New",
                "latitude": -1.3006,
                "longitude": 36.8106,
                "cabinet_count": 1,
                "source": "api",
                "retrieved_on": "2026-10-23",
            },
            {
                "operator": "ARC Ride",
                "name": "Station Four",
                "address": "Loc 4",
                "latitude": -1.3500,
                "longitude": 36.8500,
                "cabinet_count": 1,
                "source": "api",
                "retrieved_on": "2026-10-23",
            },
        ]

        registry, diff = match_and_update_registry(
            incoming_sites=incoming_sites,
            previous_registry=previous_registry,
            run_date="2026-10-23",
        )

        self.assertEqual(diff.unchanged_count, 1)
        self.assertEqual(diff.moved_count, 1)
        self.assertEqual(diff.removed_count, 1)
        self.assertEqual(diff.new_count, 1)

        # Unchanged station
        s1 = next(r for r in registry if r["station_id"] == "ARC_01.2900S_036.8000E")
        self.assertFalse(s1["is_removed"])
        self.assertEqual(s1["first_seen"], "2026-10-01")
        self.assertEqual(s1["last_seen"], "2026-10-23")

        # Moved station
        s2 = next(r for r in registry if r["station_id"] == "ARC_01.3000S_036.8100E")
        self.assertFalse(s2["is_removed"])
        self.assertEqual(s2["first_seen"], "2026-10-01")
        self.assertEqual(s2["last_seen"], "2026-10-23")
        self.assertAlmostEqual(s2["latitude"], -1.3006, places=4)

        # Removed station
        s3 = next(r for r in registry if r["station_id"] == "ARC_01.3100S_036.8200E")
        self.assertTrue(s3["is_removed"])
        self.assertEqual(s3["first_seen"], "2026-10-01")
        self.assertEqual(s3["last_seen"], "2026-10-20")  # Remains previous date

        # New station
        s4 = next(r for r in registry if r["name"] == "Station Four")
        self.assertFalse(s4["is_removed"])
        self.assertEqual(s4["first_seen"], "2026-10-23")
        self.assertEqual(s4["last_seen"], "2026-10-23")

    def test_gate_triggering_run_blocks_and_leaves_file_intact(self):
        """Gate drops > 40%: blocked without --force (leaves dataset intact), passes with --force."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            cfg = IngestionConfig(pct_drop_block=0.40)

            # Setup previous snapshot with 10 stations
            prev_records = []
            for i in range(10):
                prev_records.append({
                    "operator": "Spiro",
                    "station_id": f"SPIRO_00{i}",
                    "name": f"Station {i}",
                    "address": "Nairobi",
                    "latitude": -1.2900 + i * 0.01,
                    "longitude": 36.8000 + i * 0.01,
                    "cabinet_count": "",
                    "source": "spironet",
                    "retrieved_on": "2026-10-20",
                    "first_seen": "2026-10-01",
                    "last_seen": "2026-10-20",
                    "is_removed": False,
                    "co_located": False,
                    "co_located_with": "",
                    "last_verified": "",
                })
            current_path = get_current_dataset_path("Spiro", base_dir=base_dir)
            from scripts.ingestion.core import atomic_write_csv
            from scripts.ingestion.config import ALL_COLUMNS
            atomic_write_csv(current_path, prev_records, fieldnames=ALL_COLUMNS)

            # Current input has only 4 stations (60% drop > 40% gate)
            input_file = base_dir / "dropped_input.json"
            markers = []
            for i in range(4):
                markers.append({
                    "name": f"Station {i}",
                    "address": "Nairobi",
                    "coordinates": f"{-1.2900 + i * 0.01}, {36.8000 + i * 0.01}",
                    "categoryId": "swap-station",
                    "cityId": "c1",
                })
            input_file.write_text(json.dumps({
                "cities": [{"id": "c1", "name": "Nairobi", "country": "KE"}],
                "markers": markers,
            }), encoding="utf-8")

            # 1. Run without --force: MUST fail with GateFailureError
            with self.assertRaises(GateFailureError) as ctx:
                ingest_operator(
                    operator="spiro",
                    input_path=input_file,
                    retrieved_on="2026-10-23",
                    config=cfg,
                    base_dir=base_dir,
                    force=False,
                )
            self.assertIn("Gate blocked", str(ctx.exception))
            self.assertIn("60.0%", str(ctx.exception))

            # Verify current dataset is UNTOUCHED (still 10 rows)
            with open(current_path, "r", encoding="utf-8") as f:
                reader = list(csv.DictReader(f))
                self.assertEqual(len(reader), 10)

            # 2. Run with --force: MUST pass and update dataset
            records_forced, _, _ = ingest_operator(
                operator="spiro",
                input_path=input_file,
                retrieved_on="2026-10-23",
                config=cfg,
                base_dir=base_dir,
                force=True,
            )
            self.assertEqual(len(records_forced), 10)  # 4 active + 6 marked removed
            active_forced = sum(1 for r in records_forced if not r["is_removed"])
            self.assertEqual(active_forced, 4)

    def test_human_verification_overlay_precedence(self):
        """Verification overlay: verified coordinates override raw coordinates and set last_verified date."""
        records = [
            {
                "operator": "ARC Ride",
                "station_id": "ARC_01.2900S_036.8000E",
                "name": "Raw Station",
                "latitude": -1.2900,
                "longitude": 36.8000,
                "last_verified": "",
            },
            {
                "operator": "ARC Ride",
                "station_id": "ARC_UNVERIFIED",
                "name": "Unverified Station",
                "latitude": -1.3000,
                "longitude": 36.8100,
                "last_verified": "",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            overlay_file = Path(tmpdir) / "human_verification_overlay.csv"
            overlay_content = (
                "station_id,verified_latitude,verified_longitude,last_verified,notes\n"
                "ARC_01.2900S_036.8000E,-1.2915,36.8025,2026-10-22,Satellite pin adjusted\n"
            )
            overlay_file.write_text(overlay_content, encoding="utf-8")

            updated = apply_verification_overlay(records, overlay_path=overlay_file)

            # Verified station coordinates must be replaced
            v_site = next(r for r in updated if r["station_id"] == "ARC_01.2900S_036.8000E")
            self.assertEqual(v_site["latitude"], -1.2915)
            self.assertEqual(v_site["longitude"], 36.8025)
            self.assertEqual(v_site["last_verified"], "2026-10-22")

            # Unverified station remains untouched
            unv_site = next(r for r in updated if r["station_id"] == "ARC_UNVERIFIED")
            self.assertEqual(unv_site["latitude"], -1.3000)
            self.assertEqual(unv_site["longitude"], 36.8100)
            self.assertEqual(unv_site["last_verified"], "")

            # Confirm overlay file on disk was NOT modified (read-only)
            self.assertEqual(overlay_file.read_text(encoding="utf-8"), overlay_content)

    def test_cross_operator_colocation_flag(self):
        """Stations within colocation threshold (100m) are flagged co_located with peer ID."""
        arc_stations = [
            {
                "operator": "ARC Ride",
                "station_id": "ARC_01",
                "latitude": -1.2900,
                "longitude": 36.8000,
                "is_removed": False,
            },
            {
                "operator": "ARC Ride",
                "station_id": "ARC_FAR",
                "latitude": -1.4000,
                "longitude": 36.9000,
                "is_removed": False,
            },
        ]
        spiro_stations = [
            {
                "operator": "Spiro",
                "station_id": "SPIRO_01",
                # 50m north of ARC_01: -1.2900 + (50 / 111195) ≈ -1.28955
                "latitude": -1.28955,
                "longitude": 36.8000,
                "is_removed": False,
            }
        ]

        detect_colocation(arc_stations, spiro_stations, colocation_distance_meters=100.0)

        # ARC_01 and SPIRO_01 are ~50m apart -> co_located
        self.assertTrue(arc_stations[0]["co_located"])
        self.assertEqual(arc_stations[0]["co_located_with"], "SPIRO_01")

        self.assertTrue(spiro_stations[0]["co_located"])
        self.assertEqual(spiro_stations[0]["co_located_with"], "ARC_01")

        # ARC_FAR is ~16km away -> not co-located
        self.assertFalse(arc_stations[1]["co_located"])
        self.assertEqual(arc_stations[1]["co_located_with"], "")

    def test_markers_closer_than_id_precision_collision_rule_and_stability(self):
        """Two markers closer than 4dp ID precision collide deterministically and maintain stable IDs across runs."""
        # Coordinates ~4.5m apart: both round to -1.2345, 36.9876
        marker_1_coords = (-1.23451, 36.98761)
        marker_2_coords = (-1.23454, 36.98764)

        payload_day1 = {
            "cities": [{"id": "c1", "name": "Nairobi", "country": "KE"}],
            "markers": [
                {
                    "name": "Site Alpha",
                    "address": "Street Alpha",
                    "coordinates": f"{marker_1_coords[0]}, {marker_1_coords[1]}",
                    "categoryId": "swap-station",
                    "cityId": "c1",
                },
                {
                    "name": "Site Beta",
                    "address": "Street Beta",
                    "coordinates": f"{marker_2_coords[0]}, {marker_2_coords[1]}",
                    "categoryId": "swap-station",
                    "cityId": "c1",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            inbox_dir = base_dir / "data" / "inbox" / "spiro"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            input_file = inbox_dir / "2026-10-21_spiro.json"
            with open(input_file, "w", encoding="utf-8") as f:
                json.dump(payload_day1, f)

            # --- Run 1: First Ingestion ---
            records_run1, report_run1, diff_run1 = ingest_operator(
                operator="spiro",
                input_path=input_file,
                retrieved_on="2026-10-21",
                base_dir=base_dir,
            )

            self.assertEqual(len(records_run1), 2)
            self.assertEqual(diff_run1.new_count, 2)

            site_alpha = next(r for r in records_run1 if r["name"] == "Site Alpha")
            site_beta = next(r for r in records_run1 if r["name"] == "Site Beta")

            # Deterministic collision rule: canonical sort (-1.23454 < -1.23451) assigns base ID to Beta and -2 to Alpha
            self.assertEqual(site_beta["station_id"], "SPIRO_01.2345S_036.9876E")
            self.assertEqual(site_alpha["station_id"], "SPIRO_01.2345S_036.9876E-2")
            self.assertNotEqual(site_alpha["station_id"], site_beta["station_id"])

            # --- Run 2: Second Identical Ingestion ---
            input_file_day2 = inbox_dir / "2026-10-22_spiro.json"
            with open(input_file_day2, "w", encoding="utf-8") as f:
                json.dump(payload_day1, f)

            records_run2, report_run2, diff_run2 = ingest_operator(
                operator="spiro",
                input_path=input_file_day2,
                retrieved_on="2026-10-22",
                base_dir=base_dir,
            )

            # Every ID must remain completely stable vs previous snapshot
            self.assertEqual(diff_run2.new_count, 0)
            self.assertEqual(diff_run2.moved_count, 0)
            self.assertEqual(diff_run2.removed_count, 0)
            self.assertEqual(diff_run2.unchanged_count, 2)

            site_alpha_run2 = next(r for r in records_run2 if r["name"] == "Site Alpha")
            site_beta_run2 = next(r for r in records_run2 if r["name"] == "Site Beta")

            self.assertEqual(site_beta_run2["station_id"], "SPIRO_01.2345S_036.9876E")
            self.assertEqual(site_alpha_run2["station_id"], "SPIRO_01.2345S_036.9876E-2")
            self.assertEqual(site_alpha_run2["first_seen"], "2026-10-21")
            self.assertEqual(site_alpha_run2["last_seen"], "2026-10-22")
            self.assertEqual(site_beta_run2["first_seen"], "2026-10-21")
            self.assertEqual(site_beta_run2["last_seen"], "2026-10-22")

    def test_run_report_lists_outside_kenya_dropped_records_up_to_twenty(self):
        """Run report lists records dropped outside Kenya with name and coordinates, capped at 20."""
        # Create payload with 25 markers in Cairo (outside Kenya) and 1 valid Kenya marker
        markers = []
        for i in range(25):
            markers.append({
                "name": f"Foreign Station {i+1}",
                "address": "Cairo, Egypt",
                "coordinates": f"{30.0444 + i * 0.001}, 31.2357",
                "categoryId": "swap-station",
                "cityId": "c_foreign",
            })
        markers.append({
            "name": "Nairobi Hub",
            "address": "Kilimani",
            "coordinates": "-1.2900, 36.7900",
            "categoryId": "swap-station",
            "cityId": "c_kenya",
        })

        payload = {
            "cities": [
                {"id": "c_foreign", "name": "Cairo", "country": "KE"},  # marked KE to reach coordinate check
                {"id": "c_kenya", "name": "Nairobi", "country": "KE"},
            ],
            "markers": markers,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            inbox_dir = base_dir / "data" / "inbox" / "spiro"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            input_file = inbox_dir / "foreign_payload.json"
            with open(input_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)

            records, report, diff = ingest_operator(
                operator="spiro",
                input_path=input_file,
                retrieved_on="2026-10-23",
                base_dir=base_dir,
            )

            self.assertEqual(len(records), 1)
            self.assertEqual(report.dropped_by_reason["outside_kenya"], 25)
            self.assertEqual(len(report.outside_kenya_dropped), 25)

            # Test format_full_run_report output
            from scripts.ingest_operator import format_full_run_report
            report_text = format_full_run_report(
                operator="Spiro",
                run_date="2026-10-23",
                execution_time="2026-10-23T12:00:00",
                report=report,
                diff=diff,
                total_registry_count=1,
                active_count=1,
                removed_count=0,
                colocated_count=0,
                gate_status="PASSED",
                warnings=[],
            )

            self.assertIn("Records Dropped Outside Kenya (at most 20):", report_text)
            self.assertIn("Foreign Station 1 (30.0444, 31.2357)", report_text)
            self.assertIn("Foreign Station 20", report_text)
            self.assertNotIn("Foreign Station 21", report_text)  # Capped at 20
            self.assertIn("... and 5 more", report_text)


if __name__ == "__main__":
    unittest.main()
