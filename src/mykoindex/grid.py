"""Pracovní mřížka: definice, sampling, reprojekce.

Mřížka je pravidelná v lon/lat (WGS84), s krokem odvozeným z ``resolution_m``
při středové šířce. Řádky jdou od severu k jihu (row 0 = nejsevernější),
aby seděly na konvenci rastru/obrázku a north-up GeoTIFF transformace.

Pro vzdálenostní výpočty (IDW) používáme lokální equirektangulární
projekci do metrů – nad oblastí ~50 km je chyba zanedbatelná.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# metry na stupeň
_M_PER_DEG_LAT = 110_540.0


def _m_per_deg_lon(lat_deg: float) -> float:
    return 111_320.0 * np.cos(np.radians(lat_deg))


@dataclass
class Grid:
    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float
    nx: int
    ny: int
    lons: np.ndarray  # 1D, vzestupně (západ -> východ), délka nx
    lats: np.ndarray  # 1D, SESTUPNĚ (sever -> jih), délka ny
    dlon: float
    dlat: float  # kladné číslo (velikost kroku)

    # --- konstrukce ---
    @classmethod
    def from_bbox(cls, bbox, resolution_m: float) -> "Grid":
        lon_min, lat_min, lon_max, lat_max = (float(x) for x in bbox)
        lat_c = 0.5 * (lat_min + lat_max)
        dlat = resolution_m / _M_PER_DEG_LAT
        dlon = resolution_m / _m_per_deg_lon(lat_c)

        nx = max(2, int(round((lon_max - lon_min) / dlon)))
        ny = max(2, int(round((lat_max - lat_min) / dlat)))

        # středy buněk
        lons = lon_min + (np.arange(nx) + 0.5) * dlon
        lats = lat_max - (np.arange(ny) + 0.5) * dlat  # sever -> jih
        return cls(lon_min, lat_min, lon_max, lat_max, nx, ny, lons, lats, dlon, dlat)

    # --- pomůcky ---
    @property
    def shape(self) -> tuple[int, int]:
        return (self.ny, self.nx)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.lon_min, self.lat_min, self.lon_max, self.lat_max)

    def meshgrid(self) -> tuple[np.ndarray, np.ndarray]:
        """Vrátí (LON, LAT) o tvaru (ny, nx)."""
        return np.meshgrid(self.lons, self.lats)

    def xy_meters(self) -> tuple[np.ndarray, np.ndarray]:
        """Lokální metrické souřadnice středů buněk (pro vzdálenosti)."""
        lat_c = 0.5 * (self.lat_min + self.lat_max)
        LON, LAT = self.meshgrid()
        x = (LON - self.lon_min) * _m_per_deg_lon(lat_c)
        y = (LAT - self.lat_min) * _M_PER_DEG_LAT
        return x, y

    def lonlat_to_meters(self, lon, lat) -> tuple[np.ndarray, np.ndarray]:
        lat_c = 0.5 * (self.lat_min + self.lat_max)
        x = (np.asarray(lon) - self.lon_min) * _m_per_deg_lon(lat_c)
        y = (np.asarray(lat) - self.lat_min) * _M_PER_DEG_LAT
        return x, y

    def geotransform(self):
        """Affine transformace pro rasterio (north-up)."""
        from rasterio.transform import from_origin

        # origin = levý horní roh (ne střed buňky)
        west = self.lon_min
        north = self.lat_max
        return from_origin(west, north, self.dlon, self.dlat)

    # --- sampling ---
    def sample(self, field: np.ndarray, lon, lat, method: str = "bilinear"):
        """Odečti hodnotu(y) pole v bodě/bodech (lon, lat).

        Body mimo mřížku se ořežou na okraj (edge clamp).
        """
        lon = np.atleast_1d(np.asarray(lon, dtype=float))
        lat = np.atleast_1d(np.asarray(lat, dtype=float))

        # zlomkové indexy: col roste s lon, row roste s klesající lat
        fx = (lon - self.lons[0]) / self.dlon
        fy = (self.lats[0] - lat) / self.dlat

        if method == "nearest":
            ix = np.clip(np.round(fx).astype(int), 0, self.nx - 1)
            iy = np.clip(np.round(fy).astype(int), 0, self.ny - 1)
            out = field[iy, ix]
            return out if out.size > 1 else float(out[0])

        # bilineární
        x0 = np.clip(np.floor(fx).astype(int), 0, self.nx - 1)
        y0 = np.clip(np.floor(fy).astype(int), 0, self.ny - 1)
        x1 = np.clip(x0 + 1, 0, self.nx - 1)
        y1 = np.clip(y0 + 1, 0, self.ny - 1)
        tx = np.clip(fx - x0, 0.0, 1.0)
        ty = np.clip(fy - y0, 0.0, 1.0)

        v00 = field[y0, x0]
        v10 = field[y0, x1]
        v01 = field[y1, x0]
        v11 = field[y1, x1]
        top = v00 * (1 - tx) + v10 * tx
        bot = v01 * (1 - tx) + v11 * tx
        out = top * (1 - ty) + bot * ty
        return out if out.size > 1 else float(out[0])


def reproject_to_grid(
    src_values: np.ndarray,
    src_lons: np.ndarray,
    src_lats: np.ndarray,
    grid: Grid,
) -> np.ndarray:
    """Přemapuj zdrojové pole (definované na 1D lon/lat osách) na pracovní mřížku.

    Používá bilineární interpolaci přes scipy. Zdrojové osy mohou být vzestupné
    i sestupné; interně se srovnají na vzestupné.
    """
    from scipy.interpolate import RegularGridInterpolator

    lons = np.asarray(src_lons, dtype=float)
    lats = np.asarray(src_lats, dtype=float)
    vals = np.asarray(src_values, dtype=float)

    if lats[0] > lats[-1]:  # srovnej na vzestupné pro interpolátor
        lats = lats[::-1]
        vals = vals[::-1, :]
    if lons[0] > lons[-1]:
        lons = lons[::-1]
        vals = vals[:, ::-1]

    interp = RegularGridInterpolator(
        (lats, lons), vals, bounds_error=False, fill_value=None
    )
    LON, LAT = grid.meshgrid()
    pts = np.column_stack([LAT.ravel(), LON.ravel()])
    out = interp(pts).reshape(grid.shape)
    return out
