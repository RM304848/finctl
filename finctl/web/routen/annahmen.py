"""Annahmen, ihr Abschnitt Prognosebasis und die Stellschrauben dahinter.

Auch `/api/setting`: was hier gesetzt wird, sind Annahmen, keine Einstellungen
der Oberflaeche.
"""

from __future__ import annotations

import datetime as _dtm
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl.ledger import db as ledger
from finctl.web.basis import (
    CONFIG_DIR,
    TEMPLATES,
    conn,
    umleiten,
)

router = APIRouter()


#: Die Stellschrauben, wie sie auf /annahmen erscheinen: Schluessel,
#: Beschriftung, Einheit und -- das Wichtigste -- WAS sie bewegt. Ohne die
#: letzte Spalte sieht der Gehaltsfloor aus wie eine Zahl, an der die Ziele
#: haengen, und er bewegt dort nachweislich nichts.
STELLSCHRAUBEN = (
    ("inflation_pa", "Inflation", "prozent",
     "Ziele (Hochrechnung) und Konten ab Monat 13."),
    ("rendite_nominal_pa", "Rendite Tagesgeld", "prozent",
     "Ziele. Tagesgeld bis zur Puffergrenze, nach Steuer gemessen."),
    ("rendite_depot_pa", "Rendite Depot und Policen (vor Steuern)", "prozent",
     ("Ziele. Alles über dem Tagesgeld-Ziel fließt ins Depot. "
      "Steuer 26,375 %, ohne Teilfreistellung.")),
    ("kostenschwelle_pa", "Kostenschwelle für Policen", "prozent",
     "Monatsabschluss: Warnzeichen an Policen mit höheren Effektivkosten."),
    ("gehalt_steigerung_pa", "Gehaltssteigerung p.a.", "prozent",
     "Ziele. Die grösste Einzelwirkung von allen."),
    ("salary_floor_cents", "Gehalts-Untergrenze", "euro",
     "Konten, NICHT die Ziele — dort rechnet der gemessene Median."),
    ("teilzeit_anteil", "Teilzeit: Anteil am Gehalt", "prozent",
     "Hochrechnung, sobald die Klammer Teilzeit auf Planung an ist."),
    ("teilzeit_ab", "Teilzeit ab (JJJJ-MM)", "monat",
     "Erster Monat in Teilzeit; ohne Eintrag rechnet die Klammer nicht."),
    ("lebenserwartung", "Lebenserwartung (Alter)", "jahre",
     "Hochrechnung: bis zu welchem Alter gerechnet wird und das Kapital reichen muss."),
    ("abzug_renten", "Abzug auf Renten (Steuer und KV)", "prozent",
     ("Hochrechnung: was von jeder Rente und Kapitalauszahlung abgeht, "
      "ohne die Schichten zu unterscheiden.")),
    ("aufbrauchen", "Vermögen aufbrauchen bis dahin", "prozent",
     "Ziel Rentenlücke: 100 % heißt, das Kapital endet bei null."),
)


def _renten() -> list[tuple]:
    """Die Rentenquellen als Zeilen der Belegtabelle -- aus renten.yaml.

    Namen und Herleitung aus der Konfiguration: ein Anbieter im Code ist
    einer, den spaeter jemand einzeln wieder herausnehmen muss. Betrag und
    Stand traegt man im Monatsabschluss nach, wenn ein Schreiben kommt.
    """
    from finctl import renten as _renten

    aus = []
    for q in _renten.quellen():
        wann = _renten.beginn(q)
        text = " · ".join(t for t in (
            _renten.ARTEN[q.art] + (f" ab {wann.strftime('%Y-%m')}" if wann else ""),
            "heutige Kaufkraft" if q.kaufkraft == "heute" else "nominal",
            f"Stand {q.stand.isoformat()}" if q.stand else "ohne Stand",
            q.herleitung) if t)
        aus.append((q.name, q.cents, "euro/Monat" if q.art == "rente" else "euro", text))
    return aus


