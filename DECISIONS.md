# DECISIONS – rozhodnutí a defaulty

Doplňky tam, kde SPEC.md nechal prostor. Zdroj pravdy zůstává `SPEC.md`
(sekce Model + Akceptační kritéria); zde je jen to, co bylo dorozhodnuto.

## Rozšíření na Zlínský + Olomoucký kraj
- bbox rozšířen na `[16.65, 48.85, 18.60, 50.30]` (~130×160 km, vč. Jeseníků,
  Beskyd, Chřibů, Hostýnských vrchů). 13 lokalit, 18 měst.
- **DEM + les**: `fetch_static.py` skládá mozaiku dlaždic (9× Copernicus DEM 1°,
  2× ESA WorldCover 3°), přesamplováno na ~100 m. Les = podíl stromů (třída 10).
  DEM ověřen: max 1487 m ≈ Praděd. Les mean 47 % (víc na V v horách).
- **Netatmo**: `tile_deg` zvýšeno na 0.25 + `max_tiles` pojistka (velká oblast =
  jinak stovky volání). Reálně ~48 dlaždic; 1416 srážkových / ~4000 teplotních stanic.
- **Barevná škála**: obrácena na zelená=ideál / červená=sucho (export `_STOPS`, legenda).
- **TOP tipy v lese** (`analysis.find_hotspots`): lokální maxima indexu v lese
  (min_index/min_forest/min_dist_km v configu) → značky v mapě.
- **Popis počasí za 20 dní** (`analysis.weather_summary`): úhrny, epizody, sucho,
  trend + vlhkost/teplota → text v mapě.
- **Odhad „kdy porostou"** (`sources/forecast.py` Open-Meteo, bez klíče +
  `analysis.forecast_text`): najde v předpovědi vydatný déšť (≥ soak_mm/3 dny)
  při vhodné teplotě → fruktifikace za 7–12 dní; jinak popíše aktuální stav.

## Struktura repa
- Kořen repa = kořen projektu (ne vnořený `mykoindex/`). Ostrý kód žije v
  `src/mykoindex/`, prototypy zůstávají v `prototype/` jako referenční jádro.
- Prototypy `mykoindex_prototyp.py` / `mykoindex_mapa.html` nebyly dodané →
  vytvořeny jako samostatné referenční implementace (numpy/scipy jádro a
  in-browser generované pole), z nichž vychází `src/`.

## Pracovní mřížka
- Mřížka je pravidelná v **lon/lat (WGS84)**, krok odvozený z `resolution_m`
  (1000 m) při středové šířce → buňky ~1×1 km, čistý bbox pro Leaflet
  `imageOverlay`. Řádky sever→jih (north-up rastr).
- Vzdálenosti pro IDW: lokální equirektangulární projekce do metrů (chyba nad
  ~50 km zanedbatelná) místo plné projekce – bez další závislosti.
- Reprojekce zdrojů: bilineární `scipy.RegularGridInterpolator` na 1D osách.

## Model (defaulty převzaté ze SPEC)
- `tau_days = 12`, `api_saturation_mm = 60`, okno API `30` dní, teplotní okno `7` dní.
- `w_moist = 0.55`, `w_temp = 0.45` (ladicí; kalibrace z GBIF je může přepsat).
- Teplota: optimum 14 °C, rozsah 5–25 °C, výškový gradient 0.6 °C/100 m, ref. 300 m.
- `aspect_factor = 0.82 + 0.18·northness` (0.64–1.0); `forest_factor = 0.35 + 0.65·forest01`.
- Verdikty: ≥70 Vyraž, ≥50 Dá se, ≥32 Počkej, jinak Sucho.
- **Northness**: definován jako projekce svahu na sever (`-dz/dy` normalizovaný
  sklonem), rovina → 0. Sever i východ „drží vláhu"; MVP váží severní expozici,
  východní varianta je možné rozšíření.

## QC a slévání
- QC měřáků: rozsah [0, 250] mm, MAD `k = 5`, detekce zaseknuté konstanty (když
  je dodána časová řada). Netatmo prochází QC vždy (proměnlivá kvalita).
- Slévání: aditivní conditional merging, IDW `k = 8`, mocnina `2`,
  `merged = max(0, radar + IDW(gauge − radar_v_bodě))`.
- **Netatmo okno**: koriguje jen poslední `2` dny (data jsou jen živá, 24 h).

## Zdroje dat – měkké selhání (kritérium 5)
- Každý zdroj má reálný fetcher i `synthetic_*`. Reálný fetcher při výpadku
  loguje a vrací `None`; pipeline pokračuje:
  - MERGE `None` → nulová páteř (+ Netatmo rezidua), zdroj označen `unavailable`.
  - Netatmo/ERA5/terén/les `None` → v ostrém běhu fallback na syntetiku (aby
    mapa vždy vznikla), v `localities.json` viditelně označeno v `sources_used`.
- Režim `mode` a `sources_used` jsou v `localities.json` i v UI (odděluje
  „OSTRÁ DATA" vs „DEMO / ILUSTRACE" – SPEC sekce 10).

