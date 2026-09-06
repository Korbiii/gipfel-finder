"""SQLite-Cache für alle drei Pipeline-Stufen (cache.db, reine Standardbibliothek).

Jede Stufe bekommt eine eigene Tabelle mit `last_fetched`:

  fahrplan   – Stufe 1: erreichbare Bahnhöfe (aus daten/fahrplan.csv)
  gipfel     – Stufe 2: Gipfel (dedupliziert, mit Ausgangsbahnhof)
  touren     – Stufe 3: Touren-Links pro Gipfel
  meta       – Konfigurations-Fingerprint, Zeitstempel, Warnungen

Schlüsselidee: Jede Zeile wird mit einem `config_key` getaggt – der SHA1-Hash
der relevanten Einstellungen in config.py (Startbahnhof, Radien, Keys) INKLUSIVE
des Inhalts von daten/fahrplan.csv (Dateiinhalt wird gehasht). Ändert sich die
Konfiguration ODER der Fahrplan, gelten alte Zeilen als veraltet und werden von
den Lade-Funktionen ignoriert. So können niemals Daten einer anderen
Konfiguration angezeigt werden, ohne dass irgendeine API-Abfrage nötig ist.

Hinweis zum Umbau: Die alte Tabelle heißt weiterhin `fahrplan` (vorher hielt
der Cache die Bahnhöfe unter `bahnhoefe`). Alte `bahnhoefe`-Zeilen der
Bahn-API-Ära bleiben unberührt in cache.db liegen und stören nicht.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config

_SCHEMA = {
    "fahrplan": """
        CREATE TABLE IF NOT EXISTS fahrplan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id TEXT NOT NULL,
            name TEXT NOT NULL,
            lat REAL,
            lon REAL,
            fahrzeit_min INTEGER,
            linie TEXT,
            hoehe_m REAL,
            config_key TEXT NOT NULL,
            last_fetched TEXT NOT NULL
        )
    """,
    "gipfel": """
        CREATE TABLE IF NOT EXISTS gipfel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            osm_id TEXT NOT NULL,
            name TEXT NOT NULL,
            hoehe_m INTEGER,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            bahnhof_id TEXT,
            bahnhof_name TEXT,
            fahrzeit_min INTEGER,
            config_key TEXT NOT NULL,
            last_fetched TEXT NOT NULL,
            UNIQUE (osm_id, config_key)
        )
    """,
    "touren": """
        CREATE TABLE IF NOT EXISTS touren (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            osm_id TEXT NOT NULL,
            quelle TEXT NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            config_key TEXT NOT NULL,
            last_fetched TEXT NOT NULL
        )
    """,
    "meta": """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """,
}
# ---------------------------------------------------------------------------
# Grundlagen
# ---------------------------------------------------------------------------

def _verbinden() -> sqlite3.Connection:
    conn = sqlite3.connect(config.CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Legt die Tabellen an, falls cache.db noch nicht existiert."""
    conn = _verbinden()
    try:
        for ddl in _SCHEMA.values():
            conn.execute(ddl)
        _migriere(conn)
        conn.commit()
    finally:
        conn.close()


def _migriere(conn: sqlite3.Connection) -> None:
    """Führt fehlende Spalten für ältere Datenbanken nach (z. B. hoehe_m)."""
    vorhanden = {r[1] for r in conn.execute("PRAGMA table_info(fahrplan)")}
    if "hoehe_m" not in vorhanden:
        conn.execute("ALTER TABLE fahrplan ADD COLUMN hoehe_m REAL")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _meta_lesen(schluessel: str) -> str | None:
    conn = _verbinden()
    try:
        zeile = conn.execute("SELECT value FROM meta WHERE key=?", (schluessel,)).fetchone()
        return zeile["value"] if zeile else None
    finally:
        conn.close()


def _meta_schreiben(schluessel: str, wert: str) -> None:
    conn = _verbinden()
    try:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (schluessel, wert))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Konfigurations-Fingerprint
# ---------------------------------------------------------------------------

