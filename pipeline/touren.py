"""Stufe 3: Tourenvorschläge pro Gipfel.

Primär: Outdooractive Data API (Suche von Touren nahe der Gipfelkoordinaten).
Sofern kein Key gesetzt ist oder die Anfrage fehlschlägt, werden automatisch
Fallback-Suchlinks erzeugt (Komoot, Outdooractive-Web, Alpenverein aktiv,
Google-Maps-Anreise) – alle drei gipfelspezifisch. Die Suchlinks werden
bewusst IMMER zusätzlich gespeichert, damit die Seite auch ohne OA-Key und
bei einem API-Ausfall voll nutzbar bleibt.

Einzeln testen:   python -m pipeline.touren  [--force]
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import quote

import requests

import config
from . import cache
from .gipfel import fetch_gipfel


def _oa_tour_links(gipfel: dict) -> list:
    """Outdooractive Data API: Touren in der Nähe des Gipfels.

    Hinweis: Der genaue Endpunkt der OA-Data-API unterscheidet sich je nach
    Anbieter-Projekt. Der Standard-Pfad ist hier hinterlegt; falls dein Zugang
    einen anderen Pfad nutzt, einfach OUTDOORACTIVE_API_BASE in config.py
    anpassen. Fehler führen nie zum Abbruch – es gibt immer die Fallback-Links.
    """
    url = config.OUTDOORACTIVE_API_BASE.rstrip("/") + "/tour/search.json"
    params = {
        "key": config.OUTDOORACTIVE_API_KEY,
        "latitude": gipfel["lat"],
        "longitude": gipfel["lon"],
        "radius": config.OUTDOORACTIVE_TOUR_RADIUS_M,
        "limit": 5,
    }
    antwort = requests.get(url, params=params, timeout=config.OUTDOORACTIVE_TIMEOUT_S,
                           headers=config.REQUEST_HEADERS)
    antwort.raise_for_status()
    daten = antwort.json()
    tours = (daten.get("tours") or daten.get("data") or daten.get("results")) or []
    links = []
    for t in tours[:5]:
        name = t.get("title") or t.get("name") or "Tour"
        if isinstance(t.get("links"), dict):
            url_t = t.get("url") or t["links"].get("frontend")
        else:
            url_t = t.get("url")
        if not url_t:
            continue
        links.append({"quelle": "outdooractive-api", "name": name, "url": url_t})
    return links


def _fallback_links(gipfel: dict) -> list:
    """Immer nutzbare gipfelspezifische Suchlinks (kein API-Key nötig)."""
    name = quote(gipfel["name"])
    lat, lon = f"{gipfel['lat']:.5f}", f"{gipfel['lon']:.5f}"
    start = quote(config.START_STATION["name"])
    return [
        {"quelle": "komoot", "name": "Komoot-Touren in der Nähe",
         "url": f"https://www.komoot.de/smarttour/d?sport=hiking&lat={lat}&lon={lon}&zoom=12"},
        {"quelle": "outdooractive", "name": "Outdooractive (Websuche)",
         "url": f"https://www.outdooractive.com/de/suche/?q={name}"},
        {"quelle": "alpenvereinaktiv", "name": "Alpenverein aktiv (Suche)",
         "url": f"https://www.alpenvereinaktiv.com/de/suche/?q={name}"},
        {"quelle": "anreise", "name": "Anreise (Google Maps)",
         "url": f"https://www.google.com/maps/dir/{start}/{lat},{lon}"},
    ]


def fetch_touren(force: bool = False, fortschritt=None) -> dict:
    """Stufe 3: Touren-Links pro Gipfel – Cache oder frisch von OA/Fallback."""
    cache.init_db()
    if not force and cache.stage_filled("touren"):
        return cache.load_touren()

    gipfel = cache.load_gipfel()
    if not gipfel:
        gipfel = fetch_gipfel(force=False)  # Pipeline automatisch aufziehen

    warnungen: list = []
    alle: dict = {}
    totals = len(gipfel)
    for idx, g in enumerate(gipfel, start=1):
        links = []
        if config.OUTDOORACTIVE_API_KEY:
            try:
                links += _oa_tour_links(g)
            except Exception as e:
                warnungen.append(f"{g['name']}: Outdooractive-Abfrage fehlgeschlagen, "
                                 f"nur Fallback-Links ({e.__class__.__name__})")
        links += _fallback_links(g)
        alle[g["osm_id"]] = links
        if fortschritt:
            fortschritt(idx, totals, f"Touren: {idx}/{totals} ({g['name']})")

    if not alle:
        raise RuntimeError("Touren: es gibt keine Gipfel, für die Touren gesucht werden könnten.")
    cache.save_touren(alle)
    cache.save_warnings("touren", warnungen)
    cache.touch("touren")
    return alle


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Stufe 3: Touren (Outdooractive/Fallback)")
    parser.add_argument("--force", action="store_true", help="API neu abfragen, Cache ignorieren")
    args = parser.parse_args()
    try:
        resultat = fetch_touren(force=args.force)
    except Exception as e:
        print(f"FEHLER: {e}")
        raise SystemExit(1)
    gipfel = cache.load_gipfel()
    if config.OUTDOORACTIVE_API_KEY:
        print("Outdooractive-Key ist gesetzt (API-Versuche aktiv).")
    else:
        print("Kein Outdooractive-Key gesetzt -> nur Fallback-Links.")
    print(f"{len(resultat)} Gipfel mit {sum(len(v) for v in resultat.values())} Touren-Links.")
    for g in gipfel[:10]:
        links = resultat.get(g["osm_id"], [])
        print(f"  {g['name']}: {len(links)} Links")


if __name__ == "__main__":
    main()