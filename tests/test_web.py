"""Local dashboard.

Covers the two write paths, because those can corrupt the ledger: manual
categorization must be sticky, and a split must be refused unless it balances.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from finctl.web.server import app

DB = Path("data/finance.db")
pytestmark = pytest.mark.skipif(not DB.exists(), reason="no ledger present")
client = TestClient(app)


def _ein_objekt(bedingung: str = "1=1") -> str:
    """Eine Objektkennung aus der Datenbank, nicht aus diesem Text.

    Dieselbe Begruendung wie bei `sample_tx`, nur eine Ebene hoeher: Eine
    feste Kennung ist zugleich bruechig UND persoenlich -- sie faengt an zu
    404en, sobald das Objekt anders heisst, und sie traegt eine echte Wohnung
    in eine Datei, die spaeter weitergegeben werden soll.
    """
    import sqlite3

    conn = sqlite3.connect(DB)
    try:
        zeile = conn.execute(
            f"SELECT id FROM properties WHERE {bedingung} ORDER BY id LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not zeile:
        pytest.skip(f"kein Objekt mit {bedingung}")
    return str(zeile[0])


def _rolle(rolle: str) -> str:
    """Das Konto mit dieser Rolle aus forecast.yaml -- wie `_ein_objekt`."""
    from finctl import kontenregeln as kr

    rollen = kr.wirksam().get("account_roles") or {}
    konto = next((k for k, v in rollen.items() if (v or {}).get("role") == rolle), None)
    if konto is None:
        pytest.skip(f"kein Konto mit der Rolle {rolle}")
    return konto


def _geparste_konten() -> list[str]:
    """Die Konten, die aus Auszuegen gelesen werden, in Registerreihenfolge."""
    from finctl import konten

    return [str(k["id"]) for k in konten.laden()
            if k.get("ingest_mode") == "parsed" and k.get("active", True)]


def _kredite() -> list[dict]:
    from finctl.realestate.loan import lade_kredite

    return lade_kredite()


def _bindung_im_abschnitt() -> tuple[str, str]:
    """Ein Kredit, dessen Zinsbindung VOR dem Ende seines letzten Abschnitts
    endet -- und der Tag, an dem die Anschlusskondition greift."""
    from datetime import date, timedelta

    for k in _kredite():
        segs = k.get("segments") or []
        bindung = k.get("zinsbindung_ende") or k.get("zinsbindung_end")
        if not segs or not bindung:
            continue
        bindung = date.fromisoformat(str(bindung))
        start = date.fromisoformat(str(segs[-1]["start"]))
        ende = date.fromisoformat(str(segs[-1]["end"]))
        if start <= bindung < ende:
            return str(k["id"]), (bindung + timedelta(days=1)).isoformat()
    pytest.skip("kein Kredit mit Zinsbindung innerhalb des letzten Abschnitts")
    raise AssertionError            # pragma: no cover


@pytest.fixture
def sample_tx():
    """A transaction id resolved at run time.

    Hard-coding one is brittle: ids shift every time the ledger is rebuilt
    from statements, so a test pinned to id 771 starts 404-ing for reasons
    that have nothing to do with the code under test.
    """
    import sqlite3

    db = sqlite3.connect(DB)
    row = db.execute(
        "SELECT t.id FROM transactions t JOIN splits s ON s.transaction_id = t.id "
        "WHERE t.amount_cents < 0 ORDER BY t.id LIMIT 1").fetchone()
    db.close()
    if row is None:
        pytest.skip("no suitable transaction")
    return row[0]


@pytest.mark.parametrize("path", ["/healthz", "/", "/review", "/flagged"])
def test_pages_render(path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize(("alt", "neu"), [
    ("/review", "/transactions?ansicht=offen"),
    ("/review?account=dkb-giro", "/transactions?account=dkb-giro&ansicht=offen"),
    ("/flagged", "/transactions?ansicht=aufteilen"),
    ("/report?year=2025", "/rueckblick?year=2025&ansicht=vorjahr"),
    ("/monitor", "/rueckblick?ansicht=fixkosten"),
    ("/monitor?umfang=alle&konto=x", "/rueckblick?konto=x&ansicht=alle"),
    ("/prognosebasis", "/annahmen#prognosebasis"),
    ("/bestaende", "/monatsabschluss#konten"),
    ("/kontenregister", "/einrichtung#konten"),
    ("/abos", "/vertraege?ansicht=abos"),
    ("/versicherungen", "/vertraege?ansicht=versicherungen"),
])
def test_a_merged_page_forwards_to_its_tab(alt, neu):
    """Ein Lesezeichen auf die alte Seite landet auf ihrem Reiter, samt Filter."""
    r = client.get(alt, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == neu


def test_the_tabs_count_with_the_other_filters():
    """Offen und Aufzuteilen sind Filter auf Transaktionen, keine eigenen Listen:
    der Reiter zaehlt, was die Ansicht dann auch zeigt."""
    def zahl(html, ansicht):
        m = re.search(rf'ansicht={ansicht}"[^>]*>[^<]*<span class="zahl">(\d+)</span>', html)
        return int(m.group(1))

    def treffer(pfad):
        return int(re.search(r"— (\d+) Treffer", client.get(pfad).text).group(1))

    html = client.get("/transactions").text
    for ansicht in ("offen", "aufteilen"):
        assert zahl(html, ansicht) == treffer(f"/transactions?ansicht={ansicht}")
    # Aufzuteilen ist ein Teil von Offen: ohne Kategorie, nur mit Absicht.
    assert zahl(html, "aufteilen") <= zahl(html, "offen")


def test_review_page_is_not_bloated():
    """Category options are injected once, not repeated per row.

    97 categories across 40 rows is ~300KB of identical markup, which made a
    page used daily feel sluggish for no reason.

    Gemessen je ZEILE statt als absolute Groesse: die Seite waechst mit dem,
    was in der Warteschlange steht, und ein fester Deckel bricht, sobald ein
    Buchungstext laenger wird -- was er tat, als PayPal ein Konto wurde und
    Absenderadressen mitbrachte. Die Aussage war nie "die Seite ist klein",
    sondern "die Kategorienliste steht einmal da".

    Gemessen wird deshalb das Markup der Zeilen selbst. Die ganze Seite durch
    die Zeilenzahl zu teilen legte den festen Teil (Stil, Kopf, Skripte) auf
    die Zeilen um: je kuerzer die Warteschlange, desto "schwerer" jede Zeile.
    """
    import re

    seite = client.get("/transactions?ansicht=offen").text
    zeilen = re.findall(r'<tr id="row-.*?</tr>', seite, re.S)
    if not zeilen:
        # Eine abgearbeitete Warteschlange ist der Normalfall, kein Fehler --
        # nur laesst sich an null Zeilen nichts ueber ihre Groesse sagen.
        pytest.skip("Review-Warteschlange ist leer")
    assert seite.count("const CATS") == 1, "Kategorienliste je Zeile wiederholt"
    assert sum(map(len, zeilen)) / len(zeilen) < 3_000


def test_unknown_transaction_is_404():
    assert client.get("/tx/99999999").status_code == 404
    assert client.post("/api/categorize/99999999",
                       data={"mgmt": "konsum/sonstiges"}).status_code == 404


def test_split_must_balance(sample_tx):
    """The ledger invariant is enforced at the API, not just in the UI."""
    res = client.post(f"/api/split/{sample_tx}", json={
        "parts": [{"amount_cents": -1, "mgmt": "konsum/gaming"}]})
    assert res.status_code == 400
    assert "off by" in res.json()["error"]


def test_split_with_no_parts_is_refused(sample_tx):
    assert client.post(f"/api/split/{sample_tx}", json={"parts": []}).status_code == 400


def test_unknown_category_is_a_clear_400_not_a_500(sample_tx):
    """A renamed category must not surface as a foreign-key crash.

    This happens naturally: rename a category in taxonomy.yaml and any caller
    still using the old id hits it. A 500 tells them nothing.
    """
    import sqlite3

    db = sqlite3.connect(DB)
    total = db.execute("SELECT amount_cents FROM transactions WHERE id=?",
                       (sample_tx,)).fetchone()[0]
    db.close()

    res = client.post(f"/api/split/{sample_tx}", json={
        "parts": [{"amount_cents": total, "mgmt": "abo/does-not-exist"}]})
    assert res.status_code == 400
    assert "unknown category" in res.json()["error"]

    res = client.post(f"/api/categorize/{sample_tx}", data={"mgmt": "nope/nope"})
    assert res.status_code == 400


def test_balanced_split_is_accepted_and_marked_manual(sample_tx):
    import sqlite3

    db = sqlite3.connect(DB)
    total = db.execute("SELECT amount_cents FROM transactions WHERE id=?",
                       (sample_tx,)).fetchone()[0]
    before = db.execute(
        "SELECT amount_cents, mgmt_category_id, tax_category_id, source FROM splits "
        "WHERE transaction_id=? ORDER BY seq", (sample_tx,)).fetchall()
    db.close()

    # Categories are resolved from the database rather than named literally:
    # a rename in taxonomy.yaml should not break an unrelated API test.
    db = sqlite3.connect(DB)
    cats = [r[0] for r in db.execute(
        "SELECT id FROM mgmt_categories WHERE parent_id IS NOT NULL "
        "AND active = 1 ORDER BY id LIMIT 2")]
    db.close()

    half = total // 2
    res = client.post(f"/api/split/{sample_tx}", json={"parts": [
        {"amount_cents": half, "mgmt": cats[0], "note": "part one"},
        {"amount_cents": total - half, "mgmt": cats[1], "note": "part two"},
    ]})
    assert res.status_code == 200, res.json()

    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT amount_cents, source FROM splits WHERE transaction_id=? ORDER BY seq",
        (sample_tx,)).fetchall()
    assert [r[0] for r in rows] == [half, total - half]
    assert all(r[1] == "manual" for r in rows)

    # Restore what was there: the tests run against the real ledger, and a test
    # must not leave the user's categorization rewritten.
    db.execute("DELETE FROM splits WHERE transaction_id=?", (sample_tx,))
    for seq, (cents, mgmt, tax, source) in enumerate(before):
        db.execute(
            "INSERT INTO splits (transaction_id, seq, amount_cents, mgmt_category_id, "
            "tax_category_id, source, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))",
            (sample_tx, seq, cents, mgmt, tax, source))
    db.commit()
    db.close()


def test_leaf_filter_does_not_bleed_into_its_siblings():
    """A leaf id is matched exactly, a parent id by prefix.

    "einkommen/gehalt" as a LIKE prefix also matched
    "einkommen/gehalt-nebentaetig" -- a different income, taxed differently and
    reported on a different Anlage. Filtering to one silently showed both.
    """
    import re

    def hits(category):
        body = client.get("/transactions", params={"category": category}).text
        return int(re.search(r"— (\d+) Treffer", body, re.I).group(1))

    leaf = hits("einkommen/gehalt")
    sibling = hits("einkommen/gehalt-nebentaetig")
    parent = hits("einkommen")
    assert leaf + sibling <= parent
    if sibling:
        assert leaf < parent


def test_multiple_categories_are_ored_not_intersected():
    """Two filters must widen the result, never narrow it to nothing."""
    import re

    def hits(params):
        body = client.get("/transactions", params=params).text
        return int(re.search(r"— (\d+) Treffer", body, re.I).group(1))

    a = hits({"category": ["lebensmittel"]})
    b = hits({"category": ["gastronomie"]})
    both = hits({"category": ["lebensmittel", "gastronomie"]})
    assert both == a + b


def test_a_manual_split_is_always_recorded_outside_the_database():
    """Marking a split 'manual' without recording it is silent data loss.

    It survives `categorize --recompute`, which makes it look safe, but not a
    rebuild from statements -- the exact failure overrides.yaml exists to
    prevent. One endpoint reintroduced it and put ten corrections, including
    two Casa Con settlements, one `rm data/finance.db` from gone.
    """
    import sqlite3

    from finctl.rules.categorize import unrecorded_manual_splits

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        assert unrecorded_manual_splits(db) == []
    finally:
        db.close()


def test_nothing_in_the_runtime_reaches_the_network_or_a_model():
    """The founding constraint, asserted rather than trusted.

    "The operations of the tool need to run without AI to keep cost low."
    AI authored the rules; execution is ordinary Python. A stray `import
    requests` added later would break that silently and only show up on a
    machine with no network.
    """
    import pathlib
    import re

    banned = re.compile(
        r"^\s*(?:from|import)\s+(requests|httpx|urllib|openai|anthropic|socket)\b",
        re.M)
    # Der Starter der App zum Doppelklicken fragt einen Port auf DIESEM
    # Rechner ab (frei? laeuft schon Finance OS?). Dafuer braucht er socket
    # und urllib -- und darf nirgendwo sonst hin.
    nur_lokal = {"finctl/starter.py"}
    offenders = []
    for path in pathlib.Path("finctl").rglob("*.py"):
        if path.as_posix() in nur_lokal:
            continue
        hit = banned.search(path.read_text(encoding="utf-8"))
        if hit:
            offenders.append(f"{path}: {hit.group(0).strip()}")
    assert not offenders, offenders

    from finctl import starter

    quelle = pathlib.Path("finctl/starter.py").read_text(encoding="utf-8")
    assert starter.HOST == "127.0.0.1"
    assert not re.search(r"https?://(?!\{HOST\})", quelle), "der Starter nennt eine fremde Adresse"


def test_the_dashboard_binds_to_localhost_by_default():
    """Local heisst local.

    Die Vorgabe steht jetzt in config/server.yaml und nicht mehr als
    Kommandozeilenschalter -- ein Schalter wird vergessen, eine Datei nicht.
    Geprueft wird deshalb die WIRKUNG und nicht die Signatur: was bindet der
    Server, wenn niemand etwas angibt.
    """
    from finctl.web import auth

    # Die echte config/server.yaml ist privat und darf im Netz stehen; die
    # Vorgabe ist, was ohne Datei gilt (conftest legt keine an).
    host, port = auth.bindung()
    assert host == "127.0.0.1", host
    assert port > 0

    # Und die Datei, sobald sie geschrieben wird, sagt, was ein anderer Wert bedeutet.
    assert "Passwort" in auth.KOPF


def test_update_and_backup_share_the_cli_code_path():
    """One implementation, two callers.

    The dashboard buttons could have shelled out to the CLI or kept a second
    copy of the loop. Both age badly: the copy drifts, and a subprocess turns
    a clear exception into an exit code and a wall of text.
    """
    import inspect

    from finctl import cli

    assert "ops.ingest_all" in inspect.getsource(cli.ingest_run)
    assert "ops.write_backup" in inspect.getsource(cli.backup)


def test_the_backup_target_comes_from_config_not_code():

    from finctl import ops

    target, keep = ops.backup_settings()
    assert target.is_absolute(), target      # ~ expanded, not left literal
    assert keep >= 1


def test_a_backup_contains_the_data_folder_and_nothing_else(tmp_path):
    """Gesichert wird, was nur hier existiert -- nicht der Quelltext.

    Bis zum 23.09.2026 packte jedes Archiv `finctl/` und `tests/` mit: bei
    jedem Lauf dieselben Dateien, die ohnehin in der Versionsverwaltung
    liegen. Das verwechselt zwei Dinge, die getrennt gehoeren -- was der
    Nutzer entschieden hat, und womit er es aufgeschrieben hat.

    Das Blatt `stand.yaml` muss mit, weil ein Archiv ohne Code sonst aussieht
    wie ein unvollstaendiges.
    """
    import tarfile

    from finctl import ops

    out = ops.write_backup(tmp_path, keep=12)
    with tarfile.open(out["archive"]) as tar:
        names = tar.getnames()

    for needed in ops.ARCHIVTEILE:
        assert any(n == needed or n.startswith(needed + "/") for n in names), needed

    oberste = {n.split("/")[0] for n in names}
    assert oberste == set(ops.ARCHIVTEILE), sorted(oberste)
    for code in ("finctl", "tests", "pyproject.toml", "HANDBUCH.md"):
        assert code not in oberste

    # Und das Blatt sagt, was fehlt und wie man zurueckkommt.
    with tarfile.open(out["archive"]) as tar:
        blatt = tar.extractfile("stand.yaml").read().decode("utf-8")
    assert "NICHT ENTHALTEN ist das Programm" in blatt
    assert "restore" in blatt and "ort --setzen" in blatt


def test_old_snapshots_are_pruned(tmp_path):
    """A folder with two hundred archives is a folder nobody looks in."""
    from finctl import ops

    for i in range(4):
        (tmp_path / f"finance-os_2020010{i}-000000.tar.gz").write_bytes(b"x")
    out = ops.write_backup(tmp_path, keep=3)
    assert out["kept"] == 3
    assert len(list(tmp_path.glob("finance-os_*.tar.gz"))) == 3
    # The one just written is never the one pruned.
    assert Path(out["archive"]).exists()


@pytest.mark.parametrize("path", ["/konten", "/rueckblick", "/steuer"])
def test_later_pages_render(path):
    assert client.get(path).status_code == 200


def test_a_manual_decision_moves_from_the_bank_leg_to_the_payment(tmp_path):
    """Die Entscheidung gehört an die Zahlung, nicht an ihre Deckung.

    Solange PayPal ein Händler war, hat der Eigentümer 51 Mal von Hand
    eingetragen, wofür eine Zahlung war -- das ist das einzige Wissen im
    Werkzeug, das aus keiner Datei zurückzurechnen ist. Seit PayPal ein Konto
    ist, sitzt es an der falschen Zeile: die Bankzeile ist nur noch die
    Umbuchung, und bliebe die Kategorie dort, stünde der Betrag zweimal im
    Ledger.
    """
    import sqlite3

    import yaml

    from finctl.ingest import paypal

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(open("finctl/ledger/schema.sql", encoding="utf-8").read())
    for konto in ("paypal", "c24"):
        db.execute("INSERT INTO accounts (id, display_name, institution, "
                   "account_type, ingest_mode, parser_profile) "
                   "VALUES (?,?,'X','giro','parsed','p')", (konto, konto))
    db.execute("INSERT INTO statements (id, account_id, source_path, source_name, "
               "file_sha256, period_start, period_end, balance_start_cents, "
               "balance_end_cents, parser_profile, parser_version, status, imported_at) "
               "VALUES (1,'paypal','p','n','h','2025-01-01','2025-12-31',0,0,"
               "'paypal_csv','1','imported','t')")
    db.execute("INSERT INTO mgmt_categories (id, name, kind) "
               "VALUES ('gastronomie/restaurant','Restaurant','expense')")
    # Die Zahlung, ihre Deckung -- über den zugehörigen Code verbunden -- und
    # die Bankzeile, die dieselbe Deckung von der anderen Seite zeigt.
    zeilen = [
        (1, "paypal", "2025-07-02", -9000, "Handyzahlung Erika Muster AAA",
         "Handyzahlung", "AAA"),
        (2, "paypal", "2025-07-02", 9000,
         "Bankgutschrift auf PayPal-Konto BBB zu AAA",
         "Bankgutschrift auf PayPal-Konto", "BBB"),
        (3, "c24", "2025-07-04", -9000, "Lastschrift PayPal Europe",
         "Lastschrift", None),
    ]
    for tid, konto, wann, cents, roh, zweck, ref in zeilen:
        db.execute("INSERT INTO transactions (id, account_id, statement_id, "
                   "booking_date, amount_cents, raw_text, purpose, customer_ref, "
                   "counterparty, seq_in_statement, dedup_hash, created_at) "
                   "VALUES (?,?,1,?,?,?,?,?,?,?,?,'t')",
                   (tid, konto, wann, cents, roh, zweck, ref,
                    "Erika Muster" if tid == 1 else None, tid, f"h{tid}"))
        db.execute("INSERT INTO splits (transaction_id, seq, amount_cents, source, "
                   "mgmt_category_id, created_at, updated_at) "
                   "VALUES (?,0,?,?,?,'t','t')",
                   (tid, cents, "manual" if tid == 3 else "default",
                    "gastronomie/restaurant" if tid == 3 else None))

    pfad = tmp_path / "overrides.yaml"
    pfad.write_text(yaml.safe_dump({"overrides": [{
        "dedup_hash": "h3", "when": "2025-07-04", "account": "c24",
        "amount_cents": -9000, "text": "Lastschrift PayPal Europe",
        "parts": [{"amount_cents": -9000, "mgmt": "gastronomie/restaurant",
                   "tax": None, "property": None, "note": None}]}]}), encoding="utf-8")

    fertig = paypal.uebernehmen(db, pfad)
    assert [f["gegenpartei"] for f in fertig] == ["Erika Muster"]

    # Die Kategorie sitzt jetzt an der Zahlung ...
    assert db.execute("SELECT mgmt_category_id FROM splits WHERE transaction_id = 1"
                      ).fetchone()["mgmt_category_id"] == "gastronomie/restaurant"
    # ... und die Bankzeile trägt sie NICHT mehr, weder in der Datenbank noch
    # in der Datei. Sonst zählte derselbe Betrag zweimal.
    assert db.execute("SELECT COUNT(*) FROM splits WHERE transaction_id = 3"
                      ).fetchone()[0] == 0
    uebrig = yaml.safe_load(pfad.read_text(encoding="utf-8"))["overrides"]
    assert [e["account"] for e in uebrig] == ["paypal"]


def test_a_payout_that_clears_the_balance_finds_the_payment_it_covers(tmp_path):
    """Eine Auszahlung trägt keinen zugehörigen Code.

    Sie gehört zu keiner einzelnen Zahlung, sondern räumt das Guthaben ab --
    und damit zu allem seit dem letzten Nullstand. Das steht im laufenden
    Saldo und muss nicht geraten werden.
    """
    import sqlite3

    import yaml

    from finctl.ingest import paypal

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(open("finctl/ledger/schema.sql", encoding="utf-8").read())
    for konto in ("paypal", "c24"):
        db.execute("INSERT INTO accounts (id, display_name, institution, "
                   "account_type, ingest_mode, parser_profile) "
                   "VALUES (?,?,'X','giro','parsed','p')", (konto, konto))
    db.execute("INSERT INTO statements (id, account_id, source_path, source_name, "
               "file_sha256, period_start, period_end, balance_start_cents, "
               "balance_end_cents, parser_profile, parser_version, status, imported_at) "
               "VALUES (1,'paypal','p','n','h','2025-01-01','2025-12-31',0,0,"
               "'paypal_csv','1','imported','t')")
    db.execute("INSERT INTO mgmt_categories (id, name, kind) "
               "VALUES ('konsum/event','Event','expense')")
    zeilen = [
        (1, "paypal", "2025-03-23", 3500, "Handyzahlung Max Beispiel CCC",
         "Handyzahlung", "CCC"),
        (2, "paypal", "2025-03-24", -3500,
         "Von Nutzer eingeleitete Abbuchung DDD",
         "Von Nutzer eingeleitete Abbuchung", "DDD"),
        (3, "c24", "2025-03-24", 3500, "Überweisung PayPal Europe",
         "Überweisung", None),
    ]
    for tid, konto, wann, cents, roh, zweck, ref in zeilen:
        db.execute("INSERT INTO transactions (id, account_id, statement_id, "
                   "booking_date, amount_cents, raw_text, purpose, customer_ref, "
                   "seq_in_statement, dedup_hash, created_at) "
                   "VALUES (?,?,1,?,?,?,?,?,?,?,'t')",
                   (tid, konto, wann, cents, roh, zweck, ref, tid, f"h{tid}"))
        db.execute("INSERT INTO splits (transaction_id, seq, amount_cents, source, "
                   "created_at, updated_at) VALUES (?,0,?,'default','t','t')",
                   (tid, cents))

    pfad = tmp_path / "overrides.yaml"
    pfad.write_text(yaml.safe_dump({"overrides": [{
        "dedup_hash": "h3", "when": "2025-03-24", "account": "c24",
        "amount_cents": 3500, "text": "Überweisung PayPal Europe",
        "parts": [{"amount_cents": 3500, "mgmt": "konsum/event",
                   "tax": None, "property": None, "note": None}]}]}), encoding="utf-8")

    fertig = paypal.uebernehmen(db, pfad)
    assert [f["zahlung_id"] for f in fertig] == [1]
    assert db.execute("SELECT mgmt_category_id FROM splits WHERE transaction_id = 1"
                      ).fetchone()["mgmt_category_id"] == "konsum/event"


def test_the_transactions_table_has_one_category_column():
    """A read-only "Aktuell" beside an empty "Neu" cost 330px of a table whose
    most-read column is the Verwendungszweck. One column, prefilled."""
    import re

    body = client.get("/transactions", params={"limit": 3}).text
    header = re.search(r"<tr class=\"kopf\"><th style=\"width:\d+px\">Datum.*?</tr>",
                       body, re.S).group(0)
    heads = [re.sub(r"<[^>]+>", "", c).strip()
             for c in re.findall(r"<th[^>]*>(.*?)</th>", header, re.S)]
    assert "Kategorie" in heads
    assert "Aktuell" not in heads and "Neu" not in heads

    # Prefilled, and carrying what it was prefilled WITH, so a save can tell
    # a real change from an untouched row.
    assert re.search(r'id="cat-\d+"[^>]*value="[^"]+"[^>]*data-cur="[^"]+"', body)


def test_saving_an_untouched_row_writes_nothing():
    """Re-posting what a rule decided would freeze it as a manual override,
    immune to every later correction of the rulebook."""
    src = Path("finctl/web/templates/transactions.html").read_text(encoding="utf-8")
    assert "sel.dataset.id !== (sel.dataset.cur || '')" in src
    assert "if(catChanged) body.append('mgmt'" in src


def test_a_transaction_row_saves_on_choice_without_a_button():
    """Kategorie und Objekt speichern beim Waehlen; ein Knopf je Zeile entfaellt.

    Zweihundert Speichern-Knoepfe machten die Tabelle bei 1280 Pixeln so
    breit, dass die Spalte "Regel" hinter dem Scrollen lag -- fuer einen
    Knopf, den die Kategorie schon nicht brauchte.
    """
    body = client.get("/transactions", params={"limit": 3}).text
    zeilen = body[body.index('<table class="tx">'):]
    assert ">Speichern</button>" not in zeilen[:zeilen.index("</table>")]
    assert 'onchange="save(' in body
    src = Path("finctl/web/templates/transactions.html").read_text(encoding="utf-8")
    # Nur ein GEAENDERTES Objekt wird geschrieben, wie die Kategorie.
    assert "if(propChanged) body.append('property'" in src


def test_the_conflicts_box_starts_collapsed():
    """It is a diagnosis, not a task. It belongs available, not in front of the
    table the page is opened for -- it was taking half the screen.

    <details> rather than JavaScript: the state belongs in the markup, and it
    keeps working if no script runs.
    """
    body = client.get("/transactions", params={"limit": 3}).text
    if '<details class="note bad">' not in body:
        pytest.skip("no rule conflicts recorded")
    import re
    head = re.search(r'<details class="note bad">.*?</summary>', body, re.S).group(0)
    assert "open" not in head.split(">")[0]     # collapsed by default
    assert "Regeln" in head                     # says how many, before opening

@pytest.mark.parametrize("path", ["/monatsabschluss", "/kredite", "/kredite?jahr=2045"])
def test_the_new_screens_render(path):
    assert client.get(path).status_code == 200


def test_every_nav_link_resolves():
    """A link in the header that 404s is worse than no link.

    The navigation is the only map of what the tool can do, so it is the one
    place where a dead entry actively misleads.
    """
    import re

    html = client.get("/").text
    links = set(re.findall(r'href="(/[a-z-]*)"', html))
    assert links, "navigation is empty"
    for link in sorted(links):
        assert client.get(link).status_code == 200, f"{link} is dead"


def test_goals_page_shows_the_declared_targets():
    """Until this was wired up there were two goal systems and the second one
    had no screen at all -- it drove nothing and nobody could see it.

    Geprueft wird gegen die KONFIGURATION, nicht gegen einen Zielnamen: bis
    auf den Puffer, an dem die Kaskade haengt, wird jedes Ziel auf der Seite
    angelegt und kann jederzeit wieder verschwinden.
    """
    html = client.get("/ziele").text
    ziele = _ziele_wie_konfiguriert()
    for ziel in ziele:
        assert f'data-ziel="{ziel["id"]}"' in html, ziel["id"]
        assert ziel["name"] in html, ziel["id"]
    # Der obere Balken misst den BESTAND, ohne jede Renditeannahme -- es gibt
    # deshalb keine Coast-Zahl mehr, die man mit ihm verwechseln koennte.
    #
    # Geprueft wird die SEITE ohne die Zielnamen: wie jemand sein Ziel nennt,
    # ist seine Sache -- "Coast Fire" als Name ist kein gerechneter Coast-Wert.
    ohne_namen = html
    for ziel in ziele:
        ohne_namen = ohne_namen.replace(ziel["name"], "")
    assert "Coast" not in ohne_namen


def _ziele_wie_konfiguriert() -> list[dict]:
    """Die geltenden Ziele: goals.yaml mit dem Overlay von der Seite."""
    import yaml

    from finctl.forecast import ziele as _z

    def lesen(name):
        pfad = Path("config") / name
        return (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}) if pfad.exists() else {}

    zusammen = _z.merge_edits(lesen("goals.yaml"), lesen("ziele_custom.yaml"))
    return [g for g in (zusammen.get("ziele") or []) if g.get("id")]


def test_a_note_can_be_changed_and_an_empty_field_restores_the_base():
    """Die Notiz erklaert die Zahl -- und muss aenderbar sein, ohne die
    Begruendung in der Basisdatei zu loeschen.

    Leer heisst deshalb LOESCHEN der eigenen Notiz, nicht "leere Notiz":
    sonst waere die Begruendung aus balances.yaml unsichtbar, ohne dass sie
    jemand entfernt haette.
    """
    import yaml

    from finctl import bestaende as best

    basis = yaml.safe_load(Path("config/balances.yaml").read_text(encoding="utf-8"))
    mit_notiz = next((b for b in basis["balances"] if (b.get("note") or "").strip()), None)
    if mit_notiz is None:
        pytest.skip("keine Position mit Notiz")
    key = mit_notiz.get("account_id") or mit_notiz["name"]
    vorher = Path("config/balances.yaml").read_bytes()
    try:
        assert client.post("/api/bestand", json={"key": key, "note": "eigene Notiz"}).json()["ok"]
        assert best.overlay()["overrides"][key]["note"] == "eigene Notiz"
        assert "eigene Notiz" in client.get("/monatsabschluss").text
        client.post("/api/bestand", json={"key": key, "note": "  "})
        assert "note" not in (best.overlay()["overrides"].get(key) or {})
        assert mit_notiz["note"].strip()[:40] in client.get("/monatsabschluss").text
    finally:
        client.post("/api/bestand", json={"key": key, "remove": True})
    assert Path("config/balances.yaml").read_bytes() == vorher


def test_a_remaining_liability_is_named_until_it_is_struck():
    """Verbindlichkeiten gehoeren in die Planung. Was noch in balances.yaml
    steht, zieht weiter ab -- und darf deshalb nicht unsichtbar werden, bis es
    gestrichen ist. Ein gespeicherter Stand darf die Streichung nicht
    zuruecknehmen."""
    import yaml

    from finctl import bestaende as best

    vorher = Path("config/balances.yaml").read_bytes()
    # WORTWOERTLICH sichern und zuruecklegen, nicht ueber YAML. Ein
    # safe_dump im finally warf den Kopfkommentar der Datei weg -- genau der
    # Schaden, gegen den die Overlay-Datei ueberhaupt existiert.
    eigen = Path("config/balances_custom.yaml")
    eigen_vorher = eigen.read_text(encoding="utf-8") if eigen.exists() else None
    try:
        assert client.post("/api/verpflichtung",
                           json={"name": "Pytest-Schuld", "cents": 123400,
                                 "note": "aus dem Test"}).json()["ok"]
        # Gepflegt wird sie nicht mehr hier, sondern in der Planung. Solange
        # sie abzieht, nennt der Abschnitt sie -- samt Knopf zum Streichen.
        html = client.get("/monatsabschluss").text
        assert "Pytest-Schuld streichen" in html
        basis = yaml.safe_load(Path("config/balances.yaml").read_text(encoding="utf-8"))
        offen = best.verpflichtungen(basis, best.overlay()["verpflichtungen"])
        assert any(o["name"] == "Pytest-Schuld" and o["cents"] == 123400 for o in offen)

        # Gestrichen: die Zeile verschwindet, die Basisdatei bleibt unberuehrt.
        client.post("/api/verpflichtung", json={"kennung": "pytest-schuld", "remove": True})
        offen = best.verpflichtungen(basis, best.overlay()["verpflichtungen"])
        assert not any(o["name"] == "Pytest-Schuld" for o in offen)
        # Ein danach abgelegter Stand schreibt die Datei neu -- ohne die
        # Streichung zu verlieren.
        irgendein = next(b for b in basis["balances"] if b.get("name"))
        client.post("/api/bestand", json={"key": irgendein.get("account_id") or irgendein["name"],
                                          "note": "Pytest-Notiz"})
        assert best.overlay()["verpflichtungen"]["pytest-schuld"] == {"geloescht": True}
        assert "Pytest-Schuld" not in client.get("/monatsabschluss").text
    finally:
        if eigen_vorher is None:
            eigen.unlink(missing_ok=True)
        else:
            eigen.write_text(eigen_vorher, encoding="utf-8")
    assert Path("config/balances.yaml").read_bytes() == vorher


def test_a_balance_override_does_not_touch_the_documented_base(tmp_path):
    """balances.yaml carries the reasoning for every position.

    A dashboard that rewrites it destroys that on the first click, so edits go
    to a sibling file and the base stays as written.
    """
    from pathlib import Path

    base = Path("config/balances.yaml")
    before = base.read_text(encoding="utf-8")
    custom = Path("config/balances_custom.yaml")
    had = custom.read_text(encoding="utf-8") if custom.exists() else None
    try:
        res = client.post("/api/bestand", json={
            "key": "pytest-position", "cents": 12345, "as_of": "2026-09-12"})
        assert res.status_code == 200
        assert base.read_text(encoding="utf-8") == before
        assert "pytest-position" in custom.read_text(encoding="utf-8")
        client.post("/api/bestand", json={"key": "pytest-position", "remove": True})
        assert "pytest-position" not in custom.read_text(encoding="utf-8")
    finally:
        if had is None:
            custom.unlink(missing_ok=True)
        else:
            custom.write_text(had, encoding="utf-8")


def test_a_balance_without_an_amount_is_refused():
    assert client.post("/api/bestand", json={"key": "x"}).status_code == 400
    assert client.post("/api/bestand", json={"cents": 1}).status_code == 400


# ------------------------------------------------- toggleable scenarios

def _clean_scenario(client, sid):
    """Restlos weg, auch wenn es eine Verpflichtung MIT Zeilen ist.

    Ein blosses loeschen kam daran nicht vorbei -- genau die Sperre, die
    test_an_obligation_can_be_renamed_but_still_not_switched_off prueft --, und
    der Rueckstand blieb in der Konfiguration des Eigentuemers stehen. Tests
    schreiben hier in die echten Dateien; was sie anlegen, muessen sie auch
    unter ihren eigenen Sperren wieder loswerden.
    """
    import yaml

    spec = yaml.safe_load(Path("config/szenarien.yaml").read_text(encoding="utf-8")) or {}
    ziel = next((x for x in spec.get("szenarien") or [] if x.get("id") == sid), None)
    for i in reversed(range(len(ziel.get("zeilen") or []))) if ziel else ():
        client.post("/api/szenario-zeile",
                    json={"szenario": sid, "index": i, "loeschen": True})
    client.post("/api/szenario", json={"id": sid, "loeschen": True})


def test_a_scenario_can_be_created_filled_toggled_and_removed():
    """The whole loop, because each step is useless without the others."""
    sid = "pytest-plan"
    _clean_scenario(client, sid)
    try:
        assert client.post("/api/szenario",
                           json={"neu": True, "name": "Pytest Plan"}).json()["id"] == sid
        assert client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "Rate", "frequenz": "monatlich",
            "amount_cents": -35000, "start": "2026-11", "ende": "2031-10",
            "kategorie": "mobilitaet/kfz-finanzierung",
            "konto": "trade-republic"}).status_code == 200
        assert client.post("/api/szenario",
                           json={"id": sid, "aktiv": True}).status_code == 200
        assert "Pytest Plan" in client.get("/planung").text
    finally:
        _clean_scenario(client, sid)
        assert "Pytest Plan" not in client.get("/planung").text


def test_an_inactive_scenario_changes_nothing_in_the_forecast():
    """Parking a plan must remove its effect completely, not partly.

    If a switched-off plan still moved the projection, the toggle would be
    decoration and every number downstream would be quietly wrong.
    """
    import re

    sid = "pytest-forecast"
    _clean_scenario(client, sid)
    try:
        client.post("/api/szenario", json={"neu": True, "name": "Pytest Forecast"})
        client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "Brocken", "frequenz": "einmalig",
            "amount_cents": -900000, "start": "2026-12",
            "kategorie": "konsum/sonstiges"})

        def figures():
            """Alle Zahlen der Seite, nicht die ersten fünfundzwanzig.

            Der Ausschnitt traf ursprünglich den geänderten Monat und wanderte
            weg, als die Seite einen Horizontschalter bekam -- der Test wurde
            rot, obwohl der Toggle nachweislich 9.000 bewegt. Ein Test, der an
            einer Zeilennummer der Ausgabe hängt, prüft das Layout und nicht
            das Verhalten.
            """
            return re.findall(r"-?[\d.]+,\d\d", client.get("/konten").text)

        client.post("/api/szenario", json={"id": sid, "aktiv": False})
        off = figures()
        client.post("/api/szenario", json={"id": sid, "aktiv": True})
        on = figures()
        assert on != off, "an active scenario must move the forecast"
        client.post("/api/szenario", json={"id": sid, "aktiv": False})
        assert figures() == off, "switching off must restore the original figures"
    finally:
        _clean_scenario(client, sid)


def test_a_line_is_refused_rather_than_stored_half_built():
    sid = "pytest-guard"
    _clean_scenario(client, sid)
    try:
        client.post("/api/szenario", json={"neu": True, "name": "Pytest Guard"})
        for bad in ({"label": "", "frequenz": "monatlich", "amount_cents": -1,
                     "start": "2026-11", "kategorie": "konsum/sonstiges"},
                    {"label": "x", "frequenz": "stuendlich", "amount_cents": -1,
                     "start": "2026-11", "kategorie": "konsum/sonstiges"},
                    {"label": "x", "frequenz": "monatlich", "start": "2026-11",
                     "kategorie": "konsum/sonstiges"},
                    {"label": "x", "frequenz": "monatlich", "amount_cents": -1,
                     "kategorie": "konsum/sonstiges"},
                    {"label": "x", "frequenz": "monatlich", "amount_cents": -1,
                     "start": "2026-11"}):
            res = client.post("/api/szenario-zeile", json={"szenario": sid, **bad})
            assert res.status_code == 400, bad
    finally:
        _clean_scenario(client, sid)


def test_a_duplicate_scenario_name_is_refused():
    sid = "pytest-dup"
    _clean_scenario(client, sid)
    try:
        client.post("/api/szenario", json={"neu": True, "name": "Pytest Dup"})
        assert client.post("/api/szenario",
                           json={"neu": True, "name": "Pytest Dup"}).status_code == 400
    finally:
        _clean_scenario(client, sid)


# ------------------------------------------------- deleting a category

def test_a_category_with_bookings_cannot_be_deleted():
    """Deleting one that is in use would strip the category off real splits.

    They keep their amount and leave every report -- the failure mode that is
    hardest to notice, because every household total still adds up. `retire`
    exists for that case and demands somewhere for the splits to go.
    """
    res = client.post("/api/category",
                      json={"action": "delete", "id": "lebensmittel/supermarkt"})
    assert res.status_code == 409
    assert "Buchungen" in res.json()["error"]


def test_an_unused_category_can_be_created_and_deleted():
    cid = "konsum/pytest-temporaer"
    client.post("/api/category", json={"action": "delete", "id": cid})
    created = client.post("/api/category",
                          json={"action": "add", "parent": "konsum",
                                "name": "Pytest Temporaer"})
    assert created.status_code == 200
    assert client.post("/api/category",
                       json={"action": "delete", "id": cid}).status_code == 200
    assert client.get("/kategorien").text.count("pytest-temporaer") == 0


def test_a_scenario_line_without_a_category_is_refused():
    """A line with no category is a lump sum with a name: switched on it adds
    money movements that no report can place."""
    sid = "pytest-kat"
    client.post("/api/szenario", json={"id": sid, "loeschen": True})
    try:
        client.post("/api/szenario", json={"neu": True, "name": "Pytest Kat"})
        res = client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "Rate", "frequenz": "monatlich",
            "amount_cents": -1000, "start": "2026-11"})
        assert res.status_code == 400
        assert "Kategorie" in res.json()["error"]
        assert client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "Rate", "frequenz": "monatlich",
            "amount_cents": -1000, "start": "2026-11",
            "kategorie": "konsum/sonstiges"}).status_code == 200
    finally:
        client.post("/api/szenario", json={"id": sid, "loeschen": True})


# ------------------------------------------------- editing a goal

def test_a_goal_edit_overlays_without_rewriting_the_documented_base():
    """goals.yaml carries the derivation of every figure.

    A target of 1.092.608 with no note is unauditable in a year -- nobody
    reconstructs "30.000 times 25 inflated over nineteen years" from the
    number alone. So the reasoning is an editable field, and the base file
    that explains it is never rewritten.
    """
    from pathlib import Path

    base = Path("config/goals.yaml")
    before = base.read_text(encoding="utf-8")
    overlay = Path("config/ziele_custom.yaml")
    had = overlay.read_text(encoding="utf-8") if overlay.exists() else None
    import re

    import yaml as _yaml

    # Ein sichtbares Ziel aus goals.yaml -- welches, entscheidet der Eigentuemer
    # auf der Seite, nicht der Test.
    dokumentiert = {g["id"] for g in _yaml.safe_load(before)["ziele"]}
    gid = next(g for g in re.findall(r'data-ziel="([^"]+)"', client.get("/ziele").text)
               if g in dokumentiert)
    try:
        res = client.post("/api/ziel", json={
            "id": gid, "cents": 12_345_600, "stichtag": "2046-01-01",
            "notiz": "pytest"})
        assert res.status_code == 200
        assert base.read_text(encoding="utf-8") == before
        html = client.get("/ziele").text
        assert "123.456,00" in html and "pytest" in html
    finally:
        client.post("/api/ziel", json={"id": gid, "zuruecksetzen": True})
        if had is None:
            overlay.unlink(missing_ok=True)
        else:
            overlay.write_text(had, encoding="utf-8")


def test_the_retired_percentage_targets_are_gone_from_the_settings():
    """They drove a SECOND Barista FIRE definition that never touched the bar.

    Two definitions of one goal is worse than one imprecise definition,
    because the wrong one is the editable one.
    """
    html = client.get("/ziele").text
    assert "coverage_target_pct" not in html
    assert "barista_target_pct" not in html
    assert "objekt_remaining_cents" not in html


def test_a_retired_category_with_nothing_attached_offers_deletion():
    """The button sat inside the active branch, so the normal case for
    deleting -- a category that has already handed its bookings over -- was
    the one case with no button at all."""
    import yaml

    remap = yaml.safe_load(Path("config/taxonomy_migrations.yaml").read_text(
        encoding="utf-8")).get("remap") or {}
    html = client.get("/kategorien").text
    assert any(alt in html for alt in remap), "keine abgeloeste Kategorie auf der Seite"
    assert "loeschen(" in html


def test_a_toggled_scenario_reaches_the_account_forecast():
    """The toggle has to move the projection, not only the goals page.

    It once reached the goals and nothing else, because the wiring sat inside
    one route instead of in ops where every forecast passes through -- the CLI
    included. A plan that looks free in the projection is worse than no plan.
    """
    import re

    sid = "pytest-ops"
    client.post("/api/szenario", json={"id": sid, "loeschen": True})
    try:
        client.post("/api/szenario", json={"neu": True, "name": "Pytest Ops"})
        client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "Brocken", "frequenz": "einmalig",
            "amount_cents": -900000, "start": "2026-12",
            "kategorie": "konsum/sonstiges"})

        def figures():
            """Alle Zahlen der Seite, nicht die ersten fünfundzwanzig.

            Der Ausschnitt traf ursprünglich den geänderten Monat und wanderte
            weg, als die Seite einen Horizontschalter bekam -- der Test wurde
            rot, obwohl der Toggle nachweislich 9.000 bewegt. Ein Test, der an
            einer Zeilennummer der Ausgabe hängt, prüft das Layout und nicht
            das Verhalten.
            """
            return re.findall(r"-?[\d.]+,\d\d", client.get("/konten").text)

        client.post("/api/szenario", json={"id": sid, "aktiv": False})
        off = figures()
        client.post("/api/szenario", json={"id": sid, "aktiv": True})
        assert figures() != off
        client.post("/api/szenario", json={"id": sid, "aktiv": False})
        assert figures() == off
    finally:
        client.post("/api/szenario", json={"id": sid, "loeschen": True})


def test_the_goals_page_carries_no_settings_any_more():
    """Every field there was either a second way to say what a goal says, or
    an editor that wrote where nothing reads it."""
    html = client.get("/ziele").text
    for gone in ("buffer_target_cents", "sister_obligation_cents",
                 "salary_floor_cents", "api/dispo"):
        assert gone not in html, gone


def test_the_salary_floor_lives_only_with_the_assumptions():
    """Sie stand auf /annahmen und /planung -- eine Zahl, zwei Felder. Sie ist
    eine Annahme, also steht sie bei den Annahmen."""
    assert "Gehalts-Untergrenze" in client.get("/annahmen").text
    assert "Gehalts-Untergrenze" not in client.get("/planung").text


def test_the_retired_forecast_page_is_gone():
    assert client.get("/limits").status_code == 404


def test_a_scenario_line_can_name_the_account_it_is_debited_from():
    """Konten warns per account and per day.

    A rate charged to Trade Republic that the model books against DKB shows no
    breach where a real one would happen, so the account is worth stating even
    though it is optional.
    """
    import yaml

    konto = _rolle("consumption")
    sid = "pytest-konto"
    client.post("/api/szenario", json={"id": sid, "loeschen": True})
    try:
        client.post("/api/szenario", json={"neu": True, "name": "Pytest Konto"})
        assert client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "Rate", "frequenz": "monatlich",
            "amount_cents": -5000, "start": "2026-11",
            "kategorie": "abo/sonstiges",
            "konto": konto}).status_code == 200
        spec = yaml.safe_load(open("config/szenarien.yaml", encoding="utf-8"))
        line = next(x for x in spec["szenarien"] if x["id"] == sid)["zeilen"][0]
        assert line["konto"] == konto
        assert konto in client.get("/planung").text
    finally:
        client.post("/api/szenario", json={"id": sid, "loeschen": True})


def test_a_prolongation_without_repayment_is_refused():
    """Zero repayment never repays the loan. The schedule would run to its
    horizon and report a payoff date sixty years out, which looks like an
    answer rather than an impossible input."""
    res = client.post("/api/kredit-anschluss",
                      json={"id": "002", "zins_pct": 4.5, "tilgung_pct": 0})
    assert res.status_code == 400


def test_a_prolongation_changes_the_payoff_date():
    from pathlib import Path

    custom = Path("config/loans_custom.yaml")
    had = custom.read_text(encoding="utf-8") if custom.exists() else None
    try:
        before = client.get("/kredite").text
        assert client.post("/api/kredit-anschluss", json={
            "id": "002", "zins_pct": 4.5, "tilgung_pct": 2.0,
            "ab": "2033-01-01"}).status_code == 200
        assert client.get("/kredite").text != before
    finally:
        client.post("/api/kredit-anschluss", json={"id": "002", "entfernen": True})
        if had is None:
            custom.unlink(missing_ok=True)
        else:
            custom.write_text(had, encoding="utf-8")


def test_the_loan_header_states_the_terms_in_force():
    """A decision is made against the balance NOW, not the opening balance.

    The header also separates "anfängliche Tilgung" from "Tilgung heute",
    because the repayment share of an annuity grows every month: a loan quoted
    at 2,00 % stands near 2,78 % nine years in. Showing only one of them under
    the word "anfänglich" would be wrong either way.
    """
    html = client.get("/kredite").text
    for label in ("Zins p.a.", "anfängliche Tilgung", "Tilgung heute",
                  "Restschuld heute", "Zins gesamt", "Ende Zinsbindung"):
        assert label in html, label
    # Prozente mit drei Stellen, wie eine Bank sie nennt.
    assert re.search(r"\d[,.]\d{3} %", html)


def test_the_prolongation_form_is_prefilled_from_the_current_terms():
    """An empty form invites a guess. The natural starting point is the
    payment already being made, expressed as today's repayment share."""
    html = client.get("/kredite").text
    assert 'id="a-zins-001"' in html and 'id="a-tilg-001"' in html
    assert "Anschlussfinanzierung" in html


