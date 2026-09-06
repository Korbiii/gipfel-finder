"""Stufe 2: Gipfel pro Bahnhof – OpenStreetMap via Overpass API.

Pro Bahnhof wird ein Knoten-Query rund um die Bahnhofskoordinaten gestellt:
    node(around:8000,lat,lon)["natural"="peak"]["name"]

Performance: Statt EINER Anfrage pro Bahnhof (bei ~300 Bahnhöfen ein Engpass)
werden mehrere Bahnhöfe zu EINER Overpass-Anfrage gebündelt (Vereinigung mehrerer
`around`-Klauseln, siehe OVERPASS_BATCH_SIZE in config.py). Die Zuordnung
„Gipfel → Bahnhof“ erfolgt danach lokal per Luftlinien-Distanz (Haversine); pro
Gipfel gewinnt der Bahnhof mit der kürzesten Fahrtzeit, bei Gleichstand der
alphabetisch erste Name (identische Rangfolge wie zuvor). Schlägt eine
Batch-Anfrage fehl, werden die Bahnhöfe des Chunks einzeln nachgefragt.

Einzeln testen:   python -m pipeline.gipfel  [--force]
"""

from __future__ import annotations

import argparse
import math
import sys
import time

import requests

import config
from . import cache
from .fahrplan import fetch_stationen

_QUERY = (
    "[out:json][timeout:{timeout}];"
    "node(around:{radius},{lat:.5f},{lon:.5f})[\"natural\"=\"peak\"][\"name\"];"
    "out body;"
)


def _overpass_holen(lat: float, lon: float) -> dict:
    """Overpass-Query mit Retry (max. OVERPASS_RETRIES Wiederholungen)."""
    query = _QUERY.format(timeout=config.OVERPASS_TIMEOUT_S,
                          radius=config.GIPFEL_RADIUS_M, lat=lat, lon=lon)
    letzter_fehler: Exception | None = None
    for versuch in range(config.OVERPASS_RETRIES + 1):
        try:
            antwort = requests.post(config.OVERPASS_API_URL, data={"data": query},
                                    timeout=config.OVERPASS_TIMEOUT_S + 10,
                                    headers=config.REQUEST_HEADERS)
            antwort.raise_for_status()
            return antwort.json()
        except requests.RequestException as e:
            letzter_fehler = e
            if versuch < config.OVERPASS_RETRIES:
                time.sleep(3 * (versuch + 1))  # kurzes Backoff, dann erneut
    raise letzter_fehler  # type: ignore[misc]


def _batch_query(stationen: list) -> str:
    """Eine Overpass-Anfrage für mehrere Bahnhöfe (Union mehrerer around-Klauseln)."""
    klauseln = []
    for bh in stationen:
        klauseln.append(
            f'node(around:{config.GIPFEL_RADIUS_M},{bh["lat"]:.5f},{bh["lon"]:.5f})'
            '["natural"="peak"]["name"];'
        )
    union = "(\n  " + "\n  ".join(klauseln) + "\n);"
    return (f"[out:json][timeout:{config.OVERPASS_TIMEOUT_S}];\n"
            f"{union}\n"
            "out body;")


def _overpass_batch_holen(stationen: list) -> dict:
    """Batch-Overpass-Query mit Retry (max. OVERPASS_RETRIES Wiederholungen)."""
    query = _batch_query(stationen)
    letzter_fehler: Exception | None = None
    for versuch in range(config.OVERPASS_RETRIES + 1):
        try:
            antwort = requests.post(config.OVERPASS_API_URL, data={"data": query},
                                    timeout=config.OVERPASS_TIMEOUT_S + 30,
                                    headers=config.REQUEST_HEADERS)
            antwort.raise_for_status()
            return antwort.json()
        except requests.RequestException as e:
            letzter_fehler = e
            if versuch < config.OVERPASS_RETRIES:
                time.sleep(3 * (versuch + 1))  # kurzes Backoff, dann erneut
    raise letzter_fehler  # type: ignore[misc]


