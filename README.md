# ⛰️ Gipfel-Finder (München)

Lokale Webanwendung, die alle Berggipfel auflistet, die innerhalb von **90 Minuten
Bahnfahrt ab München Hbf** erreichbar sind – inklusive Tourvorschlägen pro Gipfel.

```
python app.py   →   http://127.0.0.1:5000
```

Die Fahrtzeiten kommen **bewusst nicht mehr aus einer Live-Bahn-API** (die bisherigen
Schnittstellen wie `transport.rest` / HAFAS / db-vendo-client sind unzuverlässig und
aktuell nicht als „sinnvolle Bahn-API“ verfügbar). Stattdessen liest die App eine
**von Hand gepflegte Fahrplan-CSV** (`daten/fahrplan.csv`) – einmal erstellt, danach
komplett offline. Da sich die Fahrzeiten auf den Alpen-Zubringerstrecken nur selten
ändern, reicht es, die Datei **alle paar Monate** mit einem frischen Fahrplan-PDF zu
aktualisieren.

## Projektstruktur

```
├── app.py                # Flask-Server + REST-Routen
├── config.py             # Startbahnhof, Fahrplan-Pfad, Radien  ← hier anpassen
├── pipeline/
│   ├── cache.py          # SQLite-Cache (cache.db) für alle 3 Stufen
│   ├── fahrplan.py       # Stufe 1: PDF/TXT-Import → fahrplan.csv → Cache (offline)
│   ├── hoehen.py         # Stufe 1b: Bahnhofs-Höhen (Open-Meteo Elevation, gratis)
│   ├── gipfel.py         # Stufe 2: Gipfel pro Bahnhof (Overpass API)
│   └── touren.py         # Stufe 3: Touren (Outdooractive oder Fallback-Links)
├── daten/
│   ├── fahrplan.csv      # manuelle Fahrplan-Datei  ← „die Quelle der Wahrheit“
│   ├── vorlagen/         # Beispielfahrplan zum Reinklicken (wird vom Import ignoriert)
│   └── *.pdf / *.txt     # hier echte Fahrplan-PDFs für den Import ablegen
├── templates/index.html  # Startseite
├── static/               # style.css, script.js (Vanilla JS, kein Build-Schritt)
├── cache.db              # wird automatisch erstellt
└── requirements.txt
```

## Setup (einmalig)

