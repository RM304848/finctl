"""Toggleable what-if plans.

What is tested is the behaviour that makes them useful rather than decorative:
that a frequency expands into the right months, that switching one off removes
its effect completely, and that a yearly line and a monthly line can be
compared at all.
"""

from __future__ import annotations

from datetime import date

from finctl.forecast import szenarien as sz


def _line(**kw):
    base = dict(label="x", amount_cents=-10000, frequency="monatlich",
                start=date(2026, 11, 1), end=None)
    base.update(kw)
    return sz.Line(**base)


def test_monthly_line_hits_every_month():
    out = _line(end=date(2027, 2, 1)).months(date(2030, 1, 1))
    assert [m.isoformat() for m in out] == [
        "2026-11-01", "2026-12-01", "2027-01-01", "2027-02-01"]


def test_quarterly_line_steps_by_three():
    out = _line(frequency="quartalsweise", end=date(2027, 8, 1)).months(date(2030, 1, 1))
    assert [m.isoformat() for m in out] == [
        "2026-11-01", "2027-02-01", "2027-05-01", "2027-08-01"]


def test_yearly_line_steps_by_twelve():
    out = _line(frequency="jaehrlich", end=date(2029, 1, 1)).months(date(2030, 1, 1))
    assert [m.isoformat() for m in out] == ["2026-11-01", "2027-11-01", "2028-11-01"]


def test_a_one_off_lands_once():
    assert _line(frequency="einmalig").months(date(2030, 1, 1)) == [date(2026, 11, 1)]


def test_a_line_with_no_end_runs_to_the_horizon_not_forever():
    """An open commitment is real -- a subscription, a rate with no stated term.

    Refusing to model it would push the user to invent an end date, which is a
    worse answer than carrying it to the edge of the projection.
    """
    out = _line().months(date(2027, 4, 1))
    assert out[-1] == date(2027, 4, 1)
    assert len(out) == 6


def test_a_line_starting_after_the_horizon_lands_nowhere():
    assert _line(start=date(2031, 1, 1)).months(date(2030, 1, 1)) == []


def test_yearly_and_monthly_lines_become_comparable():
    """A yearly premium and a monthly rate are not comparable as written, and
    "what does this plan cost me a month" is the question actually asked."""
    yearly = _line(frequency="jaehrlich", amount_cents=-120000)
    monthly = _line(frequency="monatlich", amount_cents=-10000)
    assert yearly.monthly_equivalent_cents() == monthly.monthly_equivalent_cents()
    assert yearly.annual_cents() == monthly.annual_cents() == -120000


def test_a_one_off_has_no_monthly_equivalent():
    """It changes the balance once, not the rate of saving. Folding it into a
    monthly figure would misstate both."""
    assert _line(frequency="einmalig", amount_cents=-500000).monthly_equivalent_cents() == 0


# --------------------------------------------------------------- toggling

SPEC = {"szenarien": [
    {"id": "auto", "name": "Auto", "aktiv": True, "zeilen": [
        {"label": "Anzahlung", "amount_cents": -500000, "frequenz": "einmalig",
         "start": "2026-11"},
        {"label": "Rate", "amount_cents": -35000, "frequenz": "monatlich",
         "start": "2026-11", "ende": "2031-10"},
    ]},
    {"id": "haus", "name": "Zweites Haus", "aktiv": False, "zeilen": [
        {"label": "Rate", "amount_cents": -90000, "frequenz": "monatlich",
         "start": "2027-01"},
    ]},
]}


def test_only_active_scenarios_count():
    loaded = sz.load(SPEC)
    assert [s.id for s in sz.active(loaded)] == ["auto"]
    assert sz.monthly_impact_cents(loaded) == -35000


def test_switching_one_on_changes_the_impact():
    """Parking a plan must be one toggle, with nothing left behind."""
    loaded = sz.load(SPEC)
    for s in loaded:
        s.active = True
    assert sz.monthly_impact_cents(loaded) == -35000 - 90000


