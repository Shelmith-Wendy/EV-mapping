"""ARC Ride adapter: parses cabinet feeds, clusters sites, and encapsulates manual network fetch."""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

from scripts.ingestion.config import (
    ARCRIDE_DEFAULT_COOLDOWN_SECONDS,
    ARCRIDE_DEFAULT_TIMEOUT_SECONDS,
    ARCRIDE_DEFAULT_URL,
    DEFAULT_MERGE_DISTANCE_METERS,
    FORBIDDEN_IDENTIFIER_COLUMNS,
    IngestionConfig,
    load_config,
)
from scripts.ingestion.core import (
    IngestionReport,
    atomic_write_json,
    clean_display_text,
    cluster_sites,
    is_in_kenya_box,
    parse_coordinates,
)


def make_cabinet_token(identifier: str, salt: str) -> str:
    """Generate a one-way deterministic token for a cabinet identifier within a single run.

    The salt is randomly generated per run, held only in local memory, and never stored
    on disk, logged, or returned. This ensures identifiers are strictly irreversible while
    preserving distinct-cabinet counting within the run.
    """
    digest = hashlib.sha256(f"{salt}:{identifier}".encode("utf-8")).hexdigest()[:16]
    return f"cab_{digest}"


def redact_arcride_record(record: dict[str, Any], salt: str) -> dict[str, Any]:
    """Redact device identifiers from a single ARC Ride record.

    Extracts raw identifier (imei_number, serial_number, device_id), generates a one-way token
    with the per-run salt, sets 'cabinet_token', and removes all forbidden identifier keys.
    """
    clean_record = dict(record)

    # Check for existing cabinet_token
    existing_token = clean_record.get("cabinet_token")

    raw_id = (
        clean_record.get("imei_number")
        or clean_record.get("serial_number")
        or clean_record.get("device_id")
    )

    if raw_id is not None and str(raw_id).strip():
        clean_record["cabinet_token"] = make_cabinet_token(str(raw_id).strip(), salt)
    elif existing_token:
        clean_record["cabinet_token"] = existing_token

    # Strip all forbidden identifier keys
    for k in list(clean_record.keys()):
        k_str = str(k).lower().strip()
        if (
            k_str in FORBIDDEN_IDENTIFIER_COLUMNS
            or "imei" in k_str
            or "serial" in k_str
            or "mac_address" in k_str
            or "device_id" in k_str
        ):
            del clean_record[k]

    return clean_record


def redact_arcride_payload(
    raw_payload: list[dict[str, Any]] | dict[str, Any],
    salt: str | None = None,
) -> tuple[list[dict[str, Any]] | dict[str, Any], str]:
    """Return a redacted copy of an ARC Ride payload where all device identifiers are replaced.

    The random salt is generated per-run if not supplied and must never be persisted.

    Returns:
        tuple of (redacted_payload, salt_used)
    """
    effective_salt = salt or secrets.token_hex(16)
    if isinstance(raw_payload, dict):
        redacted_dict = dict(raw_payload)
        if "results" in raw_payload and isinstance(raw_payload["results"], list):
            redacted_dict["results"] = [
                redact_arcride_record(r, effective_salt) if isinstance(r, dict) else r
                for r in raw_payload["results"]
            ]
        return redacted_dict, effective_salt
    elif isinstance(raw_payload, list):
        redacted_list = [
            redact_arcride_record(r, effective_salt) if isinstance(r, dict) else r
            for r in raw_payload
        ]
        return redacted_list, effective_salt
    else:
        return raw_payload, effective_salt


