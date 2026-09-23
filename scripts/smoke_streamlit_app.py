#!/usr/bin/env python3
"""Smoke checks: public data, embed wiring, static sync, optional live HTTP checks."""

from __future__ import annotations

import argparse
import re
import shutil
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
KENYA_FILES = dashboard_files_for(REGIONS["kenya"])


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"OK    {message}")


def newest_mtime(folder: Path) -> float:
    if not folder.is_dir():
        return 0.0
    return max((path.stat().st_mtime for path in folder.rglob("*") if path.is_file()), default=0.0)


def sync_static() -> None:
    if STATIC_DASHBOARD.exists():
        shutil.rmtree(STATIC_DASHBOARD)
    shutil.copytree(DASHBOARD_SRC, STATIC_DASHBOARD)

    STATIC_PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)
    regions_index = PUBLIC_ROOT / "regions.json"
    if regions_index.is_file():
        shutil.copy2(regions_index, STATIC_PUBLIC_ROOT / "regions.json")
    for region_dir in PUBLIC_ROOT.iterdir():
        if not region_dir.is_dir():
            continue
        dest_dir = STATIC_PUBLIC_ROOT / region_dir.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src in region_dir.glob("*"):
            if src.is_file():
                shutil.copy2(src, dest_dir / src.name)

    app_v = str(int((STATIC_DASHBOARD / "app.js").stat().st_mtime))
    css_v = str(int((STATIC_DASHBOARD / "styles.css").stat().st_mtime))
    index_path = STATIC_DASHBOARD / "index.html"
    html = index_path.read_text(encoding="utf-8")
    html = re.sub(r"styles\.css(?:\?v=[^\"']*)?", f"styles.css?v={css_v}", html)
    html = re.sub(r"app\.js(?:\?v=[^\"']*)?", f"app.js?v={app_v}", html)
    index_path.write_text(html, encoding="utf-8")


def check_sources() -> None:
    if not (PUBLIC_ROOT / "regions.json").is_file():
        fail("missing data/public/regions.json — run: python scripts/run_pipeline.py")
    ok("public/regions.json")
    kenya = PUBLIC_ROOT / "kenya"
    for name in KENYA_FILES:
        path = kenya / name
        if not path.is_file():
            fail(f"missing {path} — run: python scripts/run_pipeline.py")
        ok(f"public/kenya/{name}")
    for name in ("index.html", "app.js", "styles.css"):
        if not (DASHBOARD_SRC / name).is_file():
            fail(f"missing dashboard/{name}")
        ok(f"dashboard/{name}")


def check_embed_wiring() -> None:
    js = (DASHBOARD_SRC / "app.js").read_text(encoding="utf-8")
    css = (DASHBOARD_SRC / "styles.css").read_text(encoding="utf-8")
    checks = [
        ('URL_PARAMS.get("embed") === "1"' in js, "app.js reads embed=1"),
        ('URL_PARAMS.get("region")' in js, "app.js reads region param"),
        ('"/app/static/data/public"' in js, "app.js embedded PUBLIC_ROOT"),
        ("regions.json" in js, "app.js loads regions.json"),
        ("selectedRegionId" in js, "app.js single-region selection"),
        ("data-clear-primary" in js, "app.js Clear handlers"),
        ("body.is-embed .toolbar" in css, "styles.css hides toolbar when embedded"),
    ]
    for passed, label in checks:
        if not passed:
            fail(label)
        ok(label)


def check_static_copy() -> None:
    if not (STATIC_DASHBOARD / "index.html").is_file():
        fail("app/static/dashboard/index.html missing after sync")
    if not (STATIC_PUBLIC_ROOT / "regions.json").is_file():
        fail("app/static/data/public/regions.json missing after sync")
    for name in KENYA_FILES:
        if not (STATIC_PUBLIC_ROOT / "kenya" / name).is_file():
            fail(f"app/static/data/public/kenya/{name} missing after sync")
    stamped = (STATIC_DASHBOARD / "index.html").read_text(encoding="utf-8")
    if "app.js?v=" not in stamped or "styles.css?v=" not in stamped:
        fail("static index.html missing cache-bust query strings")
    if newest_mtime(DASHBOARD_SRC) > newest_mtime(STATIC_DASHBOARD) + 1:
        fail("static dashboard older than dashboard/ source")
    ok("app/static synced + cache-bust stamped")


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
    http_ok(f"{base}/app/static/dashboard/index.html?embed=1&region=kenya&type=all")
    http_ok(f"{base}/app/static/dashboard/app.js")
    http_ok(f"{base}/app/static/data/public/regions.json")
    for name in KENYA_FILES:
        http_ok(f"{base}/app/static/data/public/kenya/{name}")


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
    check_embed_wiring()
    sync_static()
    check_static_copy()

    if not args.skip_http and args.base_url:
        check_live(args.base_url)

    print("Streamlit smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
