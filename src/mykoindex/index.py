"""Vlhkostní vrstva (API30), teplotní skóre a výsledný mykoindex."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def api30(
    daily_merged: np.ndarray,
    *,
    tau_days: float = 12.0,
    window_days: int = 30,
) -> np.ndarray:
    """Antecedent precipitation index s exponenciálně klesající vahou.

    ``daily_merged`` má tvar (D, ny, nx), kde index 0 = NEJNOVĚJŠÍ den
    (dnešek), rostoucí index = starší dny. Vrací pole (ny, nx).

        API30 = Σ_{d=0..D-1} merged[d] · exp(−d / tau)
    """
    arr = np.asarray(daily_merged, dtype=float)
    if arr.ndim != 3:
        raise ValueError("daily_merged musí mít tvar (D, ny, nx)")
    d = min(window_days, arr.shape[0])
    days = np.arange(d)
    weights = np.exp(-days / float(tau_days))
    stack = np.nan_to_num(arr[:d], nan=0.0)
    return np.tensordot(weights, stack, axes=(0, 0))


def moisture_score(api_field: np.ndarray, *, saturation_mm: float = 60.0) -> np.ndarray:
    """Normalizace vážené srážkové sumy na 0–1."""
    return np.clip(np.asarray(api_field, dtype=float) / float(saturation_mm), 0.0, 1.0)


def local_temperature(
    t_mean: np.ndarray | float,
    elev_m: np.ndarray | float,
    *,
    lapse_c_per_100m: float = 0.6,
    elev_ref_m: float = 300.0,
) -> np.ndarray:
    """Regionální teplota přepočtená na lokální podle výšky.

        T_local = T_mean − lapse · (elev − elev_ref) / 100
    """
    t = np.asarray(t_mean, dtype=float)
    e = np.asarray(elev_m, dtype=float)
    return t - lapse_c_per_100m * (e - elev_ref_m) / 100.0


def temperature_score(
    t_local: np.ndarray | float,
    *,
    t_min: float = 5.0,
    t_opt: float = 14.0,
    t_max: float = 25.0,
) -> np.ndarray:
    """Trojúhelníkové skóre 0–1 s optimem v ``t_opt``.

        temp = 0                         mimo [t_min, t_max]
        temp = (T − t_min)/(t_opt−t_min) pro T ≤ t_opt
        temp = (t_max − T)/(t_max−t_opt) jinak
    """
    t = np.asarray(t_local, dtype=float)
    rising = (t - t_min) / (t_opt - t_min)
    falling = (t_max - t) / (t_max - t_opt)
    score = np.where(t <= t_opt, rising, falling)
    score = np.where((t < t_min) | (t > t_max), 0.0, score)
    return np.clip(score, 0.0, 1.0)


def aspect_factor(
    northness: np.ndarray | float,
    *,
    base: float = 0.82,
    span: float = 0.18,
) -> np.ndarray:
    """Sever/východ drží vláhu → mírná výhoda. ``northness`` ∈ [−1, 1]."""
    n = np.clip(np.asarray(northness, dtype=float), -1.0, 1.0)
    return base + span * n


def forest_factor(
    forest01: np.ndarray | float,
    *,
    base: float = 0.35,
    span: float = 0.65,
    enabled: bool = True,
) -> np.ndarray:
    """Les jako předpoklad růstu. Když je modul vypnutý, vrací 1."""
    if not enabled:
        return np.ones_like(np.asarray(forest01, dtype=float))
    f = np.clip(np.asarray(forest01, dtype=float), 0.0, 1.0)
    return base + span * f


@dataclass
class IndexInputs:
    moisture: np.ndarray
    temp: np.ndarray
    aspect: np.ndarray
    forest: np.ndarray


def mykoindex(
    inp: IndexInputs,
    *,
    w_moist: float = 0.55,
    w_temp: float = 0.45,
) -> np.ndarray:
    """Výsledný index 0–100.

        index = (moisture·W_MOIST + temp·W_TEMP) · aspect · forest · 100
    """
    core = inp.moisture * w_moist + inp.temp * w_temp
    idx = core * inp.aspect * inp.forest * 100.0
    return np.clip(idx, 0.0, 100.0)


def verdict(score: float, verdicts: list[dict]) -> str:
    """Slovní verdikt podle prahů (sestupně dle ``min``)."""
    for rule in sorted(verdicts, key=lambda r: -r["min"]):
        if score >= rule["min"]:
            return rule["label"]
    return verdicts[-1]["label"] if verdicts else ""
