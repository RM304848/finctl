"""Abos: Termin statt Median, zugeordnete Buchungen, geteilt und eingesammelt."""

import sqlite3
from datetime import date

import pytest

from finctl import abos
from finctl.forecast.engine import derive_recurring


def _abo(**extra):
    basis = {"id": "x", "name": "X", "kategorie": "abo/office", "konto": "a",
             "betrag_cents": -9900, "takt": 12, "faellig": "2027-04-05"}
    basis.update(extra)
    return abos.normalisieren(basis)


def _db(*buchungen):
    """(Datum, Cent, Name, Kategorie[, Hash])."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, account_id TEXT,
            booking_date TEXT, amount_cents INTEGER, counterparty TEXT,
            counterparty_norm TEXT, raw_text TEXT, dedup_hash TEXT);
        CREATE TABLE splits (transaction_id INTEGER, seq INTEGER DEFAULT 0,
            mgmt_category_id TEXT, amount_cents INTEGER);
    """)
    for n, zeile in enumerate(buchungen, start=1):
        wann, cents, name, kat = zeile[:4]
        h = zeile[4] if len(zeile) > 4 else f"h{n}"
        norm = "".join(ch for ch in name.lower() if ch.isalnum())
        conn.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
                     (n, "paypal", wann, cents, name, norm, f"Zahlung {name}", h))
        conn.execute("INSERT INTO splits VALUES (?,0,?,?)", (n, kat, cents))
    return conn


def _geteilt(*zeitraeume):
    return {"besetzung": [], "zeitraeume": list(zeitraeume)}


def _zr(von, bis, einsammeln, *personen):
    return {"von": von, "bis": bis, "einsammeln": einsammeln,
            "personen": [{"name": n, "anteil_cents": c} for n, c in personen]}


# ------------------------------------------------------------------ Termin

def test_a_declared_subscription_lands_on_its_due_month_not_as_a_twelfth():
    termine = abos.posten([_abo(faellig="2027-04-13", betrag_cents=-10900)], "a",
                          ab=date(2026, 9, 1), bis=date(2027, 12, 31))
    assert [(t["faellig"], t["betrag_cents"]) for t in termine] == [
        (date(2027, 4, 13), -10900)]


def test_nothing_is_due_after_the_cancellation():
    termine = abos.posten([_abo(takt=1, faellig="2026-10-01",
                                gekuendigt_zum="2026-12-31")], "a",
                          ab=date(2026, 9, 1), bis=date(2027, 6, 30))
    assert [t["faellig"] for t in termine] == [
        date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)]


def test_a_prepaid_stock_charges_nothing_before_it_runs_out():
    termine = abos.posten([_abo(faellig="2028-04-05", vorrat_bis="2028-04-05")], "a",
                          ab=date(2026, 9, 1), bis=date(2028, 12, 31))
    assert [t["faellig"] for t in termine] == [date(2028, 4, 5)]


# ------------------------------------------------------ zugeordnete Buchungen

def test_only_the_assigned_booking_is_replaced_not_the_rest_of_amazon():
    """Einkauf und Abo unter demselben Namen: nur das Abo geht.

    Gleiche Gegenpartei, gleiche Kategorie -- die Reihe allein trennt die
    beiden nicht. Der Vertragsbetrag trennt sie: der Einkauf ist um ein
    Vielfaches daneben und bleibt deshalb im Median stehen.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE mgmt_categories (id TEXT PRIMARY KEY, kind TEXT,
                                      fixkosten INTEGER DEFAULT 0);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, account_id TEXT,
                                   booking_date TEXT, dedup_hash TEXT,
                                   counterparty_norm TEXT);
        CREATE TABLE splits (transaction_id INTEGER, mgmt_category_id TEXT,
                             amount_cents INTEGER);
        INSERT INTO mgmt_categories VALUES ('konsum/sonstiges', NULL, 0);
    """)
    for n, monat in enumerate(("01", "02", "03", "04", "05", "06"), start=1):
        conn.execute("INSERT INTO transactions VALUES (?, 'a', ?, ?, 'haendler')",
                     (n, f"2026-{monat}-10", f"einkauf{n}"))
        conn.execute("INSERT INTO splits VALUES (?, 'konsum/sonstiges', -2500)", (n,))
    conn.execute("INSERT INTO transactions VALUES "
                 "(99, 'a', '2026-02-14', 'prime', 'haendler')")
    conn.execute("INSERT INTO splits VALUES (99, 'konsum/sonstiges', -8990)")

    prime = _abo(kategorie="konsum/sonstiges", betrag_cents=-8990, buchungen=["prime"])
    items = derive_recurring(conn, "a", since="2026-01-01", escalation_pa=0.0,
                             ohne=abos.ausschluesse(conn, [prime]))
    assert [i.amount_cents for i in items] == [-2500]


