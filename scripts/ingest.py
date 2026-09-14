#!/usr/bin/env python3
"""Ingest Zeno station listings into a source-agnostic standard schema."""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from common import (
    ROOT,
    STATION_TYPE_VALUES,
    STATIONS_STANDARD,
    ZENO_STATION_TYPES,
    data_dir,
    write_json,
)

RAW_HTML = ROOT / "data" / "raw" / "zeno_locations.html"
PROCESSED = data_dir()
ZENO_URL = "https://zeno.earth/locations/locations.html"

PLUS_CODE_RE = re.compile(r"^[A-Z0-9]{4,8}\+[A-Z0-9]{2,3}", re.I)
POI_SUFFIXES = {"church", "hub", "hq", "roundabout", "rd", "road"}
PLACE_HINTS = {"road", "rd", "bypass", "way", "crescent", "cres", "square", "roundabout"}
GENERIC_LAST = {"town", "township"}
BUSINESS_LAST = {
    "autospares", "pharmacy", "shop", "services", "technologies",
    "motorcycles", "motorcycle", "hardware", "salon", "studio",
    "building", "house", "spares", "parts", "enterprise", "missions",
    "wholesales", "bookshop", "cyber", "laundry", "charger", "station",
    "solution", "solutions", "master", "safari", "automart", "hotel",
    "centre", "center", "mall", "plaza", "motors",
}


def load_zeno_html() -> str:
    force = os.environ.get("KENYA_EV_FORCE_FETCH", "").strip().lower() in {"1", "true", "yes"}
    if force or not RAW_HTML.exists() or RAW_HTML.stat().st_size == 0:
        response = requests.get(ZENO_URL, timeout=30)
        response.raise_for_status()
        RAW_HTML.parent.mkdir(parents=True, exist_ok=True)
        RAW_HTML.write_bytes(response.content)
    return RAW_HTML.read_text(encoding="utf-8")


def cell_text(row, class_name: str) -> str:
    cell = row.find("td", class_=class_name)
    return cell.get_text(strip=True) if cell else ""


def host_name(full_name: str) -> str:
    if "—" in full_name:
        return full_name.split("—", 1)[1].strip()
    return full_name


def place_key(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip().rstrip(".,").strip().casefold()
    for suffix in (" town", " township"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def is_locality(text: str, locality: str) -> bool:
    return bool(text) and place_key(text) == place_key(locality)


def extract_landmark(name: str, address: str, locality: str) -> str | None:
    host = re.sub(r"\s+", " ", host_name(name)).strip()

    if "," in host:
        suffix = host.rsplit(",", 1)[1].strip()
        if suffix and not is_locality(suffix, locality):
            cleaned = re.sub(r"^(opposite|opp\.?)\s+", "", suffix, flags=re.I).strip()
            return cleaned or suffix
        prefix = host.rsplit(",", 1)[0].strip()
        if prefix and not is_locality(prefix, locality):
            prefix_tokens = set(place_key(prefix).split())
            if prefix_tokens & PLACE_HINTS:
                return prefix
    else:
        words = host.split()
        last = words[-1].casefold() if words else ""
        if len(words) >= 2 and last in POI_SUFFIXES:
            if last in {"rd", "road"}:
                return host
            remainder = re.sub(r"\bZeno\b", "", " ".join(words[:-1])).strip()
            if remainder and not is_locality(remainder, locality):
                tokens = remainder.split()
                if last == "church" and len(tokens) == 2:
                    return tokens[0]
                return remainder
        if len(words) == 1 and host and not is_locality(host, locality):
            return host
        if (
            len(words) >= 2
            and not is_locality(host, locality)
            and place_key(locality) not in place_key(host)
        ):
            last_word = words[-1]
            if last_word.casefold() not in BUSINESS_LAST | GENERIC_LAST and not is_locality(last_word, locality):
                return last_word

    addr_rest = PLUS_CODE_RE.sub("", address).strip(" ,")
    if addr_rest and not is_locality(addr_rest, locality):
        if place_key(locality) in place_key(host):
            return None
        return addr_rest

    return None


def parse_zeno_source(html: str, retrieved_at: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for row in soup.select("table tbody tr"):
        name = cell_text(row, "name")
        address = cell_text(row, "addr")
        locality = cell_text(row, "locality")
        raw_type = cell_text(row, "type-col")
        gmap = row.find("td", class_="gmap")
        maps_link = gmap.find("a") if gmap else None
        records.append({
            "station_id": cell_text(row, "code"),
            "name": name,
            "station_type": ZENO_STATION_TYPES.get(raw_type, raw_type),
            "locality": locality,
            "landmark": extract_landmark(name, address, locality),
            "address_raw": address,
            "phone": cell_text(row, "phone"),
            "hours": cell_text(row, "hours"),
            "source_maps_url": maps_link["href"] if maps_link and maps_link.has_attr("href") else "",
            "source_name": "zeno_earth",
            "retrieved_at": retrieved_at,
        })
    return records


def warn_unknown_types(records: list[dict]) -> None:
    unknown = sorted({row["station_type"] for row in records} - STATION_TYPE_VALUES)
    if unknown:
        print(f"WARNING: unknown station_type values: {unknown}")


def write_outputs(records: list[dict]) -> None:
    write_json(PROCESSED / STATIONS_STANDARD, records)


def print_summary(records: list[dict]) -> None:
    type_counts = Counter(row["station_type"] for row in records)
    landmark_count = sum(1 for row in records if row["landmark"])
    print(f"total: {len(records)}")
    print(f"by station_type: {dict(type_counts)}")
    print(f"landmark not null: {landmark_count}")


def main() -> int:
    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    records = parse_zeno_source(load_zeno_html(), retrieved_at)
    print_summary(records)
    warn_unknown_types(records)
    write_outputs(records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