**Python 3.10+** wird vorausgesetzt (Windows: von [python.org](https://www.python.org)
installieren und bei der Installation „Add python.exe to PATH“ anhaken).

```bash
cd gipfel-finder
python -m venv .venv
.venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

## Fahrplan aktualisieren (alle paar Monate)

`daten/fahrplan.csv` ist das Herzstück von Stufe 1. Drei Wege, die CSV zu füllen:

### Weg A: GTFS-Daten von gtfs.de einpflegen (empfohlen)

[gtfs.de](https://www.gtfs.de/) stellt den kompletten **Schienenregionalverkehr
Deutschlands** (RB, RE, IRE, S-Bahnen, nicht-bundeseigene Bahnen) als frei
downloadbares GTFS-Zip bereit (tagesaktuell generiert, CC-BY). Damit lassen sich
die erreichbaren Bahnhöfe ab München Hbf **exakt inkl. Umstiegen** bestimmen:

```bash
python -m pipeline.fahrplan --gtfs
```

Das Tool lädt das Zip (~12 MB), liest `stops / routes / trips / stop_times /
calendar(_dates)`, berechnet für jeden Halt die kürzeste Reisezeit ab dem
Startbahnhof (max. 15 Min. Umstiegswartezeit, max. 2 Umstiege) und schreibt
`daten/fahrplan.csv`. Bahnsteig-Haltestellen (z. B. „München Hbf (tief)") werden
automatisch zu einem Bahnhof zusammengeführt.

- Bereits heruntergeladene Datei wiederverwenden: `--gtfs-zip daten/gtfs.zip`
  bzw. lege dein Zip einfach als `daten/gtfs.zip` ab (dann funktioniert der
  Import komplett offline).
- Anderen gtfs.de-Feed nutzen: `--gtfs-url https://download.gtfs.de/germany/de_full/latest.zip`
  (de_full = Gesamt-Deutschland, nv_free = Nahverkehr, fv_free = Fernverkehr).
- Taktung: Der Import nimmt einen Montag als Referenztag (alle Werktage).

### Weg B: Direkt editieren (schnell)

Die CSV ist im Format `linie;bahnhof;fahrzeit_min;lat;lon`.
Neu eintragen oder Werte korrigieren – die Web-App liest sie beim nächsten
„Neu suchen erzwingen“ (oder App-Neustart) automatisch neu.

### Weg C: Aus einem Fahrplan-PDF importieren

Das Fahrplan-PDF deiner Linie in `daten/` legen (z. B. die Bayerische Oberlandbahn,
Werdenfelsbahn o. ä.), dann:

```bash
python -m pipeline.fahrplan --import
```

Das Tool extrahiert aus dem PDF die Stationen + Fahrzeiten ab München Hbf
(Zeilen mit Uhrzeiten; der Startbahnhof wird über `config.START_STATION` erkannt)
und schreibt `daten/fahrplan.csv` neu. Koordinaten, die noch fehlen, holt es
**einmalig** per OSM-Nominatim; bereits vorhandene Koordinaten werden übernommen.
Manuell ergänzte Zeilen der alten CSV bleiben erhalten.

Wer offline importieren will: `python -m pipeline.fahrplan --import --no-geocode`
(Koordinaten dann per Hand in die CSV eintragen, sonst werden diese Bahnhöfe in
Stufe 2 übersprungen).

> **Hinweis:** Die aktuell ausgelieferte `daten/fahrplan.csv` wurde per
> GTFS-Import (rv_free) erzeugt. Je nach Fahrplanperiode können Zeiten sich
> ändern – alle paar Monate `python -m pipeline.fahrplan --gtfs` neu ausführen
> und in der App „Neu suchen erzwingen“ klicken.

## Starten

```bash
python app.py
```

→ Browser: <http://127.0.0.1:5000>

## Kachel-Logik & „Neu suchen erzwingen“

Die drei Stufen (1. Fahrplan → 2. Gipfel → 3. Touren) laufen **einmal** und werden
danach in `cache.db` gespeichert. Beim Laden der Seite wird **nur der Cache**
gelesen – keine externen API-Aufrufe. Ehemalige Fahrplan-Daten aus der Bahn-API-Ära
werden ignoriert. Kopfzeile zeigt pro Stufe, wann zuletzt aktualisiert wurde
(„Zuletzt aktualisiert: vor 3 Tagen“).

Erst ein Klick auf **„Neu suchen erzwingen“** holt eine Stufe neu. Im
Bestätigungsdialog kann **gezielt eine einzelne Stufe** gewählt werden, z. B.
„Nur Fahrplan“ – Gipfel und Touren bleiben dann im Cache. Die Neusuche läuft im
Hintergrund mit Fortschrittsanzeige.

> Änderst du `config.py` ODER `daten/fahrplan.csv` (neuer Datei-Inhalt), wird
> automatisch ein neuer Konfigurations-Fingerprint gebildet. Alte Cache-Einträge
> gelten dann als veraltet und die nächste „Neusuche“ füllt den Cache neu.

## Stufen einzeln testen

Jede Stufe ist als eigenständiges Modul aufrufbar und schreibt dabei in den Cache:

```bash
python -m pipeline.fahrplan --force     # Stufe 1: CSV neu einlesen → Cache
python -m pipeline.hoehen               # Stufe 1b: Bahnhofs-Höhen ergänzen
python -m pipeline.gipfel               # Stufe 2: Gipfel (Overpass API)
python -m pipeline.touren               # Stufe 3: Touren (Outdooractive/Fallback)
```

Wird eine Stufe ausgeführt, deren Vorstufe im Cache noch fehlt, zieht sich die
Pipeline die Vorstufe automatisch nach.

## Konfiguration anpassen

| Einstellung | config.py | Bedeutung |
| --- | --- | --- |
| Startbahnhof | `START_STATION` | Name, Koordinaten (z. B. für andere Stadt) |
| Fahrplan-Datei | `FAHRPLAN_CSV` / `FAHRPLAN_ORDNER` | von der App gelesene CSV bzw. Import-Ordner |
| GTFS | `GTFS_URL`, `GTFS_ZIP`, `GTFS_UMSTIEGSWARTE_MIN`, `GTFS_MAX_UMSTIEGE` | gtfs.de-Download, Umstiegs-Parameter |
| Nominatim | `NOMINATIM_URL`, `NOMINATIM_DELAY_S` | einmalige Geokodierung beim PDF/TXT-Import |
| Fahrzeit | `MAX_FAHRZEIT_MINUTEN` | max. Bahnfahrt ab Start (Standard: 90) |
| Gipfel-Radius | `GIPFEL_RADIUS_M` | Luftlinie um den Bahnhof (Standard: 8000 m) |
| Gipfel-Batching | `OVERPASS_BATCH_SIZE` | Bahnhöfe je Overpass-Anfrage (Standard: 40, 1 = einzeln) |
| Bahnhofs-Höhen | `ELEVATION_API_URL`, `ELEVATION_BATCH_SIZE` | Open-Meteo-Elevation (gratis, kein Key) |
| Outdooractive | Env-Var `OUTDOORACTIVE_API_KEY` bzw. `config.OUTDOORACTIVE_API_BASE` | Tour-API (optional) |
| Cache-Datei | `CACHE_DB_PATH` | Pfad der SQLite-Datenbank |

> **Outdooractive-Key setzen (optional):** Nie direkt in `config.py` eintragen
> (das Repo ist öffentlich!). Stattdessen `.env.example` nach `.env` kopieren
> und dort den Key eintragen – `.env` wird von Git ignoriert und beim Start
> automatisch geladen (`python-dotenv`). Alternativ die Umgebungsvariable
> `OUTDOORACTIVE_API_KEY` direkt im System/Deployment setzen.

## Auf GitHub Pages veröffentlichen (statischer Export)

GitHub Pages kann keinen Flask-Server ausführen – es werden aber einmalig
**statische Dateien** exportiert, die dann überall (Pages, Cloudflare Pages,
Netlify, …) gehostet werden können. Alles funktioniert danach im Browser weiter
(Karte, Filter, Sortierung), nur „Neu suchen erzwingen“ entfällt:

```bash
python -m pipeline.export_web        # schreibt docs/ (HTML, daten.json, CSS/JS, fahrplan.csv)
git add docs && git commit -m "Web-Export aktualisiert" && git push
```

Danach in GitHub: **Settings → Pages → „Deploy from a branch“ → `main` + `/docs`**
(gilt für öffentliche Repos auf dem Free-Plan; bei privaten Repos wird ein
bezahlter Plan benötigt).

> 🌐 **Live:** Der statische Export dieses Repos läuft unter
> <https://Korbiii.github.io/gipfel-finder/>.

Nach jeder Aktualisierung von Fahrplan/Gipfeln/Touren den Export erneut ausführen.

> **Achtung:** GitHub Pages auf einem **privaten** Repo erfordert einen bezahlten
> Plan. Auf dem Free-Plan wird die Seite nur mit einem **öffentlichen** Repo
> veröffentlicht.

## Fehlerbehandlung

- **Keine `daten/fahrplan.csv`:** Die UI zeigt eine klare Anleitung, wie man den
  Fahrplan per Import erzeugt – alles andere bleibt unverändert stehen.
- **Bahnhof ohne Koordinaten:** Wird in Stufe 2 übersprungen und als Warnung
  angezeigt – die App stürzt nicht ab. Koordinaten in der CSV nachtragen.
- **Bahnhofshöhen:** Kommen aus der kostenlosen Open-Meteo-Elevation-API
  (`pipeline/hoehen.py`, Stufe 1b) und werden in den Fahrplan-Cache geschrieben.
  Schlägt ein Batch fehl, werden die Bahnhöfe einzeln nachgefragt; ohne Höhen
  zeigen die Spalten „Start-Höhe“ und „Höhenmeter“ ein „—“.
- **Overpass überlastet:** Timeout + bis zu 2 Retries mit Backoff, 1 s Pause
  zwischen den (Batch-)Anfragen. Die Gipfel-Suche (Stufe 2) ist gebündelt
  (`OVERPASS_BATCH_SIZE` Bahnhöfe je Anfrage) und damit deutlich schneller als
  eine Anfrage pro Bahnhof; schlägt ein Batch fehl, werden dessen Bahnhöfe
  automatisch einzeln nachgefragt.
- **Outdooractive:** Fehlgeschlagene Anfragen erzeugen Warnungen, die
  Fallback-Links bleiben aber immer verfügbar.

## Datenquellen

- Manueller Fahrplan (`daten/fahrplan.csv`) – Fahrtzeiten ab München Hbf
- Overpass API / OpenStreetMap – Gipfel (`natural=peak`)
- Outdooractive Data API – Touren (optional), sonst Komoot / Alpenverein aktiv
- Karte: Leaflet + OpenStreetMap-Kacheln (CDN)

**Haftungs-/Datenschutzhinweis:** Die App nutzt öffentliche APIs und handgepflegte
Fahrplandaten. Plane Touren in den Bergen verantwortungsvoll (Wetter, Kondition,
Ausrüstung) und prüfe konkrete Verbindungen vor der Fahrt – die Fahrzeiten sind
nur Richtwerte, keine Verkehrsauskunft.