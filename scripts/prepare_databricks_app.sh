#!/usr/bin/env bash
# Phase 1: bake dashboard + public data into app/ so Databricks Apps (source_code_path=./app) is self-contained.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/app"
PUBLIC_ROOT="$ROOT/data/public"
DASHBOARD_SRC="$ROOT/dashboard"

if [[ ! -d "$DASHBOARD_SRC" ]]; then
  echo "Missing $DASHBOARD_SRC" >&2
  exit 1
fi
if [[ ! -f "$PUBLIC_ROOT/regions.json" ]]; then
  echo "Missing $PUBLIC_ROOT/regions.json — run: python scripts/run_pipeline.py" >&2
  exit 1
fi
if [[ ! -d "$PUBLIC_ROOT/kenya" ]]; then
  echo "Missing $PUBLIC_ROOT/kenya — run: python scripts/run_pipeline.py" >&2
  exit 1
fi

# Ensure Phase 2 manifest exists for Kenya before bake.
(cd "$ROOT/scripts" && python3 volume_cli.py write-manifest --public-dir "$PUBLIC_ROOT/kenya")
(cd "$ROOT/scripts" && python3 volume_cli.py validate --public-dir "$PUBLIC_ROOT/kenya") || {
  echo "Volume contract validation failed for $PUBLIC_ROOT/kenya" >&2
  exit 1
}
rm -rf "$APP/dashboard"
cp -a "$DASHBOARD_SRC" "$APP/dashboard"

rm -rf "$APP/data/public"
mkdir -p "$APP/data/public"
cp -a "$PUBLIC_ROOT/regions.json" "$APP/data/public/regions.json"
for region_dir in "$PUBLIC_ROOT"/*/; do
  region_id="$(basename "$region_dir")"
  mkdir -p "$APP/data/public/$region_id"
  cp -a "$region_dir." "$APP/data/public/$region_id/"
done

echo "Prepared Databricks app snapshot:"
echo "  $APP/dashboard"
echo "  $APP/data/public (regions.json + $(ls -1 "$APP/data/public" | grep -v regions.json | wc -l) regions)"