def config_info() -> dict:
    """Alle Einstellungen, die sich auf den Cache auswirken (als JSON-hashbar)."""
    info = {
        "start_station": config.START_STATION,
        "fahrplan_csv": str(config.FAHRPLAN_CSV),
        "max_fahrzeit_min": config.MAX_FAHRZEIT_MINUTEN,
        "gipfel_radius_m": config.GIPFEL_RADIUS_M,
        "overpass_url": config.OVERPASS_API_URL,
        "oa_key": config.OUTDOORACTIVE_API_KEY,
        "oa_basis": config.OUTDOORACTIVE_API_BASE,
        "oa_radius_m": config.OUTDOORACTIVE_TOUR_RADIUS_M,
    }
    # Inhalts-Hash der Fahrplan-CSV: Ändert sich die CSV (neues Update), gilt
    # der alte Cache automatisch als veraltet – ohne dass jemand config.py anfasst.
    try:
        pfad = Path(config.FAHRPLAN_CSV)
        if pfad.exists():
            info["fahrplan_csv_sha1"] = hashlib.sha1(
                pfad.read_bytes()).hexdigest()
    except OSError:
        pass
    return info


def config_key() -> str:
    """Hash der Konfiguration – Trenner zwischen alten und neuen Cache-Zeilen."""
    text = json.dumps(config_info(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(text).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Cache-Status (für die UI: "Zuletzt aktualisiert: vor 3 Tagen")
# ---------------------------------------------------------------------------

def stage_filled(stufe: str) -> bool:
    """True, wenn für die aktuelle Konfiguration schon Daten der Stufe existieren.

    „hoehen“ ist eine Zusatz-Stufe ohne eigene Tabelle: sie gilt als gefüllt,
    wenn ALLE Bahnhöfe der aktuellen Konfiguration eine Höhe haben.
    """
    init_db()
    key = config_key()
    if stufe == "hoehen":
        conn = _verbinden()
        try:
            fehlend = conn.execute(
                "SELECT 1 FROM fahrplan WHERE config_key=? AND hoehe_m IS NULL LIMIT 1",
                (key,),
            ).fetchone()
            return fehlend is None
        finally:
            conn.close()
    conn = _verbinden()
    try:
        zeile = conn.execute(
            f"SELECT 1 FROM {stufe} WHERE config_key=? LIMIT 1", (key,)
        ).fetchone()
        return zeile is not None
    finally:
        conn.close()


def get_last_fetched(stufe: str) -> str | None:
    """ISO-Zeitstempel der letzten Aktualisierung der Stufe (oder None)."""
    init_db()
    key = config_key()
    return _meta_lesen(f"last_fetched:{stufe}:{key}")


def touch(stufe: str) -> None:
    """Merkt sich den aktuellen Zeitpunkt als letzte Aktualisierung der Stufe."""
    init_db()
    key = config_key()
    _meta_schreiben(f"last_fetched:{stufe}:{key}", _jetzt())


def save_warnings(stufe: str, warnungen: list) -> None:
    init_db()
    key = config_key()
    _meta_schreiben(f"warnings:{stufe}:{key}", json.dumps(warnungen, ensure_ascii=False))


def load_warnings(stufe: str) -> list:
    init_db()
    key = config_key()
    roh = _meta_lesen(f"warnings:{stufe}:{key}")
    if not roh:
        return []
    try:
        return json.loads(roh)
    except ValueError:
        return []


def human_ago(iso: str | None, jetzt: datetime | None = None) -> str:
    """Macht aus einem ISO-Zeitstempel eine deutsche Relativangabe."""
    if not iso:
        return "noch nie"
    try:
        ts = datetime.fromisoformat(iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return iso
    jetzt = jetzt or datetime.now(timezone.utc)
    sek = (jetzt - ts).total_seconds()
    if sek < 60:
        return "gerade eben"
    minuten = int(sek // 60)
    if minuten < 60:
        return f"vor {minuten} Min."
    stunden = minuten // 60
    if stunden < 24:
        return f"vor {stunden} Std."
    tage = stunden // 24
    if tage < 30:
        return f"vor {tage} Tagen"
    return ts.strftime("%d.%m.%Y")
# ---------------------------------------------------------------------------
# Stufe 1: Fahrplan (Bahnhöfe aus daten/fahrplan.csv)
# ---------------------------------------------------------------------------

def save_fahrplan(zeilen: list) -> None:
    """zeilen: [{station_id, name, lat, lon, fahrzeit_min, linie, [hoehe_m]}, …]"""
    init_db()
    key = config_key()
    jetzt = _jetzt()
    conn = _verbinden()
    try:
        conn.execute("DELETE FROM fahrplan WHERE config_key=?", (key,))
        conn.executemany(
            "INSERT INTO fahrplan(station_id,name,lat,lon,fahrzeit_min,linie,hoehe_m,config_key,last_fetched)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            [(r["station_id"], r["name"], r.get("lat"), r.get("lon"),
              r.get("fahrzeit_min"), r.get("linie"), r.get("hoehe_m"), key, jetzt)
             for r in zeilen],
        )
        conn.commit()
    finally:
        conn.close()


def save_bahnhof_hoehen(hoehen_by_station_id: dict) -> int:
    """Setzt die Höhen für Bahnhöfe der aktuellen Konfiguration.

    hoehen_by_station_id: {station_id: hoehe_m}  →  Anzahl aktualisierter Zeilen.
    """
    init_db()
    key = config_key()
    conn = _verbinden()
    try:
        anz = 0
        for station_id, hoehe in hoehen_by_station_id.items():
            cur = conn.execute(
                "UPDATE fahrplan SET hoehe_m=? WHERE station_id=? AND config_key=?",
                (hoehe, station_id, key),
            )
            anz += cur.rowcount
        conn.commit()
        return anz
    finally:
        conn.close()


def load_fahrplan() -> list:
    init_db()
    key = config_key()
    conn = _verbinden()
    try:
        zeilen = conn.execute(
            "SELECT station_id,name,lat,lon,fahrzeit_min,linie,hoehe_m FROM fahrplan"
            " WHERE config_key=? ORDER BY (fahrzeit_min IS NULL), fahrzeit_min, name",
            (key,),
        ).fetchall()
        return [dict(z) for z in zeilen]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stufe 2: Gipfel
# ---------------------------------------------------------------------------

def save_gipfel(zeilen: list) -> None:
    init_db()
    key = config_key()
    jetzt = _jetzt()
    conn = _verbinden()
    try:
        conn.execute("DELETE FROM gipfel WHERE config_key=?", (key,))
        conn.executemany(
            "INSERT OR REPLACE INTO gipfel"
            "(osm_id,name,hoehe_m,lat,lon,bahnhof_id,bahnhof_name,fahrzeit_min,config_key,last_fetched)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(r["osm_id"], r["name"], r.get("hoehe_m"), r["lat"], r["lon"],
              r.get("bahnhof_id"), r.get("bahnhof_name"), r.get("fahrzeit_min"), key, jetzt)
             for r in zeilen],
        )
        conn.commit()
    finally:
        conn.close()


