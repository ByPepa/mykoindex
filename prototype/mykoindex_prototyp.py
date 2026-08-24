#!/usr/bin/env python3
"""Referenční prototyp jádra – QC + IDW + conditional merging + mykoindex.

Samostatný, závislý jen na numpy/scipy. Slouží jako výchozí popis metod,
které jsou v ostrém kódu rozšířené v ``src/mykoindex/`` (merge.py, index.py).
Spuštění:  python prototype/mykoindex_prototyp.py
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree


# --- QC měřáků -------------------------------------------------------------
def qc_gauges(vals, rain_min=0.0, rain_max=250.0, k=5.0):
    """Vrátí bool masku ponechaných stanic (rozsah + robustní MAD)."""
    v = np.asarray(vals, float)
    keep = np.isfinite(v) & (v >= rain_min) & (v <= rain_max)
    if keep.sum() >= 4:
        med = np.median(v[keep])
        mad = np.median(np.abs(v[keep] - med))
        scale = 1.4826 * mad
        if scale > 1e-9:
            keep &= np.abs(v - med) <= k * scale
    return keep


# --- IDW -------------------------------------------------------------------
def idw(sx, sy, sv, gx, gy, k=8, power=2.0):
    """Inverse-distance-weighting z bodů (metry) na mřížku."""
    if len(sx) == 0:
        return np.zeros_like(gx, float)
    tree = cKDTree(np.column_stack([sx, sy]))
    kk = min(k, len(sx))
    dist, idx = tree.query(np.column_stack([gx.ravel(), gy.ravel()]), k=kk)
    if kk == 1:
        dist, idx = dist[:, None], idx[:, None]
    w = 1.0 / np.power(dist + 1e-6, power)
    out = np.sum(w * sv[idx], axis=1) / np.sum(w, axis=1)
    return out.reshape(gx.shape)


# --- Conditional merging ---------------------------------------------------
def conditional_merge(radar, gx, gy, sx, sy, sval, radar_at_stations, k=8, power=2.0):
    """merged = max(0, radar + IDW(gauge - radar_v_bodě))."""
    if len(sx) == 0:
        return np.clip(radar, 0, None)
    resid = sval - radar_at_stations
    resid_field = idw(sx, sy, resid, gx, gy, k=k, power=power)
    return np.clip(radar + resid_field, 0, None)


def _demo():
    # umělá mřížka 60x60 v metrech
    n = 60
    gx, gy = np.meshgrid(np.linspace(0, 60000, n), np.linspace(0, 60000, n))
    truth = 20 + 8 * np.sin(gx / 8000) * np.cos(gy / 8000)
    radar = truth - 6.0  # radar podhodnocuje

    rng = np.random.default_rng(0)
    si = rng.integers(0, n, 30)
    sj = rng.integers(0, n, 30)
    sx, sy = gx[si, sj], gy[si, sj]
    sval = truth[si, sj].copy()
    sval[5] = 400.0  # vadná stanice

    keep = qc_gauges(sval)
    sx, sy, sval = sx[keep], sy[keep], sval[keep]
    radar_at = radar[si, sj][keep]

    merged = conditional_merge(radar, gx, gy, sx, sy, sval, radar_at)
    print(f"QC ponecháno {keep.sum()}/{keep.size} stanic")
    print(f"MAE radar  vs pravda: {np.abs(radar - truth).mean():.2f} mm")
    print(f"MAE merged vs pravda: {np.abs(merged - truth).mean():.2f} mm")


if __name__ == "__main__":
    _demo()
