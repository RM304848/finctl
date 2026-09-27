"""Ein geplanter Immobilienverkauf, wie ihn vier Seiten brauchen.

/konten (die Rate faellt weg), /immobilie (der Termin steht auf der Seite),
/kredite (der Kredit endet vorzeitig) und /planung (die Zeile wird gerechnet)
fragen dasselbe. Als Kopie in vier Modulen waere die fuenfte Antwort die, die
irgendwann abweicht.
"""

from __future__ import annotations

import sqlite3

from finctl.pfade import CONFIG_DIR


def _verkaeufe_aus_plan(c: sqlite3.Connection) -> dict:
    """Verkaeufe aus eingeschalteten Planklammern, je Objekt."""
    import datetime as _dt

    from finctl.forecast import jahre as _jm

    return {v.property_id: v for v in _jm.verkaeufe(c, _dt.date.today().year)
            if v.klammer}


def _verkauf_kontext(c: sqlite3.Connection, prop: dict) -> dict:
    """Was ein geplanter Verkauf tatsaechlich einbringt -- und ab wann steuerfrei.

    Drei Zahlen, die niemand im Kopf haben soll: die Restschuld zum Termin aus
    dem Tilgungsplan, der Nettozufluss daraus, und das Ende der Frist nach
    §23 EStG. Die Frist stand frueher als eigenes Feld in properties.yaml und
    wurde damit gepflegt wie eine Tatsache, obwohl sie eine Rechnung ist:
    Anschaffung plus zehn Jahre, gerechnet vom NOTARVERTRAG. Ein Feld, das man
    falsch eintragen kann, wo eine Formel genuegt.
    """
    import datetime as _dt

    import yaml

    from finctl.forecast import jahre as _jm

    out: dict = {"termin": prop.get("planned_sale_on"),
                 "preis_cents": prop.get("sale_price_cents"),
                 "restschuld_cents": None, "restschuld_stand": None,
                 "netto_cents": None,
                 "frist_ende": None, "steuerfrei": None,
                 "vorschlag_cents": None}

    gekauft = prop.get("acquired_on")
    if gekauft:
        g = _dt.date.fromisoformat(str(gekauft))
        frist = g.replace(year=g.year + 10)
        out["frist_ende"] = frist.isoformat()
        if prop.get("planned_sale_on"):
            out["steuerfrei"] = _dt.date.fromisoformat(
                str(prop["planned_sale_on"])) > frist

    kauf = prop.get("purchase_price_cents") or 0
    eigen = prop.get("equity_cents") or 0
    if kauf or eigen:
        out["vorschlag_cents"] = kauf + eigen

    if prop.get("planned_sale_on"):
        ab = _dt.date.fromisoformat(str(prop["planned_sale_on"]))
        ab = _dt.date(ab.year, ab.month, 1)
        path = CONFIG_DIR / "loans.yaml"
        spec = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) \
            if path.exists() else {}
        loan = next((x for x in spec.get("loans") or []
                     if x.get("property_id") == prop["id"]), None)
        if loan:
            try:
                betrag, stand = _jm.restschuld(loan, ab)
                out["restschuld_cents"] = betrag
                # Nur nennen, wenn der Plan NICHT bis zum Termin reicht --
                # sonst ist der Stichtag der Termin und die Angabe Rauschen.
                if (stand.year, stand.month) != (ab.year, ab.month):
                    out["restschuld_stand"] = stand.isoformat()
            except Exception:
                pass
        if out["preis_cents"]:
            out["netto_cents"] = out["preis_cents"] - (out["restschuld_cents"] or 0)
    return out