def test_the_loan_header_separates_planned_from_booked_interest():
    """They answer different questions and disagreeing is the useful signal.

    The schedule figure covers the whole year including months that have not
    happened; the ledger figure is what was actually split out of the rate.
    Showing only one of them hides the case that found nine months of one
    loan booked entirely as repayment.
    """
    html = client.get("/kredite").text
    assert "laut Plan" in html and "gebucht" in html


def test_a_prolongation_that_can_never_apply_says_so():
    """A loan can repay before a late follow-up would start.

    Binding the view to the last segment WITH payments showed the existing
    forward offer as though it were the entered terms -- a number that looks
    accounted for and is not.
    """
    from pathlib import Path

    custom = Path("config/loans_custom.yaml")
    had = custom.read_text(encoding="utf-8") if custom.exists() else None
    try:
        client.post("/api/kredit-anschluss", json={
            "id": "001", "zins_pct": 3.8, "tilgung_pct": 2.0, "ab": "2060-01-01"})
        assert "Greift nie" in client.get("/kredite").text
    finally:
        client.post("/api/kredit-anschluss", json={"id": "001", "entfernen": True})
        if had is None:
            custom.unlink(missing_ok=True)
        else:
            custom.write_text(had, encoding="utf-8")


def test_a_prolongation_mirrors_the_same_metrics():
    from pathlib import Path

    custom = Path("config/loans_custom.yaml")
    had = custom.read_text(encoding="utf-8") if custom.exists() else None
    try:
        client.post("/api/kredit-anschluss", json={
            "id": "002", "zins_pct": 5.0, "tilgung_pct": 2.5, "ab": "2033-01-01"})
        html = client.get("/kredite").text
        for row in ("neue Rate", "Restschuld bei Beginn",
                    "Zins über die Restlaufzeit", "Laufzeit"):
            assert row in html, row
    finally:
        client.post("/api/kredit-anschluss", json={"id": "002", "entfernen": True})
        if had is None:
            custom.unlink(missing_ok=True)
        else:
            custom.write_text(had, encoding="utf-8")


