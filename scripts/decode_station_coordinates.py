#!/usr/bin/env python3
"""Decode station coordinates from Plus Codes / Maps URLs; skip if already set."""

from __future__ import annotations

import statistics
import sys
from collections import Counter, defaultdict

import requests

from common import (
    LOCALITIES_GEOCODED,
    STATIONS_GEOCODED,
    STATIONS_STANDARD,
    current_region,
    data_dir,
    nominatim_geocode,
    read_json,
    write_json,
)
from regions.kenya import (
    business_name_from_url,
    decode_plus_code,
    haversine_km,
    resolve_maps_for_station,
    resolve_share_google_q,
)

PROCESSED = data_dir()
STATIONS_PATH = PROCESSED / STATIONS_STANDARD
LOCALITIES_PATH = PROCESSED / LOCALITIES_GEOCODED

MISMATCH_THRESHOLD_KM = 5.0
TERTIARY_AGREE_KM = 3.0


GEOCODED_ENRICHMENT_KEYS = (
    "decoded_latitude",
    "decoded_longitude",
    "resolved_latitude",
    "resolved_longitude",
    "secondary_decode_latitude",
    "secondary_decode_longitude",
    "distance_km",
    "final_latitude",
    "final_longitude",
    "coordinate_source",
    "maps_navigation_url",
    "tertiary_check_latitude",
    "tertiary_check_longitude",
    "tertiary_check_agrees",
    "needs_review",
    "review_reason",
)


def load_previous_geocoded() -> dict[str, dict]:
    path = PROCESSED / STATIONS_GEOCODED
    if not path.exists():
        return {}
    return {row["station_id"]: row for row in read_json(path)}


def load_previous_resolved(previous_geocoded: dict[str, dict]) -> dict[tuple[str, str], tuple[float | None, float | None]]:
    previous = {}
    for row in previous_geocoded.values():
        resolved = (row.get("resolved_latitude"), row.get("resolved_longitude"))
        if resolved[0] is not None and resolved[1] is not None:
            # Taipei (and similar) omit source_maps_url; Kenya uses "" when absent.
            previous[(row["station_id"], row.get("source_maps_url") or "")] = resolved
    return previous


def reuse_previous_geocoded(station: dict, previous: dict | None) -> dict | None:
    """Pass through prior enrichment when the station's decode inputs are unchanged."""
    if not previous:
        return None
    if previous.get("address_raw") != station.get("address_raw"):
        return None
    if previous.get("source_maps_url") != station.get("source_maps_url"):
        return None
    if previous.get("locality") != station.get("locality"):
        return None
    if previous.get("final_latitude") is None or previous.get("final_longitude") is None:
        return None
    return {
        **station,
        **{key: previous.get(key) for key in GEOCODED_ENRICHMENT_KEYS},
    }


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
    country_hint: str,
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
        query = f"{landmark}, {country_hint}" if landmark else f"{locality}, {country_hint}"
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


def record_from_source_coords(station: dict) -> dict:
    lat = float(station["latitude"])
    lng = float(station["longitude"])
    return {
        **station,
        "decoded_latitude": None,
        "decoded_longitude": None,
        "resolved_latitude": lat,
        "resolved_longitude": lng,
        "secondary_decode_latitude": None,
        "secondary_decode_longitude": None,
        "distance_km": None,
        "final_latitude": lat,
        "final_longitude": lng,
        "coordinate_source": "source",
        "maps_navigation_url": maps_navigation_url(
            station.get("source_maps_url"), lat, lng, None
        ),
        "tertiary_check_latitude": None,
        "tertiary_check_longitude": None,
        "tertiary_check_agrees": None,
        "needs_review": False,
        "review_reason": None,
    }


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
    skipped = sum(1 for record in records if record.get("coordinate_source") == "source")

    print(f"anchors: {len(anchors)} ({dict(anchor_source_counts)})")
    if missing_anchors:
        print(f"localities without anchor ({len(missing_anchors)}): {missing_anchors}")
    print(f"skipped (source lat/lon): {skipped}")
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
    cfg = current_region()
    country_hint = cfg.geocode_country_hint
    stations = read_json(STATIONS_PATH)
    nominatim_anchors = load_nominatim_anchors()
    previous_geocoded = load_previous_geocoded()
    previous_resolved = load_previous_resolved(previous_geocoded)
    session = requests.Session()

    reused: dict[str, dict] = {}
    for station in stations:
        if station.get("latitude") is not None and station.get("longitude") is not None:
            continue
        prior = reuse_previous_geocoded(station, previous_geocoded.get(station["station_id"]))
        if prior is not None:
            reused[station["station_id"]] = prior

    maps_by_station: dict[str, tuple[float | None, float | None, str | None]] = {}
    for station in stations:
        if station["station_id"] in reused:
            maps_by_station[station["station_id"]] = (None, None, None)
            continue
        if station.get("latitude") is not None and station.get("longitude") is not None:
            maps_by_station[station["station_id"]] = (None, None, None)
            continue
        resolved_lat, resolved_lng, final_url = resolve_maps_for_station(
            session, station, previous_resolved, fetch_live=True
        )
        maps_by_station[station["station_id"]] = (resolved_lat, resolved_lng, final_url)

    need_decode = [
        station for station in stations
        if station["station_id"] not in reused
        and (station.get("latitude") is None or station.get("longitude") is None)
    ]
    anchors, anchor_sources = bootstrap_anchors(need_decode, nominatim_anchors, maps_by_station)
    update_locality_anchors(anchors, anchor_sources, need_decode, country_hint)

    geocode = nominatim_geocode()
    tertiary_cache: dict[str, tuple[float | None, float | None]] = {}
    records = []

    for station in stations:
        if station["station_id"] in reused:
            records.append(reused[station["station_id"]])
            continue
        if station.get("latitude") is not None and station.get("longitude") is not None:
            records.append(record_from_source_coords(station))
            continue

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

        source_maps_url = station.get("source_maps_url") or ""
        if resolved_lat is None and anchor is not None and "share.google" in source_maps_url and final_url:
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
            lookup_url = final_url or source_maps_url
            business_name = business_name_from_url(lookup_url)
            if business_name:
                query = f"{business_name}, {station['locality']}, {country_hint}"
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

    write_json(PROCESSED / STATIONS_GEOCODED, records)
    print(f"reused previous geocode: {len(reused)}")
    print_summary(records, anchors, anchor_sources, stations)
    return 0


if __name__ == "__main__":
    sys.exit(main())
