"""Vorraete: Verbrauch aus Peilungen, Reichweite, monatliche Ruecklage."""

from __future__ import annotations

from datetime import date

import pytest

from finctl.energie import profil
from finctl.energie import vorrat as vr


def _tank(**extra) -> vr.Vorrat:
    werte = {"id": "tank", "art": "heizoel", "name": "Heizöl", "einheit": "l",
             "kapazitaet": 3000.0, "profil": "linear",
             "lieferungen": [vr.Lieferung(date(2025, 10, 1), 2000.0, 200000)],
             "staende": [vr.Stand(date(2025, 1, 1), 1500.0),
                         vr.Stand(date(2026, 1, 1), 1500.0)]}
    werte.update(extra)
    return vr.Vorrat(**werte)


def test_consumption_is_what_was_there_plus_what_came_minus_what_is_left():
    """1.500 + 2.000 - 1.500: ein Jahr, 2.000 Liter."""
    a = vr.auswerten(_tank(), date(2026, 1, 1))
    assert [x.verbrauch for x in a.abschnitte] == [2000.0]
    assert a.jahresverbrauch == pytest.approx(2000.0)


def test_the_monthly_reserve_uses_the_price_of_the_last_delivery_rounded_up():
    """2.000 l zu 1,00 €/l sind 2.000 € im Jahr: 166,67 € -> 167 € im Monat."""
    a = vr.auswerten(_tank(), date(2026, 1, 1))
    assert a.preis_ct == pytest.approx(100.0)
    assert a.jahreskosten_cents == 200000
    assert a.ruecklage_monat_cents == 16700


def test_the_range_runs_until_the_minimum_stock():
    """1.500 l bei 2.000 l im Jahr, linear: 1.000 l bis zum Mindestbestand von
    500 l sind ein halbes Jahr."""
    a = vr.auswerten(_tank(mindestbestand=500.0), date(2026, 1, 1))
    assert a.reicht_bis == date(2026, 1, 1) + (date(2026, 7, 3) - date(2026, 1, 1))
    assert not a.reicht_lange


def test_a_summer_reading_does_not_look_like_a_frugal_year_with_the_heating_profile():
    """Mai bis September: kaum Heizarbeit. Linear waeren 100 Liter in fuenf
    Monaten 240 im Jahr; mit Gradtagen ist es ein Vielfaches."""
    tank = _tank(profil="heizung", lieferungen=[],
                 staende=[vr.Stand(date(2026, 5, 1), 1100.0), vr.Stand(date(2026, 10, 1), 1000.0)])
    a = vr.auswerten(tank, date(2026, 10, 1))
    anteil = profil.gewicht(date(2026, 5, 1), 153, "heizung")
    assert a.jahresverbrauch == pytest.approx(100.0 / anteil)
    assert a.jahresverbrauch > 100.0 * 365 / 153


def test_today_is_extrapolated_from_the_last_reading_and_later_deliveries():
    tank = _tank(lieferungen=[vr.Lieferung(date(2025, 10, 1), 2000.0, 200000),
                              vr.Lieferung(date(2026, 2, 1), 500.0, 55000)])
    a = vr.auswerten(tank, date(2026, 3, 1))
    verbraucht = 2000.0 * 59 / 365
    assert a.stand_heute == pytest.approx(1500.0 + 500.0 - verbraucht)
    assert a.preis_ct == pytest.approx(110.0)


def test_without_two_readings_nothing_is_guessed():
    a = vr.auswerten(_tank(staende=[vr.Stand(date(2026, 1, 1), 1500.0)]), date(2026, 3, 1))
    assert a.jahresverbrauch is None and a.reicht_bis is None and a.ruecklage_monat_cents is None


@pytest.mark.parametrize(("tank", "meldung"), [
    (dict(staende=[vr.Stand(date(2026, 1, 1), 3500.0)]), "über der Kapazität"),
    (dict(staende=[vr.Stand(date(2026, 1, 1), 100.0), vr.Stand(date(2026, 2, 1), 900.0)],
          lieferungen=[]), "fehlt eine Lieferung"),
    (dict(lieferungen=[vr.Lieferung(date(2026, 1, 1), 0.0, 100)]), "Menge über null"),
    (dict(grundlast=1.5), "Grundlastanteil"),
])
def test_what_cannot_be_right_is_refused(tank, meldung):
    with pytest.raises(ValueError, match=meldung):
        vr.pruefen(_tank(**tank))


def test_it_survives_the_round_trip_through_yaml():
    tank = _tank(mindestbestand=300.0, grundlast=0.1, profil="heizung")
    zurueck = vr.aus_roh("tank", vr.als_roh(tank))
    assert zurueck == tank
