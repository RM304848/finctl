"""Geteilt: Projekte, Salden und die schlanke Buchungstabelle zum Zuordnen.

Rechnung und Pruefungen stehen in `finctl/geteilt.py`; hier wird gelesen,
geprueft und geschrieben. Das Ledger wird nur gelesen -- ein Projekt aendert
keine Buchung.
"""

from __future__ import annotations

import datetime as _dtm
import sqlite3

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl.web.basis import TEMPLATES, categories, conn, zeitraeume

router = APIRouter()

_TEILE_SQL = """
    SELECT t.dedup_hash, s.seq,
           (SELECT COUNT(*) FROM splits x WHERE x.transaction_id = t.id) AS teile,
           t.booking_date, t.account_id, t.raw_text, s.mgmt_category_id,
           s.amount_cents
    FROM   splits s JOIN transactions t ON t.id = s.transaction_id
"""


def _fehler(exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


def _alle_teile(c: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in c.execute(_TEILE_SQL + " ORDER BY t.booking_date")]


def _kategorienamen(c: sqlite3.Connection) -> dict[str, str]:
    return {k["id"]: k["label"] for k in categories(c)}


def _uebersicht(daten, je_projekt, salden) -> list[dict]:
    """Je Projekt die Zeile der Tabelle auf /projekte."""
    out = []
    for p in daten.projekte.values():
        posten = je_projekt.get(p.id, [])
        eigene = [s for s in salden if s.projekt == p.id]
        out.append({
            "projekt": p, "posten": posten,
            "kosten": -sum(x.cents for x in posten),
            "mein_anteil": sum(x.anteile.get("ich", 0) for x in posten),
            "offen": sum(s.offen_cents for s in eigene),
            "von": min((x.datum for x in posten), default=None),
            "bis": max((x.datum for x in posten), default=None),
            "salden": eigene,
        })
    return sorted(out, key=lambda r: r["bis"] or "9999", reverse=True)


def _gruppiert(posten, schluessel) -> list[tuple[str, int]]:
    summe: dict[str, int] = {}
    for x in posten:
        k = schluessel(x)
        summe[k] = summe.get(k, 0) - x.cents
    return sorted(summe.items(), key=lambda kv: -kv[1])


@router.get("/projekte", response_class=HTMLResponse)
def projekte(request: Request, offen: str = ""):
    from finctl import geteilt as _g

    daten = _g.laden(_g.PFAD)
    c = conn()
    try:
        je_projekt = _g.posten(daten, _alle_teile(c))
        namen = _kategorienamen(c)
    finally:
        c.close()
    zeilen = _uebersicht(daten, je_projekt, _g.salden(daten, je_projekt))
    for z in zeilen:
        z["nach_kategorie"] = _gruppiert(
            z["posten"], lambda x: namen.get(x.kategorie, x.kategorie or "ohne Kategorie"))
        z["nach_monat"] = sorted(_gruppiert(z["posten"], lambda x: x.datum[:7]))
    return TEMPLATES.TemplateResponse(request, "projekte.html", {
        "zeilen": zeilen, "personen": daten.personen, "offen": offen,
        "kategorienamen": namen})


@router.get("/salden", response_class=HTMLResponse)
def salden(request: Request, alle: int = 0):
    from finctl import geteilt as _g

    daten = _g.laden(_g.PFAD)
    c = conn()
    try:
        je_projekt = _g.posten(daten, _alle_teile(c))
    finally:
        c.close()
    je_person: dict[str, list] = {}
    for s in _g.salden(daten, je_projekt):
        if alle or s.offen_cents:
            je_person.setdefault(s.person, []).append(s)
    zeilen = sorted(
        ({"person": p, "name": daten.personen.get(p, p), "salden": liste,
          "offen": sum(s.offen_cents for s in liste)} for p, liste in je_person.items()),
        key=lambda z: -z["offen"])
    return TEMPLATES.TemplateResponse(request, "salden.html", {
        "zeilen": zeilen, "projekte": daten.projekte, "alle": alle,
        "offen_gesamt": sum(z["offen"] for z in zeilen)})


def _im_zeitraum(rows: list[dict], von: str, bis: str) -> list[dict]:
    """Die Zeilen von `von` bis `bis` einschliesslich; leer heisst offen."""
    return [r for r in rows
            if (not von or r["booking_date"][:10] >= von)
            and (not bis or r["booking_date"][:10] <= bis)]


@router.get("/geteilt/buchungen", response_class=HTMLResponse)
def geteilt_buchungen(request: Request, von: str = "", bis: str = "", konto: str = "",
                      kategorie: list[str] = Query(default=[]), q: str = "",
                      projekt: str = "", ohne: int = 0, alle_richtungen: int = 0,
                      limit: int = 300):
    """Nur zum Zuordnen: Kategorie ist Anzeige, das Projekt das einzige Feld."""
    from finctl import geteilt as _g

    heute = _dtm.date.today()
    von = von or (heute - _dtm.timedelta(days=90)).isoformat()
    daten = _g.laden(_g.PFAD)
    # Ohne Zeitraum geholt: jeder Zeitraum daneben zaehlt mit den uebrigen
    # Filtern, auch denen, die erst hier in Python greifen (Projekt, ohne).
    where, args = ["1=1"], []
    for bedingung, wert in (("t.account_id = ?", konto),
                            ("t.raw_text LIKE ?", f"%{q}%" if q else "")):
        if wert:
            where.append(bedingung)
            args.append(wert)
    if not alle_richtungen:
        where.append("s.amount_cents < 0")
    gewaehlt = [k for k in kategorie if k]
    if gewaehlt:
        where.append("(" + " OR ".join(
            "s.mgmt_category_id = ? OR s.mgmt_category_id LIKE ?" for _ in gewaehlt) + ")")
        for k in gewaehlt:
            args += [k, k + "/%"]
    c = conn()
    try:
        rows = [dict(r) for r in c.execute(
            _TEILE_SQL + " WHERE " + " AND ".join(where)
            + " ORDER BY t.booking_date DESC, t.id, s.seq", args)]
        namen = _kategorienamen(c)
        konten = [r["id"] for r in c.execute(
            "SELECT id FROM accounts WHERE ingest_mode = 'parsed' ORDER BY id")]
        erster = c.execute("SELECT MIN(booking_date) FROM transactions").fetchone()[0] or ""
    finally:
        c.close()
    for r in rows:
        zu = daten.buchungen.get((r["dedup_hash"], r["seq"]))
        r["projekt"] = zu.projekt if zu else ""
        r["teilung"] = zu.teilung if zu else None
    if ohne:
        rows = [r for r in rows if not r["projekt"]]
    if projekt:
        rows = [r for r in rows if r["projekt"] == projekt]
    zeit_optionen = [({"von": a, "bis": b}, text, len(_im_zeitraum(rows, a, b)))
                     for text, a, b in zeitraeume(heute, erster)]
    rows = _im_zeitraum(rows, von, bis)
    gesamt = len(rows)
    return TEMPLATES.TemplateResponse(request, "geteilt_buchungen.html", {
        "rows": rows[:limit], "gesamt": gesamt, "projekte": daten.projekte,
        "personen": daten.personen, "kategorienamen": namen, "konten": konten,
        "zeit_optionen": zeit_optionen,
        "f": {"von": von, "bis": bis, "konto": konto, "kategorie": gewaehlt, "q": q,
              "projekt": projekt, "ohne": ohne, "alle_richtungen": alle_richtungen}})


# ---------------------------------------------------------------- Schreiben

def _aendern(body: dict, wie):
    """Laden, aendern, pruefen, schreiben -- oder 400 mit deutscher Meldung."""
    from finctl import geteilt as _g

    daten = _g.laden(_g.PFAD)
    try:
        ergebnis = wie(daten, body)
    except (ValueError, TypeError, KeyError) as exc:
        return _fehler(exc)
    if not body.get("vorschau"):
        _g.schreiben(daten, _g.PFAD)
    return {"ok": True, **(ergebnis or {})}


@router.post("/api/geteilt/person")
async def api_person(request: Request):
    from finctl import geteilt as _g

    return _aendern(await request.json(), lambda d, b: {
        "id": _g.person_setzen(d, b.get("id") or None, str(b.get("name") or ""))})


@router.post("/api/geteilt/projekt")
async def api_projekt(request: Request):
    from finctl import geteilt as _g

    return _aendern(await request.json(), lambda d, b: {
        "id": _g.projekt_setzen(d, b.get("id") or None, str(b.get("name") or ""),
                                [str(p) for p in b.get("personen") or []])})


@router.post("/api/geteilt/projekt/entfernen")
async def api_projekt_entfernen(request: Request):
    def entfernen(d, b):
        pid = str(b.get("id") or "")
        if pid not in d.projekte:
            raise ValueError("Unbekannter Budgettopf.")
        del d.projekte[pid]
        for k in [k for k, z in d.buchungen.items() if z.projekt == pid]:
            del d.buchungen[k]
        return {}
    return _aendern(await request.json(), entfernen)


@router.post("/api/geteilt/zuordnen")
async def api_zuordnen(request: Request):
    from finctl import geteilt as _g

    return _aendern(await request.json(), lambda d, b: {
        "n": _g.zuordnen(d, [(str(h), int(t)) for h, t in b.get("teile") or []],
                         b.get("projekt") or None)})


def _cents_des_teils(buchung: str, teil: int) -> int:
    c = conn()
    try:
        row = c.execute(
            "SELECT s.amount_cents FROM splits s JOIN transactions t "
            "ON t.id = s.transaction_id WHERE t.dedup_hash = ? AND s.seq = ?",
            (buchung, teil)).fetchone()
    finally:
        c.close()
    if row is None:
        raise ValueError("Diese Buchung gibt es im Ledger nicht.")
    return -row["amount_cents"]


@router.post("/api/geteilt/teilung")
async def api_teilung(request: Request):
    """Eine eigene Teilung setzen. Mit `vorschau` nur rechnen."""
    from finctl import geteilt as _g

    def setzen(d, b):
        schluessel = (str(b["buchung"]), int(b.get("teil") or 0))
        cents = _cents_des_teils(*schluessel)
        return {"anteile": _g.teilung_setzen(d, schluessel, b.get("teilung") or None, cents)}
    return _aendern(await request.json(), setzen)


@router.post("/api/geteilt/ausgeglichen")
async def api_ausgeglichen(request: Request):
    """Den Haken setzen oder loesen; gemerkt wird der Anteil von jetzt."""
    from finctl import geteilt as _g

    def haken(d, b):
        projekt, person = str(b.get("projekt") or ""), str(b.get("person") or "")
        c = conn()
        try:
            je_projekt = _g.posten(d, _alle_teile(c))
        finally:
            c.close()
        anteil = next((s.anteil_cents for s in _g.salden(d, je_projekt)
                       if s.projekt == projekt and s.person == person), 0)
        _g.ausgleichen(d, projekt, person, bool(b.get("an")), anteil, _dtm.date.today())
        return {}
    return _aendern(await request.json(), haken)
