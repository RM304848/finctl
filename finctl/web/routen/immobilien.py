"""Immobilien: Uebersicht und Einzelseite mit Kennzahlen."""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl.web.basis import (
    TEMPLATES,
    _summe,
    categories,
    conn,
    tax_categories,
)
from finctl.web.verkauf import _verkaeufe_aus_plan, _verkauf_kontext

router = APIRouter()


@router.get("/immobilien", response_class=HTMLResponse)
def immobilien(request: Request):
    from finctl.realestate import kpi as kpimod

    c = conn()
    try:
        ids = [r["id"] for r in c.execute("SELECT id FROM properties ORDER BY id")]
        kpis = []
        for pid in ids:
            k = kpimod.compute(c, pid)
            # No schedule yet: leave the annuity whole.
            with contextlib.suppress(Exception):
                k = kpimod.split_annuity(c, k)
            kpis.append(k)
    finally:
        c.close()
    # Aus dem Register, nicht aus der Datenbank: ein gerade angelegtes Objekt
    # steht erst nach dem naechsten `init` in der Tabelle, soll aber sofort
    # auf der Seite erscheinen -- sonst sieht der Knopf aus, als tue er nichts.
    from finctl import objekte as _obj

    return TEMPLATES.TemplateResponse(request, "immobilien.html", {
        "kpis": kpis, "objekte": _obj.vorhandene(),
        "zustaende": _obj.ZUSTAENDE})


@router.post("/api/objekt-neu")
async def api_objekt_neu(request: Request):
    """Ein Objekt anlegen -- Kennung, Name, Anschrift, Datum, Zustand.

    Die Steuerfelder bleiben auf /immobilie, neben der Unterlage, aus der sie
    stammen. Beim Anlegen hat niemand die AfA-Basis zur Hand.
    """
    from finctl import objekte as _obj

    body = await request.json()
    try:
        return {"ok": True, "objekt": _obj.anlegen(body)}
    except ValueError as fehler:
        return JSONResponse({"error": str(fehler)}, status_code=400)


@router.post("/api/objekt-entfernen")
async def api_objekt_entfernen(request: Request):
    """Ein Objekt ausblenden -- der Weg, den ein neuer Nutzer zuerst braucht.

    Wer dieses Werkzeug uebernimmt, erbt sonst fremde Wohnungen in seiner
    Vermoegensrechnung. Die Basisdatei bleibt unangetastet; ihre Herleitung
    soll kein Klick loeschen.
    """
    from finctl import objekte as _obj

    body = await request.json()
    try:
        _obj.entfernen(str(body.get("id") or ""))
    except ValueError as fehler:
        return JSONResponse({"error": str(fehler)}, status_code=400)
    return {"ok": True}


@router.get("/immobilie/{property_id}", response_class=HTMLResponse)
def immobilie(request: Request, property_id: str):
    from finctl.realestate import kpi as kpimod

    c = conn()
    try:
        prop = c.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if prop is None:
            return HTMLResponse("<h1>not found</h1>", status_code=404)
        k = kpimod.compute(c, property_id)
        with contextlib.suppress(Exception):
            k = kpimod.split_annuity(c, k)
        rows = [dict(r) for r in c.execute(
            """
            SELECT s.id AS split_id, t.id, t.booking_date, t.account_id,
                   s.amount_cents, s.mgmt_category_id, s.tax_category_id,
                   s.note, t.raw_text
            FROM   splits s JOIN transactions t ON t.id = s.transaction_id
            WHERE  s.property_id = ?
            ORDER  BY t.booking_date DESC, s.seq
            """, (property_id,))]
        cats, taxes = categories(c), tax_categories(c)
        verkauf = _verkauf_kontext(c, dict(prop))
        verkauf["plan"] = _verkaeufe_aus_plan(c).get(property_id)
    finally:
        c.close()
    from finctl import objekte as _objekte

    prognose = dict((_objekte.vorhandene().get(property_id) or {}).get("prognose") or {})
    for feld in ("ab", "bis"):
        if prognose.get(feld):
            prognose[feld] = _objekte.als_monat(prognose[feld]).strftime("%Y-%m")
    return TEMPLATES.TemplateResponse(request, "immobilie.html",
                                      {"p": dict(prop), "k": k, "rows": rows,
                                       "prognose": prognose,
                                       "verkauf": verkauf, "summe": _summe(rows),
                                       "categories": cats, "tax_categories": taxes,
                                       "cat_label": {x["id"]: x["label"] for x in cats},
                                       "tax_label": {x["id"]: x["label"] for x in taxes}})


@router.post("/api/property/{property_id}")
async def api_property(property_id: str, request: Request):
    """Save acquisition data. Without it there is no AfA and no yield."""
    body = await request.json()
    fields = ("purchase_price_cents", "incidental_costs_cents", "equity_cents",
              "land_share_pct", "afa_rate_pct", "afa_start", "acquired_on",
              "planned_sale_on", "sale_price_cents")
    sets, args = [], []
    for f in fields:
        if f in body:
            sets.append(f"{f} = ?")
            args.append(body[f] if body[f] not in ("", None) else None)
    if not sets:
        return JSONResponse({"error": "nothing to update"}, status_code=400)
    args.append(property_id)
    # Erst in die Datei, dann in die Datenbank. Diese Zahlen -- was gezahlt
    # wurde, was eingesetzt ist, was beim Verkauf erwartet wird -- stehen in
    # keinem Kontoauszug und liessen sich aus keinem wiederherstellen.
    from finctl import overlays as _ov
    _ov.objekt_setzen(property_id, {f: body[f] for f in fields if f in body})
    c = conn()
    try:
        c.execute(f"UPDATE properties SET {', '.join(sets)} WHERE id = ?", args)
        c.commit()
    finally:
        c.close()
    return {"ok": True}


@router.post("/api/objekt-prognose/{property_id}")
async def api_objekt_prognose(property_id: str, request: Request):
    """Was das Objekt kuenftig abwirft -- nach properties_custom.yaml.

    Nur die Datei, keine Datenbank: die Prognose ist eine Annahme, und die
    Jahresrechnung liest sie bei jedem Aufruf neu.
    """
    from finctl import objekte as _objekte

    body = await request.json()
    try:
        _objekte.prognose_setzen(property_id, {f: body.get(f) for f in
                                               _objekte.PROGNOSE_FELDER if f in body})
    except ValueError as fehler:
        return JSONResponse({"error": str(fehler)}, status_code=400)
    return {"ok": True}
