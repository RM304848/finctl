"""Strom: Hochrechnung, Abschlag und die Frage, wann etwas abgerechnet ist."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from finctl import strom as st
from finctl.web.server import app

client = TestClient(app)


def _zeitraum(beginn=date(2026, 1, 1), ende=date(2026, 12, 31), **extra) -> st.Zeitraum:
    werte = {"zaehlerstand_beginn": 1000.0,
             "tarife": [st.Tarif(beginn, 14600, 30.0)],
             "abschlaege": [st.Abschlag(beginn, 10000)]}
    werte.update(extra)
    return st.Zeitraum(beginn, ende, **werte)


# ---------------------------------------------------------------- Rechnung

def test_consumption_is_extrapolated_linearly_to_the_end_of_the_period():
    """181 Tage, 1.810 kWh: zehn am Tag, 3.650 im Jahr."""
    r = st.rechnen(_zeitraum(), [st.Ablesung(date(2026, 7, 1), 2810.0)], date(2026, 9, 18))
    assert r.zustand == "laufend"
    assert (r.kwh_bisher, r.tage_bisher, r.kwh_tag) == (1810.0, 181, 10.0)
    assert r.kwh_erwartet == pytest.approx(3650.0)


def test_the_balance_is_the_sum_of_its_items_and_negative_means_pay_more():
    """1.200 € Abschlag gegen 146 € Grundpreis und 3.650 kWh zu 30 ct."""
    r = st.rechnen(_zeitraum(), [st.Ablesung(date(2026, 7, 1), 2810.0)], date(2026, 9, 18))
    assert [p.cents for p in r.posten] == [120000, -14600, -109500]
    assert r.saldo_cents == -4100


def test_a_credit_is_positive():
    r = st.rechnen(_zeitraum(), [st.Ablesung(date(2026, 7, 1), 1905.0)], date(2026, 9, 18))
    assert r.saldo_cents > 0


def test_what_the_prepayment_covers_and_whether_consumption_stays_inside():
    r = st.rechnen(_zeitraum(), [st.Ablesung(date(2026, 7, 1), 2810.0)], date(2026, 9, 18))
    assert r.kwh_gedeckt == pytest.approx((120000 - 14600) / 30.0)
    assert r.kwh_luft < 0          # darueber
    knapp = st.rechnen(_zeitraum(), [st.Ablesung(date(2026, 7, 1), 2500.0)], date(2026, 9, 18))
    assert knapp.kwh_luft > 0      # im Rahmen


def test_a_price_change_mid_period_splits_base_and_unit_price_by_days():
    z = _zeitraum(tarife=[st.Tarif(date(2026, 1, 1), 14600, 30.0),
                          st.Tarif(date(2026, 7, 1), 29200, 40.0)])
    r = st.rechnen(z, [st.Ablesung(date(2026, 7, 1), 2810.0)], date(2026, 9, 18))
    grund = [p.cents for p in r.posten if p.label.startswith("Grundpreis")]
    arbeit = [p.cents for p in r.posten if p.label.startswith("Arbeitspreis")]
    assert grund == [-round(14600 * 181 / 365), -round(29200 * 184 / 365)]
    assert arbeit == [-round(3650 * 181 / 365 * 30), -round(3650 * 184 / 365 * 40)]


def test_twelve_prepayments_and_a_new_amount_counts_from_its_month():
    z = _zeitraum(abschlaege=[st.Abschlag(date(2026, 1, 1), 10000),
                              st.Abschlag(date(2026, 7, 1), 12000)])
    termine = st.zahlungstermine(z)
    assert len(termine) == 12
    assert [c for _t, c in termine] == [10000] * 6 + [12000] * 6


def test_a_contract_ending_after_five_months_counts_five_prepayments():
    z = _zeitraum(ende=date(2026, 5, 31))
    assert len(st.zahlungstermine(z)) == 5
    r = st.rechnen(z, [st.Ablesung(date(2026, 3, 1), 1590.0)], date(2026, 4, 1))
    assert r.kwh_erwartet == pytest.approx(590 / 59 * 151)


def test_the_recommendation_is_per_year_and_rounded_up_to_whole_euros():
    """(3.650 kWh × 30 ct + 146 €) ÷ 12 = 103,42 € -> 104 €."""
    r = st.rechnen(_zeitraum(), [st.Ablesung(date(2026, 7, 1), 2810.0)], date(2026, 9, 18))
    assert r.empfehlung_cents == 10400
    assert r.empfehlung_grundlage == "eigene Rechnung"
    # Auch ein kurzer Zeitraum empfiehlt auf 365 Tage.
    kurz = st.rechnen(_zeitraum(ende=date(2026, 5, 31)),
                      [st.Ablesung(date(2026, 3, 1), 1590.0)], date(2026, 4, 1))
    assert kurz.empfehlung_cents == 10400


def test_a_price_guarantee_ending_before_the_period_is_flagged():
    z = _zeitraum(preisgarantie_bis=date(2026, 6, 30))
    assert st.rechnen(z, [], date(2026, 3, 1)).garantie_endet_vorher
    z = _zeitraum(preisgarantie_bis=date(2027, 6, 30))
    assert not st.rechnen(z, [], date(2026, 3, 1)).garantie_endet_vorher


# ------------------------------------------------------ Abgerechnet ist erst,
# ------------------------------------------------------ was abgehakt ist

def test_a_reading_on_the_cutoff_date_is_not_a_settlement():
    r = st.rechnen(_zeitraum(), [st.Ablesung(date(2027, 1, 1), 4650.0)], date(2027, 2, 1))
    assert r.zustand == "ausstehend"
    assert r.abgelesen_bis_ende
    assert r.kwh_erwartet == pytest.approx(3650.0)
    assert r.abweichung_cents is None


def test_only_the_tick_makes_the_suppliers_figures_count():
    z = _zeitraum(abgerechnet=True, endabrechnung_kwh=4015.0, endabrechnung_cents=-15000)
    r = st.rechnen(z, [st.Ablesung(date(2027, 1, 1), 4650.0)], date(2027, 2, 1))
    assert r.zustand == "abgerechnet"
    assert r.empfehlung_grundlage == "abgerechnet"
    # 4.015 kWh im Jahr: (4.015 × 30 + 14.600) / 12 = 11.254 Cent -> 113 €
    assert r.empfehlung_cents == 11300
    assert r.abweichung_cents == -15000 - r.saldo_cents


def test_the_next_period_takes_the_cutoff_reading_and_the_last_tariff():
    z = _zeitraum(tarife=[st.Tarif(date(2026, 1, 1), 14600, 30.0),
                          st.Tarif(date(2026, 10, 1), 15000, 32.0)])
    neu = st.naechster_zeitraum(z, [st.Ablesung(date(2027, 1, 1), 4650.0)], date(2027, 2, 1))
    assert (neu.beginn, neu.ende) == (date(2027, 1, 1), date(2027, 12, 31))
    assert neu.zaehlerstand_beginn == 4650.0 and not neu.anfangsstand_geschaetzt
    assert neu.tarife == [st.Tarif(date(2027, 1, 1), 15000, 32.0)]
    assert neu.abschlaege[0].monat_cents == st.rechnen(
        z, [st.Ablesung(date(2027, 1, 1), 4650.0)], date(2027, 2, 1)).empfehlung_cents


def test_without_a_cutoff_reading_the_opening_reading_is_estimated_and_marked():
    neu = st.naechster_zeitraum(_zeitraum(), [st.Ablesung(date(2026, 7, 1), 2810.0)],
                                date(2027, 2, 1))
    assert neu.anfangsstand_geschaetzt
    assert neu.zaehlerstand_beginn == pytest.approx(1000 + 3650, abs=0.1)


def test_a_running_period_has_no_successor_yet():
    with pytest.raises(ValueError, match="läuft noch"):
        st.naechster_zeitraum(_zeitraum(), [], date(2026, 6, 1))


# ---------------------------------------------------------------- Pruefung

@pytest.mark.parametrize(("neu", "meldung"), [
    (st.Ablesung(date(2026, 8, 1), 1500.0), "unter dem vorigen"),
    (st.Ablesung(date(2026, 12, 1), 5000.0), "Zukunft"),
    (st.Ablesung(date(2025, 6, 1), 900.0), "keinem Abrechnungszeitraum"),
    (st.Ablesung(date(2026, 7, 1), 3000.0), "schon eine Ablesung"),
])
def test_a_reading_that_cannot_be_right_is_refused(neu, meldung):
    with pytest.raises(ValueError, match=meldung):
        st.ablesung_pruefen([_zeitraum()], [st.Ablesung(date(2026, 7, 1), 2810.0)],
                            neu, date(2026, 9, 18))


def test_a_period_must_start_with_a_tariff_and_not_overlap_another():
    with pytest.raises(ValueError, match="spätestens am Beginn"):
        st.zeitraum_pruefen(_zeitraum(tarife=[st.Tarif(date(2026, 2, 1), 0, 30.0)]), [])
    with pytest.raises(ValueError, match="Überschneidet"):
        st.zeitraum_pruefen(_zeitraum(), [_zeitraum(beginn=date(2026, 6, 1),
                                                    ende=date(2027, 5, 31))])


# -------------------------------------------------------------------- Web

@pytest.fixture
def datei(tmp_path, monkeypatch):
    pfad = tmp_path / "strom.yaml"
    monkeypatch.setattr(st, "PFAD", pfad)
    return pfad


def _anlegen():
    heute = date.today()
    beginn = heute - timedelta(days=100)
    return client.post("/api/strom/zeitraum", json={"nr": None, "zeitraum": {
        "beginn": beginn.isoformat(),
        "ende": (beginn + timedelta(days=364)).isoformat(),
        "zaehlerstand_beginn": 1000,
        "tarife": [{"ab": beginn.isoformat(), "grundpreis_jahr_cents": 14600,
                    "arbeitspreis_ct_kwh": 30}],
        "abschlaege": [{"ab": beginn.isoformat(), "monat_cents": 10000}]}})


def test_the_page_opens_without_a_file_and_offers_the_first_period(datei):
    seite = client.get("/strom")
    assert seite.status_code == 200
    assert "Erster Abrechnungszeitraum" in seite.text
    assert not datei.exists()


def test_a_reading_saved_through_the_page_lands_in_the_file_and_on_the_page(datei):
    assert _anlegen().status_code == 200
    gestern = (date.today() - timedelta(days=1)).isoformat()
    antwort = client.post("/api/strom/ablesung", json={"datum": gestern, "stand": 1990})
    assert antwort.status_code == 200, antwort.text
    roh = yaml.safe_load(datei.read_text(encoding="utf-8"))
    assert roh["ablesungen"] == [{"datum": gestern, "stand": 1990.0}]
    seite = client.get("/strom").text
    assert "990 kWh" in seite and "Rechenweg" in seite


def test_a_refused_reading_answers_in_german_and_writes_nothing(datei):
    _anlegen()
    vorher = datei.read_text(encoding="utf-8")
    antwort = client.post("/api/strom/ablesung",
                          json={"datum": date.today().isoformat(), "stand": 10})
    assert antwort.status_code == 400
    assert "unter dem vorigen" in antwort.json()["error"]
    assert datei.read_text(encoding="utf-8") == vorher


def test_energy_sits_in_the_contracts_group():
    kopf = client.get("/energie").text
    kopf = kopf[kopf.index("<header>"):kopf.index("</header>")]
    gruppe = kopf[kopf.index(">Verträge<"):]
    assert 'href="/energie"' in gruppe[:gruppe.index("</span>\n  </span>")]
    r = client.get("/strom", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/energie?ansicht=strom"


def test_the_guarantee_hint_goes_once_the_new_price_is_entered():
    z = _zeitraum(preisgarantie_bis=date(2026, 6, 30),
                  tarife=[st.Tarif(date(2026, 1, 1), 14600, 30.0),
                          st.Tarif(date(2026, 7, 1), 16000, 35.0)])
    assert not st.rechnen(z, [], date(2026, 3, 1)).garantie_endet_vorher


def test_a_tariff_or_prepayment_that_started_before_the_period_applies_from_its_start():
    """Vom Vertrag abgeschrieben: der Abschlag lief schon, bevor der Zeitraum begann."""
    z = _zeitraum(beginn=date(2025, 11, 14), ende=date(2026, 11, 13),
                  tarife=[st.Tarif(date(2025, 11, 1), 18624, 31.45)],
                  abschlaege=[st.Abschlag(date(2024, 11, 15), 4633)])
    st.zeitraum_pruefen(z, [])
    assert [c for _t, c in st.zahlungstermine(z)] == [4633] * 12
    r = st.rechnen(z, [st.Ablesung(date(2026, 5, 14), 1500.0)], date(2026, 9, 18))
    assert sum(p.cents for p in r.posten if p.label.startswith("Grundpreis")) == -18624
    assert r.posten[1].label == "Grundpreis ab 2025-11-14"
    with pytest.raises(ValueError, match="nach dem Ende"):
        st.zeitraum_pruefen(_zeitraum(abschlaege=[st.Abschlag(date(2027, 2, 1), 100)]), [])


def test_the_page_asks_for_the_base_price_per_month():
    seite = (Path("finctl/web/templates/energie.html")).read_text(encoding="utf-8")
    assert "Grundpreis €/Monat" in seite
    assert "centsAus(tr.querySelector('.t-grund').value) * 12" in seite


# ------------------------------------------------------------ Heizprofil

def test_a_heating_meter_read_in_autumn_expects_the_winter_still_to_come():
    """Linear haette den Oktober aufs Jahr hochgerechnet und den Winter
    verschluckt. Mit Gradtagen zaehlt ein Sommerhalbjahr wenig."""
    from finctl.energie import profil
    from finctl.energie.zaehler import Messung

    heizung = Messung(einheit="kWh", profil="heizung")
    z = _zeitraum()
    ablesung = [st.Ablesung(date(2026, 10, 1), 1000.0 + 3000.0)]
    linear = st.rechnen(z, ablesung, date(2026, 10, 2))
    mit_winter = st.rechnen(z, ablesung, date(2026, 10, 2), heizung)
    anteil = profil.gewicht(date(2026, 1, 1), 273, "heizung")
    assert mit_winter.kwh_erwartet == pytest.approx(3000.0 / anteil)
    assert mit_winter.kwh_erwartet > linear.kwh_erwartet


def test_the_weights_of_a_year_add_up_to_one_whatever_the_base_load():
    from finctl.energie import profil

    for grundlast in (0.0, 0.2, 1.0):
        assert profil.gewicht(date(2026, 1, 1), 365, "heizung", grundlast) == pytest.approx(1.0)
        schaltjahr = profil.gewicht(date(2028, 1, 1), 366, "heizung", grundlast)
        assert schaltjahr == pytest.approx(1.0, abs=0.003)


def test_gas_is_billed_in_kwh_from_cubic_metres():
    """1.000 m³ bei Brennwert 11 und Zustandszahl 0,95 sind 10.450 kWh."""
    from finctl.energie.zaehler import Messung

    gas = Messung(einheit="m³", abrechnung="kWh", faktor=11 * 0.95)
    z = _zeitraum()
    r = st.rechnen(z, [st.Ablesung(date(2027, 1, 1), 2000.0)], date(2027, 1, 2), gas)
    assert r.kwh_erwartet == pytest.approx(10450.0)
    assert "ct/kWh" in r.posten[-1].herleitung
