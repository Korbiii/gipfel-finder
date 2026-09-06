"""Tests für pipeline/daten.py: Datenmodell für Flask-API & statischen Export."""

from __future__ import annotations

from pipeline import cache, daten


def test_api_daten_berechnet_aufstieg_korrekt(isolierte_cache_db):
    cache.save_fahrplan([{"station_id": "GAP", "name": "Garmisch-Partenkirchen",
                          "lat": 47.49, "lon": 11.09, "fahrzeit_min": 70,
                          "linie": "RB", "hoehe_m": 707.0}])
    cache.save_gipfel([{"osm_id": "node/1", "name": "Alpspitze", "hoehe_m": 2628,
                        "lat": 47.43, "lon": 11.05, "bahnhof_id": "GAP",
                        "bahnhof_name": "Garmisch-Partenkirchen", "fahrzeit_min": 70}])
    cache.save_touren({})

    ergebnis = daten.api_daten()

    assert ergebnis["summen"]["gipfel"] == 1
    assert ergebnis["summen"]["fahrplan"] == 1
    gipfel = ergebnis["gipfel"][0]
    assert gipfel["bahnhof_hoehe_m"] == 707.0
    assert gipfel["aufstieg_m"] == 1921  # 2628 - 707


def test_api_daten_aufstieg_none_ohne_hoehen(isolierte_cache_db):
    cache.save_fahrplan([{"station_id": "GAP", "name": "Garmisch-Partenkirchen",
                          "lat": 47.49, "lon": 11.09, "fahrzeit_min": 70,
                          "linie": "RB"}])  # keine hoehe_m
    cache.save_gipfel([{"osm_id": "node/1", "name": "Alpspitze", "hoehe_m": 2628,
                        "lat": 47.43, "lon": 11.05, "bahnhof_id": "GAP",
                        "bahnhof_name": "Garmisch-Partenkirchen", "fahrzeit_min": 70}])
    cache.save_touren({})

    ergebnis = daten.api_daten()

    assert ergebnis["gipfel"][0]["aufstieg_m"] is None


def test_stufen_status_zeigt_alle_vier_stufen(isolierte_cache_db):
    status = daten.stufen_status()
    assert set(status.keys()) == {"fahrplan", "hoehen", "gipfel", "touren"}
    assert status["fahrplan"]["vorhanden"] is False
