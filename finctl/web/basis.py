"""Was alle Seiten des Dashboards brauchen.

Die Verbindung zur Datenbank, die Vorlagen, die Geldformatierung und die
SQL-Bausteine fuer Zeitraum und Basis. Das stand frueher im Kopf von
server.py, zusammen mit 57 Routen -- wer eine Seite suchte, scrollte an allem
vorbei.

Hier haengt nichts an FastAPI: dieses Modul kennt weder `app` noch einen
Router, damit die Richtung der Abhaengigkeiten eindeutig bleibt.
"""

from __future__ import annotations

import datetime as _dtm
import sqlite3
from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.datastructures import QueryParams
from starlette.responses import RedirectResponse

from finctl import module as _module
from finctl import pfade as _p
from finctl.ledger import db as ledger

# Weitergereicht, nicht selbst gebildet: `server.py` haelt die alten Namen
# erreichbar, damit die Aufteilung in Router keine Aufruferaenderung erzwingt.
CONFIG_DIR = _p.CONFIG_DIR
DB_PATH = _p.DB_PATH
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


#: Wie lang eine Notiz im Dashboard werden darf. Ein Textfeld ohne Grenze
#: fuellt sich, und die Prosa, die gerade aus den Vorlagen gefallen ist, waere
#: an ihrer naechsten Stelle.
NOTIZ_MAX = 200


def euro(cents: int | None) -> str:
    return ledger.format_eur(cents or 0)


def betrag(cents: int | None) -> str:
    """Ein Betrag fuer ein EINGABEFELD: deutsch, ohne Waehrungszeichen.

    Die Felder trugen bisher, was Python ausspuckt -- "72.0", "4671.00",
    "831000.0". Gelesen wurden sie von einem Parser, der den Punkt als
    Tausendertrennung wegwirft, denn genau das ist er auf deutschen
    Eingaben. Aus 4671.00 wurden so 467.100,00: Faktor hundert, ausgeloest
    davon, dass jemand auf Speichern drueckt, ohne etwas geaendert zu haben.

    Ausgabe und Eingabe muessen dieselbe Sprache sprechen. Der Parser in
    base.html liest inzwischen beide Schreibweisen, aber darauf soll sich hier
    nichts verlassen muessen.
    """
    if cents is None:
        return ""
    return f"{cents / 100:,.2f}".replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


def _bindung_text() -> str:
    from finctl.web import auth as _auth

    host, _ = _auth.bindung()
    if host in ("127.0.0.1", "localhost", "::1"):
        return "localhost only"
    return f"im Netz ({host})" + ("" if _auth.passwort_gesetzt() else " · OHNE PASSWORT")


TEMPLATES.env.globals["bindung_text"] = _bindung_text
# Die Navigation zeigt nur Seiten eingeschalteter Module (finctl/module.py).
# `aktive_module` einmal je Seite, `seite_an` je Link mit dieser Menge.
TEMPLATES.env.globals["aktive_module"] = _module.aktive
TEMPLATES.env.globals["seite_an"] = _module.seite_an
TEMPLATES.env.filters["euro"] = euro
TEMPLATES.env.filters["betrag"] = betrag


def vorzeichen(cents) -> str:
    """Die Farbklasse eines Betrags mit Richtung: rot raus, gruen rein, null ohne.

    Eine Regel fuer die ganze App (docs/design_conventions.yaml, vorzeichenfarbe):
    Rueckblick faerbte, Vertraege und Planung zeigten dieselben Betraege in
    Textfarbe.
    """
    if cents is None or cents == 0:
        return ""
    return "neg" if cents < 0 else "pos"


TEMPLATES.env.filters["vz"] = vorzeichen


def adresse(request, pfad: str | None = None, **setzen) -> str:
    """Die Adresse der Seite mit ausgetauschten Parametern -- fuer Reiter.

    Was sonst gefiltert ist, bleibt: ein Reiter wechselt die Ansicht, nicht
    das Konto oder den Zeitraum. Ein leerer Wert nimmt den Parameter heraus.
    """
    params = [(k, v) for k, v in request.query_params.multi_items() if k not in setzen]
    params += [(k, str(v)) for k, v in setzen.items() if v not in (None, "")]
    pfad = request.url.path if pfad is None else pfad
    return pfad + (f"?{QueryParams(params)}" if params else "")


def umleiten(request, pfad: str, anker: str = "", **setzen) -> RedirectResponse:
    """Eine zusammengelegte Seite auf ihren neuen Ort, samt ihrer Parameter.

    307 statt 301: einen dauerhaften Umzug merkt sich der Browser fuer immer,
    und eine Umleitung, die sich spaeter noch einmal aendert, fuehrte dann
    weiter zum alten Ziel.
    """
    ziel = adresse(request, pfad, **setzen) + (f"#{anker}" if anker else "")
    return RedirectResponse(ziel, status_code=307)


