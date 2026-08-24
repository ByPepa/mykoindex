# 🍄 Mykoindex

Denní odhad **podmínek pro růst hřibů** pro **Zlínský + Olomoucký kraj**,
zobrazený jako interaktivní mapa. Model slévá radarová a srážkoměrná data
(ČHMÚ MERGE) s doplňkem amatérských stanic (Netatmo), přidává teplotu z pozemních
stanic (ČHMÚ + Netatmo), terén (DEM) a lesní masku (ESA WorldCover). Navíc:
barevná škála zelená=ideál/červená=sucho, zvýraznění TOP míst v lese, textový
popis počasí za 20 dní a odhad „kdy porostou" z předpovědi (Open-Meteo).

> Model odhaduje **podmínky**, ne přítomnost hub. Družice nevidí pod koruny –
> jde o pravděpodobnost, ne jistotu.

## Rychlý start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1) Spočítej DEMO index (bez tokenů/klíčů, syntetická data)
python scripts/run_daily.py --demo

# 2) Zobraz mapu (statický web čte out/)
python -m http.server 8899
#   → otevři http://localhost:8899/web/index.html
```

`--demo` vygeneruje `out/index.tif`, `out/index.png` a `out/localities.json`.
Mapa je čistě statická – nenačítá žádné API, jen exporty z `out/`.

## Ostrý běh (reálná data)

```bash
cp .env.example .env         # a vyplň tokeny/klíče
python scripts/netatmo_auth.py   # jednorázově → NETATMO_REFRESH_TOKEN
python scripts/run_daily.py      # spočítá dnešek z reálných zdrojů
```

Potřebné přístupy (viz `.env.example`):
- **Netatmo** – OAuth2 (client id/secret + refresh token z `netatmo_auth.py`).
- **Copernicus CDS** – `CDS_API_KEY` pro ERA5-Land.
- **ČHMÚ MERGE** – veřejná data; ověř tvar URL na `opendata.chmi.cz` a nastav
  `sources.chmi.url_template` v `config.yaml` (viz `DECISIONS.md`).

Každý zdroj, který selže, se zaloguje a nahradí – pipeline nespadne. Režim
(`live`/`demo`) a použité zdroje jsou v `out/localities.json` i v UI.

## Struktura

```
config.yaml            bbox, lokality, města, váhy, prahy, cesty (zdroj pravdy parametrů)
src/mykoindex/
  grid.py              mřížka, sampling, reprojekce
  merge.py             QC + IDW + conditional merging
  index.py             API30 + teplota + mykoindex + verdikty
  export.py            GeoTIFF + PNG overlay + localities.json
  pipeline.py          orchestrace fetch→merge→index→export
  sources/             chmi_merge, netatmo, era5, terrain, forest, gbif
  calibrate.py         GBIF presence-background → doporučené váhy/prahy
scripts/
  run_daily.py         CLI (--demo / ostrý)
  netatmo_auth.py      jednorázový OAuth
  calibrate.py         kalibrace (M6)
web/index.html         Leaflet mapa (OSM + overlay + živé skóre)
prototype/             referenční jádro (samostatné)
tests/                 pytest
```

## Kalibrace (M6)

```bash
python scripts/calibrate.py --demo --write   # zapíše doporučení do DECISIONS.md
```

Presence-background z GBIF nálezů *Boletus*: nálezy = pozitiva, náhodné body =
pozadí, logistická regrese → relativní váhy vlhkost/teplota a prahy verdiktů.

## Testy

```bash
pytest -q
```

Pokrývají QC (odfiltrování vadné stanice), slévání (přiblížení k měřákům),
model (API30, teplota, index, verdikty), mřížku a celý demo běh + graceful
degradaci bez tokenů.

## Automatizace

`.github/workflows/daily.yml` spouští pipeline denně a publikuje `out/` + `web/`
na GitHub Pages. Tajemství jdou přes GitHub Secrets (nikdy do gitu).
```
