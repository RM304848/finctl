"""Vertraege mit Termin -- Abos und Versicherungen -- und ihre Buchungen."""

from __future__ import annotations

import datetime as _dtm

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl.web.basis import (
    TEMPLATES,
    _jsonfaehig,
    adresse,
    conn,
    umleiten,
)

router = APIRouter()


# Eine Seite, zwei Reiter: dieselbe Tabelle, dieselben Masken, dieselbe
# Rechnung -- nur aus verschiedenen Dateien. Getrennt bleiben die DATEN, weil
# eine Versicherung anders gepflegt wird als ein Abo (kein Vorrat, anderer
# Rhythmus der Unterlagen).
REITER = {"abos": ("abo", "Abos"), "versicherungen": ("versicherung", "Versicherungen")}


@router.get("/vertraege", response_class=HTMLResponse)
def vertraege(request: Request, ansicht: str = "abos"):
    ansicht = ansicht if ansicht in REITER else "abos"
    reiter = [{"id": k, "label": name, "href": adresse(request, ansicht=k)}
              for k, (_, name) in REITER.items()]
    return _vertragsseite(request, REITER[ansicht][0], ansicht, reiter)


@router.get("/abos")
def abos_seite(request: Request):
    return umleiten(request, "/vertraege", ansicht="abos")


@router.get("/versicherungen")
def versicherungen_seite(request: Request):
    return umleiten(request, "/vertraege", ansicht="versicherungen")


def _vertragsseite(request: Request, art: str, ansicht: str, reiter: list[dict]):
    """Abos mit Termin: anlegen, aendern, teilen, einsammeln.

    Alles, was sich nicht aus den Buchungen ablesen laesst, wird hier
    eingetragen -- Vorrat, Kuendigung, wer mitzahlt. Was sich ablesen laesst,
    schlaegt die Seite vor.
    """
    from finctl import abos as _ab

    heute = _dtm.date.today()
    c = conn()
    try:
        try:
            liste, fehler = _ab.laden(art=art), None
        except ValueError as exc:
            liste, fehler = [], str(exc)
        konten = [dict(r) for r in c.execute(
            "SELECT id, display_name FROM accounts "
            "WHERE ingest_mode = 'parsed' AND active = 1 ORDER BY display_name")]
        kategorien = [
            {"id": r["id"], "label": f"{r['oberbegriff']} / {r['name']}"
             if r["oberbegriff"] else r["name"]}
            for r in c.execute(
                "SELECT k.id, k.name, o.name AS oberbegriff FROM mgmt_categories k "
                "LEFT JOIN mgmt_categories o ON o.id = k.parent_id "
                "WHERE k.parent_id IS NOT NULL ORDER BY k.id")]
        daten = []
        for a in liste:
            termine = _ab.posten([a], a["konto"], ab=heute,
                                 bis=_ab.plus_monate(heute, 37))
            abrechnung = _ab.abrechnung(c, a, heute=heute)
            daten.append(_jsonfaehig({
                **a,
                "naechster": termine[0]["faellig"] if termine else None,
                "abrechnung": abrechnung,
                "offen_cents": sum(z["offen"] for z in abrechnung),
                "faellig_offen": any(z["status"] == "faellig" for z in abrechnung),
                "preis": _ab.preisabweichung(c, a),
                "belege": _ab.belege(c, a),
                "vorschlaege": _ab.vorschlaege(c, a),
                "fruehere": _ab.fruehere(c, a),
                "laufend": _ab.laufend(c, a, heute=heute),
            }))
    finally:
        c.close()
    return TEMPLATES.TemplateResponse(request, "abos.html", {
        "abos": daten, "fehler": fehler, "konten": konten,
        "kategorien": kategorien, "heute": heute.isoformat(),
        "art": art, "ansicht": ansicht, "reiter": reiter,
        "dateien": [f"config/{d}" for d in _ab.ARTEN[art][:2]]})


@router.get("/api/abos/buchungen")
def api_abo_buchungen(q: str = ""):
    """Abbuchungen, aus denen ein Abo angelegt werden kann."""
    from finctl import abos as _ab

    if len(q.strip()) < 2:
        return {"rows": []}
    c = conn()
    try:
        return {"rows": _ab.buchungen(c, q)}
    finally:
        c.close()


@router.get("/api/abos/eingaenge")
def api_abo_eingaenge(q: str = ""):
    """Eingaenge, die als Rueckzahlung zugeordnet werden koennen."""
    from finctl import abos as _ab

    if len(q.strip()) < 2:
        return {"rows": []}
    c = conn()
    try:
        return {"rows": _ab.eingaenge(c, q)}
    finally:
        c.close()


@router.post("/api/abos/fruehere")
async def api_abo_fruehere(request: Request):
    """Fruehere Buchungen zu einem Entwurf, der noch nicht gespeichert ist.

    Beim Anlegen gibt es noch keinen abgelegten Beleg, aus dem die Seite die
    Gegenpartei kennen koennte -- die Liste kaeme sonst erst nach dem
    Speichern, also genau dann nicht, wenn sie beim Zuordnen hilft.
    """
    from finctl import abos as _ab

    body = await request.json()
    entwurf = {"kategorie": str(body.get("kategorie") or ""),
               "buchungen": [str(h) for h in body.get("buchungen") or []],
               "ignoriert": [str(h) for h in body.get("ignoriert") or []]}
    if not entwurf["kategorie"] or not entwurf["buchungen"]:
        return {"rows": []}
    c = conn()
    try:
        return {"rows": _ab.fruehere(c, entwurf)}
    finally:
        c.close()


@router.post("/api/abos")
async def api_abo_speichern(request: Request, art: str = "abo"):
    """Ein Vertrag anlegen oder ersetzen -- geprueft, bevor es geschrieben wird.

    Die Kennung gilt ueber beide Arten: die Prognose fuehrt Abos und
    Versicherungen in einer Liste.
    """
    from finctl import abos as _ab

    if art not in _ab.ARTEN:
        return JSONResponse({"error": f"unbekannte Art {art}"}, status_code=400)
    body = await request.json()
    try:
        fremd = {a["id"] for andere in _ab.ARTEN if andere != art
                 for a in _ab.laden(art=andere)}
        if body.get("id") in fremd:
            raise ValueError(f"Kennung {body.get('id')} ist schon vergeben")
        _ab.speichern(body, art=art)
    except (ValueError, TypeError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True, "id": body.get("id")}


@router.post("/api/abos/{abo_id}/entfernen")
async def api_abo_entfernen(abo_id: str, art: str = "abo"):
    from finctl import abos as _ab

    if art not in _ab.ARTEN:
        return JSONResponse({"error": f"unbekannte Art {art}"}, status_code=400)
    _ab.entfernen(abo_id, art=art)
    return {"ok": True}
