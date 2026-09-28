"""Die mitgelieferten allgemeinen Regeln: eine Grundschicht unter den eigenen.

Wer bei null anfaengt, hat sonst einen leeren Regelsatz und ordnet jede
Buchung von Hand zu. Die Grundschicht kennt oeffentliche Haendler, Dienste
und Behoerden -- und tritt hinter jede eigene Regel zurueck.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from finctl.rules import engine
from finctl.rules.engine import TxContext
from finctl.vorgaben import ORDNER


def _tx(text: str, cents: int = -1_000) -> TxContext:
    return TxContext(id=1, account_id="giro", booking_date="2026-01-15", amount_cents=cents,
                     raw_text=text, counterparty_norm="", counterparty_iban=None,
                     tx_type="")


def _regeln(tmp_path: Path, an: bool, eigene: str = "[]", custom: str | None = None):
    basis = tmp_path / "rules.yaml"
    basis.write_text(f"allgemeine_regeln: {'true' if an else 'false'}\n\nrules: {eigene}\n",
                     encoding="utf-8")
    eigen = tmp_path / "rules_custom.yaml"
    if custom:
        eigen.write_text(custom, encoding="utf-8")
    return engine.load_rules(basis, eigen)


def test_the_layer_is_off_unless_the_rulebook_switches_it_on(tmp_path):
    """Ein aelterer Regelsatz ohne den Schalter bleibt, wie er war."""
    assert _regeln(tmp_path, an=False) == []
    an = _regeln(tmp_path, an=True)
    assert an and all(r.provenance == "allgemein" for r in an)
    assert min(r.priority for r in an) >= engine.GRUNDSCHICHT_ABSTAND


def test_every_own_rule_wins_whatever_its_priority(tmp_path):
    eigene = ('[{id: mein-laden, priority: 900, name: Mein Laden, '
              'match: {text: [REWE]}, set: {mgmt: konsum/sonstiges}}]')
    regeln = _regeln(tmp_path, an=True, eigene=eigene)
    treffer = engine.first_match(regeln, _tx("REWE Markt GmbH"), {})
    assert treffer.id == "mein-laden"


def test_an_own_rule_with_the_same_id_replaces_the_general_one(tmp_path):
    eigene = ('[{id: allg-supermarkt, priority: 60, name: Eigener Supermarkt, '
              'match: {text: [Hofladen]}, set: {mgmt: lebensmittel/supermarkt}}]')
    regeln = _regeln(tmp_path, an=True, eigene=eigene)
    [supermarkt] = [r for r in regeln if r.id == "allg-supermarkt"]
    assert supermarkt.provenance != "allgemein"


def test_a_general_rule_can_be_switched_off_on_its_own(tmp_path):
    custom = "regeln:\n  allg-supermarkt: {entfernt: true}\n"
    regeln = _regeln(tmp_path, an=True, custom=custom)
    assert "allg-supermarkt" not in {r.id for r in regeln}


def test_every_general_rule_points_at_a_category_of_the_starting_tree():
    baum = yaml.safe_load((ORDNER / "taxonomy.yaml").read_text(encoding="utf-8"))["management"]
    bekannt = {f"{k}/{c}" for k, v in baum.items() for c in (v.get("children") or {})}
    regeln = engine.regeln_aus(engine.grundschicht())
    assert len({r.id for r in regeln}) == len(regeln)
    fremd = [r.id for r in regeln if r.actions.get("mgmt") not in bekannt]
    assert not fremd, fremd


def test_typical_statement_lines_land_where_expected():
    regeln = engine.regeln_aus(engine.grundschicht())

    def kategorie(text, cents=-1_000):
        r = engine.first_match(regeln, _tx(text, cents), {})
        return r.actions["mgmt"] if r else None

    assert kategorie("REWE Markt GmbH Filiale 1234") == "lebensmittel/supermarkt"
    assert kategorie("NETFLIX.COM") == "abo/video-streaming"
    assert kategorie("Lohn/Gehalt 01.2026", 300_000) == "einkommen/gehalt"
    assert kategorie("Kapitalertragsteuer", -1_234) == "steuern/kapitalertragsteuer"
    # Kurze Woerter, die in anderen stecken, stehen nicht in der Grundschicht.
    assert kategorie("Espresso Bar am Markt") is None
    assert kategorie("Normalpreis Kartenzahlung") is None


def test_switching_the_layer_keeps_the_comments_of_the_rulebook(tmp_path):
    pfad = tmp_path / "rules.yaml"
    pfad.write_text("# Warum diese Regeln\n\nrules: []\n", encoding="utf-8")
    engine.grundschicht_setzen(True, pfad)
    assert engine.grundschicht_an(pfad)
    assert "# Warum diese Regeln" in pfad.read_text(encoding="utf-8")
    engine.grundschicht_setzen(False, pfad)
    assert not engine.grundschicht_an(pfad)
    assert pfad.read_text(encoding="utf-8").count("allgemeine_regeln") == 1


def test_a_new_install_starts_with_the_layer_switched_on():
    assert yaml.safe_load((ORDNER / "rules.yaml").read_text(encoding="utf-8"))[
        "allgemeine_regeln"] is True


def test_a_general_rule_whose_category_was_deleted_is_left_out():
    """Wer eine Kategorie der Startvorlage loescht, bekommt keine Buchungen darauf."""
    import sqlite3

    from finctl.rules.categorize import ohne_fremde_kategorien

    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE mgmt_categories (id TEXT, active INTEGER)")
    c.execute("INSERT INTO mgmt_categories VALUES ('lebensmittel/supermarkt', 1)")
    rest = ohne_fremde_kategorien(c, engine.regeln_aus(engine.grundschicht()))
    assert {r.actions["mgmt"] for r in rest} == {"lebensmittel/supermarkt"}
