"""Rule engine behaviour.

The determinism test is the one that matters most: it is the regression guard
for the no-AI-at-runtime guarantee. If categorizing twice can produce different
splits, the ledger is not reproducible and no report built on it is defensible.
"""

from __future__ import annotations

import pytest

from finctl.rules.engine import RULES_PATH, RuleError, TxContext, load_rules, matches, squash


def ctx(**kw) -> TxContext:
    base = dict(
        id=1, account_id="dkb-giro", booking_date="2026-05-04",
        amount_cents=-66000, raw_text="", counterparty_norm="",
        counterparty_iban=None, tx_type="",
    )
    base.update(kw)
    return TxContext(**base)


# ------------------------------------------------------------------ squash

def test_squash_absorbs_the_pdf_spacing_defect():
    """`Erika Mus termann` and `Erika Mustermann` must match the same rule."""
    assert squash("Erika Mus termann") == squash("Erika Mustermann")
    assert squash("WEG Musterstr. 78-84") == squash("wegmusterstr7884")
    assert squash("Auszahlu ng") == "auszahlung"
    assert squash(None) == ""


def test_squash_is_accent_and_case_insensitive():
    assert squash("Grundstücksverwaltung") == squash("GRUNDSTUECKSVERWALTUNG".replace("UE", "Ü"))
    assert squash("Straße") == squash("STRASSE")


# ----------------------------------------------------------------- matchers

def test_text_matches_through_stray_spaces():
    tx = ctx(raw_text="05.08.2026Basislastschrift -180,00 Erika Mus termann")
    assert matches({"text": "Mustermann"}, tx, {})


def test_text_all_requires_every_needle():
    tx = ctx(raw_text="Stadt Musterstadt 110000000001/Grundsteuer")
    assert matches({"text_all": ["Stadt Musterstadt", "Grundsteuer"]}, tx, {})
    assert not matches({"text_all": ["Stadt Musterstadt", "Hundesteuer"]}, tx, {})


def test_sign_and_amount_window():
    tx = ctx(amount_cents=-8850, raw_text="Stadt Musterstadt Grundsteuer")
    assert matches({"sign": "-", "amount_abs_between": [80, 100]}, tx, {})
    # This is what separates one property's Grundsteuer from another's: same
    # payee, same purpose text, different amount.
    assert not matches({"amount_abs_between": [15, 30]}, tx, {})


def test_iban_is_own():
    own = {"DE10100000000000000101": "giro"}
    assert matches({"iban_is_own": True}, ctx(counterparty_iban="DE10100000000000000101"), own)
    assert not matches({"iban_is_own": True}, ctx(counterparty_iban="DE99999999999999999999"), own)
    assert not matches({"iban_is_own": True}, ctx(counterparty_iban=None), own)


def test_any_of_and_none_of():
    tx = ctx(raw_text="REWE Musterstadt")
    assert matches({"any_of": [{"text": "ALDI"}, {"text": "REWE"}]}, tx, {})
    assert not matches({"none_of": [{"text": "REWE"}]}, tx, {})


def test_unknown_matcher_is_an_error():
    """Silently ignoring a typo'd matcher would make a rule quietly match
    everything, which is worse than refusing to load the rulebook."""
    with pytest.raises(RuleError):
        matches({"txt": "REWE"}, ctx(), {})


# ----------------------------------------------------------------- rulebook

def test_real_rulebook_loads_and_is_ordered():
    rules = load_rules(RULES_PATH)
    assert rules, "rulebook is empty"
    priorities = [r.priority for r in rules]
    assert priorities == sorted(priorities), "rules must evaluate in priority order"


def test_every_rule_sets_something():
    for rule in load_rules(RULES_PATH):
        assert rule.actions or rule.splits, f"{rule.id} sets nothing"


def test_rule_ids_are_unique():
    ids = [r.id for r in load_rules(RULES_PATH)]
    assert len(ids) == len(set(ids))


def test_transfer_rules_outrank_spending_rules():
    """A transfer that also mentions a merchant must be netted, not counted
    as spend. Priority ordering is what guarantees that."""
    rules = {r.id: r for r in load_rules(RULES_PATH)}
    transfer = min(r.priority for r in rules.values() if r.id.startswith("transfer-"))
    spend = rules["supermarkt"].priority
    assert transfer < spend


# ------------------------------------------------- disagreeing with a rule

