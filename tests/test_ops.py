"""The shared operations behind both the CLI and the two dashboard buttons.

These are the paths the owner runs without help, so what is tested here is not
that they compute the right number but that they cannot leave the ledger in a
state that needs a developer to explain.
"""

from __future__ import annotations

import sqlite3

from finctl import ops
from finctl.ledger import db as ledger


def test_update_loads_the_taxonomy_before_categorizing(monkeypatch):
    """A rule may name a category the database has not seen yet.

    This is the one failure mode the Update button could not recover from on
    its own: edit config/taxonomy.yaml, add a rule pointing at the new
    category, press Update -- and categorize died on a FOREIGN KEY constraint,
    with an error naming an INSERT statement rather than the missing category.
    Loading the taxonomy first makes the button self-sufficient, which is the
    whole premise of operating this without assistance.

    The order is asserted rather than just the fact of loading, because loading
    afterwards would look identical in a coverage report and fix nothing.
    """
    import finctl.rules.categorize as cz
    import finctl.tax.taxonomy as tx

    order: list[str] = []

    def spy_load(conn, *args, **kwargs):
        order.append("taxonomy")
        return {"mgmt": 0, "tax": 0}

    def spy_categorize(conn, *args, **kwargs):
        order.append("categorize")
        return cz.CategorizeResult()

    def spy_vorgaenge(conn, *args, **kwargs):
        order.append("vorgaenge")
        return {}

    import finctl.ledger.gruppen as gr

    monkeypatch.setattr(tx, "load_into_db", spy_load)
    monkeypatch.setattr(cz, "categorize", spy_categorize)
    monkeypatch.setattr(gr, "apply", spy_vorgaenge)
    monkeypatch.setattr(ops, "ingest_all", lambda conn: [])

    from finctl.ledger.db import SCHEMA_PATH

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    result = ops.update(conn)
    # Vorgaenge zuletzt: sie lesen die Kategorie, die categorize gerade setzt.
    assert order == ["taxonomy", "categorize", "vorgaenge"]
    assert "taxonomy" in result
    assert "vorgaenge" in result


def test_a_missing_backup_target_refuses_instead_of_guessing(tmp_path, monkeypatch):
    """Hier stand ein fester Pfad in die Cloud des Autors.

    Auf einem fremden Rechner waere das bestenfalls ein Ordner, den niemand
    kennt, und auf Windows ein sinnloses
    `C:\\Users\\...\\Library\\CloudStorage\\...`. Das Schlimme daran ist nicht
    der Pfad, sondern dass die Sicherung SCHEINBAR liefe: im Ernstfall stuende
    man vor einem Ordner, den man nie gesehen hat.

    Wohin gesichert wird, ist eine Frage an den Nutzer. Sie gehoert in die
    Einrichtung, und solange sie unbeantwortet ist, wird nicht gesichert.
    """
    import pytest

    monkeypatch.setattr(ops, "CONFIG_DIR", tmp_path)
    with pytest.raises(ops.KeinBackupZielError) as fehler:
        ops.backup_settings()
    # Die Meldung muss sagen, WO es einzutragen ist -- sonst sucht man.
    assert "backup.yaml" in str(fehler.value)

    (tmp_path / "backup.yaml").write_text("directory: ~/wohin\nkeep: 3\n",
                                          encoding="utf-8")
    ziel, anzahl = ops.backup_settings()
    assert ziel.is_absolute() and anzahl == 3      # ~ aufgeloest, nicht roh


def test_a_first_backup_works_before_a_target_is_configured(tmp_path, monkeypatch):
    """`--to` ist der erste Lauf, bevor die Einrichtung durch ist.

    Wer gerade erst installiert hat, soll sichern koennen, ohne vorher eine
    YAML-Datei anzulegen -- sonst ist die erste Sicherung die, die ausfaellt.

    Mit einer ECHTEN, leeren Datenbank. Der erste Versuch gab hier nur eine
    fehlende Datei vor; der Lauf brach dann an der Datenbankpruefung ab und
    erreichte die Zielaufloesung nie. Der Test waere auch dann gruen gewesen,
    wenn genau das kaputt ist, was er prueft.
    """
    db = tmp_path / "finance.db"
    ledger.connect(db).close()                 # leer, aber gueltig
    monkeypatch.setattr(ops, "CONFIG_DIR", tmp_path / "ohne-konfiguration")
    monkeypatch.setattr(ops, "DB_PATH", db)

    ergebnis = ops.write_backup(to=tmp_path / "ziel")

    archive = sorted((tmp_path / "ziel").glob("finance-os_*.tar.gz"))
    assert len(archive) == 1, ergebnis
    assert archive[0].stat().st_size > 0
