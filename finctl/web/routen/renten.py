"""Renten: gesetzliche Rente und Policen anlegen und pflegen.

Die eine Stelle dafuer. Der Monatsabschluss prueft nur, ob ein Schreiben aus
diesem Jahr vorliegt, und fuehrt hierher -- zwei Felder fuer denselben Betrag
laufen auseinander. Gespeichert wird ueber /api/rente und /api/rente-neu
(routen/annahmen.py) nach renten_custom.yaml.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from finctl.web.basis import TEMPLATES

router = APIRouter()


@router.get("/renten", response_class=HTMLResponse)
def renten(request: Request):
    from finctl import assumptions as _ann
    from finctl import person as _person
    from finctl import renten as _renten

    heute = date.today()
    schwelle = _ann.kostenschwelle_pa()
    beginn = _person.rentenbeginn()
    zeilen = [{
        "q": q, "aktuell": q.aktuell(heute),
        "art": _renten.ARTEN[q.art],
        "kaufkraft": "heutige Kaufkraft" if q.kaufkraft == "heute" else "nominal",
        "ab": q.ab.strftime("%Y-%m") if q.ab else "",
        "stand": q.stand.isoformat() if q.stand else "",
        "kosten_hoch": q.kosten_pa is not None and q.kosten_pa > schwelle,
        "notiz": q.notiz or q.herleitung,
    } for q in _renten.quellen()]
    return TEMPLATES.TemplateResponse(request, "renten.html", {
        "zeilen": zeilen, "schwelle": schwelle, "jahr": heute.year,
        "rentenbeginn": beginn.strftime("%Y-%m") if beginn else ""})
