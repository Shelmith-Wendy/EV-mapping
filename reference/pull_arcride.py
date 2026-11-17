import csv
from collections import Counter
from datetime import date
from math import cos, hypot, radians

import requests

CABINETS_URL = "https://arcrideglobal.com/api/cabinets"
OUTPUT_PATH = "arcride_stations.csv"
SAME_SITE_METERS = 30
KENYA_MIN_LAT, KENYA_MAX_LAT = -4.8, 5.2
KENYA_MIN_LNG, KENYA_MAX_LNG = 33.8, 42.0
KEPT_COLUMNS = ["operator", "name", "address", "latitude", "longitude",
                "cabinet_count", "source", "retrieved_on"]

response = requests.get(
    CABINETS_URL,
    headers={"User-Agent": "station-dashboard/1.0"},
    timeout=30,
)
response.raise_for_status()
records = response.json()["results"]

sites = []
all_device_ids = []
unnamed_locations = []
outside_kenya = bad_coordinates = 0

for record_number, record in enumerate(records):
    try:
        latitude = float(record["lat"])
        longitude = float(record["lng"])
    except (KeyError, TypeError, ValueError):
        bad_coordinates += 1
        continue
    if not (KENYA_MIN_LAT <= latitude <= KENYA_MAX_LAT and KENYA_MIN_LNG <= longitude <= KENYA_MAX_LNG):
        outside_kenya += 1
        continue

    name = " ".join((record.get("name") or "").split())
    if not name:
        unnamed_locations.append((round(latitude, 3), round(longitude, 3)))
        continue

    device_id = record.get("imei_number") or record.get("serial_number") or f"no-id-{record_number}"
    all_device_ids.append(device_id)
    meters_per_degree_lng = 111_320 * cos(radians(latitude))
    site = next(
        (candidate for candidate in sites
         if hypot((longitude - candidate["longitude"]) * meters_per_degree_lng,
                  (latitude - candidate["latitude"]) * 110_540) < SAME_SITE_METERS),
        None,
    )
    if site is None:
        site = {"operator": "ARC Ride", "address": record.get("address"),
                "latitude": latitude, "longitude": longitude,
                "device_ids": set(), "names": Counter(),
                "source": CABINETS_URL, "retrieved_on": date.today().isoformat()}
        sites.append(site)
    site["device_ids"].add(device_id)
    site["names"][name] += 1

if not sites:
    raise SystemExit("No Kenyan stations found, existing file left untouched.")

for site in sites:
    site["name"] = site["names"].most_common(1)[0][0]
    site["cabinet_count"] = len(site["device_ids"])

with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as output_file:
    writer = csv.DictWriter(output_file, fieldnames=KEPT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(sites)

duplicate_device_ids = len(all_device_ids) - len(set(all_device_ids))
print(f"records: {len(records)} | outside Kenya: {outside_kenya} | bad coordinates: {bad_coordinates} | unnamed: {len(unnamed_locations)}")
print(f"unnamed record locations: {unnamed_locations}")
print(f"Kenya sites: {len(sites)} | cabinets: {sum(site['cabinet_count'] for site in sites)} | device IDs repeated across records: {duplicate_device_ids}")
for site in sorted(sites, key=lambda site: site["cabinet_count"], reverse=True)[:5]:
    print(f"largest: {site['cabinet_count']} cabinets | names seen: {dict(site['names'])}")