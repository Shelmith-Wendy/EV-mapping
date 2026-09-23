"""Kenya region adapter: Zeno stations + county boundaries + town-label helpers."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup
from openlocationcode import openlocationcode as olc

from regions.base import RegionConfig, fix_invalid_geometries

STATION_TYPE_MAP = {
    "fast_charger": "fast_charger",
    "swap_station": "swap_station",
}

ROOT = Path(__file__).resolve().parent.parent
RAW_HTML = ROOT / "data" / "raw" / "zeno_locations.html"
OVERRIDES_PATH = ROOT / "data" / "raw" / "town_overrides.json"
REPO_DIR = ROOT / "data" / "raw" / "kenya-counties-subcounties"
GEOJSON_DIR = REPO_DIR / "geojson"
REPO_URL = "https://github.com/Mondieki/kenya-counties-subcounties"
ZENO_URL = "https://zeno.earth/locations/locations.html"

ZENO_STATION_TYPES = {
    "Fast Charger": "fast_charger",
    "Swap Station": "swap_station",
}

PLUS_CODE_RE = re.compile(r"^[A-Z0-9]{4,8}\+[A-Z0-9]{2,3}", re.I)
PLUS_CODE_ANY_RE = re.compile(r"[A-Za-z0-9]{4,8}\+[A-Za-z0-9]{2,3}")
POI_SUFFIXES = {"church", "hub", "hq", "roundabout", "rd", "road"}
PLACE_HINTS = {"road", "rd", "bypass", "way", "crescent", "cres", "square", "roundabout"}
GENERIC_LAST = {"town", "township"}
BUSINESS_LAST = {
    "autospares", "pharmacy", "shop", "services", "technologies",
    "motorcycles", "motorcycle", "hardware", "salon", "studio",
    "building", "house", "spares", "parts", "enterprise", "missions",
    "wholesales", "bookshop", "cyber", "laundry", "charger", "station",
    "solution", "solutions", "master", "safari", "automart", "hotel",
    "centre", "center", "mall", "plaza", "motors",
}

PIN_RE = re.compile(r"!3d(-?\d+\.?\d*)!4d(-?\d+\.?\d*)")
VIEWPORT_RE = re.compile(r"@(-?\d+\.?\d*),(-?\d+\.?\d*),")
Q_LATLNG_RE = re.compile(r"[?&]q=(-?\d+\.?\d*)[,%2C\+]+(-?\d+\.?\d*)")
SEARCH_PATH_RE = re.compile(r"/maps/search/(-?\d+\.?\d*)[,%2C\+]+(-?\d+\.?\d*)")
DIR_PATH_RE = re.compile(r"/maps/dir/(.+)")

REQUEST_GAP_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 15
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
SECONDARY_PLUS_AGREE_KM = 1.0

NAIROBI_COUNTY = "Nairobi"
ADMIN_MARKERS = ("ward", "location", "division", "sublocation", "township")
ADMIN_SUFFIXES = (" sublocation", " location", " division", " ward", " township", " town")
KEEP_UPPER = {"CBD"}
NEW_PREFIX = re.compile(r"^new\s+", re.I)

KENYA_COUNTIES = [
    "Mombasa", "Kwale", "Kilifi", "Tana River", "Lamu", "Taita-Taveta",
    "Garissa", "Wajir", "Mandera", "Marsabit", "Isiolo", "Meru",
    "Tharaka-Nithi", "Embu", "Kitui", "Machakos", "Makueni", "Nyandarua",
    "Nyeri", "Kirinyaga", "Murang'a", "Kiambu", "Turkana", "West Pokot",
    "Samburu", "Trans Nzoia", "Uasin Gishu", "Elgeyo-Marakwet", "Nandi",
    "Baringo", "Laikipia", "Nakuru", "Narok", "Kajiado", "Kericho", "Bomet",
    "Kakamega", "Vihiga", "Bungoma", "Busia", "Siaya", "Kisumu", "Homa Bay",
    "Migori", "Kisii", "Nyamira", "Nairobi",
]


def county_key(text: str) -> str:
    return text.replace("-", "").replace(" ", "").replace("'", "").casefold()


COUNTY_BY_KEY = {county_key(name): name for name in KENYA_COUNTIES}


# --- Station source (Zeno HTML) ---

def load_zeno_html() -> str:
    force = os.environ.get("KENYA_EV_FORCE_FETCH", "").strip().lower() in {"1", "true", "yes"}
    if force or not RAW_HTML.exists() or RAW_HTML.stat().st_size == 0:
        response = requests.get(ZENO_URL, timeout=30)
        response.raise_for_status()
        RAW_HTML.parent.mkdir(parents=True, exist_ok=True)
        RAW_HTML.write_bytes(response.content)
    return RAW_HTML.read_text(encoding="utf-8")


def cell_text(row, class_name: str) -> str:
    cell = row.find("td", class_=class_name)
    return cell.get_text(strip=True) if cell else ""


def host_name(full_name: str) -> str:
    if "—" in full_name:
        return full_name.split("—", 1)[1].strip()
    return full_name


def place_key(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip().rstrip(".,").strip().casefold()
    for suffix in (" town", " township"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def is_locality(text: str, locality: str) -> bool:
    return bool(text) and place_key(text) == place_key(locality)


def extract_landmark(name: str, address: str, locality: str) -> str | None:
    host = re.sub(r"\s+", " ", host_name(name)).strip()

    if "," in host:
        suffix = host.rsplit(",", 1)[1].strip()
        if suffix and not is_locality(suffix, locality):
            cleaned = re.sub(r"^(opposite|opp\.?)\s+", "", suffix, flags=re.I).strip()
            return cleaned or suffix
        prefix = host.rsplit(",", 1)[0].strip()
        if prefix and not is_locality(prefix, locality):
            prefix_tokens = set(place_key(prefix).split())
            if prefix_tokens & PLACE_HINTS:
                return prefix
    else:
        words = host.split()
        last = words[-1].casefold() if words else ""
        if len(words) >= 2 and last in POI_SUFFIXES:
            if last in {"rd", "road"}:
                return host
            remainder = re.sub(r"\bZeno\b", "", " ".join(words[:-1])).strip()
            if remainder and not is_locality(remainder, locality):
                tokens = remainder.split()
                if last == "church" and len(tokens) == 2:
                    return tokens[0]
                return remainder
        if len(words) == 1 and host and not is_locality(host, locality):
            return host
        if (
            len(words) >= 2
            and not is_locality(host, locality)
            and place_key(locality) not in place_key(host)
        ):
            last_word = words[-1]
            if last_word.casefold() not in BUSINESS_LAST | GENERIC_LAST and not is_locality(last_word, locality):
                return last_word

    addr_rest = PLUS_CODE_RE.sub("", address).strip(" ,")
    if addr_rest and not is_locality(addr_rest, locality):
        if place_key(locality) in place_key(host):
            return None
        return addr_rest

    return None


def parse_zeno_source(html: str, retrieved_at: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for row in soup.select("table tbody tr"):
        name = cell_text(row, "name")
        address = cell_text(row, "addr")
        locality = cell_text(row, "locality")
        raw_type = cell_text(row, "type-col")
        gmap = row.find("td", class_="gmap")
        maps_link = gmap.find("a") if gmap else None
        records.append({
            "station_id": cell_text(row, "code"),
            "name": name,
            "station_type": ZENO_STATION_TYPES.get(raw_type, raw_type),
            "locality": locality,
            "landmark": extract_landmark(name, address, locality),
            "address_raw": address,
            "phone": cell_text(row, "phone"),
            "hours": cell_text(row, "hours"),
            "source_maps_url": maps_link["href"] if maps_link and maps_link.has_attr("href") else "",
            "source_name": "zeno_earth",
            "retrieved_at": retrieved_at,
        })
    return records


class ZenoStationSource:
    def fetch(self) -> list[dict]:
        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        records = parse_zeno_source(load_zeno_html(), retrieved_at)
        for record in records:
            native = record["station_type"]
            record["station_type"] = STATION_TYPE_MAP.get(native, native)

        # Reuse prior retrieved_at when the HTML-derived record is unchanged so
        # re-runs of an unchanged source stay byte-stable for acceptance diffs.
        staging = Path(os.environ.get("KENYA_EV_DATA_DIR", str(ROOT / "data" / "staging" / "kenya")))
        previous_path = staging / "stations_standard.json"
        if previous_path.exists():
            previous_by_id = {
                row["station_id"]: row
                for row in json.loads(previous_path.read_text(encoding="utf-8"))
            }
            for record in records:
                old = previous_by_id.get(record["station_id"])
                if not old:
                    continue
                comparable = {key: value for key, value in record.items() if key != "retrieved_at"}
                old_comparable = {key: old.get(key) for key in comparable}
                if comparable == old_comparable:
                    record["retrieved_at"] = old["retrieved_at"]
        return records


# --- Boundary source ---

class KenyaBoundarySource:
    def load(self) -> dict:
        if not REPO_DIR.exists():
            subprocess.run(
                ["git", "clone", "--depth=1", REPO_URL, str(REPO_DIR)],
                check=True,
            )

        features = []
        unexpected_files = []
        for path in sorted(GEOJSON_DIR.glob("*.json")):
            county = COUNTY_BY_KEY.get(county_key(path.stem))
            if county is None:
                unexpected_files.append(path.name)
                continue
            geometry = json.loads(path.read_text(encoding="utf-8"))
            features.append({
                "type": "Feature",
                "properties": {"county": county},
                "geometry": geometry,
            })

        names = [feature["properties"]["county"] for feature in features]
        expected = set(KENYA_COUNTIES)
        got = set(names)
        print(f"features: {len(features)}")
        if unexpected_files:
            print(f"unexpected files (no mapping): {unexpected_files}")
        missing = sorted(expected - got)
        extra = sorted(got - expected)
        if missing:
            print(f"missing counties: {missing}")
        if extra:
            print(f"unexpected counties: {extra}")
        if len(features) == 47 and got == expected:
            print("county set matches all 47")

        fix_invalid_geometries(features, "county")
        return {"type": "FeatureCollection", "features": features}


# --- Plus Code + Google Maps share-link resolution ---

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


def plus_code_in_text(text: str) -> str | None:
    for match in PLUS_CODE_ANY_RE.finditer(text or ""):
        code = match.group().upper()
        try:
            if olc.isValid(code):
                return code
        except Exception:
            continue
    return None


def decode_plus_code(address_raw: str, anchor_lat: float, anchor_lng: float):
    code = plus_code_in_text(address_raw)
    if not code:
        return None, None
    try:
        full_code = olc.recoverNearest(code, anchor_lat, anchor_lng)
        area = olc.decode(full_code)
    except Exception:
        return None, None
    return area.latitudeCenter, area.longitudeCenter


def q_param_text(url: str) -> str | None:
    match = re.search(r"[?&]q=([^&]+)", url)
    return unquote(match.group(1)) if match else None


def extract_maps_coordinates(url: str):
    if not url or "consent.google.com" in url:
        return None, None
    pin = PIN_RE.findall(url)
    if pin:
        lat, lng = pin[-1]
        return float(lat), float(lng)
    viewport = VIEWPORT_RE.search(url)
    if viewport:
        return float(viewport.group(1)), float(viewport.group(2))
    q_match = Q_LATLNG_RE.search(url)
    if q_match:
        return float(q_match.group(1)), float(q_match.group(2))
    search_match = SEARCH_PATH_RE.search(url)
    if search_match:
        return float(search_match.group(1)), float(search_match.group(2))
    dir_match = DIR_PATH_RE.search(url)
    if dir_match:
        pairs = re.findall(r"(-?\d+\.?\d*),(-?\d+\.?\d*)", dir_match.group(1))
        if pairs:
            lat, lng = pairs[-1]
            return float(lat), float(lng)
    return None, None


def fetch_final_url(session: requests.Session, url: str) -> str | None:
    try:
        response = session.get(
            url,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return response.url
    except requests.RequestException:
        return None


def business_name_from_url(url: str) -> str | None:
    if not url:
        return None
    place_match = re.search(r"/maps/place/([^/?]+)", url)
    if place_match:
        name = unquote(place_match.group(1).replace("+", " ")).strip()
        return name or None
    q_text = q_param_text(url)
    if not q_text:
        return None
    if Q_LATLNG_RE.search(f"?q={q_text}") or plus_code_in_text(q_text):
        return None
    return q_text.replace("+", " ").strip() or None


def resolve_share_google_q(
    final_url: str,
    address_raw: str,
    decoded_lat: float | None,
    decoded_lng: float | None,
    anchor_lat: float,
    anchor_lng: float,
):
    q_latlng = Q_LATLNG_RE.search(final_url)
    if q_latlng:
        return float(q_latlng.group(1)), float(q_latlng.group(2)), None, None, None

    q_text = q_param_text(final_url)
    if not q_text:
        return None, None, None, None, None

    q_plus = plus_code_in_text(q_text)
    address_plus = plus_code_in_text(address_raw)
    if not q_plus:
        return None, None, None, None, None
    if q_plus == address_plus:
        return None, None, None, None, None

    secondary_lat, secondary_lng = decode_plus_code(q_plus, anchor_lat, anchor_lng)
    if decoded_lat is None or decoded_lng is None or secondary_lat is None or secondary_lng is None:
        return None, None, secondary_lat, secondary_lng, "dual_plus_code_decode_failed"

    distance_km = haversine_km(decoded_lat, decoded_lng, secondary_lat, secondary_lng)
    if distance_km <= SECONDARY_PLUS_AGREE_KM:
        return None, None, secondary_lat, secondary_lng, "plus_code_decode_confirmed"
    return None, None, secondary_lat, secondary_lng, f"dual_plus_code_disagree_{distance_km:.1f}km"


def resolve_maps_url(session: requests.Session, url: str):
    final_url = fetch_final_url(session, url)
    if not final_url:
        return None, None, final_url
    coords = extract_maps_coordinates(final_url)
    if coords[0] is not None:
        return coords[0], coords[1], final_url
    return None, None, final_url


def resolve_maps_for_station(
    session: requests.Session,
    station: dict,
    previous_resolved: dict[tuple[str, str], tuple[float | None, float | None]],
    fetch_live: bool,
) -> tuple[float | None, float | None, str | None]:
    source_url = station.get("source_maps_url") or ""
    cache_key = (station["station_id"], source_url)
    if cache_key in previous_resolved:
        lat, lng = previous_resolved[cache_key]
        return lat, lng, None
    if not fetch_live:
        return None, None, None
    resolved_lat, resolved_lng, final_url = resolve_maps_url(session, source_url)
    time.sleep(REQUEST_GAP_SECONDS)
    return resolved_lat, resolved_lng, final_url


# --- Town label normalization ---

@lru_cache(maxsize=1)
def load_overrides() -> tuple[dict[str, str], dict[str, str]]:
    if not OVERRIDES_PATH.exists():
        return {}, {}
    data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    aliases = {str(k): str(v) for k, v in data.get("aliases", {}).items()}
    stations = {str(k): str(v) for k, v in data.get("stations", {}).items()}
    return aliases, stations


def title_words(name: str) -> str:
    upper = name.upper()
    if upper in KEEP_UPPER:
        return upper
    return " ".join(word[:1].upper() + word[1:] for word in name.split())


def strip_admin_suffixes(name: str) -> str:
    cleaned = name.strip()
    changed = True
    while changed:
        changed = False
        for suffix in ADMIN_SUFFIXES:
            if cleaned.lower().endswith(suffix):
                cleaned = cleaned[: -len(suffix)].strip()
                changed = True
    return cleaned


def is_admin_label(name: str) -> bool:
    lower = name.lower()
    return any(marker in lower for marker in ADMIN_MARKERS)


def clean_town(name: str | None, county: str | None = None, station_id: str | None = None) -> str | None:
    if not name:
        return None

    aliases, station_overrides = load_overrides()
    if station_id and station_id in station_overrides:
        return station_overrides[station_id]

    cleaned = name.strip()
    if "/" in cleaned:
        cleaned = cleaned.split("/", 1)[0].strip()
    cleaned = NEW_PREFIX.sub("", cleaned)
    cleaned = strip_admin_suffixes(cleaned)
    cleaned = title_words(cleaned)
    if cleaned in aliases:
        cleaned = aliases[cleaned]
    return cleaned or name.strip()


def town_from_display(display_name: str | None, county: str) -> str | None:
    if not display_name:
        return None
    for part in display_name.split(","):
        candidate = clean_town(part.strip(), county)
        if not candidate or candidate == county:
            continue
        if candidate.endswith("County") or candidate == "Kenya":
            continue
        if not is_admin_label(candidate):
            return candidate
    return None


def pick_town(address: dict, county: str, display_name: str | None) -> str | None:
    if not address:
        return None
    if county == NAIROBI_COUNTY:
        keys = ("suburb", "neighbourhood", "quarter", "town", "city", "municipality")
    else:
        keys = ("town", "city", "municipality", "village", "suburb")
    for key in keys:
        raw = address.get(key)
        if not raw:
            continue
        candidate = clean_town(strip_admin_suffixes(raw), county)
        if candidate and not is_admin_label(candidate):
            return candidate
    return town_from_display(display_name, county)


CONFIG = RegionConfig(
    region_id="kenya",
    display_name="Kenya",
    cluster="east_africa",
    bounds=((-5.0, 33.5), (5.6, 42.2)),
    tiers=["county", "town"],
    expected_unit_count=47,
    station_type_map=STATION_TYPE_MAP,
    geocode_country_hint="Kenya",
    dataset_source="zeno_earth",
    dataset_source_label="Zeno Moto",
    station_source=ZenoStationSource(),
    boundary_source=KenyaBoundarySource(),
)