@router.get("/annahmen", response_class=HTMLResponse)
def annahmen(request: Request):
    """Die Zahlen, an denen der ganze Plan haengt.

    Drei Sorten, und sie sehen absichtlich verschieden aus: was man dreht,
    was gemessen wird, und was in einer Unterlage steht. Ein Formular, das
    alle gleich darstellt, laedt dazu ein, eine Standmitteilung zu
    ueberschreiben, weil sie neben der Inflationsrate steht.
    """
    from finctl import assumptions as _ann
    from finctl import overlays as _ov
    from finctl.forecast import jahre as _jm

    _ann.reset_cache()
    ueber = _ov.einstellungen()
    dreh = []
    for key, label, einheit, wirkung in STELLSCHRAUBEN:
        wert = _ann.get(*_ann.UEBERSCHREIBBAR[key], default="" if einheit == "monat" else 0)
        basis = _ann.basiswert(key)
        dreh.append({"key": key, "label": label,
                     "einheit": einheit,
                     "wirkung": wirkung, "wert": wert, "basis": basis,
                     "ueberschrieben": key in ueber})

    c = conn()
    try:
        median = _jm.gehalt_median_cents(c)
        monate = c.execute(
            "SELECT COUNT(DISTINCT substr(t.booking_date,1,7)) FROM splits s "
            "JOIN transactions t ON t.id = s.transaction_id "
            "WHERE s.mgmt_category_id = 'einkommen/gehalt' "
            "  AND t.booking_date >= date('now','-24 months') "
            "  AND substr(t.booking_date,1,7) < strftime('%Y-%m','now')"
        ).fetchone()[0]
    finally:
        c.close()

    from finctl import person as _person

    belegt = _renten()
    beginn = _person.rentenbeginn()
    if beginn:
        belegt.append(("Rentenbeginn", beginn.strftime("%Y-%m"), "text",
                       (f"Monat nach dem {_person.angaben()['rente_ab_alter']}. "
                        "Geburtstag, aus der Einrichtung.")))
    # Die einzige Frist im ganzen Plan, die nicht verschiebbar ist -- und nur
    # fuer Privatversicherte. Hier steht nicht die Sperre selbst, sondern der
    # Tag, an dem man anfangen muss, sich damit zu befassen.
    stichtag = _person.pkv_stichtag()
    if stichtag:
        belegt.append(
            ("PKV-Entscheidung spätestens", stichtag.isoformat(), "text",
             (f"Ein Jahr vor der harten Sperre des §6 Abs. 3a SGB V mit "
              f"{_person.GKV_SPERRE_ALTER} ({_person.pkv_sperre().isoformat()}). "
              "Danach ist die Rückkehr in die GKV ausgeschlossen.")))
    from finctl import objekte as _objekte

    return TEMPLATES.TemplateResponse(request, "annahmen.html", {
        "dreh": dreh, "belegt": belegt, "objekte": _objekte.prognosen(),
        "median": median, "median_monate": monate,
        "median_rueckfall": _ann.salary_median_cents(),
        **_prognosebasis(),
    })


def _lebensende() -> list[int]:
    """Das Jahr, in dem die Lebenserwartung erreicht ist -- bis dahin muss das
    Kapital reichen. Leer ohne Geburtsdatum."""
    from finctl import assumptions as _ann
    from finctl import person as _person

    g = _person.geburtstag()
    return [g.year + _ann.lebenserwartung()] if g else []


