"""Einen unbekannten CSV-Export selbst beschreiben: Spalten zuordnen.

Wenn keine Beschreibung in `profiles/banken_csv.py` passt, liest die
Einrichtung die Datei, schlaegt vor, welche Spalte Datum, Betrag, Gegenpartei
und Saldo ist, und laesst korrigieren. Heraus kommt ein `CsvProfil` wie jedes
andere, gespeichert in `config/csv_profile_custom.yaml` -- eine
Handentscheidung, also in `config/` und nicht in der Datenbank. Ab dann
erkennt der Import die Bank an ihrer Kopfzeile wie jede beschriebene.

Die Vorschlaege sind Heuristik ueber Spaltennamen und Beispielwerte. Sie
entscheiden nichts: gespeichert wird, was der Mensch bestaetigt, und die
Vorschau zeigt vorher, ob der Auszug damit aufgeht.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml

from finctl.ingest.csvbank import CsvBankParser, CsvProfil, _zellen, profil_aus
from finctl.pfade import CONFIG_DIR

PFAD = CONFIG_DIR / "csv_profile_custom.yaml"
PRAEFIX = "eigen_"

KOPF = """# Selbst beschriebene CSV-Exporte -- geschrieben von der Einrichtung.
#
# Je Bank: woran die Kopfzeile zu erkennen ist und welche Spalte was bedeutet.
# Aufbau wie finctl/ingest/profiles/banken_csv.py; `saldo` ist spalte, kopf,
# ende oder keiner. Der Import nennt ein Profil hier eigen_<kennung>.

