"""Ab wann die Prognose rechnet, und mit welchem Saldo je Konto.

Aus `ops.py` hierher, weil `bestaende.py` beides braucht und die Prognose
nicht von der App abhaengen darf (`finctl/module.py`). `ops._stichtag` und
`ops._saldo_zum` bleiben als Namen dort gueltig.
"""

from __future__ import annotations

from datetime import date

from finctl.forecast import abgleich


def stichtag(conn):
    """Der letzte Tag, bis zu dem ALLE Konten vollstaendig belegt sind.

    /konten rechnet ab hier. Was danach gebucht oder per Snapshot erfasst
    ist, zaehlt nicht: ein angebrochener Monat -- DKB bis 04.09., ein
    Snapshot vom 11.09. -- liess die Prognose erst im Oktober beginnen und
    den September weg, obwohl dessen Gehalt und Kosten noch ausstanden. Ein
    Monat zaehlt ganz oder gar nicht, und fuer alle Konten derselbe.
    """
    ende = abgleich.letzter_vollstaendiger_monat(conn)
    if ende is None:
        return date.fromordinal(date.today().replace(day=1).toordinal() - 1)
    return ende


def saldo_zum(conn, account: str, stichtag: date) -> int | None:
    """Der Saldo eines Kontos am Stichtag, aus dem Auszug zurueckgerechnet.

    Reicht der letzte Auszug ueber den Stichtag hinaus, werden die Buchungen
    danach vom Endsaldo abgezogen. None, wenn es keinen Auszug gibt.
    """
    stmt = conn.execute(
        "SELECT period_end, balance_end_cents FROM statements "
        "WHERE account_id=? AND status='imported' AND period_end >= ? "
        "ORDER BY period_end ASC LIMIT 1", (account, stichtag.isoformat())).fetchone()
    if stmt is None:
        stmt = conn.execute(
            "SELECT period_end, balance_end_cents FROM statements "
            "WHERE account_id=? AND status='imported' "
            "ORDER BY period_end DESC LIMIT 1", (account,)).fetchone()
        return None if stmt is None else int(stmt["balance_end_cents"])
    danach = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM transactions "
        "WHERE account_id = ? AND booking_date > ? AND booking_date <= ?",
        (account, stichtag.isoformat(), stmt["period_end"])).fetchone()[0]
    return int(stmt["balance_end_cents"]) - int(danach or 0)
