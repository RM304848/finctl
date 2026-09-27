"""Nichts in config/ zeigt auf eine Kategorie, die es nicht (mehr) gibt.

Am 25.09.2026 brach der Neuaufbau aus `config/` an genau dieser Stelle: eine
Regel nannte noch eine Kategorie, die laengst in eine andere gewandert war.
Das laufende Hauptbuch merkte davon nichts -- dort stand die alte Kennung
stillgelegt noch in der Tabelle, also hielt der Fremdschluessel. Erst eine
frische Datenbank scheiterte, und zwar an einem INSERT ohne Hinweis darauf,
welche Regel es war. Diese Pruefungen sagen es vorher, mit Namen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from finctl.pfade import CONFIG_DIR

MIGRATIONEN = CONFIG_DIR / "taxonomy_migrations.yaml"


def stillgelegte(pfad: Path = MIGRATIONEN) -> set[str]:
    if not pfad.exists():
        return set()
    spec = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
    return set(spec.get("remap") or {}) | set(spec.get("retire") or [])


def verweise(daten, alt: set[str], ort: str = "") -> list[str]:
    """Jede Stelle in einer YAML-Struktur, die eine stillgelegte Kennung nennt.

    `was` wird uebersprungen: dort halten Handentscheidungen fest, was VOR
    ihnen galt. Das ist Geschichte, kein Verweis.
    """
    funde: list[str] = []
    if isinstance(daten, dict):
        for k, v in daten.items():
            if k == "was":
                continue
            if isinstance(k, str) and k in alt:
                funde.append(f"{ort}/{k} (als Schluessel)")
            funde += verweise(v, alt, f"{ort}/{k}")
    elif isinstance(daten, list):
        for i, v in enumerate(daten):
            funde += verweise(v, alt, f"{ort}[{i}]")
    elif isinstance(daten, str) and daten in alt:
        funde.append(f"{ort}: {daten}")
    return funde


def test_verweise_findet_eine_stillgelegte_kennung():
    alt = {"alt/weg"}
    daten = {"regeln": [{"id": "x", "set": {"mgmt": "alt/weg"}}],
             "overrides": [{"parts": [{"mgmt": "neu/da"}], "was": {"category": "alt/weg"}}]}
    assert verweise(daten, alt) == ["/regeln[0]/set/mgmt: alt/weg"]


@pytest.mark.skipif(not MIGRATIONEN.exists(), reason="keine Migrationen eingetragen")
def test_keine_datei_in_config_nennt_eine_stillgelegte_kategorie():
    alt = stillgelegte()
    funde = []
    for datei in sorted(CONFIG_DIR.glob("*.yaml")):
        if datei == MIGRATIONEN:
            continue
        daten = yaml.safe_load(datei.read_text(encoding="utf-8"))
        funde += [f"{datei.name}{f}" for f in verweise(daten, alt)]
    assert not funde, ("Verweise auf stillgelegte Kategorien -- auf die Ersatzkategorie "
                       "aus taxonomy_migrations.yaml umstellen:\n  " + "\n  ".join(funde))


def _aktive_kategorien(tmp_path: Path) -> tuple[set[str], set[str]]:
    from finctl.ledger import db as ledger
    from finctl.tax import taxonomy as tx

    ledger.init_db(tmp_path / "leer.db")
    conn = ledger.connect(tmp_path / "leer.db")
    try:
        tx.load_into_db(conn)
        mgmt = {r[0] for r in conn.execute("SELECT id FROM mgmt_categories WHERE active = 1")}
        steuer = {r[0] for r in conn.execute("SELECT id FROM tax_categories")}
    finally:
        conn.close()
    return mgmt, steuer


def test_jede_regel_nennt_eine_kategorie_die_es_gibt(tmp_path):
    """Gegen eine FRISCHE Datenbank -- die laufende kennt noch Stillgelegtes."""
    from finctl.rules import engine

    mgmt, steuer = _aktive_kategorien(tmp_path)
    funde = []
    for regel in engine.load_rules():
        for teil in [regel.actions, *regel.splits]:
            if teil.get("mgmt") and teil["mgmt"] not in mgmt:
                funde.append(f"{regel.id}: Kategorie {teil['mgmt']}")
            if teil.get("tax") and teil["tax"] not in steuer:
                funde.append(f"{regel.id}: Steuerposition {teil['tax']}")
    assert not funde, "Regeln mit unbekannter Kategorie:\n  " + "\n  ".join(funde)
