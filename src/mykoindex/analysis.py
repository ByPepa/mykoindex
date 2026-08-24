"""Odvozené výstupy: TOP ideální místa, popis počasí, odhad „kdy porostou"."""
from __future__ import annotations

import numpy as np

_MONTHS = ["", "ledna", "února", "března", "dubna", "května", "června",
          "července", "srpna", "září", "října", "listopadu", "prosince"]


def _cz_date(d: np.datetime64) -> str:
    dt = d.astype("datetime64[D]").astype(object)
    return f"{dt.day}. {dt.month}."


def _km(grid, lon1, lat1, lon2, lat2) -> float:
    x1, y1 = grid.lonlat_to_meters(lon1, lat1)
    x2, y2 = grid.lonlat_to_meters(lon2, lat2)
    return float(np.hypot(float(x1) - float(x2), float(y1) - float(y2)) / 1000.0)


def find_hotspots(index_field, forest01, grid, cfg) -> list[dict]:
    """TOP ideální lesní buňky (lokální maxima indexu) s minimálním rozestupem."""
    h = cfg.raw.get("hotspots", {})
    if not h.get("enabled", True):
        return []
    n = int(h.get("n", 12))
    min_index = float(h.get("min_index", 55))
    min_forest = float(h.get("min_forest", 0.5))
    min_dist = float(h.get("min_dist_km", 6))

    mask = (forest01 >= min_forest) & (index_field >= min_index) & np.isfinite(index_field)
    rows = np.argwhere(mask)
    if rows.size == 0:
        return []
    vals = index_field[mask]
    order = np.argsort(-vals)

    selected: list[dict] = []
    for oi in order:
        r, c = rows[oi]
        lon = float(grid.lons[c]); lat = float(grid.lats[r])
        if all(_km(grid, lon, lat, s["lon"], s["lat"]) >= min_dist for s in selected):
            selected.append({"lon": round(lon, 4), "lat": round(lat, 4),
                             "score": round(float(index_field[r, c]), 1)})
        if len(selected) >= n:
            break
    return selected


def build_series(cfg, grid, daily_stack, index_field, t_local, forecast, ncx: int = 18) -> dict:
    """Hrubá mřížka časových řad pro klik na mapě: 30denní srážky + index + teplota
    + předpověď + parametry. Umožní frontendu spočítat vlhkost a odhad pro libovolný bod.
    """
    lon_min, lat_min, lon_max, lat_max = grid.bbox
    step = (lon_max - lon_min) / ncx
    ncy = max(2, int(round((lat_max - lat_min) / step)))
    lons = lon_min + (np.arange(ncx) + 0.5) * step
    lats = lat_max - (np.arange(ncy) + 0.5) * ((lat_max - lat_min) / ncy)
    LON, LAT = np.meshgrid(lons, lats)
    flon, flat = LON.ravel(), LAT.ravel()

    idxv = np.atleast_1d(np.asarray(grid.sample(index_field, flon, flat))).reshape(-1)
    tempv = np.atleast_1d(np.asarray(grid.sample(t_local, flon, flat))).reshape(-1)
    D = int(daily_stack.shape[0])
    precip = np.zeros((flon.size, D))
    for d in range(D):
        precip[:, d] = np.atleast_1d(np.asarray(grid.sample(daily_stack[d], flon, flat))).reshape(-1)

    cells = [
        {"lon": round(float(flon[i]), 4), "lat": round(float(flat[i]), 4),
         "index": round(float(idxv[i]), 1), "temp": round(float(tempv[i]), 1),
         "precip": [round(float(x), 1) for x in precip[i]]}
        for i in range(flon.size)
    ]

    fcast = None
    if forecast is not None:
        fcast = {
            "dates": [str(x) for x in forecast.dates],
            "precip": [round(float(x), 1) for x in forecast.precip_mm],
            "temp": [None if not np.isfinite(x) else round(float(x), 1) for x in forecast.temp_c],
        }

    m = cfg.model
    fc = cfg.raw.get("forecast", {})
    params = {
        "tau_days": float(m.get("tau_days", 12)),
        "saturation_mm": float(m.get("api_saturation_mm", 60)),
        "w_moist": float(m.get("w_moist", 0.55)), "w_temp": float(m.get("w_temp", 0.45)),
        "t_min": float(m.get("t_min", 5)), "t_opt": float(m.get("t_opt", 14)), "t_max": float(m.get("t_max", 25)),
        "soak_mm": float(fc.get("soak_mm", 20)), "lag": fc.get("fruiting_lag_days", [7, 12]),
        "ideal_min": float(cfg.raw.get("history", {}).get("ideal_min_score", 60)),
    }
    return {"ncx": ncx, "ncy": ncy, "window_days": D, "cells": cells,
            "forecast": fcast, "params": params}


