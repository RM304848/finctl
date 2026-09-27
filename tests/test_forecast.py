"""Forecast engine, with emphasis on intra-month sequencing.

The trough, not the closing balance, is what the Dispo warning must fire on --
and the trough depends on the ORDER of movements inside a month, not just
their sum.
"""

from __future__ import annotations

from datetime import date

import pytest

from finctl.forecast.engine import RecurringItem, project


def rec(label, cents, **kw):
    return RecurringItem(label=label, account_id="a", amount_cents=cents, **kw)


def test_trough_sits_below_the_closing_balance():
    """The whole point: a month can close comfortably and still dip."""
    p = project(account_id="a", opening_cents=100000, start=date(2026, 10, 1),
                months=1, recurring=[rec("rent", -300000), rec("salary", 500000)])
    row = p.rows[0]
    assert row.trough_cents == -200000        # costs land first
    assert row.closing_cents == 300000        # but it closes fine
    assert row.trough_cents < row.closing_cents
def test_a_foreign_obligation_converts_at_its_declared_rate():
    """The purchase is contracted in rupiah; a euro estimate hides it.

    The figure in planned.yaml was 65.000 against 885.000.000 IDR, implying
    13.615 IDR/EUR -- a rate that does not exist. Recording the foreign amount
    and the rate separately makes re-pricing one number instead of arithmetic
    done by hand.
    """
    import pytest

    """Die Umrechnung bleibt getestet, auch nachdem ihre einzige Anzeige
    entfallen ist.

    config/planned.yaml war die einzige Stelle, die Fremdwährung tragen
    konnte, und ist am 13.09.2026 weggefallen. planned_amount_cents bleibt
    stehen: es ist der Baustein, mit dem das Zeilenmodell Fremdwährung
    bekommen kann, und ihn wegzuwerfen hiesse, ihn beim nächsten Posten neu zu
    schreiben.
    """
    from finctl.forecast.engine import planned_amount_cents

    item = {"label": "Inselhaus", "amount_foreign": -885_000_000,
            "currency": "IDR", "fx_rate": 16500}
    assert planned_amount_cents(item) == -5363636        # 53.636,36

    # 500 points of rate is about 1.500 euro on this one payment.
    assert planned_amount_cents({**item, "fx_rate": 17000}) == -5205882

    # Euro items are untouched.
    assert planned_amount_cents({"amount_cents": -6500000}) == -6500000

    # A foreign amount with no rate is refused rather than silently treated
    # as euro, which would understate this payment by a factor of 16.500.
    with pytest.raises(ValueError, match="fx_rate"):
        planned_amount_cents({"label": "x", "amount_foreign": -1, "currency": "IDR"})


def test_a_funded_payment_does_not_look_like_an_overdraft():
    """Modelling the outflow without the transfer that covers it cries wolf.

    The instalments are booked against the giro account, but the money sits
    until a few days before. Without the funding legs DKB projected to -58.715
    and warned every month -- which is how you learn to ignore warnings.
    """
    from finctl.forecast.engine import funding_legs

    item = {"label": "Inselhaus", "account": "dkb-giro", "date": "2026-10-15",
            "amount_foreign": -442500000, "currency": "IDR", "fx_rate": 20000,
            "funding": [{"from": "scalable", "when": "2026-10-07",
                         "amount_cents": 1057600}]}
    legs = funding_legs(item)
    assert [(g["account"], g["when"], g["amount_cents"]) for g in legs] == [
        ("scalable", "2026-10-07", -1057600),
        ("dkb-giro", "2026-10-07", 1057600),
    ]
    # Nets to zero: at household level this must add nothing at all.
    assert sum(g["amount_cents"] for g in legs) == 0
    # No funding declared -> no legs, rather than a guessed transfer.
    assert funding_legs({k: v for k, v in item.items() if k != "funding"}) == []


def test_funding_dates_survive_the_yaml_boolean_trap():
    """`on:` is the boolean True in YAML 1.1, so the key is `when:`.

    With `on:` the date was unreachable, every transfer silently fell back to
    the payment date, and the trough stayed wrong while looking plausible.
    """
    import yaml

    parsed = yaml.safe_load("{from: scalable, on: 2026-10-07, amount_cents: 1}")
    assert True in parsed and "on" not in parsed        # the trap itself

    parsed = yaml.safe_load("{from: scalable, when: 2026-10-07, amount_cents: 1}")
    assert "when" in parsed


def test_a_servicing_account_does_not_pay_its_loan_twice():
    """Sparda is neutral by construction and projected to -2.664.

    The rate appeared twice: once inferred from its own payment history, once
    from the amortisation schedule. The household forecast fixed this long ago
    and the per-account one never did, so the account that exists purely to
    pass 540 through looked like it was bleeding.
    """
    import sqlite3

    from finctl.forecast.engine import derive_recurring

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(open("finctl/ledger/schema.sql", encoding="utf-8").read())
    db.execute("INSERT INTO accounts (id, display_name, institution, account_type, "
               "ingest_mode, parser_profile) VALUES ('s','S','X','giro','parsed','p')")
    db.execute("INSERT INTO statements (id, account_id, source_path, source_name, "
               "file_sha256, period_start, period_end, balance_start_cents, "
               "balance_end_cents, parser_profile, parser_version, status, imported_at) "
               "VALUES (1,'s','p','n','h','2026-01-01','2026-12-31',0,0,'p','1','imported','t')")
    db.execute("INSERT INTO mgmt_categories (id, name, kind) VALUES ('kredit','K','expense')")
    db.execute("INSERT INTO mgmt_categories (id, parent_id, name, kind) "
               "VALUES ('kredit/zinsen','kredit','Z','expense')")
    for n in range(6):
        db.execute("INSERT INTO transactions (id, account_id, statement_id, booking_date, "
                   "amount_cents, raw_text, seq_in_statement, dedup_hash, created_at) "
                   "VALUES (?, 's', 1, ?, -54000, 'x', ?, ?, 't')",
                   (n, f"2026-0{n+1}-02", n, f"h{n}"))
        db.execute("INSERT INTO splits (transaction_id, seq, amount_cents, "
                   "mgmt_category_id, source, created_at, updated_at) "
                   "VALUES (?, 0, -54000, 'kredit/zinsen', 'rule', 't', 't')", (n,))

    # Without the exclusion the loan is in the baseline AND in the schedule.
    assert [i.label for i in derive_recurring(db, "s")] == ["kredit/zinsen"]
    assert derive_recurring(db, "s", exclude={"kredit/zinsen"}) == []


