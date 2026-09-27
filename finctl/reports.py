"""Cross-account reporting.

Transfer verification is the important piece here. DKB prints a counterparty
IBAN on 1 transaction in 336, so IBAN matching alone cannot prove that money
leaving one account arrived in another. Pairing can: an outflow of X on one
account should have an inflow of X on another within a few days.

That is the same idea as the statement reconciliation gate -- prove it with
arithmetic rather than trust a string match -- applied across accounts instead
of within one statement.
"""

from __future__ import annotations

import sqlite3
from datetime import date


def _iso_within(a: str, b: str, days: int) -> bool:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= days


def transfer_pairs(conn: sqlite3.Connection, *, window_days: int = 5) -> dict:
    """Match transfer legs across parsed accounts.

    An unmatched leg is not necessarily an error: money moving to Sparda,
    Scalable or the joint account leaves the parsed set entirely, and there is
    no second leg to find. Those are reported separately from legs that should
    have paired and did not.
    """
    parsed = {
        row["id"]
        for row in conn.execute(
            "SELECT id FROM accounts WHERE ingest_mode = 'parsed' AND active = 1"
        )
    }

    legs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id AS split_id, t.id AS tx_id, t.account_id, t.booking_date,
                   s.amount_cents, s.transfer_account_id, t.raw_text
            FROM   splits s
            JOIN   transactions t ON t.id = s.transaction_id
            WHERE  s.mgmt_category_id LIKE 'transfer/%'
            ORDER  BY t.booking_date, s.id
            """
        )
    ]

    outflows = [leg for leg in legs if leg["amount_cents"] < 0]
    inflows = [leg for leg in legs if leg["amount_cents"] > 0]

    matched: list[dict] = []
    used: set[int] = set()

    for out in outflows:
        for inn in inflows:
            if inn["split_id"] in used:
                continue
            if inn["account_id"] == out["account_id"]:
                continue
            if inn["amount_cents"] != -out["amount_cents"]:
                continue
            if not _iso_within(inn["booking_date"], out["booking_date"], window_days):
                continue
            used.add(inn["split_id"])
            matched.append({
                "amount_cents": -out["amount_cents"],
                "from": out["account_id"], "to": inn["account_id"],
                "sent": out["booking_date"], "received": inn["booking_date"],
            })
            break

    matched_out = {m["sent"] + m["from"] + str(m["amount_cents"]) for m in matched}
    unmatched_out = [
        leg for leg in outflows
        if leg["booking_date"] + leg["account_id"] + str(-leg["amount_cents"])
        not in matched_out
    ]
    unmatched_in = [leg for leg in inflows if leg["split_id"] not in used]

    def leaves_parsed_set(leg: dict) -> bool:
        target = leg["transfer_account_id"]
        return target is not None and target not in parsed

    return {
        "window_days": window_days,
        "matched_pairs": matched,
        "matched_count": len(matched),
        "matched_cents": sum(m["amount_cents"] for m in matched),
        # Expected: the other side is outside the three parsed accounts.
        "leaves_parsed_set": [
            {k: leg[k] for k in ("account_id", "booking_date", "amount_cents",
                                 "transfer_account_id")}
            for leg in unmatched_out + unmatched_in if leaves_parsed_set(leg)
        ],
        # Unexpected: looks internal but no counterpart was found.
        "unpaired": [
            {"account": leg["account_id"], "date": leg["booking_date"],
             "amount_cents": leg["amount_cents"], "text": leg["raw_text"][:70]}
            for leg in unmatched_out + unmatched_in if not leaves_parsed_set(leg)
        ],
        "net_cents": sum(leg["amount_cents"] for leg in legs),
    }


def spend_by_category(conn: sqlite3.Connection, year: str = "2026") -> dict:
    """Spending by management category, with transfers excluded.

    Transfers are movement, not spend: including them would make the totals
    meaningless, since a euro moved three times would count three times.
    """
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT COALESCE(parent.name, cat.name) AS kategorie,
                   cat.name                        AS subkategorie,
                   COUNT(*)                        AS n,
                   SUM(s.amount_cents)             AS cents
            FROM        splits s
            JOIN        transactions t   ON t.id = s.transaction_id
            LEFT JOIN   mgmt_categories cat    ON cat.id = s.mgmt_category_id
            LEFT JOIN   mgmt_categories parent ON parent.id = cat.parent_id
            WHERE       t.booking_date LIKE ? || '%'
              AND       (cat.kind IS NULL OR cat.kind <> 'transfer')
            GROUP BY    s.mgmt_category_id
            ORDER BY    SUM(s.amount_cents)
            """,
            (year,),
        )
    ]
    return {"year": year, "rows": rows}
