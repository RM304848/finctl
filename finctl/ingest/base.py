"""Parser contract and text normalization shared by all statement profiles."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class RawTxn:
    """One transaction exactly as the statement presented it."""

    booking_date: str                 # ISO
    amount_cents: int
    raw_text: str
    value_date: str | None = None
    counterparty: str | None = None
    counterparty_iban: str | None = None
    purpose: str | None = None
    customer_ref: str | None = None
    tx_type: str | None = None
    seq: int = 0


@dataclass(slots=True)
class StatementHeader:
    account_hint: str | None          # account/IBAN as printed
    period_start: str                 # ISO
    period_end: str                   # ISO
    balance_start_cents: int
    balance_end_cents: int
    statement_no: str | None = None


@dataclass(slots=True)
class ParseResult:
    header: StatementHeader
    transactions: list[RawTxn] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Which date the printed balances follow. Most issuers reconcile on the
    # booking date, but Scalable's Kontostand lines are value-dated: interest
    # booked on the 1st with Wertstellung on the previous month's last day is
    # already inside the opening balance, and so is everything value-dated on
    # the 1st itself -- the opening is the balance at the end of that day.
    # Reconciling such a statement on booking dates fails by exactly the
    # value of those lines.
    reconcile_basis: str = "booking"        # 'booking' | 'value'


@runtime_checkable
class StatementParser(Protocol):
    """Every profile is pure: PDF in, structured data out, no I/O, no network."""

    profile_id: str
    version: str

    def matches(self, text: str, path: Path) -> bool:
        """Fingerprint the document. Cheap text checks only."""

    def parse(self, pages: list[str], path: Path) -> ParseResult:
        """Parse pre-extracted page texts into a ParseResult."""


# ------------------------------------------------------------- normalization

_LEGAL_FORMS = (
    "gmbhcokg", "gmbhco", "gmbh", "mbh", "aktiengesellschaft", "ag", "kgaa",
    "kg", "ohg", "ug", "se", "ev", "eg", "sarl", "bv", "nv", "ltd", "plc", "inc",
)


def normalize_counterparty(value: str | None) -> str | None:
    """Build a match key that survives the PDF's spurious spacing.

    DKB statements render names with kerning that pdfplumber reads as real
    spaces -- `Erika Mus termann`, `Auszahlu ng`. Tolerance tuning does not fix
    it, because the gaps are genuinely in the character positions. So the key
    discards whitespace and punctuation entirely: `Mus termann` and
    `Mustermann` both collapse to `mustermann`.

    Used for clustering and for rules that opt into counterparty matching.
    Rules match against the full transaction text by default, precisely because
    counterparty extraction from a PDF is best-effort.
    """
    if not value:
        return None
    text = unicodedata.normalize("NFKD", value).casefold()
    text = text.replace("ß", "ss")
    text = "".join(ch for ch in text if ch.isalnum())
    for form in _LEGAL_FORMS:
        if text.endswith(form) and len(text) > len(form) + 2:
            text = text[: -len(form)]
            break
    return text or None


_DIGIT_RUN = re.compile(r"\d{3,}")

# German IBANs, tolerating the grouping spaces statements print them with.
_IBAN_RE = re.compile(r"\bDE\d{2}[ ]?(?:\d{4}[ ]?){4}\d{2}\b")


def find_iban(text: str | None) -> str | None:
    """First German IBAN in a description block, whitespace removed.

    Only some issuers print the counterparty IBAN -- C24 always does, Trade
    Republic sometimes, DKB essentially never. Where it is present it is the
    most reliable way to recognise a transfer between the user's own accounts,
    so it is worth extracting opportunistically.
    """
    if not text:
        return None
    m = _IBAN_RE.search(text)
    return re.sub(r"\s", "", m.group(0)) if m else None


def guess_counterparty(description_lines: list[str]) -> str | None:
    """Best-effort counterparty from a DKB-style description block.

    The name leads the first description line and runs until a reference
    number or date. This is a hint for clustering, not a source of truth --
    which is why rules match on full text by default.
    """
    if not description_lines:
        return None
    tokens: list[str] = []
    for token in description_lines[0].split():
        if _DIGIT_RUN.search(token) or re.match(r"^\d{2}[./]\d{2}", token):
            break
        tokens.append(token)
        if len(tokens) >= 8:
            break
    return " ".join(tokens).strip(" ,.:;-") or None


def collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
