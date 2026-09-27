"""DKB Girokonto statement parser.

Layout, rendered from an invented statement in the real one's shape:

    Datum Erläuterung Betrag Soll EUR Betrag Haben EUR
    Kontostand am 04.08.2026, Auszug Nr. 8              3.344,67
    05.08.2026Basislastschrift                            -180,00
    Beispiel Versicherung AG 08.2026: VNr.
    000-0000000 Erika Mus termann ...
    ...
    Kontostand am 04.09.2026 um 18:04 Uhr               3.487,38

The split surname is not a typo: the PDF renders it that way, and the parser
has to survive it.

Two properties of the layout do the structural work:

* The date column and the Erläuterung column touch, so pdfplumber emits them
  concatenated (`05.08.2026Basislastschrift`). That is convenient rather than
  annoying: a leading `DD.MM.YYYY` is an unambiguous marker for "new
  transaction", and everything until the next one is description.
* Both balances are printed, so the parse can be *verified* rather than
  trusted -- see reconcile().
"""

from __future__ import annotations

import re
from datetime import datetime
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

PROFILE_ID = "dkb_giro"
VERSION = "1.0.0"

# DKB's kerning makes pdfplumber emit stray spaces *inside* numbers as well as
# inside words -- real examples: `2 6.02.2026`, `0 3.03.2026`. A strict date
# pattern silently skips those lines, and the resulting statement fails
# reconciliation rather than importing wrong. So both dates and amounts are
# matched space-tolerantly here, and whitespace is stripped before conversion.
_D = r"\d\s{0,2}"
_DATE = rf"{_D}{_D}\.\s{{0,2}}{_D}{_D}\.\s{{0,2}}{_D}{_D}{_D}\d"
_AMOUNT = r"-?\d{1,3}(?:[.\s]\d{3})*\s?,\s?\d{2}"

# A transaction line: date, then the booking type, then the amount at line end.
_TXN = re.compile(
    rf"^(?P<date>{_DATE})"
    r"(?P<type>.*?)"
    rf"\s+(?P<amount>{_AMOUNT})\s*$"
)

_BALANCE = re.compile(
    rf"^Kontostand am (?P<date>{_DATE})"
    r"(?:,\s*Auszug Nr\.\s*(?P<no>\d+)|\s+um\s+[\d:\s]+\s*Uhr)?"
    rf"\s+(?P<amount>{_AMOUNT})\s*$"
)

_ACCOUNT = re.compile(r"irokonto\s*(?P<no>\d[\d\s]{4,}?),\s*(?P<iban>DE[\d\s]{20,})")
_HEADER_ROW = re.compile(r"^Datum\s+Erläuterung")

# Everything from here down is legal boilerplate, not transactions.
_FOOTER_MARKERS = (
    "Der angegebene Kontostand berücksichtigt nicht",
    "Deutsche Kreditbank AG Vorsitzender des Aufsichtsrats",
    "Guthaben sind als Einlagen",
    "Die Gutschrift von Schecks",
)


def _iso(german_date: str) -> str:
    """Parse a German date, tolerating the stray spaces DKB's kerning creates."""
    return datetime.strptime(re.sub(r"\s+", "", german_date), "%d.%m.%Y").date().isoformat()


class DkbGiroParser:
    profile_id = PROFILE_ID
    version = VERSION

    def matches(self, text: str, path: Path) -> bool:
        return "Deutsche Kreditbank" in text and "Kontoauszug" in text

    def parse(self, pages: list[str], path: Path) -> ParseResult:
        warnings: list[str] = []
        account_hint: str | None = None
        statement_no: str | None = None
        balances: list[tuple[str, int]] = []

        txns: list[RawTxn] = []
        current: RawTxn | None = None
        description: list[str] = []

        def close_current() -> None:
            nonlocal current, description
            if current is None:
                return
            body = [collapse_spaces(line) for line in description if line.strip()]
            current.purpose = collapse_spaces(" ".join(body)) or None
            current.counterparty = guess_counterparty(body)
            current.counterparty_iban = find_iban(" ".join(body))
            current.raw_text = collapse_spaces(f"{current.raw_text} {' '.join(body)}")
            txns.append(current)
            current, description = None, []

        for page_text in pages:
            for line in page_text.split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue

                if any(marker in stripped for marker in _FOOTER_MARKERS):
                    close_current()
                    break  # rest of this page is boilerplate

                if account_hint is None and (m := _ACCOUNT.search(stripped)):
                    account_hint = re.sub(r"\s", "", m.group("iban"))

                if m := _BALANCE.match(stripped):
                    close_current()
                    balances.append((_iso(m.group("date")), parse_de_amount(m.group("amount"))))
                    if m.group("no"):
                        statement_no = m.group("no")
                    continue

                if _HEADER_ROW.match(stripped):
                    close_current()
                    continue

                if m := _TXN.match(stripped):
                    close_current()
                    current = RawTxn(
                        booking_date=_iso(m.group("date")),
                        amount_cents=parse_de_amount(m.group("amount")),
                        raw_text=stripped,
                        tx_type=collapse_spaces(m.group("type")) or None,
                        seq=len(txns),
                    )
                    continue

                if current is not None:
                    description.append(stripped)

        close_current()

        if len(balances) < 2:
            raise ValueError(
                f"expected an opening and a closing Kontostand, found {len(balances)}"
            )

        opening, closing = balances[0], balances[-1]
        header = StatementHeader(
            account_hint=account_hint,
            period_start=opening[0],
            period_end=closing[0],
            balance_start_cents=opening[1],
            balance_end_cents=closing[1],
            statement_no=statement_no,
        )

        if len(balances) > 2:
            warnings.append(
                f"{len(balances)} balance lines found; used first and last"
            )

        for index, txn in enumerate(txns):
            txn.seq = index

        return ParseResult(header=header, transactions=txns, warnings=warnings)


PARSER = DkbGiroParser()