def test_rule_conflicts_needs_a_prior_rule(tmp_path):
    """An override with no recorded prior is not a disagreement.

    Categorizing something the rulebook never claimed is just work, not
    evidence that a rule is wrong.
    """
    import yaml

    from finctl.rules.categorize import rule_conflicts

    path = tmp_path / "overrides.yaml"
    path.write_text(yaml.safe_dump({"overrides": [
        {"dedup_hash": "a", "parts": [{"mgmt": "konsum/gaming"}]},
    ]}), encoding="utf-8")
    assert rule_conflicts(path) == []


def test_rule_conflicts_counts_repeated_disagreement(tmp_path):
    import yaml

    from finctl.rules.categorize import rule_conflicts

    path = tmp_path / "overrides.yaml"
    path.write_text(yaml.safe_dump({"overrides": [
        {"dedup_hash": h, "when": "2026-03-01", "text": "DB Vertrieb",
         "was": {"rule": "bahn-abo", "category": "mobilitaet/bahn-beruflich"},
         "parts": [{"mgmt": "mobilitaet/bahn-privat"}]}
        for h in ("a", "b", "c")
    ]}), encoding="utf-8")

    out = rule_conflicts(path)
    assert len(out) == 1
    assert out[0]["rule"] == "bahn-abo"
    assert out[0]["count"] == 3
    assert out[0]["most_common"] == "mobilitaet/bahn-privat"
    assert out[0]["unanimous"] is True


def test_a_split_is_not_a_disagreement(tmp_path):
    """Breaking one payment into parts says nothing about the rule's category."""
    import yaml

    from finctl.rules.categorize import rule_conflicts

    path = tmp_path / "overrides.yaml"
    path.write_text(yaml.safe_dump({"overrides": [
        {"dedup_hash": "a",
         "was": {"rule": "paypal", "category": "konsum/sonstiges"},
         "parts": [{"mgmt": "abo/ki"}, {"mgmt": "abo/office"}]},
    ]}), encoding="utf-8")
    assert rule_conflicts(path) == []


def test_correcting_to_the_same_category_is_not_a_conflict(tmp_path):
    import yaml

    from finctl.rules.categorize import rule_conflicts

    path = tmp_path / "overrides.yaml"
    path.write_text(yaml.safe_dump({"overrides": [
        {"dedup_hash": "a",
         "was": {"rule": "supermarkt", "category": "lebensmittel/supermarkt"},
         "parts": [{"mgmt": "lebensmittel/supermarkt"}]},
    ]}), encoding="utf-8")
    assert rule_conflicts(path) == []


# ------------------------------------------------------- category lifecycle

def test_redeclaring_a_category_revives_it(tmp_path):
    """A retired category that is declared again must come back.

    Without this the ON CONFLICT path updated the name but left active=0, so
    any split migrated into it was silently orphaned -- which is exactly what
    happened when two Nebenkosten categories were merged into one.
    """

    from finctl.ledger import db as ledger
    from finctl.tax import taxonomy as tx

    db_path = tmp_path / "t.db"
    ledger.init_db(db_path)
    conn = ledger.connect(db_path)

    spec = tmp_path / "tax.yaml"
    spec.write_text(
        "management:\n  wohnen:\n    name: Wohnen\n    kind: expense\n"
        "    children:\n      miete: Miete\n", encoding="utf-8")
    tx.load_into_db(conn, spec)
    conn.execute("UPDATE mgmt_categories SET active = 0 WHERE id = 'wohnen/miete'")
    conn.commit()
    assert conn.execute(
        "SELECT active FROM mgmt_categories WHERE id='wohnen/miete'").fetchone()[0] == 0

    tx.load_into_db(conn, spec)
    assert conn.execute(
        "SELECT active FROM mgmt_categories WHERE id='wohnen/miete'").fetchone()[0] == 1
    conn.close()