def _jahresrechnung():
    """Die Extrapolation Jahr fuer Jahr, mit denselben Toepfen wie /ziele.

    Bis zum spaetesten Stichtag der Ziele und bis zur Lebenserwartung: erst
    dort zeigt sich, ob das Kapital neben den Renten reicht.
    """
    import yaml as _y

    from finctl.forecast import abgleich as _ag
    from finctl.forecast import jahre as _jm
    from finctl.forecast import ziele as _zm

    def _lies(name):
        pfad = CONFIG_DIR / name
        return (_y.safe_load(pfad.read_text(encoding="utf-8")) or {}) \
            if pfad.exists() else {}

    ziele = _zm.merge_edits(_lies("goals.yaml"), _lies("ziele_custom.yaml"))
    stichtage = [int(str(g["stichtag"])[:4])
                 for g in (ziele.get("ziele") or []) if g.get("stichtag")]

    # Und derselbe Ausgangsbestand. Mit null zu beginnen zeigte zwar dieselben
    # Bloecke, aber ein Endkapital, das dem Balken auf /ziele widerspricht --
    # zwei Zahlen fuer eine Rechnung sind schlimmer als eine ungenaue.
    from finctl import bestaende as _best

    c = conn()
    try:
        merged = _best.zusammenfuehren(
            c, _lies("balances.yaml"), _lies("balances_custom.yaml").get("overrides"))
        lauf = _jm.project(c, opening_cents=_zm.liquid_cents(merged),
                           toepfe=_zm.toepfe(merged),
                           depot_gewinn_cents=_zm.depot_gewinn(merged),
                           puffer_cents=_zm.puffer_cents(ziele),
                           policen_je_konto=_zm.policen_je_konto(merged),
                           end_year=max([2045, *stichtage, *_lebensende()]))
    finally:
        c.close()

    namen = {b.id: b.label for b in _ag.bloecke()}
    namen.update(rendite="Rendite", kaskade="Tagesgeld-Ziel und Depot",
                 depotentnahme="Aus dem Depot entnommen", rente="Renten",
                 policen="Policen ausgezahlt")
    blockfolge = [b.id for b in _ag.bloecke()]
    reihenfolge = [*blockfolge[:1], "rente", *blockfolge[1:],
                   "depotentnahme", "policen", "rendite", "kaskade"]
    tabelle = []
    for r in lauf.years:
        gruppen = [{"id": g, "label": namen.get(g, g),
                    "summe": sum(p.cents for p in r.posten[g]),
                    "posten": [p.als_dict() for p in r.posten[g]]}
                   for g in reihenfolge if r.posten.get(g)]
        tabelle.append({
            "jahr": r.year, **r.cashflow(),
            "sparrate": r.saving_cents, "rendite": r.return_cents,
            "ins_depot": r.ins_depot_cents,
            "tagesgeld": r.tagesgeld_cents, "depot": r.depot_cents,
            "policen": r.policen_cents, "anfang": r.opening_cents,
            "schluss": r.closing_cents, "gruppen": gruppen})

    return lauf, tabelle, namen


@router.get("/hochrechnung", response_class=HTMLResponse)
def hochrechnung(request: Request, fluss: str = ""):
    """Wohin das Geld in den naechsten Jahren fliesst -- Jahr fuer Jahr und nach Grund.

    Bis zum 27.09.2026 ein Abschnitt auf /annahmen. Dort stehen die Eingaben;
    was sie bewirken, ist eine eigene Frage und gehoert zur Vorausschau, neben
    Ziele, Konten und Planung.
    """
    from finctl.forecast import abgleich as _ag
    from finctl.forecast.herkunft import QUELLEN as _QUELLEN

    lauf, tabelle, namen = _jahresrechnung()
    c = conn()
    try:
        fenster = _ag.fenster(c)
    finally:
        c.close()
    # Standard ist das naechste Jahr: das erste, das ganz Prognose ist.
    jahre = [r.year for r in lauf.years]
    naechstes = _dtm.date.today().year + 1
    fluss_jahr = int(fluss) if fluss.isdigit() and int(fluss) in jahre else (
        naechstes if naechstes in jahre else (jahre[0] if jahre else 0))
    return TEMPLATES.TemplateResponse(request, "hochrechnung.html", {
        "quellen": _QUELLEN, "jahre_tabelle": tabelle,
        "fluss": _fluss_prognose(next((r for r in lauf.years if r.year == fluss_jahr), None),
                                 namen, fenster),
        "fluss_jahr": fluss_jahr, "fluss_jahre": jahre,
        "ruhestand": _ruhestand(), "reicht_bis": lauf.reicht_bis,
    })


