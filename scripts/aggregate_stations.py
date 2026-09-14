#!/usr/bin/env python3
"""Aggregate stations into county and town summary tables."""

from __future__ import annotations

import sys

import pandas as pd

from common import (
    COUNT_COLS,
    COUNTIES_GEOJSON,
    COUNTY_SUMMARY,
    STATIONS_WITH_COUNTY,
    TOWN_SUMMARY,
    data_dir,
    read_json,
)

PROCESSED = data_dir()


def counts_by(stations: pd.DataFrame, group_cols: str | list[str]) -> pd.DataFrame:
    cols = [group_cols] if isinstance(group_cols, str) else list(group_cols)
    counts = (
        stations.groupby([*cols, "station_type"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["fast_charger", "swap_station"], fill_value=0)
        .rename(columns={"fast_charger": "fast_charger_count", "swap_station": "swap_station_count"})
    )
    counts["total_count"] = counts["fast_charger_count"] + counts["swap_station_count"]
    return counts


def main() -> int:
    stations = pd.read_json(PROCESSED / STATIONS_WITH_COUNTY)
    counties_geo = read_json(PROCESSED / COUNTIES_GEOJSON)

    type_counts = counts_by(stations, "county")
    all_counties = pd.DataFrame({"county": [f["properties"]["county"] for f in counties_geo["features"]]})
    county_summary = (
        all_counties.merge(type_counts, left_on="county", right_index=True, how="left")
        .fillna(0)
        .astype({col: int for col in COUNT_COLS})
        .sort_values("county")
        .reset_index(drop=True)
    )

    town_types = counts_by(stations, ["town", "county"])
    town_coords = stations.groupby(["town", "county"]).agg(
        latitude=("final_latitude", "mean"),
        longitude=("final_longitude", "mean"),
    )
    town_summary = (
        town_types.join(town_coords)
        .reset_index()
        [["town", "county", "fast_charger_count", "swap_station_count", "total_count", "latitude", "longitude"]]
        .sort_values(["town", "county"])
        .reset_index(drop=True)
    )

    station_total = len(stations)
    county_total = int(county_summary["total_count"].sum())
    town_total = int(town_summary["total_count"].sum())
    county_fast = int(county_summary["fast_charger_count"].sum())
    county_swap = int(county_summary["swap_station_count"].sum())
    checks = [
        ("county vs stations", county_total, station_total),
        ("town vs stations", town_total, station_total),
        ("county vs town totals", county_total, town_total),
        ("county fast vs swap split", county_fast + county_swap, station_total),
    ]
    failed = False
    for name, actual, expected in checks:
        if actual != expected:
            print(f"FAIL: {name} expected {expected}, got {actual}")
            failed = True
    if failed:
        return 1

    county_summary.to_json(PROCESSED / COUNTY_SUMMARY, orient="records", indent=2, force_ascii=False)
    town_summary.to_json(PROCESSED / TOWN_SUMMARY, orient="records", indent=2, force_ascii=False)

    print("top 5 counties:")
    for row in county_summary.nlargest(5, "total_count").itertuples(index=False):
        print(f"  {row.county}: {row.total_count}")
    print("top 5 towns:")
    for row in town_summary.nlargest(5, "total_count").itertuples(index=False):
        print(f"  {row.town}: {row.total_count}")
    n_counties = int((county_summary["total_count"] > 0).sum())
    n_towns = len(town_summary)
    print(
        f"{int(county_summary['total_count'].sum())} / "
        f"{int(county_summary['fast_charger_count'].sum())} fast / "
        f"{int(county_summary['swap_station_count'].sum())} swap "
        f"across {n_counties} counties and {n_towns} towns"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
