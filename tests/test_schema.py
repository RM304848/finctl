"""The schema must not promise more than the code delivers.

Nine tables accumulated here with zero rows and zero references --
recurring_series, forecast_assumptions, scenarios, scenario_events,
investment_flows, property_segments, loan_segments, monthly_aggregates and a
schema_migrations table nobody ever wrote a version into. Each was a
reasonable idea at the time, and together they described a system that does
not exist: a forecast stored in tables, a portfolio engine, a migration
framework.

That is worse than a smaller schema. Someone reading it -- the owner in two
years, with no memory of this -- would conclude the forecast lives in the
database and go looking for the code that fills those tables.
"""

from __future__ import annotations

import re
from pathlib import Path

SCHEMA = Path("finctl/ledger/schema.sql")
PACKAGE = Path("finctl")


def _tables() -> list[str]:
    return re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)",
                      SCHEMA.read_text(encoding="utf-8"))


def test_every_table_has_a_consumer():
    """A table no code reads or writes is a promise nothing keeps."""
    code = "\n".join(p.read_text(encoding="utf-8")
                     for p in PACKAGE.rglob("*.py"))
    orphans = [t for t in _tables() if not re.search(rf"\b{t}\b", code)]
    assert orphans == [], (
        f"Tabellen ohne Konsument: {orphans}. Entweder benutzen oder entfernen "
        f"-- ein Schema, das etwas verspricht, was der Code nicht einlöst, "
        f"schickt den nächsten Leser auf die Suche nach Code, den es nicht gibt.")


def test_the_removed_tables_stay_removed():
    """Named individually, so re-adding one is a deliberate act."""
    gone = {"recurring_series", "forecast_assumptions", "scenarios",
            "scenario_events", "investment_flows", "property_segments",
            "loan_segments", "monthly_aggregates", "schema_migrations"}
    assert gone.isdisjoint(_tables())


def test_the_schema_loads_into_a_fresh_database():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    built = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(_tables()) == built
