"""Testy pracovní mřížky: sampling a reprojekce."""
import numpy as np

from mykoindex.grid import Grid, reproject_to_grid


def _grid():
    return Grid.from_bbox([17.5, 49.15, 18.0, 49.62], resolution_m=1000)


def test_grid_shape_and_orientation():
    g = _grid()
    assert g.shape == (g.ny, g.nx)
    assert g.lats[0] > g.lats[-1]   # sever -> jih
    assert g.lons[0] < g.lons[-1]   # západ -> východ


def test_sample_constant_field():
    g = _grid()
    field = np.full(g.shape, 7.0)
    assert np.isclose(g.sample(field, 17.75, 49.4), 7.0)


def test_sample_linear_gradient():
    g = _grid()
    LON, _ = g.meshgrid()
    # pole = lon → sampling v bodě vrátí ~lon
    val = g.sample(LON, 17.8, 49.4)
    assert abs(val - 17.8) < 0.02


def test_sample_clamps_outside():
    g = _grid()
    field = np.arange(g.ny * g.nx, dtype=float).reshape(g.shape)
    # bod daleko mimo bbox → nespadne, ořízne na okraj
    v = g.sample(field, 10.0, 40.0)
    assert np.isfinite(v)


def test_reproject_preserves_constant():
    g = _grid()
    src_lons = np.linspace(17.4, 18.1, 20)
    src_lats = np.linspace(49.7, 49.1, 18)
    src = np.full((18, 20), 3.14)
    out = reproject_to_grid(src, src_lons, src_lats, g)
    assert out.shape == g.shape
    assert np.allclose(out, 3.14, atol=1e-6)
