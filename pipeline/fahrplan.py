"""Stufe 1: Erreichbare Bahnhöfe aus einem manuell gepflegten Fahrplan.

Warum kein Live-Fahrplan mehr?
------------------------------
Die bisher genutzten Fahrplan-APIs (transport.rest / db-vendo-client, HAFAS)
sind unzuverlässig bzw. aktuell als "sinnvolle Bahn-API" nicht verfügbar. Auf
den Alpen-Zubringerstrecken ändert sich die Fahrzeit selten – ein manueller
Abgleich alle paar Monate reicht völlig (und ist mit einem neuen PDF schnell
erledigt). Deshalb liest die App jetzt eine einfache, von Hand pflegbare Datei.

Datenfluss:
  daten/*.fahrplan.pdf oder *.txt  →  python -m pipeline.fahrplan --import
                                                  →  daten/fahrplan.csv
  daten/fahrplan.csv               →  fetch_stationen()  (App / weitere Stufen)
                                                  →  cache.db (offline)

  Hinweis: Unterordner (z. B. daten/vorlagen/) werden vom Import ignoriert –
  dort kann man Muster-/Beispieldateien ablegen, ohne dass sie in die CSV kommen.

CSV-Format (Semikolon-getrennt), Spalten:
  linie;bahnhof;fahrzeit_min;lat;lon
  - fahrzeit_min = Minuten ab START_STATION (der Start selbst fehlt in der CSV).
  - lat/lon werden nur beim Import per OSM-Nominatim gesetzt; da die App alle
    Bahnhofs-Koordinaten braucht, fehlen ohne Import die Koordinaten (die
    betroffenen Bahnhöfe werden dann in der App übersprungen und gewarnt).

Einzeln testen / nutzen:
  python -m pipeline.fahrplan [--force]          # lese CSV → Cache
  python -m pipeline.fahrplan --import [...]     # PDF/TXT → CSV
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import requests

import config
from . import cache

# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------

_ZEIT = re.compile(r"^(\d{1,2}):(\d{2})$")

# Richtungs-/Halt-Kennzeichen am Ende einer Stationszeile (werden entfernt)
_RICHTUNG = {"ab", "an", "ank", "abf", "ankf"}

# Kennzeichen von Hinweis-/Fußnotenzeilen (werden übersprungen)
_HINWEIS_MARKER = ("(", ")", "*", "mo-fr", "sa+so", "sonn- und feiertags",
                   "täglich", "nur", "samstag", "sonntag", "werktags", "verkehrt")


def _norm_name(name: str) -> str:
    """Nur Buchstaben/Ziffern (für Namen-Vergleiche), kleingeschrieben."""
    return "".join(c for c in name.casefold() if c.isalnum())


def _ist_startzeile(name: str) -> bool:
    """True, wenn die Zeile den START_STATION aus config.py meint."""
    n, sn = _norm_name(name), _norm_name(config.START_STATION["name"])
    if not sn:
        return False
    return n == sn or (sn in n and len(n) <= len(sn) + 12)


def _saeubere_name(roh: str) -> str:
    """Fußnotenzeichen & Richtungs-Wörter aus einem Stationsnamen entfernen."""
    roh = roh.strip().lstrip("*!†")
    if any(m and m in roh.casefold() for m in _HINWEIS_MARKER):
        return ""
    teile = roh.split()
    while teile and teile[-1].casefold() in _RICHTUNG:
        teile.pop()
    name = " ".join(teile).strip()
    return name


def _zeit_min(wert: str) -> int | None:
    """"7:32" / "07:32" → Minuten seit Mitternacht (oder None)."""
    m = _ZEIT.match(wert)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _differenz_min(ab: int, an: int) -> int | None:
    """Fahrtzeit zwischen zwei Uhrzeiten (Minuten), mit Mitternachts-Übergang."""
    d = an - ab
    if d < -720:            # Zug fährt nach Mitternacht weiter
        d += 1440
    if d < 0 or d > 900:    # unplausibel (Fahrzeit > 15 h) → Zeile/palte passt nicht
        return None
    return d
_ZEIT_RE = re.compile(r"\d{1,2}:\d{2}")


def _zeilen_mit_zeiten(text: str) -> list:
    """Zeilen als (name, [(offset, uhrzeit), ...]).

    Der offset ist die Zeichenposition der Uhrzeit innerhalb der Zeile. Damit
    lassen sich PDF-Layout-Spalten (die beim Textextrahieren als Leerzeichen
    erhalten bleiben) dem richtigen Zug zuordnen.
    """
    zeilen = []
    for zeile in text.splitlines():
        if not zeile.strip():
            continue
        zeiten = [(m.start(), m.group(0))
                  for m in _ZEIT_RE.finditer(zeile)
                  if _ZEIT.match(m.group(0))]
        if not zeiten:
            continue  # z. B. Zugnummern-Zeile ohne Uhrzeiten
        name = _saeubere_name(zeile[:zeiten[0][0]])
        if name:
            zeilen.append((name, zeiten))
    return zeilen


def _parse_fahrplan_text(text: str) -> dict:
    """Fahrplan-Text (eine Seite/eine Richtung) → {bahnhof: fahrzeit_min}.

    Zeilenweise: Jede Zeile ist "Station   7:08  7:38 …", die Uhrzeiten stehen
    über ihre Position in exakt derselben Spalte wie die Abfahrtszeiten am
    Startbahnhof (Layout-Modus von pypdf bzw. ausgerichtete Textvorlagen).

    Vorgehen: Zeilen mit dem Startbahnhof bilden die „Spalten“ dieses
    Abschnitts (Richtungswechsel = neue Startzeile). Für jede Station wird jede
    Ankunftszeit über ihren Spalten-Offset der passenden Abfahrtszeit
    zugeordnet (monoton, damit auch Züge ohne Halt an verschobenen Spalten
    richtig landen); die Fahrzeit ist das Minimum der so gefundenen Differenzen.
    """
    zeilen = _zeilen_mit_zeiten(text)
    ergebnis: dict[str, int] = {}

    basis: list[tuple[int, int]] | None = None   # (spalten-offset, minuten) am Start
    for name, zeiten in zeilen:
        if _ist_startzeile(name):
            basis = [(off, mzt) for off, t in zeiten
                     if (mzt := _zeit_min(t)) is not None]
            continue
        if basis is None:
            continue  # Zeilen vor der ersten Startzeile (Kopf) ignorieren
        if len(basis) == 1:
            # nur ein Zug auf der Seite – einfache Differenz
            ab = basis[0][1]
            dauern = [d for _, t in zeiten
                      if (m := _zeit_min(t)) is not None
                      and (d := _differenz_min(ab, m)) is not None]
        else:
            dauern = []
            letzter_spalte = -1
            for off, t in zeiten:
                an = _zeit_min(t)
                if an is None:
                    continue
                kandidaten = [i for i in range(len(basis)) if i >= letzter_spalte] \
                    or list(range(len(basis)))
                spalte = min(kandidaten, key=lambda i: abs(basis[i][0] - off))
                letzter_spalte = spalte
                d = _differenz_min(basis[spalte][1], an)
                if d is not None:
                    dauern.append(d)
        if dauern:
            ergebnis[name] = min(dauern)
    return ergebnis


def _extract_pdf_text(pfad: Path) -> str:
    """Text aus einem Fahrplan-PDF (pypdf; Layout-Modus erhält Spalten)."""
    from pypdf import PdfReader

    reader = PdfReader(str(pfad))
    seiten = []
    for seite in reader.pages:
        try:
            text = seite.extract_text(extraction_mode="layout") or ""
        except Exception:
            text = ""
        if not text.strip():
            try:
                text = seite.extract_text() or ""
            except Exception:
                text = ""
        seiten.append(text)
    return "\n".join(seiten)


def _extract_text(pfad: Path) -> str:
    if pfad.suffix.lower() == ".pdf":
        return _extract_pdf_text(pfad)
    return pfad.read_text(encoding="utf-8-sig", errors="replace")


# ---------------------------------------------------------------------------
# Nominatim (einmalige Geokodierung beim Import; danach offline)
# ---------------------------------------------------------------------------

_session = requests.Session()
_nominatim_cache: dict[str, tuple[float, float] | None] = {}


def _geocode(bahnhof: str) -> tuple[float, float] | None:
    """liefert (lat, lon) für einen Bahnhofsnamen – oder None (auch gecacht)."""
    if bahnhof in _nominatim_cache:
        return _nominatim_cache[bahnhof]

    suchbegriffe = ([f"Bahnhof {bahnhof}"]
                    if "bahnhof" not in bahnhof.casefold() else [bahnhof])
    treffer: tuple[float, float] | None = None
    for begriff in suchbegriffe:
        try:
            antwort = _session.get(
                config.NOMINATIM_URL,
                params={"q": begriff, "format": "json", "limit": 1,
                        "countrycodes": "de", "accept-language": "de"},
                timeout=config.HTTP_TIMEOUT_S,
                headers=config.REQUEST_HEADERS,
            )
            roh = antwort.json()
            treffer = (float(roh[0]["lat"]), float(roh[0]["lon"])) if roh else None
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            treffer = None
        if treffer:
            break
        time.sleep(config.NOMINATIM_DELAY_S)   # politesse: max 1 Request/Sekunde
    _nominatim_cache[bahnhof] = treffer
    return treffer


# ---------------------------------------------------------------------------
# Import: PDF/TXT → daten/fahrplan.csv
# ---------------------------------------------------------------------------

def importiere(pfade: list[str] | None = None,
               geocoden: bool = True,
               ausgabe: str | None = None) -> list:
    """Liest Fahrplan-PDFs/TXTs, extrahiert Bahnhöfe + Fahrzeiten, schreibt CSV."""
    ausgabepfad = Path(ausgabe or config.FAHRPLAN_CSV)
    ausgabepfad.parent.mkdir(parents=True, exist_ok=True)

    if pfade:
        dateien = [Path(p) for p in pfade]
    else:
        dateien = sorted(
            p for p in Path(config.FAHRPLAN_ORDNER).glob("*")
            if p.suffix.lower() in (".pdf", ".txt")
        )
    if not dateien:
        raise RuntimeError(
            f"Keine Fahrplan-Dateien in '{config.FAHRPLAN_ORDNER}' gefunden. "
            "PDF/TXT dort ablegen oder per --pdf/--txt angeben."
        )

    gesamt: dict[str, dict] = {}
    for pfad in dateien:
        text = _extract_text(pfad)
        teilergebnis = _parse_fahrplan_text(text)
        if not teilergebnis:
            print(f"  ⚠ {pfad.name}: keine Stationszeilen mit Uhrzeiten gefunden "
                  f"(Startzeile '{config.START_STATION['name']}' nicht erkennbar?)")
            continue
        for name, fahrzeit in teilergebnis.items():
            eintrag = gesamt.setdefault(name, {"linien": set(), "fahrzeit_min": None})
            eintrag["linien"].add(pfad.stem)
            eintrag["fahrzeit_min"] = (fahrzeit
                                       if eintrag["fahrzeit_min"] is None
                                       else min(eintrag["fahrzeit_min"], fahrzeit))
        print(f"  ✓ {pfad.name}: {len(teilergebnis)} Bahnhöfe geparst")

    if not gesamt:
        raise RuntimeError("Import ergab keine Bahnhöfe – bitte Fahrplan-Quelle prüfen.")
# Koordinaten-Bestand aus einer früheren CSV übernehmen, Neue per Nominatim holen
    bereits: dict[str, tuple[float, float]] = {}
    if ausgabepfad.exists():
        for zeile in _csv_lesen(ausgabepfad):
            if zeile.get("lat") and zeile.get("lon") and zeile.get("bahnhof"):
                try:
                    bereits[zeile["bahnhof"]] = (float(zeile["lat"]), float(zeile["lon"]))
                except ValueError:
                    pass

    koord: dict[str, tuple[float, float] | None] = {}
    for name in gesamt:
        if name in bereits:
            koord[name] = bereits[name]
        elif geocoden:
            print(f"  … Koordinaten für {name!r} (Nominatim) …")
            koord[name] = _geocode(name)
        else:
            koord[name] = None

    zeilen = []
    for name in sorted(gesamt, key=lambda n: (gesamt[n]["fahrzeit_min"] or 0, n)):
        e = gesamt[name]
        lat, lon = koord.get(name) or (None, None)
        zeilen.append({
            "linie": "+".join(sorted(e["linien"])),
            "bahnhof": name,
            "fahrzeit_min": e["fahrzeit_min"],
            "lat": lat,
            "lon": lon,
        })

    # Manuell ergänzte Zeilen der alten CSV behalten (fehlen im neuen Fahrplan)
    for alt in (_csv_lesen(ausgabepfad) if ausgabepfad.exists() else []):
        if alt.get("bahnhof") and alt["bahnhof"] not in gesamt:
            zeilen.append({
                "linie": alt.get("linie") or "manuell",
                "bahnhof": alt["bahnhof"],
                "fahrzeit_min": alt.get("fahrzeit_min"),
                "lat": alt.get("lat"),
                "lon": alt.get("lon"),
            })

    _csv_schreiben(ausgabepfad, zeilen)
    fehlende = [name for name in gesamt if koord.get(name) is None]
    print()
    print(f"Fertig: {len(gesamt)} Bahnhöfe aus {len(dateien)} Datei(en) → {ausgabepfad}")
    if fehlende:
        print(f"⚠ Ohne Koordinaten ({len(fehlende)}): {', '.join(sorted(fehlende))} "
              "– per Nominatim nicht gefunden, bitte in der CSV nachtragen.")
    return zeilen


# ---------------------------------------------------------------------------
# CSV lesen/schreiben
# ---------------------------------------------------------------------------

def _csv_lesen(pfad: Path) -> list[dict]:
    if not pfad.exists():
        return []
    with pfad.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _csv_schreiben(pfad: Path, zeilen: list[dict]) -> None:
    with pfad.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["linie", "bahnhof", "fahrzeit_min",
                                               "lat", "lon"], delimiter=";")
        writer.writeheader()
        for z in zeilen:
            writer.writerow({
                "linie": z.get("linie") or "",
                "bahnhof": z["bahnhof"],
                "fahrzeit_min": z.get("fahrzeit_min"),
                "lat": (f"{z['lat']:.5f}" if z.get("lat") is not None else ""),
                "lon": (f"{z['lon']:.5f}" if z.get("lon") is not None else ""),
            })
# ---------------------------------------------------------------------------
# GTFS-Import (gtfs.de / GTFS-Static) → daten/fahrplan.csv
# ---------------------------------------------------------------------------
# gtfs.de stellt den kompletten Schienenregionalverkehr (RB/RE/IRE/S-Bahn)
# als tagesaktuelles GTFS-Zip bereit. Dieses Modul rechnet daraus die erreich-
# baren Bahnhöfe ab START_STATION aus (inkl. Umstiege) und schreibt sie in
# daten/fahrplan.csv – danach nutzt die App diese CSV wie gewohnt weiter.

def _norm_bahnname(name: str) -> str:
    """Normalisiert einen Bahnhofsnamen (Umlaute → ae/oe/ue, nur alnum)."""
    n = name.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    n = n.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    return "".join(c for c in n.casefold() if c.isalnum())


def _gtfs_min(zeit: str) -> int | None:
    """"07:32:00" oder "7:32" → Minuten seit Mitternacht (oder None)."""
    if not zeit:
        return None
    teile = zeit.split(":")
    try:
        return int(teile[0]) * 60 + int(teile[1])
    except (ValueError, IndexError):
        return None


def _naechster_montag() -> str:
    """YYYYMMDD des nächsten Montags (heute, wenn Montag) – Referenztag."""
    from datetime import date, timedelta
    heute = date.today()
    tage = (-heute.weekday()) % 7
    return (heute + timedelta(days=tage)).strftime("%Y%m%d")


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Luftlinie zwischen zwei Koordinaten in Kilometern."""
    from math import asin, cos, radians, sin, sqrt
    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = p2 - p1
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))

