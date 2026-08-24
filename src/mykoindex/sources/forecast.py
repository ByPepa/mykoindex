"""Předpověď počasí přes Open-Meteo (zdarma, bez klíče).

Denní srážky + průměrná teplota pro střed oblasti, horizont ~16 dní.
Slouží k teoretickému odhadu, kdy by mohly hřiby růst (viz analysis.forecast_text).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import requests

log = logging.getLogger(__name__)


@dataclass
class Forecast:
    dates: np.ndarray       # datetime64[D]
    precip_mm: np.ndarray   # denní úhrn
    temp_c: np.ndarray      # denní průměrná teplota


def fetch(cfg) -> Forecast | None:
    """Denní předpověď pro střed bboxu. None při chybě/vypnutí."""
    fc = cfg.raw.get("forecast", {})
    if not fc.get("enabled", True):
        return None
    lon_c = 0.5 * (cfg.lon_min + cfg.lon_max)
    lat_c = 0.5 * (cfg.lat_min + cfg.lat_max)
    url = fc.get("url", "https://api.open-meteo.com/v1/forecast")
    days = int(fc.get("days", 16))
    try:
        r = requests.get(
            url,
            params={
                "latitude": round(lat_c, 3),
                "longitude": round(lon_c, 3),
                "daily": "precipitation_sum,temperature_2m_mean",
                "forecast_days": days,
                "timezone": "Europe/Prague",
            },
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()["daily"]
        return Forecast(
            dates=np.array(d["time"], dtype="datetime64[D]"),
            precip_mm=np.array([x if x is not None else 0.0 for x in d["precipitation_sum"]], dtype=float),
            temp_c=np.array([x if x is not None else np.nan for x in d["temperature_2m_mean"]], dtype=float),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("forecast: Open-Meteo selhalo (%s) → přeskočeno", exc)
        return None


def synthetic(cfg) -> Forecast:
    """Syntetická předpověď pro --demo: déšť za pár dní, mírné teploty."""
    import datetime

    days = int(cfg.raw.get("forecast", {}).get("days", 16))
    start = np.datetime64(datetime.date.today())
    dates = np.array([start + np.timedelta64(i, "D") for i in range(days)], dtype="datetime64[D]")
    precip = np.zeros(days)
    precip[2:5] = [8.0, 14.0, 9.0]   # epizoda za 2–4 dny (~31 mm)
    temp = 14.0 + 2.0 * np.sin(np.arange(days) / 3.0)
    return Forecast(dates=dates, precip_mm=precip, temp_c=temp)
