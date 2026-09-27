"""Der Livestand steht nur da, wenn er etwas sagt, was der Auszug nicht sagt."""

import sqlite3
from datetime import date

from finctl.monatsabschluss import konten


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE accounts (id TEXT, display_name TEXT, ingest_mode TEXT);
        CREATE TABLE statements (account_id TEXT, period_end TEXT);
        INSERT INTO accounts VALUES ('c24','C24','parsed'), ('dkb','DKB','parsed');
        INSERT INTO statements VALUES ('c24','2026-08-31'), ('dkb','2026-09-04');
    """)
    return conn


def test_a_live_balance_as_old_as_the_statement_is_not_shown():
    zeilen = konten(_db(), date(2026, 9, 14), "2026-09", [
        {"account_id": "c24", "cents": 6057, "as_of": "2026-08-31"}])
    assert next(p for p in zeilen if p.name == "C24").zusatz == ""


def test_a_newer_live_balance_is_shown_with_its_amount():
    zeilen = konten(_db(), date(2026, 9, 14), "2026-09", [
        {"account_id": "dkb", "cents": 1135738, "as_of": "2026-09-11"}])
    zusatz = next(p for p in zeilen if p.name == "DKB").zusatz
    assert zusatz.startswith("Livestand 2026-09-11")
    assert "11.357,38" in zusatz
