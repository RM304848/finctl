"""Eine Sicherung aus der Einrichtung heraus wiederherstellen -- ohne Terminal."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finctl import ops
from finctl import pfade as _p
from finctl.web.server import app

client = TestClient(app)


@pytest.fixture
def archiv(tmp_path) -> Path:
    if not Path("data/finance.db").exists():
        pytest.skip("kein Hauptbuch")
    return Path(ops.write_backup(to=tmp_path / "sicherung", keep=5)["archive"])


@pytest.fixture
def daneben(tmp_path, monkeypatch) -> Path:
    """Der jetzige Datenordner -- neben ihm entsteht der wiederhergestellte."""
    jetzt = tmp_path / "Finance OS"
    jetzt.mkdir()
    monkeypatch.setattr(_p, "DATEN", jetzt)
    return jetzt


def _transaktionen(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    finally:
        conn.close()


def test_a_backup_lands_in_a_new_folder_beside_the_current_one(archiv, daneben):
    bericht = ops.wiederherstellen_neben(archiv)
    ziel = Path(bericht["ziel"])
    assert ziel.parent == daneben.parent
    assert ziel.name.startswith("Finance OS (Sicherung 20")
    assert bericht["stimmig"]
    assert bericht["transaktionen"] == _transaktionen(Path("data/finance.db"))
    # Umgeschaltet wird erst mit "Diesen Stand verwenden".
    assert _p.ort() is None
    # Ein zweites Mal ueberschreibt den ersten nicht.
    zweiter = Path(ops.wiederherstellen_neben(archiv)["ziel"])
    assert zweiter != ziel and zweiter.name.endswith(" 2")


def test_the_folder_name_carries_the_time_of_the_backup_and_no_colon():
    assert ops._stempel("finance-os_20260927-213124.tar.gz") == "2026-09-27 21.31"
    assert ops._stempel("irgendwas.tar.gz") is None


def test_an_uploaded_backup_is_restored_and_only_a_backup(archiv, daneben):
    falsch = client.post("/api/wiederherstellen-datei",
                         files={"datei": ("auszug.csv", b"a;b\n", "text/csv")})
    assert falsch.status_code == 400
    with archiv.open("rb") as f:
        res = client.post("/api/wiederherstellen-datei",
                          files={"datei": (archiv.name, f, "application/gzip")})
    assert res.status_code == 200, res.text
    assert Path(res.json()["ziel"]).is_dir() and res.json()["stimmig"]


def test_only_a_backup_from_the_list_can_be_named(daneben):
    res = client.post("/api/wiederherstellen", json={"name": "../../etc/passwd"})
    assert res.status_code == 400


def test_the_setup_lists_the_backups(monkeypatch):
    monkeypatch.setattr(ops, "sicherungen", lambda: [
        {"name": "finance-os_20260927-213124.tar.gz", "bytes": 512000, "am": "2026-09-27 21:31"}])
    html = client.get("/einrichtung").text
    assert "Aus einer Sicherung wiederherstellen" in html
    assert "2026-09-27 21:31" in html and "finance-os_20260927-213124.tar.gz" in html
