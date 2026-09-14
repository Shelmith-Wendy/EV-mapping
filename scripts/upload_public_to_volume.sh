#!/usr/bin/env bash
# Phase 2: upload data/public contract files to a UC Volume.
# Requires Databricks CLI auth and an existing Volume (bundle deploy creates it).
#
#   ./scripts/upload_public_to_volume.sh [catalog] [schema] [volume]
#
# Defaults: main zeno_ev_map public
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC="$ROOT/data/public"
CATALOG="${1:-main}"
SCHEMA="${2:-zeno_ev_map}"
VOLUME="${3:-public}"
DEST="/Volumes/${CATALOG}/${SCHEMA}/${VOLUME}"

if ! command -v databricks >/dev/null 2>&1; then
  echo "databricks CLI not found" >&2
  exit 1
fi

cd "$ROOT/scripts"
python3 volume_cli.py write-manifest --public-dir "$PUBLIC"
python3 volume_cli.py validate --public-dir "$PUBLIC" --catalog "$CATALOG" --schema "$SCHEMA" --volume "$VOLUME"

FILES=(
  county_summary.json
  town_summary.json
  stations_with_county.json
  kenya_counties_display.geojson
  manifest.json
)

echo "Uploading to ${DEST}/"
for name in "${FILES[@]}"; do
  src="$PUBLIC/$name"
  if [[ ! -f "$src" ]]; then
    echo "Missing $src" >&2
    exit 1
  fi
  databricks fs cp "$src" "${DEST}/${name}" --overwrite
  echo "  uploaded ${name}"
done

echo "Listing ${DEST}/"
databricks fs ls "$DEST"
echo "Done."
