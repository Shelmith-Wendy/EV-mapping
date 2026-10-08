"""Taipei region adapter: city open-data CSVs + district boundaries."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import ssl
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

from regions.base import RegionConfig, fix_invalid_geometries

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "taipei"
SWAP_CSV = RAW_DIR / "swap.csv"
CHARGE_CSV = RAW_DIR / "charge.csv"
DISTRICTS_GEOJSON = RAW_DIR / "taipei_districts.geojson"
GEOCODE_CACHE = RAW_DIR / "geocode_cache.json"
GEOCODE_FAILURES = RAW_DIR / "geocode_failures.json"
DOORPLATE_CSV = RAW_DIR / "doorplates.csv"

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
MAX_UNPLACED_STATIONS = 3

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

# 鄉鎮市區代碼 from the doorplate file. Chinese name stays the join key.
DISTRICT_CODE = {
    "松山區": "63000010",
    "信義區": "63000020",
    "大安區": "63000030",
    "中山區": "63000040",
    "中正區": "63000050",
    "大同區": "63000060",
    "萬華區": "63000070",
    "文山區": "63000080",
    "南港區": "63000090",
    "內湖區": "63000100",
    "士林區": "63000110",
    "北投區": "63000120",
}

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_SECTION_DIGIT = {
    "1": "一",
    "2": "二",
    "3": "三",
    "4": "四",
    "5": "五",
    "6": "六",
    "7": "七",
    "8": "八",
    "9": "九",
    "10": "十",
}


class _RelaxedStrictAdapter(HTTPAdapter):
    """Keep chain and hostname checks; clear only the Python 3.13 strict X.509 flag."""

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        context = ssl.create_default_context()
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        pool_kwargs["ssl_context"] = context
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)


def download_csv(url: str, path: Path) -> None:
    session = requests.Session()
    session.mount("https://", _RelaxedStrictAdapter())
    response = session.get(url, timeout=120)
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


def _ascii_digits(text: str) -> str:
    return (text or "").translate(_FULLWIDTH_DIGITS)


def _normalize_plate_text(text: str) -> str:
    text = _ascii_digits(text).replace("臺北市", "台北市")
    text = text.split("（", 1)[0].split("(", 1)[0]
    text = re.sub(r"\s+", "", text)
    text = text.replace("—段", "一段").replace("–段", "一段").replace("－段", "一段")
    return re.sub(
        r"(\d+)段",
        lambda match: _SECTION_DIGIT.get(match.group(1), match.group(1)) + "段",
        text,
    )


def _plate_number(raw: str) -> str:
    text = _ascii_digits(raw)
    paired = re.match(r"(\d+)之(\d+)號", text)
    if paired:
        return f"{int(paired.group(1))}之{int(paired.group(2))}"
    single = re.match(r"(\d+)號", text)
    if single:
        return str(int(single.group(1)))
    return ""


def _unit_number(raw: str, suffix: str) -> str:
    match = re.fullmatch(rf"(\d+){suffix}", _ascii_digits(raw).strip())
    return str(int(match.group(1))) if match else ""


def _split_suffix(suffix: str) -> tuple[str, str, str]:
    lane = alley = number = ""
    lane_match = re.match(r"(\d+)巷", suffix)
    if lane_match:
        lane = str(int(lane_match.group(1)))
        suffix = suffix[lane_match.end():]
    alley_match = re.match(r"(\d+)弄", suffix)
    if alley_match:
        alley = str(int(alley_match.group(1)))
        suffix = suffix[alley_match.end():]
    number_match = re.search(r"(\d+)(?:號之(\d+)|(?:之|-)(\d+)號|號)", suffix)
    if number_match:
        base = str(int(number_match.group(1)))
        sub = number_match.group(2) or number_match.group(3)
        number = f"{base}之{int(sub)}" if sub else base
    return lane, alley, number


def twd97_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Inverse TWD97 / TM2 zone 121 (EPSG:3826) to WGS84 degrees."""
    semi_major = 6378137.0
    semi_minor = 6356752.314245
    lon0 = math.radians(121.0)
    scale = 0.9999
    eccentricity = math.sqrt(1 - (semi_minor ** 2) / (semi_major ** 2))
    x -= 250000.0
    meridian = y / scale
    mu = meridian / (
        semi_major
        * (1 - eccentricity ** 2 / 4 - 3 * eccentricity ** 4 / 64 - 5 * eccentricity ** 6 / 256)
    )
    e1 = (1 - math.sqrt(1 - eccentricity ** 2)) / (1 + math.sqrt(1 - eccentricity ** 2))
    foot = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )
    second_e = (eccentricity * semi_major / semi_minor) ** 2
    cos_foot = math.cos(foot)
    tan_foot = math.tan(foot)
    sin_foot = math.sin(foot)
    c1 = (second_e * cos_foot) ** 2
    t1 = tan_foot ** 2
    radius = semi_major * (1 - eccentricity ** 2) / (1 - eccentricity ** 2 * sin_foot ** 2) ** 1.5
    prime = semi_major / math.sqrt(1 - eccentricity ** 2 * sin_foot ** 2)
    d = x / (prime * scale)
    lat = foot - (prime * tan_foot / radius) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * second_e) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 3 * c1 ** 2 - 252 * second_e) * d ** 6 / 720
    )
    lon = lon0 + (
        d
        - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * second_e + 24 * t1 ** 2) * d ** 5 / 120
    ) / cos_foot
    return math.degrees(lat), math.degrees(lon)


