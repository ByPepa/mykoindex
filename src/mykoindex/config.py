"""Načtení config.yaml + proměnných z .env.

Konfigurace je zdroj pravdy pro parametry. Kód sahá jen sem, nikdy si
nedrží natvrdo bbox, váhy ani prahy.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    # src/mykoindex/config.py -> repo root o tři úrovně výš
    return Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimalistický .env loader (bez závislosti na python-dotenv)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # .env nikdy nepřepisuje už nastavené prostředí (CI má přednost)
        os.environ.setdefault(key, value)


@dataclass
class Locality:
    name: str
    lon: float
    lat: float


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path
    bbox: tuple[float, float, float, float]
    resolution_m: float
    model: dict[str, Any]
    qc: dict[str, Any]
    merge: dict[str, Any]
    modules: dict[str, Any]
    localities: list[Locality]
    cities: list[Locality]
    sources: dict[str, Any]
    data_dir: Path
    out_dir: Path
    env: dict[str, str | None] = field(default_factory=dict)

    # --- pohodlné přístupové zkratky ---
    @property
    def lon_min(self) -> float:
        return self.bbox[0]

    @property
    def lat_min(self) -> float:
        return self.bbox[1]

    @property
    def lon_max(self) -> float:
        return self.bbox[2]

    @property
    def lat_max(self) -> float:
        return self.bbox[3]


def load_config(path: str | os.PathLike | None = None) -> Config:
    root = _repo_root()
    cfg_path = Path(path) if path else root / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    _load_dotenv(root / ".env")

    paths = raw.get("paths", {})
    data_dir = root / paths.get("data_dir", "data")
    out_dir = root / paths.get("out_dir", "out")
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    bbox = tuple(float(x) for x in raw["bbox"])  # type: ignore[assignment]

    localities = [Locality(**d) for d in raw.get("localities", [])]
    cities = [Locality(**d) for d in raw.get("cities", [])]

    env = {
        "NETATMO_CLIENT_ID": os.environ.get("NETATMO_CLIENT_ID"),
        "NETATMO_CLIENT_SECRET": os.environ.get("NETATMO_CLIENT_SECRET"),
        "NETATMO_REFRESH_TOKEN": os.environ.get("NETATMO_REFRESH_TOKEN"),
        "CDS_API_KEY": os.environ.get("CDS_API_KEY"),
        "CDS_API_URL": os.environ.get("CDS_API_URL"),
    }

    return Config(
        raw=raw,
        root=root,
        bbox=bbox,  # type: ignore[arg-type]
        resolution_m=float(raw.get("resolution_m", 1000)),
        model=raw.get("model", {}),
        qc=raw.get("qc", {}),
        merge=raw.get("merge", {}),
        modules=raw.get("modules", {}),
        localities=localities,
        cities=cities,
        sources=raw.get("sources", {}),
        data_dir=data_dir,
        out_dir=out_dir,
        env=env,
    )
