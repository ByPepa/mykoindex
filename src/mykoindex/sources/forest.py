"""Lesní maska (+ volitelně druh porostu).

Reálně: ESA WorldCover (třída stromy) / Copernicus HRL z lokálního rastru
(``sources.forest.mask_path``). Bez masky: syntetická lesnatost pro ``--demo``
(les kopíruje vyšší polohy).

Druhová vrstva (species_layer) je volitelná: hrubá klasifikace
smrčiny/bučiny/březiny; MVP dodává jen binární les.
"""
from __future__ import annotations

import logging

import numpy as np

from ..grid import Grid, reproject_to_grid

log = logging.getLogger(__name__)


def fetch(cfg, grid: Grid) -> np.ndarray | None:
    """Vrať forest01 ∈ [0,1] na mřížce, nebo None při chybě."""
    mask_path = cfg.sources.get("forest", {}).get("mask_path")
    if not mask_path:
        log.info("forest: mask_path není nastaven → přeskočeno")
        return None
    from pathlib import Path

    p = Path(mask_path)
    mask_path = str(p if p.is_absolute() else cfg.root / p)
    try:
        import rasterio

        with rasterio.open(mask_path) as ds:
            data = ds.read(1).astype(float)
            b = ds.bounds
            src_lons = np.linspace(b.left, b.right, ds.width)
            src_lats = np.linspace(b.top, b.bottom, ds.height)
        # ESA WorldCover: třída 10 = stromy. Když jde o podíl 0-100, znormuj.
        if np.nanmax(data) > 1.5:
            if np.isin(data, [10]).any():
                data = (data == 10).astype(float)
            else:
                data = data / 100.0
        forest = reproject_to_grid(data, src_lons, src_lats, grid)
        return np.clip(forest, 0.0, 1.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("forest: čtení masky selhalo (%s) → přeskočeno", exc)
        return None


def synthetic(grid: Grid, elev: np.ndarray | None = None, seed: int = 7) -> np.ndarray:
    """Syntetická lesnatost: víc lesa ve vyšších polohách."""
    rng = np.random.default_rng(seed)
    if elev is None:
        LON, LAT = grid.meshgrid()
        elev = 250 + 400 * np.exp(-(((LON - 17.85) ** 2) / 0.02))
    e = np.asarray(elev, dtype=float)
    frac = np.clip((e - 220.0) / 250.0, 0.0, 1.0)
    frac = 0.85 * frac + 0.10
    frac = np.clip(frac + rng.normal(0, 0.08, size=grid.shape), 0.0, 1.0)
    return frac
