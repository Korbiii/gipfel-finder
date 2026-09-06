"""Tests für Distanzberechnung & Bahnhof-Zuordnung in pipeline/gipfel.py."""

from __future__ import annotations

from pipeline import gipfel


def test_abstand_m_gleicher_punkt_ist_null():
    assert gipfel._abstand_m(48.1, 11.5, 48.1, 11.5) == 0


def test_abstand_m_plausibler_wert():
    # München Hbf -> Marienplatz, Luftlinie ca. 1,5 km
    distanz = gipfel._abstand_m(48.140213, 11.557275, 48.137222, 11.575556)
    assert 1000 < distanz < 2000


def test_station_zum_gipfel_waehlt_naechste_station(monkeypatch):
    monkeypatch.setattr(gipfel.config, "GIPFEL_RADIUS_M", 8000)
    gipfel_dict = {"lat": 47.43, "lon": 11.05}
    stationen = [
        {"station_id": "weit", "name": "Weit weg", "lat": 47.0, "lon": 10.0,
         "fahrzeit_min": 50},
        {"station_id": "nah", "name": "Garmisch-Partenkirchen", "lat": 47.4925,
         "lon": 11.0956, "fahrzeit_min": 70},
    ]
    treffer = gipfel._station_zum_gipfel(gipfel_dict, stationen)
    assert treffer is True
    assert gipfel_dict["bahnhof_id"] == "nah"


def test_station_zum_gipfel_ohne_treffer_bei_zu_grosser_distanz(monkeypatch):
    monkeypatch.setattr(gipfel.config, "GIPFEL_RADIUS_M", 100)
    gipfel_dict = {"lat": 47.43, "lon": 11.05}
    stationen = [{"station_id": "weit", "name": "Weit weg", "lat": 40.0,
                  "lon": 10.0, "fahrzeit_min": 50}]
    assert gipfel._station_zum_gipfel(gipfel_dict, stationen) is False


def test_station_zum_gipfel_ueberspringt_fehlende_koordinaten(monkeypatch):
    monkeypatch.setattr(gipfel.config, "GIPFEL_RADIUS_M", 8000)
    gipfel_dict = {"lat": 47.43, "lon": 11.05}
    stationen = [
        {"station_id": "ohne_koords", "name": "Ohne Koordinaten", "lat": None,
         "lon": None, "fahrzeit_min": 10},
        {"station_id": "nah", "name": "Garmisch-Partenkirchen", "lat": 47.4925,
         "lon": 11.0956, "fahrzeit_min": 70},
    ]
    assert gipfel._station_zum_gipfel(gipfel_dict, stationen) is True
    assert gipfel_dict["bahnhof_id"] == "nah"
