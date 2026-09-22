#!/usr/bin/env python3
"""Ingest region station listings into the standard schema via the region adapter."""

from __future__ import annotations

import sys
from collections import Counter

from common import STATION_TYPE_VALUES, STATIONS_STANDARD, current_region, data_dir, write_json

PROCESSED = data_dir()


def warn_unknown_types(records: list[dict]) -> None:
    unknown = sorted({row["station_type"] for row in records} - STATION_TYPE_VALUES)
    if unknown:
        print(f"WARNING: unknown station_type values: {unknown}")


def print_summary(records: list[dict]) -> None:
    type_counts = Counter(row["station_type"] for row in records)
    landmark_count = sum(1 for row in records if row.get("landmark"))
    print(f"total: {len(records)}")
    print(f"by station_type: {dict(type_counts)}")
    print(f"landmark not null: {landmark_count}")


def main() -> int:
    cfg = current_region()
    records = cfg.station_source.fetch()
    print_summary(records)
    warn_unknown_types(records)
    write_json(PROCESSED / STATIONS_STANDARD, records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