def test_a_discontinued_habit_is_dropped_from_the_baseline():
    """A decision the rolling window cannot see for six months.

    The joint-account top-ups through Trade Republic were replaced by a larger
    standing order. Extrapolating them would charge for both, and a forecast
    that keeps billing you for a habit you ended is one you stop believing.
    """
    import yaml

    spec = yaml.safe_load(open("config/forecast.yaml", encoding="utf-8"))
    stopped = spec.get("discontinued") or []
    assert stopped, "config/forecast.yaml carries no discontinued list"
    for entry in stopped:
        # Each one has to say what, when and why -- otherwise it is just a
        # number quietly removed from a forecast.
        assert entry.get("category")
        assert entry.get("from")
        assert entry.get("reason")


def test_discontinued_is_scoped_to_the_account_that_stopped():
    """The standing order still runs on DKB; only the ad-hoc channel is shut."""
    import sqlite3

    from finctl import kontenregeln as kr
    from finctl import ops

    eingestellt = [e for e in kr.wirksam().get("discontinued") or [] if e.get("account")]
    if not eingestellt:
        pytest.skip("nichts eingestellt in forecast.yaml")
    db = sqlite3.connect("data/finance.db")
    db.row_factory = sqlite3.Row
    try:
        zu = ops.account_forecast(db, eingestellt[0]["account"], months=3)
        betrieb = ops.account_forecast(db, _konto_mit_rolle("operating"), months=3)
    finally:
        db.close()
    # Das Betriebskonto zahlt weiter; nur der eingestellte Kanal ist zu.
    assert betrieb["rows"] and zu["rows"]


def test_only_the_operating_account_sweeps(tmp_path):
    """Abraeumen ist ein Uebertrag, und ein Uebertrag hat zwei Seiten.

    Die Gegenbuchung baut `household_accounts` nur fuer das Betriebskonto.
    Erklaert ein zweites Konto ein Abraeum-Ziel -- auf /konten ein Klick --,
    dann darf dort nichts abgeraeumt werden: sonst verschwindet Geld, das
    nirgends ankommt. Und das Betriebskonto ist das mit der ROLLE, nicht das
    alphabetisch erste mit einem Ziel.
    """
    import sqlite3
    from pathlib import Path

    from finctl import kontenregeln as kr
    from finctl import ops

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")

    regeln = kr.wirksam().get("account_roles") or {}
    betrieb = next(k for k, v in regeln.items() if (v or {}).get("role") == "operating")
    # Ein Konto, das VOR dem Betriebskonto einsortiert wird und selbst ein
    # Ziel erklaert -- genau der Fall, der die Auswahl vorher kippte.
    anderes = next(k for k in sorted(regeln) if k < betrieb)
    eigen = {"account_roles": {**{k: dict(v or {}) for k, v in regeln.items()},
                               anderes: {**dict(regeln[anderes] or {}),
                                         "sweep_to": betrieb, "ceiling_cents": 5000}}}
    db = sqlite3.connect("data/finance.db")
    db.row_factory = sqlite3.Row
    try:
        from finctl.forecast import konten as fk

        echt = fk.forecast_config
        fk.forecast_config = lambda: kr.zusammenfuehren(echt(), eigen)
        try:
            views = {v["id"]: v for v in ops.household_accounts(db, months=12)}
        finally:
            fk.forecast_config = echt
    finally:
        db.close()

    assert views[betrieb]["sweep_total_cents"] > 0, "das Betriebskonto raeumt ab"
    assert views[anderes]["sweep_total_cents"] == 0, (
        f"{anderes} raeumt ab, ohne dass das Geld irgendwo ankommt")


def test_the_ceiling_leaves_room_for_a_median_month():
    """Der Deckel sagt „alles darüber gehört aufs Tagesgeld". Er kann nicht
    kleiner sein als das, was das Konto durch den Monat bringt -- sonst meldet
    die Seite Geld als frei, das drei Wochen später ausgegeben wird.

    GEMESSEN, nicht getippt: hier stand eine Konstante aus einem Monat, den
    niemand mehr nachrechnet (8.834 Median). Sinkt der Bedarf, war der Test
    eine Behauptung über die Vergangenheit und verbot einen Deckel, der längst
    passt. Der Vergleich läuft deshalb gegen den Median der Prognose.
    """
    import sqlite3
    import statistics
    from pathlib import Path

    from finctl import kontenregeln as kr
    from finctl import ops

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    regeln = kr.wirksam().get("account_roles") or {}
    betrieb = next(k for k, v in regeln.items() if (v or {}).get("sweep_to"))
    db = sqlite3.connect("data/finance.db")
    db.row_factory = sqlite3.Row
    try:
        view = next(v for v in ops.household_accounts(db, months=12)
                    if v["id"] == betrieb)
    finally:
        db.close()

    deckel, floor = view["ceiling_cents"], view["dispo_threshold_cents"]
    median = statistics.median(-r["costs_cents"] for r in view["rows"])
    assert deckel > floor
    assert deckel - floor >= median, (
        f"{betrieb}: Deckel {deckel/100:.0f} über Grenze {floor/100:.0f} lässt "
        f"{(deckel - floor)/100:.0f} für einen Monat, der im Median "
        f"{median/100:.0f} kostet — was abgeräumt wird, fehlt mitten im Monat.")
    # Und die Probe aufs Exempel: der Tiefpunkt haelt trotz Abraeumen.
    assert not view["breach_months"], view["breach_months"]


def test_each_floor_states_what_it_protects():
    """A threshold with no stated reason gets moved by whoever is annoyed by
    it next. Every declared floor carries a note explaining what it covers."""
    import yaml

    spec = yaml.safe_load(open("config/forecast.yaml", encoding="utf-8"))
    text = open("config/forecast.yaml", encoding="utf-8").read()
    for account, role in spec["account_roles"].items():
        if role.get("floor_cents"):
            assert role.get("note") or f"floor_cents: {role['floor_cents']}" in text, account


