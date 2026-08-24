"""Zdroje dat: ČHMÚ MERGE, Netatmo, ERA5-Land, terén, les, GBIF.

Každý modul má reálný fetcher (s měkkým selháním – při výpadku loguje a
vrací None) a ``synthetic_*`` generátor pro ``--demo`` běh bez tokenů.
"""
