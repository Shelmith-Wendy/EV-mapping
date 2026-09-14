#!/usr/bin/env python3
"""Decode station coordinates from Plus Codes and verify against Maps URLs."""

from __future__ import annotations

import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from urllib.parse import unquote

import requests
from openlocationcode import openlocationcode as olc

from common import (
    LOCALITIES_GEOCODED,
    STATIONS_GEOCODED,
    STATIONS_STANDARD,
    data_dir,
    nominatim_geocode,
    read_json,
    write_json,
)

PROCESSED = data_dir()
STATIONS_PATH = PROCESSED / STATIONS_STANDARD
LOCALITIES_PATH = PROCESSED / LOCALITIES_GEOCODED

MISMATCH_THRESHOLD_KM = 5.0
SECONDARY_PLUS_AGREE_KM = 1.0
TERTIARY_AGREE_KM = 3.0
REQUEST_GAP_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 15
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

PLUS_CODE_RE = re.compile(r"[A-Za-z0-9]{4,8}\+[A-Za-z0-9]{2,3}")
PIN_RE = re.compile(r"!3d(-?\d+\.?\d*)!4d(-?\d+\.?\d*)")
VIEWPORT_RE = re.compile(r"@(-?\d+\.?\d*),(-?\d+\.?\d*),")
Q_LATLNG_RE = re.compile(r"[?&]q=(-?\d+\.?\d*)[,%2C\+]+(-?\d+\.?\d*)")
SEARCH_PATH_RE = re.compile(r"/maps/search/(-?\d+\.?\d*)[,%2C\+]+(-?\d+\.?\d*)")
DIR_PATH_RE = re.compile(r"/maps/dir/(.+)")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


def load_previous_resolved() -> dict[tuple[str, str], tuple[float | None, float | None]]:
    path = PROCESSED / STATIONS_GEOCODED
    if not path.exists():
        return {}
    previous = {}
    for row in read_json(path):
        resolved = (row.get("resolved_latitude"), row.get("resolved_longitude"))
        if resolved[0] is not None and resolved[1] is not None:
            previous[(row["station_id"], row["source_maps_url"])] = resolved
    return previous


def load_nominatim_anchors() -> dict[str, tuple[float, float]]:
    if not LOCALITIES_PATH.exists():
        return {}
    anchors = {}
    for row in read_json(LOCALITIES_PATH):
        if row.get("latitude") is not None and row.get("longitude") is not None:
            anchors[row["locality"]] = (float(row["latitude"]), float(row["longitude"]))
    return anchors


def bootstrap_anchors(
    stations: list[dict],
    nominatim_anchors: dict[str, tuple[float, float]],
    maps_by_station: dict[str, tuple[float | None, float | None, str | None]],
) -> tuple[dict[str, tuple[float, float]], dict[str, str]]:
    anchors = dict(nominatim_anchors)
    sources = {locality: "geocoded" for locality in nominatim_anchors}
    maps_by_locality: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for station in stations:
        resolved_lat, resolved_lng, _ = maps_by_station[station["station_id"]]
        if resolved_lat is not None and resolved_lng is not None:
            maps_by_locality[station["locality"]].append((resolved_lat, resolved_lng))

    for locality in sorted({station["locality"] for station in stations}):
        if locality in anchors:
            continue
        coords = maps_by_locality.get(locality)
        if not coords:
            continue
        lat, lng = coords[0]
        anchors[locality] = (lat, lng)
        sources[locality] = "maps_url"

    return anchors, sources


def update_locality_anchors(
    anchors: dict[str, tuple[float, float]],
    anchor_sources: dict[str, str],
    stations: list[dict],
) -> None:
    if not LOCALITIES_PATH.exists():
        return

    landmarks_by_locality: dict[str, str | None] = {}
    for station in stations:
        locality = station["locality"]
        if locality not in landmarks_by_locality and station.get("landmark"):
            landmarks_by_locality[locality] = station["landmark"]

    rows = read_json(LOCALITIES_PATH)
    by_locality = {row["locality"]: row for row in rows}
    for locality, (lat, lng) in sorted(anchors.items()):
        source = anchor_sources[locality]
        landmark = landmarks_by_locality.get(locality)
        if locality in by_locality:
            row = by_locality[locality]
            if row.get("latitude") is not None and row.get("source") == "geocoded":
                continue
            row["latitude"] = lat
            row["longitude"] = lng
            row["source"] = source
            if source == "maps_url":
                row["display_name"] = None
            continue
        query = f"{landmark}, Kenya" if landmark else f"{locality}, Kenya"
        rows.append({
            "locality": locality,
            "landmark": landmark,
            "query_used": query,
            "latitude": lat,
            "longitude": lng,
            "display_name": None,
            "source": source,
        })

    rows.sort(key=lambda row: row["locality"])
    write_json(LOCALITIES_PATH, rows)


