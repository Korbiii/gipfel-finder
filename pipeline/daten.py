"""Aufbereitung der Cache-Daten für die Flask-API und den statischen Web-Export.

Die Funktionen hier sind die gemeinsame Quelle für das Datenmodell, das sowohl
die Flask-API (`/api/daten`) als auch der statische Export
(`pipeline/export_web.py`) ausliefert – damit weichen die beiden nie voneinander ab.
"""

from __future__ import annotations

import config
from pipeline import cache

STUFEN = ("fahrplan", "hoehen", "gipfel", "touren")


def stufen_status() -> dict:
    """Vorhanden + „Zuletzt aktualisiert“ pro Stufe für die aktuelle Konfiguration."""
    return {
        s: {
            "vorhanden": cache.stage_filled(s),
            "zuletzt": cache.human_ago(cache.get_last_fetched(s)),
            "warnungen": cache.load_warnings(s),
        }
        for s in STUFEN
    }


def api_daten() -> dict:
    bahnhoefe = cache.load_fahrplan()
    gipfel = cache.load_gipfel()
    touren = cache.load_touren()
    bahnhof_hoehe = {b["station_id"]: b.get("hoehe_m") for b in bahnhoefe}
    for g in gipfel:
        g["touren"] = touren.get(g["osm_id"], [])
        g["bahnhof_hoehe_m"] = bahnhof_hoehe.get(g.get("bahnhof_id"))
        if g.get("hoehe_m") is not None and g["bahnhof_hoehe_m"] is not None:
            # grobe Abschätzung der Höhenmeter (Luftlinie Bahnhof → Gipfel)
            g["aufstieg_m"] = max(0, int(round(g["hoehe_m"] - g["bahnhof_hoehe_m"])))
        else:
            g["aufstieg_m"] = None
    return {
        "konfig": {
            "start_name": config.START_STATION["name"],
            "max_fahrzeit_min": config.MAX_FAHRZEIT_MINUTEN,
            "gipfel_radius_km": config.GIPFEL_RADIUS_M // 1000,
            "fahrplan_csv": config.FAHRPLAN_CSV,
            "oa_key_aktiv": bool(config.OUTDOORACTIVE_API_KEY),
        },
        "stufen": stufen_status(),
        "bahnhoefe": bahnhoefe,
        "summen": {
            "fahrplan": len(bahnhoefe),
            "gipfel": len(gipfel),
            "hoehen": sum(1 for b in bahnhoefe if b.get("hoehe_m") is not None),
        },
        "gipfel": gipfel,
    }