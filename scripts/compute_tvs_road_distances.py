#!/usr/bin/env python3
"""Compute road distances and route geometries between TVS candidate sites.

Run manually, Taipei only, never called by the pipeline.

Usage:
    python scripts/compute_tvs_road_distances.py

Environment variables:
    OSRM_BASE_URL   OSRM server root (default https://router.project-osrm.org)

Output:
    data/raw/taipei/tvs_road_distances.json

The file is keyed by station_id and records the nearest other TVS site, the
road distance (route + both snap distances, in metres), the straight-line
distance, each snap distance, and the encoded route polyline (polyline6).

Routing: driving profile, OSRM.
  1. Try one /table request with annotations=distance for all sites to find the
     nearest neighbour per site. If this fails, fall back to /route for every pair.
  2. For each site's chosen nearest neighbour, fetch the full route:
     OSRM /route/v1/driving/... with overview=full&geometries=polyline6.
     Check that route distance + both snap distances matches the saved road_distance_m
     within 1 m, and report any mismatch. Save polyline6 geometry string.

Etiquette:
  - User-Agent identifies this project.
  - At most 1 request per second.
  - 2 retries on transient failure.
  - Stop immediately on HTTP 403 or 429.
  - No straight-line fallback; pairs without a valid route are skipped.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    raise SystemExit("requests is not installed; run: pip install requests")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PUBLIC = ROOT / "data" / "public" / "taipei" / "tvs_candidates.json"
OUTPUT_PATH = ROOT / "data" / "raw" / "taipei" / "tvs_road_distances.json"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
PROFILE = "driving"
USER_AGENT = "ev-mapping-taipei/1.0 (TVS candidate spacing analysis; github.com/tvs-ev-mapping)"
MIN_DELAY_SECONDS = 1.0
MAX_RETRIES = 2

# ---------------------------------------------------------------------------
# Haversine (metres)
# ---------------------------------------------------------------------------

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS-84 points, in metres."""
    to_rad = math.pi / 180.0
    d_lat = (lat2 - lat1) * to_rad
    d_lon = (lon2 - lon1) * to_rad
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1 * to_rad) * math.cos(lat2 * to_rad) * math.sin(d_lon / 2) ** 2
    )
    return EARTH_RADIUS_M * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# OSRM HTTP helpers
# ---------------------------------------------------------------------------

