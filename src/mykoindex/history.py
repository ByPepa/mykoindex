"""Rolující 30denní historie – kde a kdy byly ideální podmínky pro růst.

Každý běh uloží dnešní index grid, přidá záznam lokalit do `out/history.json`
a smaže vše starší než okno. Z uložených gridů spočítá:
  - „ideal_days" grid = kolik z posledních N dní byla buňka ideální (index ≥ práh),
  - „days_since_ideal" = jak dávno byla naposled ideální (kvůli fruktifikačnímu
    zpoždění ~1–2 týdny: kde bylo nedávno ideálně = kam teď vyrazit),
  - 30denní timeline skóre pro pojmenované lokality.

Historie žije v `data/history/` (grid npy, nepublikuje se) a `out/history.json`
(publikuje se pro web). Na serveru přežívá na disku; v CI se commituje do repa.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .grid import Grid
from .index import verdict

log = logging.getLogger(__name__)


def _hist_dir(cfg) -> Path:
    d = cfg.data_dir / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_date(name: str) -> date | None:
    try:
        return datetime.strptime(name, "%Y%m%d").date()
    except ValueError:
        return None


def _prune_grids(hist_dir: Path, keep_after: date) -> None:
    for f in hist_dir.glob("grid_*.npy"):
        d = _parse_date(f.stem.replace("grid_", ""))
        if d is None or d < keep_after:
            f.unlink(missing_ok=True)


def _load_history_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log.warning("history.json poškozený → zakládám nový")
    return {"days": []}


def record(cfg, grid: Grid, index_field: np.ndarray, today: date | None = None) -> dict:
    """Zaznamenej dnešek do historie a vrať odvozené výstupy pro export."""
    today = today or date.today()
    h = cfg.raw.get("history", {})
    window = int(h.get("window_days", 30))
    ideal_min = float(h.get("ideal_min_score", 60))
    keep_after = today - timedelta(days=window - 1)
    verdicts = cfg.model.get("verdicts", [])

    hist_dir = _hist_dir(cfg)

    # 1) ulož dnešní grid + prune
    np.save(hist_dir / f"grid_{today:%Y%m%d}.npy", index_field.astype("float32"))
    _prune_grids(hist_dir, keep_after)

    # 2) načti gridy v okně → ideal_days + days_since_ideal
    files = sorted(hist_dir.glob("grid_*.npy"))
    dated = [(_parse_date(f.stem.replace("grid_", "")), f) for f in files]
    dated = [(d, f) for d, f in dated if d and d >= keep_after]
    ideal_days = np.zeros(grid.shape, dtype=np.int16)
    days_since = np.full(grid.shape, -1, dtype=np.int16)  # -1 = nikdy
    for d, f in dated:
        try:
            g = np.load(f)
        except Exception:  # noqa: BLE001
            continue
        if g.shape != grid.shape:  # oblast se změnila → starý grid ignoruj
            continue
        mask = g >= ideal_min
        ideal_days += mask.astype(np.int16)
        ago = (today - d).days
        upd = mask & ((days_since == -1) | (ago < days_since))
        days_since[upd] = ago

    # 3) update out/history.json (lokality) + prune
    hj_path = cfg.out_dir / "history.json"
    hj = _load_history_json(hj_path)
    days = [row for row in hj.get("days", []) if row.get("date") != today.isoformat()]
    loc_today = []
    for loc in cfg.localities:
        s = float(grid.sample(index_field, loc.lon, loc.lat))
        loc_today.append({"name": loc.name, "score": round(s, 1),
                          "verdict": verdict(s, verdicts)})
    days.append({"date": today.isoformat(),
                 "region_mean": round(float(np.nanmean(index_field)), 1),
                 "region_max": round(float(np.nanmax(index_field)), 1),
                 "ideal_frac": round(float((index_field >= ideal_min).mean()), 3),
                 "localities": loc_today})
    # prune podle data
    days = [r for r in days if (_iso(r["date"]) and _iso(r["date"]) >= keep_after)]
    days.sort(key=lambda r: r["date"])
    hj = {"updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "window_days": window, "ideal_min_score": ideal_min, "days": days}
    hj_path.write_text(json.dumps(hj, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4) timeline per lokalita (z history.json)
    timelines: dict[str, list] = {loc.name: [] for loc in cfg.localities}
    for row in days:
        for l in row["localities"]:
            if l["name"] in timelines:
                timelines[l["name"]].append({"date": row["date"], "score": l["score"]})

    # 5) skóre historie u lokalit: ideal_days a naposledy před X dny
    loc_hist = {}
    for loc in cfg.localities:
        idl = int(grid.sample(ideal_days.astype(float), loc.lon, loc.lat, method="nearest"))
        rec = int(grid.sample(days_since.astype(float), loc.lon, loc.lat, method="nearest"))
        loc_hist[loc.name] = {"ideal_days": idl, "last_ideal_days_ago": (rec if rec >= 0 else None)}

    return {
        "n_history_days": len(dated),
        "window_days": window,
        "ideal_min_score": ideal_min,
        "ideal_days_grid": ideal_days,
        "ideal_days_max": int(ideal_days.max()) if ideal_days.size else 0,
        "days_since_ideal_grid": days_since,
        "locality_timelines": timelines,
        "locality_history": loc_hist,
    }


def _iso(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None
