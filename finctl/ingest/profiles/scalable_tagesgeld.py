"""Scalable Capital Tagesgeld statement parser.

    Zeitraum 01.08.2026 - 31.08.2026
    Kontostand am 01.08.2026   51.774,43 EUR
    Kontostand am 31.08.2026   46.575,43 EUR
    Buchung     Wertstellung  Beschreibung                              Betrag
    01.08.2026  31.07.2026    Erhaltene Zinsen                     +118,42 EUR
    01.08.2026  31.07.2026    ... einbehaltene ... Kapitalertragssteuer -29,61 EUR
    08.08.2026  10.08.2026    Abhebung vom Geldkonto            -4.250,00 EUR

The cleanest layout of the five: full dates on both columns, explicit signs,
and an unambiguous currency suffix.

This is the savings account, so it matters for two reasons beyond cash flow:
it is where the buffer (including the 17.000 held for the owner's sister)
actually sits, and it books Zinsen, Kapitalertragsteuer and Solidaritäts-
zuschlag as separate lines -- which is exactly what Anlage KAP needs.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from finctl.ingest.base import ParseResult, RawTxn, StatementHeader, collapse_spaces
from finctl.ledger.db import parse_de_amount

PROFILE_ID = "scalable_tagesgeld"
VERSION = "1.0.0"

_AMOUNT = r"[+-]?\d{1,3}(?:\.\d{3})*,\d{2}"

_PERIOD = re.compile(r"Zeitraum\s+(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})")
_BALANCE = re.compile(rf"Kontostand am\s+(\d{{2}}\.\d{{2}}\.\d{{4}})\s+({_AMOUNT})\s*EUR")
_ACCOUNT = re.compile(r"Verrechnungskonto\s+(DE[\dA-Z]+)")

_TXN = re.compile(
    rf"^(?P<booking>\d{{2}}\.\d{{2}}\.\d{{4}})\s+(?P<valuta>\d{{2}}\.\d{{2}}\.\d{{4}})\s+"
    rf"(?P<desc>.+?)\s+(?P<amount>{_AMOUNT})\s*EUR\s*$"
)

_STOP = ("Scalable Capital Bank GmbH", "Geschäftsführer", "Aufsichtsrat")


class ScalableTagesgeldParser:
    profile_id = PROFILE_ID
    version = VERSION

    def matches(self, text: str, path: Path) -> bool:
        return "Scalable Capital" in text and "Kontoauszug" in text

    def parse(self, pages: list[str], path: Path) -> ParseResult:
        period = None
        account_hint: str | None = None
        balances: list[tuple[str, int]] = []
        txns: list[RawTxn] = []

        for page_text in pages:
            for line in page_text.split("\n"):
                stripped = line.strip()
                if not stripped or stripped.startswith(_STOP):
                    continue

                if period is None and (m := _PERIOD.search(stripped)):
                    period = (
                        datetime.strptime(m.group(1), "%d.%m.%Y").date().isoformat(),
                        datetime.strptime(m.group(2), "%d.%m.%Y").date().isoformat(),
                    )
                    continue

                if account_hint is None and (m := _ACCOUNT.search(stripped)):
                    account_hint = m.group(1)

                if m := _BALANCE.search(stripped):
                    balances.append((
                        datetime.strptime(m.group(1), "%d.%m.%Y").date().isoformat(),
                        parse_de_amount(m.group(2)),
                    ))
                    continue

                if m := _TXN.match(stripped):
                    desc = collapse_spaces(m.group("desc"))
                    txns.append(
                        RawTxn(
                            booking_date=datetime.strptime(
                                m.group("booking"), "%d.%m.%Y").date().isoformat(),
                            value_date=datetime.strptime(
                                m.group("valuta"), "%d.%m.%Y").date().isoformat(),
                            amount_cents=parse_de_amount(m.group("amount")),
                            raw_text=stripped,
                            tx_type=desc[:40],
                            purpose=desc,
                            counterparty=desc[:60],
                            seq=len(txns),
                        )
                    )

        if len(balances) < 2:
            raise ValueError(f"expected two Kontostand lines, found {len(balances)}")
        if period is None:
            period = (balances[0][0], balances[-1][0])

        return ParseResult(
            header=StatementHeader(
                account_hint=account_hint,
                period_start=period[0],
                period_end=period[1],
                balance_start_cents=balances[0][1],
                balance_end_cents=balances[-1][1],
            ),
            transactions=txns,
            warnings=[],
            # Scalable's Kontostand lines follow Wertstellung, not Buchung.
            reconcile_basis="value",
        )


PARSER = ScalableTagesgeldParser()