def test_the_ceiling_is_swept_rather_than_watched():
    """A ceiling with a destination is a monthly instruction, not an
    observation. Modelling the account as if nobody acts on it showed 41.517
    piling up on an account that pays no interest -- true only if you decide
    to do nothing, which is the opposite of what the ceiling is for.
    """
    from finctl.forecast.engine import project

    items = [RecurringItem(label="einkommen/gehalt", account_id="a",
                           amount_cents=500000),
             RecurringItem(label="wohnen/miete", account_id="a",
                           amount_cents=-100000)]
    watched = project(account_id="a", opening_cents=1000000, start=date(2026, 1, 1),
                      months=3, recurring=items)
    swept = project(account_id="a", opening_cents=1000000, start=date(2026, 1, 1),
                    months=3, recurring=items, sweep_above_cents=1100000)

    # Left alone it climbs; swept it holds at the ceiling.
    assert watched.rows[-1].closing_cents > 1100000
    assert [r.closing_cents for r in swept.rows] == [1100000] * 3
    assert swept.rows[0].swept_cents == 300000      # 14.000 -> 11.000
    # And the money is not invented: what is swept is what was above.
    assert all(r.swept_cents == 400000 for r in swept.rows[1:])


def test_no_sweep_without_a_declared_destination():
    """Capping a balance with nowhere to put the money would make the forecast
    lose it."""
    from finctl import ops

    assert ops._sweep_ceiling("scalable", {"account_roles": {
        "scalable": {"ceiling_cents": 100}}}) is None
    assert ops._sweep_ceiling("dkb-giro", {"account_roles": {
        "dkb-giro": {"ceiling_cents": 100, "sweep_to": "scalable"}}}) == 100


def test_no_account_is_left_negative_so_another_can_save():
    """The order is the policy.

    Sweeping first and topping up afterwards would move money into savings
    while a Giro sits in Dispo -- paying overdraft interest to earn deposit
    interest, which is the exact trade the arrangement exists to avoid.
    """
    import sqlite3

    from finctl import ops

    db = sqlite3.connect("data/finance.db")
    db.row_factory = sqlite3.Row
    try:
        views = ops.household_accounts(db, months=12)
    finally:
        db.close()

    # Das versorgende Konto ist das mit der ROLLE, nicht das erste mit einem
    # Abraeum-Ziel: erklaert ein zweites eines, waere es sonst alphabetisch
    # entschieden.
    funder = next(v for v in views if v.get("role") == "operating")

    ohne = _ohne_auffuellung()
    for v in views:
        if "error" in v or v is funder or v["id"] in ohne:
            continue        # the operating account absorbs the shortfalls
        assert v["worst_trough_cents"] >= v["dispo_threshold_cents"], v["id"]

    # And the top-ups are not conjured: what the others received, the
    # operating account paid.
    received = sum(v.get("topped_up_cents", 0) for v in views if v is not funder)
    assert funder["topped_up_cents"] == received


def test_a_commitment_that_has_already_happened_is_not_projected_twice():
    """Once the October instalments are in the ledger they are history.

    The projection starts the month AFTER the last statement, so a planned
    item dated before that is never projected -- which is what stops a
    commitment from being counted once as a plan and once as a transaction.
    This is the property the whole Planung page relies on, so it is asserted
    rather than assumed.
    """
    from datetime import date

    from finctl.forecast import engine as fc

    past = fc.OneOff(label="schon passiert", account_id="a",
                     month=date(2026, 9, 1), amount_cents=-100_000)
    future = fc.OneOff(label="kommt noch", account_id="a",
                       month=date(2026, 11, 1), amount_cents=-100_000)
    proj = fc.project(account_id="a", opening_cents=0,
                      start=fc.add_months(date(2026, 9, 1), 1), months=6,
                      recurring=[], one_offs=[past, future])
    touched = [r for r in proj.rows if r.closing_cents != 0]
    assert touched, "the future item must land somewhere"
    assert all(r.month >= date(2026, 10, 1) for r in proj.rows)
    # Only the November item moved the balance: -100.000, not -200.000.
    assert proj.rows[-1].closing_cents == -100_000


# ------------------------------------------- langer Horizont, Schritt 5

def test_a_derived_item_rises_with_inflation():
    """Über zwölf Monate ist flach vertretbar, über sechzig nicht.

    Die Stromrechnung von 2031 ist nicht die von heute, und eine Prognose,
    die das behauptet, sieht in Monat 50 präzise aus und liegt systematisch
    zu niedrig.
    """
    from datetime import date

    from finctl.forecast.engine import RecurringItem

    item = RecurringItem(label="strom", account_id="x", amount_cents=-5000,
                         escalation_pa=0.02)
    anker = date(2026, 10, 1)
    assert item.amount_in(anker, anker) == -5000
    assert item.amount_in(date(2027, 10, 1), anker) == -5100
    assert item.amount_in(date(2031, 10, 1), anker) == -5520


def test_a_flat_item_stays_flat():
    from datetime import date

    from finctl.forecast.engine import RecurringItem

    item = RecurringItem(label="x", account_id="x", amount_cents=-5000)
    assert item.amount_in(date(2031, 10, 1), date(2026, 10, 1)) == -5000


def test_escalation_steps_yearly_not_continuously():
    """Ein Abo steigt zum Vertragsjahr, nicht kontinuierlich. Eine glatte
    Kurve täuschte eine Genauigkeit vor, die die Sache nicht hat."""
    from datetime import date

    from finctl.forecast.engine import RecurringItem

    item = RecurringItem(label="x", account_id="x", amount_cents=-1000,
                         escalation_pa=0.10)
    anker = date(2026, 1, 1)
    assert item.amount_in(date(2026, 11, 1), anker) == -1000
    assert item.amount_in(date(2027, 1, 1), anker) == -1100


def test_an_override_is_not_escalated():
    """Wer den Gehaltsfloor auf 4.671 setzt, meint 4.671 -- nicht 4.671 mit
    Tariferhöhung."""
    from datetime import date

    from finctl.forecast.engine import RecurringItem, project

    item = RecurringItem(label="einkommen/gehalt", account_id="a",
                         amount_cents=400000, escalation_pa=0.02)
    proj = project(account_id="a", opening_cents=0, start=date(2026, 1, 1),
                   months=30, recurring=[item],
                   income_overrides={"einkommen/gehalt": 467100})
    assert all(r.income_cents == 467100 for r in proj.rows)