def test_one_ticked_booking_takes_the_whole_contract_out_of_the_median():
    """Ein Haken ist ein Beispiel, keine Inventur.

    Der Ausschluss ging frueher genau ueber die angehakten Buchungen. Ein
    Vertrag, von dem nur eine Handvoll Buchungen erklaert war, liess deshalb
    den ganzen Rest im Median stehen und buchte seinen Termin zusaetzlich:
    derselbe Beitrag zweimal, Monat fuer Monat, ohne dass eine Seite es sagte.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE mgmt_categories (id TEXT PRIMARY KEY, kind TEXT,
                                      fixkosten INTEGER DEFAULT 0);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, account_id TEXT,
                                   booking_date TEXT, dedup_hash TEXT,
                                   counterparty_norm TEXT);
        CREATE TABLE splits (transaction_id INTEGER, mgmt_category_id TEXT,
                             amount_cents INTEGER);
        INSERT INTO mgmt_categories VALUES ('versicherung/pkv', NULL, 1);
    """)
    # Sechs Monatsbeitraege, der Beitrag steigt unterwegs -- angehakt ist nur
    # der letzte. Dazu eine Erstattung, die echtes Geld ist und bleibt.
    for n, (monat, cents) in enumerate(zip(
            ("01", "02", "03", "04", "05", "06"),
            (-41000, -41000, -44400, -44400, -44400, -44400), strict=True), start=1):
        conn.execute("INSERT INTO transactions VALUES (?, 'a', ?, ?, 'kasse')",
                     (n, f"2026-{monat}-01", f"beitrag{n}"))
        conn.execute("INSERT INTO splits VALUES (?, 'versicherung/pkv', ?)", (n, cents))
    conn.execute("INSERT INTO transactions VALUES "
                 "(90, 'a', '2026-03-18', 'erstattung', 'kasse')")
    conn.execute("INSERT INTO splits VALUES (90, 'versicherung/pkv', 104500)")

    pkv = _abo(kategorie="versicherung/pkv", betrag_cents=-44400,
               buchungen=["beitrag6"])
    items = derive_recurring(conn, "a", since="2026-01-01", escalation_pa=0.0,
                             ohne=abos.ausschluesse(conn, [pkv]))
    # Kein Beitrag mehr im Median -- der Vertrag bucht ihn ja mit Termin.
    # Die Erstattung steht noch da, und das ist richtig.
    assert [i.amount_cents for i in items] == [104500]


def test_a_transfer_to_a_pass_through_account_is_spending():
    """Geld auf PayPal ist weg; Geld auf dem Tagesgeld nicht."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE mgmt_categories (id TEXT PRIMARY KEY, kind TEXT,
                                      fixkosten INTEGER DEFAULT 0);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, account_id TEXT,
                                   booking_date TEXT);
        CREATE TABLE splits (transaction_id INTEGER, mgmt_category_id TEXT,
                             amount_cents INTEGER, transfer_account_id TEXT);
        INSERT INTO mgmt_categories VALUES ('transfer/eigenkonto', 'transfer', 0);
    """)
    for n, monat in enumerate(("01", "02", "03", "04", "05", "06"), start=1):
        conn.execute("INSERT INTO transactions VALUES (?, 'a', ?)", (n, f"2026-{monat}-10"))
        conn.execute("INSERT INTO splits VALUES (?, 'transfer/eigenkonto', -4000, 'paypal')", (n,))
        conn.execute("INSERT INTO transactions VALUES (?, 'a', ?)", (n + 100, f"2026-{monat}-12"))
        conn.execute("INSERT INTO splits VALUES (?, 'transfer/eigenkonto', -50000, 'scalable')",
                     (n + 100,))
    items = derive_recurring(conn, "a", since="2026-01-01", escalation_pa=0.0,
                             durchlaufend={"paypal"})
    assert [i.amount_cents for i in items] == [-4000]


