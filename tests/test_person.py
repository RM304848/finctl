"""Wer plant: Geburtstag, Krankenversicherung, Rentenalter.

Alle Personen hier sind erfunden.
"""

from __future__ import annotations

from datetime import date

import pytest
import yaml

from finctl import person

BASIS = """person:
  # Die Herleitung, die kein Klick loeschen darf.
  birth_date: 1980-07-15
  krankenversicherung: gesetzlich
schwellen:
  grv_ab_alter: 67
"""


def _config(tmp_path):
    (tmp_path / "lebensplan.yaml").write_text(BASIS, encoding="utf-8")
    return tmp_path


def test_the_pension_starts_the_month_after_the_birthday(tmp_path):
    assert person.rentenbeginn(_config(tmp_path)) == date(2047, 8, 1)


def test_only_the_privately_insured_have_a_deadline(tmp_path):
    """§6 Abs. 3a SGB V gilt nur fuer den Weg zurueck in die GKV."""
    cfg = _config(tmp_path)
    assert person.pkv_stichtag(cfg) is None
    person.setzen({"krankenversicherung": "privat"}, cfg)
    assert person.pkv_sperre(cfg) == date(2035, 7, 15)
    assert person.pkv_stichtag(cfg) == date(2034, 7, 15)


def test_the_deadline_follows_the_birthday(tmp_path):
    """Dynamisch: ein anderes Geburtsdatum, eine andere Frist."""
    cfg = _config(tmp_path)
    person.setzen({"krankenversicherung": "privat", "geburtsdatum": "1990-03-01"}, cfg)
    assert person.pkv_stichtag(cfg) == date(2044, 3, 1)


def test_the_setup_writes_only_what_differs(tmp_path):
    cfg = _config(tmp_path)
    person.setzen({"geburtsdatum": "1980-07-15", "krankenversicherung": "privat",
                   "rente_ab_alter": "67"}, cfg)
    roh = yaml.safe_load((cfg / "lebensplan_custom.yaml").read_text(encoding="utf-8"))
    assert roh == {"person": {"krankenversicherung": "privat"}}
    person.setzen({"krankenversicherung": "gesetzlich"}, cfg)
    roh = yaml.safe_load((cfg / "lebensplan_custom.yaml").read_text(encoding="utf-8"))
    assert "person" not in (roh or {})
    assert "Die Herleitung" in (cfg / "lebensplan.yaml").read_text(encoding="utf-8")


def test_without_a_birthday_nothing_is_dated(tmp_path):
    (tmp_path / "lebensplan.yaml").write_text("person: {}\n", encoding="utf-8")
    assert person.rentenbeginn(tmp_path) is None
    assert person.pkv_stichtag(tmp_path) is None


@pytest.mark.parametrize("felder", [{"geburtsdatum": "2999-01-01"},
                                    {"krankenversicherung": "beides"},
                                    {"rente_ab_alter": "30"}])
def test_wrong_input_is_refused(tmp_path, felder):
    with pytest.raises(ValueError):
        person.setzen(felder, _config(tmp_path))
