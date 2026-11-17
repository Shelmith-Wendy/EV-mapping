"""Unit tests for ARC Ride adapter and manual fetch constraints."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.ingestion.adapters.arcride import (
    ARCRIDE_DEFAULT_URL,
    adapt_arcride,
    fetch_arcride_cabinets,
    make_cabinet_token,
    redact_arcride_payload,
    redact_arcride_record,
)
from scripts.ingestion.config import BASE_COLUMNS, IngestionConfig
from scripts.ingestion.core import IngestionReport


class TestArcRideAdapter(unittest.TestCase):
    """Test ARC Ride payload adaptation, spatial clustering, and in-memory unit counts."""

    @classmethod
    def setUpClass(cls):
        fixture_path = Path(__file__).parent / "fixtures" / "synthetic_arcride.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            cls.synthetic_payload = json.load(f)

    def test_synthetic_arcride_adaptation_and_clustering(self):
        report = IngestionReport()
        sites = adapt_arcride(
            self.synthetic_payload,
            retrieved_on="2026-10-23",
            report=report,
        )

        # Drop counts verification
        self.assertEqual(report.dropped_by_reason["unnamed_location"], 1)
        self.assertEqual(report.dropped_by_reason["invalid_coordinates"], 1)
        self.assertEqual(report.dropped_by_reason["outside_kenya"], 1)
        # Kept: 4 valid records
        self.assertEqual(report.kept_count, 4)

        # Clustered into 2 distinct physical sites
        self.assertEqual(len(sites), 2)

        # Find Nairobi Kilimani site
        kilimani_site = next(s for s in sites if "Solar" in s["name"])
        # Casing variants: "Solar Point Hub" was seen 2x, "SOLAR POINT HUB" was seen 1x
        self.assertEqual(kilimani_site["name"], "Solar Point Hub")
        self.assertEqual(kilimani_site["operator"], "ARC Ride")
        self.assertEqual(kilimani_site["address"], "Kilimani, Nairobi, Kenya")
        self.assertEqual(kilimani_site["source"], ARCRIDE_DEFAULT_URL)
        self.assertEqual(kilimani_site["retrieved_on"], "2026-10-23")
        # Distinct cabinets: synthetic-dev-001 and synthetic-dev-002 -> 2
        self.assertEqual(kilimani_site["cabinet_count"], 2)

        # Find Kisumu site
        kisumu_site = next(s for s in sites if "Lakeside" in s["name"])
        self.assertEqual(kisumu_site["name"], "Lakeside Station")
        self.assertEqual(kisumu_site["cabinet_count"], 1)

        # Verify output columns match BASE_COLUMNS
        for site in sites:
            self.assertEqual(tuple(site.keys()), BASE_COLUMNS)

    def test_manual_fetch_single_shot_and_honest_ua(self):
        """Rule 3: Single-shot request with honest UA from config, no retries."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.text = '{"results": []}'
        mock_response.json.return_value = {"results": [{"name": "Mock Station"}]}

        config = IngestionConfig(contact_email="test@example.com")
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldown_file = Path(tmpdir) / ".cooldown"

            with patch("requests.get", return_value=mock_response) as mock_get:
                results = fetch_arcride_cabinets(
                    config=config,
                    cooldown_path=cooldown_file,
                )
                self.assertEqual(len(results), 1)
                self.assertEqual(mock_get.call_count, 1)  # Exactly one request
                headers_sent = mock_get.call_args[1]["headers"]
                self.assertIn("test@example.com", headers_sent["User-Agent"])

    def test_manual_fetch_cooldown_enforcement(self):
        """Rule 3: Enforce minimum interval between fetches."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {"results": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            cooldown_file = Path(tmpdir) / ".cooldown"
            # Write recent timestamp
            cooldown_file.write_text(str(time.time()), encoding="utf-8")

            with patch("requests.get", return_value=mock_response):
                # Should fail without force
                with self.assertRaises(RuntimeError) as ctx:
                    fetch_arcride_cabinets(
                        cooldown_path=cooldown_file,
                        min_interval_seconds=3600,
                        force=False,
                    )
                self.assertIn("minimum cooldown", str(ctx.exception))

                # Should succeed with force=True
                results = fetch_arcride_cabinets(
                    cooldown_path=cooldown_file,
                    min_interval_seconds=3600,
                    force=True,
                )
                self.assertEqual(results, [])

    def test_manual_fetch_403_stops_immediately(self):
        """Rule 3: Stop immediately on any 403; never retry or attempt to bypass."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {}
        mock_response.text = "Forbidden"

        with tempfile.TemporaryDirectory() as tmpdir:
            cooldown_file = Path(tmpdir) / ".cooldown"
            with patch("requests.get", return_value=mock_response):
                with self.assertRaises(PermissionError) as ctx:
                    fetch_arcride_cabinets(cooldown_path=cooldown_file)
                self.assertIn("403 Forbidden", str(ctx.exception))

    def test_manual_fetch_challenge_stops_immediately(self):
        """Rule 3: Stop immediately on challenge or bot detection."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"cf-mitigated": "challenge"}
        mock_response.text = "Just a moment... cloudflare challenge"

        with tempfile.TemporaryDirectory() as tmpdir:
            cooldown_file = Path(tmpdir) / ".cooldown"
            with patch("requests.get", return_value=mock_response):
                with self.assertRaises(PermissionError) as ctx:
                    fetch_arcride_cabinets(cooldown_path=cooldown_file)
                self.assertIn("challenge", str(ctx.exception).lower())

    def test_one_way_cabinet_token_properties(self):
        """Tokens are deterministic within run (same salt) and non-invertible across runs."""
        salt_a = "salt_alpha_001"
        salt_b = "salt_beta_002"

        # Deterministic with same salt
        tok_1 = make_cabinet_token("device-101", salt_a)
        tok_2 = make_cabinet_token("device-101", salt_a)
        self.assertEqual(tok_1, tok_2)
        self.assertTrue(tok_1.startswith("cab_"))

        # Distinct for different IDs
        tok_3 = make_cabinet_token("device-102", salt_a)
        self.assertNotEqual(tok_1, tok_3)

        # Non-invertible across different per-run salts
        tok_diff_salt = make_cabinet_token("device-101", salt_b)
        self.assertNotEqual(tok_1, tok_diff_salt)

    def test_redact_arcride_payload_preserves_cabinet_counts(self):
        """Redacting payload replaces identifiers with tokens and preserves distinct counting."""
        redacted, salt = redact_arcride_payload(self.synthetic_payload)

        # Ensure no forbidden keys exist in results
        for r in redacted["results"]:
            self.assertNotIn("imei_number", r)
            self.assertNotIn("serial_number", r)

        # Adapt both raw and redacted payloads
        report_raw = IngestionReport()
        sites_raw = adapt_arcride(self.synthetic_payload, retrieved_on="2026-10-23", report=report_raw)

        report_redacted = IngestionReport()
        sites_redacted = adapt_arcride(redacted, retrieved_on="2026-10-23", report=report_redacted)

        # Both produce identical cluster counts and identical cabinet counts per site
        self.assertEqual(len(sites_raw), len(sites_redacted))
        for raw_site, red_site in zip(sites_raw, sites_redacted):
            self.assertEqual(raw_site["name"], red_site["name"])
            self.assertEqual(raw_site["cabinet_count"], red_site["cabinet_count"])
            self.assertEqual(raw_site["latitude"], red_site["latitude"])
            self.assertEqual(raw_site["longitude"], red_site["longitude"])


if __name__ == "__main__":
    unittest.main()
