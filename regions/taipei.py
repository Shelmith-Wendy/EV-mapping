"""Taipei region adapter: city open-data CSVs + district boundaries."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path

import requests
from urllib3.exceptions import InsecureRequestWarning

from regions.base import RegionConfig, fix_invalid_geometries

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "taipei"
SWAP_CSV = RAW_DIR / "swap.csv"
CHARGE_CSV = RAW_DIR / "charge.csv"
DISTRICTS_GEOJSON = RAW_DIR / "taipei_districts.geojson"
GEOCODE_CACHE = RAW_DIR / "geocode_cache.json"
GEOCODE_FAILURES = RAW_DIR / "geocode_failures.json"

SWAP_URL = (
    "https://data.taipei/api/frontstage/tpeod/dataset/resource.download"
    "?rid=c0f07b6c-ff55-4fae-a390-ddfe9374d4d7"
)
CHARGE_URL = (
    "https://data.taipei/api/frontstage/tpeod/dataset/resource.download"
    "?rid=c940eb09-c131-46e5-837c-5c96e7897253"
)
TOWN_BOUNDARIES_URL = (
    "https://raw.githubusercontent.com/g0v/twgeojson/master/json/twTown1982.geo.json"
)

STATION_TYPE_MAP = {
    "swap": "swap_station",
    "charge": "standard_charger",
}

DISTRICT_RE = re.compile(r"[台臺]北市([^\s\d]+?區)")
ROAD_RE = re.compile(
    r"[台臺]北市(?P<district>[^\s\d]+?區)"
    r"(?P<road>[^\d號]+?(?:路|街|大道|巷|道))"
)
TAIPEI_COUNTY_NAMES = {"台北市", "臺北市"}
TAIPEI_VIEWBOX = ((24.96, 121.45), (25.21, 121.67))
DATASET_SOURCE = "taipei_open_data"
DATASET_SOURCE_LABEL = "Taipei Open Data"

# Locked English display names. Chinese `district` stays the join key everywhere.
DISTRICT_EN = {
    "中山區": "Zhongshan",
    "中正區": "Zhongzheng",
    "信義區": "Xinyi",
    "內湖區": "Neihu",
    "北投區": "Beitou",
    "南港區": "Nangang",
    "士林區": "Shilin",
    "大同區": "Datong",
    "大安區": "Da'an",
    "文山區": "Wenshan",
    "松山區": "Songshan",
    "萬華區": "Wanhua",
}


def download_csv(url: str, path: Path) -> None:
    response = requests.get(url, timeout=120, verify=False)
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)


def load_csv_rows(path: Path, url: str) -> list[dict[str, str]]:
    force = os.environ.get("KENYA_EV_FORCE_FETCH", "").strip().lower() in {"1", "true", "yes"}
    if force or not path.exists() or path.stat().st_size == 0:
        download_csv(url, path)
    text = path.read_bytes().decode("big5")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def extract_district(address: str) -> str | None:
    match = DISTRICT_RE.search(address or "")
    return match.group(1) if match else None


def geocode_queries_for(address: str) -> list[str]:
    """Full address first, then progressively coarser fallbacks Nominatim can resolve."""
    address = (address or "").strip()
    queries = [address, f"{address}, Taiwan"]
    road_match = ROAD_RE.search(address)
    if road_match:
        district = road_match.group("district")
        road = road_match.group("road").strip(" ,、")
        queries.append(f"臺北市{district}{road}")
        queries.append(f"{road}, {district}, 台北")
        queries.append(f"{road}, {district}, Taipei, Taiwan")
    district = extract_district(address)
    if district:
        queries.append(f"臺北市{district}")
        queries.append(f"{district}, Taipei, Taiwan")
    # Deduplicate while preserving order.
    seen = set()
    ordered = []
    for query in queries:
        if query and query not in seen:
            seen.add(query)
            ordered.append(query)
    return ordered


def load_geocode_cache() -> dict[str, dict]:
    if not GEOCODE_CACHE.exists():
        return {}
    return json.loads(GEOCODE_CACHE.read_text(encoding="utf-8"))


def save_geocode_cache(cache: dict[str, dict]) -> None:
    GEOCODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    GEOCODE_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def coords_in_taipei(lat: float, lng: float) -> bool:
    (south, west), (north, east) = TAIPEI_VIEWBOX
    pad = 0.05
    return (south - pad) <= lat <= (north + pad) and (west - pad) <= lng <= (east + pad)


def geocode_address(geocode, address: str, cache: dict[str, dict]) -> tuple[float | None, float | None]:
    from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable

    if address in cache:
        hit = cache[address]
        lat, lng = hit.get("latitude"), hit.get("longitude")
        if lat is not None and lng is not None and coords_in_taipei(float(lat), float(lng)):
            return float(lat), float(lng)
        # Stale/wrong cache (e.g. out-of-city hit) — retry below.
        if lat is not None and lng is not None and not coords_in_taipei(float(lat), float(lng)):
            cache.pop(address, None)

    for query in geocode_queries_for(address):
        location = None
        for _ in (1, 2):
            try:
                # Unbounded search — Taipei house numbers often miss with bounded=True.
                location = geocode(query, language="zh-TW")
                break
            except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError):
                continue
        if location is None:
            continue
        lat, lng = float(location.latitude), float(location.longitude)
        if not coords_in_taipei(lat, lng):
            continue
        cache[address] = {
            "latitude": lat,
            "longitude": lng,
            "display_name": location.address,
            "query_used": query,
        }
        return lat, lng

    cache[address] = {
        "latitude": None,
        "longitude": None,
        "display_name": None,
        "query_used": geocode_queries_for(address)[-1] if address else None,
    }
    return None, None


class TaipeiStationSource:
    def fetch(self) -> list[dict]:
        from common import nominatim_geocode

        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        swap_rows = load_csv_rows(SWAP_CSV, SWAP_URL)
        charge_rows = load_csv_rows(CHARGE_CSV, CHARGE_URL)

        records = []
        for row in swap_rows:
            records.append(self._row_to_station(row, "swap", "taipei_swap", retrieved_at))
        for row in charge_rows:
            records.append(self._row_to_station(row, "charge", "taipei_charge", retrieved_at))

        geocode = nominatim_geocode()
        cache = load_geocode_cache()
        failures = []
        geocoded = []

        for index, station in enumerate(records, start=1):
            lat, lng = geocode_address(geocode, station["address_raw"], cache)
            if lat is None or lng is None:
                failures.append({
                    "station_id": station["station_id"],
                    "name": station["name"],
                    "address_raw": station["address_raw"],
                })
                print(
                    f"GEOCODE FAIL [{index}/{len(records)}] "
                    f"{station['station_id']}: {station['address_raw']}",
                    flush=True,
                )
            else:
                station["latitude"] = lat
                station["longitude"] = lng
                geocoded.append(station)
            if index % 25 == 0 or index == len(records):
                save_geocode_cache(cache)
                print(
                    f"geocode progress {index}/{len(records)} "
                    f"(ok={len(geocoded)} fail={len(failures)})",
                    flush=True,
                )

        save_geocode_cache(cache)
        GEOCODE_FAILURES.write_text(
            json.dumps(failures, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"taipei fetch: {len(geocoded)} geocoded / {len(records)} total "
            f"({len(failures)} logged failures in {GEOCODE_FAILURES})"
        )
        return geocoded

    def _row_to_station(self, row: dict[str, str], native_type: str, id_prefix: str, retrieved_at: str) -> dict:
        address = (row.get("地址") or "").strip()
        city = (row.get("縣市") or "").strip()
        district = extract_district(address)
        return {
            "station_id": f"{id_prefix}_{row.get('序號', '').strip()}",
            "name": (row.get("名稱") or "").strip(),
            "station_type": STATION_TYPE_MAP[native_type],
            "locality": district or city or "臺北市",
            "address_raw": address,
            "source_name": DATASET_SOURCE,
            "operator": (row.get("廠商") or "").strip(),
            "retrieved_at": retrieved_at,
        }


class TaipeiBoundarySource:
    def load(self) -> dict:
        if not DISTRICTS_GEOJSON.exists() or DISTRICTS_GEOJSON.stat().st_size == 0:
            self._build_districts_file()

        collection = json.loads(DISTRICTS_GEOJSON.read_text(encoding="utf-8"))
        features = collection["features"]
        print(f"features: {len(features)}")
        names = [feature["properties"]["district"] for feature in features]
        if len(features) == 12:
            print(f"district set matches all 12: {names}")

        apply_district_en(features)
        fix_invalid_geometries(features, "district")
        return {"type": "FeatureCollection", "features": features}

    def _build_districts_file(self) -> None:
        print(f"downloading town boundaries from {TOWN_BOUNDARIES_URL}")
        response = requests.get(TOWN_BOUNDARIES_URL, timeout=180)
        response.raise_for_status()
        source = response.json()
        features = []
        for feature in source["features"]:
            props = feature["properties"]
            county = props.get("COUNTYNAME", "")
            town_id = str(props.get("TOWNID", ""))
            if county not in TAIPEI_COUNTY_NAMES and not town_id.startswith("630"):
                continue
            zh_name = props["TOWNNAME"]
            features.append({
                "type": "Feature",
                "properties": {
                    "district": zh_name,
                    "district_en": DISTRICT_EN[zh_name],
                },
                "geometry": feature["geometry"],
            })
        apply_district_en(features)
        DISTRICTS_GEOJSON.parent.mkdir(parents=True, exist_ok=True)
        DISTRICTS_GEOJSON.write_text(
            json.dumps(
                {"type": "FeatureCollection", "features": features},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        print(f"wrote {DISTRICTS_GEOJSON} ({len(features)} districts)")


def apply_district_en(features: list[dict]) -> None:
    """Stamp locked English labels onto district features; fail if any name is unknown."""
    missing = []
    for feature in features:
        zh_name = feature["properties"]["district"]
        en_name = DISTRICT_EN.get(zh_name)
        if not en_name:
            missing.append(zh_name)
            continue
        feature["properties"]["district_en"] = en_name
    if missing:
        raise ValueError(f"missing DISTRICT_EN entries for: {sorted(set(missing))}")


CONFIG = RegionConfig(
    region_id="taipei",
    display_name="Taipei",
    cluster="east_asia",
    bounds=TAIPEI_VIEWBOX,
    tiers=["district"],
    expected_unit_count=12,
    station_type_map=STATION_TYPE_MAP,
    geocode_country_hint="Taiwan",
    dataset_source=DATASET_SOURCE,
    dataset_source_label=DATASET_SOURCE_LABEL,
    station_source=TaipeiStationSource(),
    boundary_source=TaipeiBoundarySource(),
)
