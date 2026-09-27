"""Nichts aus dem Hauptbuch steht im Code -- gemessen, nicht gelistet.

`tests/test_namen.py` prueft eine Liste verbotener Namen. Eine Liste belegt
aber nur, dass DIESE Namen fehlen: Als der Code am 27.09.2026 zum ersten Mal
gegen das Hauptbuch gehalten wurde, standen darin trotzdem sechs Menschen, mit
denen Abos geteilt werden, zwei Wohnorte, eine echte IBAN, der Anfang eines
echten Kontoauszugs und die Zins- und Tilgungsbetraege eines laufenden
Kredits -- in Tests und Docstrings, also in genau dem, was weitergegeben
werden soll.

Dieser Test nimmt deshalb das Hauptbuch selbst als Liste:

* jede GEGENPARTEI, die in einer Buchung steht,
* jeden BETRAG mit Cent (ab 10 Euro), deutsch geschrieben oder als Centzahl,
* jede lange ZIFFERNFOLGE aus `config/` -- Kreditnummern, IBANs, Vertraege,
* jeden NAMEN, der in `config/` einen Anteil an etwas traegt.

Nichts davon darf im Code, in den Tests oder in der Doku stehen. Runde
Betraege zaehlen nicht (1.200,00 ist ein Beispiel, 1.217,43 eine Buchung).

Auf dem Musterhaushalt laeuft er nicht: dort IST das Hauptbuch erfunden und
steht absichtlich in `tests/musterhaushalt/`.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import pytest
import yaml

WURZEL = Path(__file__).resolve().parent.parent

ORDNER = ("finctl", "tests", "docs")
DATEIEN = ("HANDBUCH.md", "CLAUDE.md", "pyproject.toml")
ENDUNGEN = {".py", ".html", ".sql", ".yaml", ".yml", ".md", ".csv", ".txt", ".toml", ".json"}
#: Erfunden, und dort absichtlich.
AUSSER = ("tests/musterhaushalt/", "tests/beispiele/")

#: Gegenparteien, die kein Mensch und kein Ort sind: Buchungsarten, Banken,
#: Plattformen. Ein Eintrag hier ist eine Aussage -- "das ist kein Name" --
#: und keiner fuer eine Person.
KEIN_NAME = {
    "Abhebung vom Geldkonto", "Cash reward allocation", "Deutschland",
    "Erhaltene Zinsen", "Gaming", "Gemeinschaftskonto", "Kreditrate",
    "Restaurant", "Tankstelle", "Überweisung", "Vertrieb", "Vertrieb GmbH",
    "APPLE.COM/BILL", "C24 Bank", "Microsoft Payments", "Spotify",
    "PayPal Europe S.a.r.l. et Cie S.C.A", "RTL interactive GmbH",
    "JET-Tankstelle",
}


def _texte() -> dict[str, str]:
    out = {}
    pfade = [p for o in ORDNER for p in (WURZEL / o).rglob("*")]
    pfade += [WURZEL / d for d in DATEIEN]
    for pfad in pfade:
        rel = pfad.relative_to(WURZEL).as_posix()
        if (not pfad.is_file() or pfad.suffix not in ENDUNGEN
                or rel.startswith(AUSSER) or "__pycache__" in rel):
            continue
        out[rel] = pfad.read_text(encoding="utf-8", errors="replace")
    return out


@pytest.fixture(scope="module")
def hauptbuch():
    if os.environ.get("FINCTL_TESTDATEN") == "muster":
        pytest.skip("auf dem Musterhaushalt ist das Hauptbuch erfunden")
    db = Path("data/finance.db")
    if not db.exists():
        pytest.skip("kein Hauptbuch")
    conn = sqlite3.connect(db)
    try:
        yield conn
    finally:
        conn.close()


def _funde(muster: re.Pattern, texte: dict[str, str]) -> list[str]:
    return [f"{datei}: {m.group(0)!r}" for datei, text in texte.items()
            for m in muster.finditer(text)]


def test_no_counterparty_from_the_ledger_appears_in_the_code(hauptbuch):
    namen = {r[0].strip() for r in hauptbuch.execute(
        "SELECT DISTINCT counterparty FROM transactions WHERE counterparty IS NOT NULL")}
    namen = {n for n in namen if len(n) >= 6 and n not in KEIN_NAME}
    muster = re.compile("|".join(re.escape(n) for n in sorted(namen, key=len, reverse=True)))
    funde = _funde(muster, _texte())
    assert not funde, "Gegenparteien aus dem Hauptbuch:\n  " + "\n  ".join(funde[:30])


def test_no_booked_amount_with_cents_appears_in_the_code(hauptbuch):
    gebucht = {r[0] for tabelle in ("transactions", "splits") for r in hauptbuch.execute(
        f"SELECT DISTINCT ABS(amount_cents) FROM {tabelle}")}
    gebucht = {c for c in gebucht if c >= 1000 and c % 100}
    funde = []
    for datei, text in _texte().items():
        for m in re.finditer(r"\b(\d{1,3}(?:\.\d{3})*),(\d{2})\b", text):
            if int(m.group(1).replace(".", "")) * 100 + int(m.group(2)) in gebucht:
                funde.append(f"{datei}: {m.group(0)}")
        # Als Centzahl erst ab 100 Euro: darunter sind es Jahreszahlen.
        for m in re.finditer(r"(?<![\d.,_])(\d{1,3}(?:_\d{3})+|\d{5,9})(?![\d_])", text):
            if int(m.group(1).replace("_", "")) in gebucht:
                funde.append(f"{datei}: {m.group(0)}")
    assert not funde, "Gebuchte Betraege:\n  " + "\n  ".join(funde[:30])


def test_no_reference_number_from_the_config_appears_in_the_code(hauptbuch):
    """Kreditnummern, Vertragsnummern, IBANs -- alles ab sieben Ziffern."""
    config = Path("config")
    nummern = set()
    for datei in config.glob("*.yaml"):
        if datei.name == "overrides.yaml":      # Hashes, keine Nummern
            continue
        nummern |= set(re.findall(r"(?<!\d)\d{7,}(?!\d)", datei.read_text(encoding="utf-8")))
    # Runde Betraege in Cent (13000000) sind keine Kennung.
    nummern = {n for n in nummern if not n.endswith("00000")}
    if not nummern:
        pytest.skip("keine Nummern in config/")
    muster = re.compile(r"(?<!\d)(" + "|".join(sorted(nummern, key=len, reverse=True))
                        + r")(?!\d)")
    funde = _funde(muster, _texte())
    assert not funde, "Nummern aus config/:\n  " + "\n  ".join(funde[:30])


def test_no_person_who_shares_a_cost_appears_in_the_code(hauptbuch):
    """Wer an einem Abo oder einer Versicherung einen Anteil traegt."""
    namen: set[str] = set()

    def sammeln(knoten) -> None:
        if isinstance(knoten, dict):
            if "anteil_cents" in knoten and isinstance(knoten.get("name"), str):
                namen.add(knoten["name"])
            for wert in knoten.values():
                sammeln(wert)
        elif isinstance(knoten, list):
            for wert in knoten:
                sammeln(wert)

    for datei in Path("config").glob("*.yaml"):
        sammeln(yaml.safe_load(datei.read_text(encoding="utf-8")))
    teile = {t for n in namen for t in [n, *re.split(r"[\s-]+", n)] if len(t) >= 4}
    if not teile:
        pytest.skip("niemand teilt etwas")
    muster = re.compile(r"\b(" + "|".join(re.escape(t) for t in sorted(teile)) + r")\b")
    funde = _funde(muster, _texte())
    assert not funde, "Namen aus config/:\n  " + "\n  ".join(funde[:30])
