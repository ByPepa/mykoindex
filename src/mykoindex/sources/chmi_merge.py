"""ČHMÚ MERGE – páteř srážek (radar Brdy-Praha + Skalka slité se srážkoměry).

Reálný produkt (ověřeno na opendata.chmi.cz):
  adresář:  .../meteorology/weather/radar/composite/merge1h/hdf5/
  soubor:   T_PASV23_C_OKPR_YYYYMMDDHHMM00.hdf   (ODIM_H5, HDF5)
  obsah:    1h úhrn srážek (quantity ACRR, mm), gain/offset, nodata/undetect
  grid:     Mercator (projdef v atributech where/), ~1.56 km, UL roh = proj (0,0)
  čas:      konec 1h intervalu, UTC

POZOR: otevřený server drží jen ~6–7 dní historie. Plné 30denní okno API30 se
proto BUDUJE postupně – každý stažený soubor se trvale cachuje do data/merge/,
takže denní cron okno postupně naplní. Viz DECISIONS.md.

V kopcích radar podhodnocuje (stínění) → slévání se srážkoměry/Netatmo
je zásadní; to obstarává merge.py.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import numpy as np

from ..grid import Grid

log = logging.getLogger(__name__)

_DEFAULT_DIR = "https://opendata.chmi.cz/meteorology/weather/radar/composite/merge1h/hdf5"
_DEFAULT_FILENAME = "T_PASV23_C_OKPR_{dt:%Y%m%d%H}0000.hdf"


def _urls(cfg, dt: datetime) -> tuple[str, str]:
    ch = cfg.sources.get("chmi", {})
    base = ch.get("merge_dir", _DEFAULT_DIR).rstrip("/")
    fname = ch.get("filename_template", _DEFAULT_FILENAME).format(dt=dt)
    return base + "/" + fname, fname


def _download_hour_file(cfg, dt: datetime):
    """Stáhni (a trvale zacachuj) jeden hodinový ODIM soubor. Cesta k .hdf nebo None."""
    import requests

    url, fname = _urls(cfg, dt)
    cache = cfg.data_dir / "merge" / fname
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and cache.stat().st_size > 0:
        return cache
    try:
        r = requests.get(url, timeout=60)
    except Exception as exc:  # noqa: BLE001
        log.debug("MERGE download %s: %s", url, exc)
        return None
    if r.status_code != 200 or not r.content:
        return None
    cache.write_bytes(r.content)
    return cache


def _read_odim_to_grid(path, grid: Grid) -> np.ndarray | None:
    """Přečti ODIM_H5 1h úhrn a reprojektuj (Mercator → pracovní mřížka)."""
    try:
        import h5py
        from pyproj import CRS, Transformer
    except Exception as exc:  # noqa: BLE001
        log.warning("chmi_merge: chybí h5py/pyproj (%s)", exc)
        return None

    with h5py.File(path, "r") as f:
        d = f["dataset1/data1"]
        raw = d["data"][:].astype(float)
        what = d["what"].attrs
        gain = float(what["gain"]); offset = float(what["offset"])
        nodata = float(what["nodata"]); undetect = float(what["undetect"])
        vals = raw * gain + offset
        vals[raw == nodata] = np.nan
        vals[raw == undetect] = 0.0

        wh = f["where"].attrs
        projdef = wh["projdef"]
        projdef = projdef.decode() if isinstance(projdef, bytes) else projdef
        xscale = float(wh["xscale"]); yscale = float(wh["yscale"])
        xsize = int(wh["xsize"]); ysize = int(wh["ysize"])
        ul_lon = float(wh["UL_lon"]); ul_lat = float(wh["UL_lat"])

    crs = CRS.from_proj4(projdef)
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x0, y0 = fwd.transform(ul_lon, ul_lat)  # projected UL roh

    LON, LAT = grid.meshgrid()
    X, Y = fwd.transform(LON.ravel(), LAT.ravel())
    col = (X - x0) / xscale - 0.5   # index středů pixelů
    row = (y0 - Y) / yscale - 0.5

    out = _bilinear(vals, col, row, xsize, ysize).reshape(grid.shape)
    return out


def _bilinear(field, col, row, xsize, ysize):
    inside = (col >= 0) & (col <= xsize - 1) & (row >= 0) & (row <= ysize - 1)
    c0 = np.clip(np.floor(col).astype(int), 0, xsize - 1)
    r0 = np.clip(np.floor(row).astype(int), 0, ysize - 1)
    c1 = np.clip(c0 + 1, 0, xsize - 1)
    r1 = np.clip(r0 + 1, 0, ysize - 1)
    tc = np.clip(col - c0, 0, 1)
    tr = np.clip(row - r0, 0, 1)
    v00 = field[r0, c0]; v10 = field[r0, c1]
    v01 = field[r1, c0]; v11 = field[r1, c1]
    top = v00 * (1 - tc) + v10 * tc
    bot = v01 * (1 - tc) + v11 * tc
    out = top * (1 - tr) + bot * tr
    out[~inside] = np.nan
    return out


def fetch_daily_precip(
    cfg, grid: Grid, end_day: date | None = None, days: int = 30
) -> np.ndarray | None:
    """Denní úhrny na pracovní mřížce, tvar (days, ny, nx), index 0 = end_day.

    Pro den D se sčítá 24 hodinových úhrnů končících v D 01:00 … (D+1) 00:00 UTC.
    Chybějící hodiny (rolled-off historie) se přeskočí; den bez dat → NaN→0.
    Vrací None, pokud se nestáhl ani jeden soubor.
    """
    end_day = end_day or date.today()
    stack = np.full((days, grid.ny, grid.nx), np.nan, dtype=float)
    any_hour = False

    for di in range(days):
        the_day = end_day - timedelta(days=di)
        hours = []
        for h in range(24):
            dt = datetime(the_day.year, the_day.month, the_day.day, tzinfo=timezone.utc) + timedelta(hours=h + 1)
            path = _download_hour_file(cfg, dt)
            if path is None:
                continue
            field = _read_odim_to_grid(path, grid)
            if field is not None:
                hours.append(field)
                any_hour = True
        if hours:
            stack[di] = np.nansum(np.stack(hours, axis=0), axis=0)

    if not any_hour:
        log.warning("chmi_merge: nestáhl se žádný MERGE soubor → přeskočeno")
        return None
    n_missing = int(np.isnan(stack).all(axis=(1, 2)).sum())
    if n_missing:
        log.warning(
            "chmi_merge: %d/%d dní bez dat (server drží ~6–7 dní; okno se buduje cronem)",
            n_missing, days,
        )
    return np.nan_to_num(stack, nan=0.0)


def synthetic_daily_precip(grid: Grid, days: int = 30, seed: int = 20) -> np.ndarray:
    """Syntetická páteř: 30 dní se srážkovou epizodou a prostorovým vzorem.

    Index 0 = dnešek. Epizoda ~ před 5–8 dny přinese víc srážek na SV (hory).
    """
    rng = np.random.default_rng(seed)
    LON, LAT = grid.meshgrid()
    oro = 0.6 + 1.0 * np.clip((LAT - grid.lat_min) / (grid.lat_max - grid.lat_min), 0, 1)
    oro = oro * (0.7 + 0.6 * np.clip((LON - grid.lon_min) / (grid.lon_max - grid.lon_min), 0, 1))

    stack = np.zeros((days, grid.ny, grid.nx), dtype=float)
    for d in range(days):
        base = max(0.0, rng.normal(1.2, 1.5))
        if 5 <= d <= 8:
            base += rng.uniform(6, 12)
        wobble = 1.0 + 0.3 * np.sin(LON * 30 + d) * np.cos(LAT * 30 - d)
        field = base * oro * wobble + rng.normal(0, 0.3, size=grid.shape)
        stack[d] = np.clip(field, 0.0, None)
    return stack
