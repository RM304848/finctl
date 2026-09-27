"""Statement import: detect, parse, reconcile, persist.

The reconciliation gate is the heart of this module. German statements print
both the opening and closing balance, so a parse can be *proved* complete:

    balance_start + sum(transactions) == balance_end

A statement that fails is rejected rather than partially imported. This is what
makes deterministic PDF parsing trustworthy without any AI in the loop -- and
it replaces the hand-maintained `Kontostand / Delta` rows in the Excel model.
"""

from __future__ import annotations

import hashlib
import importlib
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import yaml

from finctl.ingest.base import ParseResult, StatementParser, normalize_counterparty
from finctl.ledger.db import now_iso
from finctl.pfade import CONFIG_DIR

# Profiles are registered explicitly: an unknown layout must fail loudly rather
# than be guessed at by a nearby parser.
PROFILE_MODULES = {
    "dkb_giro": "finctl.ingest.profiles.dkb_giro",
    "trade_republic": "finctl.ingest.profiles.trade_republic",
    "c24_giro": "finctl.ingest.profiles.c24_giro",
    "sparda_giro": "finctl.ingest.profiles.sparda_giro",
    "scalable_tagesgeld": "finctl.ingest.profiles.scalable_tagesgeld",
    "paypal_csv": "finctl.ingest.profiles.paypal_csv",
    # CSV-Exporte der Banken: beschrieben, nicht programmiert. `modul:kennung`
    # zeigt auf einen Eintrag in PARSERS des Moduls.
    **{k: f"finctl.ingest.profiles.banken_csv:{k}"
       for k in ("ing_csv", "comdirect_csv", "dkb_csv", "sparkasse_csv", "volksbank_csv",
                 "commerzbank_csv", "n26_csv")},
}


