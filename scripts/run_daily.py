#!/usr/bin/env python3
"""Spočítej dnešní mykoindex a vyexportuj out/index.tif, index.png, localities.json.

    python scripts/run_daily.py           # ostrá data (potřebuje tokeny/klíče)
    python scripts/run_daily.py --demo    # syntetika, bez tokenů (vývoj, CI)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# umožni spuštění bez instalace balíku
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mykoindex.config import load_config  # noqa: E402
from mykoindex.pipeline import run_and_export  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Mykoindex – denní běh")
    ap.add_argument("--demo", action="store_true", help="běž na syntetice bez tokenů")
    ap.add_argument("--config", default=None, help="cesta ke config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    summary = run_and_export(cfg, demo=args.demo)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