def test_the_next_renewal_is_suggested_once_it_is_imported():
    """Nur dieselbe Gegenpartei in DERSELBEN Kategorie, und nur Neueres.

    Unter Apple laufen Crunchyroll, iCloud und Claude; iCloud ist keine
    Crunchyroll-Verlängerung, und ein Beleg von vor dem jüngsten auch nicht.
    """
    conn = _db(("2025-04-14", -9999, "Apple Bill", "abo/video-streaming", "vorjahr"),
               ("2026-04-14", -10900, "Apple Bill", "abo/video-streaming", "alt"),
               ("2027-04-13", -10900, "Apple Bill", "abo/video-streaming", "neu"),
               ("2027-04-20", -299, "Apple Bill", "abo/cloud", "icloud"),
               ("2027-04-13", -2500, "Rewe", "lebensmittel", "rewe"))
    abo = _abo(kategorie="abo/video-streaming", buchungen=["alt"])
    assert [b["dedup_hash"] for b in abos.vorschlaege(conn, abo)] == ["neu"]


def test_earlier_bookings_are_offered_so_they_stop_counting_twice():
    """Nur aeltere derselben Gegenpartei und Kategorie, ohne Ignorierte."""
    conn = _db(("2026-06-14", -299, "Apple Bill", "abo/cloud", "juni"),
               ("2026-07-14", -299, "Apple Bill", "abo/cloud", "juli"),
               ("2026-05-14", -299, "Apple Bill", "abo/cloud", "mai"),
               ("2026-07-14", -2200, "Apple Bill", "abo/ki", "ki"),
               ("2026-08-14", -299, "Apple Bill", "abo/cloud", "aug"),
               ("2026-09-14", -299, "Apple Bill", "abo/cloud", "sep"))
    abo = _abo(kategorie="abo/cloud", buchungen=["aug"], ignoriert=["mai"])
    assert [b["dedup_hash"] for b in abos.fruehere(conn, abo)] == ["juli", "juni"]


def test_the_latest_assigned_booking_checks_the_amount():
    """Ein Kauf, der noch nicht im Auszug steht, bleibt so sichtbar."""
    conn = _db(("2026-04-05", -9900, "Microsoft Payments", "abo/office", "paypal2026"))
    m365 = _abo(betrag_cents=-8924, buchungen=["paypal2026"])
    assert abos.preisabweichung(conn, m365) == {
        "erwartet": -8924, "gebucht": -9900, "datum": "2026-04-05"}
    conn.execute("INSERT INTO transactions VALUES (9, 'trade-republic', '2026-09-14', "
                 "-8924, 'Amazon', 'amazon', 'Amazon', 'amazon2026')")
    conn.execute("INSERT INTO splits VALUES (9, 0, 'abo/office', -8924)")
    m365["buchungen"].append("amazon2026")
    assert abos.preisabweichung(conn, m365) is None


# ------------------------------------------------------------------ geteilt

def test_repayments_are_matched_by_name_and_close_the_period_by_themselves():
    conn = _db(("2026-04-05", 2000, "Tom Probe", "abo/office"),
               ("2026-04-10", 4000, "MBeisp", "abo/office"),
               ("2026-04-12", 2000, "Theo Test", None))
    office = _abo(geteilt=_geteilt(_zr("2026-04", "2027-04", "2026-04",
                                       ("Tom Probe", 2000), ("MBeisp", 4000),
                                       ("Theo Test", 2000))))
    [z] = abos.abrechnung(conn, office, heute=date(2026, 9, 14))
    assert (z["zurueck"], z["offen"], z["status"]) == (8000, 0, "abgerechnet")


def test_an_open_share_is_due_only_from_the_collection_month():
    conn = _db()
    office = _abo(geteilt=_geteilt(_zr("2027-04", "2028-04", "2028-04", ("MBeisp", 4000))))
    assert abos.abrechnung(conn, office, heute=date(2026, 9, 14))[0]["status"] == "offen"
    assert abos.abrechnung(conn, office, heute=date(2028, 4, 2))[0]["status"] == "faellig"