def load_parser(profile_id: str) -> StatementParser:
    # Selbst beschriebene Exporte (finctl/ingest/zuordnung.py) stehen in
    # config/, nicht im Code -- sie kommen bei jedem Aufruf frisch aus der Datei.
    if profile_id.startswith("eigen_"):
        from finctl.ingest import zuordnung

        try:
            return zuordnung.eigene()[profile_id]
        except KeyError as exc:
            raise ValueError(f"unknown parser profile: {profile_id}") from exc
    try:
        dotted = PROFILE_MODULES[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown parser profile: {profile_id}") from exc
    modul, _, eintrag = dotted.partition(":")
    try:
        module = importlib.import_module(modul)
    except ModuleNotFoundError as exc:
        raise ValueError(f"parser profile '{profile_id}' is not implemented yet") from exc
    return module.PARSERS[eintrag] if eintrag else module.PARSER


def available_profiles() -> list[str]:
    found = []
    for profile_id, dotted in PROFILE_MODULES.items():
        try:
            importlib.import_module(dotted.partition(":")[0])
        except ModuleNotFoundError:
            continue
        found.append(profile_id)
    from finctl.ingest import zuordnung

    return found + sorted(zuordnung.eigene())


def extract_pages(path: Path) -> list[str]:
    """Den Text eines Auszugs holen, egal in welcher Form er kommt.

    Ein Parserprofil bekommt Seiten und gibt einen ParseResult zurueck -- das
    gilt unveraendert. Nur woher die Seiten stammen, haengt jetzt am Format:
    PayPal liefert kein PDF, sondern CSV, und dafuer einen zweiten Weg durch
    `import_statement` zu legen hiesse, das Abstimmtor zweimal zu haben.
    """
    if path.suffix.lower() == ".csv":
        # Eine Seite, der ganze Text. `matches` sieht damit die Kopfzeile,
        # und das Profil parst selbst -- genau wie bei einem PDF. Viele
        # Banken exportieren noch Windows-1252.
        from finctl.ingest.csvbank import text_lesen

        return [text_lesen(path)]
    if path.suffix.lower() == ".txt":
        # Seitentext, wie ihn `finctl anonymisieren` aus einem PDF schreibt:
        # Seiten durch Seitenvorschub getrennt. So laesst sich ein Parser an
        # einem Beispiel pruefen, das niemandem gehoert.
        from finctl.ingest.csvbank import text_lesen

        return text_lesen(path).split("\f")
    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def detect(pages: list[str], path: Path) -> StatementParser | None:
    joined = "\n".join(pages[:2])
    for profile_id in available_profiles():
        parser = load_parser(profile_id)
        if parser.matches(joined, path):
            return parser
    return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------- reconcile


@dataclass(slots=True)
class Reconciliation:
    expected_cents: int
    actual_cents: int
    delta_cents: int

    @property
    def ok(self) -> bool:
        return self.delta_cents == 0


ADJUSTMENTS_PATH = CONFIG_DIR / "adjustments.yaml"


def apply_corrections(result: ParseResult, account_id: str) -> list[str]:
    """Apply declared corrections to a statement header.

    Used where an issuer's own figures are internally inconsistent and the
    correct value can be proven from other data -- for instance a printed
    opening balance that disagrees with both the previous statement's close
    and this statement's own transaction list.

    Deliberately narrow: this corrects a stated balance, it never invents a
    transaction. Inventing one would put money in the ledger that does not
    exist.
    """
    if not ADJUSTMENTS_PATH.exists():
        return []
    spec = yaml.safe_load(ADJUSTMENTS_PATH.read_text(encoding="utf-8")) or {}
    notes: list[str] = []

    for fix in spec.get("corrections", []):
        if fix.get("account_id") != account_id:
            continue
        if str(fix.get("period_start")) != result.header.period_start:
            continue
        if basis := fix.get("reconcile_basis"):
            result.reconcile_basis = basis
            notes.append(f"reconcile basis set to '{basis}' by declared correction")

        field = fix["field"]
        if not hasattr(result.header, field):
            continue
        current = getattr(result.header, field)
        if current != int(fix["printed_cents"]):
            notes.append(
                f"correction for {field} skipped: statement says {current}, "
                f"but the correction expects {fix['printed_cents']}"
            )
            continue
        setattr(result.header, field, int(fix["corrected_cents"]))
        notes.append(
            f"{field} corrected {fix['printed_cents']} -> {fix['corrected_cents']} "
            f"(declared in adjustments.yaml)"
        )
    return notes


def reconcile(result: ParseResult) -> Reconciliation:
    header = result.header

    if result.reconcile_basis == "value":
        # Count only what the printed balances actually moved: entries whose
        # value date falls inside the period. Entries value-dated earlier are
        # already in the opening balance, though they are still imported --
        # this statement is the only place they appear.
        in_period = [
            txn for txn in result.transactions
            if header.period_start <= (txn.value_date or txn.booking_date)
            <= header.period_end
        ]
    else:
        in_period = result.transactions

    total = sum(txn.amount_cents for txn in in_period)
    expected = header.balance_end_cents - header.balance_start_cents
    return Reconciliation(
        expected_cents=expected,
        actual_cents=total,
        delta_cents=total - expected,
    )


# ------------------------------------------------------------------ dedup


def dedup_hash(account_id: str, txn, occurrence: int) -> str:
    """Stable identity for a transaction.

    Includes an occurrence index so two genuinely identical payments on the
    same day stay two transactions, while a re-imported or overlapping
    statement collapses onto the same rows.
    """
    parts = [
        account_id,
        txn.booking_date,
        str(txn.amount_cents),
        (txn.counterparty or "").strip(),
        (txn.purpose or "").strip(),
        str(occurrence),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def assign_occurrences(account_id: str, transactions) -> list[tuple[object, str]]:
    seen: dict[tuple, int] = defaultdict(int)
    out = []
    for txn in transactions:
        key = (txn.booking_date, txn.amount_cents, txn.counterparty, txn.purpose)
        occurrence = seen[key]
        seen[key] += 1
        out.append((txn, dedup_hash(account_id, txn, occurrence)))
    return out


# ----------------------------------------------------------------- import


@dataclass(slots=True)
class ImportOutcome:
    path: Path
    status: str                       # imported | rejected | skipped | unknown_layout
    account_id: str | None = None
    profile: str | None = None
    transactions: int = 0
    period: tuple[str, str] | None = None
    reconciliation: Reconciliation | None = None
    message: str | None = None


def import_statement(
    conn: sqlite3.Connection,
    path: Path,
    account_id: str,
    *,
    force: bool = False,
) -> ImportOutcome:
    sha = file_sha256(path)
    existing = conn.execute(
        "SELECT id, status FROM statements WHERE file_sha256 = ?", (sha,)
    ).fetchone()
    # Only a *successful* import blocks a retry. A previously rejected
    # statement must be re-parseable, since the usual reason it failed is a
    # parser bug that has since been fixed.
    if existing and existing["status"] == "imported" and not force:
        return ImportOutcome(path=path, status="skipped", account_id=account_id,
                             message="already imported (identical file)")
    if existing:
        conn.execute("DELETE FROM statements WHERE id = ?", (existing["id"],))

    row = conn.execute(
        "SELECT parser_profile FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if row is None:
        return ImportOutcome(path=path, status="rejected", account_id=account_id,
                             message=f"unknown account '{account_id}'")

    pages = extract_pages(path)
    parser = load_parser(row["parser_profile"]) if row["parser_profile"] else None
    if parser is None or not parser.matches("\n".join(pages[:2]), path):
        detected = detect(pages, path)
        if detected is None:
            return ImportOutcome(path=path, status="unknown_layout", account_id=account_id,
                                 message="no parser profile matches; run `finctl ingest probe`")
        parser = detected

    result = parser.parse(pages, path)
    result.warnings.extend(apply_corrections(result, account_id))
    rec = reconcile(result)
    status = "imported" if rec.ok else "rejected"
    cur = conn.execute(
        """
        INSERT INTO statements
            (account_id, source_path, source_name, file_sha256, period_start, period_end,
             balance_start_cents, balance_end_cents, parser_profile, parser_version,
             status, reconcile_basis, reconcile_delta_cents, imported_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            account_id, str(path), path.name, sha,
            result.header.period_start, result.header.period_end,
            result.header.balance_start_cents, result.header.balance_end_cents,
            parser.profile_id, parser.version, status, result.reconcile_basis,
            rec.delta_cents, now_iso(),
        ),
    )
    statement_id = cur.lastrowid

    inserted = 0
    if rec.ok:
        for txn, digest in assign_occurrences(account_id, result.transactions):
            conn.execute(
                """
                INSERT OR IGNORE INTO transactions
                    (account_id, statement_id, booking_date, value_date, amount_cents,
                     currency, counterparty, counterparty_norm, counterparty_iban,
                     purpose, customer_ref, tx_type, raw_text, seq_in_statement,
                     occurrence_index, dedup_hash, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    account_id, statement_id, txn.booking_date, txn.value_date,
                    txn.amount_cents, "EUR", txn.counterparty,
                    normalize_counterparty(txn.counterparty), txn.counterparty_iban,
                    txn.purpose, txn.customer_ref, txn.tx_type, txn.raw_text,
                    txn.seq, 0, digest, now_iso(),
                ),
            )
            inserted += 1

    conn.commit()
    return ImportOutcome(
        path=path,
        status=status,
        account_id=account_id,
        profile=parser.profile_id,
        transactions=inserted,
        period=(result.header.period_start, result.header.period_end),
        reconciliation=rec,
        message=None if rec.ok else "reconciliation failed -- statement not imported",
    )


# ------------------------------------------------------------------ probe


def probe(path: Path, max_lines: int = 60) -> dict:
    """Dump an unknown layout so a new profile can be written by hand.

    This is the entire 'new bank' workflow, and it happens at development time.
    Nothing here calls a model; the output is for a human (or for me, in a
    Claude Code session) to read while writing a deterministic parser.
    """
    pages = extract_pages(path)
    detected = detect(pages, path)
    return {
        "file": str(path),
        "pages": len(pages),
        "detected_profile": detected.profile_id if detected else None,
        "first_page_lines": (pages[0].split("\n") if pages else [])[:max_lines],
    }
