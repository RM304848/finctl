"""Wohin gesichert wird, fragt die App -- sie raet es nicht.

Hier stand ein fester Pfad in die Cloud des Autors. Auf einem fremden Rechner
waere das ein Ordner, den niemand kennt; auf Windows ein sinnloses
`C:\\Users\\...\\Library\\CloudStorage\\...`. Das Schlimme daran ist nicht der
Pfad, sondern dass die Sicherung SCHEINBAR liefe.

Geprueft wird deshalb beides: dass ohne Ziel nichts geschrieben wird, und dass
ein eingetragenes Ziel vor dem Speichern wirklich beschrieben wurde.
"""

from __future__ import annotations

import os

import pytest

from finctl import ops


def test_an_unwritable_target_is_refused_before_it_is_saved(tmp_path):
    """Durch SCHREIBEN geprueft, nicht durch Nachdenken ueber Rechte.

    Ein Cloud-Ordner, der gerade nicht eingehaengt ist, sieht vorhanden aus
    und nimmt trotzdem nichts an. `os.access` sagt dazu das Falsche.
    """
    if os.name == "nt":
        pytest.skip("chmod setzt unter Windows kein Schreibverbot; geprueft auf macOS")
    gesperrt = tmp_path / "gesperrt"
    gesperrt.mkdir()
    gesperrt.chmod(0o500)                      # lesen ja, schreiben nein
    try:
        assert ops.ziel_pruefen(gesperrt)      # nicht leer = Grund genannt
    finally:
        gesperrt.chmod(0o700)


def test_a_relative_target_is_refused(tmp_path):
    """Ein relativer Pfad haengt am Arbeitsverzeichnis und zeigt nach dem
    naechsten Start woanders hin -- die Sicherung waere dann verstreut."""
    assert "vollstaendiger Pfad" in ops.ziel_pruefen("backups")


def test_a_writable_target_passes(tmp_path):
    assert ops.ziel_pruefen(tmp_path / "neu") == ""
    assert (tmp_path / "neu").is_dir()         # wird angelegt, nicht verlangt


def test_the_status_says_what_is_missing_instead_of_failing(tmp_path, monkeypatch):
    """Eine frische Installation hat kein Ziel. Das ist kein Fehler."""
    monkeypatch.setattr(ops, "CONFIG_DIR", tmp_path)
    from finctl import overlays
    monkeypatch.setattr(overlays, "CONFIG_DIR", tmp_path)

    stand = ops.sicherungsstand()
    assert stand["ziel"] is None
    assert not stand["erreichbar"]
    assert "backup.yaml" in stand["grund"]     # sagt, WO einzutragen ist


def test_the_base_file_keeps_its_comments_when_the_target_is_changed(
        tmp_path, monkeypatch):
    """Das Overlay-Muster, und hier zaehlt es besonders.

    In backup.yaml steht, was das Archiv enthaelt und warum zwoelf Staende
    bleiben. Ein Dashboard, das die Datei neu schreibt, loescht das beim
    ersten Klick.
    """
    from finctl import overlays

    monkeypatch.setattr(ops, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(overlays, "CONFIG_DIR", tmp_path)
    basis = tmp_path / "backup.yaml"
    basis.write_text("# Die Begruendung, die bleiben muss.\n"
                     "directory: ~/alt\nkeep: 12\n", encoding="utf-8")

    overlays.sicherung_setzen({"directory": str(tmp_path / "neu"), "keep": 3})

    assert "# Die Begruendung, die bleiben muss." in basis.read_text(encoding="utf-8")
    ziel, anzahl = ops.backup_settings()
    assert ziel == tmp_path / "neu" and anzahl == 3


@pytest.mark.parametrize("koerper,teil", [
    ({}, "Kein Ordner"),
    ({"directory": "relativ"}, "vollstaendiger Pfad"),
])
def test_the_api_refuses_what_it_cannot_write_to(koerper, teil):
    from starlette.testclient import TestClient

    from finctl.web.server import app

    with TestClient(app) as client:
        res = client.post("/api/backup/ziel", json=koerper)
    assert res.status_code == 400
    assert teil in res.json()["error"]
