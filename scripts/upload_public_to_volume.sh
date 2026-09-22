#!/usr/bin/env bash
# Upload data/public contract files to a UC Volume (regions.json + each region/).
# Requires Databricks CLI auth and an existing Volume (bundle deploy creates it).
#
#   ./scripts/upload_public_to_volume.sh [catalog] [schema] [volume]
#
# Defaults: main zeno_ev_map public
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC_ROOT="$ROOT/data/public"
CATALOG="${1:-main}"
SCHEMA="${2:-zeno_ev_map}"
VOLUME="${3:-public}"
DEST="/Volumes/${CATALOG}/${SCHEMA}/${VOLUME}"

if ! command -v databricks >/dev/null 2>&1; then
  echo "databricks CLI not found" >&2
  exit 1
fi

if [[ ! -f "$PUBLIC_ROOT/regions.json" ]]; then
  echo "Missing $PUBLIC_ROOT/regions.json — run: python scripts/run_pipeline.py" >&2
  exit 1
fi

cd "$ROOT/scripts"

echo "Uploading regions.json to ${DEST}/"
databricks fs cp "$PUBLIC_ROOT/regions.json" "${DEST}/regions.json" --overwrite

for region_dir in "$PUBLIC_ROOT"/*/; do
  region_id="$(basename "$region_dir")"
  echo "Region ${region_id}:"
  python3 volume_cli.py write-manifest --public-dir "$region_dir"
  python3 volume_cli.py validate --public-dir "$region_dir" --catalog "$CATALOG" --schema "$SCHEMA" --volume "$VOLUME"

  mapfile -t FILES < <(python3 -c "
from pathlib import Path
from volume_contract import contract_files, region_id_from_public_dir
rid = region_id_from_public_dir(Path('$region_dir'))
print('\n'.join(contract_files(rid)))
")

  for name in "${FILES[@]}"; do
    src="$region_dir$name"
    if [[ ! -f "$src" ]]; then
      echo "Missing $src" >&2
      exit 1
    fi
    databricks fs cp "$src" "${DEST}/${region_id}/${name}" --overwrite
    echo "  uploaded ${region_id}/${name}"
  done
done

echo "Listing ${DEST}/"
databricks fs ls "$DEST"
echo "Done."
