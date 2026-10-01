"""Sparda-Bank Giro statement parser.

    Bu-Tag Wert Vorgang
    alter Kontostand vom 31.07.2026            612,30 H
    04.08. 04.08. Dauerauftragsgutschr         540,00 H
    Erika Mustermann
    Musterweg
    28.08. 30.08. Darlehenstilgung             540,00 S
    Kreditrate
    RECHN.ZINS 150,00 TILG./ENTG. 390,00
    neuer Kontostand vom 31.08.2026            612,30 H

Two things make this account worth parsing despite being small:

* It services a property loan, and prints the interest/principal
  split on every payment (`RECHN.ZINS ... TILG./ENTG. ...`). That split never
  appears on the DKB side, where only the funding transfer is visible -- so
  this is the only place the loan's true amortisation can be observed.
* Direction is carried by an `H`/`S` suffix (Haben/Soll) rather than a sign,
  which parse_de_amount already understands.

Booking dates carry no year; it comes from the Kontostand lines, which do.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from finctl.ingest.base import (
    ParseResult,
    RawTxn,
    StatementHeader,
    collapse_spaces,
    find_iban,
    guess_counterparty,
)
from finctl.ledger.db import parse_de_amount

PROFILE_ID = "sparda_giro"
VERSION = "1.0.0"

_AMOUNT = r"\d{1,3}(?:\.\d{3})*,\d{2}"

_OPENING = re.compile(rf"alter Kontostand vom (\d{{2}}\.\d{{2}}\.\d{{4}})\s+({_AMOUNT})\s*([HS])")
_CLOSING = re.compile(rf"neuer Kontostand vom (\d{{2}}\.\d{{2}}\.\d{{4}})\s+({_AMOUNT})\s*([HS])")
_ACCOUNT = re.compile(r"IBAN:\s*(DE[\d\s]{20,})")

_TXN = re.compile(
    rf"^(?P<booking>\d{{2}}\.\d{{2}}\.)\s+(?P<valuta>\d{{2}}\.\d{{2}}\.)\s+"
    rf"(?P<type>.+?)\s+(?P<amount>{_AMOUNT})\s+(?P<dc>[HS])\s*$"
)

# The Tilgung/Zinsen split Sparda prints under a loan payment.
_LOAN_SPLIT = re.compile(
    rf"RECHN\.ZINS\s+(?P<zinsen>{_AMOUNT})\s+TILG\./ENTG\.\s+(?P<tilgung>{_AMOUNT})"
)

_STOP = ("Bitte beachten Sie die Hinweise", "Sollzins geduldete")


def _signed(amount: str, dc: str) -> int:
    cents = parse_de_amount(amount)
    return cents if dc.upper() == "H" else -cents


class SpardaGiroParser:
    profile_id = PROFILE_ID
    version = VERSION

    def matches(self, text: str, path: Path) -> bool:
        # Der CSV-Export nennt die Bank auch, gehoert aber dem Atruvia-Profil
        # `volksbank_csv` (finctl/ingest/profiles/banken_csv.py) -- wie bei der DKB.
        if path.suffix.lower() == ".csv":
            return False
        return "Sparda" in text.replace(" ", "") or "GENODEF1S01" in text

    def parse(self, pages: list[str], path: Path) -> ParseResult:
        warnings: list[str] = []
        opening = closing = None
        account_hint: str | None = None

        rows: list[tuple[str, str, int, str]] = []
        bodies: list[list[str]] = []
        current: list[str] | None = None

        for page_text in pages:
            for line in page_text.split("\n"):
                stripped = line.strip()
                if not stripped or stripped.startswith(_STOP):
                    continue

                if account_hint is None and (m := _ACCOUNT.search(stripped)):
                    account_hint = re.sub(r"\s", "", m.group(1))

                if m := _OPENING.search(stripped):
                    opening = (datetime.strptime(m.group(1), "%d.%m.%Y").date(),
                               _signed(m.group(2), m.group(3)))
                    current = None
                    continue
                if m := _CLOSING.search(stripped):
                    closing = (datetime.strptime(m.group(1), "%d.%m.%Y").date(),
                               _signed(m.group(2), m.group(3)))
                    current = None
                    continue

                if m := _TXN.match(stripped):
                    rows.append((m.group("booking"), collapse_spaces(m.group("type")),
                                 _signed(m.group("amount"), m.group("dc")), stripped))
                    current = []
                    bodies.append(current)
                    continue

                if current is not None:
                    current.append(stripped)

        if opening is None or closing is None:
            raise ValueError("alter/neuer Kontostand not found")

        period = (opening[0], closing[0])
        txns: list[RawTxn] = []
        for (booking, tx_type, cents, raw), body in zip(rows, bodies, strict=True):
            body = [collapse_spaces(b) for b in body if b.strip()]
            joined = " ".join(body)

            # Preserve the loan split verbatim in the note: it is the only
            # place that loan's amortisation is visible.
            note = None
            if m := _LOAN_SPLIT.search(joined):
                note = (f"Zinsen {m.group('zinsen')} / Tilgung {m.group('tilgung')}")

            txns.append(
                RawTxn(
                    booking_date=self._resolve_year(booking, period),
                    amount_cents=cents,
                    raw_text=collapse_spaces(f"{raw} {joined}"),
                    tx_type=tx_type,
                    purpose=collapse_spaces(joined) or None,
                    counterparty=guess_counterparty(body),
                    counterparty_iban=find_iban(joined),
                    customer_ref=note,
                )
            )

        txns.sort(key=lambda t: t.booking_date)
        for index, txn in enumerate(txns):
            txn.seq = index

        return ParseResult(
            header=StatementHeader(
                account_hint=account_hint,
                period_start=opening[0].isoformat(),
                period_end=closing[0].isoformat(),
                balance_start_cents=opening[1],
                balance_end_cents=closing[1],
            ),
            transactions=txns,
            warnings=warnings,
        )

    @staticmethod
    def _resolve_year(day_month: str, period: tuple[date, date]) -> str:
        day, month = (int(p) for p in day_month.strip(".").split("."))
        start, end = period
        for year in (start.year, end.year):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if start <= candidate <= end:
                return candidate.isoformat()
        return date(end.year, month, day).isoformat()


PARSER = SpardaGiroParser()
