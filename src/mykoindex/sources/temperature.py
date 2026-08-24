"""Teplota z pozemních stanic: ČHMÚ (profesionální) + Netatmo (husté amatérské).

Náhrada za ERA5-Land bez nutnosti CDS klíče a v (skoro) reálném čase.
ČHMÚ dává přesné, ale řídké body; Netatmo husté, ale méně kvalitní (QC nutné).

Každá stanice se přepočte na REFERENČNÍ výšku (elev_ref, default 300 m) pomocí
výškového gradientu:  T_ref = T_stanice + lapse·(elev_stanice − elev_ref)/100.
Reference se rozprostře IDW do gridu; index.local_temperature pak přidá zpět
gradient podle DEM → lokální teplota v každé buňce.

ČHMÚ „now" 10min data:  climate/now/data/10m-{WSI}-{YYYYMMDD}.json  (element T, °C)
Meta (souřadnice+výška): climate/now/metadata/meta1-{YYYYMMDD}.json
Netatmo:                 getpublicdata required_data=temperature (place.altitude).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import requests

from ..grid import Grid
from ..merge import idw, qc_gauges

log = logging.getLogger(__name__)

_CHMI_NOW = "https://opendata.chmi.cz/meteorology/climate/now"


@dataclass
class TempStations:
    lon: np.ndarray
    lat: np.ndarray
    elev: np.ndarray
    temp: np.ndarray  # °C, průměr za okno

    def __len__(self) -> int:
        return int(self.lon.size)

    @staticmethod
    def concat(items):
        items = [s for s in items if s is not None and len(s)]
        if not items:
            return None
        return TempStations(
            lon=np.concatenate([s.lon for s in items]),
            lat=np.concatenate([s.lat for s in items]),
            elev=np.concatenate([s.elev for s in items]),
            temp=np.concatenate([s.temp for s in items]),
        )


# ------------------------------------------------------------------ ČHMÚ ---
def _chmi_station_meta(cfg, day: date):
    """Stáhni meta1 (WSI, lon, lat, elev) a vrať dict WSI -> (lon,lat,elev)."""
    for d in (day, day - timedelta(days=1)):
        url = f"{_CHMI_NOW}/metadata/meta1-{d:%Y%m%d}.json"
        try:
            r = requests.get(url, timeout=45)
            if r.status_code != 200:
                continue
            rows = r.json()["data"]["data"]["values"]
            out = {}
            for wsi, ghid, name, lon, lat, elev, begin in rows:
                out[wsi] = (float(lon), float(lat), float(elev))
            return out
        except Exception as exc:  # noqa: BLE001
            log.debug("chmi meta %s: %s", d, exc)
    return None


def fetch_chmi(cfg, days: int = 3, margin_deg: float = 0.4) -> TempStations | None:
    """Průměrná 2m teplota (element T) na ČHMÚ stanicích v bbox±margin."""
    end = date.today()
    meta = _chmi_station_meta(cfg, end)
    if not meta:
        log.info("chmi teplota: metadata nedostupná → přeskočeno")
        return None

    lonmin, latmin, lonmax, latmax = cfg.bbox
    near = {
        wsi: (lon, lat, elev)
        for wsi, (lon, lat, elev) in meta.items()
        if lonmin - margin_deg <= lon <= lonmax + margin_deg
        and latmin - margin_deg <= lat <= latmax + margin_deg
    }
    if not near:
        return None

    cache_dir = cfg.data_dir / "chmi_now"
    cache_dir.mkdir(parents=True, exist_ok=True)
    import json
    from concurrent.futures import ThreadPoolExecutor

    def _station(item):
        wsi, (lon, lat, elev) = item
        temps = []
        for di in range(days):
            d = end - timedelta(days=di)
            fname = f"10m-{wsi}-{d:%Y%m%d}.json"
            cache = cache_dir / fname
            try:
                if cache.exists() and di > 0 and cache.stat().st_size > 0:
                    payload = cache.read_bytes()
                else:
                    r = requests.get(f"{_CHMI_NOW}/data/{fname}", timeout=45)
                    if r.status_code != 200:
                        continue
                    payload = r.content
                    cache.write_bytes(payload)
                for v in json.loads(payload)["data"]["data"]["values"]:
                    if v[1] == "T" and v[3] is not None:
                        try:
                            temps.append(float(v[3]))
                        except (TypeError, ValueError):
                            pass
            except Exception as exc:  # noqa: BLE001
                log.debug("chmi T %s %s: %s", wsi, d, exc)
        return (lon, lat, elev, float(np.mean(temps))) if temps else None

    # národní síť má stovky stanic → paralelně (jen HTTP GETy)
    with ThreadPoolExecutor(max_workers=12) as ex:
        rows = [r for r in ex.map(_station, list(near.items())) if r is not None]

    if not rows:
        return None
    a = np.array(rows, dtype=float)
    log.info("chmi teplota: %d stanic", len(rows))
    return TempStations(lon=a[:, 0], lat=a[:, 1], elev=a[:, 2], temp=a[:, 3])


# --------------------------------------------------------------- Netatmo ---
def fetch_netatmo(cfg) -> TempStations | None:
    """Teplota z veřejných Netatmo stanic (required_data=temperature)."""
    from .netatmo import _tiles, refresh_access_token

    token = refresh_access_token(cfg)
    if not token:
        return None
    api = cfg.sources.get("netatmo", {}).get("api_url", "https://api.netatmo.com/api/getpublicdata")
    tile = float(cfg.sources.get("netatmo", {}).get("tile_deg", 0.15))
    max_tiles = int(cfg.sources.get("netatmo", {}).get("max_tiles", 200))
    hdr = {"Authorization": f"Bearer {token}"}
    rows = []
    import time

    nbbox = cfg.sources.get("netatmo", {}).get("bbox") or cfg.bbox
    tiles = list(_tiles(nbbox, tile))
    if len(tiles) > max_tiles:
        log.warning("netatmo teplota: %d dlaždic > max_tiles=%d → omezuji", len(tiles), max_tiles)
        tiles = tiles[:max_tiles]
    for (lo, la, lo2, la2) in tiles:
        params = {"lat_ne": la2, "lon_ne": lo2, "lat_sw": la, "lon_sw": lo,
                  "required_data": "temperature", "filter": "true"}
        try:
            for attempt in range(3):
                r = requests.get(api, params=params, headers=hdr, timeout=30)
                if r.status_code == 503:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                for st in r.json().get("body", []):
                    place = st.get("place") or {}
                    loc = place.get("location")
                    alt = place.get("altitude")
                    if not loc or alt is None:
                        continue
                    for mod in (st.get("measures") or {}).values():
                        types = mod.get("type")
                        res = mod.get("res")
                        if isinstance(types, list) and "temperature" in types and isinstance(res, dict):
                            last = list(res.values())[-1]
                            t = last[types.index("temperature")]
                            rows.append((float(loc[0]), float(loc[1]), float(alt), float(t)))
                            break
                break
            time.sleep(0.3)
        except Exception as exc:  # noqa: BLE001
            log.debug("netatmo teplota dlaždice: %s", exc)
    if not rows:
        return None
    a = np.array(rows, dtype=float)
    log.info("netatmo teplota: %d stanic", len(rows))
    return TempStations(lon=a[:, 0], lat=a[:, 1], elev=a[:, 2], temp=a[:, 3])


# ------------------------------------------------------ složení do gridu ---
def _reference_field(cfg, grid: Grid, stns: TempStations) -> np.ndarray:
    """QC + přepočet na referenční výšku + IDW → pole teploty při elev_ref."""
    m = cfg.model
    lapse = float(m.get("lapse_c_per_100m", 0.6))
    elev_ref = float(m.get("elev_ref_m", 300.0))

    # přepočet na referenční výšku
    t_ref = stns.temp + lapse * (stns.elev - elev_ref) / 100.0

    # QC na referenčních teplotách (rozsah + MAD)
    qc = qc_gauges(t_ref, rain_min_mm=-45.0, rain_max_mm=50.0,
                   mad_k=float(cfg.qc.get("mad_k", 5.0)))
    keep = qc.keep
    if qc.n_dropped:
        log.info("teplota QC: vyřazeno %d/%d stanic", qc.n_dropped, len(stns))
    if keep.sum() == 0:
        return None

    sx, sy = grid.lonlat_to_meters(stns.lon[keep], stns.lat[keep])
    gx, gy = grid.xy_meters()
    field = idw(sx, sy, t_ref[keep], gx, gy,
                k=int(cfg.merge.get("idw_k", 8)), power=float(cfg.merge.get("idw_power", 2.0)))
    return field


def fetch(cfg, grid: Grid, days: int = 3) -> np.ndarray | None:
    """Slej ČHMÚ + Netatmo teploty do referenčního pole (elev_ref). None při chybě."""
    stns = TempStations.concat([fetch_chmi(cfg, days=days), fetch_netatmo(cfg)])
    if stns is None:
        return None
    return _reference_field(cfg, grid, stns)


def synthetic(grid: Grid, mean_c: float = 15.5, seed: int = 3) -> np.ndarray:
    """Referenční teplota s mírným S-J gradientem (pro --demo)."""
    rng = np.random.default_rng(seed)
    LON, LAT = grid.meshgrid()
    grad = (grid.lat_max - LAT) * 1.5
    return mean_c + grad - grad.mean() + rng.normal(0, 0.2, size=grid.shape)
