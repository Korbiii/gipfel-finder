"""Stufe 1b: Bahnhofs-Höhen – freie Open-Meteo Elevation-API (kein Key nötig).

Die Höhe jedes Bahnhofs dient in der Tabelle als Spalte „Start-Höhe“ und
ermöglicht eine grobe Abschätzung der zu erwartenden Höhenmeter einer Tour
(Gipfelhöhe − Bahnhofshöhe).

Mehrere Bahnhöfe werden zu EINER Anfrage gebündelt (latitude/longitude als
kommagetrennte Listen, ELEVATION_BATCH_SIZE in config.py). Schlägt ein Batch
fehl, werden die Bahnhöfe des Chunks einzeln nachgefragt.

Einzeln testen:   python -m pipeline.hoehen  [--force]
"""

from __future__ import annotations

import argparse
import sys
import time

import requests

import config
from . import cache
from .fahrplan import fetch_stationen


def _hoehen_holen(stationen: list) -> dict:
    """Höhen für mehrere Koordinaten in EINER Anfrage holen.

    stationen: [{station_id, lat, lon, name}, …]  →  {station_id: hoehe_m}
    """
    lat_str = ",".join(f"{bh['lat']:.5f}" for bh in stationen)
    lon_str = ",".join(f"{bh['lon']:.5f}" for bh in stationen)
    antwort = requests.get(
        config.ELEVATION_API_URL,
        params={"latitude": lat_str, "longitude": lon_str},
        timeout=config.ELEVATION_TIMEOUT_S,
        headers=config.REQUEST_HEADERS,
    )
    antwort.raise_for_status()
    daten = antwort.json()
    werte = daten.get("elevation") or []
    erg: dict = {}
    for bh, w in zip(stationen, werte):
        if w is not None:
            erg[bh["station_id"]] = float(w)
    return erg


def _hoehen_holen_retry(stationen: list, warnungen: list) -> dict:
    """Batch-Anfrage mit Retry; bei Fehler Fallback je Bahnhof einzeln."""
    letzter_fehler: Exception | None = None
    for versuch in range(config.OVERPASS_RETRIES + 1):
        try:
            return _hoehen_holen(stationen)
        except Exception as e:  # noqa: BLE001
            letzter_fehler = e
            if versuch < config.OVERPASS_RETRIES:
                time.sleep(2 * (versuch + 1))
    # Batch endgültig fehlgeschlagen → je Bahnhof einzeln versuchen
    warnungen.append(f"Höhen-Batch ({len(stationen)} Bahnhöfe): API-Fehler "
                     f"({letzter_fehler.__class__.__name__}) → einzeln nachgefragt")
    gesammelt: dict = {}
    for bh in stationen:
        try:
            gesammelt.update(_hoehen_holen([bh]))
        except Exception as e2:  # noqa: BLE001
            warnungen.append(f"{bh['name']}: Höhen-Fehler ({e2.__class__.__name__})")
    return gesammelt


def fetch_hoehen(force: bool = False, fortschritt=None) -> dict:
    """Stufe 1b: Bahnhofs-Höhen – nur fehlende abfragen (oder alle bei force)."""
    cache.init_db()
    bahnhoefe = cache.load_fahrplan()
    if not bahnhoefe:
        bahnhoefe = fetch_stationen(force=False)

    if force:
        beduerftig = [bh for bh in bahnhoefe
                      if bh.get("lat") is not None and bh.get("lon") is not None]
    else:
        beduerftig = [bh for bh in bahnhoefe
                      if bh.get("lat") is not None and bh.get("lon") is not None
                      and bh.get("hoehe_m") is None]

    if not beduerftig:
        # alles vorhanden → gecachte Werte liefern, ohne Netz
        return {bh["station_id"]: bh["hoehe_m"] for bh in bahnhoefe
                if bh.get("hoehe_m") is not None}

    warnungen: list = []
    sammeln: dict = {}
    batch_groesse = max(1, int(getattr(config, "ELEVATION_BATCH_SIZE", 40)))
    chunks = [beduerftig[i:i + batch_groesse]
              for i in range(0, len(beduerftig), batch_groesse)]
    gesamt = len(bahnhoefe)
    verarbeitet = 0
    for chunk in chunks:
        sammeln.update(_hoehen_holen_retry(chunk, warnungen))
        verarbeitet += len(chunk)
        if fortschritt:
            fortschritt(verarbeitet, gesamt, f"Höhen: Bahnhof {verarbeitet}/{gesamt}")

    if not sammeln:
        # kein Abbruch: die nächsten Stufen (Gipfel/Touren) sollen weiterlaufen
        # können, auch wenn die Höhen-API gerade nicht erreichbar ist.
        cache.save_bahnhof_hoehen({})
    else:
        cache.save_bahnhof_hoehen(sammeln)
    cache.save_warnings("hoehen", warnungen)
    cache.touch("hoehen")
    return sammeln


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Stufe 1b: Bahnhofs-Höhen (Open-Meteo Elevation)")
    parser.add_argument("--force", action="store_true",
                        help="Alle Bahnhöfe neu abfragen, gespeicherte Höhen ignorieren")
    args = parser.parse_args()
    try:
        hoehen = fetch_hoehen(force=args.force)
    except Exception as e:
        print(f"FEHLER: {e}")
        raise SystemExit(1)
    if not hoehen:
        print("FEHLER: keine Bahnhofshöhen ermittelbar (API evtl. nicht erreichbar).")
        raise SystemExit(1)
    gipfel = cache.load_gipfel()
    print(f"{len(hoehen)} Bahnhofshöhen gespeichert, {len(gipfel)} Gipfel im Cache:")
    bahnhoefe = cache.load_fahrplan()
    for bh in bahnhoefe[:10]:
        h = bh.get("hoehe_m")
        print(f"  {f'{h:>6.0f} m' if h is not None else '     — '}  {bh['name']}")


if __name__ == "__main__":
    main()