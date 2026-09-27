"""Energie: Zaehler und Vorraete, je einer ein Reiter.

Die Seite steht fuer sich -- kein Ledger, keine Prognose. Gerechnet und
geprueft wird in `finctl/energie/`; hier wird nur gelesen, geprueft und
geschrieben. Die Schnittstellen unter /api/strom/ gelten weiter und meinen
ohne Angabe den Stromzaehler.
"""

from __future__ import annotations

import datetime as _dtm

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl.web.basis import TEMPLATES, adresse, umleiten

router = APIRouter()


def _fehler(exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


def _laden():
    from finctl.energie import speicher as _sp

    return _sp.laden()


# ------------------------------------------------------------------- Seite

@router.get("/strom")
def strom_seite(request: Request):
    return umleiten(request, "/energie", ansicht="strom")


@router.get("/energie", response_class=HTMLResponse)
def energie_seite(request: Request, ansicht: str = "strom"):
    from finctl.energie import speicher as _sp

    heute = _dtm.date.today()
    fehler = None
    try:
        bestand = _laden()
    except (ValueError, TypeError, AttributeError) as exc:
        bestand, fehler = _sp.Bestand({"strom": _sp.Zaehler("strom", "strom", "Strom",
                                                           _sp._z.STROM)}, {}), str(exc)
    reiter = ([{"id": k, "label": z.name} for k, z in bestand.zaehler.items()]
              + [{"id": k, "label": v.name} for k, v in bestand.vorraete.items()]
              + [{"id": "neu", "label": "+ Zähler oder Vorrat"}])
    for r in reiter:
        r["href"] = adresse(request, ansicht=r["id"])
    if ansicht not in bestand.zaehler and ansicht not in bestand.vorraete and ansicht != "neu":
        ansicht = "strom"
    daten = {"reiter": reiter, "ansicht": ansicht, "fehler": fehler, "heute": heute,
             "zaehlerarten": _sp.ZAEHLERARTEN, "vorratsarten": _vorratsarten()}
    if ansicht in bestand.zaehler:
        daten.update(_zaehler_daten(bestand.zaehler[ansicht], heute))
    elif ansicht in bestand.vorraete:
        from finctl.energie import vorrat as _vr

        v = bestand.vorraete[ansicht]
        daten.update(vorrat=v, auswertung=_vr.auswerten(v, heute))
    return TEMPLATES.TemplateResponse(request, "energie.html", daten)


def _vorratsarten():
    from finctl.energie import vorrat as _vr

    return _vr.ARTEN


def _zaehler_daten(z, heute) -> dict:
    from finctl.energie import zaehler as _z

    rechnungen = [_z.rechnen(zr, z.ablesungen, heute, z.messung) for zr in z.zeitraeume]
    aktuell = rechnungen[-1] if rechnungen else None
    vorschlag = None
    if aktuell and heute > aktuell.zeitraum.ende:
        try:
            vorschlag = _z.naechster_zeitraum(aktuell.zeitraum, z.ablesungen, heute, z.messung)
        except ValueError:
            vorschlag = None
    return {"zaehler": z, "m": z.messung, "aktuell": aktuell,
            "frueher": list(reversed(rechnungen[:-1])),
            "ablesungen": [a for a in z.ablesungen
                           if aktuell and aktuell.zeitraum.enthaelt(a.datum)],
            "vorschlag": vorschlag, "nr": len(z.zeitraeume) - 1}


# ------------------------------------------------------------------ Zaehler

def _zaehler(bestand, body: dict):
    zid = str(body.get("zaehler") or "strom")
    if zid not in bestand.zaehler:
        raise ValueError(f"Unbekannter Zähler {zid}.")
    return zid, bestand.zaehler[zid]


@router.post("/api/strom/ablesung")
@router.post("/api/energie/ablesung")
async def api_ablesung(request: Request):
    from finctl.energie import speicher as _sp
    from finctl.energie import zaehler as _z

    body = await request.json()
    try:
        bestand = _laden()
        zid, z = _zaehler(bestand, body)
        neu = _z.Ablesung(_z.lies_datum(body.get("datum"), "Datum"),
                          round(_z.lies_zahl(body.get("stand"), "Zählerstand"), 2))
        _z.ablesung_pruefen(z.zeitraeume, z.ablesungen, neu, _dtm.date.today(), z.messung)
    except ValueError as exc:
        return _fehler(exc)
    z.ablesungen = [*z.ablesungen, neu]
    _sp.schreiben(bestand, zid)
    return {"ok": True}


@router.post("/api/strom/ablesung/entfernen")
@router.post("/api/energie/ablesung/entfernen")
async def api_ablesung_entfernen(request: Request):
    from finctl.energie import speicher as _sp
    from finctl.energie import zaehler as _z

    body = await request.json()
    try:
        bestand = _laden()
        zid, z = _zaehler(bestand, body)
        datum = _z.lies_datum(body.get("datum"), "Datum")
    except ValueError as exc:
        return _fehler(exc)
    z.ablesungen = [a for a in z.ablesungen if a.datum != datum]
    _sp.schreiben(bestand, zid)
    return {"ok": True}


@router.post("/api/strom/zeitraum")
@router.post("/api/energie/zeitraum")
async def api_zeitraum(request: Request):
    """Den Vertrag eines Zeitraums speichern; ohne `nr` einen ersten anlegen."""
    from finctl.energie import speicher as _sp
    from finctl.energie import zaehler as _z

    body = await request.json()
    try:
        bestand = _laden()
        zid, z = _zaehler(bestand, body)
        nr = body.get("nr")
        neu = _z.zeitraum_aus(body.get("zeitraum") or {})
        _z.zeitraum_pruefen(neu, [o for i, o in enumerate(z.zeitraeume) if i != nr])
    except (ValueError, TypeError, AttributeError) as exc:
        return _fehler(exc)
    if nr is None:
        z.zeitraeume.append(neu)
    else:
        z.zeitraeume[nr] = neu
    _sp.schreiben(bestand, zid)
    return {"ok": True}


@router.post("/api/strom/zeitraum/neu")
@router.post("/api/energie/zeitraum/neu")
async def api_zeitraum_neu(request: Request):
    """Das naechste Abrechnungsjahr aus dem Vorschlag anlegen."""
    from finctl.energie import speicher as _sp
    from finctl.energie import zaehler as _z

    try:
        body = await request.json()
    except ValueError:
        body = {}
    try:
        bestand = _laden()
        zid, z = _zaehler(bestand, body or {})
        if not z.zeitraeume:
            raise ValueError("Es gibt noch keinen Zeitraum.")
        neu = _z.naechster_zeitraum(z.zeitraeume[-1], z.ablesungen, _dtm.date.today(),
                                    z.messung)
    except ValueError as exc:
        return _fehler(exc)
    z.zeitraeume.append(neu)
    _sp.schreiben(bestand, zid)
    return {"ok": True}


@router.post("/api/energie/zaehler")
async def api_zaehler(request: Request):
    """Einen Zaehler anlegen oder seine Einstellungen aendern (nicht den Strom)."""
    from finctl.energie import speicher as _sp

    body = await request.json()
    try:
        bestand = _laden()
        zid = str(body.get("id") or "")
        if zid == "strom":
            raise ValueError("Der Stromzähler hat keine Einstellungen.")
        alt = bestand.zaehler.get(zid)
        roh = {"art": body.get("art") or (alt.art if alt else ""),
               "name": str(body.get("name") or "").strip() or None,
               "profil": body.get("profil"), "grundlast": body.get("grundlast"),
               "brennwert": body.get("brennwert"), "zustandszahl": body.get("zustandszahl")}
        if not zid:
            zid = _sp.kennung(roh["name"] or roh["art"] or "", set(bestand.zaehler)
                              | set(bestand.vorraete) | {"neu"})
        neu = _sp.zaehler_aus(zid, roh)
        if alt:
            neu.zeitraeume, neu.ablesungen = alt.zeitraeume, alt.ablesungen
        _sp.einstellungen_pruefen(neu)
    except (ValueError, TypeError) as exc:
        return _fehler(exc)
    bestand.zaehler[zid] = neu
    _sp.schreiben(bestand, zid)
    return {"ok": True, "id": zid}


# ------------------------------------------------------------------ Vorraete

@router.post("/api/energie/vorrat")
async def api_vorrat(request: Request):
    """Einen Vorrat anlegen oder seine Einstellungen aendern."""
    from finctl.energie import speicher as _sp
    from finctl.energie import vorrat as _vr

    body = await request.json()
    try:
        bestand = _laden()
        vid = str(body.get("id") or "")
        alt = bestand.vorraete.get(vid)
        roh = {"art": body.get("art") or (alt.art if alt else ""),
               "name": str(body.get("name") or "").strip() or None,
               "kapazitaet": body.get("kapazitaet"),
               "mindestbestand": body.get("mindestbestand"),
               "profil": body.get("profil"), "grundlast": body.get("grundlast")}
        if not vid:
            vid = _sp.kennung(roh["name"] or roh["art"] or "", set(bestand.zaehler)
                              | set(bestand.vorraete) | {"neu"})
        neu = _vr.aus_roh(vid, roh)
        if alt:
            neu.lieferungen, neu.staende = alt.lieferungen, alt.staende
        _vr.pruefen(neu)
    except (ValueError, TypeError) as exc:
        return _fehler(exc)
    bestand.vorraete[vid] = neu
    _sp.schreiben(bestand, vid)
    return {"ok": True, "id": vid}


@router.post("/api/energie/vorrat/eintrag")
async def api_vorrat_eintrag(request: Request):
    """Eine Lieferung oder eine Peilung eintragen."""
    from finctl.energie import speicher as _sp
    from finctl.energie import vorrat as _vr
    from finctl.energie import zaehler as _z

    body = await request.json()
    try:
        bestand = _laden()
        v = bestand.vorraete.get(str(body.get("vorrat") or ""))
        if v is None:
            raise ValueError("Unbekannter Vorrat.")
        datum = _z.lies_datum(body.get("datum"), "Datum")
        if datum > _dtm.date.today():
            raise ValueError("Das Datum liegt in der Zukunft.")
        menge = _z.lies_zahl(body.get("menge"), "Menge")
        if body.get("art") == "lieferung":
            cents = round(_z.lies_zahl(body.get("cents"), "Rechnungsbetrag"))
            v.lieferungen = sorted([*v.lieferungen, _vr.Lieferung(datum, menge, cents)],
                                   key=lambda x: x.datum)
        elif body.get("art") == "stand":
            v.staende = sorted([*v.staende, _vr.Stand(datum, menge)], key=lambda x: x.datum)
        else:
            raise ValueError("Art ist lieferung oder stand.")
        _vr.pruefen(v)
    except (ValueError, TypeError) as exc:
        return _fehler(exc)
    _sp.schreiben(bestand, v.id)
    return {"ok": True}


@router.post("/api/energie/vorrat/eintrag/entfernen")
async def api_vorrat_eintrag_entfernen(request: Request):
    from finctl.energie import speicher as _sp
    from finctl.energie import vorrat as _vr
    from finctl.energie import zaehler as _z

    body = await request.json()
    try:
        bestand = _laden()
        v = bestand.vorraete.get(str(body.get("vorrat") or ""))
        if v is None:
            raise ValueError("Unbekannter Vorrat.")
        datum = _z.lies_datum(body.get("datum"), "Datum")
        if body.get("art") == "lieferung":
            v.lieferungen = [x for x in v.lieferungen if x.datum != datum]
        else:
            v.staende = [x for x in v.staende if x.datum != datum]
        _vr.pruefen(v)
    except (ValueError, TypeError) as exc:
        return _fehler(exc)
    _sp.schreiben(bestand, v.id)
    return {"ok": True}


@router.post("/api/energie/entfernen")
async def api_entfernen(request: Request):
    """Einen Zaehler oder Vorrat entfernen. Der Strom bleibt: er hat seine
    eigene Datei, und ohne Stromzaehler gibt es kein Energie-Modul."""
    from finctl.energie import speicher as _sp

    body = await request.json()
    kid = str(body.get("id") or "")
    bestand = _laden()
    if kid == "strom":
        return _fehler(ValueError("Der Stromzähler bleibt."))
    if bestand.zaehler.pop(kid, None) is None and bestand.vorraete.pop(kid, None) is None:
        return _fehler(ValueError("Unbekannt."))
    _sp.schreiben(bestand, kid)
    return {"ok": True}