TEMPLATES.env.globals["adresse"] = adresse


def conn() -> sqlite3.Connection:
    return ledger.connect(DB_PATH)


def _summe(rows_or_conn, sql: str = "", args=()) -> dict:
    """Anzahl und Summe einer gefilterten Menge, getrennt nach Richtung.

    Eine Nettosumme aus gemischten Vorzeichen luegt: 0,00 kann heissen
    "nichts passiert" oder "12.000 raus und 12.000 rein". Beide Richtungen
    kommen deshalb mit zurueck und werden angezeigt, sobald es beide gibt.

    Nimmt entweder eine fertige Zeilenliste (dann wird in Python gezaehlt)
    oder eine Verbindung samt Abfrage, die vier Werte liefert.
    """
    if not sql:
        betraege = [int(r["amount_cents"] or 0) for r in rows_or_conn]
        return {"n": len(betraege), "netto": sum(betraege),
                "raus": sum(x for x in betraege if x < 0),
                "rein": sum(x for x in betraege if x > 0)}
    n, netto, raus, rein = rows_or_conn.execute(sql, args).fetchone()
    return {"n": n or 0, "netto": netto or 0, "raus": raus or 0, "rein": rein or 0}


def categories(c: sqlite3.Connection) -> list[dict]:
    """Leaf categories only -- parents are headings, not destinations."""
    return [
        {"id": r["id"], "label": f"{r['parent']} / {r['name']}",
         "tax": r["default_tax_id"]}
        for r in c.execute(
            """
            SELECT child.id, child.name, child.default_tax_id, parent.name AS parent
            FROM   mgmt_categories child
            JOIN   mgmt_categories parent ON parent.id = child.parent_id
            WHERE  child.active = 1
            ORDER  BY parent.sort_order, child.sort_order
            """
        )
    ]


def tax_categories(c: sqlite3.Connection) -> list[dict]:
    return [
        {"id": r["id"], "label": f"{r['parent']} / {r['name']}",
         "needs_property": bool(r["requires_property"])}
        for r in c.execute(
            """
            SELECT child.id, child.name, parent.name AS parent, child.requires_property
            FROM   tax_categories child
            JOIN   tax_categories parent ON parent.id = child.parent_id
            WHERE  child.active = 1
            ORDER  BY parent.sort_order, child.sort_order
            """
        )
    ]


def setting(c: sqlite3.Connection, key: str, fallback):
    """An edited target, or the documented default from config."""
    row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return fallback, "config"
    try:
        return type(fallback)(row["value"]), "edited"
    except (TypeError, ValueError):
        return row["value"], "edited"


# ------------------------------------------------------------------ views


def period_clause(year: str) -> tuple[str, list]:
    """Date filter for a dashboard period.

    'all' means lifetime; anything else is a single year. Defaulting to the
    current year matters once more than one year is loaded: a table headed
    2026 that silently also contains 2027 is worse than no table.
    """
    if year == "all":
        return "1=1", []
    return "substr(t.booking_date,1,4) = ?", [year]


# Categories that move capital rather than spend it. Purchase instalments buy
# an asset; an ETF sale is wealth changing form. Both are real money and
# neither is consumption, and mixing them into a "where does it go" share
# makes every running cost look negligible beside them -- 78.000 of purchase
# instalments put the 2026 total at 174% of income and pushed the biggest
# everyday category to 1,3%.
#
# Excluded from the default view, never hidden: `?basis=alles` shows everything.
CAPITAL_PREFIXES = ("investment/", "immobilie/kaufnebenkosten")


def basis_clause(basis: str) -> tuple[str, list]:
    if basis == "alles":
        return "1=1", []
    return (("COALESCE(s.mgmt_category_id,'') NOT LIKE ? "
             "AND COALESCE(s.mgmt_category_id,'') NOT LIKE ?"),
            [p + "%" for p in CAPITAL_PREFIXES])


def _jsonfaehig(wert):
    """Datumswerte fuer tojson: Jinja kann `date` nicht serialisieren."""
    if isinstance(wert, (_dtm.date, _dtm.datetime)):
        return wert.isoformat()
    if isinstance(wert, dict):
        return {k: _jsonfaehig(v) for k, v in wert.items()}
    if isinstance(wert, (list, tuple)):
        return [_jsonfaehig(v) for v in wert]
    return wert


def _slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "szenario"
