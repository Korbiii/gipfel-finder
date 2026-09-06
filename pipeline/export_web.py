"""Statischer Export des Gipfel-Finders für GitHub Pages (bzw. jedes statische Hosting).

GitHub Pages kann keinen Python/Flask-Server ausführen. Dieses Skript erzeugt
deshalb aus den Cache-Daten eine einmalige, statische Kopie der Anwendung:

    python -m pipeline.export_web             # schreibt nach docs/
    python -m pipeline.export_web --out web   # oder in einen anderen Ordner

Ergebnis (z. B. docs/):
    index.html      Oberfläche (wie die Flask-Startseite, ohne Backend)
    daten.json      alle Gipfel, Bahnhöfe, Touren, Cache-Status
    style.css       (Kopie aus static/)
    script.js       (Kopie aus static/ – läuft dann im „statischen Modus“)
    fahrplan.csv    Kopie der gepflegten Fahrplan-Datei (falls vorhanden)

Danach:  git add docs && git commit -m "..." && git push
Dann unter GitHub → Settings → Pages → „Deploy from a branch“ → main + /docs.
Nach jeder Aktualisierung von Fahrplan/Gipfeln/Touren muss der Export neu laufen.

Hinweis: Für private Repos verlangt GitHub Pages einen bezahlten Plan; auf dem
Free-Plan funktioniert Pages nur mit einem öffentlichen Repository.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from pipeline import cache
from pipeline.daten import api_daten, stufen_status

ROOT = Path(__file__).resolve().parent.parent


def _export_zeit() -> str:
    return datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def _render_index() -> str:
    """Rendert templates/index.html ohne Flask (mit url_for-Ersatz)."""
    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tmpl = env.get_template("index.html")

    def url_for(endpoint: str, filename: str | None = None, **_kw) -> str:
        """Im statischen Export liegen die Dateien direkt neben index.html."""
        return filename or ""

    return tmpl.render(
        config=config,
        stufen=stufen_status(),
        STATISCH=True,
        export_datum=_export_zeit(),
        url_for=url_for,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", default="docs", help="Ausgabeordner (Standard: docs)")
    args = parser.parse_args()

    if not Path(config.CACHE_DB_PATH).exists():
        print("⚠  cache.db fehlt – zuerst App/Pipeline laufen lassen (python app.py).")
        return 2

    cache.init_db()
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "index.html").write_text(_render_index(), encoding="utf-8")
    daten = json.dumps(api_daten(), ensure_ascii=False, separators=(",", ":"))
    (out_dir / "daten.json").write_text(daten, encoding="utf-8")
    for name in ("style.css", "script.js"):
        shutil.copyfile(ROOT / "static" / name, out_dir / name)
    fahrplan = ROOT / config.FAHRPLAN_CSV
    if fahrplan.exists():
        shutil.copyfile(fahrplan, out_dir / "fahrplan.csv")

    print(f"Statischer Export nach {out_dir} geschrieben:")
    for p in sorted(out_dir.iterdir()):
        print(f"  {p.name}  ({p.stat().st_size:,} Bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())