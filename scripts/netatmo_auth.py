#!/usr/bin/env python3
"""Jednorázové získání Netatmo refresh_tokenu (OAuth2 authorization_code).

Netatmo zrušil grant password (2022), takže je nutný jednorázový souhlas
v prohlížeči. Postup:

  1) Na https://dev.netatmo.com založ aplikaci → CLIENT_ID a CLIENT_SECRET,
     do "redirect URI" dej:  http://localhost:8765/callback
  2) Vyplň je do .env (NETATMO_CLIENT_ID / NETATMO_CLIENT_SECRET).
  3) Spusť:  python scripts/netatmo_auth.py
  4) Otevře se prohlížeč se souhlasem, po potvrzení se vypíše refresh_token.
  5) Vlož ho do .env jako NETATMO_REFRESH_TOKEN.

Scope: read_station (stačí na getpublicdata).
"""
from __future__ import annotations

import http.server
import os
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mykoindex.config import load_config  # noqa: E402

REDIRECT_URI = "http://localhost:8765/callback"
AUTH_URL = "https://api.netatmo.com/oauth2/authorize"
TOKEN_URL = "https://api.netatmo.com/oauth2/token"
SCOPE = "read_station"

_result: dict = {}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        _result.update({k: v[0] for k, v in params.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h2>Hotovo, vrať se do terminálu.</h2>".encode("utf-8"))

    def log_message(self, *_):  # ticho
        pass


def main() -> int:
    cfg = load_config()
    cid = cfg.env.get("NETATMO_CLIENT_ID")
    secret = cfg.env.get("NETATMO_CLIENT_SECRET")
    if not (cid and secret):
        print("Chybí NETATMO_CLIENT_ID / NETATMO_CLIENT_SECRET v .env")
        return 1

    state = secrets.token_urlsafe(16)
    auth = AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": cid,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
            "response_type": "code",
        }
    )
    print("Otevírám prohlížeč pro souhlas…\n", auth)
    webbrowser.open(auth)

    server = http.server.HTTPServer(("localhost", 8765), _Handler)
    server.handle_request()  # počkej na jeden callback

    if _result.get("state") != state:
        print("Nesouhlasí state (možný CSRF) – zkus znovu.")
        return 1
    code = _result.get("code")
    if not code:
        print("Nepřišel authorization code:", _result)
        return 1

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": cid,
            "client_secret": secret,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
        },
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    print("\n=== ULOŽ DO .env ===")
    print("NETATMO_REFRESH_TOKEN=" + tok.get("refresh_token", "(chybí)"))
    print("====================")
    print("(access_token vyprší; refresh_token je trvalý, používá ho pipeline.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
