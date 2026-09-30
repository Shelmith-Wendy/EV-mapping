#!/usr/bin/env bash
# Bake dashboard + public data into app/static so Databricks Apps (source_code_path=./app)
# can serve them with server.enableStaticServing. The Streamlit process only reads these files.
# Interpreter is `uv run python`. Set PYTHON to another executable to override (still via uv run).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
run_python() { uv run "${PYTHON:-python}" "$@"; }
APP="$ROOT/app"
PUBLIC_ROOT="$ROOT/data/public"
DASHBOARD_SRC="$ROOT/dashboard"
STATIC_DASHBOARD="$APP/static/dashboard"
STATIC_PUBLIC="$APP/static/data/public"

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
(cd "$ROOT/scripts" && run_python volume_cli.py write-manifest --public-dir "$PUBLIC_ROOT/kenya")
(cd "$ROOT/scripts" && run_python volume_cli.py validate --public-dir "$PUBLIC_ROOT/kenya") || {
  echo "Volume contract validation failed for $PUBLIC_ROOT/kenya" >&2
  exit 1
}

rm -rf "$STATIC_DASHBOARD"
mkdir -p "$APP/static"
cp -a "$DASHBOARD_SRC" "$STATIC_DASHBOARD"

rm -rf "$APP/static/data"
mkdir -p "$STATIC_PUBLIC"
cp -a "$PUBLIC_ROOT/regions.json" "$STATIC_PUBLIC/regions.json"
for region_dir in "$PUBLIC_ROOT"/*/; do
  region_id="$(basename "$region_dir")"
  files="$(
    cd "$ROOT/scripts" && REGION_ID="$region_id" run_python -c '
import os
from common import dashboard_files_for
from regions import REGIONS
region_id = os.environ["REGION_ID"]
if region_id not in REGIONS:
    raise SystemExit(0)
print("\n".join((*dashboard_files_for(REGIONS[region_id]), "manifest.json")))
'
  )"
  if [[ -z "$files" ]]; then
    continue
  fi
  mkdir -p "$STATIC_PUBLIC/$region_id"
  while IFS= read -r name; do
    name="${name%$'\r'}"
    [[ -n "$name" ]] || continue
    cp -a "$region_dir$name" "$STATIC_PUBLIC/$region_id/$name"
  done <<< "$files"
done

STATIC_DASHBOARD="$STATIC_DASHBOARD" run_python - <<'PY'
import hashlib
import os
import re
from pathlib import Path

dashboard = Path(os.environ["STATIC_DASHBOARD"])
index_path = dashboard / "index.html"

def short_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]

app_version = short_hash(dashboard / "app.js")
css_version = short_hash(dashboard / "styles.css")
original = index_path.read_text(encoding="utf-8")
stamped = re.sub(r"styles\.css(?:\?v=[^\"']*)?", f"styles.css?v={css_version}", original)
stamped = re.sub(r"app\.js(?:\?v=[^\"']*)?", f"app.js?v={app_version}", stamped)
index_path.write_text(stamped, encoding="utf-8")
print(f"stamped app.js?v={app_version} styles.css?v={css_version}")
PY

echo "Prepared Databricks app snapshot:"
echo "  $STATIC_DASHBOARD"
echo "  $STATIC_PUBLIC (regions.json + $(ls -1 "$STATIC_PUBLIC" | grep -v regions.json | wc -l) regions)"
