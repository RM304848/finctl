"""Das Kontenregister: geschlossene Konten und das Betriebskonto.

Ein geschlossenes Konto bleibt in der Historie, faellt aber aus jeder Auswahl.
Vorher setzte `finctl init` jedes Konto auf aktiv, und Amex, Revolut und
Onvista standen Jahre nach der Kuendigung in der Kontenliste der Planung.
"""

from __future__ import annotations

import sqlite3

import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from finctl import cli
from finctl.web.server import app


def test_init_marks_a_closed_account_inactive(tmp_path, monkeypatch):
    konfig = tmp_path / "config"
    konfig.mkdir()
    (konfig / "accounts.yaml").write_text(yaml.safe_dump({"accounts": [
        {"id": "offen", "display_name": "Offen", "institution": "X",
         "account_type": "giro", "ingest_mode": "summary"},
        {"id": "zu", "display_name": "Zu", "institution": "X",
         "account_type": "giro", "ingest_mode": "summary", "active": False},
        {"id": "datiert", "display_name": "Datiert", "institution": "X",
         "account_type": "giro", "ingest_mode": "summary", "closed_on": "2025-03-31"},
    ]}), encoding="utf-8")
    db = tmp_path / "finance.db"
    monkeypatch.setattr(cli, "CONFIG_DIR", konfig)
    monkeypatch.setattr(cli, "DB_PATH", db)

    ergebnis = CliRunner().invoke(cli.app, ["init"])
    assert ergebnis.exit_code == 0, ergebnis.output

    zeilen = {r[0]: (r[1], r[2]) for r in sqlite3.connect(db).execute(
        "SELECT id, active, closed_on FROM accounts")}
    assert zeilen["offen"] == (1, None)
    assert zeilen["zu"] == (0, None)
    assert zeilen["datiert"] == (0, "2025-03-31")


def test_the_planning_dropdown_names_the_operating_account():
    from finctl import kontenregeln as kr

    betrieb = next(k for k, v in (kr.wirksam().get("account_roles") or {}).items()
                   if (v or {}).get("role") == "operating")
    html = TestClient(app).get("/planung").text
    assert f"{betrieb} (Betriebskonto)" in html
    assert '<option value="">Betriebskonto</option>' not in html