def _ruhestand() -> dict:
    """Die Rentenquellen, wie sie zum Beginn ankommen -- nominal, mit Stand."""
    from finctl import assumptions as _ann
    from finctl import person as _person
    from finctl import renten as _renten

    heute = _dtm.date.today()
    inflation = _ann.inflation_pa()
    zeilen = []
    for q in _renten.quellen():
        wann = _renten.beginn(q)
        zeilen.append({
            "name": q.name, "art": _renten.ARTEN[q.art], "monatlich": q.art == "rente",
            "ab": wann.strftime("%Y-%m") if wann else "", "cents": q.cents,
            "kaufkraft": "heute" if q.kaufkraft == "heute" else "nominal",
            "nominal": _renten.nominal_cents(q, wann.year, inflation) if wann else None,
            "stand": q.stand.isoformat() if q.stand else "", "aktuell": q.aktuell(heute)})
    beginn = _person.rentenbeginn()
    return {"quellen": zeilen, "beginn": beginn.strftime("%Y-%m") if beginn else ""}


#: Posten, die als eigener Knoten stehen: eine Planzeile oder ein Kredit ist
#: ein GRUND, den man beim Namen kennen will. Gemessenes und Angenommenes steht
#: je Block -- vierzig Kategorien waeren vierzig Striche.
_EINZELN = {"plan": "Plan", "vertrag": "Vertrag"}
#: Keine Einnahme und keine Ausgabe, sondern Verteilung auf die Toepfe: die
#: Differenz steht als "gespart" bzw. "aus Rücklagen" im Bild.
_VERTEILUNG = {"rendite", "kaskade", "depotentnahme", "policen"}


def _fluss_verweis(p, block: str, fenster) -> str:
    """Wohin ein Knoten fuehrt: zur Klammer, zum Kredit -- oder zu den
    Buchungen, aus denen sein Block gemessen ist (das Messfenster)."""
    v = p.verweis or ""
    if p.quelle == "plan":
        return f"/planung#klammer-{v.split(':')[0]}" if ":" in v else "/planung"
    if v.startswith("rente:"):
        return "/hochrechnung#ruhestand"
    if p.quelle == "vertrag":
        return "/vertraege" if v.startswith("abo:") else "/kredite"
    if block == "objekt" and v:
        return f"/immobilie/{v}"      # dort steht die Prognose des Objekts
    if fenster is None:
        return "/annahmen#prognosebasis"
    return (f"/transactions?block={block}&start={fenster.von.isoformat()}"
            f"&end={fenster.bis.isoformat()}")


