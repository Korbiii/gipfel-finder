"""Gemeinsame Test-Fixtures: isolierte cache.db, damit Tests nie die echte
lokale cache.db im Projektstamm berühren."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import config
from pipeline import cache


@pytest.fixture()
def isolierte_cache_db(tmp_path, monkeypatch):
    """Zeigt config.CACHE_DB_PATH auf eine leere, temporäre SQLite-Datei."""
    db_pfad = tmp_path / "test_cache.db"
    monkeypatch.setattr(config, "CACHE_DB_PATH", str(db_pfad))
    cache.init_db()
    return db_pfad
