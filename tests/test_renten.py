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
