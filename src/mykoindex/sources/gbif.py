"""GBIF – nálezy rodu Boletus pro kalibraci prahů/vah (offline náhrada deníku).

Uživatel nemá vlastní log nálezů. Náhrada: nálezy Boletus pro ČR/bbox
z GBIF (iNaturalist do GBIF ústí), roky ~2015–2025, s datem a souřadnicemi.

Vychýlení: nálezy hlavně o víkendech a u parkovišť, souřadnice rozmazané →
používat hlavně na ČASOVOU kalibraci (kdy fruktifikace), méně na přesné kde.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import requests

log = logging.getLogger(__name__)

_API = "https://api.gbif.org/v1"
_BACKBONE = "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c"  # GBIF Backbone Taxonomy
_BOLETUS_GENUS_KEY = 8287374  # rod Boletus L. (edulis, reticulatus, pinophilus…)


def _resolve_taxon_key(taxon: str) -> int | None:
    """Najdi klíč rodu v GBIF backbone (fallback na známý klíč rodu Boletus)."""
    try:
        r = requests.get(
            f"{_API}/species/search",
            params={"q": taxon, "rank": "GENUS", "datasetKey": _BACKBONE, "limit": 5},
            timeout=30,
        )
        r.raise_for_status()
        for res in r.json().get("results", []):
            if res.get("rank") == "GENUS" and res.get("canonicalName", "").lower() == taxon.lower():
                return int(res["key"])
    except Exception as exc:  # noqa: BLE001
        log.warning("gbif: lookup rodu selhal (%s)", exc)
    return _BOLETUS_GENUS_KEY if taxon.lower() == "boletus" else None


@dataclass
class Occurrences:
    lons: np.ndarray
    lats: np.ndarray
    dates: np.ndarray  # numpy datetime64[D]

    def __len__(self) -> int:
        return int(self.lons.size)


def fetch(cfg, limit: int = 3000) -> Occurrences | None:
    """Stáhni nálezy rodu Boletus pro kalibraci (přímo přes GBIF REST API).

    Pro kalibraci prahů (hlavně ČASOVou) se bere celá ČR (``sources.gbif.country``,
    default CZ) – v malém bboxu je nálezů málo. None při chybě/prázdnu.
    """
    src = cfg.sources.get("gbif", {})
    taxon = src.get("taxon", "Boletus")
    year_from = int(src.get("year_from", 2015))
    year_to = int(src.get("year_to", 2025))
    country = src.get("country", "CZ")

    key = int(src.get("genus_key")) if src.get("genus_key") else _resolve_taxon_key(taxon)
    if not key:
        log.warning("gbif: nepodařilo se určit taxonKey pro %s", taxon)
        return None

    params = {
        "taxonKey": key,
        "year": f"{year_from},{year_to}",
        "hasCoordinate": "true",
        "hasGeospatialIssue": "false",
        "limit": 300,
    }
    if country:
        params["country"] = country
    else:  # jinak omez na bbox
        lon_min, lat_min, lon_max, lat_max = cfg.bbox
        params["geometry"] = (
            f"POLYGON(({lon_min} {lat_min},{lon_max} {lat_min},"
            f"{lon_max} {lat_max},{lon_min} {lat_max},{lon_min} {lat_min}))"
        )

    rows: list[tuple[float, float, str]] = []
    offset = 0
    try:
        while len(rows) < limit:
            params["offset"] = offset
            r = requests.get(f"{_API}/occurrence/search", params=params, timeout=45)
            r.raise_for_status()
            res = r.json()
            for o in res.get("results", []):
                if o.get("decimalLongitude") is not None and o.get("eventDate"):
                    rows.append(
                        (
                            float(o["decimalLongitude"]),
                            float(o["decimalLatitude"]),
                            str(o["eventDate"])[:10],
                        )
                    )
            if res.get("endOfRecords") or not res.get("results"):
                break
            offset += 300
    except Exception as exc:  # noqa: BLE001
        log.warning("gbif: stažení nálezů selhalo (%s) → co mám, to použiju", exc)

    if not rows:
        return None
    lons = np.array([r[0] for r in rows])
    lats = np.array([r[1] for r in rows])
    dates = np.array([r[2] for r in rows], dtype="datetime64[D]")
    log.info("gbif: staženo %d nálezů rodu %s (%s)", len(rows), taxon, country or "bbox")
    return Occurrences(lons=lons, lats=lats, dates=dates)


def synthetic(cfg, n: int = 400, seed: int = 99) -> Occurrences:
    """Syntetické nálezy: sezónní (srpen–říjen), lehce vázané na hory."""
    rng = np.random.default_rng(seed)
    lon_min, lat_min, lon_max, lat_max = cfg.bbox
    lons = rng.uniform(lon_min, lon_max, n)
    lats = rng.uniform(lat_min, lat_max, n)
    years = rng.integers(2015, 2026, n)
    # den v roce: špička kolem 260 (polovina září), rozptyl
    doy = np.clip(rng.normal(255, 30, n).astype(int), 150, 330)
    dates = np.array(
        [np.datetime64(f"{y}-01-01") + np.timedelta64(int(d) - 1, "D") for y, d in zip(years, doy)],
        dtype="datetime64[D]",
    )
    return Occurrences(lons=lons, lats=lats, dates=dates)