def test_a_known_end_stops_a_derived_item():
    """Ein Handyvertrag, der 2028 ausläuft, bucht sonst bis 2031 weiter."""
    from datetime import date

    from finctl.forecast.engine import RecurringItem

    item = RecurringItem(label="abo/mobilfunk", account_id="x",
                         amount_cents=-3000, ends=date(2028, 3, 1))
    assert item.due_in(date(2028, 3, 1))
    assert not item.due_in(date(2028, 4, 1))


def test_the_long_horizon_is_asked_for_never_assumed():
    """Ein Median aus neun Monaten ist über ein Jahr eine gute Näherung und
    über fünf eine Behauptung."""
    from finctl import ops

    assert ops.HORIZON_DEFAULT == 12
    assert ops.HORIZON_LONG == 60


def test_annual_premiums_reach_the_account_forecast():
    """Eine Jahresprämie erscheint in einem Monat von neun.

    Damit unterschreitet sie jede sinnvolle Mindestzahl und fiel aus der
    Kontoprognose ganz heraus: KFZ-Steuer, KFZ-Versicherung, Hausrat, PHV und
    Risiko-LV zusammen rund 1.035 im Jahr echter Verpflichtung, von denen sie
    schlicht nichts wusste. Das Haushaltsmodell hatte das behoben, die
    Kontoprognose nie -- dieselbe Asymmetrie wie beim Sparda-Darlehen.
    """
    from pathlib import Path

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    from finctl.forecast.engine import derive_recurring
    from finctl.ledger.db import connect

    conn = connect()
    try:
        items = derive_recurring(conn, _konto_mit_rolle("operating"))
        fix = {r[0] for r in conn.execute(
            "SELECT id FROM mgmt_categories WHERE fixkosten = 1")}
    finally:
        conn.close()
    jaehrlich = {i.label for i in items if i.every_months == 12}
    # Welche Praemien das sind, steht in den Auszuegen, nicht hier. Geprueft
    # wird, dass es sie gibt -- und dass nur Vertraege jaehrlich wiederkehren.
    assert jaehrlich, "keine Jahrespraemie in der Kontoprognose"
    assert jaehrlich <= fix, jaehrlich - fix


def test_an_annual_item_is_summed_not_medianed():
    """Zwei Zahlungen auf einen Vertrag sind zusammen die Jahresprämie.

    Ihr Median ist keine von beiden und die Jahreskosten von gar nichts --
    Eine Hausratpraemie von 580,40 in zwei Raten meldete der Median als 94,70.
    """
    import sqlite3

    from finctl.forecast.engine import derive_recurring

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE mgmt_categories (id TEXT PRIMARY KEY, kind TEXT,
                                      fixkosten INTEGER DEFAULT 0);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, account_id TEXT,
                                   booking_date TEXT);
        CREATE TABLE splits (transaction_id INTEGER, mgmt_category_id TEXT,
                             amount_cents INTEGER);
        INSERT INTO mgmt_categories VALUES ('versicherung/hausrat', NULL, 1);
        INSERT INTO transactions VALUES (1,'a','2026-03-01'),(2,'a','2026-04-01');
        INSERT INTO splits VALUES (1,'versicherung/hausrat',-54000),
                                  (2,'versicherung/hausrat',-10016);
    """)
    items = derive_recurring(conn, "a", since="2026-01-01", escalation_pa=0.0)
    hausrat = next(i for i in items if i.label == "versicherung/hausrat")
    assert hausrat.amount_cents == -64016
    assert hausrat.every_months == 12


def test_a_quarterly_levy_is_not_flattened_into_one_annual_lump():
    """Die Grundsteuer kommt vierteljährlich, nicht einmal im Jahr.

    Vier Quartale zu einem Jahresklumpen zusammenzuziehen lässt drei Monate
    leer stehen und den vierten um das Dreifache zu hoch -- und die Senke ist
    genau das, was die Dispowarnung liest.
    """
    import sqlite3
    from datetime import date

    from finctl.forecast.engine import derive_recurring

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE mgmt_categories (id TEXT PRIMARY KEY, kind TEXT,
                                      fixkosten INTEGER DEFAULT 0);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, account_id TEXT,
                                   booking_date TEXT);
        CREATE TABLE splits (transaction_id INTEGER, mgmt_category_id TEXT,
                             amount_cents INTEGER);
        INSERT INTO mgmt_categories VALUES ('immobilie/grundsteuer', NULL, 1);
        INSERT INTO transactions VALUES (1,'a','2026-02-16'),(2,'a','2026-05-15'),
                                        (3,'a','2026-08-17');
        INSERT INTO splits VALUES (1,'immobilie/grundsteuer',-14925),
                                  (2,'immobilie/grundsteuer',-14925),
                                  (3,'immobilie/grundsteuer',-14925);
    """)
    items = derive_recurring(conn, "a", since="2026-01-01", escalation_pa=0.0)
    steuer = next(i for i in items if i.label == "immobilie/grundsteuer")
    assert steuer.amount_cents == -14925
    assert steuer.every_months == 3
    # Das nächste Quartal, nicht das nächste Jahr.
    assert steuer.starts == date(2026, 11, 1)