_session: requests.Session | None = None
_last_request_time: float = 0.0


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def _throttle() -> None:
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_DELAY_SECONDS:
        time.sleep(MIN_DELAY_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _get(url: str, params: dict) -> dict:
    """GET with throttling and limited retries. Raises SystemExit on 403/429."""
    session = _get_session()
    last_exc: Exception | None = None
    for attempt in range(1 + MAX_RETRIES):
        _throttle()
        try:
            response = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            last_exc = exc
            print(f"  request error (attempt {attempt + 1}): {exc}", file=sys.stderr)
            continue
        if response.status_code in (403, 429):
            raise SystemExit(
                f"HTTP {response.status_code} from OSRM — stopping immediately as instructed."
            )
        if not response.ok:
            last_exc = requests.HTTPError(
                f"HTTP {response.status_code} for {url}", response=response
            )
            print(
                f"  HTTP {response.status_code} (attempt {attempt + 1})", file=sys.stderr
            )
            continue
        return response.json()
    raise SystemExit(f"All {1 + MAX_RETRIES} attempts failed for {url}: {last_exc}")


# ---------------------------------------------------------------------------
# Snap distance helper
# ---------------------------------------------------------------------------

def _snap_distance_m(waypoint: dict, original_lat: float, original_lon: float) -> float:
    """Distance from the original coordinate to the OSRM snapped location."""
    snapped_lon, snapped_lat = waypoint["location"]  # OSRM: [lng, lat]
    return haversine_m(original_lat, original_lon, snapped_lat, snapped_lon)


# ---------------------------------------------------------------------------
# OSRM table request (all-pairs, one HTTP call)
# ---------------------------------------------------------------------------

def _try_table(sites: list[dict]) -> dict[str, dict] | None:
    coords = ";".join(f"{s['final_longitude']},{s['final_latitude']}" for s in sites)
    url = f"{OSRM_BASE_URL}/table/v1/{PROFILE}/{coords}"
    params = {"annotations": "distance"}

    print(f"  -> table request for {len(sites)} sites ...", flush=True)
    try:
        data = _get(url, params)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"  table request error: {exc}", file=sys.stderr)
        return None

    code = data.get("code", "")
    if code != "Ok":
        print(f"  table returned code={code!r}; falling back to route requests.", file=sys.stderr)
        return None

    distances = data.get("distances")
    if not distances:
        print("  table returned no distance matrix; falling back.", file=sys.stderr)
        return None

    waypoints = data.get("sources") or data.get("waypoints") or []
    if len(waypoints) != len(sites):
        print(
            f"  table waypoint count mismatch ({len(waypoints)} vs {len(sites)}); falling back.",
            file=sys.stderr,
        )
        return None

    n = len(sites)
    results: dict[str, dict] = {}
    for i, candidate in enumerate(sites):
        best_road_m: float = math.inf
        best_j: int = -1

        snap_from_m = _snap_distance_m(
            waypoints[i], candidate["final_latitude"], candidate["final_longitude"]
        )

        for j in range(n):
            if j == i:
                continue
            route_m = distances[i][j]
            if route_m is None:
                continue
            other = sites[j]
            snap_to_m = _snap_distance_m(
                waypoints[j], other["final_latitude"], other["final_longitude"]
            )
            total_road_m = snap_from_m + float(route_m) + snap_to_m
            if total_road_m < best_road_m:
                best_road_m = total_road_m
                best_j = j

        if best_j == -1:
            continue

        other = sites[best_j]
        snap_to_m = _snap_distance_m(
            waypoints[best_j], other["final_latitude"], other["final_longitude"]
        )
        straight_m = haversine_m(
            candidate["final_latitude"],
            candidate["final_longitude"],
            other["final_latitude"],
            other["final_longitude"],
        )
        assert best_road_m >= straight_m - 1e-6, (
            f"Road distance {best_road_m:.1f} m < straight-line {straight_m:.1f} m "
            f"for {candidate['station_id']} -> {other['station_id']}."
        )
        results[candidate["station_id"]] = {
            "nearest_id": other["station_id"],
            "nearest_name": other.get("locality") or other.get("name") or "TVS",
            "road_distance_m": round(best_road_m, 1),
            "straight_line_m": round(straight_m, 1),
            "snap_from_m": round(snap_from_m, 1),
            "snap_to_m": round(snap_to_m, 1),
        }

    return results


# ---------------------------------------------------------------------------
# Route geometry fetcher
# ---------------------------------------------------------------------------

def _fetch_route_geometry(
    candidate: dict, nearest: dict
) -> tuple[str | None, float | None, float | None, float | None]:
    """
    Fetch full route geometry (polyline6) from candidate -> nearest.
    Returns (polyline6_str, total_road_m, snap_from_m, snap_to_m) or (None, None, None, None).
    """
    coords = (
        f"{candidate['final_longitude']},{candidate['final_latitude']}"
        f";{nearest['final_longitude']},{nearest['final_latitude']}"
    )
    url = f"{OSRM_BASE_URL}/route/v1/{PROFILE}/{coords}"
    params = {"overview": "full", "geometries": "polyline6", "steps": "false"}

    data = _get(url, params)
    if data.get("code") != "Ok" or not data.get("routes"):
        return None, None, None, None

    route = data["routes"][0]
    route_dist_m = float(route["distance"])
    geometry = route.get("geometry")

    waypoints = data.get("waypoints", [])
    if len(waypoints) < 2:
        return geometry, None, None, None

    snap_from_m = _snap_distance_m(
        waypoints[0], candidate["final_latitude"], candidate["final_longitude"]
    )
    snap_to_m = _snap_distance_m(
        waypoints[1], nearest["final_latitude"], nearest["final_longitude"]
    )
    total_road_m = snap_from_m + route_dist_m + snap_to_m
    return geometry, total_road_m, snap_from_m, snap_to_m


