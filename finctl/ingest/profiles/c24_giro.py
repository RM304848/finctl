"""C24 Smartkonto statement parser.

Layout:

    Kontoauszug 01/2026 Kontostand 212,40 €
    01.01.2026 - 31.01.2026 Eingeräumter Dispokredit 0,00 €
    Transaktionsübersicht
    Buchung Valuta Transaktionsinformation Betrag
    15.01. 15.01. Lastschrift - 7,99 €
    Mobilfunk Beispiel GmbH
    K1234567 R7654321 V1122334 Tarif S
    IBAN: DE00123456780000000000 / BIC: BEISDEFF
    ...
    Zusammenfassung
    Startsaldo 420,00 €
    Endsaldo 212,40 €

Three things differ from DKB and matter:

* Booking dates carry no year (`15.01.`), so the year comes from the statement
  period. A month earlier than the period's start month belongs to the period's
  end year -- which only arises on a December/January statement.
* The sign is a separate token from the number (`- 4,99 €`, `+ 150,00 €`).
* Transactions are listed newest-first. Order is normalized on the way out so
  seq_in_statement is chronological, matching every other profile.
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
    guess_counterparty,
)
from finctl.ledger.db import parse_de_amount

PROFILE_ID = "c24_giro"
VERSION = "1.0.0"

_AMOUNT = r"\d{1,3}(?:\.\d{3})*,\d{2}"

_PERIOD = re.compile(r"^(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})")
_IBAN = re.compile(r"IBAN:\s*(DE[\dA-Z]+)")
# C24 prints the counterparty's IBAN under each transaction. That is the most
# reliable signal available for identifying transfers between your own
# accounts, so it is captured rather than discarded with the rest of the
# description furniture.
_COUNTERPARTY_IBAN = re.compile(r"IBAN:\s*(DE[0-9A-Z]{18,20})\s*/\s*BIC:")
_START = re.compile(rf"^Startsaldo\s+([+-]?\s*{_AMOUNT})\s*€")
_END = re.compile(rf"^Endsaldo\s+([+-]?\s*{_AMOUNT})\s*€")

_TXN = re.compile(
    r"^(?P<booking>\d{2}\.\d{2}\.)\s+(?P<valuta>\d{2}\.\d{2}\.)\s+"
    r"(?P<type>.+?)\s+(?P<sign>[+-])\s*(?P<amount>" + _AMOUNT + r")\s*€\s*$"
)

# Description blocks end at these; they are layout, not counterparty detail.
#
# The account holder's own name is deliberately NOT in this list. Every page
# repeats an address block that starts with it, but so does the description of
# every transfer the holder made to their own other accounts -- and skipping
# those discarded the counterparty IBAN with them. The page header is handled
# by resetting state at each page boundary instead.
_SKIP_PREFIXES = (
    "C24 Bank GmbH", "Ust.-ID", "Transaktionsübersicht", "Buchung Valuta",
    "Zusammenfassung", "Kontobelastungen", "Kontogutschriften", "Startsaldo",
    "Endsaldo", "Weitere Informationen", "Kontoauszug", "C24 Smartkonto",
    "Girokonto", "BIC:",
)


class C24GiroParser:
    profile_id = PROFILE_ID
    version = VERSION

    def matches(self, text: str, path: Path) -> bool:
        return "C24" in text and "Kontoauszug" in text

    def parse(self, pages: list[str], path: Path) -> ParseResult:
        warnings: list[str] = []
        period: tuple[date, date] | None = None
        account_hint: str | None = None
        start_cents: int | None = None
        end_cents: int | None = None

        raw_rows: list[tuple[str, str, int, str]] = []  # booking, type, cents, raw
        descriptions: list[list[str]] = []
        current: list[str] | None = None

        for page_text in pages:
            # Each page repeats the address and account header. Closing the open
            # description here stops that header attaching to the last
            # transaction of the previous page.
            current = None
            for line in page_text.split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue

                if period is None and (m := _PERIOD.match(stripped)):
                    period = (
                        datetime.strptime(m.group(1), "%d.%m.%Y").date(),
                        datetime.strptime(m.group(2), "%d.%m.%Y").date(),
                    )
                    continue

                if account_hint is None and (m := _IBAN.search(stripped)):
                    account_hint = m.group(1)

                if start_cents is None and (m := _START.match(stripped)):
                    start_cents = parse_de_amount(m.group(1))
                    current = None
                    continue
                if (m := _END.match(stripped)):
                    end_cents = parse_de_amount(m.group(1))
                    current = None
                    continue

                if m := _TXN.match(stripped):
                    cents = parse_de_amount(m.group("amount"))
                    if m.group("sign") == "-":
                        cents = -cents
                    raw_rows.append(
                        (m.group("booking"), collapse_spaces(m.group("type")), cents, stripped)
                    )
                    current = []
                    descriptions.append(current)
                    continue

                if current is not None and (
                        _COUNTERPARTY_IBAN.search(stripped)
                        or not stripped.startswith(_SKIP_PREFIXES)):
                    current.append(stripped)
                elif stripped.startswith(_SKIP_PREFIXES):
                    current = None

        if period is None:
            raise ValueError("no statement period found")
        if start_cents is None or end_cents is None:
            raise ValueError("Startsaldo/Endsaldo not found")

        txns: list[RawTxn] = []
        for (booking, tx_type, cents, raw), body in zip(raw_rows, descriptions, strict=True):
            body = [collapse_spaces(b) for b in body if b.strip()]
            # The statement prints this account's own IBAN in the page header;
            # only a *different* IBAN identifies a counterparty.
            iban = next(
                (m.group(1) for line in body
                 if (m := _COUNTERPARTY_IBAN.search(line)) and m.group(1) != account_hint),
                None,
            )
            # The IBAN line is reference data, not a counterparty name: exclude
            # it when guessing who this was with.
            named = [line for line in body if not _COUNTERPARTY_IBAN.search(line)]
            txns.append(
                RawTxn(
                    booking_date=self._resolve_year(booking, period),
                    amount_cents=cents,
                    raw_text=collapse_spaces(f"{raw} {' '.join(body)}"),
                    tx_type=tx_type,
                    purpose=collapse_spaces(" ".join(named)) or None,
                    counterparty=guess_counterparty(named),
                    counterparty_iban=iban,
                )
            )

        # Statements list newest-first; normalize so seq is chronological.
        txns.sort(key=lambda t: t.booking_date)
        for index, txn in enumerate(txns):
            txn.seq = index

        header = StatementHeader(
            account_hint=account_hint,
            period_start=period[0].isoformat(),
            period_end=period[1].isoformat(),
            balance_start_cents=start_cents,
            balance_end_cents=end_cents,
        )
        return ParseResult(header=header, transactions=txns, warnings=warnings)

    @staticmethod
    def _resolve_year(day_month: str, period: tuple[date, date]) -> str:
        """Attach a year to a `DD.MM.` booking date using the statement period."""
        day, month = (int(part) for part in day_month.strip(".").split("."))
        start, end = period
        for year in (start.year, end.year):
            candidate = date(year, month, day)
            if start <= candidate <= end:
                return candidate.isoformat()
        # Outside the printed period: keep the start year rather than inventing
        # one, and let the reconciliation gate decide whether the parse is sound.
        return date(start.year, month, day).isoformat()


PARSER = C24GiroParser()