def test_a_documented_forward_offer_is_shown_instead_of_a_guess():
    """A loan can have the lender's own forward quote on file.

    Putting a preview computed from today's terms beside it would be an
    invented number next to a known one. The documented segment wins, and the
    page says which of the three it is showing.
    """
    angebot = [s for k in _kredite() for s in k.get("segments") or []
               if s.get("basis") == "offer"]
    if not angebot:
        pytest.skip("kein Kredit mit dokumentiertem Angebot")
    html = client.get("/kredite").text
    assert "Aus dem vorliegenden Angebot" in html
    zins = f"{float(angebot[0]['annual_rate_pct']):.3f}"
    assert f"{zins.replace('.', ',')} %" in html or f"{zins} %" in html


def test_a_preview_attaches_at_the_end_of_the_fixed_rate_period():
    """A segment can run to 2056 with a constant rate while the
    Zinsbindung ends 30.06.2031 and the rate turns variable then.

    Attaching the preview at the segment end would skip the one question that
    is actually open on this loan -- and show nothing at all, because the loan
    is already repaid by then at the old terms.
    """
    _, anschluss = _bindung_im_abschnitt()
    assert anschluss in client.get("/kredite").text


def _kredit_abschnitt(html: str, loan_id: str) -> str:
    """Nur der Abschnitt EINES Kredits.

    Beide Tests unten haben ueber die ganze Seite gesucht und gingen kaputt,
    als ein vierter Kredit dazukam -- ohne dass an dem, was sie pruefen, etwas
    falsch gewesen waere. Ein Test, der an der Zahl der Objekte haengt, prueft
    die Daten und nicht das Verhalten.
    """
    anfang = html.index(f'id="kredit-{loan_id}"')
    naechster = html.find('<details class="klapp" id="kredit-', anfang + 10)
    return html[anfang:naechster if naechster != -1 else len(html)]


def test_the_reset_button_says_what_it_will_restore():
    """Two cases, one button, and the label has to distinguish them.

    With a saved override, resetting is a server-side delete and what comes
    back is either the documented offer or the preview. With nothing saved,
    only the fields have been typed into and resetting is local. Telling the
    user the wrong one is how a documented Sparda offer gets called "the
    preview".
    """
    from pathlib import Path

    custom = Path("config/loans_custom.yaml")
    had = custom.read_text(encoding="utf-8") if custom.exists() else None
    try:
        # Whitespace-normalised: the hint spans several template lines, so a
        # raw substring check passes or fails on indentation rather than on
        # the sentence actually shown.
        def shown():
            return " ".join(client.get("/kredite").text.split())

        html = shown()
        # An der ZAHL der Kredite haengt hier nichts: der Test soll sagen, was
        # der Knopf beschriftet ist, nicht wie viele Kredite es gerade gibt.
        # Ein vierter Eintrag -- die Eigenheim-Vorlage -- liess ihn vorher
        # fehlschlagen, ohne dass am Knopf etwas falsch gewesen waere.
        assert "Zurücksetzen" in html
        # Woraufhin zurueckgesetzt wird, steht als Attribut am Knopf und nicht
        # als Satz daneben: ein Satz altert, das Attribut ist der Zustand.
        assert 'data-zuruecksetzen-auf="eingetragen"' in html

        client.post("/api/kredit-anschluss",
                    json={"id": "001", "zins_pct": 3.0, "tilgung_pct": 2.0})
        client.post("/api/kredit-anschluss",
                    json={"id": "002", "zins_pct": 5.5, "tilgung_pct": 3.0,
                          "ab": "2033-01-01"})
        html = shown()
        # Ein Kredit hat ein dokumentiertes Angebot, der andere nicht:
        # zurueckgesetzt wird einmal darauf, einmal auf die Vorschau.
        assert 'data-zuruecksetzen-auf="angebot"' in html
        assert 'data-zuruecksetzen-auf="vorschau"' in html
    finally:
        for lid in ("001", "002"):
            client.post("/api/kredit-anschluss", json={"id": lid, "entfernen": True})
        if had is None:
            custom.unlink(missing_ok=True)
        else:
            custom.write_text(had, encoding="utf-8")


def test_the_form_carries_its_starting_values_for_a_local_reset():
    """Resetting typed-in fields must not need a round trip."""
    html = client.get("/kredite").text
    assert 'data-start=' in html


def test_a_prolongation_works_on_a_loan_whose_segment_outruns_its_fixed_rate():
    """A segment can run to 2056 at a constant rate; the Zinsbindung
    ends 30.06.2031.

    Clipping at the Zinsbindung was applied to the preview but not to an
    ENTERED condition, so every attempt to type one in for this loan landed
    after 2056 -- past the payoff -- and the table vanished with a "Greift
    nie" instead of an answer.
    """
    from pathlib import Path

    custom = Path("config/loans_custom.yaml")
    had = custom.read_text(encoding="utf-8") if custom.exists() else None
    kredit, anschluss = _bindung_im_abschnitt()
    try:
        assert client.post("/api/kredit-anschluss", json={
            "id": kredit, "zins_pct": 4.67, "tilgung_pct": 2.0}).status_code == 200
        html = " ".join(_kredit_abschnitt(client.get("/kredite").text, kredit).split())
        assert "Greift nie" not in html
        assert anschluss in html
    finally:
        client.post("/api/kredit-anschluss", json={"id": kredit, "entfernen": True})
        if had is None:
            custom.unlink(missing_ok=True)
        else:
            custom.write_text(had, encoding="utf-8")


def test_both_columns_agree_on_the_payoff_date():
    """Two answers to one question, side by side, is worse than one wrong one.

    The left column was built without the clip, so it reported 2056 while the
    right reported 2057 for the same entered terms.
    """
    import re
    from pathlib import Path

    custom = Path("config/loans_custom.yaml")
    had = custom.read_text(encoding="utf-8") if custom.exists() else None
    kredit, _ = _bindung_im_abschnitt()
    try:
        client.post("/api/kredit-anschluss", json={
            "id": kredit, "zins_pct": 4.67, "tilgung_pct": 2.0})
        html = _kredit_abschnitt(client.get("/kredite").text, kredit)
        rows = re.findall(
            r'<td class="muted">([^<]+)</td>\s*<td class="num[^"]*">\s*(?:<strong>)?([^<]+)',
            html)
        payoffs = [v.strip() for k, v in rows if k.strip() == "abbezahlt"]
        # Beide Spalten DIESES Kredits, nicht die letzten zwei der Seite.
        assert len(payoffs) == 2 and payoffs[0] == payoffs[1], payoffs
    finally:
        client.post("/api/kredit-anschluss", json={"id": kredit, "entfernen": True})
        if had is None:
            custom.unlink(missing_ok=True)
        else:
            custom.write_text(had, encoding="utf-8")


# ------------------------------------------------- collapsible tables

def test_the_long_tables_collapse_to_their_title():
    """Konten and Kredite both put a long table under a short answer.

    The answer is the point of the page; the table is the evidence. Using
    <details> rather than JavaScript keeps the state in the markup, lets the
    browser remember it while paging, and survives a session with no script
    running at all -- which this tool is built to do.
    """
    import re

    for path in ("/konten", "/kredite", "/kredite?jahr=2030",
                 "/rueckblick?ansicht=fixkosten", "/rueckblick?ansicht=fixkosten&year=2025"):
        html = client.get(path).text
        opened = len(re.findall(r"<details[ >]", html))
        assert opened and opened == html.count("</details>"), path
        assert 'class="klapp"' in html


def test_an_account_that_breaches_its_floor_opens_by_default():
    """A month below the floor is the reason to look at the page.

    Hiding it behind a closed toggle would make the one table that matters the
    one you have to go looking for.
    """
    import re

    html = client.get("/konten").text
    for block in re.findall(r"<details[^>]*>.*?</details>", html, re.S):
        if "unter Grenze" in block:
            assert re.match(r"<details[^>]*\bopen\b", block), \
                "a breaching account must not start collapsed"


def test_the_heaviest_fixed_cost_block_stays_open():
    """The groups are sorted heaviest first, and that one is the answer.

    Collapsing every block would mean opening the page and seeing nothing but
    headings -- the loans outweigh every subscription, and that is the point
    being made.
    """
    import re

    html = client.get("/rueckblick?ansicht=fixkosten").text
    first = re.search(r"<details[^>]*>", html)
    assert first and "open" in first.group(0)


def test_a_collapsed_fixed_cost_block_still_says_what_is_inside():
    """A title you have to open to evaluate is not a summary."""
    import re

    html = client.get("/rueckblick?ansicht=fixkosten").text
    summaries = re.findall(r"<summary>(.*?)</summary>", html, re.S)
    assert summaries
    assert any("Position" in s and "/ Monat" in s for s in summaries)


# ------------------------------------------------- Planung: merged page


def _drop_bracket(sid):
    """Remove a bracket, obligation or not, straight out of the file."""
    from pathlib import Path

    import yaml

    spec = Path("config/szenarien.yaml")
    if not spec.exists():
        return
    loaded = yaml.safe_load(spec.read_text(encoding="utf-8")) or {}
    loaded["szenarien"] = [x for x in (loaded.get("szenarien") or [])
                           if x.get("id") != sid]
    spec.write_text(yaml.safe_dump(loaded, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def _bracket(name, pflicht=False):
    """Create a bracket and hand back its id, removing any leftover first.

    The cleanup goes straight to the file rather than through the API,
    because an obligation deliberately cannot be deleted through it -- and a
    leftover from a failed run would otherwise make every later run fail with
    "gibt es schon".
    """
    sid = name.lower().replace(" ", "-")
    _drop_bracket(sid)
    body = {"neu": True, "name": name}
    if pflicht:
        body["pflicht"] = True
    res = client.post("/api/szenario", json=body)
    assert res.status_code == 200, res.json()
    return res.json()["id"]


def test_a_commitment_without_a_category_is_refused():
    sid = _bracket("Pytest Pflicht", pflicht=True)
    try:
        res = client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "ohne-kat", "frequenz": "einmalig",
            "amount_cents": -100, "start": "2026-12"})
        assert res.status_code == 400
        assert "Kategorie" in res.json()["error"]
    finally:
        _drop_bracket(sid)


def test_a_commitment_uses_the_same_form_as_a_plan():
    """One form, one line model. The bracket is the only difference.

    Two forms for the same fields meant two places to look and two to forget --
    and the commitment form could not express a recurring item at all, which
    is a perfectly ordinary obligation.
    """
    sid = _bracket("Pytest Steuererstattung", pflicht=True)
    try:
        assert client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "Erstattung 2024",
            "frequenz": "einmalig", "amount_cents": 500000,
            "start": "2027-03", "kategorie": "steuern/einkommensteuer"
        }).status_code == 200
        assert client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "Erstattung 2025",
            "frequenz": "einmalig", "amount_cents": 500000,
            "start": "2027-09", "kategorie": "steuern/einkommensteuer"
        }).status_code == 200
        html = client.get("/planung").text
        assert "Erstattung 2024" in html and "Erstattung 2025" in html
    finally:
        _drop_bracket(sid)


def test_an_obligation_cannot_be_switched_off():
    """Der Grund, warum Verpflichtungen ein eigener Block auf der Seite sind.

    Das Löschen einer LEEREN Klammer ist dagegen erlaubt -- die Sperre soll
    davor schützen, eine Schuld wegzuklicken, nicht davor, einen Vertipper zu
    korrigieren. Mit Positionen darin greift sie wieder, und dann müssen die
    einzeln raus: das ist die bewusste Geste.
    """
    sid = _bracket("Pytest Halt", pflicht=True)
    try:
        res = client.post("/api/szenario", json={"id": sid, "aktiv": False})
        assert res.status_code == 400
        assert "nicht abschalten" in res.json()["error"]

        client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "x", "art": "betrag",
            "frequenz": "einmalig", "amount_cents": -1000, "start": "2027-01",
            "kategorie": "konsum/sonstiges"})
        mit = client.post("/api/szenario", json={"id": sid, "loeschen": True})
        assert mit.status_code == 400
    finally:
        _drop_bracket(sid)


def test_a_cessation_is_measured_and_needs_no_amount():
    """A new car costs the rate MINUS what the old one costs.

    Typing that saving in makes it a claim from the day it is entered, and one
    that flatters the plan. The form refuses an amount here and takes it from
    the ledger instead.
    """
    sid = _bracket("Pytest Wegfall")
    try:
        assert client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "Kraftstoff entfällt", "art": "wegfall",
            "frequenz": "monatlich", "start": "2026-11",
            "kategorie": "mobilitaet/kraftstoff"}).status_code == 200
        client.post("/api/szenario", json={"id": sid, "aktiv": True})
        html = " ".join(client.get("/planung").text.split())
        assert "gemessen" in html
        # Fuel is a real cost, so the cessation must be a positive figure.
        assert "Kraftstoff entfällt" in html
    finally:
        _drop_bracket(sid)


# ------------------------------------------- Fixkosten und alle Kategorien

def test_the_full_category_view_is_the_same_page_without_the_filter():
    """Ein zweites Template wäre eine zweite Stelle für Gruppierung,
    Monatsteiler und Jahresfilter -- und die Fälle, in denen sie auseinander
    laufen, sind genau die, in denen sich die beiden Seiten widersprechen."""
    gefiltert = client.get("/rueckblick?ansicht=fixkosten")
    alle = client.get("/rueckblick?ansicht=alle")
    assert gefiltert.status_code == alle.status_code == 200
    assert "Fixkosten" in gefiltert.text
    assert "Alle Kategorien" in alle.text
    # Die Vollansicht muss mehr Gruppen zeigen als die gefilterte.
    import re
    def zaehle(text):
        return len(re.findall(r"<summary>", text))

    assert zaehle(alle.text) > zaehle(gefiltert.text)


def test_transfers_stay_out_of_the_full_view():
    """Sie sind weder Einnahme noch Ausgabe, nur Geld, das die Seite wechselt.

    Mit 83.982 im Jahr würden sie jede Sortierung anführen und die Kopfzahl
    bedeutungslos machen.
    """
    import re

    summaries = re.findall(r"<summary>(.*?)</summary>",
                           client.get("/rueckblick?ansicht=alle").text, re.S)
    assert summaries
    assert not any("Transfer" in s for s in summaries)


def test_the_full_view_calls_its_total_a_balance():
    """In der gefilterten Ansicht ist jede Position eine Ausgabe, die Summe
    also eine Kostenzahl. In der Vollansicht stehen Einnahmen daneben -- ein
    Überschuss von 878 läse sich sonst wie monatliche Kosten von 878."""
    assert "Saldo" in client.get("/rueckblick?ansicht=alle").text
    assert "Saldo" not in client.get("/rueckblick?ansicht=fixkosten").text


def _monitor_konten():
    """Die Kontenkennungen, die die Seite selbst als Filter anbietet."""
    import re

    return re.findall(r'href="/rueckblick\?[^"]*konto=([^"&]+)"',
                      client.get("/rueckblick?ansicht=fixkosten").text)


def test_the_account_filter_narrows_the_page():
    """Ohne Konto ist die Seite die Summe aller Konten -- also darf ein
    einzelnes nie mehr Gruppen zeigen als alle zusammen."""
    import re

    konten = [k for k in _monitor_konten() if k != "all"]
    if not konten:
        pytest.skip("kein Konto im Ledger")
    ganz = len(re.findall(r"<summary>", client.get("/rueckblick?ansicht=fixkosten").text))
    for k in konten:
        html = client.get(f"/rueckblick?ansicht=fixkosten&konto={k}").text
        assert len(re.findall(r"<summary>", html)) <= ganz
        # Das gewaehlte Konto steht hervorgehoben da, sonst sieht die Seite
        # nach einem Filter aus, den niemand gesetzt hat.
        assert f'konto={k}" class="on"' in html