_ERDRADIUS_M = 6_371_008.8
_ZUORDNUNG_TOLERANZ_M = 250  # kleine Toleranz zur Around-Berechnung von Overpass


def _abstand_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Luftlinien-Distanz (Haversine) in Metern."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * _ERDRADIUS_M * math.asin(math.sqrt(a))


def _station_zum_gipfel(g: dict, stationen: list) -> bool:
    """Ordnet dem Gipfel den besten Bahnhof des Chunks zu (kürzeste Fahrtzeit).

    `stationen` muss nach (Fahrtzeit-None, Fahrtzeit, Name) sortiert sein –
    dieselbe Rangfolge wie load_fahrplan(), damit bei Gleichstand der Bahnhof
    mit dem alphabetisch ersten Namen gewinnt (Verhalten wie vor dem Batching).
    """
    radius = config.GIPFEL_RADIUS_M + _ZUORDNUNG_TOLERANZ_M
    for bh in stationen:
        lat, lon = bh.get("lat"), bh.get("lon")
        if lat is None or lon is None:
            continue
        if _abstand_m(g["lat"], g["lon"], lat, lon) > radius:
            continue
        g["fahrzeit_min"] = bh.get("fahrzeit_min")
        g["bahnhof_id"] = bh["station_id"]
        g["bahnhof_name"] = bh["name"]
        return True
    return False


def _verarbeite_station(bh: dict, warnungen: list, sammler: dict) -> None:
    """Einzelabfrage EINES Bahnhofs (Fallback / klassischer Modus)."""
    try:
        daten = _overpass_holen(bh["lat"], bh["lon"])
    except Exception as e:
        warnungen.append(f"{bh['name']}: Overpass-Fehler ({e.__class__.__name__})")
        return
    for g in _gipfel_aus_response(daten, bh.get("fahrzeit_min")):
        g["bahnhof_id"] = bh["station_id"]
        g["bahnhof_name"] = bh["name"]
        _sammle(sammler, g)


def _sammle(sammler: dict, g: dict) -> None:
    """Dedupe: Dieselbe Node-ID = derselbe Gipfel; mehrfach im Radius liegende
    Gipfel bekommen den Bahnhof mit der kürzesten Fahrtzeit als Ausgangspunkt."""
    alter = sammler.get(g["osm_id"])
    if alter is None:
        sammler[g["osm_id"]] = g
    elif g["fahrzeit_min"] is None:
        return  # neuer Bahnhof bietet keine bessere Zeit
    elif alter["fahrzeit_min"] is None or g["fahrzeit_min"] < alter["fahrzeit_min"]:
        sammler[g["osm_id"]] = g


def _gipfel_aus_response(daten: dict, fahrzeit_min: int | None) -> list:
    gipfel = []
    for el in daten.get("elements") or []:
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name or el.get("lat") is None or el.get("lon") is None:
            continue
        hoehe = tags.get("ele")
        gipfel.append({
            "osm_id": f'{el.get("type", "node")}/{el["id"]}',
            "name": name,
            "hoehe_m": int(float(hoehe)) if hoehe else None,
            "lat": el["lat"],
            "lon": el["lon"],
            "fahrzeit_min": fahrzeit_min,
        })
    return gipfel


