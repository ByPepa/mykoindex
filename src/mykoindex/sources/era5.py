"""Teplota: ERA5-Land 2m_temperature přes Copernicus CDS (cdsapi).

Reálně: denní průměr za posledních N dní, prostorově hrubý (~9 km) →
bereme jako regionální pole; detail dodá výškový gradient v index.py.
Bez klíče/knihovny: synthetic() pro ``--demo``.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np

from ..grid import Grid, reproject_to_grid

log = logging.getLogger(__name__)


def fetch(cfg, grid: Grid, day: date | None = None) -> np.ndarray | None:
    """Vrať regionální T_mean [°C] na mřížce (průměr za temp_window_days), nebo None."""
    day = day or date.today()
    window = int(cfg.model.get("temp_window_days", 7))
    api_key = cfg.env.get("CDS_API_KEY")
    if not api_key:
        log.info("era5: CDS_API_KEY není nastaven → přeskočeno")
        return None
    try:
        import cdsapi
        import xarray as xr

        lon_min, lat_min, lon_max, lat_max = grid.bbox
        days = [day - timedelta(days=i) for i in range(window)]
        cache = cfg.data_dir / f"era5_{day.isoformat()}_{window}d.nc"

        if not cache.exists():
            c = cdsapi.Client(
                url=cfg.env.get("CDS_API_URL") or "https://cds.climate.copernicus.eu/api",
                key=api_key,
            )
            c.retrieve(
                cfg.sources.get("era5", {}).get("dataset", "reanalysis-era5-land"),
                {
                    "variable": cfg.sources.get("era5", {}).get("variable", "2m_temperature"),
                    "year": sorted({d.strftime("%Y") for d in days}),
                    "month": sorted({d.strftime("%m") for d in days}),
                    "day": sorted({d.strftime("%d") for d in days}),
                    "time": [f"{h:02d}:00" for h in range(0, 24, 3)],
                    "area": [lat_max, lon_min, lat_min, lon_max],  # N,W,S,E
                    # nový CDS: data_format + download_format (jinak vrací zip)
                    "data_format": "netcdf",
                    "download_format": "unarchived",
                },
                str(cache),
            )

        # ERA5-Land občas vrátí zip i při unarchived → rozbal
        if _is_zip(cache):
            cache = _unzip_first_nc(cache)

        ds = xr.open_dataset(cache)
        tvar = "t2m" if "t2m" in ds else list(ds.data_vars)[0]
        t = ds[tvar].mean(dim=[d for d in ("time", "valid_time") if d in ds[tvar].dims])
        t_c = t.values - 273.15  # K -> °C
        lats = ds["latitude"].values
        lons = ds["longitude"].values
        ds.close()
        return reproject_to_grid(t_c, lons, lats, grid)
    except Exception as exc:  # noqa: BLE001
        log.warning("era5: stažení teploty selhalo (%s) → přeskočeno", exc)
        return None


def _is_zip(path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"PK"
    except Exception:  # noqa: BLE001
        return False


def _unzip_first_nc(path):
    """Rozbal první .nc ze zip archivu vedle původního souboru."""
    import zipfile
    from pathlib import Path

    p = Path(path)
    with zipfile.ZipFile(p) as z:
        ncs = [n for n in z.namelist() if n.endswith(".nc")]
        if not ncs:
            return p
        out = p.with_suffix(".unzipped.nc")
        with z.open(ncs[0]) as src, open(out, "wb") as dst:
            dst.write(src.read())
    return out


def synthetic(grid: Grid, mean_c: float = 15.5, seed: int = 3) -> np.ndarray:
    """Regionální teplota s mírným S-J gradientem (na výšku nereaguje – to řeší lapse)."""
    rng = np.random.default_rng(seed)
    LON, LAT = grid.meshgrid()
    grad = (grid.lat_max - LAT) * 1.5  # jih o ~0.7 °C tepleji
    field = mean_c + grad - grad.mean() + rng.normal(0, 0.2, size=grid.shape)
    return field
