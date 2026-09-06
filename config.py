"""Zentrale Konfiguration für den Gipfel-Finder.

Startbahnhof, Fahrplan-Quelldatei, Radien, API-Zugänge und Cache-Pfad werden
hier gesetzt. Nach Änderungen wird automatisch ein neuer Konfigurations-
Fingerprint berechnet: vorhandene Cache-Tabellen der alten Konfiguration werden
dann ignoriert (nicht gelöscht), bis wieder "Neu suchen erzwingen" gedrückt wird.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Stufe 1: Startbahnhof
# ---------------------------------------------------------------------------
# Einfach diesen Block ersetzen, um einen anderen Ausgangspunkt zu nutzen.
START_STATION = {
    "name": "München Hbf",
    "lat": 48.140213,
    "lon": 11.557275,
}

# ---------------------------------------------------------------------------
# Stufe 1: Fahrplan (bewusst offline/manuell – aktuell gibt es keine
# zuverlässige öffentliche Bahn-API für Erreichbarkeiten)
# ---------------------------------------------------------------------------
# Die App liest NUR `daten/fahrplan.csv` (Semikolon-getrennt):
#     linie;bahnhof;fahrzeit_min;lat;lon
# Die Datei wird beim Import erzeugt (siehe `python -m pipeline.fahrplan --help`)
# oder einfach von Hand gepflegt. Sie muss Bahnhofs-Koordinaten enthalten;
# die fehlenden Koordinaten werden beim Import (einmalig) per Nominatim geholt.
FAHRPLAN_ORDNER = "daten"                              # PDF-/TXT-Dateien für den Import
FAHRPLAN_CSV = f"{FAHRPLAN_ORDNER}/fahrplan.csv"       # von der App gelesene Datei

# Koordinaten für Bahnhöfe ohne lat/lon in der CSV werden beim Import per
# OSM-Nominatim geholt (einmalig, danach komplett offline). Der Abstand wird
# politessehalber auf >= 1 Sekunde gesetzt (nur während --import, nie in der App).
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DELAY_S = 1.1

# Maximale Bahnfahrt ab Startbahnhof in Minuten (Stufe 1)
MAX_FAHRZEIT_MINUTEN = 90

# ---------------------------------------------------------------------------
# Stufe 1: GTFS-Import (optional, empfohlen)
# ---------------------------------------------------------------------------
# GTFS-Daten von gtfs.de (https://www.gtfs.de/) – frei erhältliche Basisversion:
# kompletter Schienenregionalverkehr Deutschlands (RB, RE, IRE, S-Bahn, nicht-
# bundeseigene Bahnen), tagesaktuell generiert, CC-BY. Damit lassen sich die
# erreichbaren Bahnhöfe ab START_STATION automatisch & sehr genau bestimmen.
#
# Einpflegen:  daten/gtfs.zip  liegt bereit  ODER  URL automatisch herunterladen
#              python -m pipeline.fahrplan --gtfs
# Der Importer liest routes/trips/stop_times/calendar und berechnet die
# kürzeste Reisezeit (inkl. Umstiege) ab dem Startbahnhof, dann schreibt er
# daten/fahrplan.csv (gilt danach wieder als „die Quelle der Wahrheit“).
#
# Weitere gtfs.de-Downloads (falls gewünscht): Germany_gesamt = de_full,
# Regionalverkehr = rv_free, Nahverkehr = nv_free, Fernverkehr = fv_free.
GTFS_URL = "https://download.gtfs.de/germany/rv_free/latest.zip"
GTFS_ORDNER = "daten/gtfs"               # entpackte GTFS-Dateien
GTFS_ZIP = "daten/gtfs.zip"              # heruntergeladene Archiv-Datei
GTFS_TIMEOUT_S = 300                     # Download-Timeout (Zip ~12 MB)
GTFS_UMSTIEGSWARTE_MIN = 15              # max. Wartezeit beim Umsteigen
GTFS_MAX_UMSTIEGE = 2                    # Umstiegsobergrenze je Verbindung

# Kandidaten: nur Haltestellen in diesem Radius (km) um den Startbahnhof werden
# als Start-/Umstiegs-Kandidaten betrachtet (Performance, da rv_free global ist).
GTFS_START_RADIUS_KM = 50

# ---------------------------------------------------------------------------
# Stufe 2: Gipfel (Overpass API / OpenStreetMap)
# ---------------------------------------------------------------------------
OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
GIPFEL_RADIUS_M = 8000          # Radius um jeden Bahnhof (Luftlinie)
OVERPASS_TIMEOUT_S = 60         # Timeout pro Overpass-Request
OVERPASS_RETRIES = 2            # Wiederholungen bei Fehler/Überlastung
OVERPASS_PAUSE_S = 1.0          # Abstand zwischen zwei (Batch-)Anfragen
# Anzahl Bahnhöfe, die in EINER Overpass-Anfrage abgefragt werden (Batching).
# Statt einer Anfrage pro Bahnhof (bei ~300 Bahnhöfen viel zu lange) werden
# so nur noch wenige Anfragen gestellt. 1 = klassischer Einzel-Modus.
OVERPASS_BATCH_SIZE = 40

# ---------------------------------------------------------------------------
# Stufe 3: Touren (Outdooractive Data API, optional)
# ---------------------------------------------------------------------------
# Falls leer, werden ausschließlich Fallback-Suchlinks genutzt
# (Komoot, Outdooractive-Web, Alpenverein aktiv) – kein Absturz.
# Wenn du einen Key hast: hier eintragen, z. B.  OUTDOORACTIVE_API_KEY = "dein-key"
OUTDOORACTIVE_API_KEY = ""   # leer = nur Fallback-Links
OUTDOORACTIVE_API_BASE = "https://api.outdooractive.com/api"
OUTDOORACTIVE_TOUR_RADIUS_M = 15_000
OUTDOORACTIVE_TIMEOUT_S = 20

# ---------------------------------------------------------------------------
# Sonstiges
# ---------------------------------------------------------------------------
CACHE_DB_PATH = "cache.db"
HTTP_TIMEOUT_S = 20
REQUEST_HEADERS = {
    "User-Agent": "gipfel-finder-muenchen/1.0 (lokale Hobby-Anwendung)"
}

# Bahnhofs-Höhen (für die Spalte „Start-Höhe“ und den ungefähren Aufstieg).
# Kostenlose Open-Meteo-Elevation-API, KEIN Key nötig; nur einmalig beim
# Fahrplan-Import bzw. bei „Neu suchen erzwingen → Nur Bahnhofshöhen“ abgefragt
# und danach komplett in cache.db gespeichert.
ELEVATION_API_URL = "https://api.open-meteo.com/v1/elevation"
ELEVATION_BATCH_SIZE = 40         # Koordinaten je Anfrage (Open-Meteo erlaubt viele)
ELEVATION_TIMEOUT_S = 30