def test_two_instalments_on_one_premium_are_not_a_half_yearly_contract():
    """Benachbarte Monate sind eine Zahlung in Raten, kein eigener Termin.

    Die KFZ-Versicherung kam im Januar und im Februar. Das ist eine
    Jahresprämie; als halbjährlich gelesen wäre sie doppelt so teuer.
    """
    import sqlite3

    from finctl.forecast.engine import derive_recurring

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE mgmt_categories (id TEXT PRIMARY KEY, kind TEXT,
                                      fixkosten INTEGER DEFAULT 0);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, account_id TEXT,
                                   booking_date TEXT);
        CREATE TABLE splits (transaction_id INTEGER, mgmt_category_id TEXT,
                             amount_cents INTEGER);
        INSERT INTO mgmt_categories VALUES ('mobilitaet/kfz-versicherung', NULL, 1);
        INSERT INTO transactions VALUES (1,'a','2026-01-08'),(2,'a','2026-02-05');
        INSERT INTO splits VALUES (1,'mobilitaet/kfz-versicherung',-52000),
                                  (2,'mobilitaet/kfz-versicherung',-9346);
    """)
    items = derive_recurring(conn, "a", since="2026-01-01", escalation_pa=0.0)
    kfz = next(i for i in items if i.label == "mobilitaet/kfz-versicherung")
    assert kfz.amount_cents == -61346
    assert kfz.every_months == 12


def test_an_ordinary_purchase_that_happened_twice_is_not_an_obligation():
    """Periodizität aus zwei Beobachtungen gewöhnlicher Ausgaben zu raten
    erfände Verpflichtungen. Nur das fixkosten-Flag macht daraus einen
    Vertrag."""
    import sqlite3

    from finctl.forecast.engine import derive_recurring

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE mgmt_categories (id TEXT PRIMARY KEY, kind TEXT,
                                      fixkosten INTEGER DEFAULT 0);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, account_id TEXT,
                                   booking_date TEXT);
        CREATE TABLE splits (transaction_id INTEGER, mgmt_category_id TEXT,
                             amount_cents INTEGER);
        INSERT INTO mgmt_categories VALUES ('konsum/moebel', NULL, 0);
        INSERT INTO transactions VALUES (1,'a','2026-03-01'),(2,'a','2026-04-01');
        INSERT INTO splits VALUES (1,'konsum/moebel',-54000),
                                  (2,'konsum/moebel',-10016);
    """)
    assert derive_recurring(conn, "a", since="2026-01-01") == []


def test_a_floor_is_topped_up_once_not_every_month():
    """Die Auffüllung rechnete jeden Monat gegen die UNGEDECKTE Projektion.

    Dort wächst die Lücke -- Sparda fällt ab 04/2027 um 217 im Monat, also
    217, dann 434, dann 651 --, aber nach der ersten Überweisung ist der
    Rückstand bezahlt und nur der Monatsbetrag kommt neu hinzu. Jede
    Monatslücke einzeln zu überweisen summiert sich quadratisch: nach
    dreizehn Monaten 19.747 statt 2.821.

    In der Prognose standen dadurch 19.850 auf einem Konto, das 540 halten
    soll, und das Betriebskonto zahlte es.
    """
    from pathlib import Path

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    from finctl import ops
    from finctl.ledger.db import connect

    conn = connect()
    try:
        views = ops.household_accounts(conn, months=24)
    finally:
        conn.close()

    konto, _ = _ratenkonto()
    rate = next(v for v in views if v["id"] == konto)
    floor = rate["dispo_threshold_cents"]
    hoechster = max(r["closing_cents"] for r in rate["rows"])
    # Ein Konto, das nur seine Rate ausführt, darf nicht über seinem Floor
    # anwachsen. Grosszügig bemessen, damit ein einzelner Monatsversatz den
    # Test nicht bricht -- die Grössenordnung ist der Punkt.
    assert hoechster < floor * 3, (
        f"{konto} wächst auf {hoechster / 100:,.0f} bei Floor {floor / 100:,.0f}")


def test_topping_up_never_leaves_the_account_below_its_floor():
    """Die Gegenprobe: weniger zu überweisen darf nicht heissen, zu wenig."""
    from pathlib import Path

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    from finctl import ops
    from finctl.ledger.db import connect

    conn = connect()
    try:
        views = ops.household_accounts(conn, months=24)
    finally:
        conn.close()

    ohne = _ohne_auffuellung()
    for view in views:
        if view.get("error") or view.get("sweeps_to") or view["id"] in ohne:
            continue
        floor = view["dispo_threshold_cents"]
        tiefster = min(r["trough_cents"] for r in view["rows"])
        assert tiefster >= floor - 100, (
            f"{view['id']} fällt auf {tiefster / 100:,.0f} unter {floor / 100:,.0f}")


def test_a_servicing_floor_follows_its_loan_rate():
    """Ein Konto, das nur eine Rate ausführt, soll genau eine Rate halten.

    Eine Rate springt im April 2027 von 540 auf 757. Vorher stand der Floor
    fest auf 540, mit dem Kommentar "steigt die Rate, gehört auch dieser Wert
    hoch" -- genau solche Kommentare werden übersehen. Abgeleitet steigt er
    von selbst mit.
    """
    from datetime import date
    from pathlib import Path

    if not Path("config/loans.yaml").exists():
        pytest.skip("keine Kreditdaten")
    from finctl.ops import _rate_floor

    _, kredit = _ratenkonto()
    vorher, nachher, ab = _ratensprung(kredit)
    davor = date(ab.year - (ab.month == 1), (ab.month - 2) % 12 + 1, 1)
    assert _rate_floor(kredit, davor) == vorher
    assert _rate_floor(kredit, ab) == nachher


def test_the_servicing_account_holds_exactly_one_rate():
    """Genug, dass die Lastschrift nie platzt, und keinen Euro mehr -- das
    Geld verzinst sich auf dem Tagesgeldkonto besser."""
    from pathlib import Path

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    from finctl import ops
    from finctl.ledger.db import connect

    conn = connect()
    try:
        views = ops.household_accounts(conn, months=24)
    finally:
        conn.close()

    konto, kredit = _ratenkonto()
    _, nachher, ab = _ratensprung(kredit)
    rate = next(v for v in views if v["id"] == konto)
    # Zwei Monate Anlauf nach dem Sprung, dann haelt das Konto die neue Rate.
    ab_hier = date(ab.year + (ab.month > 10), (ab.month + 1) % 12 + 1, 1).isoformat()
    spaet = [r for r in rate["rows"] if r["month"] >= ab_hier]
    if not spaet:
        pytest.skip("der Ratensprung liegt ausserhalb der Prognose")
    for row in spaet:
        assert 0.99 * nachher <= row["trough_cents"] <= 1.06 * nachher, (
            f"{row['month'][:7]}: {row['trough_cents'] / 100:,.2f}")


def test_an_unknown_loan_falls_back_rather_than_crashing():
    """Ein Tippfehler in der Kredit-Kennung darf die Prognose nicht
    abschiessen -- sie fällt auf den eingetragenen Wert zurück."""
    from finctl.ops import _rate_floor

    assert _rate_floor("gibt-es-nicht") is None