def _fluss_prognose(r, namen: dict, fenster=None) -> dict:
    """Ein Jahr der Extrapolation als Fluss -- nach dem GRUND, nicht nach Konto.

    Die Frage dahinter: aus welchen Gruenden verschwindet das Geld? Ein Plan
    oder ein Kredit steht deshalb mit Namen da und fuehrt zu der Stelle, an
    der man ihn aendert; Gemessenes (Prognosebasis) und Angenommenes je
    Block. Dieselben Posten wie im Rechenweg darueber, keine zweite Rechnung.
    """
    from finctl.web import sankey

    if r is None:
        return {}
    knoten: dict[str, sankey.Posten] = {}
    for g, posten in r.posten.items():
        if g in _VERTEILUNG:
            continue
        for p in posten:
            if not p.cents:
                continue
            # Teilzeit ist kein Geld, das hinausgeht, sondern weniger Gehalt:
            # sie steht im Tooltip des Gehalts, nicht als eigene Senke.
            if g == "rente":
                # Je Rente ein Knoten, wie je Objekt.
                key, label = f"rente:{p.verweis}", p.label
            elif p.quelle in _EINZELN and g != "gehalt":
                key = f"{p.quelle}:{p.verweis}:{p.label}"
                # Die Zeile, nicht die Klammer ("Klammer: Zeile"): die steht im
                # Tooltip. Gekuerzt VOR der Herkunft, damit "(Plan)" bleibt.
                zeile = p.label.split(": ", 1)[-1]
                label = f"{sankey.kurz(zeile, 22)} ({_EINZELN[p.quelle]})"
            elif g == "objekt":
                # Je Objekt ein Knoten: jedes hat seine eigene Prognose.
                key, label = f"objekt:{p.verweis}", p.label.removesuffix(" netto")
            else:
                key = f"block:{g}"          # netto je Block, wie im Rechenweg
                label = namen.get(g, g)
            k = knoten.setdefault(key, sankey.Posten(
                label, 0, href=_fluss_verweis(p, g, fenster),
                # Ein gemessener Block traegt seine Kennung, damit "übrige"
                # auf die Buchungen aller seiner Bloecke zeigen kann.
                kategorien=[g] if key == f"block:{g}" and fenster else []))
            k.cents += p.cents
            k.teile.append(f"{p.label} {sankey.euro_rund(p.cents)}")
    quellen = [k for k in knoten.values() if k.cents > 0]
    senken = [sankey.Posten(k.label, -k.cents, href=k.href, teile=k.teile,
                            kategorien=k.kategorien)
              for k in knoten.values() if k.cents < 0]
    # "gespart" landet auf dem Tagesgeld; was ueber dessen Ziel liegt, wird
    # ins Depot umgeschichtet -- das sagt der Tooltip, die Zahl steht in der
    # Tabelle unter "ins Depot".
    wohin = [f"ins Depot umgeschichtet {sankey.euro_rund(r.ins_depot_cents)}"] \
        if r.ins_depot_cents else ["bleibt auf dem Tagesgeld"]
    # "übrige" aus gemessenen Bloecken fuehrt zu deren Buchungen, der Filter
    # nimmt mehrere. Steckt eine Planzeile oder ein Kredit darin, fuehrt es
    # zum Rechenweg des Jahres, wo jeder Posten einzeln steht.
    def zu_den_buchungen(bloecke: list[str]) -> str:
        from starlette.datastructures import QueryParams

        return "/transactions?" + str(QueryParams(
            [("block", b) for b in bloecke]
            + [("start", fenster.von.isoformat()), ("end", fenster.bis.isoformat())]))

    return sankey.layout(quellen, senken, rest_teile=wohin,
                         rest_href=f"#jahr-{r.year}", uebrig_href=f"#jahr-{r.year}",
                         link=zu_den_buchungen if fenster else None)


def _prognosebasis() -> dict:
    """Worauf /konten und die Jahresrechnung stehen, je Kategorie.

    Zwei Rechnungen mit zwei Basen stehen hier nebeneinander, mit dem Grund
    jeder Differenz -- und wer eine Kategorie nicht fortgeschrieben haben
    will, waehlt sie hier fuer beide ab. Ein Abschnitt auf /annahmen und keine
    eigene Seite: es ist dieselbe Frage, woraus die Extrapolation rechnet.
    """
    from finctl.forecast import prognosebasis as _pb

    c = conn()
    try:
        v = _pb.vergleich(c)
        treffer = _treffer_fuer_seite(c)
    finally:
        c.close()
    return {
        "v": v, "treffer": treffer,
        "fenster_text": (f"{_pb.monat_text(v['fenster'].von)}–{_pb.monat_text(v['fenster'].bis)}"
                         if v["fenster"] else ""),
    }


@router.get("/prognosebasis")
def prognosebasis(request: Request):
    return umleiten(request, "/annahmen", anker="prognosebasis")


def _treffer_fuer_seite(c):
    """Der Treffer-Check: Prognose vom Vormonat gegen das Ist."""
    from finctl.forecast import treffer as _tr

    ergebnis = _tr.pruefen(c)
    return ergebnis if ergebnis["monate"] else None


@router.post("/api/prognosebasis")
async def api_prognosebasis(request: Request):
    from finctl.forecast import prognosebasis as _pb

    body = await request.json()
    kategorie = str(body.get("kategorie") or "").strip()
    if not kategorie:
        return JSONResponse({"error": "kategorie fehlt"}, status_code=400)
    c = conn()
    try:
        bekannt = c.execute("SELECT 1 FROM mgmt_categories WHERE id = ?",
                            (kategorie,)).fetchone()
    finally:
        c.close()
    if not bekannt:
        return JSONResponse({"error": f"unbekannte Kategorie {kategorie}"},
                            status_code=400)
    aus = _pb.setzen(kategorie, bool(body.get("fortschreiben")))
    return {"ok": True, "nicht_fortschreiben": sorted(aus)}


