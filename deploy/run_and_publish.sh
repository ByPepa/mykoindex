#!/usr/bin/env bash
# Denní běh na serveru: spočítej index (+ zapiš do historie) a zkopíruj do web rootu.
# Použití:  MYKOINDEX_WEBROOT=/var/www/mykoindex ./deploy/run_and_publish.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# venv (vytvoř: python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt)
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi

# statické vrstvy (DEM + les) stáhni jen poprvé
if [ ! -f data/dem_bbox.tif ] || [ ! -f data/worldcover_bbox.tif ]; then
  python scripts/fetch_static.py
fi

# spočítej dnešek (ostrá data; --demo pro test bez tokenů)
python scripts/run_daily.py -v

WEBROOT="${MYKOINDEX_WEBROOT:-/var/www/mykoindex}"
mkdir -p "$WEBROOT/out"
cp web/index.html "$WEBROOT/index.html"
cp out/localities.json "$WEBROOT/out/"
cp out/index.png       "$WEBROOT/out/" 2>/dev/null || true
cp out/ideal30.png     "$WEBROOT/out/" 2>/dev/null || true
cp out/history.json    "$WEBROOT/out/" 2>/dev/null || true

echo "$(date -u +%FT%TZ) publikováno do $WEBROOT"