"""

#: Rolle -> Woerter, an denen man die Spalte im Namen erkennt (klein, ohne Umlaut).
ROLLEN = {
    "datum": ("buchungstag", "buchungsdatum", "buchung", "datum", "booking date", "date"),
    "wertstellung": ("wertstellung", "valuta", "valutadatum", "value date"),
    "betrag": ("betrag", "umsatz", "amount"),
    "saldospalte": ("saldo", "kontostand", "balance"),
    "gegenpartei": ("empfaenger", "auftraggeber", "zahlungsbeteiligter", "beguenstigter",
                    "zahlungspflichtiger", "partner", "name", "payee"),
    "zweck": ("verwendungszweck", "zweck", "reference", "buchungstext", "beschreibung"),
    "iban": ("iban",),
    "buchungstext": ("umsatzart", "buchungsart", "vorgang", "typ", "type"),
}

DATUMSFORMATE = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y")


def _klein(text: str) -> str:
    text = text.casefold()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return text


def _trenner(text: str) -> str:
    zeilen = [z for z in text.splitlines()[:60] if z.strip()]
    return max((";", ",", "\t"), key=lambda t: sum(z.count(t) for z in zeilen))


def _kopf(zeilen: list[str], trenner: str) -> int | None:
    """Die Kopfzeile: die erste Zeile mit mindestens drei Spalten, auf die
    eine Zeile gleicher Breite folgt, in der ein Datum steht."""
    for nr, zeile in enumerate(zeilen[:80]):
        kopf = _zellen(zeile, trenner)
        if len([z for z in kopf if z]) < 3:
            continue
        for folgend in zeilen[nr + 1:nr + 4]:
            zellen = _zellen(folgend, trenner)
            if abs(len(zellen) - len(kopf)) <= 1 and any(_datumsformat(z) for z in zellen):
                return nr
    return None


def _datumsformat(wert: str) -> str | None:
    for fmt in DATUMSFORMATE:
        try:
            datetime.strptime(wert.strip(), fmt)
            return fmt
        except ValueError:
            continue
    return None


def _ist_betrag(wert: str) -> bool:
    return bool(re.fullmatch(r"-?[\d.,]+\s*(€|EUR)?-?", wert.strip())) and any(
        c.isdigit() for c in wert)


def analysieren(text: str) -> dict:
    """Was in der Datei steht und was wahrscheinlich was ist."""
    trenner = _trenner(text)
    zeilen = text.splitlines()
    kopf = _kopf(zeilen, trenner)
    if kopf is None:
        raise ValueError("Keine Tabelle mit Datum gefunden -- ist das ein Kontoumsatz-Export?")
    spalten = _zellen(zeilen[kopf], trenner)
    beispiele = [z for z in (_zellen(x, trenner) for x in zeilen[kopf + 1:kopf + 40])
                 if len(z) >= len(spalten) - 1][:6]

    def werte(i: int) -> list[str]:
        return [b[i] for b in beispiele if i < len(b) and b[i]]

    vorschlag: dict[str, str | list[str] | None] = {}
    for rolle, woerter in ROLLEN.items():
        passend = [s for s in spalten if s and any(w in _klein(s) for w in woerter)]
        if rolle == "iban":
            passend = [s for s in passend if "auftragskonto" not in _klein(s)]
        if rolle in ("datum", "wertstellung"):
            passend = [s for s in passend
                       if any(_datumsformat(w) for w in werte(spalten.index(s)))]
        if rolle in ("betrag", "saldospalte"):
            passend = [s for s in passend if werte(spalten.index(s))
                       and all(_ist_betrag(w) for w in werte(spalten.index(s)))]
        if rolle == "datum":
            passend = [s for s in passend if "wert" not in _klein(s)
                       and "valuta" not in _klein(s)] or passend
        vorschlag[rolle] = passend[0] if passend else None
    betraege = werte(spalten.index(vorschlag["betrag"])) if vorschlag["betrag"] else []
    datumswerte = werte(spalten.index(vorschlag["datum"])) if vorschlag["datum"] else []
    return {
        "trenner": trenner, "kopfzeile": kopf, "spalten": spalten, "beispiele": beispiele,
        "vorschlag": vorschlag,
        "datumsformat": next((f for w in datumswerte if (f := _datumsformat(w))), "%d.%m.%Y"),
        "dezimal": "." if betraege and all(re.search(r"\.\d{1,2}$", b) and "," not in b
                                           for b in betraege) else ",",
        "saldo": "spalte" if vorschlag["saldospalte"] else "keiner",
    }


def profil_aus_zuordnung(kennung: str, zuordnung: dict, spalten: list[str]) -> CsvProfil:
    """Aus dem, was in der Einrichtung bestaetigt wurde, ein Profil.

    Erkannt wird an der GANZEN Kopfzeile: eine selbst beschriebene Bank soll
    nicht den Export einer anderen an drei gemeinsamen Spaltennamen fangen.
    """
    roh = {k: v for k, v in zuordnung.items() if v not in (None, "", [])}
    roh["erkennung"] = [s for s in spalten if s]
    return profil_aus(PRAEFIX + kennung, roh, eigen=True)


# ------------------------------------------------------------------ Speicher

def _lesen() -> dict:
    return (yaml.safe_load(PFAD.read_text(encoding="utf-8")) or {}) if PFAD.exists() else {}


def eigene() -> dict[str, CsvBankParser]:
    """Die selbst beschriebenen Exporte, als Parser -- je `eigen_<kennung>`."""
    return {PRAEFIX + str(k): CsvBankParser(profil_aus(PRAEFIX + str(k), v or {}, eigen=True))
            for k, v in (_lesen().get("profile") or {}).items()}


def speichern(kennung: str, zuordnung: dict, spalten: list[str], pfad: Path | None = None) -> str:
    """Pruefen, dann eintragen. Gibt die Profilkennung zurueck."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,30}", kennung):
        raise ValueError("Kennung: Kleinbuchstaben, Ziffern und Bindestrich, 2 bis 31 Zeichen")
    profil_aus_zuordnung(kennung, zuordnung, spalten)        # wirft, wenn etwas fehlt
    pfad = pfad or PFAD
    daten = _lesen() if pfad == PFAD else (
        (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}) if pfad.exists() else {})
    profile = daten.get("profile") or {}
    profile[kennung] = {**{k: v for k, v in zuordnung.items() if v not in (None, "", [])},
                        "erkennung": [s for s in spalten if s]}
    pfad.write_text(KOPF + yaml.safe_dump({"profile": profile}, allow_unicode=True,
                                          sort_keys=False), encoding="utf-8")
    return PRAEFIX + kennung
