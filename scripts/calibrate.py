#!/usr/bin/env python3
"""Kalibrace prahů/vah z GBIF nálezů (M6).

    python scripts/calibrate.py           # stáhne GBIF (nebo demo syntetika)
    python scripts/calibrate.py --demo    # syntetické nálezy, bez sítě
    python scripts/calibrate.py --write   # zapíše doporučení do DECISIONS.md

Reprodukovatelně vrátí doporučené w_moist/w_temp a prahy verdiktů.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mykoindex.calibrate import calibrate  # noqa: E402
from mykoindex.config import load_config  # noqa: E402
from mykoindex.sources import gbif  # noqa: E402


def _append_decisions(root: Path, cal, mode: str) -> None:
    block = [
        "",
        f"## Kalibrace z GBIF ({date.today().isoformat()}, režim: {mode})",
        "",
        f"- Nálezů (presence): {cal.n_presence}, pozadí: {cal.n_background}, AUC: {cal.auc}",
        f"- Doporučené váhy: `w_moist = {cal.w_moist}`, `w_temp = {cal.w_temp}`",
        f"- Doporučené prahy verdiktů: {json.dumps(cal.verdict_thresholds, ensure_ascii=False)}",
    ]
    for n in cal.notes:
        block.append(f"- Pozn.: {n}")
    block.append("")
    path = root / "DECISIONS.md"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(block))
    print(f"Zapsáno do {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Mykoindex – kalibrace z GBIF")
    ap.add_argument("--demo", action="store_true", help="syntetické nálezy, bez sítě")
    ap.add_argument("--write", action="store_true", help="zapiš doporučení do DECISIONS.md")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    cfg = load_config()
    if args.demo:
        occ = gbif.synthetic(cfg)
        mode = "demo"
    else:
        occ = gbif.fetch(cfg)
        if occ is None:
            print("GBIF nedostupný → padám na syntetiku.")
            occ = gbif.synthetic(cfg)
            mode = "demo (fallback)"
        else:
            mode = "live"

    cal = calibrate(cfg, occ)
    print(json.dumps(cal.__dict__, ensure_ascii=False, indent=2))
    if args.write:
        _append_decisions(cfg.root, cal, mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
