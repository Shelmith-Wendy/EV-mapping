import csv
import json
import os
from collections import Counter
from datetime import date

SNAPSHOT_PATH = "spiro_map_payload.json"
OUTPUT_PATH = "spiro_stations.csv"
KENYA_MIN_LAT, KENYA_MAX_LAT = -4.8, 5.2
KENYA_MIN_LNG, KENYA_MAX_LNG = 33.8, 42.0
KEPT_COLUMNS = ["operator", "name", "address", "latitude", "longitude",
                "cabinet_count", "source", "retrieved_on"]

with open(SNAPSHOT_PATH, encoding="utf-8-sig") as snapshot_file:
    payload = json.load(snapshot_file)

snapshot_date = date.fromtimestamp(os.path.getmtime(SNAPSHOT_PATH)).isoformat()
city_by_id = {city["id"]: city for city in payload["cities"]}

stations = []
seen_stations = set()
stations_per_city = Counter()
bad_coordinates = outside_kenya_box = duplicates = 0

for marker in payload["markers"]:
    city = city_by_id.get(marker.get("cityId"), {})
    if marker.get("categoryId") != "swap-station" or city.get("country") != "KE":
        continue
    try:
        latitude_text, longitude_text = marker["coordinates"].split(",")
        latitude, longitude = float(latitude_text), float(longitude_text)
    except (KeyError, AttributeError, ValueError):
        bad_coordinates += 1
        continue
    if not (KENYA_MIN_LAT <= latitude <= KENYA_MAX_LAT and KENYA_MIN_LNG <= longitude <= KENYA_MAX_LNG):
        outside_kenya_box += 1
        continue
    station_key = (marker.get("name"), round(latitude, 6), round(longitude, 6))
    if station_key in seen_stations:
        duplicates += 1
        continue
    seen_stations.add(station_key)
    stations_per_city[city.get("name")] += 1
    stations.append({
        "operator": "Spiro",
        "name": (marker.get("name") or "").strip(),
        "address": (marker.get("address") or "").strip(),
        "latitude": latitude,
        "longitude": longitude,
        "cabinet_count": "",
        "source": "spironet.com map payload (saved from browser)",
        "retrieved_on": snapshot_date,
    })

if not stations:
    raise SystemExit("No Kenyan swap stations found, no file written.")

with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as output_file:
    writer = csv.DictWriter(output_file, fieldnames=KEPT_COLUMNS)
    writer.writeheader()
    writer.writerows(stations)

print(f"Kenyan swap stations kept: {len(stations)} | bad coordinates: {bad_coordinates} | "
      f"outside Kenya box: {outside_kenya_box} | exact duplicates: {duplicates}")
print("top cities:", stations_per_city.most_common(10))
for station in stations[:5]:
    print(f"sample: {station['name']!r} | {station['address']!r} | ({station['latitude']}, {station['longitude']})")