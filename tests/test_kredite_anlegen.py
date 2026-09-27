"""Kredite anlegen und wieder loswerden.

Wer dieses Werkzeug uebernimmt, erbt sonst fremde Vertraege in seiner
Prognose -- und eine Rate, die er nie zahlt, drueckt den Tiefpunkt jedes
Monats. Entfernen ist deshalb der wichtigere der beiden Wege.

Testdaten sind erfunden.
"""

from __future__ import annotations

import pytest
import yaml

from finctl import kredite
from finctl.realestate.loan import lade_kredite

RATE = {"id": "ratenkredit", "name": "Möbel", "lender": "Beispielbank",
        "servicing_account_id": "giro", "opening_balance_cents": 480000,
        "annuity_cents": 15000, "annual_rate_pct": 6.0,
        "start": "2026-01-01", "ende": "2029-01-01"}


@pytest.fixture
def konfig(tmp_path):
    """Eine Basisdatei mit einem Kredit und einer Begruendung darin."""
    ordner = tmp_path / "config"
    ordner.mkdir()
    (ordner / kredite.BASIS).write_text(
        "# Warum dieser Kredit so gerechnet wird.\n"
        + yaml.safe_dump({"loans": [{"id": "geerbt", "name": "Geerbt",
                                     "lender": "Fremdbank", "status": "active"}]}),
        encoding="utf-8")
    return ordner


def test_a_new_loan_is_a_loan_the_schedule_can_compute(konfig):
    """Neun Angaben ergeben ein Segment -- mehr braucht ein Ratenkredit nicht."""
    from finctl.realestate.loan import segments_from

    kredite.anlegen(dict(RATE), konfig)
    [neu] = [k for k in lade_kredite(konfig) if k["id"] == "ratenkredit"]
    [seg] = segments_from(neu)
    assert (seg.annual_rate_pct, seg.annuity_cents) == (6.0, 15000)
    assert seg.opening_balance_cents == 480000


def test_the_hand_written_file_is_never_rewritten(konfig):
    vorher = (konfig / kredite.BASIS).read_text(encoding="utf-8")
    kredite.anlegen(dict(RATE), konfig)
    kredite.entfernen("geerbt", konfig)
    assert (konfig / kredite.BASIS).read_text(encoding="utf-8") == vorher


def test_an_inherited_loan_can_be_removed(konfig):
    """Der Punkt der ganzen Uebung: fremde Vertraege muessen weg koennen."""
    assert [k["id"] for k in lade_kredite(konfig)] == ["geerbt"]
    kredite.entfernen("geerbt", konfig)
    assert lade_kredite(konfig) == []


def test_removing_an_own_loan_leaves_no_trace(konfig):
    """Was die Seite selbst angelegt hat, wird geloescht statt ausgeblendet --
    ein `entfernt: true` auf einen Eintrag, den niemand begruendet hat, waere
    nur Altpapier in der Datei."""
    kredite.anlegen(dict(RATE), konfig)
    kredite.entfernen("ratenkredit", konfig)
    eigen = yaml.safe_load((konfig / kredite.EIGEN).read_text(encoding="utf-8"))
    assert "ratenkredit" not in (eigen.get("kredite") or {})


def test_removing_what_is_not_there_is_refused(konfig):
    with pytest.raises(ValueError, match=r"Kein Kredit"):
        kredite.entfernen("erfunden", konfig)


def test_the_scenario_templates_survive_a_write(konfig):
    """`vorlagen:` steht in derselben Datei. Ein Schreiben, das sie verliert,
    naehme szenariogebundenen Krediten ihre Konditionen."""
    (konfig / kredite.EIGEN).write_text(
        yaml.safe_dump({"vorlagen": {"900": {"annual_rate_pct": 4.5}}}),
        encoding="utf-8")
    kredite.anlegen(dict(RATE), konfig)
    eigen = yaml.safe_load((konfig / kredite.EIGEN).read_text(encoding="utf-8"))
    assert eigen["vorlagen"]["900"]["annual_rate_pct"] == 4.5


@pytest.mark.parametrize("aenderung, wort", [
    ({"id": "Mit Großbuchstaben"}, "Kennung"),
    ({"id": "geerbt"}, "vergeben"),
    ({"name": ""}, "Name"),
    ({"lender": ""}, "Gläubiger"),
    ({"servicing_account_id": None}, "Konto"),
    ({"opening_balance_cents": 0}, "Restschuld"),
    ({"annuity_cents": 0}, "Monatsrate"),
    ({"annual_rate_pct": 120}, "Zinssatz"),
    ({"start": "kein Datum"}, "Beginn"),
    ({"ende": ""}, "Ende"),
    ({"ende": "2025-01-01"}, "vor dem Beginn"),
])
def test_a_broken_loan_is_refused_with_a_reason(konfig, aenderung, wort):
    with pytest.raises(ValueError, match=wort):
        kredite.anlegen({**RATE, **aenderung}, konfig)
    assert not (konfig / kredite.EIGEN).exists()


def test_a_rate_below_the_monthly_interest_is_refused(konfig):
    """Sonst waechst die Restschuld, und der Tilgungsplan zeigt eine Prognose,
    die nie endet -- als Zahl plausibel, als Aussage falsch."""
    with pytest.raises(ValueError, match="Monatszins"):
        kredite.anlegen({**RATE, "annuity_cents": 100}, konfig)


# ---------------------------------------------------------------- Seite

from fastapi.testclient import TestClient  # noqa: E402

from finctl.web.server import app  # noqa: E402

client = TestClient(app)


def test_the_page_offers_the_form_and_a_way_out():
    html = client.get("/kredite").text
    assert 'id="neu-kredit"' in html and "Neuer Ratenkredit" in html
    # Je Kredit ein Weg, ihn loszuwerden.
    assert html.count("kreditEntfernen(") >= 1


def test_the_account_list_offers_only_open_accounts():
    """Eine Rate von einem geschlossenen Konto abzubuchen ergibt keinen Sinn."""
    import re

    html = client.get("/kredite").text
    block = html[html.index('id="k-konto"'):]
    angeboten = set(re.findall(r'<option value="([^"]+)"', block[:block.index("</select>")]))
    assert angeboten
    assert not (angeboten & {"amex", "revolut", "onvista"})   # geschlossen


def test_the_endpoints_refuse_before_they_write():
    """Beide Ablehnungen kommen vor dem Schreiben, brauchen also keine
    Konfiguration -- und fassen die echte nicht an."""
    antwort = client.post("/api/kredit-neu", json={"id": "", "name": ""})
    assert antwort.status_code == 400 and "Kennung" in antwort.json()["error"]

    antwort = client.post("/api/kredit-entfernen", json={"id": "gibtsnicht"})
    assert antwort.status_code == 400