def load_gipfel() -> list:
    init_db()
    key = config_key()
    conn = _verbinden()
    try:
        zeilen = conn.execute(
            "SELECT osm_id,name,hoehe_m,lat,lon,bahnhof_id,bahnhof_name,fahrzeit_min"
            " FROM gipfel WHERE config_key=?"
            " ORDER BY (hoehe_m IS NULL), hoehe_m DESC, name",
            (key,),
        ).fetchall()
        return [dict(z) for z in zeilen]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stufe 3: Touren
# ---------------------------------------------------------------------------

def save_touren(alle: dict) -> None:
    """alle: {osm_id: [{"quelle","name","url"}, ...]}"""
    init_db()
    key = config_key()
    jetzt = _jetzt()
    conn = _verbinden()
    try:
        conn.execute("DELETE FROM touren WHERE config_key=?", (key,))
        zeilen = []
        for osm_id, links in alle.items():
            for l in links:
                zeilen.append((osm_id, l["quelle"], l["name"], l["url"], key, jetzt))
        conn.executemany(
            "INSERT INTO touren(osm_id,quelle,name,url,config_key,last_fetched)"
            " VALUES(?,?,?,?,?,?)",
            zeilen,
        )
        conn.commit()
    finally:
        conn.close()


def load_touren() -> dict:
    """{osm_id: [{"quelle","name","url"}, ...]}"""
    init_db()
    key = config_key()
    conn = _verbinden()
    try:
        zeilen = conn.execute(
            "SELECT osm_id,quelle,name,url FROM touren WHERE config_key=? ORDER BY name",
            (key,),
        ).fetchall()
        ausgabe: dict = {}
        for z in zeilen:
            ausgabe.setdefault(z["osm_id"], []).append(
                {"quelle": z["quelle"], "name": z["name"], "url": z["url"]}
            )
        return ausgabe
    finally:
        conn.close()