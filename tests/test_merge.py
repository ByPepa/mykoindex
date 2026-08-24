"""Testy QC a slévání (akceptační kritérium 3)."""
import numpy as np
import pytest

from mykoindex.grid import Grid
from mykoindex.merge import conditional_merge, idw, qc_gauges


@pytest.fixture
def grid():
    return Grid.from_bbox([17.5, 49.15, 18.0, 49.62], resolution_m=1000)


def test_qc_drops_range_outlier():
    vals = np.array([5.0, 6.0, 4.0, 300.0, 5.5, -1.0])
    res = qc_gauges(vals, rain_min_mm=0, rain_max_mm=250, mad_k=5)
    assert not res.keep[3]  # 300 > max
    assert not res.keep[5]  # -1 < min
    assert res.keep[0] and res.keep[1]


def test_qc_drops_mad_outlier():
    # těsný shluk + jeden extrém uvnitř fyzikálního rozsahu
    vals = np.array([10, 11, 9, 10.5, 9.5, 10.2, 90.0])
    res = qc_gauges(vals, rain_min_mm=0, rain_max_mm=250, mad_k=5)
    assert not res.keep[-1]  # 90 je MAD outlier
    assert res.keep[:-1].all()


def test_qc_stuck_constant():
    vals = np.array([5.0, 6.0, 5.5, 5.2])
    series = np.array([
        [5, 6, 4, 5, 7],       # normální variabilita
        [6, 5, 7, 6, 6],
        [3, 3, 3, 3, 3],       # zaseknutá konstanta (nenulová)
        [5, 5.2, 5.1, 5.3, 5],
    ])
    res = qc_gauges(vals, series=series)
    assert not res.keep[2]


def test_idw_reproduces_station_value(grid):
    gx, gy = grid.xy_meters()
    sx, sy = grid.lonlat_to_meters(np.array([17.75]), np.array([49.4]))
    field = idw(sx, sy, np.array([42.0]), gx, gy, k=1)
    # jediná stanice → konstantní pole její hodnoty
    assert np.allclose(field, 42.0, atol=1e-6)


def test_conditional_merge_reduces_bias(grid):
    # radar systematicky podhodnocuje o 6 mm
    LON, LAT = grid.meshgrid()
    truth = 20 + 5 * np.sin(LON * 20) * np.cos(LAT * 20)
    radar = truth - 6.0

    rng = np.random.default_rng(0)
    slon = rng.uniform(17.55, 17.95, 25)
    slat = rng.uniform(49.2, 49.55, 25)
    gauge = np.asarray(grid.sample(truth, slon, slat)).reshape(-1)

    merged = conditional_merge(radar, grid, slon, slat, gauge, idw_k=8, idw_power=2)

    # chyba proti pravdě v místech stanic musí klesnout
    err_radar = np.abs(np.asarray(grid.sample(radar, slon, slat)).reshape(-1) - gauge)
    err_merged = np.abs(np.asarray(grid.sample(merged, slon, slat)).reshape(-1) - gauge)
    assert err_merged.mean() < 0.4 * err_radar.mean()


def test_conditional_merge_ignores_qc_dropped_outlier(grid):
    """Stanice-outlier po QC nesmí protrhnout slité pole."""
    LON, LAT = grid.meshgrid()
    truth = np.full(grid.shape, 15.0)
    radar = truth.copy()

    rng = np.random.default_rng(1)
    slon = rng.uniform(17.55, 17.95, 20)
    slat = rng.uniform(49.2, 49.55, 20)
    gauge = np.full(20, 15.0)
    gauge[7] = 500.0  # vadná stanice

    qc = qc_gauges(gauge, rain_min_mm=0, rain_max_mm=250, mad_k=5)
    keep = qc.keep
    assert not keep[7]

    merged = conditional_merge(radar, grid, slon[keep], slat[keep], gauge[keep])
    # bez vadné stanice zůstane pole u 15
    assert np.allclose(merged, 15.0, atol=0.5)


def test_conditional_merge_no_stations_returns_radar(grid):
    radar = np.full(grid.shape, 3.0)
    out = conditional_merge(radar, grid, np.array([]), np.array([]), np.array([]))
    assert np.allclose(out, radar)


def test_merge_never_negative(grid):
    radar = np.full(grid.shape, 1.0)
    slon = np.array([17.75]); slat = np.array([49.4])
    gauge = np.array([0.0])  # měřák 0, radar 1 → reziduum -1
    out = conditional_merge(radar, grid, slon, slat, gauge)
    assert (out >= 0).all()