def test_someone_joining_mid_term_does_not_reopen_the_others():
    """Nordmann kam im laufenden Abo dazu und zahlt im Oktober."""
    conn = _db(("2026-03-22", 4000, "Erika Muster", "abo/video-streaming"))
    abo = _abo(kategorie="abo/video-streaming",
               geteilt=_geteilt(_zr("2026-04", "2027-04", "2026-10",
                                    ("Erika Muster", 4000), ("Nordmann", 2000))))
    [z] = abos.abrechnung(conn, abo, heute=date(2026, 9, 14))
    assert z["status"] == "offen"
    assert [(p["name"], p["offen"]) for p in z["personen"]] == [
        ("Erika Muster", 0), ("Nordmann", 2000)]


def test_an_overpayment_counts_for_the_next_period_not_twice_for_the_first():
    conn = _db(("2026-04-10", 8000, "MBeisp", "abo/office"))
    office = _abo(geteilt=_geteilt(_zr("2026-04", "2027-04", "2026-04", ("MBeisp", 4000)),
                                   _zr("2027-04", "2028-04", "2027-04", ("MBeisp", 4000))))
    erstes, zweites = abos.abrechnung(conn, office, heute=date(2026, 9, 14))
    assert (erstes["zurueck"], erstes["offen"]) == (8000, 0)
    assert zweites["offen"] == 4000


def test_a_payment_assigned_by_hand_counts_whatever_its_category_and_date():
    """Eine Mitnutzerin zahlte 2024 über PayPal, unter konsum/sonstiges -- weit außerhalb
    jedes Fensters. Von Hand zugeordnet zählt es trotzdem, und genau dort."""
    conn = _db(("2024-03-29", 4000, "Erika Muster", "konsum/sonstiges", "erika2024"))
    abo = _abo(kategorie="abo/video-streaming",
               geteilt=_geteilt({"von": "2026-04", "bis": "2027-04", "einsammeln": "2026-10",
                                 "personen": [{"name": "Erika Muster", "anteil_cents": 4000,
                                               "zahlungen": ["erika2024"]}]}))
    [z] = abos.abrechnung(conn, abo, heute=date(2026, 9, 14))
    assert (z["offen"], z["personen"][0]["treffer"][0]["art"]) == (0, "zugeordnet")


def test_a_payment_assigned_to_one_person_is_not_matched_to_another():
    conn = _db(("2026-04-10", 2000, "Timo Nordmann", "abo/office", "timo"))
    abo = _abo(geteilt=_geteilt({"von": "2026-04", "bis": "2027-04", "einsammeln": "2026-04",
                                 "personen": [
                                     {"name": "Timo", "anteil_cents": 2000},
                                     {"name": "Nordmann", "anteil_cents": 2000,
                                      "zahlungen": ["timo"]}]}))
    [z] = abos.abrechnung(conn, abo, heute=date(2026, 9, 14))
    assert [(p["name"], p["offen"]) for p in z["personen"]] == [("Timo", 2000), ("Nordmann", 0)]


def test_a_rejected_automatic_match_stays_rejected():
    conn = _db(("2026-04-10", 4000, "MBeisp", "abo/office", "falsch"))
    abo = _abo(ignoriert=["falsch"],
               geteilt=_geteilt(_zr("2026-04", "2027-04", "2026-04", ("MBeisp", 4000))))
    [z] = abos.abrechnung(conn, abo, heute=date(2026, 9, 14))
    assert z["offen"] == 4000


def test_name_matches_that_count_nowhere_are_offered_to_the_person():
    conn = _db(("2024-03-29", 5000, "Erika Muster", "konsum/sonstiges", "alt"),
               ("2026-03-22", 4000, "Erika Muster", "abo/video-streaming", "zaehlt"))
    abo = _abo(kategorie="abo/video-streaming",
               geteilt=_geteilt(_zr("2026-04", "2027-04", "2026-04", ("Erika Muster", 4000))))
    [z] = abos.abrechnung(conn, abo, heute=date(2026, 9, 14))
    assert [e["hash"] for e in z["personen"][0]["vorschlaege"]] == ["alt"]


