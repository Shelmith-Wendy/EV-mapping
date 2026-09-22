"""Region adapter contract: station source, boundary source, and config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from shapely.geometry import mapping, shape


class StationSource(Protocol):
    def fetch(self) -> list[dict]:
        """Return STATION_FIELDS-shaped dicts.

        May include latitude/longitude and/or town when the source already
        provides them — downstream stages self-skip when those are set.
        """


class BoundarySource(Protocol):
    def load(self) -> dict:
        """Return a GeoJSON FeatureCollection.

        Every feature must have a properties key matching the region's first
        tier name (e.g. Kenya uses properties.county — not a renamed generic).
        """


def fix_invalid_geometries(features: list[dict], name_key: str) -> list[str]:
    """Repair invalid polygons in place with buffer(0); return fixed unit names."""
    fixed = []
    for feature in features:
        geom = shape(feature["geometry"])
        if geom.is_valid:
            continue
        feature["geometry"] = mapping(geom.buffer(0))
        fixed.append(feature["properties"][name_key])
    if fixed:
        print(f"invalid geometries fixed with buffer(0): {fixed}")
    else:
        print("all geometries valid")
    return fixed


@dataclass(frozen=True)
class RegionConfig:
    region_id: str
    display_name: str
    cluster: str
    bounds: tuple[tuple[float, float], tuple[float, float]]
    tiers: list[str]
    expected_unit_count: int
    station_type_map: dict[str, str]
    geocode_country_hint: str
    station_source: StationSource
    boundary_source: BoundarySource