# ---------------------------------------------------------------------------
# Input hash
# ---------------------------------------------------------------------------

def _candidate_hash(sites: list[dict]) -> str:
    """Stable SHA-256 of all candidate ids and coordinates."""
    payload = json.dumps(
        [
            {
                "station_id": s["station_id"],
                "final_latitude": s["final_latitude"],
                "final_longitude": s["final_longitude"],
            }
            for s in sorted(sites, key=lambda s: s["station_id"])
        ],
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not CANDIDATES_PUBLIC.is_file():
        print(f"missing {CANDIDATES_PUBLIC} — run load_tvs_candidates.py first", file=sys.stderr)
        return 1

    sites = json.loads(CANDIDATES_PUBLIC.read_text(encoding="utf-8"))
    if not sites:
        print("tvs_candidates.json is empty", file=sys.stderr)
        return 1
    print(f"Loaded {len(sites)} TVS candidate sites from {CANDIDATES_PUBLIC}")
    id_to_site = {s["station_id"]: s for s in sites}

    input_hash = _candidate_hash(sites)
    print(f"Input hash: {input_hash}")
    print(f"OSRM base URL: {OSRM_BASE_URL}")
    print()

    t0 = time.monotonic()
    total_requests = 0

    # Step 1: Table request for distance matrix
    print("Step 1: Attempting /table request ...")
    table_results = _try_table(sites)
    if table_results is not None:
        total_requests += 1
        results = table_results
        print(f"  table succeeded: {len(results)} sites.")
    else:
        print("Falling back to per-pair /route requests (not implemented in this run).", file=sys.stderr)
        return 1

    # Step 2: Route geometry requests for each site's chosen nearest
    print()
    print("Step 2: Fetching route polyline6 for each nearest pair ...")
    mismatches = []
    for sid, r in sorted(results.items()):
        candidate = id_to_site[sid]
        nearest = id_to_site[r["nearest_id"]]
        print(f"  -> route {sid} -> {r['nearest_id']} ...", flush=True)

        geom, route_road_m, snap_f, snap_t = _fetch_route_geometry(candidate, nearest)
        total_requests += 1

        if geom is None:
            print(f"    WARNING: No route found for {sid} -> {r['nearest_id']}", file=sys.stderr)
            continue

        r["polyline6"] = geom

        if route_road_m is not None:
            diff = abs(route_road_m - r["road_distance_m"])
            if diff > 1.0:
                mismatch_info = (
                    f"{sid} -> {r['nearest_id']}: table={r['road_distance_m']:.1f} m, "
                    f"route+snaps={route_road_m:.1f} m, diff={diff:.1f} m"
                )
                mismatches.append(mismatch_info)
                print(f"    MISMATCH: {mismatch_info}")
            else:
                print(f"    check OK: diff={diff:.2f} m (<= 1.0 m)")

    elapsed = time.monotonic() - t0

    # Summary
    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"Total requests: {total_requests} (1 table + {len(results)} route)")
    print(f"Elapsed: {elapsed:.1f} s")
    print(f"Mismatches (> 1 m): {len(mismatches)}")
    for m in mismatches:
        print(f"  - {m}")
    if not mismatches:
        print("  All route distances match table road_distance_m within 1 m.")

    # Build output
    output = {
        "_meta": {
            "input_hash": input_hash,
            "osrm_base_url": OSRM_BASE_URL,
            "profile": PROFILE,
            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "request_count": total_requests,
            "elapsed_seconds": round(elapsed, 1),
            "note": (
                "road_distance_m = route_distance + snap_from_m + snap_to_m. "
                "polyline6 encodes the route coordinates."
            ),
        },
        "sites": results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Saved {len(results)} entries to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