def weather_summary(cfg, daily_stack, moisture_mean: float, temp_mean_c: float) -> str:
    """Český popis vývoje počasí za posledních N dní z denních srážkových polí."""
    n = int(cfg.raw.get("weather_summary", {}).get("days", 20))
    d = min(n, daily_stack.shape[0])
    daily = np.nanmean(daily_stack[:d], axis=(1, 2))  # index0 = dnešek
    total = float(daily.sum())

    parts = [f"Za posledních {d} dní spadlo průměrně {total:.0f} mm."]

    # největší srážkový den
    imax = int(np.argmax(daily))
    if daily[imax] >= 3:
        ago = "dnes" if imax == 0 else ("včera" if imax == 1 else f"před {imax} dny")
        parts.append(f"Nejvíc pršelo {ago} (~{daily[imax]:.0f} mm).")
    else:
        parts.append("Výraznější déšť žádný.")

    # sucho = úvodní dny s <1 mm
    dry = 0
    for v in daily:
        if v < 1.0:
            dry += 1
        else:
            break
    if dry >= 3:
        parts.append(f"Poslední {dry} dny je sucho.")
    elif dry == 0:
        parts.append("Prší i v posledních dnech.")

    # trend: posledních 5 dní vs. předchozích 5
    if d >= 10:
        last5, prev5 = daily[:5].sum(), daily[5:10].sum()
        if last5 > prev5 * 1.3:
            parts.append("Srážky přibývají.")
        elif last5 < prev5 * 0.7:
            parts.append("Srážky ubývají.")

    moist = "vysoká" if moisture_mean > 0.7 else "střední" if moisture_mean > 0.4 else "nízká"
    parts.append(f"Půdní vlhkost {moist}, teplota kolem {temp_mean_c:.0f} °C.")
    return " ".join(parts)


def forecast_text(cfg, forecast, api_now_mean: float, temp_now_mean: float) -> str:
    """Teoretický odhad, kdy by mohly hřiby růst, z předpovědi Open-Meteo."""
    if forecast is None:
        return ""
    fc = cfg.raw.get("forecast", {})
    soak_mm = float(fc.get("soak_mm", 20))
    lag = fc.get("fruiting_lag_days", [7, 12])
    m = cfg.model
    t_min = float(m.get("t_min", 5)); t_max = float(m.get("t_max", 25))
    sat = float(m.get("api_saturation_mm", 60))

    p = forecast.precip_mm
    t = forecast.temp_c
    dates = forecast.dates
    total = float(np.nansum(p))

    # klouzavý 3denní úhrn → najdi první „vydatný déšť"
    roll = np.convolve(p, np.ones(3), mode="same")
    soak_idx = None
    for i in range(len(p)):
        if roll[i] >= soak_mm and (not np.isfinite(t[i]) or t_min <= t[i] <= t_max):
            soak_idx = i
            break

    if soak_idx is not None:
        lo = max(0, soak_idx - 1); hi = min(len(p), soak_idx + 2)
        event_mm = float(np.nansum(p[lo:hi]))
        tt = np.nanmean(t[lo:hi])
        f_start = dates[soak_idx] + np.timedelta64(int(lag[0]), "D")
        f_end = dates[soak_idx] + np.timedelta64(int(lag[1]), "D")
        return (
            f"Předpověď (Open-Meteo): kolem {_cz_date(dates[soak_idx])} přijde "
            f"~{event_mm:.0f} mm při ~{tt:.0f} °C. Při vhodných teplotách by hřiby "
            f"mohly naskočit zhruba {_cz_date(f_start)}–{_cz_date(f_end)}"
        )

    # bez soaku: podle aktuální vlhkosti a teploty
    moist_now = min(1.0, api_now_mean / sat)
    if moist_now > 0.6 and t_min <= temp_now_mean <= t_max:
        return ("Předpověď (Open-Meteo): bez vydatného deště, ale půda je slušně "
                "nasycená a teploty sedí – šance je už teď a v nejbližších dnech.")
    if total < 5:
        return (f"Předpověď (Open-Meteo): sucho (~{total:.0f} mm za {len(p)} dní) – "
                "bez výraznějšího deště se fruktifikace nerozjede.")
    return (f"Předpověď (Open-Meteo): jen menší srážky (~{total:.0f} mm), nic "
            "vydatného – spíš vyčkávat.")