def test_the_average_uses_the_accounts_own_months():
    """Ein Konto, das spaeter anfing, hat weniger Monate als das Ledger.

    Durch die Monate ALLER Konten geteilt saehe seine Belastung kleiner aus
    als sie ist -- und zwar umso mehr, je juenger das Konto ist. Zaehler und
    Nenner muessen denselben Filter sehen.
    """
    import re
    from datetime import date

    import finctl.web.basis as basis

    # Dieselbe Grenze wie die Seite: der laufende Monat zählt auf keiner
    # Seite der Division mit.
    grenze = date.today().replace(day=1).isoformat()
    c = basis.conn()
    try:
        zeilen = c.execute(
            "SELECT account_id, COUNT(DISTINCT substr(booking_date,1,7)) "
            "FROM transactions WHERE booking_date < ? GROUP BY account_id",
            [grenze]).fetchall()
        ganz = c.execute("SELECT COUNT(DISTINCT substr(booking_date,1,7)) "
                         "FROM transactions WHERE booking_date < ?",
                         [grenze]).fetchone()[0]
    finally:
        c.close()
    juenger = [r[0] for r in zeilen if r[1] < ganz]
    if not juenger:
        pytest.skip("jedes Konto bucht ueber das ganze Ledger")

    def monate(pfad):
        # Ohne Treffer steht kein Spaltenkopf da, also auch kein Teiler.
        treffer = re.search(r"über (\d+)\s+(?:angefangene|abgeschlossene)",
                            client.get(pfad).text)
        return int(treffer.group(1)) if treffer else None

    alle, geprueft = monate("/rueckblick?ansicht=alle"), 0
    for k in juenger:
        eigene = monate(f"/rueckblick?ansicht=alle&konto={k}")
        if eigene is None:
            continue
        assert eigene < alle, k
        geprueft += 1
    if not geprueft:
        pytest.skip("kein juengeres Konto mit Buchung in der Auswahl")


def test_a_filter_without_a_hit_says_so():
    """Eine leere Seite sieht aus wie ein Fehler. Ein Konto ohne Position in
    der Auswahl hat keine -- das ist eine Antwort und muss dastehen."""
    import re

    # Ohne Kategorieblock gibt es keinen Spaltenkopf mit dem Monatsteiler.
    leer = [k for k in _monitor_konten()
            if not re.search(r"über \d+\s+(?:angefangene|abgeschlossene)",
                             client.get(f"/rueckblick?ansicht=fixkosten&konto={k}").text)]
    if not leer:
        pytest.skip("jedes Konto hat eine Fixkostenposition")
    assert "Kein Treffer" in client.get(f"/rueckblick?ansicht=fixkosten&konto={leer[0]}").text


def test_the_year_filter_survives_the_scope_switch():
    """Sonst springt die Ansicht beim Umschalten auf ein anderes Jahr zurück
    und der Vergleich, für den man umgeschaltet hat, ist weg."""
    import re

    html = client.get("/rueckblick?ansicht=alle&year=2025").text
    # Die Reiter tragen das Jahr mit, und die Jahreslinks den Reiter.
    assert 'href="/rueckblick?year=2025&amp;ansicht=fixkosten"' in html
    assert re.search(r'href="/rueckblick\?ansicht=alle&amp;year=\d{4}"', html)


def test_an_account_rule_set_on_the_page_moves_the_forecast():
    """Ein Feld, das die Rechnung nicht bewegt, ist Dekoration.

    Die Regeln zwischen den Konten -- Deckel, Abräumen, Auffüllen -- standen
    nur in einer Datei, und auf der Seite stand ein Absatz, der sie erklärte.
    Jetzt stehen sie als Felder dort, wo sie wirken; dieser Test hält fest,
    dass sie auch wirken.
    """
    import sqlite3

    import yaml

    from finctl import kontenregeln as _kr
    from finctl import ops

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    pfad = Path("config/forecast_custom.yaml")
    vorher_datei = pfad.read_bytes() if pfad.exists() else None
    basis_datei = Path("config/forecast.yaml").read_bytes()

    def sicht(konto):
        db = sqlite3.connect("data/finance.db")
        db.row_factory = sqlite3.Row
        try:
            return next(v for v in ops.household_accounts(db, months=12)
                        if v["id"] == konto)
        finally:
            db.close()

    betrieb = next(k for k, v in (_kr.wirksam().get("account_roles") or {}).items()
                   if (v or {}).get("role") == "operating")
    vorher_eigen = set(_kr.overlay()["account_roles"].get(betrieb) or {})
    vorher = sicht(betrieb)
    deckel = vorher["ceiling_cents"]
    try:
        # Deckel senken: mehr Geld gehört aufs Tagesgeld, also wird mehr abgeräumt.
        res = client.post(f"/api/konto/{betrieb}",
                          json={"ceiling_cents": int(deckel) - 500000})
        assert res.status_code == 200, res.text
        nachher = sicht(betrieb)
        assert nachher["ceiling_cents"] == deckel - 500000
        assert nachher["sweep_total_cents"] > vorher["sweep_total_cents"]

        # Und nur DIESE Abweichung kommt dazu -- was vorher schon eigen war,
        # bleibt; die Basisdatei bleibt unberührt.
        eigen = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
        assert set(eigen["account_roles"][betrieb]) == vorher_eigen | {"ceiling_cents"}
        assert Path("config/forecast.yaml").read_bytes() == basis_datei

        # Zurück auf den Basiswert heisst: kein Eintrag mehr, und ohne jede
        # Abweichung verschwindet die Datei -- eine leere Hülle behauptet,
        # hier sei etwas gesetzt.
        client.post(f"/api/konto/{betrieb}", json={"ceiling_cents": int(deckel)})
        eigen = (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}) if pfad.exists() else {}
        assert set((eigen.get("account_roles") or {}).get(betrieb) or {}) == vorher_eigen
        assert sicht(betrieb)["sweep_total_cents"] == vorher["sweep_total_cents"]

        # Speichern ohne Änderung schreibt nichts: der Zweck aus der
        # Basisdatei endet auf einem Zeilenumbruch, das Formular nicht, und
        # ungetrimmt wäre jeder Klick eine Abweichung.
        regel = (_kr.wirksam().get("account_roles") or {}).get(betrieb) or {}
        client.post(f"/api/konto/{betrieb}",
                    json={"note": regel.get("note", ""), "role": regel.get("role"),
                          "auffuellen": regel.get("auffuellen", True),
                          "floor_from_loan": bool(regel.get("floor_from_loan")),
                          "ceiling_cents": regel.get("ceiling_cents"),
                          "sweep_to": regel.get("sweep_to"),
                          "budget_cents": _kr.basiswerte(betrieb).get("budget_cents")})
        eigen = (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}) if pfad.exists() else {}
        assert set((eigen.get("account_roles") or {}).get(betrieb) or {}) == vorher_eigen
    finally:
        if vorher_datei is None:
            pfad.unlink(missing_ok=True)
        else:
            pfad.write_bytes(vorher_datei)


def test_the_long_horizon_says_where_measurement_ends():
    """Sechzig Monate ohne diesen Hinweis wären die gefährlichere Ansicht:
    Monat 50 sieht genauso plausibel aus wie Monat 5.

    Geprüft wird der Hinweis über sein Element und sein DATUM, nicht über
    seinen Wortlaut: das Datum ist die Aussage, der Satz nur ihre Verpackung.
    """
    import sqlite3

    from finctl import ops

    kurz = client.get("/konten").text
    lang = client.get("/konten?months=60").text
    assert 'id="gemessen-bis"' not in kurz
    assert 'id="gemessen-bis"' in lang
    c = sqlite3.connect("data/finance.db")
    c.row_factory = sqlite3.Row
    try:
        stichtag = ops._stichtag(c).isoformat()
    finally:
        c.close()
    hinweis = lang[lang.index('id="gemessen-bis"'):]
    assert stichtag in hinweis[:400], "der Hinweis muss sagen, bis wann gemessen ist"


def test_both_horizons_render():
    assert client.get("/konten?months=60").status_code == 200
    assert client.get("/konten").status_code == 200


# ------------------------------- Klammern: wählen oder tippen, und bearbeiten

def _weg(sid):
    for i in range(9, -1, -1):
        client.post("/api/szenario-zeile", json={"szenario": sid, "index": i,
                                                 "loeschen": True})
    client.post("/api/szenario", json={"id": sid, "loeschen": True})


def test_a_bracket_can_be_named_instead_of_picked():
    """"Bestehende ergänzen" und "neue anlegen" sind derselbe Handgriff.

    Zwei getrennte Formulare hiessen, sich vor dem Tippen entscheiden zu
    müssen -- und die Auswahlliste zeigte vorher zwei Einträge für dieselbe
    Sache.
    """
    _weg("pytest-klammer")
    try:
        erste = client.post("/api/szenario-zeile", json={
            "klammer": "Pytest Klammer", "label": "eins", "art": "betrag",
            "frequenz": "einmalig", "amount_cents": -1000, "start": "2027-01",
            "kategorie": "konsum/sonstiges"})
        assert erste.status_code == 200 and erste.json()["count"] == 1
        # Derselbe Name trifft dieselbe Klammer, nicht eine zweite.
        zweite = client.post("/api/szenario-zeile", json={
            "klammer": "pytest klammer", "label": "zwei", "art": "betrag",
            "frequenz": "einmalig", "amount_cents": -2000, "start": "2027-02",
            "kategorie": "konsum/sonstiges"})
        assert zweite.json()["count"] == 2
    finally:
        _weg("pytest-klammer")


def test_a_line_is_edited_in_place():
    """Eine Annahme zu korrigieren darf nicht heissen, die Zeile zu löschen
    und neu anzulegen -- dabei verliert sie ihre Stellung in der Klammer, und
    bei einer Verpflichtung war Löschen ohnehin die falsche Geste."""
    from pathlib import Path

    import yaml

    _weg("pytest-edit")
    try:
        client.post("/api/szenario-zeile", json={
            "klammer": "Pytest Edit", "label": "alt", "art": "betrag",
            "frequenz": "einmalig", "amount_cents": -1000, "start": "2027-01",
            "kategorie": "konsum/sonstiges"})
        res = client.post("/api/szenario-zeile", json={
            "szenario": "pytest-edit", "index": 0, "ersetzen": True,
            "label": "neu", "art": "betrag", "frequenz": "einmalig",
            "amount_cents": -5000, "start": "2027-06",
            "kategorie": "konsum/sonstiges"})
        assert res.status_code == 200 and res.json()["count"] == 1
        spec = yaml.safe_load(Path("config/szenarien.yaml").read_text(encoding="utf-8"))
        zeile = next(s for s in spec["szenarien"]
                     if s["id"] == "pytest-edit")["zeilen"][0]
        assert zeile["label"] == "neu"
        assert zeile["amount_cents"] == -5000
        assert str(zeile["start"]).startswith("2027-06")
    finally:
        _weg("pytest-edit")


def test_an_empty_obligation_bracket_can_be_removed():
    """Die Sperre soll davor schützen, eine Schuld wegzuklicken -- nicht
    davor, einen Vertipper zu korrigieren."""
    _weg("pytest-leer")
    try:
        client.post("/api/szenario-zeile", json={
            "klammer": "Pytest Leer", "klammerart": "pflicht", "label": "x",
            "art": "betrag", "frequenz": "einmalig", "amount_cents": -1000,
            "start": "2027-01", "kategorie": "konsum/sonstiges"})
        # Mit Positionen darin: gesperrt.
        assert client.post("/api/szenario",
                           json={"id": "pytest-leer",
                                 "loeschen": True}).status_code == 400
        client.post("/api/szenario-zeile", json={"szenario": "pytest-leer",
                                                 "index": 0, "loeschen": True})
        # Leer: erlaubt.
        assert client.post("/api/szenario",
                           json={"id": "pytest-leer",
                                 "loeschen": True}).status_code == 200
    finally:
        _weg("pytest-leer")


def _pflichtklammer() -> str:
    """Eine Klammer mit `pflicht: true` -- aus `config/szenarien.yaml`.

    Vorher stand hier die Kennung einer bestimmten. Geprueft wird aber nicht
    sie, sondern dass eine Verpflichtung sich nicht wegklicken laesst.
    """
    import yaml as _y

    from finctl.web.basis import CONFIG_DIR

    pfad = CONFIG_DIR / "szenarien.yaml"
    if not pfad.exists():
        return ""
    spec = _y.safe_load(pfad.read_text(encoding="utf-8")) or {}
    for sz in spec.get("szenarien") or []:
        if sz.get("pflicht"):
            return str(sz.get("id") or "")
    return ""


def test_an_obligation_still_cannot_be_switched_off():
    pflicht = _pflichtklammer()
    if not pflicht:
        pytest.skip("keine Klammer mit pflicht: true in szenarien.yaml")
    res = client.post("/api/szenario", json={"id": pflicht, "aktiv": False})
    assert res.status_code == 400


def test_the_planning_page_gets_the_filtering_combobox():
    """Eine Auswahlliste mit 97 Kategorien ist unbenutzbar.

    Die Lösung dafür existierte längst und war auf zehn Seiten eingebunden --
    nur auf der Planungsseite nicht. Sie rüstet jede Auswahlliste mit mehr als
    sechs Einträgen automatisch auf, also auch die Zeilen-Editoren, ohne dass
    jemand daran denken muss.
    """
    import re

    html = client.get("/planung").text
    assert "upgradeSelects" in html
    # Die Kategorie der neuen Position steht in jeder Klammer.
    assert re.search(r'id="e-kat-[a-z0-9-]+-neu"', html)


def test_a_new_position_is_added_inside_its_bracket():
    """Eine Position gehoert in ihre Klammer, nicht in ein Formular oben.

    Vorher stand "Neue Position" ueber allem, und der Klammername musste
    eingetippt werden. Jetzt hat jede Klammer eine letzte Zeile
    "+ Position hinzufuegen", die denselben Bereich aufklappt wie "Oeffnen".
    """
    import re

    import yaml

    html = client.get("/planung").text
    klammern = re.findall(r'<details class="klapp" id="klammer-([^"]+)"', html)
    assert klammern
    for kid in klammern:
        assert f'id="panel-{kid}-neu"' in html, kid
        assert f"neueZeile('{kid}')" in html, kid
    assert 'id="n-ziel"' not in html

    # Der Server nimmt die Klammer weiter auch beim Namen -- und legt eine an,
    # die es noch nicht gibt.
    pfad = Path("config/szenarien.yaml")
    vorher = pfad.read_text(encoding="utf-8")
    try:
        res = client.post("/api/szenario-zeile", json={
            "szenario": "Pytest Frischer Name", "label": "Rate",
            "frequenz": "monatlich", "amount_cents": -1000,
            "start": "2027-01", "kategorie": "konsum/sonstiges"})
        assert res.status_code == 200
        spec = yaml.safe_load(pfad.read_text(encoding="utf-8"))
        assert any(x["name"] == "Pytest Frischer Name"
                   for x in spec["szenarien"])
    finally:
        pfad.write_text(vorher, encoding="utf-8")


def test_planung_shows_commitments_and_plans_separately():
    """Eine Seite, ein Zeilenmodell -- aber der Schalter gehört nur an eines.

    Eine Verpflichtung ist eingegangen. Sie abschaltbar zu machen hiesse, das
    Wegrechnen einer Schuld zu einem Klick zu machen.
    """
    html = " ".join(client.get("/planung").text.split())
    assert "Verpflichtungen" in html and "Pläne" in html
    assert "gelten in jeder Projektion" in html


def test_the_overview_is_gone_and_its_tiles_live_in_the_monthly_close():
    """Die Overview bot neben Report, Alle Kategorien und Monatsabschluss kaum
    noch etwas. Ihre Kacheln stehen im Monatsabschluss, `/` leitet dorthin --
    der Login und alte Lesezeichen landen sonst im Leeren."""
    import re

    antwort = client.get("/", follow_redirects=False)
    assert antwort.status_code in (302, 303, 307)
    assert antwort.headers["location"] == "/monatsabschluss"

    html = client.get("/monatsabschluss").text
    kopf = html[html.index("<header>"):html.index("</header>")]
    assert 'href="/"' not in kopf
    erste = kopf[kopf.index('<span class="navgruppe">'):]
    erste = erste[:erste.index("</span>\n  </span>")]
    for ziel in ("/monatsabschluss", "/rueckblick", "/transactions"):
        assert f'href="{ziel}"' in erste, ziel

    koerper = html[html.index("</header>"):]
    for kachel in ("Einnahmen", "Ausgaben", "Saldo", "Cashflow ohne ",
                   "Transaktionen offen"):
        assert re.search(rf'<div class="k">{kachel}', koerper), kachel
    # Die Konten stehen als Zeilen mit Saldo und Beleg, nicht noch einmal
    # als Kacheln daneben.
    for konto in _geparste_konten()[:2]:
        assert f'<div class="k">{konto}</div>' not in koerper, konto
        assert f'<tr id="pos-{konto}"' in koerper, konto


def test_konten_shows_the_cashflow_without_the_object():
    """Die Ueberschrift traegt den Namen aus der Konfiguration, nicht aus
    der Vorlage -- ein verkauftes Objekt stuende sonst noch Jahre dort."""
    from finctl.forecast import abgleich as _ag

    html = client.get("/konten").text
    assert 'id="ohne-objekt"' in html
    assert f"Cashflow ohne {_ag.objekt_name()}" in html
    assert "Gemeinschaftskonto" in html


def test_an_account_rule_is_edited_in_the_table_and_not_beside_it():
    """Jeder Wert stand zweimal auf der Seite: als Text und als Feld.

    Die Kontentabelle IST der Bearbeitungsbereich -- ein Panel, das unter die
    geoeffnete Zeile wandert, wie auf /regeln und /planung. Zwei Formulare
    hiessen zwei Wahrheiten ueber dasselbe Konto.
    """
    import re

    html = client.get("/konten").text
    assert html.count('id="panel"') == 1, "genau ein Bearbeitungsbereich"
    assert html.count('class="panel"') == 1
    konten = re.findall(r'<tr id="r-([a-z0-9-]+)"', html)
    assert konten, "keine Kontenzeile"
    for konto in konten:
        assert f'oeffne(\'{konto}\')' in html, konto
        assert f'id="u-{konto}"' in html, f"{konto} ohne Zweck-Unterzeile"
    assert html.count(">Öffnen</button>") == len(konten)
    # Kein zweites Formular je Konto mehr -- die Felder gibt es nur einmal.
    for feld in ("f-floor", "f-ceil", "f-role", "f-sweep", "f-note"):
        assert html.count(f'id="{feld}"') == 1, feld


def test_the_chart_is_anchored_and_adds_up_every_account():
    """Ohne Ueberschrift hing das Diagramm zwischen zwei fremden Abschnitten.

    Und mit nur einem Konto je Ansicht liess sich die Frage, ob das liquide
    Geld ueberhaupt fuer alle Untergrenzen reicht, gar nicht stellen.
    """
    html = client.get("/konten").text
    assert 'id="verlauf"' in html
    assert "konto=zusammen#verlauf" in html, "die Summe aller Konten ist eine Wahl"
    zusammen = client.get("/konten?konto=zusammen").text
    assert "<figcaption>Giro + Tagesgeld zusammen</figcaption>" in zusammen


def _konto_ansicht(kid, typ, tief, grenze, deckel=None, unter=False):
    """Ein erfundenes Konto, wie ops.household_accounts es liefert -- zwei Monate."""
    return {"id": kid, "account_type": typ, "dispo_threshold_cents": grenze,
            "ceiling_cents": deckel, "worst_trough_cents": tief,
            "rows": [{"month": m, "trough_cents": tief, "closing_cents": tief + 50_000,
                      "breaches_dispo": unter} for m in ("2026-01-01", "2026-02-01")]}


def _tips(html):
    import html as _html
    import json
    import re

    return json.loads(_html.unescape(re.search(r'data-tips="([^"]*)"', html).group(1)))


def test_the_sum_counts_only_money_available_today_but_every_floor():
    """Ein Depot ist kein verfuegbares Geld -- seine Untergrenze muss trotzdem gedeckt sein."""
    from finctl.web.routen import konten as _k

    views = [_konto_ansicht("a", "giro", 100_000, 10_000),
             _konto_ansicht("b", "tagesgeld", 200_000, 20_000),
             _konto_ansicht("c", "broker", 900_000, 30_000)]
    html = str(_k._diagramm_zusammen(views))
    monat, zeilen = _tips(html)[0]
    assert monat == "2026-01"
    werte = {name: wert for name, wert, _ in zeilen}
    assert werte == {"Giro + Tagesgeld": "3.000,00 €", "alle Untergrenzen": "600,00 €"}


def test_the_chart_legend_draws_what_the_chart_draws():
    """Die Legende zeigte "nach Gehalt" als vollen Strich, gezeichnet war er
    gestrichelt -- und nannte einen Deckel, den das Konto nicht hatte. Die
    gestrichelte Linie las sich dann als Deckel."""
    from finctl.web.routen import konten as _k

    ohne = str(_k._diagramm_konto(_konto_ansicht("a", "giro", 100_000, 10_000)))
    assert "Deckel" not in ohne and "unter Grenze" not in ohne
    assert "stroke-dasharray" not in ohne
    mit = str(_k._diagramm_konto(
        _konto_ansicht("a", "giro", 5_000, 10_000, deckel=500_000, unter=True)))
    assert "Deckel 5.000,00 €" in mit
    assert "unter Grenze" in mit
    assert mit.count("style=\"stroke:var(--warn)\"") == 1, "der Deckel ist gezeichnet"


def test_the_chart_opens_on_the_account_with_the_lowest_point():
    """Die Seite soll mit dem Problem aufmachen, nicht mit dem alphabetisch ersten Konto."""
    from finctl.web.routen import konten as _k

    views = [_konto_ansicht("a", "giro", 100_000, 0), _konto_ansicht("b", "giro", -5_000, 0)]
    assert _k._verlauf(views, "")[0] == "b"
    assert _k._verlauf(views, "a")[0] == "a"
    assert _k._verlauf(views, "gibt-es-nicht")[0] == "b"
    assert _k._verlauf(views, _k.ZUSAMMEN)[0] == _k.ZUSAMMEN


