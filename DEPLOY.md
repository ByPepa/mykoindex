# Nasazení – jak to rozjet, ať se to denně aktualizuje samo

Pipeline je server-side: **jednou denně** stáhne data ze stanic, spočítá index,
zapíše ho do 30denní historie a vyexportuje do `out/`. Web (`web/index.html`) je
statický a jen zobrazuje, co je v `out/`. Historie „kde bylo ideálně" žije na
disku (nebo v cache CI) a starší 30 dní se maže sama.

## Porovnání variant

| | GitHub Pages + Actions | Vlastní server / VPS | Sdílený webhosting (FTP) |
|---|---|---|---|
| Cena | **zdarma** (veřejné repo) | ~100–150 Kč/měs | podle tarifu |
| Vlastní server? | ne | ano | ano (bez Pythonu) |
| Denní běh | GitHub cron | systém cron / systemd | běží jinde, nahrává FTP |
| Historie | GitHub cache | disk serveru | musí se nahrávat |
| Údržba | **žádná** | ty (OS, nginx, aktualizace) | minimální |
| Náročnost | **nízká** | střední | střední |

### 👉 Doporučení: GitHub Pages + Actions
Nejjednodušší a zdarma, bez serveru a bez údržby. Nahraješ repo na GitHub,
přidáš Netatmo tokeny do Secrets, zapneš Pages – a hotovo, aktualizuje se samo.
Pipeline běží ~5–6 min/den (bohatě v limitu; veřejné repo = neomezené minuty).

---

## A) GitHub Pages + Actions (doporučeno)

1. Vytvoř na GitHubu **veřejné** repo a nahraj tento projekt:
   ```bash
   git init && git add . && git commit -m "Mykoindex"
   git branch -M main
   git remote add origin https://github.com/<ty>/<repo>.git
   git push -u origin main
   ```
   (`.env` se nenahraje – je v `.gitignore`.)

2. **Settings → Secrets and variables → Actions → New repository secret** přidej:
   - `NETATMO_CLIENT_ID`, `NETATMO_CLIENT_SECRET`, `NETATMO_REFRESH_TOKEN`
   - (volitelně `CDS_API_KEY`, `CDS_API_URL` – jen když chceš teplotu z ERA5;
     jinak jede z ČHMÚ+Netatmo stanic bez klíče)

3. **Settings → Pages → Source: GitHub Actions.**

4. Hotovo. Workflow [`.github/workflows/daily.yml`](.github/workflows/daily.yml)
   běží denně 04:30 UTC (nebo ručně přes **Actions → Run workflow**), stáhne
   statické vrstvy jen poprvé (cache), spočítá index, přičte den do historie a
   publikuje na `https://<ty>.github.io/<repo>/`.

Poznámky:
- Bez Netatmo secrets běží workflow v **demo** režimu (syntetika) – dobré na
  odzkoušení. S tokeny přepne na **live**.
- Historie a statické vrstvy přežívají mezi běhy přes `actions/cache`.

---

## B) Vlastní server / VPS (cron + nginx)

Pro plnou kontrolu. Připraveno v `deploy/`.

1. Na serveru:
   ```bash
   git clone <repo> /opt/mykoindex && cd /opt/mykoindex
   python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
   cp .env.example .env    # vyplň Netatmo tokeny
   ```
2. **Web root + nginx**: viz [`deploy/nginx.conf`](deploy/nginx.conf)
   (uprav `server_name` a cestu; publikuje se do `/var/www/mykoindex`).
3. **Denní běh** – buď cron:
   ```
   30 4 * * *  MYKOINDEX_WEBROOT=/var/www/mykoindex /opt/mykoindex/deploy/run_and_publish.sh >> /var/log/mykoindex.log 2>&1
   ```
   nebo systemd timer: [`deploy/mykoindex.service`](deploy/mykoindex.service) +
   [`deploy/mykoindex.timer`](deploy/mykoindex.timer)
   (`sudo cp deploy/mykoindex.* /etc/systemd/system/ && sudo systemctl enable --now mykoindex.timer`).
4. Ověř ručně: `MYKOINDEX_WEBROOT=/var/www/mykoindex ./deploy/run_and_publish.sh`

Historie žije v `data/history/` na disku serveru a maže se sama po 30 dnech.

---

## C) Sdílený webhosting bez Pythonu (FTP)

Pipeline pouštěj jinde (u sebe na PC v cronu, nebo přes GitHub Actions) a na
hosting nahrávej jen hotová data:
- nahraj **jednou** `web/index.html` do web rootu,
- denně nahraj přes FTP obsah `out/` (`localities.json`, `index.png`,
  `ideal30.png`, `history.json`) do `out/` na hostingu.

Historie se musí nahrávat spolu (`out/history.json` + lokální `data/history/`
drž u sebe, ať navazuje). Nejméně pohodlná varianta – zvaž raději A).
