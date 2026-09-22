"""Region registry. Shared pipeline code looks up adapters here — never by name."""

from __future__ import annotations

from regions.base import RegionConfig
from regions import kenya, taipei

REGIONS: dict[str, RegionConfig] = {
    "kenya": kenya.CONFIG,
    "taipei": taipei.CONFIG,
}