def plus_code_in_text(text: str) -> str | None:
    for match in PLUS_CODE_RE.finditer(text or ""):
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
    source_url = station["source_maps_url"]
    cache_key = (station["station_id"], source_url)
    if cache_key in previous_resolved:
        lat, lng = previous_resolved[cache_key]
        return lat, lng, None
    if not fetch_live:
        return None, None, None
    resolved_lat, resolved_lng, final_url = resolve_maps_url(session, source_url)
    time.sleep(REQUEST_GAP_SECONDS)
    return resolved_lat, resolved_lng, final_url


def zeno_link_trusted(review_reason: str | None) -> bool:
    if review_reason is None:
        return True
    if review_reason == "no_plus_code":
        return True
    if review_reason.startswith("methods_disagree_"):
        return True
    return False


def maps_navigation_url(
    source_maps_url: str | None,
    final_lat: float | None,
    final_lng: float | None,
    review_reason: str | None,
) -> str | None:
    if final_lat is None or final_lng is None:
        return None
    if source_maps_url and zeno_link_trusted(review_reason):
        return source_maps_url
    return f"https://www.google.com/maps/search/?api=1&query={final_lat},{final_lng}"


def review_reason(
    coordinate_source: str | None,
    decoded_ok: bool,
    resolved_ok: bool,
    distance_km: float | None,
    dual_plus_reason: str | None,
) -> str | None:
    if dual_plus_reason and (
        dual_plus_reason.startswith("dual_plus_code_disagree_")
        or dual_plus_reason == "dual_plus_code_decode_failed"
    ):
        return dual_plus_reason
    if resolved_ok:
        if not decoded_ok:
            return "no_plus_code"
        if distance_km is not None and distance_km > MISMATCH_THRESHOLD_KM:
            return f"methods_disagree_{distance_km:.1f}km"
        return None
    if coordinate_source == "plus_code_decode_confirmed":
        return None
    if decoded_ok:
        return "no_maps_match"
    return "no_coordinates"


def write_outputs(records: list[dict]) -> None:
    write_json(PROCESSED / STATIONS_GEOCODED, records)


def print_summary(
    records: list[dict],
    anchors: dict[str, tuple[float, float]],
    anchor_sources: dict[str, str],
    stations: list[dict],
) -> None:
    sources = Counter(record["coordinate_source"] for record in records if record["coordinate_source"])
    nav_sources = Counter(
        "zeno" if record.get("maps_navigation_url") == record.get("source_maps_url") else "coordinates"
        for record in records
        if record.get("maps_navigation_url")
    )
    reasons = Counter(record["review_reason"] for record in records if record.get("review_reason"))
    review_rows = [record for record in records if record.get("needs_review")]
    distances = [record["distance_km"] for record in records if record["distance_km"] is not None]
    tertiary = [record for record in records if record.get("tertiary_check_latitude") is not None]
    tertiary_agree = sum(1 for record in tertiary if record.get("tertiary_check_agrees"))
    anchor_source_counts = Counter(anchor_sources.values())
    missing_anchors = sorted(
        {station["locality"] for station in stations} - set(anchors)
    )

    print(f"anchors: {len(anchors)} ({dict(anchor_source_counts)})")
    if missing_anchors:
        print(f"localities without anchor ({len(missing_anchors)}): {missing_anchors}")
    print(f"coordinate_source maps_url: {sources.get('maps_url', 0)}")
    print(f"coordinate_source plus_code_decode: {sources.get('plus_code_decode', 0)}")
    print(f"coordinate_source plus_code_decode_confirmed: {sources.get('plus_code_decode_confirmed', 0)}")
    print(f"maps_navigation_url: {dict(nav_sources)}")
    print(f"tertiary_check: {len(tertiary)} run, {tertiary_agree} agree, {len(tertiary) - tertiary_agree} disagree")
    print(f"needs_review: {len(review_rows)} {dict(reasons)}")
    if distances:
        print(f"distance_km max: {max(distances):.3f}")
        print(f"distance_km median: {statistics.median(distances):.3f}")
    else:
        print("distance_km max/median: n/a (no stations with both methods)")