def test_dated_amounts_carry_the_scenario_name():
    """A forecast row has to say which plan put it there, or an unexpected
    dip is untraceable back to the toggle that caused it."""
    rows = sz.dated_amounts(sz.load(SPEC), date(2027, 1, 1))
    assert all(r["scenario_id"] == "auto" for r in rows)
    assert any(r["label"] == "Auto: Anzahlung" for r in rows)


def test_an_unknown_frequency_falls_back_rather_than_vanishing():
    """A typo must not silently drop a cost line out of the plan."""
    loaded = sz.load({"szenarien": [{"id": "x", "name": "X", "aktiv": True,
                                     "zeilen": [{"label": "y", "amount_cents": -100,
                                                 "frequenz": "woechentlich",
                                                 "start": "2026-11"}]}]})
    assert loaded[0].lines[0].frequency == "monatlich"


# ------------------------------------------------------- obligations

OBLIGATION = {"szenarien": [
    {"id": "verpflichtungen", "name": "Verpflichtungen", "pflicht": True,
     "aktiv": False,
     "zeilen": [{"label": "Rate", "amount_cents": -20000, "frequenz": "monatlich",
                 "start": "2026-11", "kategorie": "kredit/tilgung"}]},
]}


def test_an_obligation_is_active_even_when_the_flag_says_otherwise():
    """Otherwise the ban on switching it off is a file edit away.

    Purchase instalments are owed whatever else is decided; a forecast that
    lets a debt be clicked away is worse than one that cannot model options
    at all.
    """
    loaded = sz.load(OBLIGATION)
    assert loaded[0].obligation is True
    assert loaded[0].active is True
    assert sz.monthly_impact_cents(loaded) == -20000


def test_an_ordinary_plan_is_not_an_obligation():
    assert sz.load(SPEC)[0].obligation is False


# ------------------------------------------------------------- Wegfall

def test_a_cessation_takes_its_amount_from_the_ledger():
    """A new car does not cost 350 a month -- it costs 350 minus the old car.

    Typing that saving in by hand makes it a claim from the day it is entered,
    and one that flatters the plan. Measuring it keeps it honest as the real
    spending moves.
    """
    from datetime import date

    conn = _ledger(*_abo_monatlich(date(2026, 8, 1)))
    getippt = sz.Line(label="alter Wagen entfällt", amount_cents=99900,
                      kind="wegfall", start=date(2026, 11, 1),
                      buchungen=["cr202608"])
    m = sz.messung(conn, getippt, _fenster("2025-09-01", "2026-08-31"))
    # Der Betrag aus der Datei zaehlt nicht: gemessen sind 30 im Monat.
    assert sz.restbetrag_je_termin(getippt, m) == 3000


def test_an_unmeasurable_cessation_saves_nothing_and_says_so():
    """A cessation worth nothing is almost always a mistyped category.

    Sie darf nicht heimlich den Schnitt ihrer ganzen Kategorie nehmen -- das
    tat die Kontoprognose, und niemand sah, woran sie rechnete. Sie zieht
    nichts ab, und /planung nennt sie unter `offen`, damit die Zeile nicht
    stumm wirkungslos bleibt.
    """
    from datetime import date

    conn = _ledger(*_abo_monatlich(date(2026, 8, 1)))
    ohne = sz.Line(label="gibt es nicht", amount_cents=0, kind="wegfall",
                   start=date(2026, 11, 1), buchungen=[])
    m = sz.messung(conn, ohne, _fenster("2025-09-01", "2026-08-31"))
    assert (m["cents"], m["quelle"]) == (0, "keine")
    assert sz.restbetrag_je_termin(ohne, m) == 0


def test_an_unknown_kind_falls_back_to_an_amount():
    spec = {"szenarien": [{"id": "x", "name": "X", "zeilen": [
        {"label": "y", "art": "irgendwas", "amount_cents": -100,
         "frequenz": "monatlich", "start": "2026-11"}]}]}
    assert sz.load(spec)[0].lines[0].kind == "betrag"


# ---------------------------------------------------- geschlossener Kreis
#
# Eine Planzeile wirkt nur mit dem Teil, der noch nicht im Messfenster steckt.

