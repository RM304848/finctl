"""Fristen: was bald faellig ist, und wie es in den Kalender kommt.

Vertraege und Kredite sind erfunden; gebaut wird mit derselben Funktion,
die beim Laden der Dateien prueft.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from finctl import abos, fristen

HEUTE = date(2030, 1, 15)


def _vertrag(**felder) -> dict:
    return abos.normalisieren({
        "id": "beispiel", "name": "Beispielvertrag", "kategorie": "abo/beispiel",
        "konto": "giro", "betrag_cents": -12000, "takt": 12, "faellig": "2030-02-01",
        **felder})


def _kredit(**felder) -> dict:
    return {"id": "001", "name": "Beispielkredit", "status": "active",
            "zinsbindung_end": "2030-12-31",
            "segments": [{"start": "2020-01-01", "end": "2030-12-31"}], **felder}


def test_a_yearly_payment_is_due_soon_a_month_ahead():
    [f] = fristen.aus_vertraegen([_vertrag()], HEUTE)
    assert (f.art, f.datum, f.cents) == ("zahlung", date(2030, 2, 1), -12000)
    assert f.ab == date(2030, 1, 2)
    assert f.bald(HEUTE)
    assert not f.bald(date(2030, 1, 1))
    assert not f.bald(date(2030, 2, 2))


def test_monthly_payments_are_everyday_life_not_deadlines():
    assert fristen.aus_vertraegen([_vertrag(takt=1)], HEUTE) == []
    quartal = fristen.aus_vertraegen([_vertrag(takt=3)], HEUTE)
    assert [f.datum for f in quartal] == [date(2030, m, 1) for m in (2, 5, 8, 11)]


def test_the_end_of_a_prepaid_period_is_one_deadline_not_two():
    """Die Zahlung am Vorratsende ist die Verlaengerung selbst."""
    vertrag = _vertrag(faellig="2030-03-01", vorrat_bis="2030-03-01")
    assert [(f.art, f.datum) for f in fristen.aus_vertraegen([vertrag], HEUTE)] == [
        ("vorrat", date(2030, 3, 1))]


def test_a_cancelled_contract_has_no_further_payments():
    vertrag = _vertrag(takt=3, gekuendigt_zum="2030-03-31")
    assert [f.datum for f in fristen.aus_vertraegen([vertrag], HEUTE)] == [date(2030, 2, 1)]


def test_a_fixed_rate_period_is_due_a_year_ahead_and_says_whether_a_followup_exists():
    [f] = fristen.aus_krediten([_kredit()], HEUTE, set())
    assert f.ab == date(2029, 12, 31)
    assert f.bald(HEUTE)
    assert "einholen" in f.tun
    [f] = fristen.aus_krediten([_kredit()], HEUTE, {"001"})
    assert "unterschreiben" in f.tun
    angebot = _kredit(segments=[{"start": "2020-01-01", "end": "2030-12-31"},
                                {"start": "2031-01-01", "end": "2040-12-31"}])
    assert "unterschreiben" in fristen.aus_krediten([angebot], HEUTE, set())[0].tun


def test_planned_closed_and_past_loans_have_no_deadline():
    kredite = [_kredit(szenario="eigenheim"), _kredit(status="closed"),
               _kredit(zinsbindung_end="2029-12-31"), _kredit(zinsbindung_end=None)]
    assert fristen.aus_krediten(kredite, HEUTE, set()) == []


def test_the_calendar_reminds_on_the_day_to_act_and_folds_long_lines():
    """Ganztaegig am Tag, ab dem es bald ist; liegt der zurueck, heute."""
    lang = _vertrag(name="Ein langer Vertragsname, mit Komma; und Semikolon " * 2)
    [f] = fristen.aus_vertraegen([lang], HEUTE)
    jetzt = datetime(2030, 1, 15, 8, 0, tzinfo=UTC)
    text = fristen.ics([f], HEUTE, jetzt=jetzt).decode("utf-8")
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert all(len(z.encode("utf-8")) <= 75 for z in text.split("\r\n"))
    entfaltet = text.replace("\r\n ", "")
    assert "DTSTART;VALUE=DATE:20300115" in entfaltet
    assert "\\, mit Komma\\; und Semikolon" in entfaltet
    assert "UID:zahlung-beispiel-20300201@finctl.local" in entfaltet
    frueher = fristen.ics([f], date(2029, 12, 1), jetzt=jetzt).decode("utf-8")
    assert "DTSTART;VALUE=DATE:20300102" in frueher
