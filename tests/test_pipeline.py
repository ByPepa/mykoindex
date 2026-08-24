"""Testy celé pipeline v demo režimu (kritéria 1, 2, 5) a kalibrace (M6)."""
import numpy as np

from mykoindex.calibrate import calibrate
from mykoindex.config import load_config
from mykoindex.pipeline import run, run_and_export
from mykoindex.sources import gbif


def test_demo_run_produces_valid_index():
    cfg = load_config()
    res = run(cfg, demo=True)
    assert res.mode == "demo"
    assert res.index.shape == res.grid.shape
    assert np.isfinite(res.index).all()
    assert res.index.min() >= 0 and res.index.max() <= 100


def test_demo_export_writes_all_outputs(tmp_path):
    cfg = load_config()
    cfg.out_dir = tmp_path
    summary = run_and_export(cfg, demo=True)
    assert (tmp_path / "index.png").exists()
    assert (tmp_path / "localities.json").exists()
    # GeoTIFF je volitelný (závisí na rasterio), ale v tomto prostředí musí být
    assert summary["out"]["json"].endswith("localities.json")
    assert set(summary["sources_used"]) >= {"chmi_merge", "netatmo", "temperature", "terrain", "forest"}


def test_localities_have_scores_and_verdicts(tmp_path):
    import json

    cfg = load_config()
    cfg.out_dir = tmp_path  # neklobrat reálné out/
    run_and_export(cfg, demo=True)
    data = json.loads((tmp_path / "localities.json").read_text(encoding="utf-8"))
    names = {l["name"] for l in data["localities"]}
    assert {"Tesák", "Troják", "Rusava", "Hostýn"} <= names
    for l in data["localities"]:
        assert 0 <= l["score"] <= 100
        assert l["verdict"] in {"Vyraž", "Dá se", "Počkej", "Sucho"}
    assert len(data["cities"]) >= 10
    assert "weather_summary" in data and "forecast" in data
    assert isinstance(data["hotspots"], list)


def test_live_run_degrades_gracefully_without_tokens(monkeypatch):
    """Bez tokenů/klíčů reálný běh nespadne – jen použije, co má (kritérium 5)."""
    cfg = load_config()
    for k in list(cfg.env):
        cfg.env[k] = None
    # nechceme v testu bušit do opendata.chmi.cz → mrtvá adresa (rychlé selhání)
    cfg.raw["sources"]["chmi"]["merge_dir"] = "http://127.0.0.1:1"
    cfg.model["api_window_days"] = 2  # rychleji
    # teplota přes ERA5 (bez klíče → None → synthetic), ať test nesahá na síť
    cfg.raw["sources"].setdefault("temperature", {})["provider"] = "era5"
    res = run(cfg, demo=False)
    assert np.isfinite(res.index).all()
    # bez MERGE URL se páteř nestáhne → chmi_merge "unavailable"
    assert res.sources_used.get("chmi_merge") in {"unavailable", "live"}


def test_history_accumulates_and_prunes(tmp_path):
    import json
    from datetime import date, timedelta

    cfg = load_config()
    cfg.out_dir = tmp_path
    cfg.data_dir = tmp_path / "d"
    (cfg.data_dir).mkdir(parents=True, exist_ok=True)

    end = date(2026, 8, 24)
    for i in range(5, -1, -1):
        run_and_export(cfg, demo=True, day=end - timedelta(days=i))
    hj = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert len(hj["days"]) == 6  # 6 dní nasbíráno
    assert hj["days"][0]["date"] < hj["days"][-1]["date"]

    # skok o 40 dní → staré (>30) se smažou
    run_and_export(cfg, demo=True, day=end + timedelta(days=40))
    hj2 = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert len(hj2["days"]) == 1

    data = json.loads((tmp_path / "localities.json").read_text(encoding="utf-8"))
    assert "history" in data and data["history"]["window_days"] == 30
    assert "timeline" in data["localities"][0]


def test_calibration_returns_params():
    cfg = load_config()
    occ = gbif.synthetic(cfg)
    cal = calibrate(cfg, occ)
    assert 0 <= cal.w_moist <= 1 and 0 <= cal.w_temp <= 1
    assert abs(cal.w_moist + cal.w_temp - 1.0) < 1e-6
    assert set(cal.verdict_thresholds) == {"Vyraž", "Dá se", "Počkej"}
    assert 0.0 <= cal.auc <= 1.0