def test_orphaned_splits_are_detected(tmp_path):
    """Splits on a retired category must fail validation, not pass quietly.

    They keep their amount in the ledger while vanishing from every report
    that joins on the category, so nothing looks wrong while the totals stop
    adding up.
    """
    from finctl.ledger import db as ledger

    db_path = tmp_path / "t.db"
    ledger.init_db(db_path)
    conn = ledger.connect(db_path)
    conn.executescript("""
        INSERT INTO accounts (id, display_name, institution, account_type,
                              ingest_mode) VALUES ('a','A','X','giro','summary');
        INSERT INTO statements (account_id, source_path, source_name, file_sha256,
            period_start, period_end, balance_start_cents, balance_end_cents,
            parser_profile, parser_version, status, imported_at)
            VALUES ('a','p','p','h','2026-01-01','2026-01-31',0,0,'x','1','imported','n');
        INSERT INTO mgmt_categories (id, parent_id, name, kind, active)
            VALUES ('dead', NULL, 'Dead', 'expense', 0);
        INSERT INTO transactions (account_id, statement_id, booking_date, amount_cents,
            raw_text, seq_in_statement, dedup_hash, created_at)
            VALUES ('a',1,'2026-01-05',-100,'x',0,'h1','n');
        INSERT INTO splits (transaction_id, seq, amount_cents, mgmt_category_id,
            source, created_at, updated_at) VALUES (1,0,-100,'dead','manual','n','n');
    """)
    conn.commit()
    assert len(ledger.orphaned_splits(conn)) == 1
    conn.close()


def test_repoint_rule_preserves_comments(tmp_path):
    """rules.yaml carries the reasoning for each rule in comments.

    A YAML round-trip would delete every one of them, so the edit is a
    targeted text replacement and this test is what keeps it that way.
    """
    from finctl.rules.engine import repoint_rule

    path = tmp_path / "rules.yaml"
    path.write_text(
        "rules:\n"
        "  # This comment explains why the rule exists.\n"
        "  - id: alpha\n"
        "    priority: 10\n"
        "    match: {text: [\"X\"]}\n"
        "    set: {mgmt: old/one, tax: privat/nicht-abzugsfaehig}\n"
        "\n"
        "  # A second rule, untouched.\n"
        "  - id: beta\n"
        "    priority: 20\n"
        "    match: {text: [\"Y\"]}\n"
        "    set: {mgmt: other/two}\n",
        encoding="utf-8")

    assert repoint_rule("alpha", "mgmt", "new/one", path) is True
    out = path.read_text(encoding="utf-8")
    assert "mgmt: new/one" in out
    assert "This comment explains why the rule exists." in out
    assert "A second rule, untouched." in out
    assert "mgmt: other/two" in out          # beta is not touched
    assert "tax: privat/nicht-abzugsfaehig" in out


def test_repoint_rule_reports_a_miss(tmp_path):
    from finctl.rules.engine import repoint_rule

    path = tmp_path / "rules.yaml"
    path.write_text("rules:\n  - id: alpha\n    set: {tax: privat/x}\n", encoding="utf-8")
    assert repoint_rule("nope", "mgmt", "a/b", path) is False   # no such rule
    assert repoint_rule("alpha", "mgmt", "a/b", path) is False  # no mgmt action


def test_a_category_can_imply_its_tax_position(tmp_path):
    """Choosing "Parken beruflich" must not also require remembering
    "Anlage N / Fahrtkosten".

    Business and private parking look identical on a statement; only the
    category distinguishes them. Making the tax treatment follow that choice
    is what stops a deduction being lost to a forgotten second field.
    """
    from finctl.ledger import db as ledger
    from finctl.tax import taxonomy as tx

    db_path = tmp_path / "t.db"
    ledger.init_db(db_path)
    conn = ledger.connect(db_path)

    spec = tmp_path / "tax.yaml"
    spec.write_text(
        "management:\n"
        "  mobilitaet:\n    name: Mobilität\n    kind: expense\n    children:\n"
        "      parken: Parken\n"
        "      parken-beruflich: {name: Parken beruflich, tax: anlage_n/fahrtkosten}\n",
        encoding="utf-8")
    tx.load_into_db(conn, spec)

    rows = dict(conn.execute(
        "SELECT id, default_tax_id FROM mgmt_categories WHERE parent_id IS NOT NULL"
    ).fetchall())
    assert rows["mobilitaet/parken"] is None
    assert rows["mobilitaet/parken-beruflich"] == "anlage_n/fahrtkosten"
    conn.close()


def test_an_insurers_share_is_not_an_insurance_premium():
    """Allianz sells policies and is also a listed company.

    The insurance rule caught a SALE of Allianz shares and booked 612,30 of
    proceeds as a Haftpflicht premium -- an inflow recorded as insurance cost,
    with a Vorsorgeaufwand tax position on it.

    The guard tests for securities vocabulary rather than for the account: a
    depot can live anywhere, but "Handel", "trade" and an ISIN never appear on
    a premium notice.
    """
    from finctl.rules.engine import TxContext, matches

    rule = _eigene_regel("allianz")

    def hits(text):
        tx = TxContext(id=1, account_id="a", booking_date="2025-08-18",
                       amount_cents=61230, raw_text=text, counterparty_norm="",
                       counterparty_iban=None, tx_type="")
        return matches(rule.match, tx, {})

    assert hits("Basislastschrift Allianz Versicherungs-AG Beitrag")
    assert not hits("Handel Sell trade DE0001234567 VERSICHERUNG AG VNA O.N.")