def test_the_trough_stays_on_screen_on_a_phone():
    """Auf 375 Pixeln zeigte die Monatstabelle dreieinhalb von sieben Spalten,
    und die Spalte, um die es auf der Seite geht, lag ausserhalb."""
    import re

    html = client.get("/konten").text
    assert "table.monate .nebensache{display:none}" in html
    assert "table.monate td.monat{white-space:nowrap}" in html
    kopf = re.search(r'<table class="monate">\s*<tr>(.*?)</tr>', html, re.S).group(1)
    sichtbar = [re.sub(r"<[^>]+>", "", th).strip()
                for th in re.findall(r"<th(?![^>]*nebensache)[^>]*>.*?</th>", kopf, re.S)]
    assert sichtbar == ["Monat", "nach Kosten", "nach Gehalt"]


def test_the_account_table_folds_away_but_not_while_something_breaches():
    """Die Tabelle ist die Konfiguration der Seite, nicht ihr Befund.

    Sie darf zu sein -- aber nicht, solange ein Konto unter seiner Grenze
    liegt: dann ist sie die Stelle, an der man etwas aendert.
    """
    import re

    html = client.get("/konten").text
    stelle = html.index("Wozu welches Konto da ist")
    block = html[stelle:stelle + 600]
    assert "<details" in block, "die Tabelle muss klappbar sein"
    assert "<summary>" in block
    unter = re.search(r"(\d+) unter der eigenen Grenze", block)
    if unter:
        assert re.search(r"<details[^>]*\bopen\b", block), \
            "mit einer Unterschreitung darf die Tabelle nicht zu starten"
def test_a_goal_is_projected_to_its_own_date_and_one_without_a_date_is_not():
    """Zwei Balken, zwei Fragen -- aber nur, wo die zweite gestellt werden kann.

    Der Fortschrittsbalken sagt, was heute da ist. Er beantwortet nicht, ob man
    ankommt: 11 % bei zwanzig Jahren Laufzeit sagt fuer sich genommen nichts.
    Deshalb steht die Hochrechnung daneben.

    Der Tagesgeld-Puffer hat keinen Stichtag, und gegen das Kapital von 2046
    gehalten stuende er bei ueber 3.000 % -- eine Zahl, die keine Frage
    beantwortet, die jemand gestellt hat. Ziele ohne Stichtag bekommen deshalb
    gar keine Hochrechnung.
    """
    import re

    html = client.get("/ziele").text
    jahre = re.findall(r"extrapoliert zum <a[^>]*>(\d{4})-\d\d-\d\d</a>:", html)
    assert jahre, "keine Hochrechnung auf der Seite"
    # So viele Hochrechnungen wie Ziele MIT Stichtag, nicht wie Ziele.
    stichtage = [d for d in re.findall(r'id="z-datum-[^"]*" value="([^"]*)"', html) if d]
    # Hoechstens so viele Hochrechnungen wie Stichtage, und weniger Stichtage
    # als Ziele: ohne Stichtag gibt es keinen Zeitpunkt, auf den man sie
    # beziehen koennte. Ein Ziel gegen einen Topf bekommt seit der Kaskade die
    # Hochrechnung dieses Topfs.
    # Die Rentenluecke ist gerechnet: Stichtag ohne Formular.
    fest = int('data-ziel="rentenluecke"' in html)
    assert len(jahre) <= len(stichtage) + fest <= html.count('id="z-datum-') + fest
    # Ein Ziel ohne Stichtag bekommt keine -- an einem eigenen Testziel
    # geprueft, weil welche Ziele datiert sind, der Eigentuemer entscheidet.
    pfad = Path("config/ziele_custom.yaml")
    gesichert = pfad.read_text(encoding="utf-8") if pfad.exists() else None
    try:
        client.post("/api/ziel", json={"neu": True, "name": "Pytest Undatiert",
                                       "cents": 1_000_00})
        seite = client.get("/ziele").text
        karte = seite[seite.index('data-ziel="pytest-undatiert"'):]
        assert "extrapoliert" not in karte[:karte.index('class="ziel-bearbeiten"')]
    finally:
        if gesichert is None:
            pfad.unlink(missing_ok=True)
        else:
            pfad.write_text(gesichert, encoding="utf-8")
    # Und jede auf das Jahr ihres eigenen Stichtags, nicht auf ein gemeinsames.
    mit_hochrechnung = [d for d in stichtage if d[:4] in jahre]
    for jahr, stichtag in zip(jahre, mit_hochrechnung, strict=False):
        assert jahr == stichtag[:4]


def test_an_obligation_can_be_renamed_but_still_not_switched_off():
    """Die Sperre gilt dem Abschalten, nicht jeder Aenderung.

    Sie soll davor schuetzen, eine Schuld wegzuklicken. Auf JEDE Aenderung
    gelegt hiess sie, dass ausgerechnet die Klammer mit den echten
    Verpflichtungen fuer immer den Namen behaelt, den sie bei der Anlage bekam
    -- und Titel sind das Mittel, mit dem Varianten auseinandergehalten werden.
    """
    sid = "pytest-pflicht"
    _clean_scenario(client, sid)
    try:
        client.post("/api/szenario", json={"neu": True, "name": "Pytest Pflicht",
                                           "pflicht": True})
        assert client.post("/api/szenario-zeile", json={
            "szenario": sid, "label": "Rate", "frequenz": "monatlich",
            "amount_cents": -1000, "start": "2026-11",
            "kategorie": "konsum/sonstiges"}).status_code == 200

        assert client.post("/api/szenario",
                           json={"id": sid, "name": "Pytest Pflicht 2027"}
                           ).status_code == 200
        assert "Pytest Pflicht 2027" in client.get("/planung").text
        # Abschalten bleibt gesperrt, solange Positionen darin stehen.
        assert client.post("/api/szenario",
                           json={"id": sid, "aktiv": False}).status_code == 400
    finally:
        _clean_scenario(client, sid)


def test_the_amount_filter_writes_german():
    from finctl.web.server import betrag

    assert betrag(7200) == "72,00"
    assert betrag(123456) == "1.234,56"
    assert betrag(83100000) == "831.000,00"
    assert betrag(-2600) == "-26,00"
    assert betrag(0) == "0,00"
    # Leer und nicht "0,00": ein Ziel ohne Betrag ist nicht dasselbe wie ein
    # Ziel ueber null Euro, und das Feld soll den Unterschied zeigen.
    assert betrag(None) == ""


def test_a_planned_sale_needs_a_price_before_it_changes_anything():
    """Ohne Preis wird nichts gerechnet, und das ist die vorsichtige Richtung.

    Den Kredit enden zu lassen, ohne den Erlös zu kennen, hiesse eine Rate
    streichen und nichts dafür zahlen -- die Prognose sähe besser aus, WEIL
    eine Zahl fehlt. Ein Objekt mit Verkaufstermin und ohne Preis laesst
    Miete, Kosten und Rate weiterlaufen, bis der Preis eingetragen ist.

    WELCHES Objekt, entscheidet die Datenbank -- das erste mit Termin.
    """
    from finctl.forecast import jahre as _jm
    from finctl.web.server import conn

    c = conn()
    try:
        zeile = c.execute(
            "SELECT id, sale_price_cents FROM properties "
            "WHERE planned_sale_on IS NOT NULL ORDER BY id LIMIT 1").fetchone()
        if not zeile:
            pytest.skip("kein Objekt mit planned_sale_on")
        objekt, vorher = zeile[0], zeile[1]
        c.execute("UPDATE properties SET sale_price_cents=NULL WHERE id=?", (objekt,))
        c.commit()
        assert _jm.verkaeufe(c, 2026) == []
        ohne = _jm.project(c, opening_cents=0, end_year=2040).final_cents

        c.execute("UPDATE properties SET sale_price_cents=15000000 WHERE id=?",
                  (objekt,))
        c.commit()
        v = _jm.verkaeufe(c, 2026)
        assert len(v) == 1 and v[0].property_id == objekt
        # Die Restschuld kommt aus dem Tilgungsplan und ist NICHT null: der
        # Plan endet mit der Zinsbindung 11/2032, der Verkauf ist 2034, und
        # null zurueckzugeben machte aus einem Verkauf mit sechsstelliger
        # Restschuld einen ohne.
        assert v[0].restschuld_cents > 0
        assert v[0].netto_cents == 15_000_000 - v[0].restschuld_cents
        assert _jm.project(c, opening_cents=0, end_year=2040).final_cents != ohne
    finally:
        c.execute("UPDATE properties SET sale_price_cents=? WHERE id=?",
                  (vorher, objekt))
        c.commit()
        c.close()


def test_only_a_template_loan_can_have_its_terms_overwritten():
    """Die drei Verträge sind rekonstruiert, nicht eingetippt.

    Eine Anfangsrestschuld ist aus einem beobachteten Zins rückwärts
    gerechnet, ein Zinssatz aus zwei Zahlungen gelöst. Diese
    Zahlen im Dashboard überschreibbar zu machen hiesse, eine Tatsache durch
    eine Eingabe ersetzen zu können -- und zwar lautlos, weil beides gleich
    aussieht.
    """
    from pathlib import Path

    custom = Path("config/loans_custom.yaml")
    had = custom.read_text(encoding="utf-8") if custom.exists() else None
    basis_vorher = Path("config/loans.yaml").read_text(encoding="utf-8")
    echte = [str(k["id"]) for k in _kredite() if k.get("status") != "geplant"]
    vorlage = next((str(k["id"]) for k in _kredite() if k.get("status") == "geplant"), None)
    if not echte or not vorlage:
        pytest.skip("es braucht einen laufenden Kredit und eine Vorlage")
    try:
        for echt in echte:
            res = client.post("/api/kredit-vorlage", json={
                "id": echt, "principal_cents": 1, "annual_rate_pct": 1.0,
                "start": "2030-01", "ende": "2040-01"})
            assert res.status_code == 400, echt

        # Die Vorlage laesst sich setzen, und die Rate folgt aus der Laufzeit.
        res = client.post("/api/kredit-vorlage", json={
            "id": vorlage, "principal_cents": 42_000_000, "annual_rate_pct": 3.5,
            "start": "2032-01", "ende": "2061-02"})
        assert res.status_code == 200
        assert res.json()["annuitaet_cents"] == 191_660

        from finctl.realestate.loan import (
            amortise,
            lade_kredite,
            opening_balance_cents,
            segments_from,
        )
        loan = next(x for x in lade_kredite() if x["id"] == vorlage)
        sched = amortise(vorlage, opening_balance_cents(loan), segments_from(loan))
        assert opening_balance_cents(loan) == 42_000_000
        # Getilgt GENAU zum eingetragenen Ende -- das ist der ganze Zweck der
        # Eingabe: das Ende ist die Entscheidung, die Rate ihr Preis.
        assert sched.payoff_month().isoformat() == "2061-02-01"

        # Und loans.yaml bleibt unangetastet, samt seiner Herleitungen.
        assert Path("config/loans.yaml").read_text(encoding="utf-8") == basis_vorher
    finally:
        client.post("/api/kredit-vorlage", json={"id": vorlage, "entfernen": True})
        if had is None:
            custom.unlink(missing_ok=True)
        else:
            custom.write_text(had, encoding="utf-8")


def test_a_sortable_column_sorts_by_its_field_and_not_by_the_badge_beside_it():
    """Die Spalte sah sortierbar aus und ordnete etwas anderes, als in ihr steht.

    Die Kategoriezelle auf /transactions traegt neben dem Kombifeld ein
    Abzeichen wie "rule · w82a-hausgeld". Der Sortierer nahm zuerst
    `cell.textContent` -- das ist der Abzeichentext, denn ein <input>
    steuert zu textContent nichts bei --, und sortierte die Spalte
    "Kategorie" damit nach dem REGELNAMEN.

    Geprueft wird hier die Regel im Sortierer, nicht das Ergebnis im
    Browser: ein Feld ist der Inhalt seiner Zelle, Abzeichen sind Beiwerk.
    """
    js = (Path("finctl/web/templates/_resize.html")
          .read_text(encoding="utf-8"))
    kopf = js[js.index("function cellText(cell)"):js.index("function sortValue")]
    # Das Feld wird VOR dem Zellentext gefragt.
    assert kopf.index("querySelector(") < kopf.index("cell.textContent")
    # Ankreuzfelder tragen "on" und nicht den Text, den man liest.
    assert "[type=checkbox]" in kopf
    # Ein <select> sortiert nach dem sichtbaren Text, nicht nach der id.
    assert "selectedOptions" in kopf

    # Und die Zelle, um die es ging, hat beides nebeneinander.
    html = client.get("/transactions").text
    assert 'class="catsel"' in html and 'rule ·' in html


def test_a_filtered_list_says_what_it_adds_up_to():
    """47 Zeilen Lebensmittel sagen nichts, 3.888,60 schon.

    Die Kopfzeile zaehlte nur Treffer -- und beantwortete damit die Frage
    nicht, die man beim Filtern stellt. Zwei Dinge werden hier festgehalten:

    * Die Summe gilt fuer den FILTER, nicht fuer die angezeigten Zeilen. Auf
      /transactions sind die auf 200 gedeckelt, die Summe laeuft ueber alle.
    * Raus und rein stehen daneben. Eine Nettosumme aus gemischten Vorzeichen
      luegt: 0,00 kann "nichts passiert" heissen oder "12.000 raus, 12.000
      rein".
    """
    import re

    def kopf(pfad, muster=r"<h1>(.*?)</h1>"):
        m = re.search(muster, client.get(pfad).text, re.S)
        return " ".join(re.sub("<[^>]+>", " ", m.group(1)).split())

    alle = kopf("/transactions")
    assert "Treffer" in alle and "Summe" in alle and "angezeigt" in alle

    # Gefiltert: weniger Treffer, andere Summe, und kein Hinweis auf
    # abgeschnittene Zeilen, solange alles zu sehen ist.
    eng = kopf("/transactions?category=lebensmittel")
    assert "raus" in eng and "rein" in eng
    assert "angezeigt" not in eng

    # Der Review-Zaehler hat den Kontofilter frueher ignoriert und zeigte mit
    # ausgewaehltem Konto weiter die Gesamtzahl der Warteschlange.
    gesamt = int(re.search(r"— (\d+) Treffer", kopf("/transactions?ansicht=offen")).group(1))
    eines = int(re.search(r"— (\d+) Treffer",
                          kopf(f"/transactions?ansicht=offen&account={_rolle('operating')}"))
                 .group(1))
    assert eines <= gesamt

    objekt = kopf(f"/immobilie/{_ein_objekt()}", r"<h2>Transaktionen(.*?)</h2>")
    assert "Treffer" in objekt and "Summe" in objekt


def test_the_monthly_close_ticks_itself_and_offers_no_checkbox():
    """Kein Haken zum Anklicken, und das ist der Entwurf, nicht eine Luecke.

    Ein Haken ist eine Behauptung ueber die Daten, die neben den Daten liegt
    und falsch werden kann, ohne dass es auffaellt -- abgehakt und trotzdem
    veraltet ist genau der Zustand, den diese Seite verhindern soll. Jede
    Zeile liest deshalb ihren eigenen Beleg: ein Auszug, der den Vormonat
    abdeckt, oder ein Stand aus der laufenden Periode.
    """
    html = client.get("/monatsabschluss").text
    assert 'type="checkbox"' not in html
    assert "Auszug bis" in html          # der Beleg der geparsten Konten
    assert "Stand " in html              # das Datum der getippten Werte

    # Die vier Schritte des Monatsablaufs, verlinkt.
    for ziel in ("/transactions?ansicht=offen", "/planung", "/konten", "/ziele"):
        assert f'href="{ziel}"' in html

    # Und die beiden Handgriffe, mit denen der Monat beginnt und endet. Sie
    # standen auf der Overview -- auf der Seite, die "wie steht es"
    # beantwortet, statt auf der, die "was muss ich tun" beantwortet.
    assert 'id="btn-update"' in html and 'id="btn-backup"' in html


def test_an_unchanged_holding_is_filed_rather_than_ticked():
    """"Ich will das nicht checken, ich will einfach ablegen."

    Hat sich ein Wert nicht geaendert, wird er trotzdem abgelegt -- derselbe
    Betrag mit heutigem Datum. Danach steht in den Daten, was ein Haken nur
    behauptet haette, und die Zeile ist aus demselben Grund erledigt wie jede
    andere.
    """
    from datetime import date

    from finctl import monatsabschluss as ma

    heute = date.today()
    periode, jahr = f"{heute:%Y-%m}", f"{heute:%Y}"
    alt = date(heute.year - 2, 1, 1)

    posten = ma.bestaende(
        [{"account_id": "pytest-depot", "name": "Pytest Depot", "kind": "depot",
          "cents": 12345, "as_of": alt}], heute, periode, jahr)
    assert len(posten) == 1 and not posten[0].erledigt
    # Die Zeile traegt, was das Ablegen braucht: Kennung und Betrag.
    assert posten[0].schluessel == "pytest-depot" and posten[0].cents == 12345

    frisch = ma.bestaende(
        [{"account_id": "pytest-depot", "name": "Pytest Depot", "kind": "depot",
          "cents": 12345, "as_of": heute}], heute, periode, jahr)
    assert frisch[0].erledigt and frisch[0].automatisch

    # Renten laufen im Jahresrhythmus: ein Stand vom April zaehlt im September.
    rente = ma.bestaende(
        [{"name": "Pytest Rente", "kind": "rentenversicherung", "cents": 1,
          "as_of": date(heute.year, 1, 15)}], heute, periode, jahr)
    assert rente[0].gruppe == "jaehrlich" and rente[0].erledigt


def test_a_parsed_account_is_judged_by_the_last_complete_month():
    """Am 13. September kann es den Septemberauszug nicht geben.

    Gegen den laufenden Monat zu pruefen haette jeden Monatsanfang alles rot
    gefaerbt -- und eine Seite, auf der alles leuchtet, liest man nach dem
    zweiten Monat nicht mehr.
    """
    from datetime import date

    from finctl import monatsabschluss as ma
    from finctl.web.server import conn

    c = conn()
    try:
        zeilen = ma.konten(c, date(2026, 9, 13), "2026-09")
        assert zeilen and all(p.automatisch for p in zeilen)
        # August ist abgedeckt, also alles erledigt -- ohne dass ein
        # Septemberauszug existieren muesste.
        assert all(p.erledigt for p in zeilen), [p.name for p in zeilen if not p.erledigt]

        # Ein Monat weiter, ohne neue Auszuege: jetzt fehlt der September.
        spaeter = ma.konten(c, date(2026, 10, 5), "2026-10")
        assert any(not p.erledigt for p in spaeter)

        # Was fehlt, ersetzt den Beleg nicht: wie weit der Auszug reicht, steht
        # weiter da (auszug-ist-nicht-editierbar), daneben der fehlende Monat.
        viel_spaeter = ma.konten(c, date(2030, 2, 5), "2030-02")
        for p in viel_spaeter:
            if p.stand:
                assert p.hinweis == f"Auszug bis {p.stand.isoformat()} · 2030-01 fehlt", p.name
    finally:
        c.close()


def test_what_the_dashboard_writes_survives_a_rebuilt_database():
    """Der Grundsatz des Eigentuemers: Konfiguration plus Auszuege genuegen.

    Drei Stellen hielten ihn nicht. Kaufpreis, Eigenkapital und
    Verkaufspreis, die Kontountergrenzen und die Gehaltsuntergrenze gingen
    ausschliesslich in die Datenbank -- und das sind genau die Zahlen, die
    sich aus keinem Kontoauszug zurueckgewinnen lassen, weil sie nirgends
    gebucht sind. Datenbank weg hiess: weg, obwohl jede YAML noch dalag.

    Geprueft wird nicht, dass die Datei geschrieben wird, sondern dass eine
    LEERE Datenbank daraus wieder denselben Stand bekommt. Das ist die
    Eigenschaft, um die es geht.
    """
    from finctl import overlays as ov
    from finctl.web.server import conn

    props_vorher, sets_vorher = ov.objekte(), ov.einstellungen()
    konto = _rolle("operating")
    c = conn()
    try:
        objekt = _ein_objekt()
        alt = dict(c.execute(
            "SELECT sale_price_cents, purchase_price_cents FROM properties "
            "WHERE id = ?", (objekt,)).fetchone())
        assert client.post(f"/api/property/{objekt}", json={
            "sale_price_cents": 15_000_000,
            "purchase_price_cents": 13_500_000}).status_code == 200
        assert client.post(f"/api/floor/{konto}",
                           json={"cents": 75_700}).status_code == 200

        # In der Datei, nicht nur in der Datenbank.
        assert ov.objekte()[objekt]["sale_price_cents"] == 15_000_000
        assert ov.einstellungen()[f"floor_cents:{konto}"] == 75_700

        # Und jetzt der Punkt: alles aus der Datenbank raus, dann zurueck.
        c.execute("UPDATE properties SET sale_price_cents = NULL, "
                  "purchase_price_cents = NULL WHERE id = ?", (objekt,))
        c.execute("DELETE FROM settings")
        c.commit()
        ov.anwenden(c)

        wieder = dict(c.execute(
            "SELECT sale_price_cents, purchase_price_cents FROM properties "
            "WHERE id = ?", (objekt,)).fetchone())
        assert wieder["sale_price_cents"] == 15_000_000
        assert wieder["purchase_price_cents"] == 13_500_000
        assert c.execute("SELECT value FROM settings WHERE key = ?",
                         (f"floor_cents:{konto}",)).fetchone()[0] == "75700"
    finally:
        ov.schreiben("properties_custom.yaml", "objekte", props_vorher,
                     ov.OBJEKTE_KOPF)
        ov.schreiben("settings_custom.yaml", "einstellungen", sets_vorher,
                     ov.EINSTELLUNGEN_KOPF)
        c.execute("DELETE FROM settings")
        c.execute("UPDATE properties SET sale_price_cents = ?, "
                  "purchase_price_cents = ? WHERE id = ?",
                  (alt["sale_price_cents"], alt["purchase_price_cents"], objekt))
        c.commit()
        ov.anwenden(c)
        c.close()


