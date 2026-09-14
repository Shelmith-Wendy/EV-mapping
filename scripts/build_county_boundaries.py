#!/usr/bin/env python3
"""Build Kenya county GeoJSON (full join file + simplified dashboard copy)."""

from __future__ import annotations

import subprocess
import sys

from shapely.geometry import mapping, shape

from common import (
    COUNTIES_DISPLAY_GEOJSON,
    COUNTIES_GEOJSON,
    ROOT,
    data_dir,
    read_json,
    write_json,
)

PROCESSED = data_dir()
REPO_DIR = ROOT / "data" / "raw" / "kenya-counties-subcounties"
GEOJSON_DIR = REPO_DIR / "geojson"
OUTPUT_PATH = PROCESSED / COUNTIES_GEOJSON
DISPLAY_PATH = PROCESSED / COUNTIES_DISPLAY_GEOJSON
REPO_URL = "https://github.com/Mondieki/kenya-counties-subcounties"
SIMPLIFY_TOLERANCE_DEG = 0.005

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


def write_display_copy() -> None:
    """Simplify geometries for the dashboard; does not modify the join file on disk."""
    collection = read_json(OUTPUT_PATH)
    for feature in collection["features"]:
        geom = shape(feature["geometry"])
        if geom.geom_type == "GeometryCollection":
            geom = geom.geoms[0]
        feature["geometry"] = mapping(geom.simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True))

    write_json(DISPLAY_PATH, collection, indent=None)
    size = DISPLAY_PATH.stat().st_size
    print(f"{COUNTIES_DISPLAY_GEOJSON}: {size / 1024:.0f} KB ({size} bytes)")


def main() -> int:
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
        source = read_json(path)
        features.append({
            "type": "Feature",
            "properties": {"county": county},
            "geometry": source,
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

    fixed = []
    for feature in features:
        geom = shape(feature["geometry"])
        if geom.is_valid:
            continue
        feature["geometry"] = mapping(geom.buffer(0))
        fixed.append(feature["properties"]["county"])
    if fixed:
        print(f"invalid geometries fixed with buffer(0): {fixed}")
    else:
        print("all geometries valid")

    collection = {"type": "FeatureCollection", "features": features}
    write_json(OUTPUT_PATH, collection, indent=None)
    size_bytes = OUTPUT_PATH.stat().st_size
    print(f"{COUNTIES_GEOJSON}: {size_bytes / (1024 * 1024):.2f} MB ({size_bytes} bytes)")
    if size_bytes > 5 * 1024 * 1024:
        print("WARNING: file is over 5MB (ok for the spatial join; display copy is simplified next)")

    write_display_copy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
