"""Testy vlhkostní/teplotní vrstvy a výsledného indexu."""
import numpy as np

from mykoindex.index import (
    IndexInputs,
    api30,
    aspect_factor,
    forest_factor,
    local_temperature,
    moisture_score,
    mykoindex,
    temperature_score,
    verdict,
)


def test_api30_weights_recent_more():
    # dva dny, 10 mm dnes vs 10 mm před 20 dny
    stack = np.zeros((30, 1, 1))
    stack[0, 0, 0] = 10.0
    a_today = api30(stack, tau_days=12)[0, 0]
    stack2 = np.zeros((30, 1, 1))
    stack2[20, 0, 0] = 10.0
    a_old = api30(stack2, tau_days=12)[0, 0]
    assert a_today > a_old
    assert np.isclose(a_today, 10.0)  # d=0 → váha exp(0)=1
    assert np.isclose(a_old, 10.0 * np.exp(-20 / 12))


def test_moisture_saturates():
    assert moisture_score(np.array([120.0]), saturation_mm=60)[0] == 1.0
    assert moisture_score(np.array([30.0]), saturation_mm=60)[0] == 0.5


def test_temperature_curve():
    assert temperature_score(4.0) == 0.0     # pod minimem
    assert temperature_score(26.0) == 0.0    # nad maximem
    assert np.isclose(temperature_score(14.0), 1.0)  # optimum
    assert 0 < temperature_score(9.5) < 1


def test_local_temperature_lapse():
    # 100 m výš = o 0.6 °C chladněji
    t0 = local_temperature(15.0, 300.0)
    t1 = local_temperature(15.0, 400.0)
    assert np.isclose(t0 - t1, 0.6)


def test_aspect_and_forest_factor_ranges():
    assert np.isclose(aspect_factor(1.0), 1.0)
    assert np.isclose(aspect_factor(-1.0), 0.64)
    assert np.isclose(forest_factor(1.0), 1.0)
    assert np.isclose(forest_factor(0.0), 0.35)
    assert np.allclose(forest_factor(0.0, enabled=False), 1.0)


def test_mykoindex_bounds_and_formula():
    inp = IndexInputs(
        moisture=np.array([1.0]), temp=np.array([1.0]),
        aspect=np.array([1.0]), forest=np.array([1.0]),
    )
    assert np.isclose(mykoindex(inp, w_moist=0.55, w_temp=0.45)[0], 100.0)
    dry = IndexInputs(np.array([0.0]), np.array([0.0]), np.array([1.0]), np.array([1.0]))
    assert mykoindex(dry)[0] == 0.0


def test_verdict_thresholds():
    v = [{"min": 70, "label": "Vyraž"}, {"min": 50, "label": "Dá se"},
         {"min": 32, "label": "Počkej"}, {"min": 0, "label": "Sucho"}]
    assert verdict(85, v) == "Vyraž"
    assert verdict(55, v) == "Dá se"
    assert verdict(40, v) == "Počkej"
    assert verdict(10, v) == "Sucho"