def test_setting_an_account_floor_does_not_five_hundred():
    """`updated_at` ist NOT NULL und fehlte im INSERT.

    Das Setzen einer Kontountergrenze schlug fehl, seit es den Knopf gibt --
    im Browser sah man nur, dass sich nichts tut. Gefunden erst, als der
    Aufruf zum ersten Mal in einem Test lief.
    """
    from finctl import overlays as ov

    vorher = ov.einstellungen()
    konto = _rolle("operating")
    try:
        res = client.post(f"/api/floor/{konto}", json={"cents": 250_000})
        assert res.status_code == 200, res.text
        assert res.json()["cents"] == 250_000
    finally:
        ov.schreiben("settings_custom.yaml", "einstellungen", vorher,
                     ov.EINSTELLUNGEN_KOPF)
        from finctl.web.server import conn
        c = conn()
        c.execute("DELETE FROM settings WHERE key = ?", (f"floor_cents:{konto}",))
        c.commit()
        c.close()


def test_the_open_header_lays_its_groups_out_side_by_side():
    """Fünf schwebende Karten, die sich gegenseitig verdecken, sind keine Navigation.

    Der Knopf öffnet alle Gruppen auf einmal. Solange die Menüs dabei absolut
    positioniert blieben, lagen sie übereinander -- jedes an seinem eigenen
    Gruppennamen ausgerichtet, aber breiter als der Abstand dazwischen.
    Aufgeklappt stehen sie deshalb im Fluss und damit nebeneinander; nur beim
    Hovern schwebt eines.
    """
    css = Path("finctl/web/templates/base.html").read_text(encoding="utf-8")
    block = css[css.index("header.alleoffen .navlinks{"):]
    block = block[:block.index("}") + 1]
    assert "position:static" in block
    # Und während alles offen ist, darf das Hovern nichts mehr ausfahren --
    # sonst steht über der stehenden Spalte noch eine schwebende.
    assert "header.alleoffen .navgruppe:hover .navlinks" in css


def test_saving_a_row_says_what_it_became():
    """Ein grünes Aufblitzen sagt „gespeichert", aber nicht WAS jetzt gilt.

    Die Herkunftsmarke ist die einzige Stelle, an der man sieht, ob eine Zeile
    noch einer Regel folgt oder eigene Entscheidung ist -- und sie blieb beim
    Speichern auf „rule · w82a-hausgeld" stehen, obwohl die Zeile ab diesem
    Klick manuell war und keine Regelkorrektur sie je wieder anfasst. Die eine
    Folge des Klicks, die man wissen muss, war die einzige, die man nicht sah.

    Geschrieben wird hier auf eine Zeile, die BEREITS manuell ist, und mit
    ihrer eigenen Kategorie: der Aufruf ist damit ein Nulldurchgang. Ein Test
    darf die Entscheidungen des Eigentuemers nicht umwerfen, um sein eigenes
    Verhalten zu pruefen.
    """
    from finctl.web.server import conn

    c = conn()
    try:
        zeile = c.execute(
            "SELECT s.transaction_id AS tid, s.mgmt_category_id AS kat "
            "FROM splits s JOIN transactions t ON t.id = s.transaction_id "
            "WHERE s.source = 'manual' AND s.seq = 0 "
            "  AND s.mgmt_category_id IS NOT NULL "
            "  AND (SELECT COUNT(*) FROM splits x "
            "       WHERE x.transaction_id = s.transaction_id) = 1 "
            "ORDER BY t.booking_date DESC LIMIT 1").fetchone()
    finally:
        c.close()
    if zeile is None:
        pytest.skip("keine manuell gesetzte Einzelzeile im Ledger")
    tid, kat = zeile["tid"], zeile["kat"]

    pfad = Path("config/overrides.yaml")
    vorher_datei = pfad.read_text(encoding="utf-8")
    c = conn()
    try:
        vorher_zeile = dict(c.execute(
            "SELECT mgmt_category_id, tax_category_id, property_id, source, rule_id "
            "FROM splits WHERE transaction_id = ? AND seq = 0", (tid,)).fetchone())
    finally:
        c.close()

    res = client.post(f"/api/categorize/{tid}", data={"mgmt": kat})
    assert res.status_code == 200
    daten = res.json()
    # Der Server sagt, was jetzt dasteht, statt dass die Seite es raet.
    assert daten["source"] == "manual"
    assert daten["kategorie"] == kat
    assert daten["label"]

    # Die Marke ist adressierbar und unterscheidet sich vom Regelergebnis.
    html = client.get("/transactions?q=&limit=2000").text
    assert f'id="src-{tid}"' in html
    marke = html[html.index(f'id="src-{tid}"') - 200:html.index(f'id="src-{tid}"')]
    assert "eigen" in marke
    assert ".pill.eigen" in Path("finctl/web/templates/base.html").read_text(encoding="utf-8")

    # Und zurueck, Datei wie Zeile. Der Aufruf ergaenzt die Steuerposition aus
    # der Kategorie -- eine sinnvolle Ergaenzung, aber keine, die ein Testlauf
    # in den Daten des Eigentuemers hinterlassen darf.
    pfad.write_text(vorher_datei, encoding="utf-8")
    c = conn()
    try:
        c.execute("UPDATE splits SET mgmt_category_id = ?, tax_category_id = ?, "
                  "property_id = ?, source = ?, rule_id = ? "
                  "WHERE transaction_id = ? AND seq = 0",
                  (vorher_zeile["mgmt_category_id"], vorher_zeile["tax_category_id"],
                   vorher_zeile["property_id"], vorher_zeile["source"],
                   vorher_zeile["rule_id"], tid))
        c.commit()
    finally:
        c.close()


def test_saving_always_says_something_even_when_it_writes_nothing():
    """Speichern tat manchmal nichts und sagte nichts -- das sah aus wie ein Fehler.

    Zwei Fallen lagen uebereinander. `dataset.id` setzt die Vorschlagsliste
    nur beim ANKLICKEN; wer den Kategorienamen zu Ende tippt und Speichern
    drueckt, loeste gar nichts aus -- der naheliegendste Weg war der einzige,
    der nicht funktionierte. Und wenn nichts geschrieben wurde, gab es keine
    Rueckmeldung, also auch keinen Hinweis darauf, dass etwas fehlte.
    """
    quelle = Path("finctl/web/templates/transactions.html").read_text(encoding="utf-8")

    # Eine Statuszeile je Zeile, neben dem Knopf statt am Rand der Zelle.
    html = client.get("/transactions").text
    assert 'id="msg-' in html

    # Ein ausgeschriebener Treffer zaehlt wie ein angeklickter.
    assert "CATS.find(c => c.label.toLowerCase() === tipp" in quelle
    # Und jeder Ausgang sagt etwas: geschrieben, nichts geaendert, unbekannt.
    for satz in ("'gespeichert'", "'nichts geändert'", "Kategorie unbekannt"):
        assert satz in quelle, satz


def test_saving_a_holding_reports_next_to_its_own_button():
    """Eine Statuszeile unter allen Tabellen ist bei zehn Positionen keine.

    Sie stand am Seitenende -- ausserhalb des Blickfelds, genau in dem Moment,
    in dem man sie braucht. Jetzt meldet jede Zeile neben ihrem eigenen Knopf,
    und die Zeile blitzt auf wie in der Transaktionstabelle: dieselbe Geste
    soll ueberall dasselbe aussehen.
    """
    html = client.get("/monatsabschluss").text
    assert re.search(r'<tr id="pos-[^"]+"[^>]*>.*?<span class="meldung fein"></span>', html, re.S)
    quelle = Path("finctl/web/templates/monatsabschluss.html").read_text(encoding="utf-8")
    for satz in ("'gespeichert'", "'keine Zahl'", "classList.add('gespeichert')"):
        assert satz in quelle, satz


def test_a_goal_can_measure_against_one_pot_and_says_so():
    """„Summe aller Depots gegen 30.000" ist eine andere Frage als das Vermögen.

    Ohne die Einschränkung hätte der Balken beim ersten Blick 100 % gezeigt:
    Tagesgeld und Rentenversicherungen decken 30.000 zehnfach, und genau das
    soll ein Depotziel nicht messen.

    Zwei Balken, die verschiedene Dinge messen, müssen es sagen -- sonst sieht
    der eine aus wie der andere.
    """
    import yaml

    from finctl.forecast import ziele as z

    bal = yaml.safe_load(Path("config/balances.yaml").read_text(encoding="utf-8"))
    alles = z.liquid_cents(bal)
    depots = z.liquid_cents(bal, ("depot",))
    assert 0 < depots < alles

    # Verbindlichkeiten werden bei verengter Basis NICHT abgezogen: fremdes
    # Geld liegt auf Tagesgeld, und es von einem Depotziel abzuziehen hiesse,
    # dasselbe Geld zweimal zu verplanen.
    nur_tagesgeld = z.liquid_cents(bal, ("tagesgeld",))
    roh = sum(int(r["cents"]) for r in bal["balances"]
              if r.get("cents") is not None and r.get("kind") == "tagesgeld")
    assert nur_tagesgeld == roh

    html = client.get("/ziele").text
    if 'data-ziel="depot-2027"' not in html:
        pytest.skip("Depotziel auf der Seite geloescht")
    block = html[html.index('data-ziel="depot-2027"'):]
    block = block[:block.index("</details>")]
    assert "Gemessen an" in block and "Depots" in block
    # Seit der Kaskade fuehrt die Jahresrechnung das Depot als eigenen Topf --
    # das Depotziel bekommt dessen Hochrechnung.
    if 'data-ziel="depot-2027"' in html and "Stichtag" in block:
        assert "extrapoliert" in block