def _ledger(*buchungen):
    """(Datum, Cent, Gegenpartei, Kategorie, Hash)."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, account_id TEXT,
            booking_date TEXT, raw_text TEXT, counterparty_norm TEXT, dedup_hash TEXT);
        CREATE TABLE splits (transaction_id INTEGER, mgmt_category_id TEXT,
            amount_cents INTEGER);
    """)
    for n, (wann, cents, gp, kat, h) in enumerate(buchungen, start=1):
        conn.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?)",
                     (n, "dkb-giro", wann, gp, gp, h))
        conn.execute("INSERT INTO splits VALUES (?,?,?)", (n, kat, cents))
    return conn


def _fenster(von, bis, monate=12):
    from datetime import date

    from finctl.forecast.abgleich import Fenster

    return Fenster(von=date.fromisoformat(von), bis=date.fromisoformat(bis), monate=monate)


def _abo_monatlich(bis_monat):
    """Ein Abo mit 30 im Monat, von Sept. 2025 bis einschliesslich bis_monat."""
    from datetime import date

    zeilen, monat = [], date(2025, 9, 1)
    while monat <= bis_monat:
        zeilen.append((monat.replace(day=5).isoformat(), -3000, "crunchyroll",
                       "abo/video-streaming", f"cr{monat:%Y%m}"))
        monat = date(monat.year + (monat.month == 12), monat.month % 12 + 1, 1)
    return zeilen


def test_a_drop_out_removes_exactly_what_the_window_still_holds():
    """Heute zwölf Monate Abo im Fenster, also 360 im Jahr."""
    from datetime import date

    conn = _ledger(*_abo_monatlich(date(2026, 8, 1)))
    wegfall = sz.Line(label="Crunchyroll", amount_cents=0, kind="wegfall",
                      start=date(2026, 10, 1), buchungen=["cr202608"])
    m = sz.messung(conn, wegfall, _fenster("2025-09-01", "2026-08-31"))
    assert m["cents"] == -36000 and m["quelle"] == "zugeordnet"
    assert sz.restwirkung_monatlich(wegfall, m) == 3000
    assert sz.jahreswirkung(wegfall, m, 2027, 0, date(2027, 12, 1)) == 36000


def test_both_projections_answer_the_same_for_one_line():
    """Kontoprognose und Jahresrechnung, dieselbe Zeile, dieselbe Zahl.

    Die Kontoprognose rechnete einen Wegfall ueber den Schnitt der GANZEN
    Kategorie, die Jahresrechnung ueber die zugeordneten Buchungen -- bei
    derselben Zeile zwei verschiedene Monatswerte. Zwei Antworten auf eine
    Frage sind schlimmer als eine falsche: man merkt es nicht.
    """
    from datetime import date

    # Eine zweite Reihe in derselben Kategorie, die NICHT zur Zeile gehoert.
    # Genau an ihr liefen die beiden Rechnungen frueher auseinander.
    fremd = [(f"2026-0{m}-20", -500, "anderer anbieter",
              "abo/video-streaming", f"x{m}") for m in range(1, 9)]
    conn = _ledger(*_abo_monatlich(date(2026, 8, 1)), *fremd)
    wegfall = sz.Line(label="Crunchyroll", amount_cents=0, kind="wegfall",
                      start=date(2026, 10, 1), buchungen=["cr202608"])
    m = sz.messung(conn, wegfall, _fenster("2025-09-01", "2026-08-31"))
    je_termin = sz.restbetrag_je_termin(wegfall, m)
    assert je_termin == 3000
    assert je_termin * 12 == sz.jahreswirkung(wegfall, m, 2027, 0, date(2027, 12, 1))


def test_a_yearly_line_keeps_its_date_instead_of_a_twelfth():
    """Der Kontoprognose nuetzt kein Zwoelftel: sie liest den Tag.

    `restwirkung_monatlich` glaettet fuer die Seite, `restbetrag_je_termin`
    nicht -- sonst risse eine Jahrespraemie den Monatstiefpunkt nicht, den sie
    in Wahrheit reisst, und die Dispo-Warnung bliebe stumm.
    """
    from datetime import date

    zeile = sz.Line(label="Versicherung", amount_cents=-120000,
                    frequency="jaehrlich", start=date(2027, 3, 1))
    leer = {"cents": 0, "monate": 12, "buchungen": []}
    assert sz.restbetrag_je_termin(zeile, leer) == -120000
    assert sz.restwirkung_monatlich(zeile, leer) == -10000


