"""Der Treffer-Check rechnet nach, was die Prognose am Vormonatsende gesagt haette.

Zwei Eigenschaften machen ihn aussagekraeftig. Ein Ledger, das jeden Monat
dasselbe bucht, muss exakt getroffen werden -- sonst misst der Check sich
selbst. Und das Fenster darf keine Buchung aus dem Monat enthalten, den es
vorhersagt, sonst ist jeder Treffer geschummelt.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from finctl.forecast import abgleich as ag
from finctl.forecast import treffer as tr
from finctl.ledger.db import connect, init_db


@pytest.fixture
def konstant(tmp_path):
    """Vierzehn Monate mit identischen Buchungen: Miete, Konsum, Fixkosten."""
    pfad = init_db(tmp_path / "t.db")
    c = connect(pfad)
    c.execute("INSERT INTO accounts (id, display_name, institution, account_type, "
              "ingest_mode, parser_profile) VALUES ('a','A','X','giro','parsed','p')")
    for kid, kind, fix in (("wohnen", "expense", 0), ("wohnen/miete", "expense", 1),
                           ("konsum", "expense", 0), ("konsum/sonstiges", "expense", 0),
                           ("einkommen", "income", 0), ("einkommen/zinsen", "income", 0)):
        c.execute("INSERT INTO mgmt_categories (id, parent_id, name, kind, fixkosten) "
                  "VALUES (?,?,?,?,?)", (kid, kid.split("/")[0] if "/" in kid else None,
                                         kid, kind, fix))
    monat = date(2025, 1, 1)
    n = 0
    for i in range(14):
        m = ag._plus_monate(monat, i)
        ende = ag._month_end(m)
        sid = c.execute(
            "INSERT INTO statements (account_id, source_path, source_name, file_sha256, "
            "period_start, period_end, balance_start_cents, balance_end_cents, "
            "parser_profile, parser_version, status, imported_at) "
            "VALUES ('a','x','x',?,?,?,0,0,'p','1','imported','now')",
            (f"sha{i}", m.isoformat(), ende.isoformat())).lastrowid
        for tag, cents, kat in ((1, -90000, "wohnen/miete"), (10, -25000, "konsum/sonstiges"),
                                (20, 1500, "einkommen/zinsen")):
            n += 1
            tid = c.execute(
                "INSERT INTO transactions (account_id, statement_id, booking_date, "
                "amount_cents, raw_text, seq_in_statement, dedup_hash, created_at) "
                "VALUES ('a',?,?,?,'x',?,?,'now')",
                (sid, m.replace(day=tag).isoformat(), cents, n, f"h{n}")).lastrowid
            c.execute("INSERT INTO splits (transaction_id, amount_cents, mgmt_category_id, "
                      "source, created_at, updated_at) VALUES (?,?,?,'rule','now','now')",
                      (tid, cents, kat))
    c.commit()
    yield c
    c.close()


def test_a_constant_ledger_is_hit_exactly(konstant):
    monate = tr.letzte_monate(konstant, 3)
    assert monate[-1] == date(2026, 2, 1)
    for m in tr.jahresbasis(konstant, monate, plaene=[], objekt_cents=lambda _m: 0):
        for block in ("miete", "fixkosten", "konsum", "einkommen_sonst", "immobilie"):
            assert m.delta(block) == 0, (m.monat, block, m.soll, m.ist)
        assert m.unvorhersehbar_cents == 0


def test_the_window_never_contains_the_month_it_predicts(konstant):
    m = date(2026, 2, 1)
    f = tr.fenster_vor(konstant, m)
    assert f.bis < m
    assert f.monate == 12
    # Eine Ausreisserbuchung IM Zielmonat darf die Prognose nicht bewegen.
    tid = konstant.execute(
        "INSERT INTO transactions (account_id, statement_id, booking_date, amount_cents, "
        "raw_text, seq_in_statement, dedup_hash, created_at) "
        "VALUES ('a',1,'2026-02-15',-500000,'x',99,'ausreisser','now')").lastrowid
    konstant.execute("INSERT INTO splits (transaction_id, amount_cents, mgmt_category_id, "
                     "source, created_at, updated_at) "
                     "VALUES (?,-500000,'konsum/sonstiges','rule','now','now')", (tid,))
    [eintrag] = tr.jahresbasis(konstant, [m], plaene=[], objekt_cents=lambda _m: 0)
    assert eintrag.soll["konsum"] == -25000
    assert eintrag.delta("konsum") == -500000


def test_the_account_derivation_stops_before_the_cut_off(konstant):
    from finctl.forecast import engine as fc

    tid = konstant.execute(
        "INSERT INTO transactions (account_id, statement_id, booking_date, amount_cents, "
        "raw_text, seq_in_statement, dedup_hash, created_at) "
        "VALUES ('a',1,'2026-02-15',-500000,'x',99,'spaet','now')").lastrowid
    konstant.execute("INSERT INTO splits (transaction_id, amount_cents, mgmt_category_id, "
                     "source, created_at, updated_at) "
                     "VALUES (?,-500000,'konsum/sonstiges','rule','now','now')", (tid,))
    mit = fc.derive_recurring(konstant, "a", since="2025-08-01", escalation_pa=0.0)
    bis = fc.derive_recurring(konstant, "a", since="2025-08-01", escalation_pa=0.0,
                              until="2026-01-31")
    assert {i.measured_until for i in mit} == {date(2026, 2, 1)}
    assert {i.measured_until for i in bis} == {date(2026, 1, 1)}
    assert {i.label: i.amount_cents for i in bis}["konsum/sonstiges"] == -25000


@pytest.mark.skipif(not Path("data/finance.db").exists(), reason="no ledger present")
def test_the_check_runs_on_the_real_ledger():
    c = connect()
    try:
        r = tr.pruefen(c, 3)
    finally:
        c.close()
    assert len(r["jahres"]) == 3
    for j in r["jahres"]:
        assert j.sparrate_soll or j.sparrate_ist
