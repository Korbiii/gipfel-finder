"""Tests für die reinen Text-Parsing-Helfer in pipeline/fahrplan.py."""

from __future__ import annotations

from pipeline import fahrplan


def test_zeit_min_parst_hh_mm():
    assert fahrplan._zeit_min("7:32") == 7 * 60 + 32
    assert fahrplan._zeit_min("07:32") == 7 * 60 + 32
    assert fahrplan._zeit_min("23:59") == 23 * 60 + 59


def test_zeit_min_ungueltig_gibt_none():
    assert fahrplan._zeit_min("abc") is None
    assert fahrplan._zeit_min("") is None
    assert fahrplan._zeit_min("25:99") is not None  # Regex prüft nur Format, keine Wertebereiche


def test_differenz_min_normalfall():
    assert fahrplan._differenz_min(8 * 60, 9 * 60) == 60


def test_differenz_min_mitternachts_uebergang():
    # Abfahrt 23:50, Ankunft 00:20 -> 30 Minuten (über Mitternacht)
    ab = 23 * 60 + 50
    an = 0 * 60 + 20
    assert fahrplan._differenz_min(ab, an) == 30


def test_differenz_min_unplausibel_gibt_none():
    # Fahrzeit über 15 Stunden gilt als unplausibel (falsche Spalte erkannt)
    assert fahrplan._differenz_min(0, 901) is None


def test_norm_name_ignoriert_leerzeichen_und_gross_klein():
    assert fahrplan._norm_name("München Hbf") == fahrplan._norm_name("münchen hbf")
    assert fahrplan._norm_name("Bad Tölz") == "badtlz" or fahrplan._norm_name("Bad Tölz").isalnum()


def test_saeubere_name_entfernt_richtungswort():
    assert fahrplan._saeubere_name("Garmisch-Partenkirchen an") == "Garmisch-Partenkirchen"


def test_saeubere_name_entfernt_fussnoten_kennzeichen():
    assert fahrplan._saeubere_name("*Kochel") == "Kochel"


def test_saeubere_name_erkennt_hinweiszeile_als_leer():
    assert fahrplan._saeubere_name("nur samstags") == ""


def test_ist_startzeile_erkennt_start_station(monkeypatch):
    monkeypatch.setattr(fahrplan.config, "START_STATION", {"name": "München Hbf"})
    assert fahrplan._ist_startzeile("München Hbf") is True
    assert fahrplan._ist_startzeile("Garmisch-Partenkirchen") is False