def test_a_drop_out_shrinks_as_the_window_moves_past_it():
    """Nach dem Aprilauszug sind nur noch sechs Abo-Monate im Fenster."""
    from datetime import date

    conn = _ledger(*_abo_monatlich(date(2026, 9, 1)))
    wegfall = sz.Line(label="Crunchyroll", amount_cents=0, kind="wegfall",
                      start=date(2026, 10, 1), buchungen=["cr202609"])
    halb = sz.messung(conn, wegfall, _fenster("2026-04-01", "2027-03-31"))
    assert halb["cents"] == -18000
    assert sz.jahreswirkung(wegfall, halb, 2028, 0, date(2028, 12, 1)) == 18000
    vorbei = sz.messung(conn, wegfall, _fenster("2026-10-01", "2027-09-30"))
    assert vorbei["cents"] == 0
    assert sz.jahreswirkung(wegfall, vorbei, 2028, 0, date(2028, 12, 1)) == 0


def test_new_costs_only_add_what_the_window_does_not_hold_yet():
    """Die neue Versicherung ist seit Juni gebucht: drei Monate stecken schon drin."""
    from datetime import date

    conn = _ledger(*[(f"2026-{m:02d}-10", -5000, "huk", "mobilitaet/kfz-versicherung",
                      f"huk{m}") for m in (6, 7, 8)])
    neu = sz.Line(label="HUK", amount_cents=-5000, frequency="monatlich",
                  start=date(2026, 6, 1), category_id="mobilitaet/kfz-versicherung",
                  buchungen=["huk6"])
    m = sz.messung(conn, neu, _fenster("2025-09-01", "2026-08-31"))
    assert m["cents"] == -15000
    # 12 x -50 geplant, 3 x -50 schon in der Basis
    assert sz.jahreswirkung(neu, m, 2027, 0, date(2027, 12, 1)) == -45000
    assert sz.restwirkung_monatlich(neu, m) == -3750


def test_a_booked_one_off_no_longer_counts_and_leaves_the_base():
    from datetime import date

    anzahlung = sz.Line(label="Anzahlung", amount_cents=-2000000, frequency="einmalig",
                        start=date(2027, 3, 1), buchungen=["anz"])
    m = {"cents": 0, "monate": 12, "buchungen": [], "quelle": "zugeordnet"}
    assert sz.jahreswirkung(anzahlung, m, 2027, 0, date(2027, 12, 1)) == 0
    scen = sz.Scenario(id="auto", name="Auto", lines=[anzahlung])
    assert sz.einmalig_zugeordnet([scen]) == {"anz"}


def test_without_assignment_nothing_counts_but_the_category_is_suggested():
    """Ein stiller Rückfall auf die ganze Kategorie verschleierte, woran eine
    Zeile rechnet. Gezählt wird erst, was zugeordnet ist."""
    from datetime import date

    conn = _ledger(("2026-07-05", -2000, "rtl", "abo/video-streaming", "rtl7"),
                   ("2026-08-05", -2000, "dazn", "abo/video-streaming", "dazn8"))
    wegfall = sz.Line(label="Streaming", amount_cents=0, kind="wegfall",
                      start=date(2026, 10, 1), category_id="abo/video-streaming")
    f = _fenster("2025-09-01", "2026-08-31")
    m = sz.messung(conn, wegfall, f)
    assert (m["cents"], m["quelle"]) == (0, "keine")
    assert sz.jahreswirkung(wegfall, m, 2027, 0, date(2027, 12, 1)) == 0
    assert [b["dedup_hash"] for b in sz.vorschlaege(conn, wegfall, f)] == ["dazn8", "rtl7"]
    wegfall.buchungen = ["rtl7"]
    assert [b["dedup_hash"] for b in sz.vorschlaege(conn, wegfall, f)] == ["dazn8"]
