#!/usr/bin/env python3
"""Aggregate stations into one summary table per configured admin tier."""

from __future__ import annotations

import sys

import pandas as pd

from common import (
    COUNTIES_GEOJSON,
    STATIONS_WITH_COUNTY,
    current_region,
    data_dir,
    read_json,
    type_count_cols,
)

PROCESSED = data_dir()


def counts_by(
    stations: pd.DataFrame,
    group_cols: str | list[str],
    station_types: list[str],
) -> pd.DataFrame:
    cols = [group_cols] if isinstance(group_cols, str) else list(group_cols)
    counts = (
        stations.groupby([*cols, "station_type"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=station_types, fill_value=0)
        .rename(columns={name: f"{name}_count" for name in station_types})
    )
    count_cols = type_count_cols(station_types)
    counts["total_count"] = sum(counts[col] for col in count_cols)
    return counts


def main() -> int:
    cfg = current_region()
    tiers = cfg.tiers
    if not tiers:
        print("FAIL: region config has empty tiers")
        return 1

    stations = pd.read_json(PROCESSED / STATIONS_WITH_COUNTY)
    boundaries_geo = read_json(PROCESSED / COUNTIES_GEOJSON)
    primary_tier = tiers[0]
    station_types = sorted(set(cfg.station_type_map.values()))
    count_cols = type_count_cols(station_types)

    summaries: dict[str, pd.DataFrame] = {}

    primary_counts = counts_by(stations, primary_tier, station_types)
    en_key = f"{primary_tier}_en"
    unit_rows = []
    for feature in boundaries_geo["features"]:
        props = feature["properties"]
        row = {primary_tier: props[primary_tier]}
        if props.get(en_key):
            row[en_key] = props[en_key]
        unit_rows.append(row)
    all_units = pd.DataFrame(unit_rows)
    primary_summary = (
        all_units.merge(primary_counts, left_on=primary_tier, right_index=True, how="left")
        .fillna({col: 0 for col in [*count_cols, "total_count"]})
        .astype({col: int for col in [*count_cols, "total_count"]})
        .sort_values(primary_tier)
        .reset_index(drop=True)
    )
    summaries[primary_tier] = primary_summary

    for depth in range(1, len(tiers)):
        tier = tiers[depth]
        group_cols = list(reversed(tiers[: depth + 1]))
        type_counts = counts_by(stations, group_cols, station_types)
        coords = stations.groupby(group_cols).agg(
            latitude=("final_latitude", "mean"),
            longitude=("final_longitude", "mean"),
        )
        summary = (
            type_counts.join(coords)
            .reset_index()
            [[*group_cols, *count_cols, "total_count", "latitude", "longitude"]]
            .sort_values(group_cols)
            .reset_index(drop=True)
        )
        summaries[tier] = summary

    station_total = len(stations)
    failed = False
    for tier, summary in summaries.items():
        total = int(summary["total_count"].sum())
        if total != station_total:
            print(f"FAIL: {tier} vs stations expected {station_total}, got {total}")
            failed = True

    type_sum = sum(int(primary_summary[col].sum()) for col in count_cols)
    if type_sum != station_total:
        print(f"FAIL: type counts {type_sum} != stations {station_total}")
        failed = True

    if len(tiers) >= 2:
        primary_total = int(primary_summary["total_count"].sum())
        secondary_total = int(summaries[tiers[1]]["total_count"].sum())
        if primary_total != secondary_total:
            print(f"FAIL: {tiers[0]} vs {tiers[1]} totals {primary_total} != {secondary_total}")
            failed = True

    if failed:
        return 1

    for tier, summary in summaries.items():
        out_path = PROCESSED / f"{tier}_summary.json"
        summary.to_json(out_path, orient="records", indent=2, force_ascii=False)

    print(f"top 5 {primary_tier}:")
    for row in primary_summary.nlargest(5, "total_count").itertuples(index=False):
        print(f"  {getattr(row, primary_tier)}: {row.total_count}")

    if len(tiers) >= 2:
        secondary = tiers[1]
        secondary_summary = summaries[secondary]
        print(f"top 5 {secondary}:")
        for row in secondary_summary.nlargest(5, "total_count").itertuples(index=False):
            print(f"  {getattr(row, secondary)}: {row.total_count}")
        n_primary = int((primary_summary["total_count"] > 0).sum())
        n_secondary = len(secondary_summary)
        breakdown = " / ".join(
            f"{int(primary_summary[col].sum())} {col.removesuffix('_count')}"
            for col in count_cols
        )
        print(
            f"{int(primary_summary['total_count'].sum())} / {breakdown} "
            f"across {n_primary} {primary_tier} and {n_secondary} {secondary}"
        )
    else:
        n_primary = int((primary_summary["total_count"] > 0).sum())
        breakdown = " / ".join(
            f"{int(primary_summary[col].sum())} {col.removesuffix('_count')}"
            for col in count_cols
        )
        print(
            f"{int(primary_summary['total_count'].sum())} / {breakdown} "
            f"across {n_primary} {primary_tier}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
