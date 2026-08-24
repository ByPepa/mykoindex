"""Netatmo: hustší srážková korekce z veřejných amatérských stanic.

Auth výhradně OAuth2 authorization_code: refresh_token → access_token
(scope read_station). Data jsou jen živá (24 h), proto korigujeme jen
poslední 1–2 dny (viz merge.netatmo_days).

Velký bbox se dělí na dlaždice ~0.15°, jinak API vrací 503.
Kvalita stanic je proměnlivá → výstup vždy prochází QC v merge.py.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np
import requests

log = logging.getLogger(__name__)


@dataclass
class Stations:
    lons: np.ndarray
    lats: np.ndarray
    rain: np.ndarray   # mm za zvolené okno (rain_24h)

    def __len__(self) -> int:
        return int(self.lons.size)


def refresh_access_token(cfg) -> str | None:
    """Vymění refresh_token za access_token. None při chybějících údajích/chybě."""
    cid = cfg.env.get("NETATMO_CLIENT_ID")
    secret = cfg.env.get("NETATMO_CLIENT_SECRET")
    rtoken = cfg.env.get("NETATMO_REFRESH_TOKEN")
    if not (cid and secret and rtoken):
        log.info("netatmo: chybí CLIENT_ID/SECRET/REFRESH_TOKEN → přeskočeno")
        return None
    token_url = cfg.sources.get("netatmo", {}).get(
        "token_url", "https://api.netatmo.com/oauth2/token"
    )
    try:
        resp = requests.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": rtoken,
                "client_id": cid,
                "client_secret": secret,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as exc:  # noqa: BLE001
        log.warning("netatmo: obnova tokenu selhala (%s)", exc)
        return None


def _tiles(bbox, tile_deg: float):
    lon_min, lat_min, lon_max, lat_max = bbox
    lon = lon_min
    while lon < lon_max:
        lat = lat_min
        while lat < lat_max:
            yield (lon, lat, min(lon + tile_deg, lon_max), min(lat + tile_deg, lat_max))
            lat += tile_deg
        lon += tile_deg


def _parse_body(body: list) -> list[tuple[float, float, float]]:
    out = []
    for st in body or []:
        loc = (st.get("place") or {}).get("location")
        if not loc or len(loc) != 2:
            continue
        lon, lat = float(loc[0]), float(loc[1])
        measures = st.get("measures") or {}
        rain = None
        for mod in measures.values():
            res = mod.get("res")
            rt = mod.get("type")
            if isinstance(rt, list) and "rain_24h" in rt and isinstance(res, dict):
                # res: {timestamp: [values...]} v pořadí type
                vals = list(res.values())[-1]
                rain = float(vals[rt.index("rain_24h")])
            elif "rain_24h" in mod:  # zjednodušený tvar
                rain = float(mod["rain_24h"])
        if rain is not None:
            out.append((lon, lat, rain))
    return out


def fetch(cfg, bbox=None) -> Stations | None:
    """Stáhni veřejné srážkové stanice v bboxu. None při chybě/bez tokenu."""
    bbox = bbox or cfg.bbox
    token = refresh_access_token(cfg)
    if not token:
        return None
    api_url = cfg.sources.get("netatmo", {}).get(
        "api_url", "https://api.netatmo.com/api/getpublicdata"
    )
    tile_deg = float(cfg.sources.get("netatmo", {}).get("tile_deg", 0.15))
    max_tiles = int(cfg.sources.get("netatmo", {}).get("max_tiles", 200))
    headers = {"Authorization": f"Bearer {token}"}
    rows: list[tuple[float, float, float]] = []
    tiles = list(_tiles(bbox, tile_deg))
    if len(tiles) > max_tiles:
        log.warning("netatmo: %d dlaždic > max_tiles=%d → omezuji", len(tiles), max_tiles)
        tiles = tiles[:max_tiles]
    try:
        for (lo, la, lo2, la2) in tiles:
            params = {
                "lat_ne": la2,
                "lon_ne": lo2,
                "lat_sw": la,
                "lon_sw": lo,
                "required_data": "rain",
                "filter": "true",
            }
            for attempt in range(3):
                r = requests.get(api_url, params=params, headers=headers, timeout=30)
                if r.status_code == 503:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                rows.extend(_parse_body(r.json().get("body", [])))
                break
            time.sleep(0.3)  # šetrné k API
    except Exception as exc:  # noqa: BLE001
        log.warning("netatmo: stažení stanic selhalo (%s) → co mám, to použiju", exc)

    if not rows:
        return None
    arr = np.array(rows, dtype=float)
    return Stations(lons=arr[:, 0], lats=arr[:, 1], rain=arr[:, 2])


def synthetic(cfg, truth_field=None, grid=None, n: int = 60, seed: int = 11) -> Stations:
    """Náhodné stanice v bboxu. Když je dáno pravdivé pole, čtou z něj (+šum)."""
    rng = np.random.default_rng(seed)
    lon_min, lat_min, lon_max, lat_max = cfg.bbox
    lons = rng.uniform(lon_min, lon_max, n)
    lats = rng.uniform(lat_min, lat_max, n)
    if truth_field is not None and grid is not None:
        rain = np.asarray(grid.sample(truth_field, lons, lats)).reshape(-1)
        rain = np.clip(rain + rng.normal(0, 2.0, n), 0, None)
    else:
        rain = np.clip(rng.gamma(2.0, 3.0, n), 0, None)
    return Stations(lons=lons, lats=lats, rain=rain)