def test_a_cessation_reduces_costs_rather_than_adding_income():
    """Ein Wegfall ist positiv, weil Geld aufhört zu fliessen -- aber er ist
    keine Einnahme, sondern eine negative Ausgabe.

    Nach Vorzeichen einsortiert landete er bei den Einnahmen, und die kommen
    per Konvention NACH den Kosten. Der Saldo stimmte, die Kostenzeile log,
    und der Monatstiefpunkt blieb unverändert -- obwohl genau er sich
    verbessert.
    """
    from datetime import date

    from finctl.forecast.engine import OneOff, RecurringItem, project

    sprit = RecurringItem(label="sprit", account_id="a", amount_cents=-14091)
    weg = OneOff(label="Sprit entfällt", account_id="a", month=date(2026, 11, 1),
                 amount_cents=14091, reduces_cost=True)

    ohne = project(account_id="a", opening_cents=100000, start=date(2026, 11, 1),
                   months=1, recurring=[sprit])
    mit = project(account_id="a", opening_cents=100000, start=date(2026, 11, 1),
                  months=1, recurring=[sprit], one_offs=[weg])

    assert ohne.rows[0].costs_cents == -14091
    assert mit.rows[0].costs_cents == 0, "der Wegfall gehört in die Kostenzeile"
    assert mit.rows[0].income_cents == ohne.rows[0].income_cents


def test_a_cessation_lifts_the_trough():
    """Die Zahl, für die die ganze Trough-Mechanik existiert.

    Beide liegen auf Tag 1, und die Kosten standen in der Reihenfolge davor --
    der Tiefpunkt wurde gebildet, bevor die Entlastung ankam. Fällt der Sprit
    weg, zeigte die Prognose den Tiefpunkt weiter so, als würde er bezahlt.
    """
    from datetime import date

    from finctl.forecast.engine import OneOff, RecurringItem, project

    sprit = RecurringItem(label="sprit", account_id="a", amount_cents=-14091)
    weg = OneOff(label="Sprit entfällt", account_id="a", month=date(2026, 11, 1),
                 amount_cents=14091, reduces_cost=True)

    ohne = project(account_id="a", opening_cents=100000, start=date(2026, 11, 1),
                   months=1, recurring=[sprit])
    mit = project(account_id="a", opening_cents=100000, start=date(2026, 11, 1),
                  months=1, recurring=[sprit], one_offs=[weg])

    assert ohne.rows[0].trough_cents == 100000 - 14091
    assert mit.rows[0].trough_cents == 100000


def test_an_ordinary_receipt_still_arrives_after_the_costs():
    """Die pessimistische Reihenfolge bleibt, wo sie richtig ist: eine
    Gutschrift ohne Tag kommt spät im Monat, und der Tiefpunkt davor ist das,
    wovor gewarnt werden muss."""
    from datetime import date

    from finctl.forecast.engine import OneOff, RecurringItem, project

    kosten = RecurringItem(label="miete", account_id="a", amount_cents=-50000)
    gutschrift = OneOff(label="Bonus", account_id="a", month=date(2026, 11, 1),
                        amount_cents=50000)
    p = project(account_id="a", opening_cents=100000, start=date(2026, 11, 1),
                months=1, recurring=[kosten], one_offs=[gutschrift])
    assert p.rows[0].trough_cents == 50000
    assert p.rows[0].closing_cents == 100000


def test_a_wegfall_still_saves_money_in_year_nineteen():
    """Die Jahresrechnung loeste Wegfall-Zeilen nicht auf, die Monatsrechnung schon.

    `ops.py` rief `resolve_measured`, `jahre.py` nicht -- ein Wegfall sparte
    also zwoelf Monate weit Geld und danach keines mehr. Beim Auto war das der
    Kraftstoff, bei einem Eigenheim waere es die ganze Miete, also genau der
    Posten, dessen Wegfall den Kauf ueberhaupt erst traegt.

    Gezaehlt wird nur, was zugeordnet ist -- der Test ordnet deshalb die
    Mietbuchungen des Messfensters zu, wie es auf der Planungsseite geschieht.
    """
    import tempfile

    import yaml

    from finctl.forecast import abgleich as ag
    from finctl.forecast import jahre as jm
    from finctl.ledger.db import connect

    c = connect()
    fenster = ag.fenster(c)
    miete = [r[0] for r in c.execute(
        "SELECT t.dedup_hash FROM transactions t JOIN splits s ON s.transaction_id = t.id "
        "WHERE s.mgmt_category_id = 'wohnen/miete' AND t.booking_date BETWEEN ? AND ?",
        (fenster.von.isoformat(), fenster.bis.isoformat()))]
    if not miete:
        c.close()
        pytest.skip("keine Mietbuchungen im Messfenster")

    spec = {"szenarien": [{
        "id": "pytest-wegfall", "name": "Pytest Wegfall", "aktiv": True,
        "zeilen": [{"label": "Miete entfällt", "art": "wegfall",
                    "amount_cents": 0, "frequenz": "monatlich",
                    "start": "2027-01", "kategorie": "wohnen/miete",
                    "buchungen": miete}]}]}
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(spec, f, allow_unicode=True)
    f.close()

    try:
        ohne = jm.project(c, opening_cents=0, end_year=2040)
        mit = jm.project(c, opening_cents=0, end_year=2040, szenarien=f.name)
        # Nicht nur im ersten Jahr: der Wegfall muss auch 2039 noch tragen.
        assert mit.year(2027).blocks["sondereffekt"] > 0
        assert mit.year(2039).blocks["sondereffekt"] > 0
        assert mit.final_cents > ohne.final_cents
    finally:
        c.close()


