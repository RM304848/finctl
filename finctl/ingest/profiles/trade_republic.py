"""Trade Republic Kontoauszug parser.

This layout cannot be parsed from flat text, so it is the one profile that
works from word coordinates.

    DATUM   TYP      BESCHREIBUNG          ZAHLUNGSEINGANG ZAHLUNGSAUSGANG  SALDO
    01 Jan.
            Bonus    Cash reward allocation          12,34 €      21.480,55 €
    2026

Three problems and how they are solved:

* **The date cell wraps.** `01 Jan.` and `2026` sit on different text lines
  from the row they belong to, so line-based parsing splits one transaction
  across three lines. Rows are therefore reconstructed from word `top`
  coordinates, and the date column is gathered within a vertical band.
* **Type and description run together** in flat text
  (`KartentransaktionTegut Filiale`). By x-position they are cleanly separate.
* **The sign lives in the column, not the number.** `12,34` at x=369 is an
  inflow; `4,68` at x=423 is an outflow. Both print unsigned.

The running SALDO column makes every row self-checking: the signed amount must
equal the change in balance since the previous row. Column position decides the
sign, the balance delta proves it, and any disagreement is reported rather than
guessed at.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pdfplumber

from finctl.ingest.base import (
    ParseResult,
    RawTxn,
    StatementHeader,
    collapse_spaces,
    find_iban,
)
from finctl.ledger.db import parse_de_amount

PROFILE_ID = "trade_republic"
VERSION = "1.0.0"

# Column boundaries in PDF points, from the header row of a real statement.
X_TYPE = 100.0
X_DESCRIPTION = 158.0
X_AMOUNTS = 365.0
X_OUTFLOW = 410.0      # inflow column starts ~369, outflow ~423
X_SALDO = 470.0

ROW_TOLERANCE = 4.0    # words within this vertical distance share a row
DATE_BAND = 14.0       # how far the wrapped date may sit from its row

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "mär": 3, "mrz": 3, "apr": 4, "mai": 5,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dez": 12,
}

_AMOUNT = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}$")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_DAY = re.compile(r"^\d{1,2}$")


def _month(token: str) -> int | None:
    return _MONTHS.get(token.strip(". ").casefold()[:3])


# Trade Republic phrases transfers as "Outgoing transfer for X" / "Incoming
# transfer from X". Taking the text before the preposition makes every transfer
# share the counterparty "Outgoing transfer", which collapses unrelated
# payments into one useless cluster. The name is the part *after* it.
# The TYP column holds the booking type, but a card merchant is rendered as one
# unbroken token joined to it -- `KartentransaktionBeispielcafe`,
# `KartentransaktionJET-Tankstellenull`. Splitting the known prefixes off is
# what makes the merchant reachable as a counterparty at all.
_TYPE_PREFIXES = (
    "Kartentransaktion", "SEPA-Lastschrift", "Überweisung", "Handel",
    "Zinsen", "Steuern", "Bonus", "Ertrag", "Gebühr", "Prämie",
)


def _split_type(raw: str) -> tuple[str, str]:
    """Separate the booking type from the merchant glued onto it."""
    text = (raw or "").strip()
    for prefix in _TYPE_PREFIXES:
        if text.startswith(prefix) and len(text) > len(prefix):
            return prefix, text[len(prefix):].strip(" -:")
        if text == prefix:
            return prefix, ""
    return text, ""


_TRANSFER_PHRASE = re.compile(
    r"^(?:Outgoing transfer for|Incoming transfer from|Sepa Direct Debit transfer to)\s+",
    re.IGNORECASE,
)


def _counterparty(description: str, merchant: str = "") -> str | None:
    # TR appends a literal "null" where a field is absent.
    text = _TRANSFER_PHRASE.sub("", (description or "").strip()).strip(" .")
    if text.endswith("null"):
        text = text[:-4].strip()
    if not text and merchant:
        text = merchant
    if text.endswith("null"):
        text = text[:-4].strip()
    return text[:60] or None


class TradeRepublicParser:
    profile_id = PROFILE_ID
    version = VERSION

    def matches(self, text: str, path: Path) -> bool:
        return "TRADE REPUBLIC" in text.upper() and "UMSATZÜBERSICHT" in text.upper()

    def parse(self, pages: list[str], path: Path) -> ParseResult:
        warnings: list[str] = []
        header = self._parse_overview(pages)

        rows: list[dict] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                stop_y = self._section_stop_y(page)
                rows.extend(self._page_rows(page, stop_y))
                if stop_y is not None:
                    # UMSATZÜBERSICHT has ended; everything after it is a
                    # different table (see _SECTION_END).
                    break

        txns: list[RawTxn] = []
        previous_saldo: int | None = header.balance_start_cents

        for row in rows:
            amount, saldo = row["amount_cents"], row["saldo_cents"]

            # The balance delta is authoritative when available: it is arithmetic
            # rather than inference from a column boundary.
            if previous_saldo is not None and saldo is not None:
                delta = saldo - previous_saldo
                if amount is not None and abs(delta) != abs(amount):
                    warnings.append(
                        f"{row['date']}: column amount {amount} disagrees with "
                        f"balance delta {delta}; used the balance delta"
                    )
                amount = delta
            elif amount is not None and row["is_outflow"]:
                amount = -amount

            if amount is None:
                warnings.append(f"{row['date']}: no amount found, row skipped")
                continue

            booking_type, merchant = _split_type(row["type"])
            txns.append(
                RawTxn(
                    booking_date=row["date"],
                    amount_cents=amount,
                    raw_text=collapse_spaces(
                        f"{booking_type} {merchant} {row['description']}"),
                    tx_type=booking_type or None,
                    purpose=collapse_spaces(
                        f"{merchant} {row['description']}").strip() or None,
                    counterparty=_counterparty(row["description"], merchant),
                    counterparty_iban=find_iban(row["description"]),
                    seq=len(txns),
                )
            )
            if saldo is not None:
                previous_saldo = saldo

        return ParseResult(header=header, transactions=txns, warnings=warnings)

    # ------------------------------------------------------------- header

    def _parse_overview(self, pages: list[str]) -> StatementHeader:
        """Read period and balances from the KONTOÜBERSICHT block."""
        text = "\n".join(pages)

        period = re.search(
            r"DATUM\s+(\d{1,2})\s+(\w+)\.?\s+(\d{4})\s*[-–]\s*(\d{1,2})\s+(\w+)\.?\s+(\d{4})",
            text,
        )
        if not period:
            raise ValueError("no statement period found in KONTOÜBERSICHT")
        start = date(int(period.group(3)), _month(period.group(2)), int(period.group(1)))
        end = date(int(period.group(6)), _month(period.group(5)), int(period.group(4)))

        cash = re.search(
            r"Cashkonto\s+(-?[\d.]+,\d{2})\s*€\s+(-?[\d.]+,\d{2})\s*€\s+"
            r"(-?[\d.]+,\d{2})\s*€\s+(-?[\d.]+,\d{2})\s*€",
            text,
        )
        if not cash:
            raise ValueError("no Cashkonto summary row found")

        iban = re.search(r"IBAN\s+(DE[\dA-Z]+)", text)
        return StatementHeader(
            account_hint=iban.group(1) if iban else None,
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            balance_start_cents=parse_de_amount(cash.group(1)),
            balance_end_cents=parse_de_amount(cash.group(4)),
        )

    # --------------------------------------------------------------- rows

    @staticmethod
    def _section_stop_y(page) -> float | None:
        """Where UMSATZÜBERSICHT ends on this page, if it does.

        The statement continues past the cash transactions with a
        GELDMARKTFONDS table whose columns are
        `DATUM ZAHLUNGSART GELDMARKTFONDS STÜCK KURS PRO STÜCK BETRAG`.
        Its BETRAG column sits where the cash table prints SALDO, and its
        `KURS PRO STÜCK` is always `1,00 €` -- so parsing it as cash produces
        transactions that look plausible and are entirely wrong.

        Those rows are the money-market sweep, not cash movements: the cash
        they represent is already counted in the UMSATZÜBERSICHT.
        """
        markers = ("ZAHLUNGSART", "GELDMARKTFONDS", "HINWEISE", "BESTÄTIGUNG")
        tops = [
            word["top"]
            for word in page.extract_words()
            if word["text"].upper().strip(":") in markers
        ]
        return min(tops) if tops else None

    def _page_rows(self, page, stop_y: float | None = None) -> list[dict]:
        words = page.extract_words()
        if stop_y is not None:
            words = [w for w in words if w["top"] < stop_y]
        if not words:
            return []

        bands: dict[float, list[dict]] = {}
        for word in words:
            key = next(
                (k for k in bands if abs(k - word["top"]) <= ROW_TOLERANCE),
                round(word["top"], 1),
            )
            bands.setdefault(key, []).append(word)

        date_parts = [
            (top, items) for top, items in bands.items()
            if any(w["x0"] < X_TYPE for w in items)
        ]

        rows: list[dict] = []
        for top in sorted(bands):
            items = bands[top]
            saldo = [w for w in items if w["x0"] >= X_SALDO and _AMOUNT.match(w["text"])]
            if not saldo:
                continue  # not a transaction row: only they carry a balance

            amounts = [
                w for w in items
                if X_AMOUNTS <= w["x0"] < X_SALDO and _AMOUNT.match(w["text"])
            ]
            booking = self._date_for(top, date_parts)
            if booking is None:
                continue

            rows.append({
                "date": booking,
                "type": collapse_spaces(" ".join(
                    w["text"] for w in sorted(items, key=lambda w: w["x0"])
                    if X_TYPE <= w["x0"] < X_DESCRIPTION)),
                "description": collapse_spaces(" ".join(
                    w["text"] for w in sorted(items, key=lambda w: w["x0"])
                    if X_DESCRIPTION <= w["x0"] < X_AMOUNTS)).replace("null", "").strip(),
                "amount_cents": parse_de_amount(amounts[0]["text"]) if amounts else None,
                "is_outflow": bool(amounts) and amounts[0]["x0"] >= X_OUTFLOW,
                "saldo_cents": parse_de_amount(saldo[-1]["text"]),
            })
        return rows

    @staticmethod
    def _date_for(top: float, date_parts: list[tuple[float, list[dict]]]) -> str | None:
        """Reassemble `01 Jan.` / `2026` from the band around this row."""
        day = month = year = None
        for part_top, items in date_parts:
            if abs(part_top - top) > DATE_BAND:
                continue
            for word in sorted(items, key=lambda w: w["x0"]):
                if word["x0"] >= X_TYPE:
                    continue
                text = word["text"]
                if _YEAR.match(text):
                    year = int(text)
                elif _DAY.match(text):
                    day = int(text)
                elif (m := _month(text)) is not None:
                    month = m
        if day and month and year:
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return None
        return None


PARSER = TradeRepublicParser()