def test_an_alias_finds_someone_who_is_named_differently_at_paypal():
    conn = _db(("2026-04-10", 4000, "Markus Pisz", "abo/office"))
    office = _abo(geteilt=_geteilt({"von": "2026-04", "bis": "2027-04",
                                    "einsammeln": "2026-04",
                                    "personen": [{"name": "MBeisp", "anteil_cents": 4000,
                                                  "aliase": ["Markus Pisz"]}]}))
    assert abos.abrechnung(conn, office, heute=date(2026, 9, 14))[0]["offen"] == 0


def test_only_the_open_part_is_expected_in_the_forecast():
    conn = _db(("2028-04-03", 2000, "Theo Test", "abo/office"))
    office = _abo(geteilt=_geteilt(_zr("2027-04", "2028-04", "2028-04",
                                       ("Theo Test", 2000), ("MBeisp", 4000))))
    [r] = abos.rueckzahlungen(conn, [office], "a", ab=date(2026, 9, 1),
                              bis=date(2028, 12, 31), heute=date(2028, 4, 20))
    assert (r["monat"], r["cents"]) == (date(2028, 4, 1), 4000)


# ------------------------------------------------------------ speichern

def test_a_saved_subscription_can_be_loaded_again(tmp_path):
    basis, custom = tmp_path / "abos.yaml", tmp_path / "abos_custom.yaml"
    abos.speichern({"id": "amazon-prime", "name": "Amazon Prime",
                    "kategorie": "abo/sonstiges", "konto": "trade-republic",
                    "betrag_cents": -8990, "takt": 12, "faellig": "2027-02-14",
                    "buchungen": ["prime2026"], "ignoriert": ["falsch"],
                    "geteilt": {"besetzung": [{"name": "Mitnutzerin",
                                              "anteil_cents": 4495}],
                                "zeitraeume": [{"von": "2026-02", "bis": "2027-02",
                                                "einsammeln": "2026-02",
                                                "personen": [{"name": "Mitnutzerin",
                                                              "anteil_cents": 4495,
                                                              "zahlungen": ["zahlung1"]}]}]}},
                   basis=basis, custom=custom)
    [prime] = abos.laden(basis=basis, custom=custom)
    assert (prime["herkunft"], prime["betrag_cents"], prime["faellig"],
            prime["buchungen"]) == ("eigen", -8990, date(2027, 2, 14), ["prime2026"])
    assert prime["geteilt"]["besetzung"][0]["anteil_cents"] == 4495
    assert prime["ignoriert"] == ["falsch"]
    assert prime["geteilt"]["zeitraeume"][0]["personen"][0]["zahlungen"] == ["zahlung1"]


def test_an_incomplete_subscription_is_refused_before_it_is_written(tmp_path):
    custom = tmp_path / "abos_custom.yaml"
    with pytest.raises(ValueError):
        abos.speichern({"id": "kaputt", "name": "Kaputt", "kategorie": "abo/x",
                        "konto": "a", "betrag_cents": -100, "takt": 12},
                       basis=tmp_path / "abos.yaml", custom=custom)
    assert not custom.exists()


def test_removing_a_documented_subscription_only_hides_it(tmp_path):
    basis, custom = tmp_path / "abos.yaml", tmp_path / "abos_custom.yaml"
    basis.write_text("abos:\n  - {id: onedrive, name: OneDrive, kategorie: abo/cloud, "
                     "konto: a, betrag: -89.24, takt: 12, faellig: 2028-04-06}\n",
                     encoding="utf-8")
    abos.entfernen("onedrive", basis=basis, custom=custom)
    assert abos.laden(basis=basis, custom=custom) == []
    assert "onedrive" in basis.read_text(encoding="utf-8")