def main() -> int:
    stations = read_json(STATIONS_PATH)
    nominatim_anchors = load_nominatim_anchors()
    previous_resolved = load_previous_resolved()
    session = requests.Session()

    maps_by_station: dict[str, tuple[float | None, float | None, str | None]] = {}
    for station in stations:
        resolved_lat, resolved_lng, final_url = resolve_maps_for_station(
            session, station, previous_resolved, fetch_live=True
        )
        maps_by_station[station["station_id"]] = (resolved_lat, resolved_lng, final_url)

    anchors, anchor_sources = bootstrap_anchors(stations, nominatim_anchors, maps_by_station)
    update_locality_anchors(anchors, anchor_sources, stations)

    geocode = nominatim_geocode()
    tertiary_cache: dict[str, tuple[float | None, float | None]] = {}
    records = []

    for station in stations:
        locality = station["locality"]
        anchor = anchors.get(locality)
        decoded_lat, decoded_lng = (None, None)
        if anchor is not None:
            decoded_lat, decoded_lng = decode_plus_code(station["address_raw"], anchor[0], anchor[1])

        resolved_lat, resolved_lng, final_url = maps_by_station[station["station_id"]]
        secondary_lat = None
        secondary_lng = None
        dual_plus_reason = None
        tertiary_lat = None
        tertiary_lng = None
        tertiary_agrees = None

        if resolved_lat is None and anchor is not None and "share.google" in station["source_maps_url"] and final_url:
            (
                share_lat,
                share_lng,
                secondary_lat,
                secondary_lng,
                dual_plus_reason,
            ) = resolve_share_google_q(
                final_url,
                station["address_raw"],
                decoded_lat,
                decoded_lng,
                anchor[0],
                anchor[1],
            )
            if share_lat is not None:
                resolved_lat, resolved_lng = share_lat, share_lng

        distance_km = None
        if decoded_lat is not None and resolved_lat is not None:
            distance_km = haversine_km(decoded_lat, decoded_lng, resolved_lat, resolved_lng)

        if resolved_lat is not None:
            final_lat, final_lng, source = resolved_lat, resolved_lng, "maps_url"
        elif dual_plus_reason == "plus_code_decode_confirmed" and decoded_lat is not None:
            final_lat, final_lng, source = decoded_lat, decoded_lng, "plus_code_decode_confirmed"
        elif decoded_lat is not None:
            final_lat, final_lng, source = decoded_lat, decoded_lng, "plus_code_decode"
        else:
            final_lat, final_lng, source = None, None, None

        if resolved_lat is None and decoded_lat is not None:
            lookup_url = final_url or station["source_maps_url"]
            business_name = business_name_from_url(lookup_url)
            if business_name:
                query = f"{business_name}, {station['locality']}, Kenya"
                if query not in tertiary_cache:
                    location = geocode(query)
                    tertiary_cache[query] = (
                        float(location.latitude) if location else None,
                        float(location.longitude) if location else None,
                    )
                tertiary_lat, tertiary_lng = tertiary_cache[query]
                if tertiary_lat is not None and tertiary_lng is not None:
                    tertiary_agrees = (
                        haversine_km(decoded_lat, decoded_lng, tertiary_lat, tertiary_lng) <= TERTIARY_AGREE_KM
                    )

        reason = review_reason(source, decoded_lat is not None, resolved_lat is not None, distance_km, dual_plus_reason)
        records.append({
            **station,
            "decoded_latitude": decoded_lat,
            "decoded_longitude": decoded_lng,
            "resolved_latitude": resolved_lat,
            "resolved_longitude": resolved_lng,
            "secondary_decode_latitude": secondary_lat,
            "secondary_decode_longitude": secondary_lng,
            "distance_km": None if distance_km is None else round(distance_km, 3),
            "final_latitude": final_lat,
            "final_longitude": final_lng,
            "coordinate_source": source,
            "maps_navigation_url": maps_navigation_url(
                station.get("source_maps_url"), final_lat, final_lng, reason
            ),
            "tertiary_check_latitude": tertiary_lat,
            "tertiary_check_longitude": tertiary_lng,
            "tertiary_check_agrees": tertiary_agrees,
            "needs_review": reason is not None,
            "review_reason": reason,
        })

    write_outputs(records)
    print_summary(records, anchors, anchor_sources, stations)
    return 0


if __name__ == "__main__":
    sys.exit(main())
