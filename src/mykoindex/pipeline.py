"""Orchestrace: fetch → QC+slévání → index → export.

Robustní vůči výpadkům zdrojů: každý zdroj, který selže, se zaloguje a
nahradí (demo syntetikou nebo neutrální hodnotou), pipeline nespadne.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from . import analysis, export, history as history_mod, index as idx
from .config import Config, load_config
from .grid import Grid
from .merge import conditional_merge, qc_gauges
from .sources import chmi_merge, era5, forecast, forest, gbif, netatmo, temperature, terrain

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    grid: Grid
    index: np.ndarray
    mode: str
    sources_used: dict = field(default_factory=dict)
    hotspots: list = field(default_factory=list)
    weather_summary: str = ""
    forecast_text: str = ""


def _apply_netatmo_correction(cfg, grid, daily, mode, sources_used, radar_truth=None):
    """Slej Netatmo korekci do posledních netatmo_days dní."""
    ndays = int(cfg.merge.get("netatmo_days", 2))
    qc_cfg = cfg.qc
    m_cfg = cfg.merge

    if mode == "demo":
        stations = netatmo.synthetic(cfg, truth_field=radar_truth, grid=grid)
        sources_used["netatmo"] = "synthetic"
    else:
        st = netatmo.fetch(cfg)
        if st is None:
            sources_used["netatmo"] = "unavailable"
            return daily
        stations = st
        sources_used["netatmo"] = f"live ({len(st)} stanic)"

    qc = qc_gauges(
        stations.rain,
        rain_min_mm=qc_cfg.get("rain_min_mm", 0.0),
        rain_max_mm=qc_cfg.get("rain_max_mm", 250.0),
        mad_k=qc_cfg.get("mad_k", 5.0),
    )
    keep = qc.keep
    if qc.n_dropped:
        log.info("netatmo QC: vyřazeno %d/%d stanic", qc.n_dropped, len(stations))
    if keep.sum() == 0:
        return daily

    slon = stations.lons[keep]
    slat = stations.lats[keep]
    sval = stations.rain[keep]

    daily = daily.copy()
    for d in range(min(ndays, daily.shape[0])):
        daily[d] = conditional_merge(
            daily[d], grid, slon, slat, sval,
            idw_k=int(m_cfg.get("idw_k", 8)),
            idw_power=float(m_cfg.get("idw_power", 2.0)),
        )
    return daily


def run(cfg: Config | None = None, *, demo: bool = False, day: date | None = None) -> PipelineResult:
    cfg = cfg or load_config()
    day = day or date.today()
    grid = Grid.from_bbox(cfg.bbox, cfg.resolution_m)
    mode = "demo" if demo else "live"
    sources_used: dict = {}
    m = cfg.model

    # --- 1) Srážková páteř (denní stack, index 0 = dnešek) ---
    window = int(m.get("api_window_days", 30))
    if demo:
        daily = chmi_merge.synthetic_daily_precip(grid, days=window)
        sources_used["chmi_merge"] = "synthetic"
    else:
        daily = chmi_merge.fetch_daily_precip(cfg, grid, end_day=day, days=window)
        if daily is None:
            log.warning("MERGE nedostupný → páteř bude jen z Netatmo (nebo nula)")
            daily = np.zeros((window, grid.ny, grid.nx))
            sources_used["chmi_merge"] = "unavailable"
        else:
            sources_used["chmi_merge"] = "live"

    # --- 2) Netatmo korekce (QC + conditional merging, jen poslední dny) ---
    radar_truth = daily[0].copy() if demo else None
    daily = _apply_netatmo_correction(cfg, grid, daily, mode, sources_used, radar_truth)

    # --- 3) Vlhkostní vrstva ---
    api = idx.api30(daily, tau_days=float(m.get("tau_days", 12)), window_days=window)
    moisture = idx.moisture_score(api, saturation_mm=float(m.get("api_saturation_mm", 60)))

    # --- 4) Terén ---
    got = None if demo else terrain.fetch(cfg, grid)
    if got is None:
        elev, northness = terrain.synthetic(grid)
        sources_used["terrain"] = "synthetic" if demo else sources_used.get("terrain", "synthetic")
    else:
        elev, northness = got
        sources_used["terrain"] = "live"
    aspect = idx.aspect_factor(
        northness, base=float(m.get("aspect_base", 0.82)), span=float(m.get("aspect_span", 0.18))
    )

    # --- 5) Teplota: primárně pozemní stanice (ČHMÚ+Netatmo), volitelně ERA5 ---
    provider = cfg.sources.get("temperature", {}).get("provider", "stations")
    station_days = int(cfg.sources.get("temperature", {}).get("station_days", 3))
    t_field = None
    if not demo:
        if provider == "era5":
            t_field = era5.fetch(cfg, grid, day=day)
            if t_field is not None:
                sources_used["temperature"] = "era5"
        else:
            t_field = temperature.fetch(cfg, grid, days=station_days)
            if t_field is not None:
                sources_used["temperature"] = "stations (ČHMÚ+Netatmo)"
    if t_field is None:
        t_field = temperature.synthetic(grid)
        sources_used["temperature"] = "synthetic" if demo else sources_used.get("temperature", "synthetic")
    t_local = idx.local_temperature(
        t_field, elev,
        lapse_c_per_100m=float(m.get("lapse_c_per_100m", 0.6)),
        elev_ref_m=float(m.get("elev_ref_m", 300.0)),
    )
    temp = idx.temperature_score(
        t_local, t_min=float(m.get("t_min", 5)), t_opt=float(m.get("t_opt", 14)),
        t_max=float(m.get("t_max", 25)),
    )

    # --- 6) Les ---
    forest_only = bool(cfg.modules.get("forest_only", True))
    f01 = None if demo else forest.fetch(cfg, grid)
    if f01 is None:
        f01 = forest.synthetic(grid, elev=elev)
        sources_used["forest"] = "synthetic" if demo else sources_used.get("forest", "synthetic")
    else:
        sources_used["forest"] = "live"
    forest_fac = idx.forest_factor(
        f01, base=float(m.get("forest_base", 0.35)), span=float(m.get("forest_span", 0.65)),
        enabled=forest_only,
    )

    # --- 7) Výsledný index ---
    inputs = idx.IndexInputs(moisture=moisture, temp=temp, aspect=aspect, forest=forest_fac)
    index_field = idx.mykoindex(
        inputs, w_moist=float(m.get("w_moist", 0.55)), w_temp=float(m.get("w_temp", 0.45))
    )

    # --- 8) Odvozené výstupy: hotspoty, popis počasí, předpověď ---
    hotspots = analysis.find_hotspots(index_field, f01, grid, cfg)

    fc = forecast.synthetic(cfg) if demo else forecast.fetch(cfg)
    if fc is not None and not demo:
        sources_used["forecast"] = "live (Open-Meteo)"
    elif demo:
        sources_used["forecast"] = "synthetic"
    else:
        sources_used["forecast"] = "unavailable"

    weather = analysis.weather_summary(
        cfg, daily, float(np.nanmean(moisture)), float(np.nanmean(t_local))
    )
    fc_text = analysis.forecast_text(
        cfg, fc, float(np.nanmean(api)), float(np.nanmean(t_local))
    )

    return PipelineResult(
        grid=grid, index=index_field, mode=mode, sources_used=sources_used,
        hotspots=hotspots, weather_summary=weather, forecast_text=fc_text,
    )


def run_and_export(cfg: Config | None = None, *, demo: bool = False, day: date | None = None) -> dict:
    """Spočítej a vyexportuj out/index.tif, out/index.png, out/localities.json."""
    cfg = cfg or load_config()
    res = run(cfg, demo=demo, day=day)
    out = cfg.out_dir

    # 30denní historie ideálních podmínek
    hist = history_mod.record(cfg, res.grid, res.index, today=day)
    if hist.get("ideal_days_max", 0) > 0:
        export.write_ideal_overlay(hist["ideal_days_grid"], hist["ideal_days_max"], out / "ideal30.png")

    tif = export.write_geotiff(res.index, res.grid, out / "index.tif")
    png = export.write_png_overlay(res.index, out / "index.png")
    js = export.write_localities_json(
        res.index, res.grid, cfg, out / "localities.json",
        mode=res.mode, sources_used=res.sources_used,
        hotspots=res.hotspots, weather_summary=res.weather_summary,
        forecast_text=res.forecast_text, history=hist,
    )
    log.info("Export hotov: %s", ", ".join(str(p.name) for p in (tif, png, js) if p))
    return {
        "mode": res.mode,
        "sources_used": res.sources_used,
        "stats": {
            "min": float(np.nanmin(res.index)),
            "mean": float(np.nanmean(res.index)),
            "max": float(np.nanmax(res.index)),
        },
        "out": {"geotiff": str(tif) if tif else None, "png": str(png), "json": str(js)},
    }