def adapt_arcride(
    raw_data: list[dict[str, Any]] | dict[str, Any],
    retrieved_on: str | None = None,
    source_url: str | None = None,
    report: IngestionReport | None = None,
) -> list[dict[str, Any]]:
    """Transform raw ARC Ride cabinet records into normalized sites clustered by location.

    Rules & Guarantees:
    - Input: raw JSON results list or dict containing 'results'.
    - Skips records with missing names, invalid coordinates, or coordinates outside Kenya.
    - Clusters sites within 30 metres by geographic location.
    - cabinet_count is the count of distinct registered cabinets per site.
    - Device identifiers (IMEI, serial) are held in memory ONLY to count distinct cabinets;
      they are NEVER included in the returned site dictionaries.
    - Operator is 'ARC Ride', source is the feed address, retrieved_on comes from caller.
    """
    if report is None:
        report = IngestionReport()

    if isinstance(raw_data, dict):
        records = raw_data.get("results", [])
    elif isinstance(raw_data, list):
        records = raw_data
    else:
        raise ValueError(f"Expected list or dict for ARC Ride payload, got {type(raw_data).__name__}")

    effective_date = retrieved_on or date.today().isoformat()
    effective_source = source_url or ARCRIDE_DEFAULT_URL

    valid_observations: list[dict[str, Any]] = []

    for record_idx, record in enumerate(records):
        raw_lat = record.get("lat") if "lat" in record and record.get("lat") is not None else record.get("latitude")
        raw_lng = record.get("lng") if "lng" in record and record.get("lng") is not None else record.get("longitude")
        coords = parse_coordinates(raw_lat, raw_lng)
        if coords is None:
            report.record_dropped("invalid_coordinates")
            continue

        lat, lng = coords
        if not is_in_kenya_box(lat, lng):
            report.record_dropped("outside_kenya")
            report.record_outside_kenya(clean_display_text(record.get("name")), lat, lng)
            continue

        clean_name = clean_display_text(record.get("name"))
        if not clean_name:
            report.record_dropped("unnamed_location")
            continue

        # In-memory unit identifier for cabinet count deduplication
        existing_cab_count = record.get("cabinet_count")
        if existing_cab_count and str(existing_cab_count).strip().isdigit() and not record.get("imei_number"):
            cab_n = int(str(existing_cab_count).strip())
            for c_idx in range(cab_n):
                valid_observations.append({
                    "operator": "ARC Ride",
                    "name": clean_name,
                    "address": record.get("address") or "",
                    "latitude": lat,
                    "longitude": lng,
                    "source": effective_source,
                    "retrieved_on": effective_date,
                    "_device_id": f"synthetic-cab-{record_idx}-{c_idx}",
                })
            report.record_kept()
            continue

        device_id = (
            record.get("cabinet_token")
            or record.get("imei_number")
            or record.get("serial_number")
            or record.get("device_id")
            or f"synthetic-unidentified-{record_idx}"
        )

        valid_observations.append({
            "operator": "ARC Ride",
            "name": clean_name,
            "address": record.get("address") or "",
            "latitude": lat,
            "longitude": lng,
            "source": effective_source,
            "retrieved_on": effective_date,
            "_device_id": str(device_id),
        })
        report.record_kept()

    # Cluster observations by location within 30m; device IDs held in memory only
    clustered_sites = cluster_sites(
        valid_observations,
        merge_distance_meters=DEFAULT_MERGE_DISTANCE_METERS,
        unit_key_fn=lambda r: r.get("_device_id"),
    )

    return clustered_sites


def fetch_arcride_cabinets(
    source_url: str = ARCRIDE_DEFAULT_URL,
    config: IngestionConfig | None = None,
    cooldown_path: Path | str | None = None,
    min_interval_seconds: int = ARCRIDE_DEFAULT_COOLDOWN_SECONDS,
    force: bool = False,
    save_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Perform a manual single-shot fetch from ARC Ride API, strictly adhering to Rule 3.

    Rule 3 Requirements:
    1. Off by default (must be manually invoked).
    2. Exactly one request (no retry loops).
    3. Honest user agent from local untracked config.
    4. Minimum interval between fetches.
    5. Stop immediately on any 403 or challenge page.
    6. Device identifiers are redacted with a random per-run salt before saving or returning.
    """
    cfg = config or load_config()

    # Determine cooldown file path
    if cooldown_path is not None:
        state_file = Path(cooldown_path)
    else:
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        state_file = repo_root / "data" / "inbox" / "arcride" / ".last_fetch_timestamp"

    now = time.time()
    if not force and state_file.is_file():
        try:
            last_timestamp = float(state_file.read_text(encoding="utf-8").strip())
            elapsed = now - last_timestamp
            if elapsed < min_interval_seconds:
                remaining = int(min_interval_seconds - elapsed)
                raise RuntimeError(
                    f"ARC Ride fetch blocked: minimum cooldown of {min_interval_seconds}s not reached "
                    f"({remaining}s remaining). Use force=True to override."
                )
        except (ValueError, OSError) as err:
            if not isinstance(err, RuntimeError):
                pass  # Ignore corrupted timestamp file and proceed

    headers = {
        "User-Agent": cfg.user_agent,
        "Accept": "application/json",
    }

    try:
        response = requests.get(
            source_url,
            headers=headers,
            timeout=30,
        )
    except Exception as err:
        raise RuntimeError(f"ARC Ride network request failed: {err}") from err

    # Stop immediately on 403 Forbidden
    if response.status_code == 403:
        raise PermissionError(
            f"ARC Ride returned HTTP 403 Forbidden. Stopping immediately per project rules. URL: {source_url}"
        )

    # Stop immediately on challenge / bot detection pages
    content_snippet = response.text[:1000].lower()
    if (
        "challenge" in content_snippet
        or "cf-mitigated" in response.headers
        or "cloudflare" in content_snippet and response.status_code != 200
    ):
        raise PermissionError(
            "ARC Ride returned a challenge or bot mitigation page. Stopping immediately per project rules."
        )

    response.raise_for_status()

    # Record successful fetch timestamp
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(str(now), encoding="utf-8")
    except OSError:
        pass

    data = response.json()
    if not isinstance(data, dict) or "results" not in data:
        raise ValueError("Invalid ARC Ride API response: missing 'results' key")

    # Generate random per-run salt in memory only; never stored
    per_run_salt = secrets.token_hex(16)
    redacted_data, _ = redact_arcride_payload(data, salt=per_run_salt)

    if save_path is not None:
        atomic_write_json(save_path, redacted_data)

    return redacted_data["results"]