def fetch_gipfel(force: bool = False, fortschritt=None) -> list:
    """Stufe 2: deduplizierte Gipfel pro Bahnhof – Cache oder frisch von Overpass.

    Mehrere Bahnhöfe werden zu EINER Overpass-Anfrage gebündelt
    (OVERPASS_BATCH_SIZE). Schlägt eine Batch-Anfrage fehl, werden die Bahnhöfe
    des Chunks einzeln nachgefragt – es geht so nie ein Bahnhof verloren.
    """
    cache.init_db()
    if not force and cache.stage_filled("gipfel"):
        return cache.load_gipfel()

    bahnhoefe = cache.load_fahrplan()
    if not bahnhoefe:
        # Pipeline automatisch aufziehen, falls Stufe 1 noch nie gelaufen ist
        bahnhoefe = fetch_stationen(force=False)

    warnungen: list = []
    sammler: dict = {}  # osm_id -> bester Datensatz
    gesamt = len(bahnhoefe)

    # Bahnhöfe ohne Koordinaten können nicht befragt werden (wie bisher).
    stationen = []
    for bh in bahnhoefe:
        if bh.get("lat") is None or bh.get("lon") is None:
            warnungen.append(f"{bh['name']}: keine Koordinaten → für Gipfelsuche übersprungen")
        else:
            stationen.append(bh)

    # Dieselbe Sortierung wie load_fahrplan(): kürzeste Fahrtzeit zuerst, dann
    # Name. Sie bestimmt, welcher Bahnhof bei gleicher Fahrtzeit gewinnt.
    stationen.sort(key=lambda bh: (bh.get("fahrzeit_min") is None,
                                   bh.get("fahrzeit_min") or 0,
                                   bh["name"]))

    batch_groesse = max(1, int(getattr(config, "OVERPASS_BATCH_SIZE", 1)))
    batching = batch_groesse > 1
    if batching:
        chunks = [stationen[i:i + batch_groesse]
                  for i in range(0, len(stationen), batch_groesse)]
    else:
        chunks = [[bh] for bh in stationen]

    verarbeitet = 0
    for chunk in chunks:
        if verarbeitet:
            time.sleep(config.OVERPASS_PAUSE_S)  # Rate-Limit: Overpass nicht überlasten
        if batching:
            try:
                daten = _overpass_batch_holen(chunk)
            except Exception as e:
                warnungen.append(f"Batch-Abfrage ({len(chunk)} Bahnhöfe): Overpass-Fehler "
                                 f"({e.__class__.__name__}) → einzeln nachgefragt")
                daten = None
            if daten is not None:
                # Die Zuordnung „welcher Bahnhof“ passiert lokal über die Distanz.
                for g in _gipfel_aus_response(daten, None):
                    if _station_zum_gipfel(g, chunk):
                        _sammle(sammler, g)
                verarbeitet += len(chunk)
                if fortschritt:
                    fortschritt(verarbeitet, gesamt, f"Gipfel: Bahnhof {verarbeitet}/{gesamt}")
                continue
            # sonst: Fallback – Bahnhöfe dieses Chunks einzeln abfragen
        for idx_chunk, bh in enumerate(chunk):
            if idx_chunk:
                time.sleep(config.OVERPASS_PAUSE_S)
            _verarbeite_station(bh, warnungen, sammler)
            verarbeitet += 1
            if fortschritt:
                fortschritt(verarbeitet, gesamt, f"Gipfel: Bahnhof {verarbeitet}/{gesamt}")

    ergebnisse = sorted(sammler.values(),
                        key=lambda g: (g["hoehe_m"] is None, -(g["hoehe_m"] or 0), g["name"]))
    if not ergebnisse:
        raise RuntimeError("Overpass: keine Gipfel gefunden (API evtl. überlastet "
                           "oder geändert).")
    cache.save_gipfel(ergebnisse)
    cache.save_warnings("gipfel", warnungen)
    cache.touch("gipfel")
    return ergebnisse


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Stufe 2: Gipfel (Overpass API)")
    parser.add_argument("--force", action="store_true", help="API neu abfragen, Cache ignorieren")
    args = parser.parse_args()
    try:
        gipfel = fetch_gipfel(force=args.force)
    except Exception as e:
        print(f"FEHLER: {e}")
        raise SystemExit(1)
    print(f"{len(gipfel)} Gipfel (nach Dedupe):")
    for g in sorted(gipfel, key=lambda x: (x["hoehe_m"] is None, -(x["hoehe_m"] or 0))):
        hoehe = f"{g['hoehe_m']} m" if g["hoehe_m"] is not None else "?"
        print(f"  {hoehe:>8}  {g['name']:<35} ab {g['bahnhof_name']}")


if __name__ == "__main__":
    main()