"""Kalibrace prahů a vah z GBIF nálezů (presence-background).

Nálezy = pozitiva, náhodné body v čase/prostoru = pozadí. Ke každému bodu
se dopočítají modelové podmínky (vlhkost/teplota) a logistickou regresí se
odhadnou relativní váhy w_moist/w_temp; prahy verdiktů se odvodí z rozdělení
indexu v místech reálné fruktifikace.

Vychýlení nálezů (víkendy, parkoviště, rozmazané souřadnice) → spoléháme
hlavně na ČASOVOU informaci (kdy), méně na přesné kde.

``condition_fn(lon, lat, date) -> (moisture, temp)`` je zapojovací bod pro
reálné historické podmínky. Bez něj se použije reprodukovatelná sezónní
klimatologie, takže skript vždy vrátí doporučené parametry (offline).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Calibration:
    w_moist: float
    w_temp: float
    verdict_thresholds: dict  # {"Vyraž":.., "Dá se":.., "Počkej":..}
    n_presence: int
    n_background: int
    auc: float
    notes: list[str] = field(default_factory=list)


def _seasonal_climatology(lon, lat, dates, seed=0):
    """Reprodukovatelná náhrada reálných podmínek: sezónní vlhkost/teplota.

    Vlhkost kulminuje na přelomu léta/podzimu (fruktifikace hřibů),
    teplota má roční chod. Slouží k demonstraci kalibračního postupu.
    """
    rng = np.random.default_rng(seed)
    doy = np.array([(np.datetime64(d) - np.datetime64(str(d)[:4] + "-01-01")).astype(int) for d in dates])
    # vlhkost: špička kolem dne 255 (polovina září)
    moisture = np.exp(-((doy - 255) ** 2) / (2 * 40**2))
    moisture = np.clip(moisture + rng.normal(0, 0.12, doy.shape), 0, 1)
    # teplota: roční chod, optimum modelu ~14 °C na jaře/podzim
    temp_c = 10 + 12 * np.sin((doy - 110) / 365 * 2 * np.pi)
    temp = np.clip(1 - np.abs(temp_c - 14) / 12, 0, 1)
    temp = np.clip(temp + rng.normal(0, 0.1, doy.shape), 0, 1)
    return moisture, temp


def _fit_logistic(X, y, iters=4000, lr=0.2, l2=1e-3):
    """Prostá logistická regrese (numpy) → koeficienty [bias, w1, w2]."""
    n, k = X.shape
    Xb = np.column_stack([np.ones(n), X])
    w = np.zeros(k + 1)
    for _ in range(iters):
        z = Xb @ w
        p = 1.0 / (1.0 + np.exp(-z))
        grad = Xb.T @ (p - y) / n + l2 * np.r_[0, w[1:]]
        w -= lr * grad
    return w


def _auc(scores, y):
    order = np.argsort(scores)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    n_pos = y.sum()
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    return (ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def calibrate(cfg, occurrences, *, condition_fn=None, background_mult=3, seed=0) -> Calibration:
    """Z nálezů odvoď doporučené w_moist/w_temp a prahy verdiktů."""
    rng = np.random.default_rng(seed)
    notes: list[str] = []

    pres_lon, pres_lat, pres_date = occurrences.lons, occurrences.lats, occurrences.dates
    n_p = len(pres_lon)

    # pozadí: náhodné body v čase (celá sezóna) i prostoru bboxu
    n_b = n_p * background_mult
    lon_min, lat_min, lon_max, lat_max = cfg.bbox
    bg_lon = rng.uniform(lon_min, lon_max, n_b)
    bg_lat = rng.uniform(lat_min, lat_max, n_b)
    yrs = rng.integers(cfg.sources.get("gbif", {}).get("year_from", 2015),
                       cfg.sources.get("gbif", {}).get("year_to", 2025) + 1, n_b)
    doy = rng.integers(120, 330, n_b)
    bg_date = np.array(
        [np.datetime64(f"{y}-01-01") + np.timedelta64(int(d) - 1, "D") for y, d in zip(yrs, doy)],
        dtype="datetime64[D]",
    )

    cond = condition_fn or (lambda lo, la, dt: _seasonal_climatology(lo, la, dt, seed))
    if condition_fn is None:
        notes.append("Použita sezónní klimatologie (offline); zapoj condition_fn pro reálné podmínky.")

    m_p, t_p = cond(pres_lon, pres_lat, pres_date)
    m_b, t_b = cond(bg_lon, bg_lat, bg_date)

    X = np.vstack([np.column_stack([m_p, t_p]), np.column_stack([m_b, t_b])])
    y = np.r_[np.ones(n_p), np.zeros(n_b)]

    w = _fit_logistic(X, y)
    coef = np.clip(w[1:], 0, None)  # jen nezáporné příspěvky do vah
    if coef.sum() < 1e-6:
        w_moist, w_temp = 0.55, 0.45
        notes.append("Koeficienty degenerované → ponechány výchozí váhy.")
    else:
        w_moist, w_temp = (coef / coef.sum()).tolist()

    # AUC kvality rozlišení
    scores = X @ w[1:]
    auc = float(_auc(scores, y))

    # prahy verdiktů z percentilů indexu v místech nálezů
    index_p = 100.0 * (m_p * w_moist + t_p * w_temp)
    thresholds = {
        "Vyraž": round(float(np.percentile(index_p, 60)), 1),
        "Dá se": round(float(np.percentile(index_p, 35)), 1),
        "Počkej": round(float(np.percentile(index_p, 15)), 1),
    }

    return Calibration(
        w_moist=round(float(w_moist), 3),
        w_temp=round(float(w_temp), 3),
        verdict_thresholds=thresholds,
        n_presence=n_p,
        n_background=n_b,
        auc=round(auc, 3),
        notes=notes,
    )
