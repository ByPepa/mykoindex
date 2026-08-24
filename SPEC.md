# Mykoindex – zadání

> Zdroj pravdy pro tento projekt. Když něco není jednoznačné, drž se sekce
> **Model** a **Akceptační kritéria** a doplň rozumný default do `DECISIONS.md`.

Postav systém, který pro danou oblast na Moravě počítá **denní odhad podmínek
pro růst hřibů** a zobrazuje ho jako interaktivní webovou mapu s popsanými městy.
Odhad staví na slévání radarových a srážkoměrných dat s doplňkem amatérských
stanic Netatmo, na teplotě, typu lesa a terénu.

## 1. Co stavíme (dvě vrstvy)

- **Backend**: Python pipeline, spustitelná jedním příkazem, výstup = index grid
  pro „dnešek" + skóre pro pojmenované lokality. Běží lokálně i v CI (denně).
- **Frontend**: jeden statický HTML/JS soubor s Leaflet. Nenačítá žádné API přímo,
  jen zobrazuje to, co backend vyexportoval. Musí fungovat i na mobilu.

Non-goal: žádný běh těžkých výpočtů v prohlížeči, žádná databáze uživatelů,
žádný účet. Nic víc než „spočítej grid → zobraz grid".

## 2. Oblast a lokality
Bounding box (WGS84, lon/lat): `[17.50, 49.15, 18.00, 49.62]`
(Hostýnské vrchy – Zlín – Hranice na Moravě).

Lokality (skóre se vždy vypisuje): Tesák, Troják, Rusava, Hostýn.
Města na mapě: Zlín, Kroměříž, Holešov, Bystřice pod Hostýnem, Valašské Meziříčí,
Hranice na Moravě, Přerov, Fryšták, Lipník nad Bečvou, Vsetín.
Vše v `config.yaml`, ne natvrdo v kódu.

## 3. Datové zdroje
- **Srážky – páteř: ČHMÚ MERGE** (radar + srážkoměry, kriging s externím driftem,
  1×1 km, EPSG:3857, hodinově, UTC). 30 dní zpět → denní úhrny → reprojekce.
  V kopcích radar podhodnocuje (stínění) → slévání se srážkoměry je zásadní.
- **Srážky – korekce: Netatmo** (`getpublicdata`, OAuth2 authorization_code,
  dlaždice ~0.15°). Jen živá data (24 h) → korekce na poslední 1–2 dny.
- **Teplota: ERA5-Land** přes CDS (`2m_temperature`, denní průměr 7 dní, ~9 km);
  detail dodá výškový gradient z DEM.
- **Les**: maska ESA WorldCover / HRL; druh (smrčiny/bučiny/březiny) volitelně.
- **Terén**: Copernicus DEM GLO-30 → výška + expozice.
- **Kalibrace: GBIF** nálezy *Boletus* (2015–2025), presence-background;
  pozor na vychýlení (víkendy, parkoviště) → hlavně časová kalibrace.

## 4. Model
- **Slévání (conditional merging)**: `resid = gauge − radar_v_bodě`;
  `resid_field = IDW(resid)` (k≈8, mocnina 2); `merged = max(0, radar + resid_field)`.
  Před tím QC: vyhoď `rain<0`, `rain>250`, MAD (k≈5), zaseknuté konstanty.
- **API30**: `Σ merged[d]·exp(−d/TAU)` (TAU≈12), `moisture = clip(API30/60, 0, 1)`.
- **Teplota**: `T_local = T_mean − 0.6·(elev−300)/100`; trojúhelníkové skóre
  s optimem 14 °C, rozsahem 5–25 °C.
- **Terén/les**: `aspect_factor = 0.82 + 0.18·northness`;
  `forest_factor = 0.35 + 0.65·forest01`.
- **Index (0–100)**: `(moisture·0.55 + temp·0.45)·aspect·forest·100`.
- **Verdikt**: ≥70 „Vyraž", ≥50 „Dá se", ≥32 „Počkej", jinak „Sucho".

## 9. Akceptační kritéria
1. `python scripts/run_daily.py` spočítá dnešek z reálných dat → `out/index.tif`,
   `out/index.png`, `out/localities.json`.
2. `--demo` běží bez tokenů/klíčů na syntetice (vývoj a CI).
3. Slévání přibližuje pole k měřákům a QC odfiltruje vadné stanice (testy).
4. Mapa: reálný index nad OSM, popsaná města, živé skóre lokalit, mobil.
5. Chybějící zdroj pipeline nezhavaruje – zaloguje a pokračuje.
6. Žádné klíče v gitu; vše přes `.env` a `config.yaml`.

## 10. Hranice (i do UI)
Družice nevidí houby pod korunami – model odhaduje *podmínky*, ne přítomnost.
Rozlišení počasí je hrubé; detail nese les, expozice a výška. Amatérské stanice
mají proměnlivou kvalitu – QC je povinné. V UI odděl „ostrá data" vs „demo".

---
*Plné původní zadání (milníky M0–M7, struktura repa, reference) je vstupem
projektu; tato verze v repu je jeho zhuštění pro orientaci. Rozhodnutí a
defaulty viz `DECISIONS.md`.*