def test_trade_republic_crypto_isins_are_taxed_under_paragraph_23():
    """A crypto sale inside a year is a taxable private disposal.

    Trade Republic trades crypto under its own XF000 ISINs. Without a rule they
    land under Wertpapier and are marked privat/nicht-abzugsfähig -- five figures of
    proceeds booked so that nobody would ever check the holding period.
    """
    from finctl.rules.engine import TxContext, matches

    rule = _eigene_regel("krypto-traderepublic")
    assert rule.actions["mgmt"] == "investment/krypto"
    assert rule.actions["tax"] == "paragraf_23/krypto"

    tx = TxContext(id=1, account_id="trade-republic", booking_date="2025-08-21",
                   amount_cents=1034550,
                   raw_text="Handel Sell trade XF000BTC0017 Bitcoin, quantity: 0.1",
                   counterparty_norm="", counterparty_iban=None, tx_type="")
    assert matches(rule.match, tx, {})


def test_a_rule_that_still_sets_tags_is_refused():
    """Tags gibt es nicht mehr. Eine alte Regel mit `tags` soll laut scheitern,
    statt still ohne Wirkung weiterzulaufen."""
    from finctl.rules.engine import RuleError, regeln_aus

    with pytest.raises(RuleError, match="Tags gibt es nicht mehr"):
        regeln_aus([{"id": "alt", "match": {"text": ["x"]},
                     "set": {"mgmt": "konsum/sonstiges", "tags": ["reise"]}}])


def test_no_rule_in_the_rulebook_sets_tags():
    from finctl.rules.engine import load_rules

    assert not [r.id for r in load_rules() if "tags" in r.actions]


def test_a_three_letter_vendor_rule_does_not_swallow_unrelated_rows():
    """"RTL" also appears inside a Coinbase reference, RTL-Z4UEF2K2.

    A rule that collects half the crypto history is worse than none, so the
    match is on "RTL interactive" rather than on three letters.
    """
    from finctl.rules.engine import TxContext, matches

    rule = _eigene_regel("video-rtl")

    def hits(text):
        tx = TxContext(id=1, account_id="a", booking_date="2025-09-15",
                       amount_cents=-899, raw_text=text, counterparty_norm="",
                       counterparty_iban=None, tx_type="")
        return matches(rule.match, tx, {})

    assert hits("Online-Kartenzahlung 8,99 RTL interactive GmbH")
    assert not hits("Eingang Echtzeitübw. 58,17 Coinbase Ireland Limited RTL-Z4UEF2K2")


