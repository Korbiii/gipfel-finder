"""Tests für pipeline/cache.py: Konfig-Fingerprint, Zeitformatierung, Speichern/Laden."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pipeline import cache


def test_config_key_ist_stabil_bei_gleicher_konfiguration():
    """Zweimaliger Aufruf ohne Änderungen muss denselben Fingerprint liefern."""
    assert cache.config_key() == cache.config_key()


def test_config_key_aendert_sich_mit_der_konfiguration(monkeypatch):
    key_vorher = cache.config_key()
    monkeypatch.setattr(cache.config, "MAX_FAHRZEIT_MINUTEN", 999)
    key_nachher = cache.config_key()
    assert key_vorher != key_nachher


def test_human_ago_gerade_eben():
    jetzt = datetime.now(timezone.utc)
    assert cache.human_ago(jetzt.isoformat(timespec="seconds"), jetzt=jetzt) == "gerade eben"


def test_human_ago_vor_minuten():
    jetzt = datetime.now(timezone.utc)
    vorher = (jetzt - timedelta(minutes=5)).isoformat(timespec="seconds")
    assert cache.human_ago(vorher, jetzt=jetzt) == "vor 5 Min."


def test_human_ago_vor_stunden():
    jetzt = datetime.now(timezone.utc)
    vorher = (jetzt - timedelta(hours=3)).isoformat(timespec="seconds")
    assert cache.human_ago(vorher, jetzt=jetzt) == "vor 3 Std."


def test_human_ago_vor_tagen():
    jetzt = datetime.now(timezone.utc)
    vorher = (jetzt - timedelta(days=4)).isoformat(timespec="seconds")
    assert cache.human_ago(vorher, jetzt=jetzt) == "vor 4 Tagen"


def test_human_ago_ohne_wert():
    assert cache.human_ago(None) == "noch nie"


def test_fahrplan_speichern_und_laden_roundtrip(isolierte_cache_db):
    zeilen = [
        {"station_id": "A", "name": "Bahnhof A", "lat": 48.1, "lon": 11.5,
         "fahrzeit_min": 10, "linie": "RB1"},
        {"station_id": "B", "name": "Bahnhof B", "lat": 48.2, "lon": 11.6,
         "fahrzeit_min": 5, "linie": "RB2"},
    ]
    cache.save_fahrplan(zeilen)
    geladen = cache.load_fahrplan()
    # sortiert nach Fahrzeit aufsteigend -> Bahnhof B (5 Min.) zuerst
    assert [z["station_id"] for z in geladen] == ["B", "A"]


def test_stage_filled_ist_false_ohne_daten(isolierte_cache_db):
    assert cache.stage_filled("fahrplan") is False


def test_stage_filled_ist_true_nach_speichern(isolierte_cache_db):
    cache.save_fahrplan([{"station_id": "A", "name": "A", "lat": 1, "lon": 1,
                          "fahrzeit_min": 1, "linie": ""}])
    assert cache.stage_filled("fahrplan") is True


def test_warnungen_speichern_und_laden(isolierte_cache_db):
    cache.save_warnings("gipfel", ["Warnung 1", "Warnung 2"])
    assert cache.load_warnings("gipfel") == ["Warnung 1", "Warnung 2"]


def test_warnungen_leer_ohne_speichern(isolierte_cache_db):
    assert cache.load_warnings("gipfel") == []
