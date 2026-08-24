#!/usr/bin/env python3
"""Stáhni statické vrstvy (DEM, lesní maska) pro bbox – veřejná data, bez klíčů.

Skládá dlaždice, které bbox protíná (velká oblast = víc dlaždic):
  - Copernicus DEM GLO-30 (1° dlaždice) → mozaika, přesampluje na ~100 m
  - ESA WorldCover 2021 (3° dlaždice) → podíl stromů (třída 10) na ~100 m

Vše čte jen výřez bboxu z veřejných COG na AWS přes GDAL /vsicurl/ a ukládá
malé GeoTIFFy do data/. Cesty pak patří do config.yaml.

    python scripts/fetch_static.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject
from rasterio.windows import from_bounds as win_from_bounds

from mykoindex.config import load_config

RES = 0.0018  # cílové rozlišení mozaiky ~200 m (pro celou ČR únosné, na 1km mřížku stačí)


def dem_urls(bbox, margin=0.1):
    lonmin, latmin, lonmax, latmax = bbox
    urls = []
    for lat in range(math.floor(latmin - margin), math.floor(latmax + margin) + 1):
        for lon in range(math.floor(lonmin - margin), math.floor(lonmax + margin) + 1):
            ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
            name = f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"
            urls.append(f"https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif")
    return urls


def worldcover_urls(bbox, margin=0.1):
    lonmin, latmin, lonmax, latmax = bbox
    urls = []
    lat0 = math.floor((latmin - margin) / 3) * 3
    lon0 = math.floor((lonmin - margin) / 3) * 3
    for lat in range(lat0, math.floor((latmax + margin) / 3) * 3 + 3, 3):
        for lon in range(lon0, math.floor((lonmax + margin) / 3) * 3 + 3, 3):
            ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
            name = f"ESA_WorldCover_10m_2021_v200_{ns}{abs(lat):02d}{ew}{abs(lon):03d}_Map.tif"
            urls.append(f"https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/{name}")
    return urls


def _dst_grid(bbox):
    lonmin, latmin, lonmax, latmax = bbox
    width = int(round((lonmax - lonmin) / RES))
    height = int(round((latmax - latmin) / RES))
    transform = from_bounds(lonmin, latmin, lonmax, latmax, width, height)
    return width, height, transform


def build_mosaic(urls, bbox, out_path, *, to_tree_fraction=False):
    """Slej dlaždice do jednoho GeoTIFF na ~100 m mřížce bboxu."""
    width, height, dst_transform = _dst_grid(bbox)
    dst = np.zeros((height, width), dtype="float32")
    covered = np.zeros((height, width), dtype=bool)
    lonmin, latmin, lonmax, latmax = bbox

    with rasterio.Env(GDAL_HTTP_UNSAFESSL="YES", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        for url in urls:
            try:
                ds = rasterio.open("/vsicurl/" + url)
            except Exception as exc:  # noqa: BLE001 – dlaždice nemusí existovat
                print(f"  (přeskočeno) {url.split('/')[-1]}: {exc}")
                continue
            with ds:
                b = ds.bounds
                # průnik s bboxem
                ix0, iy0, ix1, iy1 = (max(lonmin, b.left), max(latmin, b.bottom),
                                      min(lonmax, b.right), min(latmax, b.top))
                if ix0 >= ix1 or iy0 >= iy1:
                    continue
                win = win_from_bounds(ix0, iy0, ix1, iy1, ds.transform)
                # dekimace na ~100 m
                oh = max(1, int(win.height / (RES / abs(ds.transform.e))))
                ow = max(1, int(win.width / (RES / ds.transform.a)))
                resamp = Resampling.mode if to_tree_fraction else Resampling.bilinear
                arr = ds.read(1, window=win, out_shape=(oh, ow), resampling=resamp).astype("float32")
                if to_tree_fraction:
                    arr = (arr == 10).astype("float32")  # WorldCover třída 10 = stromy
                src_transform = ds.window_transform(win) * rasterio.Affine.scale(
                    win.width / ow, win.height / oh
                )
                tmp = np.zeros((height, width), dtype="float32")
                reproject(
                    arr, tmp, src_transform=src_transform, src_crs=ds.crs,
                    dst_transform=dst_transform, dst_crs="EPSG:4326",
                    resampling=Resampling.average if to_tree_fraction else Resampling.bilinear,
                )
                fill = tmp != 0
                dst[fill] = tmp[fill]
                covered |= fill
                print(f"  + {url.split('/')[-1]}")

    profile = {
        "driver": "GTiff", "height": height, "width": width, "count": 1,
        "dtype": "float32", "crs": "EPSG:4326", "transform": dst_transform,
        "compress": "deflate",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dstds:
        dstds.write(dst, 1)
    return out_path, float(covered.mean())


def main() -> int:
    cfg = load_config()
    b = cfg.bbox

    dem_out = cfg.data_dir / "dem_bbox.tif"
    wc_out = cfg.data_dir / "worldcover_bbox.tif"

    print("DEM (Copernicus GLO-30) – dlaždice:")
    _, cov = build_mosaic(dem_urls(b), b, dem_out)
    print(f"  -> {dem_out} (pokrytí {cov*100:.0f} %)")

    print("Lesní maska (ESA WorldCover 2021) – dlaždice:")
    _, cov = build_mosaic(worldcover_urls(b), b, wc_out, to_tree_fraction=True)
    print(f"  -> {wc_out} (pokrytí {cov*100:.0f} %)")

    print("\nV config.yaml je nastaveno:")
    print("  sources.terrain.dem_path: data/dem_bbox.tif")
    print("  sources.forest.mask_path: data/worldcover_bbox.tif")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