def test_a_new_year_without_statements_does_not_erase_every_cost(ohne_plaene):
    """Am 1. Januar war die Extrapolation ein Geschenk statt einer Rechnung.

    `months_observed` gibt 1 zurueck, wenn ein Jahr gar keinen vollstaendigen
    Monat hat -- ein Ruecksturz, der die Division rettet und die Wahrheit
    verdeckt. Gemessen wurde dann nichts, geteilt durch eins, mal zwoelf: jede
    laufende Position stand auf null.

    Die Folge war kein Absturz, sondern eine schoene Zahl. Ohne Miete,
    Fixkosten, Immobilienkosten und Konsum sprang das Endkapital 2046 von
    775.548 auf 1.927.828 -- jeden Januar aufs Neue, bis der erste Auszug des
    neuen Jahres kommt, und niemand haette es bemerkt.
    """
    from finctl.forecast import abgleich as ag
    from finctl.forecast import jahre as jm
    from finctl.ledger.db import connect

    c = connect()
    try:
        ende = ag.letzter_vollstaendiger_monat(c)
        assert ende is not None, "kein vollstaendiger Monat im Ledger"

        # Ein Jahr NACH dem letzten gemessenen: gemessen wird trotzdem dort,
        # wo Daten sind.
        naechstes = ende.year + 1
        assert ag.fenster(c) is not None

        lauf = jm.project(c, opening_cents=0, base_year=naechstes,
                          end_year=naechstes + 10, szenarien=ohne_plaene)
        erstes = lauf.years[0].blocks
        # Die laufenden Bloecke sind da, nicht null.
        for block in ("miete", "fixkosten", "konsum", "immobilie"):
            assert erstes.get(block), f"{block} steht auf null"
        # Und die Kosten sind Kosten geblieben.
        assert erstes["fixkosten"] < 0 and erstes["konsum"] < 0

        # Gegenprobe: mit dem gemessenen Jahr als Start darf sich das
        # Endkapital nicht vervielfachen.
        normal = jm.project(c, opening_cents=0, base_year=ende.year,
                            end_year=naechstes + 10, szenarien=ohne_plaene).final_cents
        spaet = lauf.final_cents
        assert abs(spaet - normal) < abs(normal) * 0.5, (normal, spaet)
    finally:
        c.close()


def test_every_account_detail_says_where_it_comes_from():
    """Die Kontoprognose nennt je Etikett Quelle und Herleitung.

    Ohne das stand der Gehaltsfloor als Zahl da, die aussah wie gemessen, und
    niemand sah, dass /konten mit einer anderen Basis rechnet als die
    Jahresrechnung.
    """
    from pathlib import Path

    from finctl import ops
    from finctl.forecast.herkunft import QUELLEN
    from finctl.ledger.db import connect

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    c = connect()
    try:
        views = ops.household_accounts(c, months=6)
    finally:
        c.close()
    for v in views:
        if v.get("error"):
            continue
        for r in v["rows"]:
            assert sum(r["detail"].values()) == r["costs_cents"] + r["income_cents"]
            for etikett in r["detail"]:
                h = v["herkunft"][etikett]
                assert h["quelle"] in QUELLEN, (v["id"], etikett)
                assert h["herleitung"] != "ohne hinterlegte Herleitung", (v["id"], etikett)


