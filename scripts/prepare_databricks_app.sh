#!/usr/bin/env bash
# Phase 1: bake dashboard + public data into app/ so Databricks Apps (source_code_path=./app) is self-contained.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/app"
PUBLIC_SRC="$ROOT/data/public"
DASHBOARD_SRC="$ROOT/dashboard"

PUBLIC_FILES=(
  county_summary.json
  town_summary.json
  stations_with_county.json
  kenya_counties_display.geojson
  manifest.json
)

if [[ ! -d "$DASHBOARD_SRC" ]]; then
  echo "Missing $DASHBOARD_SRC" >&2
  exit 1
fi
if [[ ! -d "$PUBLIC_SRC" ]]; then
  echo "Missing $PUBLIC_SRC — run: python scripts/run_pipeline.py" >&2
  exit 1
fi

# Ensure Phase 2 manifest exists before bake.
(cd "$ROOT/scripts" && python3 volume_cli.py write-manifest --public-dir "$PUBLIC_SRC")
(cd "$ROOT/scripts" && python3 volume_cli.py validate --public-dir "$PUBLIC_SRC") || {
  echo "Volume contract validation failed for $PUBLIC_SRC" >&2
  exit 1
}
rm -rf "$APP/dashboard"
cp -a "$DASHBOARD_SRC" "$APP/dashboard"

mkdir -p "$APP/data/public"
for name in "${PUBLIC_FILES[@]}"; do
  src="$PUBLIC_SRC/$name"
  if [[ ! -f "$src" ]]; then
    echo "Missing $src — run: python scripts/run_pipeline.py" >&2
    exit 1
  fi
  cp -a "$src" "$APP/data/public/$name"
done

echo "Prepared Databricks app snapshot:"
echo "  $APP/dashboard"
echo "  $APP/data/public (${#PUBLIC_FILES[@]} files)"
