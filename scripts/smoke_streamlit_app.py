#!/usr/bin/env python3
"""Smoke checks: public data, dashboard wiring, shell, bake, optional live HTTP checks."""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

from common import dashboard_files_for
from regions import REGIONS

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
DASHBOARD_SRC = ROOT / "dashboard"
PUBLIC_ROOT = ROOT / "data" / "public"
STATIC_DASHBOARD = APP_DIR / "static" / "dashboard"
STATIC_PUBLIC_ROOT = APP_DIR / "static" / "data" / "public"


def published_files(cfg) -> tuple[str, ...]:
    return (*dashboard_files_for(cfg), "manifest.json")


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"OK    {message}")


def check_shell() -> None:
    source = (APP_DIR / "app.py").read_text(encoding="utf-8")
    if 'MAP_PATH = "/app/static/dashboard/index.html"' not in source:
        fail("app.py MAP_PATH")
    ok("app.py MAP_PATH")
    if "st.query_params" not in source:
        fail("app.py reads st.query_params")
    ok("app.py reads st.query_params")
    if "embed" in source:
        fail("app.py must not reference embed")
    ok("app.py does not pass embed")


def check_sources() -> None:
    if not (PUBLIC_ROOT / "regions.json").is_file():
        fail("missing data/public/regions.json — run: python scripts/run_pipeline.py")
    ok("public/regions.json")
    for region_id, cfg in REGIONS.items():
        for name in published_files(cfg):
            path = PUBLIC_ROOT / region_id / name
            if not path.is_file():
                fail(f"missing {path} — run: python scripts/run_pipeline.py")
            ok(f"public/{region_id}/{name}")
    for name in ("index.html", "app.js", "styles.css"):
        if not (DASHBOARD_SRC / name).is_file():
            fail(f"missing dashboard/{name}")
        ok(f"dashboard/{name}")


def check_dashboard() -> None:
    js = (DASHBOARD_SRC / "app.js").read_text(encoding="utf-8")
    css = (DASHBOARD_SRC / "styles.css").read_text(encoding="utf-8")
    checks = [
        ('const PUBLIC_ROOT = "../data/public"' in js, "app.js PUBLIC_ROOT"),
        ('URL_PARAMS.get("region")' in js, "app.js reads region param"),
        ('URL_PARAMS.get("manufacturer")' in js, "app.js reads manufacturer param"),
        ("retrieved_at" in js, "app.js reads retrieved_at"),
        ("regions.json" in js, "app.js loads regions.json"),
        ("selectedRegionId" in js, "app.js single-region selection"),
        ("data-clear-primary" in js, "app.js Clear handlers"),
        ("embed" not in js and "is-embed" not in css, "dashboard does not mention embed"),
    ]
    for passed, label in checks:
        if not passed:
            fail(label)
        ok(label)


def check_bake() -> None:
    index_path = STATIC_DASHBOARD / "index.html"
    app_js = STATIC_DASHBOARD / "app.js"
    styles = STATIC_DASHBOARD / "styles.css"
    if not index_path.is_file() or not app_js.is_file() or not styles.is_file():
        fail("baked app/static/dashboard missing — run ./scripts/prepare_databricks_app.sh")
    if not (STATIC_PUBLIC_ROOT / "regions.json").is_file():
        fail("baked app/static/data/public/regions.json missing — run ./scripts/prepare_databricks_app.sh")
    for region_id, cfg in REGIONS.items():
        for name in published_files(cfg):
            if not (STATIC_PUBLIC_ROOT / region_id / name).is_file():
                fail(f"baked app/static/data/public/{region_id}/{name} missing — run ./scripts/prepare_databricks_app.sh")
    if not (STATIC_PUBLIC_ROOT / "taipei" / "tvs_candidates.json").is_file():
        fail("baked app/static/data/public/taipei/tvs_candidates.json missing — run ./scripts/prepare_databricks_app.sh")
    ok("baked app/static/data/public/taipei/tvs_candidates.json")
    stamped = index_path.read_text(encoding="utf-8")
    app_hash = hashlib.sha256(app_js.read_bytes()).hexdigest()[:12]
    css_hash = hashlib.sha256(styles.read_bytes()).hexdigest()[:12]
    if f"app.js?v={app_hash}" not in stamped or f"styles.css?v={css_hash}" not in stamped:
        fail("baked index.html cache-bust query does not match asset sha256")
    ok("baked app/static snapshot + cache-bust stamp")


def http_ok(url: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status != 200:
                fail(f"{url} -> HTTP {response.status}")
    except urllib.error.URLError as error:
        fail(f"{url} unreachable ({error})")
    ok(url)


def check_live(base: str) -> None:
    base = base.rstrip("/")
    http_ok(f"{base}/")
    http_ok(f"{base}/app/static/dashboard/index.html")
    http_ok(f"{base}/app/static/dashboard/app.js")
    http_ok(f"{base}/app/static/data/public/regions.json")
    for region_id, cfg in REGIONS.items():
        for name in published_files(cfg):
            http_ok(f"{base}/app/static/data/public/{region_id}/{name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8501",
        help="Streamlit base URL for live checks (empty to skip)",
    )
    parser.add_argument("--skip-http", action="store_true", help="Only check files, not the running app")
    args = parser.parse_args()

    check_sources()
    check_dashboard()
    check_shell()
    check_bake()

    if not args.skip_http and args.base_url:
        check_live(args.base_url)

    print("Streamlit smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