def _gtfs_download(url: str, ziel: Path) -> Path:
    """Lädt das GTFS-Zip von der gtfs.de-Download-URL herunter."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    print(f"  … lade GTFS von {url} …")
    antwort = requests.get(url, stream=True, timeout=config.GTFS_TIMEOUT_S,
                           headers=config.REQUEST_HEADERS)
    antwort.raise_for_status()
    with ziel.open("wb") as f:
        for chunk in antwort.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    print(f"  ✓ heruntergeladen: {ziel} ({ziel.stat().st_size / 1e6:.1f} MB)")
    return ziel
def _gtfs_lesen(zip_pfad: Path) -> dict:
    """Entpackt das GTFS-Zip und baut die für die Reisezeit nötigen Strukturen.

    Rückgabe: {
        "halte_je_trip":   [[(stop_id, ankunft_min, abfahrt_min), ...]]  (sortiert, je Trip)
        "stops":           {stop_id: (name, lat, lon)},
        "linie_je_trip":   [route_short_name ...]  (parallel zu halte_je_trip),
    }
    """
    import csv
    import io
    import zipfile
    from collections import defaultdict

    with zipfile.ZipFile(str(zip_pfad)) as z:
        namen = set(z.namelist())

        def _diktate(datei: str):
            """Liefert Zeilen einer GTFS-CSV-Datei als Dicts (Stream offen halten!)."""
            if datei not in namen:
                return
            with z.open(datei) as fh:
                reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
                for zeile in reader:
                    yield zeile

        stops: dict[str, tuple[str, float, float]] = {}
        for s in _diktate("stops.txt"):
            nm = (s.get("stop_name") or "").strip()
            lat, lon = s.get("stop_lat"), s.get("stop_lon")
            if nm and lat and lon:
                try:
                    stops[s["stop_id"]] = (nm, float(lat), float(lon))
                except ValueError:
                    continue

        # Nur Haltestellen in Riechweite des Startbahnhofs betrachten
        # (der Feed deckt ganz Deutschland ab – wir brauchen nur die Region).
        slat, slon = config.START_STATION["lat"], config.START_STATION["lon"]
        max_km = config.GTFS_START_RADIUS_KM
        region: set[str] = {sid for sid, (nm, la, lo) in stops.items()
                            if _haversine_km(slat, slon, la, lo) <= max_km}

        # Referenztag (nächster Montag): ein regulärer Werktag
        montag = _naechster_montag()
        service_am_montag: set[str] = set()
        for c in _diktate("calendar.txt"):
            if c.get("monday") == "1":
                service_am_montag.add(c["service_id"])
        for cd in _diktate("calendar_dates.txt"):
            if cd.get("date") != montag:
                continue
            if cd.get("exception_type") == "2":
                service_am_montag.discard(cd["service_id"])
            elif cd.get("exception_type") == "1":
                service_am_montag.add(cd["service_id"])
        if not service_am_montag:
            print("  ℹ kein calendar/calendar_dates gefunden – nehme alle Fahrten (Montag)")
            alle_fahrten = True
        else:
            alle_fahrten = False

        routes: dict[str, str] = {}
        for r in _diktate("routes.txt"):
            routes[r["route_id"]] = (r.get("route_short_name")
                                     or r.get("route_long_name")
                                     or r["route_id"])

        trip_route: dict[str, str] = {}
        for t in _diktate("trips.txt"):
            if alle_fahrten or t.get("service_id") in service_am_montag:
                trip_route[t["trip_id"]] = routes.get(t.get("route_id"), "?")

        # --- Zwei-Pass über stop_times --------------------------------
        # stop_times.txt ist riesig (ganz Deutschland). Wir lesen sie zweimal:
        #   Pass 1: finde die Trips, die in der Start-Region halten (Region
        #           dient nur zur Trip-Selektion, nicht als Ziel-Grenze!).
        #   Pass 2: sammle von diesen Trips ALLE Halte (egal wie weit weg),
        #           damit Ziele wie Garmisch (82 min) oder Mittenwald (88 min)
        #           trotzdem gefunden werden.
        startnahe_trips: set[str] = set()
        for st in _diktate("stop_times.txt"):
            if st.get("trip_id") in trip_route and st.get("stop_id") in region:
                startnahe_trips.add(st["trip_id"])

        halt_nach_trip: dict[str, list] = defaultdict(list)
        for st in _diktate("stop_times.txt"):
            if st.get("trip_id") not in startnahe_trips:
                continue
            sid = st.get("stop_id")
            if not sid:
                continue
            arr = _gtfs_min(st.get("arrival_time") or st.get("departure_time"))
            dep = _gtfs_min(st.get("departure_time") or st.get("arrival_time"))
            if arr is None:
                continue
            try:
                seq = int(st.get("stop_sequence", 0))
            except ValueError:
                seq = 0
            halt_nach_trip[st["trip_id"]].append((seq, sid, arr, dep))

        halte_je_trip: list[list] = []
        linien_je_trip: list[str] = []
        for trip_id, waren in halt_nach_trip.items():
            waren.sort(key=lambda x: x[0])
            halte_je_trip.append([(sid, arr, dep) for _, sid, arr, dep in waren])
            linien_je_trip.append(trip_route[trip_id])

    return {
        "halte_je_trip": halte_je_trip,
        "stops": stops,
        "linie_je_trip": linien_je_trip,
    }
def _gtfs_reisezeiten(struckt: dict) -> dict:
    """Berechnet die kuerzeste Reisezeit ab START_STATION zu jeder Haltestelle.

    Grundidee:
    - Jeder Trip (Zug) ist eine chronologisch sortierte Folge von Halten
      [(stop_id, ankunft, abfahrt), ...].
    - Fuer jeden Zug, der am Startbahnhof haelt, sind alle spaeteren Halte
      direkt erreichbar (Reisezeit = Ankunft-Ziel − Abfahrt-Start).
    - Zusaetzlich wird umgestiegen: an einem Zwischenhalt mit Wartezeit ≤
      GTFS_UMSTIEGSWARTE_MIN beginnt ein Folge-Zug; die Umstiegszahl ist auf
      GTFS_MAX_UMSTIEGE begrenzt.

    Damit auch spaet abfahrende, schnellere Zuege nicht verloren gehen, wird
    pro Zustand (stop, umstiege) eine Menge nicht-dominierter Pareto-Labels
    gefuehrt: Ein Label (reisezeit, ankunft_abs) wird nur verworfen, wenn ein
    anderes in BEIDEN Werten besser/gleich ist. So bleiben z. B. ein frueher
    langsamer S-Bahn-Zug UND ein spaeter schneller RB55 erhalten.

    Rueckgabe: {stop_id: min_reisezeit_min}
    """
    halte_je_trip = struckt["halte_je_trip"]

    start_ids = set()
    slnm = _norm_bahnname(config.START_STATION["name"])
    for stop_id, (nm, _la, _lo) in struckt["stops"].items():
        if _norm_bahnname(nm) == slnm or slnm in _norm_bahnname(nm):
            start_ids.add(stop_id)
    if not start_ids:
        raise RuntimeError(
            f"START_STATION '{config.START_STATION['name']}' nicht in den "
            "GTFS-Haltestellen gefunden – bitte Name in config.py pruefen.")

    # abfahrt_je_stop: {stop_id: [(minuten, trip_ix), ...]}  (sortiert)
    from collections import defaultdict
    abfahrt_je_stop: dict[str, list] = defaultdict(list)
    for ix, halte in enumerate(halte_je_trip):
        for sid, _an, dep in halte:
            if dep is not None:
                abfahrt_je_stop[sid].append((dep, ix))
    for liste in abfahrt_je_stop.values():
        liste.sort()

    # Pareto-Label-Verwaltung: labeln[schluessel] = Liste von (reisezeit, ankunft_abs)
    labeln: dict[tuple[str, int], list[tuple[int, int]]] = defaultdict(list)

    def _dominiert(schluessel: tuple[str, int], rz: int, ankunft: int) -> bool:
        """True, wenn schon ein Label existiert, das in beiden Werten besser ist."""
        for andere_rz, andere_ank in labeln[schluessel]:
            if andere_rz <= rz and andere_ank <= ankunft:
                return True
        return False

    def _einfuegen(schluessel: tuple[str, int], rz: int, ankunft: int) -> bool:
        """Fuegt ein Label ein, entfernt von ihm dominierte. True wenn neu."""
        if _dominiert(schluessel, rz, ankunft):
            return False
        labeln[schluessel] = [(a, b) for a, b in labeln[schluessel]
                              if not (rz <= a and ankunft <= b)]
        labeln[schluessel].append((rz, ankunft))
        return True

    import heapq
    # Heap-Eintrag: (ankunft_abs, reisezeit, umstiege, stop_id, trip_ix)
    heap: list[tuple[int, int, int, str, int]] = []
    for start_id in sorted(start_ids):
        for dep, trip_ix in abfahrt_je_stop.get(start_id, []):
            heapq.heappush(heap, (dep, 0, 0, start_id, trip_ix))

    best: dict[str, int] = {}
    while heap:
        ankunft_abs, rz, umst, sid, trip_ix = heapq.heappop(heap)

        gesehen_start = False
        for zsid, an, abf in halte_je_trip[trip_ix]:
            if zsid == sid and not gesehen_start:
                gesehen_start = True
                continue            # hier steigen wir ein
            if not gesehen_start:
                continue
            if an < ankunft_abs:
                continue            # Halt zeitlich vor unserem Einstieg
            dauer = an - ankunft_abs + rz   # Reisezeit = Einstiegszeit + Fahrzeit im Zug
            if best.get(zsid) is None or dauer < best[zsid]:
                best[zsid] = dauer
            # Umstieg auf Anschluss-Zuege, solange Budget reicht
            if umst < config.GTFS_MAX_UMSTIEGE:
                for abn, nix in abfahrt_je_stop.get(zsid, []):
                    if abn < an or abn - an > config.GTFS_UMSTIEGSWARTE_MIN:
                        continue
                    neue_rz = dauer + (abn - an)   # Reisezeit inkl. Wartezeit
                    if _einfuegen((zsid, umst + 1), neue_rz, abn):
                        heapq.heappush(heap, (abn, neue_rz, umst + 1, zsid, nix))

    # Dedupe/Filter: > MAX_FAHRZEIT raus
    ergebnis = {sid: z for sid, z in best.items() if z is not None and z <= config.MAX_FAHRZEIT_MINUTEN}
    return ergebnis

def _stations_schluessel(name: str) -> str:
    """Gruppierung für die Zusammenführung von Bahnsteig-Haltestellen.

    "München Hbf (tief)" und "München Hbf Gl.27-36" gehören zum selben Bahnhof
    wie "München Hbf" – Klammern, "Gl."-Angaben und ähnliche Zusätze werden
    entfernt, bevor normalisiert wird.
    """
    import re as _re
    kern = _re.split(r"\s*[\(\[/]", name)[0]
    kern = _re.sub(r"\sGl\..*$", "", kern, flags=_re.IGNORECASE)
    kern = _re.sub(r"\s+Pbf$", "", kern.strip(), flags=_re.IGNORECASE)
    return _norm_bahnname(kern)


def importiere_gtfs(url: str | None = None, zip_pfad: str | None = None,
                    ausgabe: str | None = None) -> list:
    """GTFS-Daten einpflegen → daten/fahrplan.csv (die App-Quelle).

    - url:    gtfs.de-Download-URL (Default: config.GTFS_URL)
    - zip_pfad: vorhandene GTFS-Zip statt Download nutzen
    - ausgabe: Ziel-CSV (Default: config.FAHRPLAN_CSV)
    """
    pfad = Path(zip_pfad) if zip_pfad else Path(config.GTFS_ZIP)
    if not pfad.exists():
        pfad = _gtfs_download(url or config.GTFS_URL, pfad)

    print("  ✓ GTFS-Dateien einlesen …")
    geholt = _gtfs_lesen(pfad)
    print(f"      Trips/Halte geladen: {len(geholt['halte_je_trip'])}, "
          f"Haltestellen: {len(geholt['stops'])}")

    reisezeiten = _gtfs_reisezeiten(geholt)

    # Bahnsteig-Haltestellen (z. B. "München Hbf", "München Hbf (tief)",
    # "München Hbf Gl.27-36") zu EINEM Bahnhof zusammenführen: Je normalisiertem
    # Bahnhofsnamen wird die kürzeste Fahrzeit behalten (Koordinaten des
    # schnellsten Eintrags). So bleibt die Liste kompakt und Stufe 2 (Overpass)
    # macht pro Bahnhof nur EINE Anfrage.
    stops = geholt["stops"]
    bester_je_name: dict[str, dict] = {}
    for stop_id, dauer in reisezeiten.items():
        nm, lat, lon = stops.get(stop_id, (stop_id, None, None))
        if not nm:
            continue
        if dauer is None or dauer > config.MAX_FAHRZEIT_MINUTEN:
            continue
        schluessel = _stations_schluessel(nm)
        alter = bester_je_name.get(schluessel)
        if alter is None or dauer < alter["fahrzeit_min"]:
            bester_je_name[schluessel] = {
                "linie": "GTFS",        # Regionalverkehr-Datensatz (gtfs.de)
                "bahnhof": nm,
                "fahrzeit_min": dauer,
                "lat": lat,
                "lon": lon,
            }

    zeilen = sorted(bester_je_name.values(),
                    key=lambda r: (r["fahrzeit_min"], r["bahnhof"]))
    if not zeilen:
        raise RuntimeError("GTFS-Import ergab keine erreichbaren Bahnhöfe – "
                           "START_STATION evtl. nicht in den Daten?")

    _csv_schreiben(Path(ausgabe or config.FAHRPLAN_CSV), zeilen)
    print(f"  ✓ {len(zeilen)} erreichbare Bahnhöfe → {ausgabe or config.FAHRPLAN_CSV}")
    return zeilen


def fetch_stationen(force: bool = False, fortschritt=None) -> list:
    """Stufe 1: erreichbare Bahnhöfe – aus dem Cache oder aus der CSV.

    Die CSV ist die manuell gepflegte Quelle (siehe Modul-Docstring). Liegt
    keine Datei vor, gibt es eine klare Meldung mit dem Import-Befehl.
    """
    cache.init_db()
    if not force and cache.stage_filled("fahrplan"):
        return cache.load_fahrplan()

    csv_pfad = Path(config.FAHRPLAN_CSV)
    if not csv_pfad.exists():
        raise RuntimeError(
            "Keine Fahrplan-Datei vorhanden. Leg bitte zuerst einen Fahrplan an:\n"
            f"  1) {config.FAHRPLAN_ORDNER}/  mit einem Fahrplan-PDF oder .txt füllen\n"
            f"  2) python -m pipeline.fahrplan --import → erzeugt {config.FAHRPLAN_CSV}\n"
            "  3) Danach hier erneut „Neu suchen erzwingen“ klicken."
        )

    warnungen: list = []
    buecher = _csv_lesen(csv_pfad)
    ausgabe: list = []
    for zeile in buecher:
        name = (zeile.get("bahnhof") or "").strip()
        if not name or _ist_startzeile(name):
            continue
        try:
            fahrzeit = int(zeile.get("fahrzeit_min"))
        except (TypeError, ValueError):
            warnungen.append(f"{name}: ungültige Fahrzeit ({zeile.get('fahrzeit_min')!r}), "
                             "übersprungen")
            continue
        if fahrzeit > config.MAX_FAHRZEIT_MINUTEN:
            warnungen.append(f"{name}: {fahrzeit} Min. ab {config.START_STATION['name']} "
                             f"(über {config.MAX_FAHRZEIT_MINUTEN}) → nicht in der Liste")
            continue
        lat = float(zeile["lat"]) if zeile.get("lat") not in (None, "") else None
        lon = float(zeile["lon"]) if zeile.get("lon") not in (None, "") else None
        if lat is None or lon is None:
            warnungen.append(f"{name}: keine Koordinaten in der CSV → ohne Gipfelsuche")
        ausgabe.append({
            "station_id": name,
            "name": name,
            "lat": lat,
            "lon": lon,
            "fahrzeit_min": fahrzeit,
            "linie": (zeile.get("linie") or ""),
        })

    if not ausgabe:
        raise RuntimeError("Fahrplan-CSV enthält keine verwertbaren Bahnhöfe.")

    ausgabe.sort(key=lambda r: (r["fahrzeit_min"] or 0, r["name"]))
    cache.save_fahrplan(ausgabe)
    cache.save_warnings("fahrplan", warnungen)
    cache.touch("fahrplan")
    if fortschritt:
        fortschritt(1, 1, f"Fahrplan eingelesen: {len(ausgabe)} Bahnhöfe")
    return ausgabe


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(
        description="Stufe 1: Fahrplan einpflegen (PDF/TXT oder GTFS) → daten/fahrplan.csv und Cache")
    parser.add_argument("--import", dest="importieren", action="store_true",
                        help="Fahrplan-PDFs/TXTs aus daten/ einlesen und fahrplan.csv schreiben")
    parser.add_argument("--pdf", nargs="+", metavar="DATEI",
                        help="zusätzliche Fahrplan-PDF(s) für den Import")
    parser.add_argument("--txt", nargs="+", metavar="DATEI",
                        help="zusätzliche Fahrplan-Textdatei(en) für den Import")
    parser.add_argument("--gtfs", dest="gtfs", action="store_true",
                        help="GTFS-Daten von gtfs.de (Regionalverkehr) einpflegen → fahrplan.csv")
    parser.add_argument("--gtfs-url", dest="gtfs_url", metavar="URL",
                        help="GTFS-Download-URL (Default: config.GTFS_URL)")
    parser.add_argument("--gtfs-zip", dest="gtfs_zip", metavar="DATEI",
                        help="Vorhandenes GTFS-Zip verwenden (statt Download)")
    parser.add_argument("--no-geocode", action="store_true",
                        help="Beim Import KEINE Nominatim-Geokodierung (Offline-Betrieb)")
    parser.add_argument("--force", action="store_true",
                        help="Cache ignorieren, frisch aus der CSV lesen")
    args = parser.parse_args()

    try:
        if args.gtfs:
            zeilen = importiere_gtfs(url=args.gtfs_url, zip_pfad=args.gtfs_zip)
            print(f"GTFS-Import fertig: {len(zeilen)} erreichbare Bahnhöfe "
                  f"(≤ {config.MAX_FAHRZEIT_MINUTEN} Min.)")
        elif args.importieren:
            pfade = list(args.pdf or []) + list(args.txt or [])
            zeilen = importiere(pfade=pfade or None,
                                geocoden=not args.no_geocode)
            print(f"Folgende Stationen werden von Stufe 2 genutzt (≤ "
                  f"{config.MAX_FAHRZEIT_MINUTEN} Min.):")
            for z in zeilen:
                if z["fahrzeit_min"] is not None and \
                        z["fahrzeit_min"] <= config.MAX_FAHRZEIT_MINUTEN:
                    print(f"  {z['fahrzeit_min']:>4} min  {z['bahnhof']}")
        else:
            stationen = fetch_stationen(force=args.force)
            print(f"{len(stationen)} erreichbare Bahnhöfe (aus {config.FAHRPLAN_CSV}):")
            for s in stationen:
                print(f"  {s['fahrzeit_min']} min  {s['name']}  ({s['linie'] or 'ohne Linie'})")
    except Exception as e:
        print(f"FEHLER: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
