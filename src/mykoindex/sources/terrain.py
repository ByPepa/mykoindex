"""Terén: nadmořská výška a expozice svahu (northness).

Reálně: Copernicus DEM GLO-30 / SRTM z lokálního souboru (``sources.terrain.dem_path``).
Bez DEM: syntetický reliéf pro ``--demo`` (vyšší v Hostýnských vrších na SV).
"""
from __future__ import annotations

import logging

import numpy as np

from ..grid import Grid, reproject_to_grid

log = logging.getLogger(__name__)


def northness_from_dem(elev: np.ndarray, grid: Grid) -> np.ndarray:
    """Expozice svahu na sever jako projekce normály. Rovina → 0."""
    gx, gy = grid.xy_meters()
    dzdy, dzdx = np.gradient(elev, gy[:, 0], gx[0, :])
    # sklon k severu: -dz/dy (y roste k severu), normalizovaný sklonem
    slope = np.hypot(dzdx, dzdy)
    with np.errstate(invalid="ignore", divide="ignore"):
        north = np.where(slope > 1e-6, -dzdy / np.sqrt(1.0 + slope**2), 0.0)
    return np.clip(np.nan_to_num(north), -1.0, 1.0)


def fetch(cfg, grid: Grid) -> tuple[np.ndarray, np.ndarray] | None:
    """Vrať (elev_m, northness) na pracovní mřížce, nebo None při chybě."""
    dem_path = cfg.sources.get("terrain", {}).get("dem_path")
    if not dem_path:
        log.info("terrain: dem_path není nastaven → přeskočeno")
        return None
    from pathlib import Path

    p = Path(dem_path)
    dem_path = str(p if p.is_absolute() else cfg.root / p)
    try:
        import rasterio
        from rasterio.warp import transform_bounds

        with rasterio.open(dem_path) as ds:
            data = ds.read(1).astype(float)
            # osy zdroje
            b = ds.bounds
            src_lons = np.linspace(b.left, b.right, ds.width)
            src_lats = np.linspace(b.top, b.bottom, ds.height)
        elev = reproject_to_grid(data, src_lons, src_lats, grid)
        return elev, northness_from_dem(elev, grid)
    except Exception as exc:  # noqa: BLE001 – zdroj smí selhat
        log.warning("terrain: čtení DEM selhalo (%s) → přeskočeno", exc)
        return None


def synthetic(grid: Grid, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    """Syntetický reliéf: hřbet Hostýnských vrchů na SV oblasti."""
    LON, LAT = grid.meshgrid()
    # dvě „hory" + rovina k JZ
    base = 250.0
    ridge = 450.0 * np.exp(-(((LON - 17.85) ** 2) / 0.02 + ((LAT - 49.45) ** 2) / 0.02))
    ridge2 = 300.0 * np.exp(-(((LON - 17.70) ** 2) / 0.03 + ((LAT - 49.40) ** 2) / 0.03))
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 15, size=grid.shape)
    elev = base + ridge + ridge2 + noise
    elev = np.clip(elev, 180.0, None)
    return elev, northness_from_dem(elev, grid)
