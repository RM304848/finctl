"""Rentenquellen: Herleitung in renten.yaml, Betrag und Stand aus dem
Monatsabschluss. Alle Quellen hier sind erfunden."""

from __future__ import annotations

from datetime import date

import pytest
import yaml

from finctl import renten

BASIS = """renten:
  - id: drv
    name: Gesetzliche Rente
    art: rente
    kaufkraft: heute
    cents: 150000
    ab: 2050-01
    stand: 2026-02-01
    # Die Herleitung, die kein Klick loeschen darf.
    herleitung: Renteninformation.
  - id: police
    name: Fondspolice
    art: kapital
    kaufkraft: nominal
    cents: 5000000
    ab: 2045-06
"""


def _config(tmp_path):
    (tmp_path / "renten.yaml").write_text(BASIS, encoding="utf-8")
    return tmp_path


def test_todays_money_is_grown_to_the_start_nominal_stays(tmp_path):
    """Die DRV rechnet ohne Rentenanpassungen, eine Standmitteilung meist
    nominal. Gewandelt wird beim Rechnen, nicht in der Quelle."""
    drv, police = renten.quellen(_config(tmp_path))
    assert renten.nominal_cents(drv, 2026, 0.02) == 150000
    # 24 Jahre bei 2 %: Faktor 1,61.
    assert renten.nominal_cents(drv, 2050, 0.02) == round(150000 * 1.02 ** 24)
    assert renten.nominal_cents(police, 2045, 0.02) == 5000000


def test_a_letter_is_current_in_its_year(tmp_path):
    drv, police = renten.quellen(_config(tmp_path))
    assert drv.aktuell(date(2026, 9, 1)) and not drv.aktuell(date(2027, 1, 1))
    assert not police.aktuell(date(2026, 9, 1))      # ohne Stand


def test_the_monthly_close_writes_only_what_differs(tmp_path):
    cfg = _config(tmp_path)
    renten.setzen("drv", {"cents": 150000, "stand": "2026-03-01"}, cfg)
    roh = yaml.safe_load((cfg / "renten_custom.yaml").read_text(encoding="utf-8"))
    assert roh["renten"] == {"drv": {"stand": "2026-03-01"}}
    renten.setzen("drv", {"cents": 160000}, cfg)
    drv = renten.quellen(cfg)[0]
    assert (drv.cents, drv.stand, drv.eigen) == (160000, date(2026, 3, 1), True)
    renten.setzen("drv", {"entfernen": True}, cfg)
    drv = renten.quellen(cfg)[0]
    assert (drv.cents, drv.eigen) == (150000, False)
    assert "Die Herleitung" in (cfg / "renten.yaml").read_text(encoding="utf-8")


def test_without_its_own_date_a_pension_starts_with_retirement(tmp_path, monkeypatch):
    from finctl import person

    monkeypatch.setattr(person, "rentenbeginn", lambda: date(2061, 3, 1))
    q = renten.Quelle(id="x", name="x", art="rente", cents=1)
    assert renten.beginn(q) == date(2061, 3, 1)


@pytest.mark.parametrize("felder", [{"cents": -1}, {"stand": "2999-01-01"}])
def test_wrong_input_is_refused(tmp_path, felder):
    with pytest.raises(ValueError):
        renten.setzen("drv", felder, _config(tmp_path))


def test_the_payout_start_is_chosen_in_the_monthly_close(tmp_path):
    """Eine Police zahlt oft wahlweise ab 62 oder ab 67: das "Bezug ab" traegt
    man selbst ein. Leer heisst Rentenbeginn aus der Einrichtung."""
    cfg = _config(tmp_path)
    renten.setzen("police", {"ab": "2050-02"}, cfg)
    assert renten.quellen(cfg)[1].ab == date(2050, 2, 1)
    renten.setzen("police", {"ab": ""}, cfg)
    assert renten.quellen(cfg)[1].ab == date(2045, 6, 1)
    with pytest.raises(ValueError):
        renten.setzen("police", {"ab": "62"}, cfg)


# -------------------------------------------------- anlegen, Effektivkosten

def test_a_newcomer_creates_a_pension_without_any_file(tmp_path):
    """Ohne renten.yaml: angelegt wird im Overlay, immer nominal."""
    kennung = renten.anlegen({"name": "Gesetzliche Rente", "art": "rente",
                              "cents": 150_000}, tmp_path)
    [q] = renten.quellen(tmp_path)
    assert (q.id, q.kaufkraft, q.cents, q.angelegt) == (kennung, "nominal", 150_000, True)
    assert kennung == "gesetzliche-rente"


def test_a_created_pension_takes_amount_updates_and_can_be_deleted(tmp_path):
    kennung = renten.anlegen({"name": "Police", "art": "kapital", "cents": 5_000_000,
                              "ab": "2050-01", "kosten_pa": 0.018}, tmp_path)
    renten.setzen(kennung, {"cents": 5_500_000}, tmp_path)
    [q] = renten.quellen(tmp_path)
    assert (q.cents, q.ab, q.kosten_pa) == (5_500_000, date(2050, 1, 1), 0.018)
    renten.loeschen(kennung, tmp_path)
    assert renten.quellen(tmp_path) == []


def test_a_pension_from_the_base_file_is_not_deleted_here(tmp_path):
    with pytest.raises(ValueError):
        renten.loeschen("drv", _config(tmp_path))


def test_two_pensions_with_the_same_name_get_two_ids(tmp_path):
    a = renten.anlegen({"name": "Police", "cents": 1}, tmp_path)
    b = renten.anlegen({"name": "Police", "cents": 1}, tmp_path)
    assert (a, b) == ("police", "police-2")


@pytest.mark.parametrize("felder", [
    {"name": "", "cents": 1}, {"name": "x"}, {"name": "x", "cents": -5},
    {"name": "x", "cents": 1, "art": "lotto"}, {"name": "x", "cents": 1, "ab": "2050"},
    {"name": "x", "cents": 1, "kosten_pa": 0.5}])
def test_a_pension_with_wrong_input_is_refused(tmp_path, felder):
    with pytest.raises(ValueError):
        renten.anlegen(felder, tmp_path)


def test_costs_are_set_and_cleared_on_an_existing_source(tmp_path):
    cfg = _config(tmp_path)
    renten.setzen("drv", {"kosten_pa": 0.015}, cfg)
    assert renten.quellen(cfg)[0].kosten_pa == 0.015
    renten.setzen("drv", {"kosten_pa": ""}, cfg)
    assert renten.quellen(cfg)[0].kosten_pa is None


def test_costs_above_the_threshold_are_flagged_on_the_monthly_close(monkeypatch):
    from finctl import assumptions as ann
    from finctl.web.routen import auswertung

    teuer = renten.Quelle(id="p", name="Police", art="kapital", cents=1, kosten_pa=0.02)
    guenstig = renten.Quelle(id="q", name="Fonds", art="kapital", cents=1, kosten_pa=0.01)
    monkeypatch.setattr(renten, "quellen", lambda *a, **k: [teuer, guenstig])
    monkeypatch.setattr(ann, "kostenschwelle_pa", lambda *a, **k: 0.013)

    class P:
        gruppe = "renten"

        def __init__(self, schluessel):
            self.schluessel = schluessel

    zeilen = auswertung._rentenzeilen([P("p"), P("q")])
    assert [z["kosten_hoch"] for z in zeilen] == [True, False]