def test_saving_stores_only_what_differs_from_the_documented_entry(tmp_path):
    """Sonst friert ein Klick jedes Feld der Basisdatei ein."""
    basis, custom = tmp_path / "abos.yaml", tmp_path / "abos_custom.yaml"
    basis.write_text("abos:\n  - {id: m365, name: Microsoft 365, kategorie: abo/office, "
                     "konto: dkb-giro, betrag: -39.00, takt: 12, faellig: 2028-04-05}\n",
                     encoding="utf-8")
    [seite] = abos.laden(basis=basis, custom=custom)
    seite["geteilt"] = {"besetzung": [{"name": "MBeisp", "anteil_cents": 3600}],
                        "zeitraeume": []}
    seite["faellig"] = seite["faellig"].isoformat()
    abos.speichern(seite, basis=basis, custom=custom)

    basis.write_text(basis.read_text(encoding="utf-8").replace("-39.00", "-89.24"),
                     encoding="utf-8")
    [m365] = abos.laden(basis=basis, custom=custom)
    assert m365["betrag_cents"] == -8924
    assert m365["geteilt"]["besetzung"][0]["anteil_cents"] == 3600


def test_saving_an_unchanged_documented_entry_leaves_no_copy(tmp_path):
    basis, custom = tmp_path / "abos.yaml", tmp_path / "abos_custom.yaml"
    basis.write_text("abos:\n  - {id: m365, name: Microsoft 365, kategorie: abo/office, "
                     "konto: dkb-giro, betrag: -39.00, takt: 12, faellig: 2028-04-05}\n",
                     encoding="utf-8")
    [seite] = abos.laden(basis=basis, custom=custom)
    seite["faellig"] = seite["faellig"].isoformat()
    abos.speichern(seite, basis=basis, custom=custom)
    assert abos._roh_custom(custom) == {}


def test_a_running_share_counts_every_month_without_a_period():
    """Wer jeden Monat ueberweist, braucht keinen Zeitraum je Monat."""
    conn = _db(("2026-08-18", 700, "Kim Beispiel Premium SIM", "abo/mobilfunk", "aug"),
               ("2026-09-17", 700, "Kim Beispiel Premium SIM", "abo/mobilfunk", "sep"),
               ("2026-06-02", 700, "Kim Beispiel", "abo/mobilfunk", "vorher"),
               ("2026-09-20", 700, "Jemand Anders", "abo/mobilfunk", "fremd"),
               ("2026-08-27", 20000, "Kim Beispiel", "abo/mobilfunk", "anderes"))
    abo = _abo(kategorie="abo/mobilfunk", takt=1, betrag_cents=-699,
               geteilt={"besetzung": [{"name": "Kim Beispiel", "anteil_cents": 700,
                                       "seit": "2026-08"}]})
    kim, = abos.laufend(conn, abo, heute=date(2026, 10, 5))
    assert (kim["soll"], kim["zurueck"], kim["offen"]) == (2100, 1400, 700)
    assert [t["hash"] for t in kim["treffer"]] == ["sep", "aug"]
    assert sorted(v["hash"] for v in kim["vorschlaege"]) == ["anderes", "vorher"]


def _vertrag(abo_id, **extra):
    return {"id": abo_id, "name": abo_id, "kategorie": "versicherung/phv", "konto": "a",
            "betrag_cents": -6000, "takt": 12, "faellig": "2027-01-15", **extra}


def test_insurances_live_in_their_own_files(tmp_path, monkeypatch):
    """Dieselbe Maschine, eigene Dateien: eine Versicherung ist kein Abo."""
    monkeypatch.setattr(abos, "CONFIG_DIR", tmp_path)
    abos.speichern(_vertrag("haftpflicht", buchungen=["phv2026"]), art="versicherung")
    assert (tmp_path / "versicherungen_custom.yaml").exists()
    assert not (tmp_path / "abos_custom.yaml").exists()
    [phv] = abos.laden(art="versicherung")
    assert (phv["art"], phv["buchungen"]) == ("versicherung", ["phv2026"])
    assert abos.laden() == []
    assert abos.ausschluesse(None, abos.alle_vertraege()) == [{"dedup_hashes": ["phv2026"]}]


def test_one_id_in_both_kinds_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(abos, "CONFIG_DIR", tmp_path)
    abos.speichern(_vertrag("doppelt"), art="versicherung")
    abos.speichern(_vertrag("doppelt", kategorie="abo/sonstiges"))
    with pytest.raises(ValueError, match="doppelt"):
        abos.alle_vertraege()


def test_a_positive_amount_is_refused():
    """Ein Vertrag mit Termin ist Geld raus; ein fehlendes Minus faellt auf."""
    with pytest.raises(ValueError, match="Ausgabe"):
        _abo(betrag_cents=9900)
