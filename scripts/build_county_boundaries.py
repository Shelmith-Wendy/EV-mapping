#!/usr/bin/env python3
"""Build admin-unit GeoJSON (full join file + simplified dashboard copy)."""

from __future__ import annotations

import sys

from shapely.geometry import mapping, shape

from common import (
    COUNTIES_DISPLAY_GEOJSON,
    COUNTIES_GEOJSON,
    current_region,
    data_dir,
    read_json,
    write_json,
)

PROCESSED = data_dir()
OUTPUT_PATH = PROCESSED / COUNTIES_GEOJSON
DISPLAY_PATH = PROCESSED / COUNTIES_DISPLAY_GEOJSON

# Degrees. Kenya counties are large and need aggressive simplify for the
# dashboard. Taipei districts are small (~191KB full); skip simplify so edge
# stations stay inside the drawn outline.
SIMPLIFY_TOLERANCE_BY_REGION = {
    "kenya": 0.005,
    "taipei": 0.0,
}
DEFAULT_SIMPLIFY_TOLERANCE_DEG = 0.005


def write_display_copy(tolerance_deg: float) -> None:
    """Write dashboard geometries; optionally simplify (tolerance 0 = copy as-is)."""
    collection = read_json(OUTPUT_PATH)
    for feature in collection["features"]:
        geom = shape(feature["geometry"])
        if geom.geom_type == "GeometryCollection":
            geom = geom.geoms[0]
        if tolerance_deg > 0:
            geom = geom.simplify(tolerance_deg, preserve_topology=True)
        feature["geometry"] = mapping(geom)

    write_json(DISPLAY_PATH, collection, indent=None)
    size = DISPLAY_PATH.stat().st_size
    print(
        f"{COUNTIES_DISPLAY_GEOJSON}: {size / 1024:.0f} KB ({size} bytes) "
        f"(simplify={tolerance_deg})"
    )


def main() -> int:
    cfg = current_region()
    collection = cfg.boundary_source.load()
    write_json(OUTPUT_PATH, collection, indent=None)
    size_bytes = OUTPUT_PATH.stat().st_size
    print(f"{COUNTIES_GEOJSON}: {size_bytes / (1024 * 1024):.2f} MB ({size_bytes} bytes)")
    if size_bytes > 5 * 1024 * 1024:
        print("WARNING: file is over 5MB (ok for the spatial join; display copy is simplified next)")

    tolerance = SIMPLIFY_TOLERANCE_BY_REGION.get(cfg.region_id, DEFAULT_SIMPLIFY_TOLERANCE_DEG)
    write_display_copy(tolerance)
    return 0


if __name__ == "__main__":
    sys.exit(main())
