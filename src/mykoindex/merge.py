"""QC měřáků + slévání srážek (aditivní conditional merging).

Radar/MERGE drží prostorový vzor srážek; bodové měřáky (ČHMÚ jsou už
v MERGE, navíc Netatmo) opraví systematický bias. Reziduum
(měřák − radar v bodě) se rozetře IDW a přičte k radaru.

Referenční implementace pro prototype/: funkce ``qc_gauges``,
``idw`` a ``conditional_merge`` jsou sdílené jádro.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .grid import Grid


@dataclass
class QCResult:
    keep: np.ndarray            # bool maska ponechaných stanic
    reasons: list[str]          # důvod vyřazení pro každou stanici ("" = OK)

    @property
    def n_kept(self) -> int:
        return int(self.keep.sum())

    @property
    def n_dropped(self) -> int:
        return int((~self.keep).sum())


def qc_gauges(
    values: np.ndarray,
    *,
    rain_min_mm: float = 0.0,
    rain_max_mm: float = 250.0,
    mad_k: float = 5.0,
    series: np.ndarray | None = None,
) -> QCResult:
    """Kontrola kvality bodových srážkových měření.

    - fyzikální rozsah: vyhoď ``rain < rain_min`` a ``rain > rain_max``
    - robustní MAD filtr: medián ± ``mad_k`` · 1.4826 · MAD
    - zaseknutá konstanta: pokud je dodána ``series`` (stanice × čas),
      vyřaď stanice s nulovým rozptylem přes čas při nenulové srážce

    Vrací :class:`QCResult` s bool maskou a důvody.
    """
    v = np.asarray(values, dtype=float)
    n = v.shape[0]
    keep = np.ones(n, dtype=bool)
    reasons = [""] * n

    # 1) fyzikální rozsah + NaN
    bad_range = ~np.isfinite(v) | (v < rain_min_mm) | (v > rain_max_mm)
    for i in np.where(bad_range)[0]:
        keep[i] = False
        reasons[i] = "mimo rozsah"

    # 2) MAD filtr (jen na fyzikálně platných)
    ok = keep.copy()
    if ok.sum() >= 4:
        med = np.median(v[ok])
        mad = np.median(np.abs(v[ok] - med))
        scale = 1.4826 * mad
        if scale > 1e-9:
            lo, hi = med - mad_k * scale, med + mad_k * scale
            for i in np.where(ok)[0]:
                if v[i] < lo or v[i] > hi:
                    keep[i] = False
                    reasons[i] = "MAD outlier"

    # 3) zaseknutá konstanta přes čas
    if series is not None:
        s = np.asarray(series, dtype=float)
        for i in range(min(n, s.shape[0])):
            if not keep[i]:
                continue
            row = s[i][np.isfinite(s[i])]
            if row.size >= 5 and np.nanstd(row) < 1e-6 and np.nanmean(row) > 0.5:
                keep[i] = False
                reasons[i] = "zaseknutá konstanta"

    return QCResult(keep=keep, reasons=reasons)


def idw(
    station_x: np.ndarray,
    station_y: np.ndarray,
    station_vals: np.ndarray,
    grid_x: np.ndarray,
    grid_y: np.ndarray,
    *,
    k: int = 8,
    power: float = 2.0,
) -> np.ndarray:
    """Inverse-distance-weighting z bodů na mřížku.

    Souřadnice v metrech (viz :meth:`Grid.xy_meters`). Vrací pole tvaru
    ``grid_x.shape``. Bez stanic vrací nuly.
    """
    from scipy.spatial import cKDTree

    sx = np.asarray(station_x, dtype=float)
    sy = np.asarray(station_y, dtype=float)
    sv = np.asarray(station_vals, dtype=float)
    if sx.size == 0:
        return np.zeros_like(grid_x, dtype=float)

    pts = np.column_stack([sx, sy])
    tree = cKDTree(pts)
    kk = min(k, sx.size)

    q = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    dist, idx = tree.query(q, k=kk)
    if kk == 1:
        dist = dist[:, None]
        idx = idx[:, None]

    eps = 1e-6
    w = 1.0 / np.power(dist + eps, power)
    # bod přesně na stanici -> vezmi tu hodnotu (nekonečná váha ošetřena eps)
    vals = sv[idx]
    out = np.sum(w * vals, axis=1) / np.sum(w, axis=1)
    return out.reshape(grid_x.shape)


def conditional_merge(
    radar_field: np.ndarray,
    grid: Grid,
    station_lons: np.ndarray,
    station_lats: np.ndarray,
    station_vals: np.ndarray,
    *,
    idw_k: int = 8,
    idw_power: float = 2.0,
) -> np.ndarray:
    """Aditivní conditional merging.

    ``resid = gauge − radar_v_bodě``; ``resid_field = IDW(resid)``;
    ``merged = max(0, radar + resid_field)``.

    Bez stanic vrací radar beze změny.
    """
    radar = np.asarray(radar_field, dtype=float)
    slon = np.asarray(station_lons, dtype=float)
    slat = np.asarray(station_lats, dtype=float)
    sval = np.asarray(station_vals, dtype=float)

    if slon.size == 0:
        return np.clip(radar, 0.0, None)

    radar_at = np.asarray(grid.sample(radar, slon, slat)).reshape(-1)
    resid = sval - radar_at

    sx, sy = grid.lonlat_to_meters(slon, slat)
    gx, gy = grid.xy_meters()
    resid_field = idw(sx, sy, resid, gx, gy, k=idw_k, power=idw_power)

    merged = radar + resid_field
    return np.clip(merged, 0.0, None)
