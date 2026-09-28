"""Ruhestand in der Hochrechnung: Gehalt endet, Renten kommen, Policen zahlen
aus, das Depot deckt die Luecke. Alle Zahlen hier sind erfunden."""

from __future__ import annotations

from datetime import date

from finctl.forecast import jahre as jm
from finctl.renten import Quelle


def _rente(**kw) -> Quelle:
    werte = {"id": "drv", "name": "Gesetzliche Rente", "art": "rente", "cents": 100000,
             "kaufkraft": "heute", "ab": date(2050, 4, 1), "stand": date(2026, 1, 1)}
    werte.update(kw)
    return Quelle(**werte)


def test_salary_stops_the_month_before_retirement():
    assert jm._arbeitsmonate(2050, 0, date(2050, 4, 1)) == 3
    assert jm._arbeitsmonate(2051, 0, date(2050, 4, 1)) == 0
    assert jm._arbeitsmonate(2049, 0, None) == 12


def test_a_pension_grows_with_inflation_and_loses_the_deduction():
    [p] = jm._rentenposten([_rente()], 2050, 0, 0.02, 0.25)
    je_monat = round(100000 * 1.02 ** 24)
    assert p.cents == round(je_monat * 0.75) * 9          # April bis Dezember
    assert p.quelle == "vertrag" and p.verweis == "rente:drv"
    assert jm._rentenposten([_rente()], 2049, 0, 0.02, 0.25) == []


def test_a_policy_paid_as_capital_moves_its_share_to_the_depot():
    """Umschichtung, kein Geldfluss: das Vermoegen bleibt gleich."""
    anteile = {"police": 50_000_00, "andere": 20_000_00}
    q = _rente(id="p", art="kapital", konto="police", ab=date(2045, 6, 1))
    verrentet, info, ins_depot, aufs_tagesgeld = jm._policenwechsel([q], 2045, anteile)
    assert (verrentet, ins_depot, aufs_tagesgeld) == ([], 50_000_00, 0)
    assert info[0].cents == 50_000_00
    assert anteile == {"police": 0, "andere": 20_000_00}


def test_a_capital_payout_pays_the_pension_deduction_from_the_tagesgeld():
    """Bewusst vorsichtig: derselbe Abzug wie auf eine Rente, ohne Schichten."""
    anteile = {"police": 50_000_00}
    q = _rente(id="p", art="kapital", konto="police", ab=date(2045, 6, 1))
    [abzug], _info, ins_depot, _tg = jm._policenwechsel([q], 2045, anteile, 0.25)
    assert abzug.cents == -12_500_00 and abzug.einmalig
    assert ins_depot == 50_000_00, "der Wert geht ganz ins Depot, der Abzug ins Tagesgeld"


def test_a_capital_without_a_policy_arrives_after_the_deduction():
    q = _rente(id="k", art="kapital", ab=date(2045, 6, 1))
    [p] = jm._rentenposten([q], 2045, 0, 0.0, 0.25)
    assert p.cents == round(q.cents * 0.75)


def test_a_policy_paid_as_pension_leaves_the_capital():
    anteile = {"police": 50_000_00}
    q = _rente(id="p", konto="police", ab=date(2045, 6, 1))
    verrentet, _info, ins_depot, aufs_tagesgeld = jm._policenwechsel([q], 2045, anteile)
    assert verrentet[0].cents == -50_000_00 and verrentet[0].einmalig
    assert (ins_depot, aufs_tagesgeld) == (0, 50_000_00)


def test_in_retirement_the_depot_refills_the_buffer():
    """Vor der Rente deckt sich ein Minus nicht von selbst -- danach ist genau
    das der Plan."""
    args = {"sparrate": -30_000_00, "grenze": 10_000_00, "satz_tagesgeld": 0.0,
            "satz_depot": 0.0, "vorab_satz": 0.0, "steuer_quote": 0.0}
    davor = jm.kaskade(10_000_00, 100_000_00, 0, **args)
    assert davor["tagesgeld"] == -20_000_00 and davor["ins_depot"] == 0
    danach = jm.kaskade(10_000_00, 100_000_00, 0, entnahme=True, **args)
    assert danach["tagesgeld"] == 10_000_00
    assert danach["depot"] == 70_000_00 and danach["ins_depot"] == -30_000_00
    leer = jm.kaskade(10_000_00, 5_000_00, 0, entnahme=True, **args)
    assert leer["tagesgeld"] == -15_000_00 and leer["depot"] == 0


def test_the_capital_lasts_until_the_buffer_goes_negative():
    lauf = jm.Projection(years=[jm.Year(year=y, tagesgeld_cents=t) for y, t in
                                ((2080, 5), (2081, 1), (2082, -1))])
    assert lauf.reicht_bis == 2081
    assert jm.Projection(years=[jm.Year(year=2080, tagesgeld_cents=1)]).reicht_bis is None