## ČHMÚ MERGE – POTVRZENO a zapojeno (ostrá data)
- Endpoint ověřen: `.../meteorology/weather/radar/composite/merge1h/hdf5/`,
  soubory `T_PASV23_C_OKPR_YYYYMMDDHHMM00.hdf` (**ODIM_H5, ne GeoTIFF**),
  1h úhrn (quantity ACRR, mm), gain/offset + nodata/undetect, čas = konec
  intervalu UTC. Georeference: Mercator `projdef` v `where/`, UL roh = proj (0,0),
  ~1.56 km. Reader `_read_odim_to_grid` reprojektuje přes pyproj (ověřeno: rohy
  round-tripují, rozměry sedí, reálné úhrny 20.–21. 8. 2026 ~28–30 mm/den).
- **Historie jen ~6–7 dní** na otevřeném serveru → 30denní okno API30 se BUDUJE
  postupně: každý hodinový soubor se trvale cachuje do `data/merge/`, denní cron
  okno naplní. Dokud okno není plné, je vlhkostní vrstva podhodnocená (méně dní).
- Bez klíčů: veřejná data (CC BY 4.0). Funguje hned, `sources.chmi` v `config.yaml`.

## Teplota – POZEMNÍ STANICE místo ERA5 (default, ostrá data, bez klíče)
- Rozhodnuto: primární zdroj teploty jsou **ČHMÚ + Netatmo stanice**, ne ERA5.
  Důvod: bez CDS klíče, (skoro) reálný čas, hustší pokrytí. SPEC to připouští
  („Alternativa: bodová data ČHMÚ"). ERA5 zůstává volitelně
  (`sources.temperature.provider: era5`, potřebuje `CDS_API_KEY`).
- **ČHMÚ „now"** (ověřeno): `climate/now/data/10m-{WSI}-{YYYYMMDD}.json`, element
  **T** = 2m teplota °C (10min); souřadnice+výška z `metadata/meta1-*.json`
  (GEOGR1=lon, GEOGR2=lat, ELEVATION). Filtr na bbox±0.4° (~46 stanic v jádru+okolí).
  Pozn.: některé stanice mají `VAL` jako string → coerce na float.
- **Netatmo teplota**: `getpublicdata required_data=temperature`; modul
  `type:[temperature,humidity]`, `res:{ts:[t,rh]}`; výška z `place.altitude`
  (~340 stanic v bboxu). Amatérská kvalita → QC povinné.
- **Skládání**: každou stanici přepočti na referenční výšku
  `T_ref = T + lapse·(elev−elev_ref)/100`, QC (rozsah −45..50 °C, MAD k=5),
  IDW do gridu → referenční pole; `index.local_temperature` pak přidá gradient
  podle DEM. `station_days` (default 3) omezuje počet stažených ČHMÚ souborů.

## ERA5-Land / GBIF
- ERA5-Land přes `cdsapi` (nový CDS `.../api`, `data_format`+`download_format`,
  ošetřen zip); denní průměr 3h kroků za 7 dní; K→°C; reprojekce. Jen když
  `provider: era5`. Cache v `data/`.
- **GBIF – zapojeno (ostrá data, bez klíčů).** Přímo přes REST `api.gbif.org`
  (pygbif má nekompatibilitu s requests). Rod Boletus = klíč **8287374**
  (`species/search` na backbone; edulis/reticulatus). Kalibrace bere celou **ČR**
  (`sources.gbif.country`), ne malý bbox – v bboxu je jen ~5 nálezů, v ČR 642
  (2015–2025) s jasnou sezónou VIII–X. Prahy z reálných dat vyšly blízko SPEC
  defaultům (Počkej ~32.7 ≈ 32). Rozdělení w_moist/w_temp zůstává artefaktem
  offline klimatologie, dokud se nezapojí reálné historické podmínky (condition_fn).

## Kalibrace (M6)
- Metoda **presence-background**: nálezy = pozitiva, náhodné body v čase/prostoru
  = pozadí; logistická regrese (numpy, bez sklearn) → nezáporné koeficienty
  normované na `w_moist/w_temp`; prahy verdiktů z percentilů indexu v nálezech.
- Kvůli vychýlení nálezů (víkendy, parkoviště, rozmazané souřadnice) je důraz na
  **časovou** kalibraci. `condition_fn` je zapojovací bod pro reálné historické
  podmínky; bez něj se použije reprodukovatelná sezónní klimatologie (offline).
- Spusť `python scripts/calibrate.py --demo --write` pro zápis doporučení sem.

<!-- Sem calibrate.py připisuje doporučené parametry z jednotlivých běhů. -->

## Kalibrace z GBIF (2026-08-23, režim: demo)

- Nálezů (presence): 400, pozadí: 1200, AUC: 0.761
- Doporučené váhy: `w_moist = 1.0`, `w_temp = 0.0`
- Doporučené prahy verdiktů: {"Vyraž": 89.1, "Dá se": 75.1, "Počkej": 53.4}
- Pozn.: Použita sezónní klimatologie (offline); zapoj condition_fn pro reálné podmínky.

## Kalibrace z GBIF (2026-08-23, režim: live)

- Nálezů (presence): 642, pozadí: 1926, AUC: 0.68
- Doporučené váhy: `w_moist = 1.0`, `w_temp = 0.0`
- Doporučené prahy verdiktů: {"Vyraž": 83.4, "Dá se": 63.1, "Počkej": 32.7}
- Pozn.: Použita sezónní klimatologie (offline); zapoj condition_fn pro reálné podmínky.