def load_doorplates() -> dict | None:
    if not DOORPLATE_CSV.is_file():
        print(f"doorplate file not found at {DOORPLATE_CSV}; using Nominatim")
        return None

    points: dict[tuple, tuple[float, float]] = {}
    groups: dict[tuple, list] = {}
    roads: dict[str, set[str]] = {}
    with DOORPLATE_CSV.open(encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            number = _plate_number(row.get("號") or "")
            road = _normalize_plate_text(row.get("街路段") or "")
            if not number or not road:
                continue
            code = row.get("鄉鎮市區代碼") or ""
            lane = _unit_number(row.get("巷") or "", "巷")
            alley = _unit_number(row.get("弄") or "", "弄")
            key = (code, road, lane, alley, number)
            easting = float(row["橫座標"])
            northing = float(row["縱座標"])
            if key not in points:
                bucket = groups.setdefault((code, road, lane, alley), [0.0, 0.0, 0])
                bucket[0] += easting
                bucket[1] += northing
                bucket[2] += 1
            if key not in points or _ascii_digits(row.get("號") or "") == f"{number}號":
                points[key] = (easting, northing)
            roads.setdefault(code, set()).add(road)

    print(f"doorplates: {len(points)} addresses from {DOORPLATE_CSV.name}")
    return {
        "points": points,
        "groups": groups,
        "roads": {code: sorted(names, key=len, reverse=True) for code, names in roads.items()},
    }


def doorplate_wgs84(plates: dict | None, address: str) -> tuple[float, float] | None:
    if not plates:
        return None
    text = _normalize_plate_text(address)
    match = re.match(r"台北市(?P<district>\S+?區)(?P<rest>.+)", text)
    if not match:
        return None
    code = DISTRICT_CODE.get(match.group("district"))
    if not code:
        return None
    rest = match.group("rest")
    road = next((name for name in plates["roads"].get(code, ()) if rest.startswith(name)), "")
    if not road:
        return None
    lane, alley, number = _split_suffix(rest[len(road):])
    if number and (code, road, lane, alley, number) in plates["points"]:
        return twd97_to_wgs84(*plates["points"][(code, road, lane, alley, number)])
    base = number.split("之")[0] if number else ""
    if base and base != number and (code, road, lane, alley, base) in plates["points"]:
        return twd97_to_wgs84(*plates["points"][(code, road, lane, alley, base)])
    if lane or alley:
        bucket = plates["groups"].get((code, road, lane, alley))
        if bucket and bucket[2]:
            return twd97_to_wgs84(bucket[0] / bucket[2], bucket[1] / bucket[2])
    return None


def geocode_address(geocode, address: str, cache: dict[str, dict], plates: dict | None) -> tuple[float | None, float | None]:
    from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable

    point = doorplate_wgs84(plates, address)
    if point is not None and coords_in_taipei(point[0], point[1]):
        lat, lng = point
        cache[address] = {
            "latitude": lat,
            "longitude": lng,
            "display_name": address,
            "query_used": "doorplate",
        }
        return lat, lng

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
        plates = load_doorplates()
        failures = []
        geocoded = []
        from_doorplate = 0

        for index, station in enumerate(records, start=1):
            lat, lng = geocode_address(geocode, station["address_raw"], cache, plates)
            if cache.get(station["address_raw"], {}).get("query_used") == "doorplate":
                from_doorplate += 1
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
        GEOCODE_FAILURES.parent.mkdir(parents=True, exist_ok=True)
        GEOCODE_FAILURES.write_text(
            json.dumps(failures, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(
            f"taipei fetch: {len(geocoded)} placed / {len(records)} stations "
            f"({len(failures)} unplaced, limit {MAX_UNPLACED_STATIONS})"
        )
        if len(failures) > MAX_UNPLACED_STATIONS:
            raise RuntimeError(
                f"{len(failures)} unplaced Taipei stations exceeds the limit of {MAX_UNPLACED_STATIONS}"
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
