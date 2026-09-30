"""Einen Auszug so verfremden, dass man ihn weitergeben kann.

Wofuer: ein Parser fuer eine neue Bank braucht ein Beispiel, und das
Beispiel ist der eigene Auszug. Weitergeben soll man ihn nicht -- also eine
Kopie, an der der Parser noch alles pruefen kann (Aufbau, Datum, Betrag,
Saldo) und aus der niemand mehr liest, wer wem was gezahlt hat.

WAS BLEIBT: die Kopfzeile, jedes Datum, jeder Betrag und Saldo, und die
Woerter, mit denen Banken Buchungsarten nennen (Lastschrift, Gebucht, EUR).
Daran haengen Erkennung, Filter und Abstimmtor.

WAS ERSETZT WIRD: jede IBAN (durch eine erfundene, dieselbe IBAN immer durch
dieselbe), jede lange Ziffernfolge (Kunden-, Mandats-, Referenznummern), und
bei CSV jeder freie Text -- Namen, Verwendungszweck -- durch Platzhalter,
gleicher Text durch gleichen Platzhalter. Die eigenen Namen, die man angibt,
werden ueberall ersetzt.

BETRAEGE: auf Wunsch mit einem ganzzahligen Faktor verfremdet. Ganzzahlig,
weil dann Anfangssaldo plus Summe weiter genau den Endsaldo ergibt.

EIN PDF wird nicht umgeschrieben, sondern als Seitentext ausgegeben (.txt,
Seiten durch Seitenvorschub getrennt): der Parser liest ohnehin nur den
Text. Freier Text laesst sich dort nicht von Tabellenwoertern trennen -- was
nicht als IBAN, Nummer oder angegebener Name erkennbar ist, bleibt stehen.
Deshalb gilt fuer jede Ausgabe: vor dem Weitergeben ansehen.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from finctl.ingest.csvbank import text_lesen

#: Woerter, mit denen Banken Buchungsarten und Zustaende nennen. Sie bleiben:
#: an ihnen haengen Filter wie "nur gebucht".
WORTSCHATZ = {
    "gebucht", "vorgemerkt", "umsatz gebucht", "umsatz vorgemerkt", "eingang", "ausgang",
    "eur", "lastschrift", "gutschrift", "überweisung", "ueberweisung", "dauerauftrag",
    "kartenzahlung", "gehalt/rente", "abschluss", "entgelt", "zinsen", "folgelastschrift",
    "erstlastschrift", "gutschr. ueberweisung", "echtzeitüberweisung", "offen", "--",
    "credit transfer", "presentment", "direct debit", "income", "outgoing transfer",
    "visa-umsatz", "lastschrift / belastung", "übertrag / überweisung",
}

#: Spalten, die Arten und Zustaende tragen, keine Namen.
ART_SPALTEN = re.compile(r"(?i)^(w(ä|ae)hrung|currency|status|info|umsatztyp|type|"
                         r"umsatzart|vorgang|kategorie|original currency)$")

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[\dA-Z]{4}){3,7}(?: ?[\dA-Z]{1,3})?\b")
_ZIFFERN = re.compile(r"(?<![\d.,])\d{7,}(?![\d.,])")
_DATUM = re.compile(r"^\d{1,4}[./-]\d{1,2}[./-]\d{2,4}$")
_BETRAG = re.compile(r"^[-+]?\d[\d.]*[.,]\d{1,2}\s*(€|EUR)?-?$")
#: Ganze Euro ohne Komma (DKB: `2.000`) -- nur in einer Betragsspalte ein
#: Betrag; anderswo ist eine nackte Zahl eine Referenz und wird ersetzt.
_GANZER_BETRAG = re.compile(r"^[-+]?\d{1,3}(?:\.?\d{3})*\s*(€|EUR)?-?$")
_BETRAGSSPALTE = re.compile(r"(?i)betrag|umsatz|amount|soll|haben|saldo")
#: Eine Beschriftung, hinter der im Vorspann ein Name steht (ING: "Kunde;...").
_NAMENSFELD = re.compile(r"(?i)^(kunde|kontoinhaber|inhaber|name|kontoname)\b")


@dataclass(slots=True)
class Ersatz:
    namen: list[str] = field(default_factory=list)
    faktor: int = 1
    ibans: dict[str, str] = field(default_factory=dict)
    texte: dict[str, str] = field(default_factory=dict)
    gezaehlt: dict[str, int] = field(default_factory=lambda: {"iban": 0, "nummer": 0,
                                                              "text": 0, "name": 0})

    def iban(self, treffer: re.Match) -> str:
        roh = treffer.group(0).replace(" ", "")
        if roh not in self.ibans:
            nr = len(self.ibans) + 1
            self.ibans[roh] = f"DE00{nr:018d}"
        self.gezaehlt["iban"] += 1
        return self.ibans[roh]

    def nummer(self, treffer: re.Match) -> str:
        self.gezaehlt["nummer"] += 1
        return "0" * len(treffer.group(0))

    def namen_ersetzen(self, text: str) -> str:
        for n, name in enumerate(self.namen, 1):
            if name and name.casefold() in text.casefold():
                self.gezaehlt["name"] += 1
                text = re.sub(re.escape(name), f"Person {n}", text, flags=re.I)
        return text

    def freitext(self, text: str) -> str:
        if not text or text.casefold() in WORTSCHATZ:
            return text
        if text not in self.texte:
            self.texte[text] = f"Text {len(self.texte) + 1}"
        self.gezaehlt["text"] += 1
        return self.texte[text]

    def zeile(self, text: str) -> str:
        """Eine Zeile ausserhalb der Tabelle: nur IBAN, Nummern, Namen."""
        text = _IBAN.sub(self.iban, text)
        text = _ZIFFERN.sub(self.nummer, text)
        return self.namen_ersetzen(text)

    def betrag(self, wert: str) -> str:
        """Mit dem Faktor, in derselben Schreibweise -- Komma oder Punkt."""
        if self.faktor == 1:
            return wert
        roh = wert.replace("€", "").replace("EUR", "").strip()
        punkt = "," not in roh and re.search(r"\.\d{1,2}$", roh) is not None
        from decimal import Decimal

        from finctl.ledger.db import format_eur, parse_de_amount

        try:
            cents = (int(Decimal(roh) * 100) if punkt else parse_de_amount(roh)) * self.faktor
        except (ValueError, ArithmeticError):
            return wert
        neu = (f"{cents / 100:.2f}" if punkt
               else format_eur(cents).replace("€", "").strip())
        endung = " EUR" if "EUR" in wert else (" €" if "€" in wert else "")
        return neu + endung


def _kopfzeile(text: str) -> tuple[str, int]:
    from finctl.ingest.zuordnung import _kopf, _trenner

    trenner = _trenner(text)
    kopf = _kopf(text.splitlines(), trenner)
    if kopf is None:
        raise ValueError("Keine Tabelle mit Datum gefunden -- ist das ein Umsatz-Export?")
    return trenner, kopf


def _schreiben(zellen: list[str], trenner: str) -> str:
    puffer = io.StringIO()
    csv.writer(puffer, delimiter=trenner, quoting=csv.QUOTE_MINIMAL,
               lineterminator="").writerow(zellen)
    return puffer.getvalue()


def _nebenzeile(zeile: str, trenner: str, ersatz: Ersatz) -> str:
    """Vorspann und Fuss: Beschriftungen bleiben, Betraege gehen mit dem
    Faktor, IBANs und Nummern werden ersetzt, ein Name hinter "Kunde" auch."""
    if trenner not in zeile:
        return ersatz.zeile(zeile)
    zellen = next(csv.reader([zeile], delimiter=trenner))
    neu = []
    for i, zelle in enumerate(zellen):
        wert = zelle.strip()
        if _BETRAG.match(wert):
            neu.append(ersatz.betrag(wert))
        elif i > 0 and _NAMENSFELD.match(zellen[0].strip()) and wert:
            neu.append(ersatz.freitext(wert))
        else:
            neu.append(ersatz.zeile(zelle))
    return _schreiben(neu, trenner)


def csv_verfremden(text: str, ersatz: Ersatz) -> str:
    trenner, kopf = _kopfzeile(text)
    zeilen = text.splitlines()
    spalten = next(csv.reader([zeilen[kopf]], delimiter=trenner))
    art = {i for i, s in enumerate(spalten) if ART_SPALTEN.match(s.strip())}
    betraege = {i for i, s in enumerate(spalten) if _BETRAGSSPALTE.search(s)}
    raus = [_nebenzeile(z, trenner, ersatz) for z in zeilen[:kopf]] + [zeilen[kopf]]
    for zeile in zeilen[kopf + 1:]:
        zellen = next(csv.reader([zeile], delimiter=trenner)) if zeile.strip() else []
        if len(zellen) < len(spalten) - 1:
            raus.append(_nebenzeile(zeile, trenner, ersatz))
            continue
        neu = []
        for i, zelle in enumerate(zellen):
            wert = zelle.strip()
            if _IBAN.fullmatch(wert.replace(" ", "")) or _IBAN.fullmatch(wert):
                neu.append(_IBAN.sub(ersatz.iban, wert))
            elif _DATUM.match(wert) or not wert:
                neu.append(zelle)
            elif _BETRAG.match(wert) or (i in betraege and _GANZER_BETRAG.match(wert)):
                neu.append(ersatz.betrag(wert))
            elif i in art:
                neu.append(zelle)
            else:
                neu.append(ersatz.freitext(ersatz.zeile(wert)))
        raus.append(_schreiben(neu, trenner))
    return "\n".join(raus) + "\n"


def text_verfremden(seiten: list[str], ersatz: Ersatz) -> str:
    return "\f".join("\n".join(ersatz.zeile(z) for z in s.splitlines()) for s in seiten)


def verfremden(pfad: Path, ziel: Path | None = None, *, namen: list[str] | None = None,
               faktor: int = 1) -> tuple[Path, dict]:
    """Die Kopie schreiben. Gibt Ziel und die Zaehlung der Ersetzungen zurueck."""
    if faktor < 1:
        raise ValueError("Der Faktor ist eine ganze Zahl ab 1.")
    ersatz = Ersatz(namen=[n.strip() for n in (namen or []) if n.strip()], faktor=faktor)
    if pfad.suffix.lower() == ".pdf":
        from finctl.ingest.importer import extract_pages

        inhalt = text_verfremden(extract_pages(pfad), ersatz)
        ziel = ziel or pfad.with_name(pfad.stem + ".anonym.txt")
    else:
        inhalt = csv_verfremden(text_lesen(pfad), ersatz)
        ziel = ziel or pfad.with_name(pfad.stem + ".anonym" + pfad.suffix)
    if ziel.resolve() == pfad.resolve():
        raise ValueError("Die Kopie darf das Original nicht ersetzen.")
    ziel.write_text(inhalt, encoding="utf-8")
    return ziel, dict(ersatz.gezaehlt)
