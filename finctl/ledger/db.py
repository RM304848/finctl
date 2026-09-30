"""Database access and money handling.

Money is integer cents everywhere. Parsing German-formatted amounts is done
here rather than in each parser profile, because getting `1.234,56` wrong is
silent and expensive.
"""

from __future__ import annotations

import itertools
import re
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from finctl import pfade as _p

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB = _p.DB_PATH


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL keeps the dashboard readable while an import is writing.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Columns added after a ledger already existed. CREATE TABLE IF NOT EXISTS
# cannot introduce one, and rebuilding from every statement to gain a single
# column is a large hammer for a small change.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("mgmt_categories", "fixkosten", "INTEGER NOT NULL DEFAULT 0"),
    ("properties", "afa_base_cents", "INTEGER"),
    ("properties", "afa_extra_annual_cents", "INTEGER"),
    # EINE Gruppe je Transaktion: eine Gruppe sagt "ist Teil von".
    #
    # Eine Gruppe -- ein Vorgang -- spannt ueber Kategorien und mischt
    # Einmaliges mit Laufendem: Kaufpreis, Nebenkosten, Raten, Versicherung,
    # Wartung, Verkauf. Sie sitzt deshalb ueber der Kategorie und nicht neben
    # ihr.
    #
    # Auf der Transaktion und nicht auf dem Split: ein Vorgang ist etwas, das
    # PASSIERT ist, und eine Zerlegung in Posten aendert nicht, wozu es
    # gehoerte. Eine Amazon-Bestellung in drei Positionen bleibt eine
    # Bestellung.
    ("transactions", "group_id", "TEXT"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _ADDED_COLUMNS:
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if present and column not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(db_path: Path | str = DEFAULT_DB) -> Path:
    """Create the schema. Idempotent -- every statement is IF NOT EXISTS."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _add_missing_columns(conn)
        conn.commit()
    finally:
        conn.close()
    return Path(db_path)


# --------------------------------------------------------------------- money

_AMOUNT_RE = re.compile(r"^\s*([+-]?)\s*([\d.,\s]+?)\s*(?:EUR|€)?\s*([+-]|[HS])?\s*$", re.I)


def parse_de_amount(raw: str) -> int:
    """Parse a German-formatted amount into integer cents.

    Handles `1.234,56`, `-1.234,56`, `1234,56 EUR`, `1.234,56-` (trailing sign,
    used by several German banks) and the `H`/`S` (Haben/Soll) suffix that some
    statements print instead of a sign.

    Raises ValueError rather than guessing: a misparsed amount would pass the
    reconciliation gate only by coincidence, and silently corrupt a forecast.
    """
    if raw is None:
        raise ValueError("amount is None")
    m = _AMOUNT_RE.match(str(raw))
    if not m:
        raise ValueError(f"unparseable amount: {raw!r}")

    lead_sign, body, trail = m.group(1), m.group(2), m.group(3)
    body = body.replace(" ", "").replace(" ", "")

    # German convention: '.' groups thousands, ',' is the decimal separator.
    # Without a comma a single '.' is ambiguous: before exactly three digits
    # it groups thousands (DKB writes whole euros as `5.000`), otherwise it is
    # a decimal point, since no amount carries three decimal places.
    if "," in body:
        body = body.replace(".", "").replace(",", ".")
    elif body.count(".") > 1 or re.fullmatch(r"\d+\.\d{3}", body.lstrip("+-")):
        body = body.replace(".", "")

    try:
        value = Decimal(body)
    except InvalidOperation as exc:
        raise ValueError(f"unparseable amount: {raw!r}") from exc

    negative = lead_sign == "-" or (trail or "").upper() in {"-", "S"}
    cents = int((value * 100).to_integral_value())
    return -cents if negative else cents


def format_eur(cents: int) -> str:
    """Render cents as a German-formatted euro string."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole:,}".replace(",", ".") + f",{frac:02d} €"


# ---------------------------------------------------------------- invariants


def split_imbalances(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Transactions whose splits do not sum to the transaction amount.

    Always a bug when non-empty -- never a warning to be tolerated.
    """
    return conn.execute("SELECT * FROM v_split_imbalance ORDER BY booking_date").fetchall()


def orphaned_splits(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Splits pointing at a category that no longer exists or was retired.

    They keep their amount in the ledger but disappear from every report that
    joins on the category, so totals stop adding up with nothing visibly
    wrong. This happens whenever a category is retired without its splits
    being moved somewhere that is actually declared -- which is not
    hypothetical: a merge into an undeclared target produced exactly this.
    """
    return conn.execute(
        """
        SELECT      s.id, s.mgmt_category_id, s.amount_cents,
                    t.booking_date, t.account_id
        FROM        splits s
        JOIN        transactions t ON t.id = s.transaction_id
        LEFT JOIN   mgmt_categories m ON m.id = s.mgmt_category_id
        WHERE       s.mgmt_category_id IS NOT NULL
          AND       (m.id IS NULL OR m.active = 0)
        ORDER BY    t.booking_date
        """
    ).fetchall()


def statement_chain_gaps(conn: sqlite3.Connection) -> list[dict]:
    """Consecutive statements whose balances do not chain.

    Statement N's closing balance must equal N+1's opening balance. A break
    means a statement period is missing, which is the failure mode that would
    otherwise silently corrupt every downstream total.
    """
    rows = conn.execute(
        """
        SELECT id, account_id, period_start, period_end, reconcile_basis,
               balance_start_cents, balance_end_cents, source_name
        FROM   statements
        WHERE  status = 'imported'
        ORDER  BY account_id, period_start
        """
    ).fetchall()

    # Some issuers (Scalable) print value-dated balances, so interest and tax
    # value-dated to the previous month's last day but booked on the 1st sit
    # inside the NEXT statement while already being counted in its opening
    # balance. Those entries explain an apparent break and are not one.
    def carried_in(statement_id: int, period_start: str) -> int:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0)
            FROM   transactions
            WHERE  statement_id = ? AND value_date IS NOT NULL
                   AND value_date < ?
            """,
            (statement_id, period_start),
        ).fetchone()
        return row[0] or 0

    gaps: list[dict] = []
    by_account: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_account.setdefault(row["account_id"], []).append(row)

    for account_id, statements in by_account.items():
        for prev, nxt in itertools.pairwise(statements):
            # Only a value-dated opening balance already contains entries
            # value-dated before the period. A booking-basis statement -- or
            # one whose opening was corrected to the previous close -- does not.
            explained = (
                carried_in(nxt["id"], nxt["period_start"])
                if nxt["reconcile_basis"] == "value" else 0
            )
            expected_open = prev["balance_end_cents"] + explained
            if expected_open != nxt["balance_start_cents"]:
                gaps.append(
                    {
                        "account_id": account_id,
                        "after": prev["period_end"],
                        "before": nxt["period_start"],
                        "closing_cents": prev["balance_end_cents"],
                        "opening_cents": nxt["balance_start_cents"],
                        "explained_by_value_dating_cents": explained,
                        "delta_cents": nxt["balance_start_cents"] - expected_open,
                    }
                )
    return gaps
