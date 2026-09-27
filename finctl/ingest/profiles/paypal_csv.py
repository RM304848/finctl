"""PayPal-Kontoauszug aus dem CSV-Export.

PayPal ist kein Zwischenhaendler, sondern ein Konto. Solange es keines war,
stand im Ledger nur, dass Geld zu "PayPal Europe S.a.r.l. et Cie S.C.A"
geflossen ist -- der Haendler, die Person, der Anlass lagen alle auf PayPals
Seite. Das waren die Buchungen, die am laengsten unbeschriftet blieben.

Der Export ist ein gewoehnliches Kontobuch:

    Datum      Beschreibung                            Netto    Guthaben
    23.12.2025 Handyzahlung (Erika Mustermann)        -33,00      -33,00
    23.12.2025 Bankgutschrift auf PayPal-Konto         33,00        0,00

Zwei Zeilen, zwei verschiedene Dinge: die ZAHLUNG ist der Vorgang, die
GUTSCHRIFT ist ihre Deckung von einem anderen eigenen Konto. Genau diese
Trennung fehlte -- auf der Bank war nur die Deckung sichtbar, und die trug
keinen Namen.

`Guthaben` ist ein echter laufender Saldo, und er schliesst das
Abstimmtor: Anfangssaldo plus Summe gleich Endsaldo, ohne dass irgendetwas
geschaetzt werden muss.

ZWEI EIGENHEITEN, die der Export mitbringt:

* **Fremdwaehrung.** Eine USD-Zahlung fuehrt einen eigenen USD-Saldo, und ein
  Eurobetrag steht nirgends im Export. Der Umrechnungskurs steht auf der
  Kartenabrechnung, nicht hier. Solche Zeilen werden mit ihrem Nennbetrag
  importiert und in `currency` als das gekennzeichnet, was sie sind -- und die
  Statistik warnt, um welchen Betrag es geht. Da jede Zahlung im selben Moment
  von ihrer Deckung ausgeglichen wird, bleibt der Saldo trotzdem richtig.
* **Die Datei ist nicht chronologisch.** Waehrungen stehen in Bloecken. Sortiert
  wird deshalb nach Zeitstempel, stabil, damit eine Zahlung vor ihrer Deckung
  bleibt -- in derselben Sekunde gebucht, aber in dieser Reihenfolge gemeint.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from finctl.ingest.base import ParseResult, RawTxn, StatementHeader
from finctl.ledger.db import parse_de_amount

PROFILE_ID = "paypal_csv"
VERSION = "1.0.0"

# Die Spalten, ohne die die Datei nichts aussagt. Ein Export mit anderem
# Zuschnitt -- PayPal bietet mehrere -- soll auffallen und nicht halb passen.
PFLICHTSPALTEN = ("Datum", "Uhrzeit", "Beschreibung", "Währung", "Netto",
                  "Guthaben", "Transaktionscode")

# PayPal nennt die Datei nach dem Zeitraum, den sie abdeckt. Der ist genauer
# als die erste und letzte Buchung darin: ein Jahr ohne Zahlung im Januar
# faengt trotzdem am 1. Januar an.
_ZEITRAUM = re.compile(r"-(\d{14})-(\d{14})-")


def _zeitpunkt(zeile: dict) -> datetime:
    return datetime.strptime(f"{zeile['Datum']} {zeile['Uhrzeit']}",
                             "%d.%m.%Y %H:%M:%S")


class PaypalCsvParser:
    profile_id = PROFILE_ID
    version = VERSION

    def matches(self, text: str, path: Path) -> bool:
        kopf = text.split("\n", 1)[0]
        return all(f'"{spalte}"' in kopf for spalte in PFLICHTSPALTEN)

    def parse(self, pages: list[str], path: Path) -> ParseResult:
        zeilen = list(csv.DictReader(
            "\n".join(pages).splitlines(), skipinitialspace=True))
        # Stabil: bei gleichem Zeitstempel gewinnt die Reihenfolge der Datei,
        # und dort steht die Zahlung vor ihrer Deckung.
        zeilen = [z for _, z in sorted(enumerate(zeilen),
                                       key=lambda p: (_zeitpunkt(p[1]), p[0]))]

        txns: list[RawTxn] = []
        fremd = 0
        for nr, z in enumerate(zeilen):
            betrag = parse_de_amount(z["Netto"])
            waehrung = (z.get("Währung") or "EUR").strip() or "EUR"
            if waehrung != "EUR":
                fremd += abs(betrag)
            name = (z.get("Name") or "").strip()
            absender = (z.get("Absender E-Mail-Adresse") or "").strip()
            # Der Rohtext ist das, worauf die Regeln matchen. Alles hinein, was
            # die Zeile identifiziert -- der Name allein reicht nicht, wenn er
            # fehlt, und die Beschreibung allein nicht, wenn er da ist.
            # Der zugehoerige Code ist die einzige exakte Verbindung zwischen
            # einer Deckung und der Zahlung, die sie deckt -- Betrag und Datum
            # sind es nicht, sobald zwei Zahlungen am selben Tag gleich hoch
            # sind. Er gehoert deshalb in den Rohtext und nicht nur in die
            # Datei.
            zugehoerig = (z.get("Zugehöriger Transaktionscode") or "").strip()
            roh = " ".join(t for t in (z["Beschreibung"], name, absender,
                                       z.get("Rechnungsnummer") or "",
                                       z["Transaktionscode"],
                                       f"zu {zugehoerig}" if zugehoerig else "") if t)
            txns.append(RawTxn(
                booking_date=_zeitpunkt(z).date().isoformat(),
                value_date=_zeitpunkt(z).date().isoformat(),
                amount_cents=betrag,
                raw_text=roh,
                counterparty=name or None,
                purpose=z["Beschreibung"],
                customer_ref=z["Transaktionscode"] or None,
                tx_type=z["Beschreibung"],
                seq=nr,
            ))

        # Der Saldo VOR der ersten Zeile, aus deren eigenem Guthaben
        # zurueckgerechnet. PayPal druckt keinen Anfangssaldo, aber jede Zeile
        # traegt den Stand danach, und das ist dieselbe Auskunft.
        start = (parse_de_amount(zeilen[0]["Guthaben"])
                 - parse_de_amount(zeilen[0]["Netto"])) if zeilen else 0
        ende = parse_de_amount(zeilen[-1]["Guthaben"]) if zeilen else 0

        von, bis = self._zeitraum(path, zeilen)
        ergebnis = ParseResult(
            header=StatementHeader(
                account_hint="PayPal", period_start=von, period_end=bis,
                balance_start_cents=start, balance_end_cents=ende),
            transactions=txns,
        )
        if fremd:
            ergebnis.warnings.append(
                f"{fremd / 100:.2f} in Fremdwaehrung: mit dem Nennbetrag "
                "importiert, der Eurobetrag steht nur auf der Kartenabrechnung")
        return ergebnis

    @staticmethod
    def _zeitraum(path: Path, zeilen: list[dict]) -> tuple[str, str]:
        if treffer := _ZEITRAUM.search(path.name):
            return tuple(datetime.strptime(g, "%Y%m%d%H%M%S").date().isoformat()
                         for g in treffer.groups())
        if not zeilen:
            raise ValueError("leerer Export und kein Zeitraum im Dateinamen")
        return (_zeitpunkt(zeilen[0]).date().isoformat(),
                _zeitpunkt(zeilen[-1]).date().isoformat())


PARSER = PaypalCsvParser()