def test_concurrent_overrides_do_not_lose_each_other(tmp_path):
    """Recording an override rewrites the whole file, so writers must serialise.

    The real configuration has two writers by construction: the dashboard runs
    as a long-lived server while finctl is used from the shell. Without a lock
    the second writer's read predates the first writer's write and one decision
    vanishes -- which is not a thought experiment, it happened to the 8.000
    Studiengebühren transfer and was only caught because `validate` compares
    the ledger against this file.

    Threads rather than processes: flock is per open file description, so two
    open() calls in one process contend exactly as two processes would.
    """
    import threading

    import yaml

    from finctl.ledger import db as ledger
    from finctl.rules.categorize import record_override

    db_path = tmp_path / "t.db"
    ledger.init_db(db_path)
    conn = ledger.connect(db_path)
    conn.executescript("""
        INSERT INTO accounts (id, display_name, institution, account_type,
                              ingest_mode) VALUES ('a','A','X','giro','summary');
        INSERT INTO mgmt_categories (id, parent_id, name, kind, active)
            VALUES ('konsum', NULL, 'Konsum', 'expense', 1);
        INSERT INTO mgmt_categories (id, parent_id, name, kind, active)
            VALUES ('konsum/gaming', 'konsum', 'Gaming', 'expense', 1);
        INSERT INTO statements (id, account_id, source_path, source_name,
            file_sha256, period_start, period_end, balance_start_cents,
            balance_end_cents, parser_profile, parser_version, status,
            imported_at)
            VALUES (1,'a','p','p','h','2026-01-01','2026-01-31',0,0,'x','1',
                    'imported','n');
    """)
    n = 24
    for i in range(n):
        conn.execute(
            "INSERT INTO transactions (id, statement_id, seq_in_statement,"
            " account_id, booking_date, amount_cents, raw_text, dedup_hash,"
            " created_at) VALUES (?,1,?,?,?,?,?,?,'n')",
            (i + 1, i, "a", "2026-01-01", -100 - i, f"tx {i}", f"hash{i:03d}"))
        conn.execute(
            "INSERT INTO splits (transaction_id, seq, amount_cents,"
            " mgmt_category_id, source, created_at, updated_at)"
            " VALUES (?,0,?,?, 'manual','n','n')",
            (i + 1, -100 - i, "konsum/gaming"))
    conn.commit()

    path = tmp_path / "overrides.yaml"
    ready = threading.Barrier(n)

    def write(i: int) -> None:
        # Each thread gets its own connection: sqlite objects are not shared
        # across threads, and the contention under test is on the file anyway.
        c = ledger.connect(db_path)
        try:
            ready.wait()
            record_override(c, i + 1, path=path)
        finally:
            c.close()

    threads = [threading.Thread(target=write, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    written = yaml.safe_load(path.read_text(encoding="utf-8"))["overrides"]
    assert sorted(e["dedup_hash"] for e in written) == [f"hash{i:03d}" for i in range(n)]


def _eigene_regel(kennung: str):
    """Eine bestimmte Regel aus der eigenen `rules.yaml` -- oder ueberspringen.

    Diese Tests halten fest, warum EINE Regel so aussieht, wie sie aussieht.
    In einem anderen Regelsatz, etwa dem Musterhaushalt, gibt es sie nicht,
    und dann gibt es auch nichts zu pruefen.
    """
    from finctl.rules.engine import load_rules

    regel = next((r for r in load_rules() if r.id == kennung), None)
    if regel is None:
        pytest.skip(f"keine Regel {kennung} in rules.yaml")
    return regel


def _geteilte_regeln():
    """Regeln, die eine Zahlung in feste Bestandteile zerlegen.

    Gesucht ueber die FORM, nicht ueber die Kennung: mindestens drei Teile,
    die vorderen mit festem Betrag, der letzte als Rest. Vorher standen hier
    die Kennung einer bestimmten Regel und der Name der Person, von der die
    Zahlung kommt. Beides steht jetzt nur noch in `config/rules.yaml`.
    """
    from finctl.rules import engine

    return [r for r in engine.load_rules()
            if len(r.splits or []) >= 3
            and all("amount" in teil for teil in r.splits[:-1])
            and ((r.match or {}).get("text") or [])
            and ((r.match or {}).get("amount_between") or [])]


def test_a_shared_cost_payment_is_split_by_component():
    """Zwei Zahlungen mit demselben Verwendungszweck, nur der Betrag
    unterscheidet sie -- die groessere enthaelt einen Mietanteil, die kleinere
    nicht.

    Ungeteilt gebucht sagen sie, WAS jemand gezahlt hat, und nichts darueber,
    wofuer: jede Wohnkostenzeile liest dann brutto. Deshalb zwei Regeln statt
    einer mit Spanne -- beim kleineren Betrag bleibt kein Rest fuer die Miete,
    und ein Teil ueber 0,00 behauptete etwas, das nicht stattgefunden hat.

    Erwartung UND Wirklichkeit kommen aus der Konfiguration: was die Regel als
    Teilbetraege nennt, muss im Hauptbuch so stehen.
    """
    import sqlite3
    from pathlib import Path

    regeln = _geteilte_regeln()
    assert regeln, "keine Regel mit festen Teilbetraegen in rules.yaml"

    db = Path("data/finance.db")
    if not db.exists():
        pytest.skip("no ledger present")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    geprueft = 0
    try:
        for rule in regeln:
            betrag = int(round(float(rule.match["amount_between"][0]) * 100))
            wie = " OR ".join(["t.raw_text LIKE ?"] * len(rule.match["text"]))
            args = [f"%{s}%" for s in rule.match["text"]]

            whole = conn.execute(f"""
                SELECT COUNT(*) FROM transactions t
                WHERE  t.amount_cents = ? AND ({wie})
                  AND  (SELECT COUNT(*) FROM splits s
                        WHERE s.transaction_id = t.id) = 1
            """, [betrag, *args]).fetchone()[0]
            assert whole == 0, \
                f"{rule.id}: {whole} Zahlungen stehen ungeteilt im Ledger"

            erwartet = {(teil["mgmt"], int(round(float(teil["amount"]) * 100)))
                        for teil in rule.splits[:-1]}
            # Eine konkrete Zahlung, dann ALLE ihre Teile. Nicht die ersten
            # drei nach `seq`: in welcher Reihenfolge die Teile in der Tabelle
            # stehen, ist nicht zugesichert, und ein Test, der sich darauf
            # verlaesst, prueft die Sortierung statt der Aufteilung.
            zeile = conn.execute(f"""
                SELECT t.id FROM transactions t
                WHERE  t.amount_cents = ? AND ({wie})
                ORDER  BY t.booking_date DESC LIMIT 1
            """, [betrag, *args]).fetchone()
            if not zeile:
                continue
            parts = {(r["cat"], r["cents"]) for r in conn.execute("""
                SELECT s.mgmt_category_id AS cat, s.amount_cents AS cents
                FROM   splits s WHERE s.transaction_id = ?
            """, (zeile["id"],))}
            fehlt = erwartet - parts
            assert not fehlt, f"{rule.id}: {sorted(fehlt)} fehlen, da steht {sorted(parts)}"
            geprueft += 1
    finally:
        conn.close()
    assert geprueft, "keine der Regeln hat im Hauptbuch eine Zahlung getroffen"


def test_both_spellings_of_one_counterparty_match():
    """`squash` entfernt Leerzeichen, nicht nur Satzzeichen.

    Eine Regel auf die kuerzere von zwei Schreibweisen zu verkuerzen erschien
    sauberer und liess die laengere durchfallen -- aus 14 unzugeordneten
    Buchungen wurden 26. Beide Schreibweisen stehen deshalb einzeln in der
    Regel, und dieser Test haelt fest, warum das keine Redundanz ist.

    Geprueft werden ALLE Regeln, bei denen eine Schreibweise in einer anderen
    steckt -- genau die Falle. Vorher war es eine, und ihr Name stand hier.
    """
    import re
    import sqlite3
    from pathlib import Path

    from finctl.rules import engine

    def squash(wert: str) -> str:
        return re.sub(r"[^a-z0-9]", "", wert.lower())

    gefaehrdet = []
    for rule in engine.load_rules():
        texte = [x for x in ((rule.match or {}).get("text") or [])
                 if isinstance(x, str)]
        for kurz in texte:
            if any(kurz != lang and squash(kurz) in squash(lang) for lang in texte):
                gefaehrdet.extend(texte)
                break
    assert gefaehrdet, "keine Regel mit verschachtelten Schreibweisen"

    db = Path("data/finance.db")
    if not db.exists():
        pytest.skip("no ledger present")
    conn = sqlite3.connect(db)
    try:
        for schreibweise in sorted(set(gefaehrdet)):
            offen = conn.execute("""
                SELECT COUNT(*) FROM transactions t
                JOIN   splits s ON s.transaction_id = t.id AND s.seq = 0
                WHERE  t.raw_text LIKE ? AND t.amount_cents > 0
                  AND  s.mgmt_category_id IS NULL
            """, (f"%{schreibweise}%",)).fetchone()[0]
            assert offen == 0, \
                f"{offen} Buchungen der Schreibweise {schreibweise!r} unzugeordnet"
    finally:
        conn.close()


def test_a_rule_can_require_that_another_account_covers_the_date():
    """Die PayPal-Belastung ist nur eine Umbuchung, wo PayPal eingelesen ist.

    Früher standen dafür feste Datumsgrenzen in rules.yaml, die bei jedem
    Export von Hand nachgezogen werden mussten -- bei keinem anderen Konto
    musste man das.
    """
    from finctl.rules import engine

    def tx(datum):
        return engine.TxContext(id=1, account_id="c24", booking_date=datum,
                                amount_cents=-3300, raw_text="PayPal Europe",
                                counterparty_norm="paypaleurope",
                                counterparty_iban=None, tx_type="")

    abdeckung = {"paypal": [("2024-01-01", "2024-12-31"), ("2025-01-01", "2026-08-31")]}
    regel = {"text": ["PayPal"], "covered_by": "paypal"}
    assert engine.matches(regel, tx("2026-08-31"), {}, abdeckung)
    assert not engine.matches(regel, tx("2026-09-02"), {}, abdeckung)
    assert not engine.matches(regel, tx("2025-05-01"), {}, {})
