"""Der Regeleditor: Vorschau, Speichern, Entfernen."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl.web.basis import (
    TEMPLATES,
    categories,
    conn,
    euro,
    tax_categories,
)

router = APIRouter()


# ------------------------------------------------------------------ Regeln


def _regel_verweise_pruefen(c: sqlite3.Connection, e: dict) -> str | None:
    """Kennungen, die die Seite eintraegt, muessen es geben.

    Eine Regel mit vertippter Kategorie liefe durch -- und schriebe Splits auf
    eine Kategorie, die in keinem Bericht auftaucht.
    """
    from finctl.rules.engine import _as_list

    s, m = e.get("set") or {}, e.get("match") or {}
    if s.get("mgmt") and not c.execute(
            "SELECT 1 FROM mgmt_categories WHERE id = ? AND active = 1", (s["mgmt"],)).fetchone():
        return f"unbekannte Kategorie {s['mgmt']}"
    if s.get("tax") and not c.execute(
            "SELECT 1 FROM tax_categories WHERE id = ?", (s["tax"],)).fetchone():
        return f"unbekannte Steuerposition {s['tax']}"
    if s.get("property") and not c.execute(
            "SELECT 1 FROM properties WHERE id = ?", (s["property"],)).fetchone():
        return f"unbekanntes Objekt {s['property']}"
    konten = {r["id"] for r in c.execute("SELECT id FROM accounts")}
    fremd = [str(k) for k in _as_list(m.get("account")) if k not in konten]
    if fremd:
        return "unbekanntes Konto " + ", ".join(fremd)
    return None


@router.get("/regeln", response_class=HTMLResponse)
def regeln_seite(request: Request, q: str = "", kategorie: str = "", aus: int | None = None):
    """Die Regeln als Tabelle, bearbeitbar ohne rules.yaml anzufassen."""
    from finctl.rules import regelwerk as rw
    from finctl.rules.engine import squash

    c = conn()
    try:
        cats, taxes = categories(c), tax_categories(c)
        props = [dict(r) for r in c.execute("SELECT id, name FROM properties ORDER BY id")]
        treffer = {r["rule_id"]: r["n"] for r in c.execute(
            "SELECT rule_id, COUNT(DISTINCT transaction_id) AS n FROM splits "
            "WHERE rule_id IS NOT NULL GROUP BY rule_id")}
        vorlage = None
        if aus:
            t = c.execute("""
                SELECT t.id, t.account_id, t.booking_date, t.amount_cents, t.counterparty,
                       t.purpose, t.raw_text,
                       (SELECT mgmt_category_id FROM splits s WHERE s.transaction_id = t.id
                        ORDER BY seq LIMIT 1) AS mgmt,
                       (SELECT tax_category_id FROM splits s WHERE s.transaction_id = t.id
                        ORDER BY seq LIMIT 1) AS tax,
                       (SELECT property_id FROM splits s WHERE s.transaction_id = t.id
                        ORDER BY seq LIMIT 1) AS property_id
                FROM transactions t WHERE t.id = ?""", (aus,)).fetchone()
            if t:
                vorlage = rw.vorlage_aus_buchung(dict(t))
                vorlage["buchung"] = (f"{t['booking_date']} · {t['account_id']} · "
                                      f"{euro(t['amount_cents'])}")
        from finctl.rules import categorize as _cz
        from finctl.rules import engine as _en

        grund = {"an": _en.grundschicht_an(), "anzahl": len(_en.grundschicht())}
        grund["wuerde"] = 0 if grund["an"] else _cz.grundschicht_vorschau(c)
    finally:
        c.close()

    namen = {k["id"]: k["label"] for k in cats}
    alle = rw.laden()
    rows = []
    for e in alle:
        s = e.get("set") or {}
        mgmt = s.get("mgmt")
        if kategorie and mgmt != kategorie:
            continue
        bedingung = rw.beschreibung(e.get("match") or {})
        if q and squash(q) not in squash(f"{e['id']} {e.get('name', '')} {bedingung}"):
            continue
        rows.append({
            "id": e["id"], "name": e.get("name") or e["id"],
            "priority": int(e.get("priority", 100)), "bedingung": bedingung,
            "setzt": (namen.get(mgmt, mgmt) if mgmt else "Review" if s.get("review")
                      else "Aufteilung" if e.get("split") else "—"),
            "treffer": treffer.get(e["id"], 0), "herkunft": e["herkunft"],
        })
    rows.sort(key=lambda r: (r["priority"], r["id"]))
    return TEMPLATES.TemplateResponse(request, "regeln.html", {
        "rows": rows, "formwerte": {e["id"]: rw.formwerte(e) for e in alle},
        "vorlage": vorlage, "categories": cats, "tax_categories": taxes,
        "properties": props, "q": q, "kategorie": kategorie, "grund": grund,
    })


@router.post("/api/regeln/grundschicht")
async def api_grundschicht(request: Request):
    """Die mitgelieferten allgemeinen Regeln ein- oder ausschalten, und neu zuordnen."""
    from finctl.rules import engine
    from finctl.rules.categorize import categorize

    body = await request.json()
    engine.grundschicht_setzen(bool(body.get("an")))
    c = conn()
    try:
        result = categorize(c, recompute=True)
    finally:
        c.close()
    return {"ok": True, "queue": result.unmatched}


@router.post("/api/regeln/vorschau")
async def api_regeln_vorschau(request: Request):
    """Was die Regel im Formular fangen wuerde. Schreibt nichts."""
    from finctl.rules import categorize as cz
    from finctl.rules import engine
    from finctl.rules import regelwerk as rw

    body = await request.json()
    c = conn()
    try:
        try:
            e, alle = rw.entwurf(body)
            rules = engine.regeln_aus(alle)
            fehler = _regel_verweise_pruefen(c, e)
            if fehler:
                raise ValueError(fehler)
        except ValueError as fehler:
            return JSONResponse({"error": str(fehler)}, status_code=400)
        out = cz.vorschau(c, rules, e["id"])
        namen = {k["id"]: k["label"] for k in categories(c)}
    finally:
        c.close()
    for z in out["zeilen"]:
        z["kategorie_jetzt"] = namen.get(z["kategorie_jetzt"], z["kategorie_jetzt"])
    return {**out, "id": e["id"]}


@router.post("/api/regeln")
async def api_regeln(request: Request):
    """Eine Regel speichern und sofort neu zuordnen.

    Sofort, weil eine Seite, die nach dem Speichern dieselben Zahlen zeigt,
    genauso aussieht wie ein Knopf, der nichts tut -- so wurde es beim
    Umhaengen einer Regel schon einmal gemeldet.
    """
    from finctl.rules import engine
    from finctl.rules import regelwerk as rw
    from finctl.rules.categorize import categorize

    body = await request.json()
    c = conn()
    try:
        try:
            e, alle = rw.entwurf(body)
            engine.regeln_aus(alle)
            fehler = _regel_verweise_pruefen(c, e)
            if fehler:
                raise ValueError(fehler)
            rw.speichern(e)
        except ValueError as fehler:
            return JSONResponse({"error": str(fehler)}, status_code=400)
        result = categorize(c, recompute=True)
    finally:
        c.close()
    return {"ok": True, "id": e["id"], "zugeordnet": result.matched, "queue": result.unmatched}


@router.post("/api/regeln/{rule_id}/entfernen")
def api_regeln_entfernen(rule_id: str):
    from finctl.rules import regelwerk as rw
    from finctl.rules.categorize import categorize

    try:
        rw.entfernen(rule_id)
    except ValueError as fehler:
        return JSONResponse({"error": str(fehler)}, status_code=400)
    c = conn()
    try:
        result = categorize(c, recompute=True)
    finally:
        c.close()
    return {"ok": True, "queue": result.unmatched}