def _setting_pruefen(key: str, value) -> str | None:
    """Werte, die die Rechnung sonst still verbiegen, gar nicht erst speichern."""
    if value in (None, ""):
        return None
    einheit = next((e for k, _l, e, _w in STELLSCHRAUBEN if k == key), None)
    if einheit in ("prozent", "euro", "jahre"):
        try:
            float(value)
        except (TypeError, ValueError):
            return "keine Zahl"
    if einheit == "jahre" and not 60 <= float(value) <= 120:
        return "Alter zwischen 60 und 120"
    if key == "teilzeit_anteil" and not 0 < float(value) < 1:
        return "Anteil zwischen 0 und 100 %"
    if einheit == "monat" and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(value)):
        return "Monat als JJJJ-MM"
    return None


@router.post("/api/rente")
async def api_rente(request: Request):
    """Betrag, Stand und Notiz einer Rentenquelle aus dem Monatsabschluss.

    Dieselbe Form wie /api/bestand, damit die Tabelle dort beide Arten von
    Zeilen gleich speichert.
    """
    from finctl import renten as _renten

    body = await request.json()
    felder = {"entfernen": bool(body.get("remove"))}
    if "cents" in body:
        felder["cents"] = body["cents"]
    if body.get("as_of"):
        felder["stand"] = body["as_of"]
    if "note" in body:
        felder["notiz"] = body["note"]
    if "ab" in body:
        felder["ab"] = body["ab"]
    if "kosten_pa" in body:
        felder["kosten_pa"] = body["kosten_pa"]
    try:
        if body.get("loeschen"):
            _renten.loeschen(str(body.get("key") or ""))
        else:
            _renten.setzen(str(body.get("key") or ""), felder)
    except (TypeError, ValueError) as fehler:
        return JSONResponse({"error": str(fehler)}, status_code=400)
    return {"ok": True}


@router.post("/api/rente-neu")
async def api_rente_neu(request: Request):
    """Eine Rente oder Police anlegen -- nominal, wie im Schreiben."""
    from finctl import renten as _renten

    body = await request.json()
    try:
        kennung = _renten.anlegen(body)
    except (TypeError, ValueError) as fehler:
        return JSONResponse({"error": str(fehler)}, status_code=400)
    return {"ok": True, "id": kennung}


@router.post("/api/setting")
async def api_setting(request: Request):
    body = await request.json()
    key, value = body.get("key"), body.get("value")
    if not key:
        return JSONResponse({"error": "key required"}, status_code=400)
    from finctl import assumptions as _ann
    from finctl import overlays as _ov

    fehler = _setting_pruefen(str(key), value)
    if fehler:
        return JSONResponse({"error": fehler}, status_code=400)

    # Eine Ueberschreibung, die dem dokumentierten Wert GLEICHT, ist keine.
    # Sie zu speichern haette dieselbe Zahl an zwei Stellen stehen lassen --
    # genau die Doppelung, wegen der assumptions.yaml ueberhaupt entstanden
    # ist -- und die Basis haette sich danach nicht mehr fortschreiben
    # koennen, ohne dass die Kopie sie still ueberstimmt.
    standards = {k: (lambda kk=k: _ann.basiswert(kk))
                 for k in _ann.UEBERSCHREIBBAR}
    if key in standards and value not in (None, "") and \
            str(value) == str(standards[key]()):
        _ov.einstellung_setzen(str(key), None)
        value = None
    else:
        _ov.einstellung_setzen(str(key), value)
    _ann.reset_cache()          # der naechste Leser soll den neuen Wert sehen
    c = conn()
    try:
        if value in (None, ""):
            # Clearing an override falls back to the documented default rather
            # than storing a blank.
            c.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            c.execute(
                "INSERT INTO settings (key, value, note, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, str(value), body.get("note"), ledger.now_iso()))
        c.commit()
    finally:
        c.close()
    return {"ok": True}
