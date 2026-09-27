"""Startseite, Handbuch und der Lebenszeichen-Endpunkt."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from finctl.ledger import db as ledger
from finctl.web.basis import (
    TEMPLATES,
    basis_clause,
    period_clause,
)

router = APIRouter()


@router.get("/")
def startseite():
    """Die Overview ist am 17.09.2026 entfallen; ihre Kacheln stehen im
    Monatsabschluss. Kategorien je Jahr zeigen Report und Alle Kategorien.

    `/` bleibt als Weiterleitung: der Login und alte Lesezeichen landen hier.
    """
    return RedirectResponse("/monatsabschluss", status_code=307)


def _kacheln(c: sqlite3.Connection, jahr: str) -> dict:
    """Die Kacheln, die frueher auf der Overview standen.

    Einnahmen, Ausgaben und Saldo des Jahres ohne Kapitalbewegungen, die
    Arbeit, die in Review und Flagged wartet, der Stand jedes Kontos aus
    seinem neuesten Auszug -- und ob die Ledger-Invarianten halten.
    """
    period_sql, period_args = period_clause(jahr)
    basis_sql, basis_args = basis_clause("laufend")
    where = f"({period_sql}) AND ({basis_sql})"
    args = period_args + basis_args
    # NETTO je Kategorie, das Vorzeichen entscheidet die Seite -- wie auf der
    # Overview: der Mietanteil eines Mitbewohners mindert Wohnen, statt als
    # Einnahme zu zaehlen.
    netto = [int(r["cents"] or 0) for r in c.execute(f"""
        SELECT SUM(s.amount_cents) AS cents
        FROM        splits s
        JOIN        transactions t ON t.id = s.transaction_id
        LEFT JOIN   mgmt_categories cat ON cat.id = s.mgmt_category_id
        LEFT JOIN   mgmt_categories p ON p.id = cat.parent_id
        WHERE       COALESCE(cat.kind,'') <> 'transfer' AND {where}
        GROUP BY    COALESCE(p.id, cat.id, '__none__')""", args)]
    einnahmen = sum(v for v in netto if v > 0)
    ausgaben = sum(v for v in netto if v < 0)
    gehalt = c.execute(f"""
        SELECT COALESCE(SUM(s.amount_cents), 0)
        FROM   splits s JOIN transactions t ON t.id = s.transaction_id
        WHERE  s.mgmt_category_id LIKE 'einkommen/gehalt%' AND {where}""",
        args).fetchone()[0]
    konten = [dict(r) for r in c.execute("""
        SELECT a.id,
               (SELECT balance_end_cents FROM statements s
                WHERE s.account_id = a.id AND s.status='imported'
                ORDER BY period_end DESC LIMIT 1) AS balance_cents,
               (SELECT period_end FROM statements s
                WHERE s.account_id = a.id AND s.status='imported'
                ORDER BY period_end DESC LIMIT 1) AS as_of
        FROM   accounts a WHERE a.active = 1 AND a.ingest_mode = 'parsed'
        ORDER  BY a.id""")]
    return {
        "jahr": jahr, "einnahmen": einnahmen, "ausgaben": ausgaben,
        "gehalt": gehalt,
        "review": c.execute("SELECT COUNT(*) FROM v_review_queue").fetchone()[0],
        "flagged": c.execute(
            "SELECT COUNT(*) FROM splits WHERE source='default' "
            "AND rule_id IS NOT NULL AND mgmt_category_id IS NULL").fetchone()[0],
        "konten": konten,
        "ungleich": len(ledger.split_imbalances(c)),
        "luecken": ledger.statement_chain_gaps(c),
    }


@router.get("/handbuch", response_class=HTMLResponse)
def handbuch(request: Request):
    """HANDBUCH.md im Dashboard, mit einem Anker je Seite.

    Nicht in der Navigation: das Handbuch ist keine Ansicht auf die Daten,
    sondern das Ziel der Links aus dem Konfigurationsfuss.
    """
    from finctl.web import handbuch as _hb

    return TEMPLATES.TemplateResponse(request, "handbuch.html",
                                      {"inhalt": _hb.als_html()})


@router.get("/healthz")
def healthz():
    return {"ok": True}
