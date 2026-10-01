"""Kontoumsaetze aus dem CSV-Export einer Bank -- beschrieben statt programmiert.

Fast jede deutsche Bank bietet im Online-Banking einen CSV-Export an. Er ist
robuster als ein PDF-Auszug: die Spalten stehen fest, nichts muss aus einem
Seitenlayout geraten werden. Die Exporte unterscheiden sich nur in wenigen
Dingen -- Trennzeichen, Spaltennamen, Datumsformat, woher der Saldo kommt --,
und genau das beschreibt ein `CsvProfil`. Eine neue Bank ist damit ein
Eintrag, kein neuer Parser (`finctl/ingest/profiles/banken_csv.py`), und eine
Bank, die niemand beschrieben hat, laesst sich in der Einrichtung per
Spaltenzuordnung selbst beschreiben (`config/csv_profile_custom.yaml`).

DAS ABSTIMMTOR GILT AUCH HIER. Woher Anfangs- und Endsaldo kommen, sagt das
Profil:

- `spalte`: jede Zeile traegt den Saldo danach. Der Anfang ist der Saldo der
  ersten Zeile minus ihr Betrag, das Ende der Saldo der letzten -- und jede
  Zeile dazwischen muss an die vorige anschliessen, sonst fehlt eine.
- `kopf`: Anfangs- und Endsaldo stehen ueber oder unter der Tabelle.
- `ende`: nur der Endsaldo steht da. Der Anfang wird errechnet; ob er stimmt,
  prueft erst der Anschluss an den vorigen Auszug desselben Kontos.
- `keiner`: der Export traegt keinen Saldo. Importiert wird mit einer Warnung,
  gepruefte Summen gibt es dann nur ueber den Anschluss der Auszuege.

Vorgemerkte Umsaetze werden uebersprungen: sie koennen sich noch aendern oder
verschwinden, und der naechste Export bringt sie gebucht.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from finctl.ingest.base import ParseResult, RawTxn, StatementHeader, find_iban
from finctl.ledger.db import parse_de_amount

SALDOQUELLEN = ("spalte", "kopf", "ende", "keiner")


@dataclass(frozen=True, slots=True)
class CsvProfil:
    id: str
    bank: str
    #: Spalten, die in der Kopfzeile stehen muessen -- daran wird erkannt.
    erkennung: tuple[str, ...]
    datum: str
    datumsformat: str = "%d.%m.%Y"
    trenner: str = ";"
    dezimal: str = ","
    #: Ein vorzeichenbehafteter Betrag -- oder Soll und Haben getrennt.
    betrag: str | None = None
    soll: str | None = None
    haben: str | None = None
    wertstellung: str | None = None
    #: Die erste nicht leere dieser Spalten ist die Gegenpartei.
    gegenpartei: tuple[str, ...] = ()
    #: Fuer Eingaenge, wo die Bank sie anders nennt (DKB: dort steht beim
    #: Eingang der Kontoinhaber selbst als Empfaenger).
    gegenpartei_eingang: tuple[str, ...] = ()
    #: Diese Spalten ergeben zusammen den Verwendungszweck.
    zweck: tuple[str, ...] = ()
    iban: str | None = None
    buchungstext: str | None = None
    #: Nur Zeilen, in denen diese Spalte diesen Wert hat (gebucht statt vorgemerkt).
    nur_wenn: tuple[str, str] | None = None
    #: Zeilen, deren Datum so lautet, sind noch nicht gebucht (comdirect: "offen").
    datum_offen: str | None = None
    #: Spalten, die nur in den Suchtext der Regeln gehen (Mandatsreferenz,
    #: Glaeubiger-ID): im PDF stehen sie im Buchungstext, und Regeln
    #: erkennen einen Kredit oft nur an ihnen.
    nur_suchtext: tuple[str, ...] = ()
    #: Der Tag des Kontostands ist noch nicht abgeschlossen (DKB: der Stand
    #: vom Tag des Exports). Seine Buchungen bleiben fuer den naechsten
    #: Export, der Auszug endet am Vortag.
    letzter_tag_offen: bool = False
    saldo: str = "keiner"
    saldospalte: str | None = None
    #: Regulaere Ausdruecke fuer Saldozeilen ausserhalb der Tabelle; Gruppe `betrag`.
    saldo_anfang: str | None = None
    saldo_ende: str | None = None
    #: Regulaerer Ausdruck fuer den exportierten Zeitraum; Gruppen `von`, `bis`
    #: (TT.MM.JJJJ). Ohne ihn reicht der Auszug von der ersten zur letzten Buchung.
    zeitraum: str | None = None
    #: Regulaerer Ausdruck fuer die eigene IBAN ausserhalb der Tabelle.
    konto_kopf: str | None = None
    #: Spalte mit der eigenen IBAN in jeder Zeile.
    konto_spalte: str | None = None
    #: Woher die Beschreibung stammt -- ehrlich, damit niemand ein Profil fuer
    #: geprueft haelt, das nur nach Doku geschrieben ist.
    quelle: str = ""
    eigen: bool = False
    #: Spalten, die gar nicht gebraucht werden, aber zur Erkennung gehoeren.
    extra: dict = field(default_factory=dict)


# ------------------------------------------------------------------ Lesen

def text_lesen(path: Path) -> str:
    """Den Export als Text. Viele Banken schreiben noch Windows-1252."""
    roh = path.read_bytes()
    for kodierung in ("utf-8-sig", "cp1252"):
        try:
            return roh.decode(kodierung)
        except UnicodeDecodeError:
            continue
    return roh.decode("latin-1")


def _zellen(zeile: str, trenner: str) -> list[str]:
    return [z.strip() for z in next(csv.reader([zeile], delimiter=trenner))] if zeile else []


def kopfzeile(text: str, profil: CsvProfil) -> int | None:
    """Die Zeile, in der alle Erkennungsspalten stehen -- oder None."""
    gesucht = {s.casefold() for s in profil.erkennung}
    for nr, zeile in enumerate(text.splitlines()):
        if profil.trenner not in zeile:
            continue
        zellen = {z.casefold() for z in _zellen(zeile, profil.trenner)}
        if gesucht <= zellen:
            return nr
    return None


def _betrag(roh: str, dezimal: str) -> int:
    roh = (roh or "").replace("€", "").replace("EUR", "").strip()
    if not roh:
        raise ValueError("leerer Betrag")
    if dezimal == ",":
        return parse_de_amount(roh)
    try:
        return int((Decimal(roh.replace(",", "")) * 100).to_integral_value())
    except InvalidOperation:
        raise ValueError(f"kein Betrag: {roh!r}") from None


def _datum(roh: str, formate: str) -> str:
    for fmt in formate.split("|"):
        try:
            return datetime.strptime(roh.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"kein Datum im Format {formate}: {roh!r}")


def _suchen(muster: str | None, text: str) -> re.Match | None:
    return re.search(muster, text, re.M) if muster else None


# ------------------------------------------------------------------ Parser

class CsvBankParser:
    """Ein Parser fuer ein CsvProfil -- dieselbe Schnittstelle wie jedes Profil."""

    version = "1.0.0"

    def __init__(self, profil: CsvProfil):
        self.profil = profil
        self.profile_id = profil.id

    def matches(self, text: str, path: Path) -> bool:
        return path.suffix.lower() in (".csv", ".txt") and kopfzeile(text, self.profil) is not None

    def parse(self, pages: list[str], path: Path) -> ParseResult:
        p = self.profil
        text = "\n".join(pages)
        kopf = kopfzeile(text, p)
        if kopf is None:
            raise ValueError(f"{p.bank}: Kopfzeile nicht gefunden")
        zeilen = text.splitlines()
        spalten = _zellen(zeilen[kopf], p.trenner)
        index = {}
        for i, name in enumerate(spalten):
            index.setdefault(name.casefold(), i)   # doppelte Spalten: die erste gilt

        def wert(zellen: list[str], name: str | None) -> str:
            if not name:
                return ""
            i = index.get(name.casefold())
            return zellen[i].strip() if i is not None and i < len(zellen) else ""

        roh = []
        for zeile in zeilen[kopf + 1:]:
            zellen = _zellen(zeile, p.trenner)
            if len(zellen) < len(spalten) // 2 or not wert(zellen, p.datum):
                continue                       # Leerzeile, Fusszeile, Saldozeile
            if p.datum_offen and wert(zellen, p.datum).casefold() == p.datum_offen.casefold():
                continue
            if p.nur_wenn and wert(zellen, p.nur_wenn[0]).casefold() != p.nur_wenn[1].casefold():
                continue
            try:
                _datum(wert(zellen, p.datum), p.datumsformat)
            except ValueError:
                continue                       # eine Zeile, die kein Umsatz ist
            roh.append(zellen)
        return self._ergebnis(roh, wert, text, "\n".join(zeilen[:kopf]), path)

    def _betrag_der_zeile(self, z, wert) -> int:
        p = self.profil
        if p.betrag:
            return _betrag(wert(z, p.betrag), p.dezimal)
        soll = wert(z, p.soll)
        haben = wert(z, p.haben)
        return ((_betrag(haben, p.dezimal) if haben else 0)
                - abs(_betrag(soll, p.dezimal) if soll else 0))

    def _ergebnis(self, roh, wert, text: str, vorspann: str, path: Path) -> ParseResult:
        p = self.profil
        # Chronologisch, stabil. Viele Banken exportieren neueste zuerst; dann
        # wird die Datei umgedreht, damit die Reihenfolge innerhalb eines Tages
        # erhalten bleibt -- der laufende Saldo haengt an ihr.
        daten = [_datum(wert(z, p.datum), p.datumsformat) for z in roh]
        if len(daten) > 1 and daten[0] > daten[-1]:
            roh, daten = list(reversed(roh)), list(reversed(daten))
        paare = sorted(zip(daten, range(len(roh)), strict=True))
        roh = [roh[i] for _, i in paare]

        txns, warnungen = [], []
        for nr, z in enumerate(roh):
            betrag = self._betrag_der_zeile(z, wert)
            quellen = (p.gegenpartei_eingang if betrag > 0 and p.gegenpartei_eingang
                       else p.gegenpartei)
            gegen = next((wert(z, s) for s in quellen if wert(z, s)), "")
            zweck = " ".join(wert(z, s) for s in p.zweck if wert(z, s))
            art = wert(z, p.buchungstext)
            txns.append(RawTxn(
                booking_date=_datum(wert(z, p.datum), p.datumsformat),
                value_date=(_datum(wert(z, p.wertstellung), p.datumsformat)
                            if wert(z, p.wertstellung) else None),
                amount_cents=betrag,
                raw_text=" ".join(t for t in (art, gegen, zweck, *(
                    f"{s}: {wert(z, s)}" for s in p.nur_suchtext if wert(z, s))) if t),
                counterparty=gegen or None,
                counterparty_iban=(wert(z, p.iban).replace(" ", "") or None) if p.iban else None,
                purpose=zweck or None,
                tx_type=art or None,
                seq=nr))

        if not txns:
            raise ValueError(f"{p.bank}: keine gebuchten Umsätze in {path.name}")
        # Der exportierte Zeitraum, nicht die erste und letzte Buchung: ein
        # Monat ohne Buchung am Letzten saehe sonst unvollstaendig aus.
        von, bis = txns[0].booking_date, txns[-1].booking_date
        if z := self._zeitraum(vorspann, txns, warnungen):
            von, bis = z
        anfang, ende = self._salden(roh, wert, text, txns, warnungen, bis if z else None)
        if p.letzter_tag_offen and z:
            # Was heute schon gebucht ist, steht im Kontostand und in der
            # Datei; was heute noch kommt, in keinem. Der Tag geht deshalb
            # ganz in den naechsten Export, der an diesem Tag beginnt.
            offen = [t for t in txns if t.booking_date == bis]
            txns = [t for t in txns if t.booking_date < bis]
            if not txns:
                raise ValueError(f"{p.bank}: vor dem {bis} ist nichts gebucht -- "
                                 "der Export braucht mindestens einen abgeschlossenen Tag")
            ende -= sum(t.amount_cents for t in offen)
            bis = (datetime.strptime(bis, "%Y-%m-%d") - timedelta(days=1)).date().isoformat()
            if offen:
                warnungen.append(f"{len(offen)} Buchung(en) vom Tag des Exports bleiben für "
                                 "den nächsten Export, der an diesem Tag beginnt")
        # Die eigene IBAN: aus dem Vorspann, aus einer Spalte je Zeile, oder
        # die erste deutsche IBAN vor der Tabelle.
        treffer = _suchen(p.konto_kopf, vorspann)
        konto = (treffer.group("iban").replace(" ", "") if treffer
                 else wert(roh[0], p.konto_spalte).replace(" ", "") if p.konto_spalte
                 else find_iban(vorspann))
        return ParseResult(
            header=StatementHeader(
                account_hint=konto or p.bank,
                period_start=von, period_end=bis,
                balance_start_cents=anfang, balance_end_cents=ende),
            transactions=txns, warnings=warnungen)

    def _zeitraum(self, vorspann: str, txns, warnungen) -> tuple[str, str] | None:
        """Der Zeitraum, den die Bank im Vorspann nennt -- None ohne ihn.

        Die Buchungsdaten allein sagen nicht, ob ein Monat vollstaendig ist:
        ein ganzer September, dessen letzte Buchung am 28. lag, reichte bis
        zum 28., und der Monatsabschluss meldete ihn als fehlend. Ein genannter
        Zeitraum gilt nur, wenn er alle Buchungen umschliesst.
        """
        treffer = _suchen(self.profil.zeitraum, vorspann)
        if not treffer:
            return None
        von = _datum(treffer.group("von"), "%d.%m.%Y")
        bis = _datum(treffer.group("bis"), "%d.%m.%Y")
        if von <= txns[0].booking_date and txns[-1].booking_date <= bis:
            return von, bis
        warnungen.append(f"Zeitraum im Kopf ({von} bis {bis}) passt nicht zu den "
                         f"Buchungen; es gelten die Buchungsdaten.")
        return None

    def _salden(self, roh, wert, text, txns, warnungen,
                bis: str | None = None) -> tuple[int, int]:
        p = self.profil
        summe = sum(t.amount_cents for t in txns)
        if p.saldo == "spalte":
            salden = [_betrag(wert(z, p.saldospalte), p.dezimal) for z in roh]
            for vorher, t, nachher in zip(salden, txns[1:], salden[1:], strict=False):
                if vorher + t.amount_cents != nachher:
                    warnungen.append(f"Saldo springt am {t.booking_date}: "
                                     "fehlt eine Zeile im Export?")
                    break
            return salden[0] - txns[0].amount_cents, salden[-1]
        if p.saldo == "kopf":
            a, e = _suchen(p.saldo_anfang, text), _suchen(p.saldo_ende, text)
            if not a or not e:
                raise ValueError(f"{p.bank}: Anfangs- oder Endsaldo fehlt im Export")
            return _betrag(a.group("betrag"), p.dezimal), _betrag(e.group("betrag"), p.dezimal)
        if p.saldo == "ende":
            e = _suchen(p.saldo_ende, text)
            if not e:
                raise ValueError(f"{p.bank}: Endsaldo fehlt im Export")
            # Der Saldo gilt fuer seinen Tag. Liegt der nach dem Zeitraum,
            # steckt darin, was dazwischen gebucht wurde, und der errechnete
            # Anfang waere um genau das falsch.
            stand = e.groupdict().get("datum")
            if bis and stand and _datum(stand, "%d.%m.%Y") != bis:
                raise ValueError(
                    f"{p.bank}: der Kontostand ist vom {stand}, der Zeitraum endet am "
                    f"{datetime.strptime(bis, '%Y-%m-%d'):%d.%m.%Y}. Den Export bis heute "
                    "wählen, dann gilt der Kontostand für das Ende des Zeitraums.")
            ende = _betrag(e.group("betrag"), p.dezimal)
            warnungen.append("Anfangssaldo errechnet: geprüft wird er erst am "
                             "Anschluss an den vorigen Auszug")
            return ende - summe, ende
        warnungen.append("Der Export trägt keinen Saldo: geprüft wird nur der "
                         "Anschluss an den vorigen Auszug")
        return 0, summe


# ------------------------------------------------------------------ Profile

def profil_aus(pid: str, roh: dict, *, eigen: bool = False) -> CsvProfil:
    """Ein Profil aus einem YAML-Eintrag -- fuer die selbst beschriebenen."""
    def tupel(wert) -> tuple[str, ...]:
        if not wert:
            return ()
        return (wert,) if isinstance(wert, str) else tuple(str(x) for x in wert)

    saldo = str(roh.get("saldo") or "keiner")
    if saldo not in SALDOQUELLEN:
        raise ValueError(f"{pid}: saldo ist {', '.join(SALDOQUELLEN)}")
    if not roh.get("datum") or not (roh.get("betrag") or (roh.get("soll") and roh.get("haben"))):
        raise ValueError(f"{pid}: Datum und Betrag (oder Soll und Haben) fehlen")
    if saldo == "spalte" and not roh.get("saldospalte"):
        raise ValueError(f"{pid}: saldo: spalte braucht eine saldospalte")
    nur = roh.get("nur_wenn")
    return CsvProfil(
        id=pid, bank=str(roh.get("bank") or pid),
        erkennung=tupel(roh.get("erkennung")) or tuple(
            s for s in (roh.get("datum"), roh.get("betrag"), roh.get("soll"),
                        roh.get("haben")) if s),
        datum=str(roh["datum"]), datumsformat=str(roh.get("datumsformat") or "%d.%m.%Y"),
        trenner=str(roh.get("trenner") or ";"), dezimal=str(roh.get("dezimal") or ","),
        betrag=roh.get("betrag"), soll=roh.get("soll"), haben=roh.get("haben"),
        wertstellung=roh.get("wertstellung"), gegenpartei=tupel(roh.get("gegenpartei")),
        zweck=tupel(roh.get("zweck")), iban=roh.get("iban"),
        buchungstext=roh.get("buchungstext"),
        nur_wenn=(str(nur[0]), str(nur[1])) if nur else None,
        saldo=saldo, saldospalte=roh.get("saldospalte"),
        saldo_anfang=roh.get("saldo_anfang"), saldo_ende=roh.get("saldo_ende"),
        quelle=str(roh.get("quelle") or ("selbst zugeordnet" if eigen else "")), eigen=eigen)