def test_account_forecast_starts_after_the_last_complete_month(tmp_path):
    """Ein angebrochener Monat zaehlt nicht -- weder als Saldo noch als Fenster.

    Ein Snapshot vom 11.09. liess die Prognose im Oktober beginnen, und der
    September mit Gehalt und Kosten fehlte. Start ist der Monatsletzte, den
    alle Konten abdecken; Buchungen danach werden aus dem Saldo herausgerechnet.
    """
    import sqlite3

    from finctl import ops

    db = sqlite3.connect(tmp_path / "t.db")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE statements (account_id TEXT, period_start TEXT, period_end TEXT,
                                 balance_end_cents INTEGER, status TEXT);
        CREATE TABLE transactions (account_id TEXT, booking_date TEXT, amount_cents INTEGER);
        CREATE TABLE balance_snapshots (account_id TEXT, as_of TEXT, balance_cents INTEGER);
        INSERT INTO statements VALUES ('giro', '2026-08-04', '2026-09-04', 50000, 'imported');
        INSERT INTO statements VALUES ('spar', '2026-08-01', '2026-08-31', 7000, 'imported');
        INSERT INTO transactions VALUES ('giro', '2026-08-31', -1000);
        INSERT INTO transactions VALUES ('giro', '2026-09-01', -30000);
        INSERT INTO transactions VALUES ('giro', '2026-09-04', 5000);
        INSERT INTO balance_snapshots VALUES ('giro', '2026-09-11', 99999);
    """)
    assert ops._stichtag(db) == date(2026, 8, 31)
    assert ops._erster_offener_monat(db) == date(2026, 9, 1)
    # 500 am 04.09., davor -300 und +50 im September: 750 am 31.08.
    assert ops._saldo_zum(db, "giro", date(2026, 8, 31)) == 75000
    assert ops._saldo_zum(db, "spar", date(2026, 8, 31)) == 7000


def _konto_mit_rolle(rolle: str) -> str:
    """Das Konto mit dieser Rolle aus der Konfiguration, nicht aus dem Test."""
    from finctl import kontenregeln as kr

    rollen = kr.wirksam().get("account_roles") or {}
    konto = next((k for k, v in rollen.items() if (v or {}).get("role") == rolle), None)
    if konto is None:
        pytest.skip(f"kein Konto mit der Rolle {rolle}")
    return konto


def _ratenkonto() -> tuple[str, str]:
    """Ein Konto, das nur eine Rate ausfuehrt, und der Kredit dazu."""
    from finctl import kontenregeln as kr

    for konto, regel in (kr.wirksam().get("account_roles") or {}).items():
        if (regel or {}).get("floor_from_loan"):
            return konto, str(regel["floor_from_loan"])
    pytest.skip("kein Ratenkonto in forecast.yaml")
    raise AssertionError            # pragma: no cover -- skip springt vorher


def _ratensprung(kredit: str) -> tuple[int, int, date]:
    """Alte Rate, neue Rate und der Monat, ab dem die neue gilt -- aus loans.yaml."""
    import itertools
    from pathlib import Path

    import yaml

    spec = yaml.safe_load(Path("config/loans.yaml").read_text(encoding="utf-8"))
    loan = next(k for k in spec["loans"] if str(k["id"]) == kredit)
    abschnitte = loan.get("segments") or []
    for alt, neu in itertools.pairwise(abschnitte):
        if alt["annuity_cents"] != neu["annuity_cents"]:
            return alt["annuity_cents"], neu["annuity_cents"], neu["start"].replace(day=1)
    pytest.skip(f"Kredit {kredit} hat keinen Ratensprung")
    raise AssertionError            # pragma: no cover


def _ohne_auffuellung() -> set[str]:
    """Konten, die das Betriebskonto bewusst NICHT auffuellt."""
    from pathlib import Path

    import yaml

    spec = yaml.safe_load(Path("config/forecast.yaml").read_text(encoding="utf-8")) or {}
    return {k for k, v in (spec.get("account_roles") or {}).items()
            if (v or {}).get("auffuellen") is False}


def test_an_account_without_topping_up_keeps_only_its_standing_orders():
    """Auffuellen verzerrte den Bedarf: der Tiefpunkt klebte an der Grenze.

    Ein Konto mit `auffuellen: false` bekommt keine Auffuellung, und das
    Betriebskonto zahlt keine dafuer -- es bleibt beim Dauerauftrag.
    """
    from pathlib import Path

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    from finctl import ops
    from finctl.ledger.db import connect

    ohne = _ohne_auffuellung()
    if not ohne:
        pytest.skip("kein Konto ohne Auffuellung")
    conn = connect()
    try:
        views = ops.household_accounts(conn, months=12)
    finally:
        conn.close()
    for v in views:
        for row in v.get("rows", []):
            for etikett in row["detail"]:
                for konto in ohne:
                    assert etikett != f"Auffüllung {konto}", (v["id"], row["month"])


@pytest.fixture
def fixfenster(tmp_path):
    """Ein Konto, 09/2025 bis 08/2026, mit vier Arten von Fixkosten."""
    from finctl.ledger.db import connect, init_db

    c = connect(init_db(tmp_path / "f.db"))
    c.execute("INSERT INTO accounts (id, display_name, institution, account_type, "
              "ingest_mode, parser_profile) VALUES ('a','A','X','giro','parsed','p')")
    c.execute("INSERT INTO mgmt_categories (id, parent_id, name, kind, fixkosten) "
              "VALUES ('fix', NULL, 'fix', 'expense', 1)")
    for kid in ("praemie", "grundsteuer", "abo", "alt"):
        c.execute("INSERT INTO mgmt_categories (id, parent_id, name, kind, fixkosten) "
                  "VALUES (?, 'fix', ?, 'expense', 1)", (f"fix/{kid}", kid))
    sid = c.execute(
        "INSERT INTO statements (account_id, source_path, source_name, file_sha256, "
        "period_start, period_end, balance_start_cents, balance_end_cents, "
        "parser_profile, parser_version, status, imported_at) "
        "VALUES ('a','x','x','s','2025-09-01','2026-08-31',0,0,'p','1','imported','now')"
    ).lastrowid
    buchungen = [
        # Jahrespraemie im Januar -- ausserhalb eines Sechsmonatsfensters.
        ("2026-01-15", -60000, "fix/praemie"),
        # Quartal, mit einer Nachbuchung im Maerz: fuenf Monate mit Buchung.
        ("2025-11-15", -15000, "fix/grundsteuer"), ("2026-02-15", -11500, "fix/grundsteuer"),
        ("2026-03-02", -3500, "fix/grundsteuer"), ("2026-05-15", -15000, "fix/grundsteuer"),
        ("2026-08-15", -15000, "fix/grundsteuer"),
        # Monatlich, Buchungstag springt zwischen dem 1. und dem 31.
        ("2025-12-31", -2199, "fix/abo"), ("2026-01-31", -2199, "fix/abo"),
        ("2026-03-01", -2199, "fix/abo"), ("2026-03-31", -2199, "fix/abo"),
        ("2026-05-01", -2199, "fix/abo"), ("2026-05-31", -2199, "fix/abo"),
        ("2026-07-01", -2199, "fix/abo"), ("2026-07-31", -2199, "fix/abo"),
        ("2026-08-31", -2199, "fix/abo"),
        # Monatlich bis Juni, dann auf ein anderes Konto gewandert.
        *[(f"{j}-{m:02d}-01", -3999, "fix/alt")
          for j, m in ((2025, 9), (2025, 10), (2025, 11), (2025, 12), (2026, 1),
                       (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6))],
    ]
    for n, (tag, cents, kat) in enumerate(buchungen):
        tid = c.execute(
            "INSERT INTO transactions (account_id, statement_id, booking_date, "
            "amount_cents, raw_text, seq_in_statement, dedup_hash, created_at) "
            "VALUES ('a',?,?,?,'x',?,?,'now')", (sid, tag, cents, n, f"h{n}")).lastrowid
        c.execute("INSERT INTO splits (transaction_id, amount_cents, mgmt_category_id, "
                  "source, created_at, updated_at) VALUES (?,?,?,'rule','now','now')",
                  (tid, cents, kat))
    c.commit()
    yield c
    c.close()


def test_fixed_costs_get_a_twelve_month_window(fixfenster):
    """Sechs Monate fuer variable Kosten, zwoelf fuer Fixkosten.

    Mit einem Fenster fuer alles fehlten KFZ-Versicherung und Haftpflicht aus
    dem Januar im September ganz -- und ein zweites langes Fenster fuer alles
    brachte beendete Abos als Monatskosten zurueck.
    """
    from finctl.forecast.engine import derive_recurring

    kurz = {i.label: i for i in derive_recurring(
        fixfenster, "a", since="2026-03-01", until="2026-08-31", escalation_pa=0.0)}
    assert "fix/praemie" not in kurz

    lang = {i.label: i for i in derive_recurring(
        fixfenster, "a", since="2026-03-01", until="2026-08-31", escalation_pa=0.0,
        since_fixkosten="2025-09-01")}
    # Die Jahrespraemie kommt im naechsten Januar wieder.
    assert (lang["fix/praemie"].amount_cents, lang["fix/praemie"].every_months) == (-60000, 12)
    assert lang["fix/praemie"].starts == date(2027, 1, 1)
    # Die Grundsteuer bleibt im Quartal, trotz fuenf Monaten mit Buchung.
    assert lang["fix/grundsteuer"].every_months == 3
    assert lang["fix/grundsteuer"].amount_cents == -15000
    # Das Abo mit springendem Tag kostet seinen Monatsbetrag, nicht das Doppelte.
    assert (lang["fix/abo"].amount_cents, lang["fix/abo"].every_months) == (-2199, 1)
    # Was seit Juni nicht mehr gebucht wird, laeuft nicht weiter.
    assert "fix/alt" not in lang
