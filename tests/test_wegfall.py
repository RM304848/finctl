"""Ein Wegfall nimmt in der Kontoprognose genau heraus, was sie fortschreibt.

Die Kontoprognose leitet ihre Posten anders her als die Jahresrechnung --
variable Kosten als Schnitt ueber sechs Monate, Jahresposten an ihrem Termin.
Ein Wegfall, der dort den Zwoelfmonatsschnitt der Jahresrechnung abzog, hob
den fortgeschriebenen Posten nur zum Teil auf, und ein Konto, das nichts mehr
ausgibt, zeigte trotzdem Kosten.

Alles hier ist erfunden: zwei Konten, eine Tankstelle, ein Jahresabo.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from finctl.forecast import engine as fc
from finctl.forecast import szenarien as sz
from finctl.forecast.konten import wegfall_posten

START = date(2026, 10, 1)


@pytest.fixture
def db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(open("finctl/ledger/schema.sql", encoding="utf-8").read())
    for konto in ("karte", "giro"):
        c.execute("INSERT INTO accounts (id, display_name, institution, account_type, "
                  "ingest_mode, parser_profile) VALUES (?, ?, 'X', 'giro', 'parsed', 'p')",
                  (konto, konto))
        c.execute("INSERT INTO statements (account_id, source_path, source_name, "
                  "file_sha256, period_start, period_end, balance_start_cents, "
                  "balance_end_cents, parser_profile, parser_version, status, imported_at) "
                  "VALUES (?, 'p', 'n', ?, '2025-01-01', '2026-09-30', 0, 0, 'p', '1', "
                  "'imported', 't')", (konto, konto))
    for kat, eltern, fix in (("mobilitaet", None, 0), ("mobilitaet/kraftstoff", "mobilitaet", 0),
                             ("abo", None, 1), ("abo/cloud", "abo", 1)):
        c.execute("INSERT INTO mgmt_categories (id, parent_id, name, kind, fixkosten) "
                  "VALUES (?, ?, ?, 'expense', ?)", (kat, eltern, kat, fix))
    buchungen = [
        # Auf der Karte: einmal getankt, einmal das Jahresabo.
        ("k1", "karte", "2026-06-15", -7000, "tankstelle", "mobilitaet/kraftstoff"),
        ("w1", "karte", "2025-12-12", -1200, "wolke", "abo/cloud"),
        # Auf dem Giro tankt derselbe Haushalt weiter, bei derselben Tankstelle.
        *[(f"g{m}", "giro", f"2026-0{m}-10", -10000, "tankstelle", "mobilitaet/kraftstoff")
          for m in range(4, 10)],
    ]
    for n, (h, konto, tag, cents, gegen, kat) in enumerate(buchungen, start=1):
        c.execute("INSERT INTO transactions (id, account_id, statement_id, booking_date, "
                  "amount_cents, counterparty_norm, raw_text, seq_in_statement, dedup_hash, "
                  "created_at) VALUES (?, ?, 1, ?, ?, ?, 'x', ?, ?, 't')",
                  (n, konto, tag, cents, gegen, n, h))
        c.execute("INSERT INTO splits (transaction_id, seq, amount_cents, mgmt_category_id, "
                  "source, created_at, updated_at) VALUES (?, 0, ?, ?, 'rule', 't', 't')",
                  (n, cents, kat))
    return c


def _ableiten(db, konto, ohne=frozenset()):
    return fc.derive_recurring(
        db, konto, since="2026-04-01", since_fixkosten="2025-10-01",
        until="2026-09-30", variabel_als_schnitt=True,
        ohne=[{"dedup_hashes": sorted(ohne)}] if ohne else None)


def _klammer(*zeilen):
    return [sz.Scenario(id="k", name="Korrektur", active=True, lines=list(zeilen))]


def _wegfall(buchungen, kategorie="mobilitaet/kraftstoff", start=START, **extra):
    return sz.Line(label="Weg", amount_cents=0, kind="wegfall", start=start,
                   category_id=kategorie, buchungen=list(buchungen), **extra)


def _posten(db, konto, klammern, months=12):
    return wegfall_posten(db, konto, klammern, _ableiten(db, konto),
                          lambda ohne: _ableiten(db, konto, ohne),
                          start=START, months=months)


def _monatssummen(db, konto, klammern, months=12, nur=None):
    """Je Monat: was die Ableitung bucht plus der Wegfall -- mit `nur` fuer
    eine Kategorie und ihren Wegfall."""
    p = fc.project(account_id=konto, opening_cents=0, start=START, months=months,
                   recurring=_ableiten(db, konto),
                   one_offs=_posten(db, konto, klammern, months))
    return [sum(v for k, v in row.detail.items()
                if nur is None or k in (nur, "Korrektur: Weg")) for row in p.rows]


def test_a_wegfall_cancels_exactly_what_the_account_forecast_projects(db):
    """Ein Tankstopp im Sechsmonatsfenster ist ein Schnitt von 70/6 im Monat --
    und genau den nimmt der Wegfall wieder heraus, nicht ein Zwoelftel."""
    [posten] = [p for p in _ableiten(db, "karte") if p.label == "mobilitaet/kraftstoff"]
    assert posten.amount_cents == round(-7000 / 6)
    klammern = _klammer(_wegfall(["k1"], account_id="karte"),
                        _wegfall(["w1"], kategorie="abo/cloud", account_id="karte"))
    assert _monatssummen(db, "karte", klammern) == [0] * 12


def test_a_yearly_item_falls_away_in_its_own_month(db):
    """Das Jahresabo geht im Dezember ab; der Wegfall steht im Dezember, nicht
    als Zwoelftel in jedem Monat."""
    posten = _posten(db, "karte", _klammer(_wegfall(["w1"], kategorie="abo/cloud")))
    assert [(p.month, p.amount_cents) for p in posten] == [(date(2026, 12, 1), 1200)]
    assert posten[0].reduces_cost


def test_a_wegfall_does_nothing_before_its_start(db):
    posten = _posten(db, "karte", _klammer(_wegfall(["k1"], start=date(2027, 1, 1))))
    assert posten and min(p.month for p in posten) == date(2027, 1, 1)


def test_two_lines_on_the_same_series_take_it_out_once(db):
    zweimal = _klammer(_wegfall(["k1"]), _wegfall(["k1"]))
    einmal = _klammer(_wegfall(["k1"]))
    assert (sum(p.amount_cents for p in _posten(db, "karte", zweimal))
            == sum(p.amount_cents for p in _posten(db, "karte", einmal)))


def test_without_the_flag_the_series_ends_on_every_account(db):
    """Die Reihe ist dieselbe Tankstelle in derselben Kategorie -- auch auf
    dem Giro. Ohne Haken heisst Wegfall: es wird nirgends mehr getankt."""
    assert _monatssummen(db, "giro", _klammer(_wegfall(["k1"]))) == [0] * 12


def test_nur_kontoprognose_leaves_the_other_accounts_alone(db):
    """Die Reihe zieht von der Karte weg; auf dem Giro wird weiter getankt."""
    zeile = _wegfall(["k1"], account_id="karte", nur_kontoprognose=True)
    assert _posten(db, "giro", _klammer(zeile)) == []
    assert _monatssummen(db, "karte", _klammer(zeile),
                         nur="mobilitaet/kraftstoff") == [0] * 12


def test_nur_kontoprognose_does_not_touch_the_yearly_plan():
    """Der Haushalt gibt weiter aus, nur von einem anderen Konto. Die
    Jahresrechnung misst den Haushalt und sieht keinen Unterschied."""
    m = {"cents": -7000, "monate": 12, "buchungen": []}
    zieht_um = _wegfall(["k1"], account_id="karte", nur_kontoprognose=True)
    endet = _wegfall(["k1"], account_id="karte")
    horizont = date(2028, 12, 1)
    assert sz.jahreswirkung(zieht_um, m, 2027, 0, horizont) == 0
    assert sz.restbetrag_je_termin(zieht_um, m) == 0
    assert sz.jahreswirkung(endet, m, 2027, 0, horizont) == 7000
    assert sz.restbetrag_je_termin(endet, m) == round(7000 / 12)



def test_a_series_that_moves_is_measured_only_on_its_account(db):
    """Die Seite zeigt, welche Buchungen zur Zeile gehoeren. Zieht die Reihe nur
    von der Karte weg, gehoert das Tanken auf dem Giro nicht dazu."""
    from finctl.forecast.abgleich import Fenster

    f = Fenster(von=date(2025, 10, 1), bis=date(2026, 9, 30), monate=12)
    zieht_um = _wegfall(["k1"], account_id="karte", nur_kontoprognose=True)
    endet = _wegfall(["k1"], account_id="karte")
    assert {b["account_id"] for b in sz.messung(db, zieht_um, f)["buchungen"]} == {"karte"}
    assert {b["account_id"] for b in sz.messung(db, endet, f)["buchungen"]} == {"karte", "giro"}

def test_the_flag_is_read_only_for_a_wegfall():
    alle = sz.load({"szenarien": [{"id": "k", "name": "K", "aktiv": True, "zeilen": [
        {"label": "a", "art": "wegfall", "start": "2026-10", "konto": "karte",
         "nur_kontoprognose": True},
        {"label": "b", "art": "betrag", "amount_cents": -100, "start": "2026-10",
         "nur_kontoprognose": True}]}]})
    # Die Klammer "Teilzeit" kommt immer dazu (mit_teilzeit).
    k = next(s for s in alle if s.id == "k")
    assert [line.nur_kontoprognose for line in k.lines] == [True, False]


@pytest.mark.skipif(not __import__("pathlib").Path("data/finance.db").exists(),
                    reason="no ledger present")
def test_the_page_needs_the_account_a_series_leaves():
    """Ohne Konto wuerde die Zeile still auf dem Betriebskonto gesucht."""
    pytest.importorskip("httpx")
    import yaml
    from fastapi.testclient import TestClient

    from finctl.pfade import CONFIG_DIR
    from finctl.web.server import app

    client = TestClient(app)
    db = sqlite3.connect("data/finance.db")
    try:
        [konto] = db.execute("SELECT id FROM accounts WHERE ingest_mode = 'parsed' "
                             "ORDER BY id LIMIT 1").fetchone()
    finally:
        db.close()
    sid = "pytest-wegfall"
    zeile = {"szenario": sid, "art": "wegfall", "label": "Weg", "start": "2026-10",
             "kategorie": "konsum/sonstiges", "nur_kontoprognose": True}
    client.post("/api/szenario", json={"id": sid, "loeschen": True})
    try:
        client.post("/api/szenario", json={"neu": True, "name": "Pytest Wegfall"})
        res = client.post("/api/szenario-zeile", json=zeile)
        assert res.status_code == 400 and "Konto" in res.json()["error"]
        assert client.post("/api/szenario-zeile",
                           json={**zeile, "konto": konto}).status_code == 200
        spec = yaml.safe_load((CONFIG_DIR / "szenarien.yaml").read_text(encoding="utf-8"))
        [k] = [s for s in spec["szenarien"] if s["id"] == sid]
        assert k["zeilen"][0]["nur_kontoprognose"] is True
    finally:
        client.post("/api/szenario", json={"id": sid, "loeschen": True})
