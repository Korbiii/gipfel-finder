"""Flask-Server für den Gipfel-Finder.

Start:   python app.py
Dann:    http://127.0.0.1:5000  im Browser öffnen

Endpunkte:
  GET  /                       – Startseite (HTML)
  GET  /api/daten              – JSON: Gipfel, Bahnhöfe, Tour-Links, Status
  POST /api/neusuchen          – {"stufe": "alle"|"fahrplan"|"hoehen"|"gipfel"|"touren"}
                                 startet eine Neusuche (async, per Job)
  GET  /api/fortschritt/<job>  – Fortschritt/Ergebnis des Jobs
"""

from __future__ import annotations

import threading
import uuid

from flask import Flask, jsonify, render_template, request, send_file
from pathlib import Path

import config
from pipeline import cache
from pipeline.fahrplan import fetch_stationen
from pipeline.gipfel import fetch_gipfel
from pipeline.hoehen import fetch_hoehen
from pipeline.touren import fetch_touren

app = Flask(__name__)

STUFEN = ("fahrplan", "hoehen", "gipfel", "touren")
JOBS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Status-Sammlung (für UI & API)
# ---------------------------------------------------------------------------

def _stufen_status() -> dict:
    """Vorhanden + "Zuletzt aktualisiert" pro Stufe für die aktuelle Konfiguration."""
    return {
        s: {
            "vorhanden": cache.stage_filled(s),
            "zuletzt": cache.human_ago(cache.get_last_fetched(s)),
            "warnungen": cache.load_warnings(s),
        }
        for s in STUFEN
    }


def _api_daten() -> dict:
    bahnhoefe = cache.load_fahrplan()
    gipfel = cache.load_gipfel()
    touren = cache.load_touren()
    bahnhof_hoehe = {b["station_id"]: b.get("hoehe_m") for b in bahnhoefe}
    for g in gipfel:
        g["touren"] = touren.get(g["osm_id"], [])
        g["bahnhof_hoehe_m"] = bahnhof_hoehe.get(g.get("bahnhof_id"))
        if g.get("hoehe_m") is not None and g["bahnhof_hoehe_m"] is not None:
            # grobe Abschätzung der Höhenmeter (Luftlinie Bahnhof → Gipfel)
            g["aufstieg_m"] = max(0, int(round(g["hoehe_m"] - g["bahnhof_hoehe_m"])))
        else:
            g["aufstieg_m"] = None
    return {
        "konfig": {
            "start_name": config.START_STATION["name"],
            "max_fahrzeit_min": config.MAX_FAHRZEIT_MINUTEN,
            "gipfel_radius_km": config.GIPFEL_RADIUS_M // 1000,
            "fahrplan_csv": config.FAHRPLAN_CSV,
            "oa_key_aktiv": bool(config.OUTDOORACTIVE_API_KEY),
        },
        "stufen": _stufen_status(),
        "bahnhoefe": bahnhoefe,
        "summen": {
            "fahrplan": len(bahnhoefe),
            "gipfel": len(gipfel),
            "hoehen": sum(1 for b in bahnhoefe if b.get("hoehe_m") is not None),
        },
        "gipfel": gipfel,
    }


# ---------------------------------------------------------------------------
# Neusuche als Hintergrund-Job (dauert je nach Konfiguration mehrere Minuten)
# ---------------------------------------------------------------------------

def _job_starten(stufe: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "zustand": "laeuft",
        "meldung": "Starte …",
        "fp": {"erledigt": 0, "gesamt": 1, "text": "Warte auf Start …"},
    }
    thread = threading.Thread(target=_job_ausfuehren, args=(job_id, stufe), daemon=True)
    thread.start()
    return job_id


def _job_ausfuehren(job_id: str, stufe: str) -> None:
    job = JOBS[job_id]

    def fortschritt(erledigt: int, gesamt: int, text: str) -> None:
        job["fp"].update(erledigt=erledigt, gesamt=gesamt, text=text)

    try:
        # Gezielte Stufe(n) neu von der API laden; Downstream-Stufen
        # bleiben mit ihren eigenen Cache-Daten unangetastet.
        if stufe in ("alle", "fahrplan", "hoehen"):
            if stufe in ("alle", "fahrplan"):
                fetch_stationen(force=True, fortschritt=fortschritt)
            # neue Höhen, sobald Fahrplan frisch ist (bei fehlenden Höhen werden
            # automatisch nur die fehlenden geholt; force=True erzwingt alle)
            fetch_hoehen(force=(stufe == "hoehen"), fortschritt=fortschritt)
        if stufe in ("alle", "gipfel"):
            fetch_gipfel(force=True, fortschritt=fortschritt)
        if stufe in ("alle", "touren"):
            fetch_touren(force=True, fortschritt=fortschritt)
        job["zustand"] = "fertig"
        job["meldung"] = "Fertig – Daten wurden aktualisiert."
    except Exception as e:
        job["zustand"] = "fehler"
        # Nutzerfreundliche Meldung: der Fehlertyp (HTTPError/…) ist für die UI uninteressant
        job["meldung"] = str(e)


# ---------------------------------------------------------------------------
# Routen
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", config=config, stufen=_stufen_status())


@app.route("/api/daten")
def api_daten():
    return jsonify(_api_daten())


@app.route("/api/fahrplan.csv")
def api_fahrplan_csv():
    """Die gepflegte Fahrplan-CSV zum Anschauen/Herunterladen."""
    pfad = Path(config.FAHRPLAN_CSV)
    if not pfad.exists():
        return jsonify({"fehler": "Fahrplan-CSV existiert noch nicht – bitte zuerst importieren."}), 404
    return send_file(pfad, mimetype="text/csv; charset=utf-8")


@app.route("/api/neusuchen", methods=["POST"])
def api_neusuchen():
    daten = request.get_json(silent=True) or {}
    stufe = daten.get("stufe", "alle")
    if stufe not in ("alle", "fahrplan", "hoehen", "gipfel", "touren"):
        return jsonify({"fehler": f"Unbekannte Stufe: {stufe}"}), 400
    return jsonify({"job_id": _job_starten(stufe)})


@app.route("/api/fortschritt/<job_id>")
def api_fortschritt(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"zustand": "unbekannt"}), 404
    return jsonify({"zustand": job["zustand"], "meldung": job["meldung"], **job["fp"]})


if __name__ == "__main__":
    cache.init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)