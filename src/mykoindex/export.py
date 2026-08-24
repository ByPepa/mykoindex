"""Export výsledků pro frontend: GeoTIFF + PNG overlay + localities.json.

Frontend nic nepočítá – jen zobrazuje, co je tady vyexportováno.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .grid import Grid
from .index import verdict

log = logging.getLogger(__name__)

# Barevná škála mykoindexu: sucho (červená) → ideál (zelená)
# stopy: (index0-1, R, G, B)
_STOPS = [
    (0.00, 0.80, 0.16, 0.13),  # sytě červená „sucho"
    (0.32, 0.90, 0.45, 0.15),  # oranžová „počkej"
    (0.50, 0.95, 0.80, 0.20),  # žlutá
    (0.70, 0.55, 0.78, 0.20),  # zelenožlutá „dá se"
    (1.00, 0.10, 0.60, 0.20),  # sytě zelená „ideál / vyraž"
]


def _colormap(norm: np.ndarray) -> np.ndarray:
    """norm ∈ [0,1] → RGB (0-1) lineární interpolací mezi stopami."""
    xs = np.array([s[0] for s in _STOPS])
    r = np.interp(norm, xs, [s[1] for s in _STOPS])
    g = np.interp(norm, xs, [s[2] for s in _STOPS])
    b = np.interp(norm, xs, [s[3] for s in _STOPS])
    return np.stack([r, g, b], axis=-1)


def write_geotiff(index_field: np.ndarray, grid: Grid, path: str | Path) -> Path | None:
    """Ulož index (0–100) jako jednopásmový GeoTIFF (EPSG:4326)."""
    path = Path(path)
    try:
        import rasterio

        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=grid.ny,
            width=grid.nx,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=grid.geotransform(),
            nodata=np.nan,
            compress="deflate",
        ) as ds:
            ds.write(index_field.astype("float32"), 1)
        return path
    except Exception as exc:  # noqa: BLE001
        log.warning("export: GeoTIFF se nepodařil (%s)", exc)
        return None


def write_png_overlay(index_field: np.ndarray, path: str | Path) -> Path:
    """Poloprůhledný RGBA PNG overlay (row 0 = sever, sedí na bbox)."""
    path = Path(path)
    idx = np.clip(np.nan_to_num(index_field, nan=0.0), 0, 100)
    norm = idx / 100.0
    rgb = _colormap(norm)
    # alfa roste s indexem, ať OSM prosvítá tam, kde je sucho
    alpha = 0.30 + 0.50 * norm
    rgba = np.concatenate([rgb, alpha[..., None]], axis=-1)
    rgba8 = (np.clip(rgba, 0, 1) * 255).astype(np.uint8)
    try:
        import matplotlib.image as mpimg

        mpimg.imsave(path, rgba8)
    except Exception:  # noqa: BLE001 – fallback na čisté PNG přes PIL/numpy
        _write_png_fallback(rgba8, path)
    return path


def _write_png_fallback(rgba8: np.ndarray, path: Path) -> None:
    import struct
    import zlib

    h, w, _ = rgba8.shape
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(rgba8[y].tobytes())

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def write_ideal_overlay(ideal_days: np.ndarray, max_days: int, path: str | Path) -> Path:
    """PNG overlay: kolik z posledních N dní byla buňka ideální (zelená intenzita)."""
    path = Path(path)
    a = np.asarray(ideal_days, dtype=float)
    denom = max(1, max_days)
    frac = np.clip(a / denom, 0, 1)
    # zelená: od světlé (málo dní) po sytou (hodně dní); průhledné při 0
    rgb = np.stack([
        0.30 + 0.0 * frac - 0.20 * frac,   # R klesá
        0.75 - 0.20 * frac,                # G
        0.30 - 0.10 * frac,                # B
    ], axis=-1)
    alpha = np.where(a > 0, 0.35 + 0.55 * frac, 0.0)
    rgba = np.concatenate([np.clip(rgb, 0, 1), alpha[..., None]], axis=-1)
    rgba8 = (np.clip(rgba, 0, 1) * 255).astype(np.uint8)
    try:
        import matplotlib.image as mpimg

        mpimg.imsave(path, rgba8)
    except Exception:  # noqa: BLE001
        _write_png_fallback(rgba8, path)
    return path


def _sample_localities(index_field, grid, localities, verdicts):
    out = []
    for loc in localities:
        score = float(grid.sample(index_field, loc.lon, loc.lat))
        out.append(
            {
                "name": loc.name,
                "lon": loc.lon,
                "lat": loc.lat,
                "score": round(score, 1),
                "verdict": verdict(score, verdicts),
            }
        )
    return out


def write_localities_json(
    index_field: np.ndarray,
    grid: Grid,
    cfg,
    path: str | Path,
    *,
    mode: str = "demo",
    sources_used: dict | None = None,
    hotspots: list | None = None,
    weather_summary: str = "",
    forecast_text: str = "",
    history: dict | None = None,
) -> Path:
    """localities.json: bbox, čas, režim, skóre+verdikt lokalit, města, legenda."""
    path = Path(path)
    verdicts = cfg.model.get("verdicts", [])
    history = history or {}
    localities_out = _sample_localities(index_field, grid, cfg.localities, verdicts)
    # obohať lokality o historii (timeline + ideální dny)
    timelines = history.get("locality_timelines", {})
    loc_hist = history.get("locality_history", {})
    for l in localities_out:
        l["timeline"] = timelines.get(l["name"], [])
        l.update(loc_hist.get(l["name"], {}))
    data = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,  # "live" | "demo"
        "bbox": list(grid.bbox),  # [lon_min, lat_min, lon_max, lat_max]
        "overlay": "index.png",
        "index_range": [0, 100],
        "stats": {
            "min": round(float(np.nanmin(index_field)), 1),
            "mean": round(float(np.nanmean(index_field)), 1),
            "max": round(float(np.nanmax(index_field)), 1),
        },
        "sources_used": sources_used or {},
        "verdicts": verdicts,
        "localities": localities_out,
        "history": {
            "n_days": history.get("n_history_days", 0),
            "window_days": history.get("window_days", 30),
            "ideal_min_score": history.get("ideal_min_score", 60),
            "ideal_max_days": history.get("ideal_days_max", 0),
            "overlay": "ideal30.png" if history.get("ideal_days_max", 0) > 0 else None,
        },
        "hotspots": [
            {**h, "verdict": verdict(h["score"], verdicts)} for h in (hotspots or [])
        ],
        "weather_summary": weather_summary,
        "forecast": forecast_text,
        "cities": [{"name": c.name, "lon": c.lon, "lat": c.lat} for c in cfg.cities],
        "legend": [
            {"label": "Ideál / Vyraž", "min": 70, "color": "#1a9933"},
            {"label": "Dá se", "min": 50, "color": "#8cc63f"},
            {"label": "Počkej", "min": 32, "color": "#f2c53d"},
            {"label": "Sucho", "min": 0, "color": "#cc2921"},
        ],
        "disclaimer": (
            "Model odhaduje PODMÍNKY pro růst, ne přítomnost hub. "
            "Družice nevidí pod koruny; jde o pravděpodobnost, ne jistotu."
        ),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