def test_goals_are_ordered_by_when_they_are_due():
    """Die Liste soll sich als Weg lesen, nicht als Aufzählung.

    Erst, was ohne Termin immer gilt -- der Tagesgeld-Puffer ist eine
    stehende Untergrenze und kein Meilenstein --, dann chronologisch von nah
    nach fern. Was als nächstes ansteht, steht oben.
    """
    import re

    import yaml as _yaml

    # Ohne gezogene Reihenfolge: die gewinnt sonst, und das ist gewollt --
    # geprueft wird hier nur die Vorgabe.
    pfad = Path("config/ziele_custom.yaml")
    gesichert = pfad.read_text(encoding="utf-8") if pfad.exists() else None
    try:
        if gesichert:
            spec = _yaml.safe_load(gesichert) or {}
            spec.pop("reihenfolge", None)
            pfad.write_text(_yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
        html = client.get("/ziele").text
    finally:
        if gesichert is not None:
            pfad.write_text(gesichert, encoding="utf-8")
    # Reihenfolge der Ziele am Datumsfeld ablesen: leer heisst ohne Stichtag.
    daten = re.findall(r'id="z-datum-[^"]*" value="([^"]*)"', html)
    assert daten, "keine Ziele auf der Seite"

    ohne = [i for i, d in enumerate(daten) if not d]
    mit = [(i, d) for i, d in enumerate(daten) if d]
    # Alles ohne Stichtag steht vor allem mit.
    assert all(i < j for i in ohne for j, _ in mit)
    # Und die datierten steigen.
    assert [d for _, d in mit] == sorted(d for _, d in mit)


def test_a_narrowed_goal_names_what_it_could_not_count():
    """Ein Balken, der eine wertlose Position verschweigt, sieht präzise aus.

    Bestände ohne Wert werden übersprungen statt als null gezählt -- richtig,
    denn gestaktes ETH ist etwas wert. Bei einer verengten Basis fällt eine
    von fünf Positionen aber ins Gewicht, und dann muss dastehen, welche
    fehlt.
    """
    import yaml

    from finctl.forecast import ziele as z

    bal = yaml.safe_load(Path("config/balances.yaml").read_text(encoding="utf-8"))
    ziel = {"ziele": [{"id": "t", "name": "t", "cents": 3_000_000,
                       "stichtag": "2027-09-01", "basis": ["depot", "krypto"]}]}
    p = z.progress(ziel, bal)[0]
    # balances.yaml führt einen Bestand bewusst ohne Wert ("OFFEN: Wert zum
    # Verkaufszeitpunkt") -- genau der Fall, um den es hier geht. Welcher das
    # ist, steht in der Datei, nicht hier.
    offen = {str(r.get("name") or r.get("account_id"))
             for r in bal["balances"]
             if r.get("cents") is None and r.get("kind") in ("depot", "krypto")}
    assert offen, "kein Bestand ohne Wert in balances.yaml"
    assert offen <= set(p.ohne_wert)
    assert p.have_cents > 0        # der Rest zählt trotzdem


def test_a_goal_basis_can_name_single_accounts_not_just_kinds():
    """Niemand denkt in Kategorien.

    Der Notgroschen des Eigentuemers ist "DKB, TR, C24, Scalable, Sparda" und
    nicht "alles Tagesgeld". `basis` in goals.yaml nimmt deshalb beides --
    Bestandsarten und einzelne Positionen, gemischt. Eine Position zaehlt
    hoechstens einmal, Ueberschneidungen sind also harmlos.
    """
    # Girokonten tragen ihren Wert nicht mehr in der Datei, sondern aus dem
    # Auszug -- dieselbe Zusammenfuehrung wie /ziele.
    import sqlite3

    import yaml

    from finctl import bestaende as best
    from finctl.forecast import ziele as z

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    db = sqlite3.connect("data/finance.db")
    db.row_factory = sqlite3.Row
    try:
        bal = best.zusammenfuehren(db, yaml.safe_load(
            Path("config/balances.yaml").read_text(encoding="utf-8")))
    finally:
        db.close()
    # Welche Konten das sind, steht in balances.yaml.
    konten = tuple(str(b["account_id"]) for b in bal["balances"]
                   if b.get("kind") in ("giro", "tagesgeld"))

    ueber_arten = z.liquid_cents(bal, ("giro", "tagesgeld"))
    ueber_konten = z.liquid_cents(bal, konten)
    # Dieselben Positionen, zwei Schreibweisen, eine Zahl.
    assert ueber_arten == ueber_konten > 0
    assert ueber_konten < z.liquid_cents(bal)

    # Und eine einzelne Position misst wirklich nur diese.
    einzeln = next(k for k in konten if 0 < z.liquid_cents(bal, (k,)) < ueber_konten)
    # Gemischt, mit Ueberschneidung: nichts wird doppelt gezaehlt.
    assert z.liquid_cents(bal, ("giro", "tagesgeld", einzeln)) == ueber_arten


def test_the_basis_picker_offers_kinds_and_not_every_single_holding():
    """Zwölf Haken für eine Frage, die mit fünf beantwortet ist.

    Der Kasten bietet die ARTEN an -- alle Girokonten, alles Tagesgeld, alle
    Depots. Einzelne Positionen bleiben in goals.yaml möglich und werden
    richtig gerechnet; die Seite fragt nur nicht danach.
    """
    html = client.get("/ziele").text
    assert 'id="z-form-tagesgeld-puffer"' in html
    for art in ("giro", "tagesgeld", "depot", "rentenversicherung"):
        assert f'value="{art}"' in html, art
    # Krypto ist ein Depot und kein eigener Haken (ziele.GLEICHE_ART).
    assert 'value="krypto"' not in html
    # Und eben NICHT jede einzelne Position.
    for konto in ("dkb-giro", "sparda-giro", "deutsche-bank-depot"):
        assert f'value="{konto}"' not in html, konto

    # Speichern schreibt ins Overlay und leer loescht den Eintrag wieder.
    import yaml

    pfad = Path("config/ziele_custom.yaml")
    vorher = pfad.read_text(encoding="utf-8")
    # Irgendein geltendes Ziel -- welche es gibt, entscheidet die Seite.
    gid = _ziele_wie_konfiguriert()[0]["id"]
    try:
        client.post("/api/ziel", json={"id": gid, "basis": ["depot"]})
        gespeichert = yaml.safe_load(pfad.read_text(encoding="utf-8"))
        assert gespeichert["ziele"][gid]["basis"] == ["depot"]
        client.post("/api/ziel", json={"id": gid, "basis": []})
        gespeichert = yaml.safe_load(pfad.read_text(encoding="utf-8"))
        assert "basis" not in gespeichert["ziele"][gid]
    finally:
        pfad.write_text(vorher, encoding="utf-8")


def test_an_assumption_turned_on_the_page_reaches_the_projection():
    """Eine Stellschraube, die nur die Seite bewegt, ist eine Attrappe.

    Bis hierher las jeder Rechner direkt aus assumptions.yaml, und das
    Dashboard schrieb in settings_custom.yaml -- zwei Quellen, von denen die
    Ueberschreibung nur den Gehaltsfloor erreichte, und den auch nur in der
    Kontenvorschau. Jetzt legt `assumptions.load` das Overlay ueber die
    Datei, also sehen ALLE Leser denselben Wert.
    """
    from finctl import assumptions as ann
    from finctl import overlays as ov
    from finctl.forecast import jahre as jm
    from finctl.web.server import conn

    vorher = ov.einstellungen()
    try:
        c = conn()
        basis = jm.project(c, opening_cents=0, end_year=2040).final_cents
        c.close()

        assert client.post("/api/setting", json={"key": "rendite_nominal_pa",
                                                 "value": 0.05}).status_code == 200
        ann.reset_cache()
        assert ann.rendite_nominal_pa() == 0.05
        # Die dokumentierte Vorgabe bleibt, was sie war.
        assert ann.basiswert("rendite_nominal_pa") != 0.05

        c = conn()
        mehr = jm.project(c, opening_cents=0, end_year=2040).final_cents
        c.close()
        assert mehr > basis, "die Rendite bewegt die Hochrechnung nicht"

        # Auf die Vorgabe zurueckgesetzt heisst: kein Eintrag mehr, nicht ein
        # Eintrag mit demselben Wert. Sonst staende dieselbe Zahl zweimal.
        vorgabe = ann.basiswert("rendite_nominal_pa")
        client.post("/api/setting", json={"key": "rendite_nominal_pa",
                                          "value": vorgabe})
        assert "rendite_nominal_pa" not in ov.einstellungen()
    finally:
        ov.schreiben("settings_custom.yaml", "einstellungen", vorher,
                     ov.EINSTELLUNGEN_KOPF)
        ann.reset_cache()


def test_the_assumptions_page_separates_dials_from_facts():
    """Ein Formular, das alles gleich darstellt, lädt dazu ein, eine
    Standmitteilung zu überschreiben, weil sie neben der Inflationsrate steht.
    """
    html = client.get("/annahmen").text
    assert "Stellschrauben" in html and "Gemessen" in html and "Belegt" in html

    # Eingabefelder nur für die sechs Stellschrauben.
    from finctl import assumptions as ann

    for key in ann.UEBERSCHREIBBAR:
        assert f'id="a-{key}"' in html, key
    # Die Rentenwerte stehen da, aber ohne Feld.
    assert "Gesetzliche Rente" in html
    assert 'id="a-grv' not in html


def test_the_policies_are_named_by_the_config_not_by_the_code():
    """Die Anbieternamen standen bis zum 23.09.2026 in `routen/annahmen.py`.

    Zwei Versicherer in einer Datei, die spaeter weitergegeben werden soll --
    und fuer jeden anderen Nutzer schlicht falsch. Jetzt sagt
    config/renten.yaml, welche Quellen es gibt und wie sie heissen.

    Geprueft wird beides: dass der Name auf der Seite steht, UND dass der
    Betrag daneben erscheint. Nur der Name koennte auch eine leere Zeile sein.
    """
    from finctl import renten as _renten
    from finctl.web.basis import euro as _euro

    quellen = _renten.quellen()
    if not quellen:
        pytest.skip("keine Rentenquellen")

    html = client.get("/annahmen").text
    for q in quellen:
        assert q.name in html, q.name
        assert _euro(q.cents) in html, q.id
        # Kein Eingabefeld: eine Standmitteilung wird nicht auf Annahmen getippt.
        assert f'id="a-{q.id}"' not in html

    # Und der Gehaltsfloor sagt, dass er die Ziele NICHT bewegt.
    stelle = html.index("Gehalts-Untergrenze")
    assert "NICHT die Ziele" in html[stelle:stelle + 600]


def test_the_salary_median_is_measured_not_maintained():
    """„Neu zu messen, wenn ein Jahr voll ist" -- die Notiz war abgelaufen.

    Der eingetragene Wert stammte aus einem einzelnen guten Jahr. Über
    vierundzwanzig Monate liegt der Median tiefer, und der Unterschied im
    Endkapital 2046 betrug 126.576. Eine Zahl, die von Hand nachgeführt
    werden muss, wird nicht nachgeführt.
    """
    from finctl.forecast import jahre as jm
    from finctl.web.server import conn

    c = conn()
    try:
        gemessen = jm.gehalt_median_cents(c)
    finally:
        c.close()
    assert gemessen and gemessen > 0

    # Er steht auf der Seite als GEMESSEN, nicht als Eingabefeld.
    html = client.get("/annahmen").text
    assert "Gehalts-Median" in html
    assert 'id="a-netto_median' not in html


def test_the_pkv_deadline_is_computed_and_shown_a_year_early():
    """Die einzige Frist im Plan, die sich nicht verschieben lässt.

    §6 Abs. 3a SGB V kennt keine Ausnahme: wer 55 ist und fünf Jahre privat
    versichert war, bleibt es. Angezeigt wird deshalb nicht die Sperre,
    sondern der Tag ein Jahr davor — eine Frist, die man am Tag ihres
    Ablaufs bemerkt, ist verpasst.

    Gerechnet aus Geburtstag und Art der Krankenversicherung, nicht
    eingetippt: ein Datum, das an zwei Stellen steht, stimmt irgendwann an
    einer nicht mehr.
    """
    from finctl import person as _person

    if _person.pkv_stichtag() is None:
        pytest.skip("nicht privat versichert")
    g = _person.geburtstag()
    sperre, stichtag = _person.pkv_sperre(), _person.pkv_stichtag()
    assert sperre.year - g.year == 55
    assert (sperre.month, sperre.day) == (g.month, g.day)
    assert sperre.year - stichtag.year == 1

    html = client.get("/annahmen").text
    assert "PKV-Entscheidung" in html
    assert stichtag.isoformat() in html
    assert "§6 Abs. 3a SGB V" in html
    # Steht unter „Belegt", nicht bei den Stellschrauben: kein Eingabefeld.
    assert 'id="a-pkv' not in html


def test_the_projection_shows_its_own_inputs_year_by_year():
    """Am Ende stand eine Zahl und dazwischen nichts.

    Man konnte die Hochrechnung glauben oder nicht, aber nicht prüfen. Jetzt
    steht jedes Jahr mit seinen Blöcken da — dieselben Blöcke wie im Abgleich
    auf /planung, nur aufs Jahr statt auf den Monat.
    """
    import re

    from finctl.forecast import jahre as jm
    from finctl.web.server import conn

    # Seit dem 27.09.2026 auf einer eigenen Seite der Vorausschau; auf
    # /annahmen wird gedreht, hier steht die Wirkung.
    html = client.get("/hochrechnung").text
    assert '<h2 id="jahre">Jahr für Jahr</h2>' in html
    assert "Jahr für Jahr" not in client.get("/annahmen").text
    block = html[html.index('<h2 id="jahre">'):]
    block = block[:block.index("</table></div>")]
    assert "Sparrate" not in client.get("/ziele").text

    # Die Summen stehen in der Zeile, die Bloecke im aufklappbaren Rechenweg.
    for label in ("laufend rein", "laufend raus", "einmalig rein", "einmalig raus",
                  "Saldo", "Endkapital", "Gehalt", "Mieteinnahmen", "Kreditraten",
                  "Fixkosten"):
        assert label in block, label

    # Die Zeilen decken den ganzen Horizont ab -- und die letzte Zeile nennt
    # DIESELBE Zahl wie der Balken auf /ziele. Zwei Zahlen fuer eine Rechnung
    # waeren schlimmer als eine ungenaue; genau das passierte, solange die
    # Tabelle bei null Ausgangsbestand und bis 2045 rechnete.
    c = conn()
    try:
        # Derselbe Horizont wie auf der Seite: bis 2045, zum spaetesten
        # Stichtag der Ziele oder zur Lebenserwartung, was spaeter liegt.
        import yaml as _yh

        from finctl.forecast import ziele as _zh
        from finctl.web.routen.annahmen import _lebensende
        eigene = Path("config/ziele_custom.yaml")
        _ziele = _zh.merge_edits(
            _yh.safe_load(Path("config/goals.yaml").read_text(encoding="utf-8")),
            (_yh.safe_load(eigene.read_text(encoding="utf-8")) or {}) if eigene.exists() else {})
        horizont = max([2045, *_lebensende(), *(int(str(g["stichtag"])[:4])
                                for g in _ziele.get("ziele") or [] if g.get("stichtag"))])
        lauf = jm.project(c, opening_cents=0, end_year=horizont)
    finally:
        c.close()
    jahre = re.findall(r'<tr class="klappbar" id="jahr-(\d{4})"', block)
    assert jahre[0] == str(lauf.years[0].year)
    assert len(jahre) == len(lauf.years)

    # Ein eigenes Ziel ueber alles Liquide, faellig am 1. Januar nach dem
    # vorletzten Jahr: sein Balken ist genau das Jahresende davor -- Endkapital
    # ohne den Notgroschen, den die Tabelle im Tagesgeld zeigt.
    import yaml

    from finctl.forecast import ziele as zm

    def euro(text):
        return int(text.replace("€", "").replace(".", "").replace(",", "").strip())

    vorletztes = int(jahre[-2])
    pfad = Path("config/ziele_custom.yaml")
    gesichert = pfad.read_text(encoding="utf-8") if pfad.exists() else None
    try:
        client.post("/api/ziel", json={"neu": True, "name": "Pytest Abgleich",
                                       "cents": 1_000_000_00,
                                       "stichtag": f"{vorletztes + 1}-01-01"})
        html = client.get("/ziele").text
        karte = html[html.index('data-ziel="pytest-abgleich"'):]
        betrag = re.search(r"extrapoliert zum <a[^>]*>[\d-]+</a>: <strong>([^<]+)", karte).group(1)
        ziele = zm.merge_edits(
            yaml.safe_load(Path("config/goals.yaml").read_text(encoding="utf-8")),
            yaml.safe_load(pfad.read_text(encoding="utf-8")))
    finally:
        if gesichert is None:
            pfad.unlink(missing_ok=True)
        else:
            pfad.write_text(gesichert, encoding="utf-8")
    zeile = re.search(rf'id="jahr-{vorletztes}".*?</tr>', block, re.S).group(0)
    betraege = re.findall(r'<td class="num[^"]*">(?:<strong>)?([^<]*€)', zeile)
    tagesgeld, _depot, endkapital = (euro(x) for x in betraege[-3:])
    puffer = zm.puffer_cents(ziele) or 0
    assert euro(betrag) == endkapital - min(max(tagesgeld, 0), puffer), (betrag, zeile[-300:])

    # Das Objekt endet mit dem Leasehold: im ersten Jahr danach nichts mehr.
    #
    # Gemessen am Pachtende aus den Annahmen, nicht am letzten Jahr der
    # Tabelle: deren Horizont haengt an den Zielen, und die legt man auf der
    # Seite an -- er kann jederzeit vor dem Pachtende enden.
    from finctl import objekte as _objekte
    from finctl.forecast import abgleich as _ag

    enden = [p.bis for p in _objekte.prognosen()]
    if not enden or None in enden:
        pytest.skip("kein Objekt mit Ende der Vermietung")
    ende = max(enden).year
    c = conn()
    try:
        weit = jm.project(c, opening_cents=0, end_year=ende + 1)
    finally:
        c.close()
    danach = next(y for y in weit.years if y.year > ende)
    assert danach.blocks.get(_ag.OBJEKT, 0) == 0


def test_a_backup_can_be_restored_into_an_empty_directory():
    """Ein Backup, das nie zurückgespielt wurde, ist eine Vermutung.

    Geprüft wird die Eigenschaft, auf die es ankommt: aus dem Archiv entsteht
    eine Datenbank mit demselben Inhalt. Was ein zweiter Rechner zusätzlich
    beweist, ist etwas anderes -- ob es läuft, wo nichts eingerichtet ist.
    """
    import sqlite3
    import tempfile

    from finctl import ops
    from finctl.web.server import conn

    c = conn()
    try:
        erwartet = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    finally:
        c.close()

    with tempfile.TemporaryDirectory() as tmp:
        ziel = Path(tmp) / "archiv"
        payload = ops.write_backup(ziel, keep=2)
        archiv = Path(payload["archive"])
        assert archiv.exists()

        # Kein Ballast: Bytecode einer bestimmten Python-Version und
        # Finder-Krimskrams gehoeren nicht in ein Wiederherstellungsarchiv.
        import tarfile
        with tarfile.open(archiv) as tar:
            namen = tar.getnames()
        assert not [n for n in namen if "__pycache__" in n or n.endswith(".DS_Store")]
        # Die eingefrorenen Versionen liegen bei: pyproject nennt nur
        # Untergrenzen, und Hauptversionen brechen.
        assert "requirements.lock" in namen

        wieder = Path(tmp) / "wieder"
        ergebnis = ops.restore(archiv, wieder)
        assert ergebnis["transaktionen"] == erwartet
        assert not ergebnis["fehlend"]

        db = sqlite3.connect(wieder / "data" / "finance.db")
        try:
            assert db.execute("SELECT COUNT(*) FROM splits").fetchone()[0] > 0
        finally:
            db.close()

        # Und niemals in ein bestehendes Verzeichnis: sonst ist der erste
        # Versuch einer Wiederherstellung der Moment, in dem der letzte gute
        # Stand verloren geht.
        with pytest.raises(FileExistsError):
            ops.restore(archiv, wieder)


def test_the_mobile_menu_closes_after_picking_a_page():
    """Wer eine Seite angewählt hat, will sie sehen und nicht das Menü.

    Auf dem Telefon nimmt der aufgeklappte Kopf ein Fünftel des Bildschirms.
    Bei einem Seitenwechsel fällt das nicht auf -- die neue Seite fängt
    zugeklappt an --, bei einem Sprung INNERHALB der Seite schon: vom
    Monatsabschluss auf eine Bestandszeile lädt nichts neu, und das Menü
    stünde offen über dem, was man sehen wollte.

    Und der gemerkte Zustand gehört dem Rechner: der Schalter schreibt auf
    schmalen Schirmen nichts in den Speicher, sonst klappte der Kopf am
    Rechner zu, nur weil man unterwegs einmal getippt hat.
    """
    js = Path("finctl/web/templates/base.html").read_text(encoding="utf-8")
    assert "function navSchmal()" in js

    # Beim Laden gilt der gemerkte Zustand nur ab 700 Pixeln.
    assert "window.innerWidth >= 700 && localStorage.getItem('nav')" in js
    # Der Schalter merkt sich auf dem Telefon nichts.
    laden = js[js.index("function navUmschalten"):]
    laden = laden[:laden.index("\n}")]
    assert "if (navSchmal()) return;" in laden
    # Und ein Link schliesst.
    assert "header .navlinks a" in js
    schliesser = js[js.index("header .navlinks a"):]
    assert "remove('alleoffen')" in schliesser[:400]


def test_the_header_collapses_on_a_phone():
    """Ausgeklappt nahm der Kopf 170 Pixel, bevor die Seite anfing.

    Auf 812 Pixeln Höhe ist das ein Fünftel des Bildschirms für Navigation,
    die man dreimal am Tag braucht. Unter 700 Pixeln Breite bleiben Titel und
    Schalter; der Schalter öffnet dann eine Liste untereinander statt fünf
    schwebender Menüs nebeneinander, für die kein Platz ist.
    """
    css = Path("finctl/web/templates/base.html").read_text(encoding="utf-8")
    i = css.index("@media (max-width: 700px)")
    block = css[i:css.index("\n  }", i)]

    assert "header > .navgruppe{display:none}" in block
    assert "header.alleoffen > .navgruppe" in block
    # Raster mit festen Pixelspalten brechen sonst aus dem Bildschirm:
    # 150 + 140 + 1fr passen nicht in 375 Pixel.
    assert "grid-template-columns:1fr !important" in block

    # Und die Spaltenbreiten vom Rechner gelten dort nicht: eine Tabelle mit
    # 800 Pixeln Desktop-Layout ist auf einem Telefon unlesbar.
    resize = Path("finctl/web/templates/_resize.html").read_text(encoding="utf-8")
    assert "window.innerWidth < 700" in resize
    assert "makeSortable(t);" in resize      # Sortieren bleibt


def test_the_app_refuses_to_listen_on_the_network_without_a_password():
    """Die eine Stelle, an der ein vergessener Schalter alles offenlegt.

    `--host 0.0.0.0` ohne Passwort heisst: jedes Gerät im WLAN liest und
    ändert das vollständige Finanzleben. Ein Hinweis im Handbuch hätte dagegen
    nichts ausgerichtet -- der Start muss sich weigern.
    """
    from typer.testing import CliRunner

    from finctl.cli import app as cli

    ergebnis = CliRunner().invoke(cli, ["serve", "--host", "0.0.0.0"])
    assert ergebnis.exit_code == 1
    ausgabe = ergebnis.output + str(ergebnis.exception or "")
    assert "Passwort" in ausgabe


def test_a_password_gates_the_whole_app_and_a_cookie_keeps_it_open():
    """Eine Anmeldung, kein Benutzerkonto.

    Es gibt genau eine Person; sie soll einmal auf dem Telefon ein Passwort
    eingeben und danach nie wieder. Ohne gesetztes Passwort ändert sich gar
    nichts -- auf localhost wäre die Abfrage nur Reibung.
    """
    import tempfile

    from fastapi.testclient import TestClient

    import finctl.web.server as srv
    from finctl.web import auth

    # NICHT in config/server.yaml: ein Test, der die echte Konfiguration
    # anfasst, laesst irgendwann ein Passwort darin stehen, und dann geht die
    # Seite nicht mehr auf. Genau das ist einmal passiert.
    echt = auth.PFAD
    tmp = tempfile.TemporaryDirectory()
    auth.PFAD = Path(tmp.name) / "server.yaml"
    pfad = auth.PFAD
    try:
        assert TestClient(srv.app).get("/ziele").status_code == 200   # offen

        auth.passwort_setzen("pytest-geheim")
        zu = TestClient(srv.app)
        assert zu.get("/ziele").status_code == 401
        # Die API antwortet mit 401 und nicht mit einer Anmeldeseite: ein
        # Aufruf aus dem Seitenskript soll einen Fehler sehen, kein HTML.
        assert zu.post("/api/ziel", json={"id": "fire"}).status_code == 401
        assert zu.post("/login", data={"passwort": "falsch"}).status_code == 401

        antwort = zu.post("/login", data={"passwort": "pytest-geheim"},
                          follow_redirects=False)
        assert antwort.status_code == 303
        assert auth.COOKIE in antwort.cookies
        assert zu.get("/ziele").status_code == 200

        # Das Passwort selbst steht nirgends in der Datei.
        text = pfad.read_text(encoding="utf-8")
        assert "pytest-geheim" not in text
        assert "passwort_hash" in text and "geheimnis" in text
    finally:
        auth.PFAD = echt
        tmp.cleanup()


def test_a_post_from_a_foreign_page_is_refused():
    """Formulardaten lösen keine Vorabanfrage aus.

    Eine beliebige Webseite im selben Browser konnte einen POST auf
    /api/categorize schicken. Lesen kann sie die Antwort nicht, eine Kategorie
    umbiegen schon. Auf 127.0.0.1 ein Randrisiko -- im Netz keines mehr.
    """
    res = client.post("/api/ziel", json={"id": "fire"},
                      headers={"origin": "http://boese.example"})
    assert res.status_code == 403

    # Ohne Origin-Kopf wird durchgelassen: Kommandozeile und Tests haben
    # keinen, und der Angriff kommt aus einem Browser, der ihn immer setzt.
    from finctl.web import auth

    assert auth.herkunft_passt(None, "127.0.0.1:8765")
    assert auth.herkunft_passt("http://127.0.0.1:8765", "127.0.0.1:8765")
    assert not auth.herkunft_passt("http://boese.example", "127.0.0.1:8765")


def test_the_header_says_where_the_server_listens():
    """„localhost only" als feste Beschriftung wäre nach dem ersten
    `host:`-Wechsel gelogen -- und ausgerechnet hier ist eine falsche
    Beschriftung teuer."""

    import tempfile

    from finctl.web import auth
    from finctl.web.server import _bindung_text

    assert _bindung_text() == "localhost only"

    echt = auth.PFAD
    tmp = tempfile.TemporaryDirectory()
    try:
        auth.PFAD = Path(tmp.name) / "server.yaml"
        auth.PFAD.write_text("host: 192.168.1.42\nport: 8765\n", encoding="utf-8")
        text = _bindung_text()
        assert "192.168.1.42" in text
        # Und ohne Passwort steht es in der Kopfzeile, nicht nur im Log.
        assert "OHNE PASSWORT" in text
    finally:
        auth.PFAD = echt
        tmp.cleanup()

    html = client.get("/ziele").text
    assert "localhost only" in html


def test_each_holding_is_one_row_with_check_value_and_note():
    """Jede Position steht EINMAL auf dem Monatsabschluss.

    Bis zum 26.09.2026 stand sie zweimal -- oben in der Checkliste mit ihrem
    Beleg, unten in den Bestaenden mit Wert und Notiz, jedes Konto dazu als
    Kachel. Jetzt ist es eine Zeile: getippte Staende mit Feld, Konten mit
    Auszug als Text.
    """
    import re
    import sqlite3

    import yaml as _y

    html = client.get("/monatsabschluss").text
    zeilen = re.findall(r'<tr id="pos-([^"]+)"', html)
    assert zeilen, "keine Positionen"
    assert len(zeilen) == len(set(zeilen)), "eine Position steht doppelt"
    assert 'id="bestaende"' not in html

    # Welche Kennungen das sind, sagt die Konfiguration -- hier steht nur die Regel.
    bal = _y.safe_load(Path("config/balances.yaml").read_text(encoding="utf-8"))
    c = sqlite3.connect(DB)
    try:
        geparst = {str(r[0]) for r in c.execute(
            "SELECT id FROM accounts WHERE ingest_mode='parsed'")}
    finally:
        c.close()
    assert geparst <= set(zeilen)

    def zeile(key: str) -> str:
        ab = html.index(f'<tr id="pos-{key}"')
        return html[ab:html.index("</tr>", ab)]

    getippt = [str(r.get("account_id") or r.get("name")) for r in bal["balances"]
               if str(r.get("account_id") or "") not in geparst]
    assert getippt, "keine getippte Position"
    for key in getippt:
        assert 'data-feld="wert"' in zeile(key), key
        assert 'data-feld="notiz"' in zeile(key), key
    for key in geparst:
        assert 'data-feld="wert"' not in zeile(key), key

    # Die angesprungene Zeile hebt sich ab: /ziele verlinkt auf eine Gruppe.
    css = Path("finctl/web/templates/base.html").read_text(encoding="utf-8")
    assert "tr:target" in css


def test_a_new_bracket_is_created_as_plan_or_obligation_in_one_place():
    """Plan und Verpflichtung entstehen im selben Formular am Ende der Listen.

    Vorher stand "Neuer Plan" zwischen Ueberschrift und Liste, und eine
    Verpflichtung liess sich nur ueber "falls neu" im Positionsformular
    anlegen -- zwei Wege fuer eine Sache, einer davon versteckt.
    """
    import yaml

    html = client.get("/planung").text
    assert 'id="nk-art"' in html
    assert 'value="plan"' in html and 'value="pflicht"' in html
    assert 'id="s-name"' not in html

    pfad = Path("config/szenarien.yaml")
    vorher = pfad.read_text(encoding="utf-8")
    try:
        assert client.post("/api/szenario", json={
            "neu": True, "name": "Pytest Pflicht Neu", "pflicht": True}).status_code == 200
        eintrag = next(x for x in yaml.safe_load(pfad.read_text(encoding="utf-8"))["szenarien"]
                       if x["name"] == "Pytest Pflicht Neu")
        assert eintrag.get("pflicht") is True
    finally:
        pfad.write_text(vorher, encoding="utf-8")


def test_the_planning_page_puts_the_brackets_first_and_the_basics_last():
    """Worum es geht, steht oben; was selten angefasst wird, unten.

    Der Abgleich stand vor den Klammern und verdraengte sie. Jetzt:
    Verpflichtungen, Plaene, "Neue Klammer", dann der Abgleich.
    """
    html = client.get("/planung").text
    stellen = [html.index(s) for s in ("<h2>Verpflichtungen</h2>", "<h2>Pläne</h2>",
                                       'id="neue-klammer"', 'id="abgleich"')]
    assert stellen == sorted(stellen)

def test_every_projected_year_opens_to_its_derivation():
    """Eine Jahreszahl ohne Herleitung war nicht pruefbar -- jetzt hat jedes
    Jahr darunter seinen Rechenweg, mit Quelle je Posten."""
    import re

    html = client.get("/hochrechnung").text
    jahre = re.findall(r'<tr class="klappbar" id="jahr-(\d{4})"', html)
    panels = re.findall(r'<tr class="panel rechenweg" id="rw-(\d{4})"', html)
    assert jahre and jahre == panels
    erstes = html[html.index(f'id="rw-{jahre[0]}"'):]
    erstes = erstes[:erstes.index('id="jahr-')]
    assert "gemessen" in erstes and "vertrag" in erstes
    assert '_klappzustand' not in html  # eingebunden, nicht als Text
    assert "klapp:" in html


def test_the_forecast_basis_is_a_section_of_the_assumptions():
    """Dieselbe Frage wie die Seite -- woraus die Extrapolation rechnet --,
    also ein Abschnitt dort und keine eigene Seite."""
    r = client.get("/prognosebasis", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/annahmen#prognosebasis"
    html = client.get("/annahmen").text
    assert '<details class="klapp" id="prognosebasis">' in html
    assert "/konten" in html and "Jahresrechnung" in html
    assert "config/prognosebasis_custom.yaml" in html
    assert 'href="/prognosebasis"' not in html


def test_the_forecast_basis_api_refuses_an_unknown_category():
    r = client.post("/api/prognosebasis", json={"kategorie": "gibt/es-nicht",
                                                "fortschreiben": False})
    assert r.status_code == 400


def test_a_sale_line_is_checked_before_it_is_written():
    """Ein Verkauf ohne bekanntes Objekt, Monat oder Preis wird nicht gespeichert.

    Objekt und Klammer kommen aus der Konfiguration: ein GUELTIGES Objekt ist
    noetig, damit der zweite und dritte Fall wirklich am Monat und am Preis
    scheitern und nicht schon an der Kennung.
    """
    pfad = Path("config/szenarien.yaml")
    objekt, klammer = _ein_objekt(), _pflichtklammer()
    if not klammer:
        pytest.skip("keine Klammer in szenarien.yaml")
    vorher = pfad.read_bytes()
    try:
        for falsch in ({"objekt": "gibt-es-nicht", "start": "2030-07", "amount_cents": 1},
                       {"objekt": objekt, "start": "2030", "amount_cents": 1},
                       {"objekt": objekt, "start": "2030-07", "amount_cents": 0}):
            r = client.post("/api/szenario-zeile", json={
                "szenario": klammer, "art": "verkauf", "label": "Pytest", **falsch})
            assert r.status_code == 400, falsch
        assert pfad.read_bytes() == vorher
    finally:
        pfad.write_bytes(vorher)


def test_the_preview_server_skips_the_login_only_from_this_machine(tmp_path, monkeypatch):
    """`finctl serve --ohne-passwort`: keine Anmeldung, aber nur ueber Loopback."""
    from types import SimpleNamespace

    from finctl.web import server

    monkeypatch.setenv("FINCTL_OHNE_PASSWORT", "1")
    lokal = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    fremd = SimpleNamespace(client=SimpleNamespace(host="192.168.1.20"))
    assert server._ohne_anmeldung(lokal)
    assert not server._ohne_anmeldung(fremd)
    monkeypatch.delenv("FINCTL_OHNE_PASSWORT")
    assert not server._ohne_anmeldung(lokal)


def test_the_no_password_switch_refuses_a_network_host():
    from typer.testing import CliRunner

    from finctl.cli import app as cli

    r = CliRunner().invoke(cli, ["serve", "--host", "0.0.0.0", "--ohne-passwort",
                                 "--no-reload"])
    assert r.exit_code != 0
    assert "--ohne-passwort" in r.output


def test_accounts_with_statements_come_only_from_statements():
    """Wert und Stichtag eines Girokontos sind belegt, nicht getippt.

    Auf /bestaende stand "Auszugsende + Verkaufserloes" als getippter Stand,
    /konten rechnete vom Auszug und /ziele vom getippten Wert. Jetzt: der
    Saldo am Stichtag aus dem Auszug, daneben wie weit der Auszug reicht,
    und kein Feld und keine API, die das ueberschreibt.
    """
    import sqlite3

    import yaml

    from finctl import bestaende as best
    from finctl import ops

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    spec = yaml.safe_load(Path("config/balances.yaml").read_text(encoding="utf-8"))
    db = sqlite3.connect("data/finance.db")
    db.row_factory = sqlite3.Row
    try:
        geparst = best.geparste_konten(db)
        giros = [b for b in spec["balances"] if best.aus_auszug(b, geparst)]
        # Nicht die Art entscheidet, sondern der Parser: auch ein Tagesgeld
        # mit Auszug traegt seinen Stand aus dem Auszug.
        assert {"giro", "tagesgeld"} <= {b.get("kind") for b in giros}
        for b in giros:
            assert "cents" not in b and "as_of" not in b, b["account_id"]
        merged = best.zusammenfuehren(db, spec, {g["account_id"]: {"cents": 1}
                                                  for g in giros})
        stichtag = ops._stichtag(db).isoformat()
        for row in merged["balances"]:
            if not row.get("aus_auszug"):
                continue
            # Ein Overlay aendert nichts, und der Stichtag ist ein Monatsende.
            assert row["cents"] != 1
            assert row["as_of"] == stichtag
            assert row["auszug_bis"] >= stichtag
    finally:
        db.close()

    def zeile_mit_unterzeile(html: str, key: str) -> str:
        """Die Zeile der Position bis zur naechsten."""
        ab = html.index(f'<tr id="pos-{key}"')
        rest = html[ab + 1:]
        weiter = rest.find('<tr id="pos-')
        return rest[:weiter if weiter != -1 else rest.find("</table>")]

    html = client.get("/monatsabschluss").text
    for b in giros:
        zeile = zeile_mit_unterzeile(html, b["account_id"])
        # Kein Feld fuer Wert oder Stichtag -- die kommen aus dem Auszug.
        assert 'data-feld="wert"' not in zeile and 'data-feld="stand"' not in zeile
        assert "Auszug" in zeile
        # Die NOTIZ dagegen ist eine Aussage des Eigentuemers und aenderbar:
        # dass ein Teil des Saldos jemand anderem gehoert, steht in keinem
        # Auszug und darf nicht fuer immer in einer Datei leben.
        assert 'data-feld="notiz"' in zeile
    assert "Auszug bis" in html
    konto = giros[0]["account_id"]
    assert client.post("/api/bestand", json={"key": konto, "cents": 100,
                                             "as_of": "2026-09-30"}).status_code == 400
    assert client.post("/api/bestand", json={"key": konto,
                                             "as_of": "2026-09-30"}).status_code == 400
    try:
        assert client.post("/api/bestand",
                           json={"key": konto, "note": "Teil davon gehört jemandem"}).json()["ok"]
        zeile = zeile_mit_unterzeile(client.get("/monatsabschluss").text, konto)
        assert "Teil davon gehört jemandem" in zeile
        # Und der Wert bleibt der belegte, auch mit eigener Notiz daneben.
        assert 'data-feld="wert"' not in zeile
        # Eine Begruendung ueber mehrere Absaetze bleibt ganz -- mit 200
        # Zeichen schnitt das Speichern sie ab.
        lang = "Erster Absatz. " * 30 + "Ende."
        assert client.post("/api/bestand", json={"key": konto, "note": lang}).json()["ok"]
        html = client.get("/monatsabschluss").text
        assert "Ende.</textarea>" in zeile_mit_unterzeile(html, konto)
    finally:
        client.post("/api/bestand", json={"key": konto, "remove": True})


def test_insurances_page_uses_the_contract_page_without_stock():
    html = client.get("/vertraege?ansicht=versicherungen").text
    assert "<h1>Verträge</h1>" in html
    assert 'href="/vertraege?ansicht=versicherungen" class="on"' in html
    assert 'const ART = "versicherung"' in html
    assert "config/versicherungen_custom.yaml" in html
    assert 'href="/vertraege?ansicht=abos" class="on"' in client.get("/abos").text


def test_both_writers_of_the_balance_overlay_use_one_header():
    """Stand und Verbindlichkeit schreiben dieselbe Datei mit demselben Kopf.

    Mit zwei Koepfen ersetzte jeder Knopf den des anderen, und die Datei
    zeigte Diffs, in denen sich nichts geaendert hatte.
    """
    from finctl.web.routen.konten import OVERLAY_KOPF, bestaende_daten

    pfad = Path("config/balances_custom.yaml")
    vorher = pfad.read_text(encoding="utf-8") if pfad.exists() else None
    try:
        assert client.post("/api/verpflichtung",
                           json={"name": "Testschuld", "cents": 100}).json()["ok"]
        assert pfad.read_text(encoding="utf-8").startswith(OVERLAY_KOPF)
        assert client.post("/api/verpflichtung",
                           json={"kennung": "testschuld", "remove": True}).json()["ok"]
        konto = bestaende_daten()["rows"][0]["key"]
        assert client.post("/api/bestand", json={"key": konto, "note": "x"}).json()["ok"]
        assert pfad.read_text(encoding="utf-8").startswith(OVERLAY_KOPF)
    finally:
        if vorher is None:
            pfad.unlink(missing_ok=True)
        else:
            pfad.write_text(vorher, encoding="utf-8")


def test_categories_are_text_rows_with_one_edit_panel():
    """Wie /regeln: Zeilen als Text, EIN Bearbeitungsbereich (bearbeitungstabellen).

    Vorher trug jede Zeile ein Namensfeld, eine Zielauswahl mit allen
    Kategorien und bis zu drei Knoepfe -- sechzig Zielauswahlen auf einer Seite.
    """
    html = client.get("/kategorien").text
    tabelle = html[html.index('<table class="kategorien">'):html.index("</table>")]
    zeilen = tabelle[tabelle.index("</td></tr>"):]          # hinter dem Bereich
    assert "<input" not in zeilen and "<select" not in zeilen
    assert tabelle.count('id="panel"') == 1
    assert ">Öffnen</button>" in zeilen


def test_rueckblick_has_a_flow_tab_that_balances():
    """Reiter Fluss: ein Diagramm, dessen Rest der Saldo des Jahres ist."""
    import re

    html = client.get("/rueckblick", params={"ansicht": "fluss"}).text
    assert 'href="/rueckblick?ansicht=fluss" class="on"' in html
    assert '<svg class="fluss"' in html
    assert re.search(r'class="(rein|raus) rest"', html), "kein Rest-Knoten"
    # Unter 700 Pixeln dieselben Knoten als Liste.
    assert 'class="fluss-liste"' in html


def test_the_forecast_flow_names_plans_and_loans_as_reasons():
    """Annahmen, Abschnitt Fluss: ein Jahr der Extrapolation nach GRUND.

    Eine Planzeile oder ein Kredit steht mit Namen da und fuehrt dorthin, wo
    man ihn aendert -- die Frage war, aus welchen Gruenden Geld verschwindet.
    """
    html = client.get("/hochrechnung", params={"fluss": "2026"}).text
    abschnitt = html[html.index('<h2 id="fluss">'):]
    abschnitt = abschnitt[:abschnitt.index('class="fluss-liste"')]
    assert '<svg class="fluss"' in abschnitt
    # Mindestens ein Grund aus der Planung oder einem Vertrag, verlinkt.
    assert 'href="/planung#klammer-' in abschnitt or 'href="/kredite"' in abschnitt
    # Ein unbekanntes Jahr faellt auf ein vorhandenes zurueck statt leer.
    assert '<svg class="fluss"' in client.get("/hochrechnung", params={"fluss": "1900"}).text


def test_every_flow_node_leads_to_its_bookings():
    """Ein Klick auf einen Knoten zeigt die Buchungen dahinter -- mit Jahr,
    Konto und genau seinen Kategorien, auch bei "übrige"."""
    import html as _h
    import re

    html = client.get("/rueckblick", params={"ansicht": "fluss"}).text
    svg = html[html.index('<svg class="fluss"'):html.index("</svg>")]
    knoten = re.findall(r'<a href="([^"]+)">\s*<rect[^>]*class="(rein|raus) ?([a-z]*)"', svg)
    assert knoten
    for href, _seite, art in knoten:
        href = _h.unescape(href)
        if art != "rest":
            assert href.startswith("/transactions?category=") and "start=" in href, href
    uebrig = [_h.unescape(h) for h, _s, a in knoten if a == "uebrig"]
    assert all(h.count("category=") > 1 for h in uebrig)

    html = client.get("/hochrechnung").text
    svg = html[html.index('<svg class="fluss"'):html.index("</svg>")]
    assert "/transactions?block=" in _h.unescape(svg)


def test_an_objects_forecast_lives_on_the_object_page():
    """Miete und Kosten eines Objekts stehen an ihm, nicht auf /annahmen.

    Dort standen sie fuer genau ein Objekt; wer zwei hat oder keines, konnte
    damit nichts anfangen.
    """
    from finctl import objekte as _objekte

    annahmen = client.get("/annahmen").text
    assert "Mieteinnahme (Floor)" not in annahmen
    liste = _objekte.prognosen()
    if not liste:
        pytest.skip("kein Objekt mit Prognose")
    p = liste[0]
    assert f'href="/immobilie/{p.id}#prognose"' in annahmen
    seite = client.get(f"/immobilie/{p.id}").text
    assert 'id="prognose"' in seite and 'id="pg-miete"' in seite
    antwort = client.post(f"/api/objekt-prognose/{p.id}", json={"ab": "2026"})
    assert antwort.status_code == 400 and "JJJJ-MM" in antwort.json()["error"]


def test_teilzeit_is_planned_on_the_planning_page():
    """Teilzeit ist ein Plan: angelegt auf /planung, nicht auf /annahmen."""
    import yaml as _y

    from finctl.pfade import CONFIG_DIR

    assert "Teilzeitanteil" not in client.get("/annahmen").text
    pfad = CONFIG_DIR / "szenarien.yaml"
    vorher = pfad.read_text(encoding="utf-8") if pfad.exists() else None
    try:
        r = client.post("/api/szenario-zeile", json={
            "szenario": "Probe Teilzeit", "art": "teilzeit", "label": "halbe Stelle",
            "anteil": 0.5, "start": "2040-07"})
        assert r.status_code == 200, r.text
        spec = _y.safe_load(pfad.read_text(encoding="utf-8"))
        [k] = [s for s in spec["szenarien"] if s["name"] == "Probe Teilzeit"]
        assert k["zeilen"] == [{"label": "halbe Stelle", "art": "teilzeit",
                                "anteil": 0.5, "frequenz": "monatlich",
                                "start": "2040-07"}]
        html = client.get("/planung").text
        assert "Teilzeit · 50 %" in html and "50 % Gehalt ab 2040-07" in html
    finally:
        if vorher is None:
            pfad.unlink(missing_ok=True)
        else:
            pfad.write_text(vorher, encoding="utf-8")


def test_the_setup_asks_who_plans():
    """Geburtsdatum und Krankenversicherung stehen in der Einrichtung; die
    PKV-Frist auf /annahmen rechnet daraus und fehlt bei gesetzlich
    Versicherten."""
    from finctl import person as _person

    seite = client.get("/einrichtung").text
    assert 'id="person"' in seite and 'id="p-geburt"' in seite and 'id="p-kv"' in seite
    antwort = client.post("/api/person", json={"krankenversicherung": "beides"})
    assert antwort.status_code == 400
    annahmen = client.get("/annahmen").text
    assert ("PKV-Entscheidung" in annahmen) == (_person.pkv_stichtag() is not None)


def test_pension_letters_are_kept_current_in_the_monthly_close():
    """Betrag und Stand einer Rente kommen jedes Jahr neu -- gepflegt im
    Monatsabschluss, gelesen in der Hochrechnung."""
    from finctl import renten as _renten

    quellen = _renten.quellen()
    if not quellen:
        pytest.skip("keine Rentenquellen")
    q = quellen[0]
    abschluss = client.get("/monatsabschluss").text
    assert 'id="renten"' in abschluss and f'id="pos-rente-{q.id}"' in abschluss
    assert 'data-api="/api/rente"' in abschluss
    hoch = client.get("/hochrechnung").text
    assert 'id="ruhestand"' in hoch and q.name in hoch
    assert client.post("/api/rente", json={"key": "gibt-es-nicht", "cents": 1}).status_code == 400


def test_the_retirement_gap_is_a_fixed_goal():
    """Gerechnet aus Hochrechnung, Renten und Annahmen -- mit Rechenweg statt
    Formular, und nicht ueber /api/ziel zu aendern."""
    from finctl import person as _person

    html = client.get("/ziele").text
    if _person.rentenbeginn() is None:
        assert 'data-ziel="rentenluecke"' not in html
        return
    karte = html[html.index('data-ziel="rentenluecke"'):]
    ende = karte.find('class="card ziel-karte"')
    karte = karte[:ende if ende > 0 else 3000]
    assert "Rechenweg" in karte and "Lebenserwartung" in karte
    assert 'id="z-form-rentenluecke"' not in html
    antwort = client.post("/api/ziel", json={"id": "rentenluecke", "cents": 1})
    assert antwort.status_code == 400


def test_the_transactions_filter_takes_several_blocks():
    """Mehrere Bloecke sind ODER verknuepft -- das "übrige" der Hochrechnung
    fuehrt zu den Buchungen aller seiner Bloecke."""
    import re

    def zahl(pfad):
        html = client.get(pfad).text
        m = re.search(r"(\d+) Treffer", html)
        return int(m.group(1)) if m else 0

    a = zahl("/transactions?block=steuern&limit=2000")
    b = zahl("/transactions?block=kredit&limit=2000")
    beide = zahl("/transactions?block=steuern&block=kredit&limit=2000")
    assert beide == a + b
    assert zahl("/transactions?block=gibt-es-nicht&limit=2000") >= beide


def test_the_depot_gain_is_saved_and_cleared_like_a_note():
    """Leer heisst "nicht bekannt" -- dann gilt der Wert als Einstand."""
    from finctl.web.routen.konten import _bestand_eintrag

    eintrag = _bestand_eintrag({}, {"cents": 100_000_00, "gewinn_cents": 25_000_00})
    assert eintrag == {"cents": 100_000_00, "gewinn_cents": 25_000_00}
    assert _bestand_eintrag(dict(eintrag), {"gewinn_cents": ""}) == {"cents": 100_000_00}
    assert _bestand_eintrag({}, {"as_of": "2026-01-01"}) is None, "ohne Betrag nichts"


def test_a_partial_year_is_compared_over_the_same_months_only():
    """Neun Monate gegen zwoelf meldeten in jeder Zeile eine Ersparnis."""
    from finctl.web.routen import auswertung as _a

    z = {"n": {"2025": 12, "2026": 2},
         "je_monat": {("2025", "01"): -100_00, ("2025", "02"): -100_00, ("2025", "12"): -900_00,
                      ("2026", "01"): -150_00, ("2026", "02"): -100_00}}
    werte = _a._jahre_rechnen(z, ["2025", "2026"], {"2026": ["01", "02"]})["jahre"]
    assert werte["2025"] == {"cents": -1100_00, "delta": None, "n": 12}
    # Der Dezember 2025 zaehlt nicht: 2026 hat ihn noch nicht.
    assert werte["2026"] == {"cents": -250_00, "delta": -50_00, "n": 2}


def test_the_year_comparison_shows_every_year_without_asking_for_two():
    """Zwei Auswahllisten und ein Anzeigen-Knopf fragten, was eine Tabelle zeigen kann."""
    import re

    html = client.get("/rueckblick?ansicht=vorjahr").text
    tabelle = html[html.index("Kategorie und Subkategorie"):]
    kopf = re.search(r"<tr><th>Position</th>(.*?)</tr>", tabelle, re.S).group(1)
    jahre = re.findall(r'<th class="num"[^>]*>(\d{4})', kopf)
    assert jahre and jahre == sorted(jahre), "jedes Jahr eine Spalte, aelteste zuerst"
    assert '<select name="year"' not in html and '<select name="vs"' not in html
    assert 'aria-label="Basis"' in html


def test_the_running_month_is_never_part_of_the_comparison():
    """Ein Tag Oktober gegen einen ganzen Oktober waere eine Ersparnis, die es nicht gibt."""
    import datetime as dt

    from finctl.web.routen import auswertung as _a

    monate = {"2024": {"06", "07", "08", "09", "10", "11", "12"},
              "2025": {f"{m:02d}" for m in range(1, 13)},
              "2026": {f"{m:02d}" for m in range(1, 11)}}
    gemeinsam = _a._gemeinsame_monate(monate, dt.date(2026, 10, 1))
    assert gemeinsam["2025"] == ["06", "07", "08", "09", "10", "11", "12"]
    assert gemeinsam["2026"] == [f"{m:02d}" for m in range(1, 10)]


def test_every_tile_leads_to_the_list_that_holds_its_number():
    """Einnahmen, Ausgaben und Saldo fuehren in den Vorjahresvergleich, und dort
    steht in der Spalte des Jahres dieselbe Zahl (kachel-fuehrt-zur-liste).

    Eine Kachel, die zu zwei Listen fuehrt, traegt zwei Links statt einem.
    """
    html = client.get("/monatsabschluss").text
    reihe = html[html.index('<div class="cards">'):]
    for karte in re.split(r'(?=<(?:a|div) class="card")', reihe[:reihe.index("\n</div>")])[1:]:
        assert karte.startswith('<a class="card" href=') or "<a " in karte, karte[:80]

    for zeile in ("einnahmen", "ausgaben", "saldo"):
        kachel = re.search(rf'<a class="card" href="([^"]*)#{zeile}"><div class="k">'
                           rf'\w+ (\d{{4}})</div>\s*<div class="v[^"]*">([^<]+)</div>', html)
        assert kachel, zeile
        ziel, jahr, wert = kachel.groups()
        liste = client.get(ziel).text
        jahre = re.findall(r'<th class="num" style="width:140px">(\d{4})', liste)
        if jahr not in jahre:
            assert wert == "0,00 €"
            continue
        tr = liste[liste.index(f'<tr id="{zeile}"'):]
        werte = re.findall(r"<strong>([^<]+)</strong>", tr[:tr.index("</tr>")])
        assert werte[1 + jahre.index(jahr)] == wert, zeile


def test_due_dates_are_listed_with_their_lead_and_go_into_the_calendar():
    """Bald faellig ab dem Vorlauf; jede Frist als Kalendertermin (fristen)."""
    import datetime as dt

    from finctl import fristen

    heute = dt.date.today()
    alle = fristen.alle(heute)
    html = client.get("/monatsabschluss").text
    assert 'href="#fristen"' in html and 'id="fristen"' in html
    for f in alle:
        assert (f"nur={f.uid}" in html) == f.bald(heute), f.uid
    if not alle:
        pytest.skip("keine Fristen")
    antwort = client.get("/fristen.ics")
    assert antwort.status_code == 200
    assert antwort.headers["content-type"].startswith("text/calendar")
    assert antwort.text.count("BEGIN:VEVENT") == len(alle)
    assert client.get(f"/fristen.ics?nur={alle[0].uid}").text.count("BEGIN:VEVENT") == 1
    assert client.get("/fristen.ics?nur=gibt-es-nicht").status_code == 404


def _treffer(html: str) -> int:
    return int(re.search(r"— (\d+) Treffer", html).group(1))


_CHIP = re.compile(r'<a href="(?P<href>[^"]*)" class="(?P<on>on)?"[^>]*>(?P<text>[^<]*)'
                   r'<span class="zahl">(?P<zahl>\d+)</span>'
                   r'|<span class="leer"[^>]*>(?P<leer>[^<]*)'
                   r'<span class="zahl">(?P<null>\d+)</span>')


def _chips(html: str, name: str) -> list[dict]:
    """Die Optionen einer Auswahlzeile: Adresse (None, wenn kein Link), Text, Zahl, gewaehlt."""
    nav = re.search(rf'<nav class="auswahl" aria-label="{name}">(.*?)</nav>', html, re.S).group(1)
    return [{"href": m["href"] and m["href"].replace("&amp;", "&"),
             "text": (m["text"] or m["leer"]).strip(),
             "zahl": int(m["zahl"] or m["null"]), "on": bool(m["on"])}
            for m in _CHIP.finditer(nav)]


def test_each_filter_chip_says_how_many_it_would_leave():
    """Die Zahl am Chip ist die Trefferzahl der Seite, zu der er fuehrt --
    gezaehlt mit den uebrigen Filtern, ohne seinen eigenen (auswahlzeile)."""
    import datetime as dt

    html = client.get(f"/transactions?start={dt.date.today().year - 1}-01-01").text
    for name in ("Konto", "Quelle"):
        chips = _chips(html, name)
        assert len(chips) >= 2, name
        assert chips[0]["zahl"] == _treffer(html), "alle = was ohne diese Wahl gefunden wird"
        for c in chips[1:]:
            if c["href"]:
                assert _treffer(client.get(c["href"]).text) == c["zahl"], c["href"]
    # Mit gewaehltem Konto zaehlt die Kontozeile weiter alle Konten, die
    # Quellenzeile nur noch dieses.
    konto = next(c for c in _chips(html, "Konto")[1:] if c["href"])
    gewaehlt = client.get(konto["href"]).text
    assert [c["zahl"] for c in _chips(gewaehlt, "Konto")] == [
        c["zahl"] for c in _chips(html, "Konto")]
    assert next(c for c in _chips(gewaehlt, "Konto") if c["on"])["zahl"] == konto["zahl"]
    assert _chips(gewaehlt, "Quelle")[0]["zahl"] == konto["zahl"]


def test_a_chip_that_would_find_nothing_is_not_a_link():
    """Und angeboten wird nur, was im Hauptbuch vorkommt."""
    import sqlite3

    html = client.get("/transactions?q=gibt-es-garantiert-nicht-xyz").text
    for name in ("Konto", "Quelle"):
        chips = _chips(html, name)
        assert chips and all(c["zahl"] == 0 and not c["href"] for c in chips if not c["on"]), name
    conn = sqlite3.connect(DB)
    try:
        quellen = {r[0] for r in conn.execute(
            "SELECT DISTINCT source FROM splits WHERE seq = 0 AND source IS NOT NULL")}
    finally:
        conn.close()
    assert {c["text"] for c in _chips(html, "Quelle")[1:]} == quellen
    assert "zurücksetzen" in html
    assert "zurücksetzen" not in client.get("/transactions").text
