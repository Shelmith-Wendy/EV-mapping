#!/usr/bin/env python3
"""Reverse-geocode ARC Ride & Spiro swap station coordinates to derive clean town labels.

Uses OpenStreetMap Nominatim with an honest User-Agent and a polite 1.05s delay.
Saves progress incrementally to data/public/kenya/towns_reverse_geocoded.json so
the run is fully resumable if interrupted.

Once complete, updates:
  - data/public/kenya/stations_with_county.json
  - data/public/kenya/town_summary.json
  - data/public/kenya/manifest.json
And mirrors all files to app/static/data/public/kenya/.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regions.kenya import clean_town, pick_town

DATA_DIR = ROOT / "data" / "public" / "kenya"
STATIC_DIR = ROOT / "app" / "static" / "data" / "public" / "kenya"

STATIONS_PATH = DATA_DIR / "stations_with_county.json"
CACHE_PATH = DATA_DIR / "towns_reverse_geocoded.json"
TOWN_SUMMARY_PATH = DATA_DIR / "town_summary.json"
MANIFEST_PATH = DATA_DIR / "manifest.json"

USER_AGENT = "kenya-ev-map/0.1 (Kenya EV charging station mapping; academic research)"
REQUEST_GAP_SECONDS = 1.05
REQUEST_TIMEOUT_SECONDS = 12


def coord_key(lat: float, lng: float) -> str:
    return f"{round(lat, 5)},{round(lng, 5)}"


def load_cache() -> dict[str, dict]:
    if not CACHE_PATH.exists():
        return {}
    try:
        rows = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return {r["coord_key"]: r for r in rows if "coord_key" in r}
    except Exception as exc:
        print(f"Warning: could not read cache ({exc}), starting fresh")
        return {}


def save_cache(cache: dict[str, dict]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(cache.values(), key=lambda r: r.get("coord_key", ""))
    text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    CACHE_PATH.write_text(text, encoding="utf-8", newline="\n")
    STATIC_CACHE = STATIC_DIR / "towns_reverse_geocoded.json"
    STATIC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    STATIC_CACHE.write_text(text, encoding="utf-8", newline="\n")


def query_nominatim(lat: float, lng: float) -> tuple[dict, str | None]:
    url = f"https://nominatim.openstreetmap.org/reverse?lat={lat:.6f}&lon={lng:.6f}&format=json&accept-language=en"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(2):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("address", {}), data.get("display_name")
        except (HTTPError, URLError, TimeoutError) as err:
            if attempt == 0:
                time.sleep(2.0)
                continue
            print(f"  Warning: Nominatim error at ({lat}, {lng}): {err}", flush=True)
            return {}, None
    return {}, None


def fallback_town_from_address(address_str: str | None, county: str) -> str:
    if not address_str:
        return county
    # Remove Plus Codes (e.g. WMH6+5M7)
    cleaned = re.sub(r"^[A-Z0-9]{4,8}\+[A-Z0-9]{2,4}\s*,?", "", address_str).strip(" ,")
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    if parts:
        candidate = parts[0]
        # Ignore country or county repeats
        if candidate.lower() not in ("kenya", county.lower(), f"{county.lower()} county"):
            return clean_town(candidate, county) or candidate
    return county


def build_town_summary(stations: list[dict]) -> list[dict]:
    by_key = {}
    coords_acc = {}

    for st in stations:
        county = st.get("county") or ""
        town = st.get("town") or ""
        if not county or not town:
            continue
        key = (county, town)
        if key not in by_key:
            by_key[key] = {
                "town": town,
                "county": county,
                "fast_charger_count": 0,
                "swap_station_count": 0,
                "total_count": 0,
                "latitude": 0.0,
                "longitude": 0.0,
            }
            coords_acc[key] = [0.0, 0.0, 0]

        row = by_key[key]
        stype = st.get("station_type", "")
        if stype == "fast_charger":
            row["fast_charger_count"] += 1
        elif stype == "swap_station":
            row["swap_station_count"] += 1
        row["total_count"] += 1

        lat = st.get("final_latitude")
        lng = st.get("final_longitude")
        if lat is not None and lng is not None:
            coords_acc[key][0] += lat
            coords_acc[key][1] += lng
            coords_acc[key][2] += 1

    rows = []
    for key, row in by_key.items():
        lat_sum, lng_sum, count = coords_acc[key]
        if count > 0:
            row["latitude"] = round(lat_sum / count, 7)
            row["longitude"] = round(lng_sum / count, 7)
        rows.append(row)

    rows.sort(key=lambda r: (r["county"], r["town"]))
    return rows


def update_manifest(target_dir: Path, station_count: int) -> None:
    manifest_path = target_dir / "manifest.json"
    files = {}
    for filename in [
        "county_summary.json",
        "town_summary.json",
        "stations_with_county.json",
        "kenya_counties_display.geojson",
        "towns_reverse_geocoded.json",
    ]:
        fpath = target_dir / filename
        if fpath.exists():
            data = fpath.read_bytes()
            files[filename] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "pipeline_run_id": "merged_zeno_arcride_spiro_towns",
        "source_name": "zeno_arcride_spiro",
        "station_count": station_count,
        "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "files": files,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    print("=== Reverse-Geocoding Stations for Town Drill-Down ===", flush=True)
    stations = json.loads(STATIONS_PATH.read_text(encoding="utf-8"))
    cache = load_cache()
    print(f"Loaded {len(stations)} stations, {len(cache)} existing cached entries.", flush=True)

    pending = []
    for st in stations:
        lat = st.get("final_latitude")
        lng = st.get("final_longitude")
        if lat is None or lng is None:
            continue
        key = coord_key(lat, lng)
        if key not in cache or not cache[key].get("town"):
            pending.append((st, lat, lng, key))

    print(f"Stations to reverse-geocode: {len(pending)} (already cached: {len(stations) - len(pending)})", flush=True)

    count_processed = 0
    start_time = time.time()

    for idx, (station, lat, lng, key) in enumerate(pending, 1):
        county = station.get("county") or ""
        address_dict, display_name = query_nominatim(lat, lng)
        town = pick_town(address_dict, county, display_name)

        if not town:
            town = fallback_town_from_address(station.get("address_raw"), county)
            town_source = "address_fallback"
        else:
            town = clean_town(town, county, station.get("station_id"))
            town_source = "reverse_geocode"

        cache[key] = {
            "coord_key": key,
            "latitude": lat,
            "longitude": lng,
            "county": county,
            "town": town,
            "town_source": town_source,
            "display_name": display_name,
        }

        count_processed += 1
        if count_processed % 10 == 0 or count_processed == len(pending):
            elapsed = time.time() - start_time
            rate = count_processed / elapsed if elapsed > 0 else 0
            eta_sec = (len(pending) - count_processed) / rate if rate > 0 else 0
            print(
                f"[{count_processed}/{len(pending)}] ({count_processed/len(pending)*100:.1f}%) "
                f"{station.get('operator')} - {station.get('name')} -> {town} ({county}) "
                f"[ETA: {int(eta_sec//60)}m {int(eta_sec%60)}s]",
                flush=True,
            )
            save_cache(cache)

        time.sleep(REQUEST_GAP_SECONDS)

    # Save final cache
    save_cache(cache)
    print("Reverse geocoding complete. Applying towns to station records...", flush=True)

    # Apply towns to all stations
    for st in stations:
        lat = st.get("final_latitude")
        lng = st.get("final_longitude")
        if lat is None or lng is None:
            continue
        key = coord_key(lat, lng)
        if key in cache:
            st["town"] = cache[key]["town"]
            st["town_source"] = cache[key].get("town_source", "reverse_geocode")

    # Rebuild summaries
    town_summary = build_town_summary(stations)
    print(f"Built town summary with {len(town_summary)} unique (county, town) entries.", flush=True)

    # Write stations and summary
    for target_dir in (DATA_DIR, STATIC_DIR):
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "stations_with_county.json").write_text(
            json.dumps(stations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (target_dir / "town_summary.json").write_text(
            json.dumps(town_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        update_manifest(target_dir, len(stations))

    print(f"Successfully updated stations and town summary in {DATA_DIR} and {STATIC_DIR}.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
