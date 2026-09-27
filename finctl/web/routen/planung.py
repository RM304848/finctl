"""Klammern: Was-waere-wenn-Plaene und ihre Zeilen."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl.web.basis import (
    CONFIG_DIR,
    TEMPLATES,
    _slug,
    categories,
    conn,
    setting,
)
from finctl.web.verkauf import _verkauf_kontext

router = APIRouter()


@router.post("/api/szenario-zeile-buchungen")
async def api_szenario_zeile_buchungen(request: Request):
    """Welche Buchungen zu einer Planzeile gehoeren."""
    body = await request.json()
    spec = _read_szenarien()
    target = next((x for x in spec.get("szenarien") or []
                   if x.get("id") == body.get("szenario")), None)
    if target is None:
        return JSONResponse({"error": "unbekannte Klammer"}, status_code=404)
    lines = target.get("zeilen") or []
    idx = body.get("index")
    if not isinstance(idx, int) or not 0 <= idx < len(lines):
        return JSONResponse({"error": "Zeile nicht gefunden"}, status_code=404)
    hashes = sorted({str(h) for h in body.get("buchungen") or [] if h})
    if hashes:
        lines[idx]["buchungen"] = hashes
    else:
        lines[idx].pop("buchungen", None)
    _write_szenarien(spec)
    return {"ok": True, "buchungen": hashes}


SZENARIEN_PATH = CONFIG_DIR / "szenarien.yaml"

SZENARIEN_HEADER = """# Eigene Was-wäre-wenn-Pläne, im Dashboard gepflegt.
#
# Hier darf beliebig vieles gleichzeitig AN sein, weil die eigentliche Frage meist
# "geht beides" lautet -- das Auto und die zweite Wohnung.
#
# Ausschalten statt löschen. Einen Plan zu parken muss ein Klick sein, sonst
# wird er gelöscht und die Überlegung dahinter verschwindet mit ihm.
#
# Vorzeichen wie im Ledger: negativ ist Geld raus.

"""


def _read_szenarien() -> dict:
    import yaml as _y

    if not SZENARIEN_PATH.exists():
        return {"szenarien": []}
    return _y.safe_load(SZENARIEN_PATH.read_text(encoding="utf-8")) or {"szenarien": []}


def _write_szenarien(spec: dict) -> None:
    import yaml as _y

    SZENARIEN_PATH.write_text(
        SZENARIEN_HEADER + _y.safe_dump(spec, allow_unicode=True, sort_keys=False),
        encoding="utf-8")


def _buchungen_nach_hash(c, hashes) -> list[dict]:
    """Die zugeordneten Buchungen einer Planzeile, egal wann gebucht."""
    if not hashes:
        return []
    return [dict(r) for r in c.execute(
        f"SELECT t.dedup_hash, t.booking_date, t.account_id, t.amount_cents, "
        f"substr(t.raw_text, 1, 80) AS text FROM transactions t "
        f"WHERE t.dedup_hash IN ({','.join('?' * len(hashes))}) "
        f"ORDER BY t.booking_date DESC", list(hashes))]


def _sz_kinds():
    from finctl.forecast.szenarien import KINDS

    return KINDS


@router.get("/planung", response_class=HTMLResponse)
def planung(request: Request):
    """Everything that is not yet in the ledger: commitments and plans.

    Both used to have their own page, and the split was not the useful one.
    What they share is the whole model -- a label, a category, an account, an
    amount, a date -- and keeping two forms for that meant two places to look
    and two places to forget.

    What they do NOT share is one thing, and it is the reason they stay apart
    on the page rather than in the mechanism:

    * A VERPFLICHTUNG has been entered into. Purchase instalments are owed
      whatever else is decided, so they apply in every projection and cannot
      be switched off. Making them toggleable would invite modelling away an
      obligation, which is the one thing a forecast must never make easy.
    * A PLAN is an option. Any number can be on at once, and switching one
      off has to be a click, or the plan gets deleted and the thinking with it.

    The impact is shown against the Barista FIRE target rather than only as a
    monthly figure, because that is the number a new commitment actually
    moves: 350 a month reads as affordable and silently pushes the goal out
    by years.
    """
    from datetime import date as _date

    from finctl.forecast import abgleich as _ag
    from finctl.forecast import szenarien as _sz
    from finctl.forecast import ziele as _ziele

    scenarios = _sz.load(_read_szenarien())
    c1 = conn()
    try:
        fenster = _ag.fenster(c1)
        # Je Zeile: was davon im Messfenster steckt, was sie deshalb noch
        # beitraegt, und welche Buchungen zugeordnet sind oder passen.
        messungen, zuordnung, offen = {}, {}, []
        for s in scenarios:
            for i, line in enumerate(s.lines):
                zid = f"{s.id}-{i}"
                m = _sz.messung(c1, line, fenster)
                eigene = set(line.buchungen)
                gezaehlt = {b["dedup_hash"] for b in m["buchungen"]}
                kandidaten = [b for b in _sz.vorschlaege(c1, line, fenster)
                              if b["dedup_hash"] not in gezaehlt]
                messungen[zid] = {
                    **m, "rest": _sz.restwirkung_monatlich(line, m),
                    # Getrennt, damit man sieht, WAS zaehlt und warum: von Hand
                    # zugeordnet, automatisch als gleiche Reihe, oder nur
                    # vorgeschlagen und noch ohne Wirkung.
                    "zugeordnet": _buchungen_nach_hash(c1, line.buchungen),
                    "reihe": [b for b in m["buchungen"] if b["dedup_hash"] not in eigene],
                    "kandidaten": kandidaten}
                zuordnung[zid] = {"szenario": s.id, "index": i,
                                  "hashes": list(line.buchungen),
                                  "kandidaten": [b["dedup_hash"] for b in kandidaten]}
                if line.kind == "wegfall":
                    # Die Klammersumme zeigt, was der Wegfall JETZT abzieht.
                    line.amount_cents = messungen[zid]["rest"]
                    if not m["cents"]:
                        offen.append(f"{s.name}: {line.label}")
        # Abstimmzeile: beschreibt der Plan dieselbe Welt wie das Ledger?
        ab = _ag.build(c1, _ag.planned(c1, fenster, _sz.einmalig_zugeordnet(scenarios)),
                       _sz.einmalig_zugeordnet(scenarios))
    finally:
        c1.close()
    impact = _sz.monthly_impact_cents(scenarios)

    def read(name):
        path = CONFIG_DIR / name
        import yaml as _y
        return (_y.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}

    goals = read("goals.yaml")
    from finctl import bestaende as _best

    c = conn()
    try:
        # Dieselben Bestaende wie auf /ziele: Girokonten aus dem Auszug,
        # der Rest mit den Overlays.
        balances = _best.zusammenfuehren(
            c, read("balances.yaml"), read("balances_custom.yaml").get("overrides"))
    finally:
        c.close()
    today = _date.today()

    # What he actually saves, measured rather than assumed: twelve months of
    # income minus spend, with transfers, investments and the purchase
    # instalments of the assumed object taken out. Those are excluded because
    # they are a finished commitment, not a recurring drain, and leaving them
    # in would make every scenario look unaffordable against a deficit that
    # ends.
    from finctl import objekte as _objekte

    kennungen = [p.id for p in _objekte.prognosen()]
    # Ohne Objekt mit Prognose gibt es nichts herauszunehmen. Die Bedingung
    # ganz wegzulassen ist dann richtiger, als sie gegen NULL laufen zu
    # lassen -- das schluckte jede Kaufrate ohne Objektzuordnung mit.
    ohne_kaufraten = ("AND NOT (s.mgmt_category_id = 'immobilie/kaufnebenkosten'"
                      f" AND s.property_id IN ({', '.join('?' * len(kennungen))}))"
                      ) if kennungen else ""
    c = conn()
    try:
        row = c.execute(f"""
            SELECT SUM(CASE WHEN s.amount_cents > 0 THEN s.amount_cents ELSE 0 END) AS ein,
                   SUM(CASE WHEN s.amount_cents < 0 THEN -s.amount_cents ELSE 0 END) AS aus
            FROM   splits s JOIN transactions t ON t.id = s.transaction_id
            WHERE  t.booking_date >= date('now', '-12 months')
              AND  s.mgmt_category_id NOT LIKE 'transfer/%'
              AND  s.mgmt_category_id NOT LIKE 'investment/%'
              {ohne_kaufraten}
        """, kennungen).fetchone()
    finally:
        c.close()
    surplus = int(round(((row["ein"] or 0) - (row["aus"] or 0)) / 12))

    fire = next((p for p in _ziele.progress(goals, balances, today=today)
                 if p.goal_id == "barista-fire"), None)
    fire_rows = {}
    if fire is not None:
        needed = fire.monthly_needed_cents(today) or 0
        fire_rows = {"needed": needed, "impact": impact,
                     "surplus": surplus, "surplus_after": surplus + impact,
                     "target": fire.target_cents, "due": fire.due,
                     "pct_before": (100.0 * surplus / needed) if needed else 0.0,
                     "pct_after": (100.0 * (surplus + impact) / needed) if needed else 0.0}
    # The salary floor belongs here rather than with the goals: it is a
    # planning INPUT -- the lowest monthly salary a projection may assume --
    # not a target to be reached. Sitting among the goals it read like one.
    from finctl import assumptions as _ann

    default_floor = _ann.salary_floor_cents()
    c2 = conn()
    try:
        cats = categories(c2)
        accounts = [r["id"] for r in c2.execute(
            "SELECT id FROM accounts WHERE active = 1 ORDER BY id")]
        # Wohin eine Zeile ohne Konto bucht -- mit Kennung, nicht nur als
        # "Betriebskonto": das Wort allein verriet nicht, welches Konto es ist.
        import yaml as _yb

        from finctl.ops import _operating_account
        fpfad = CONFIG_DIR / "forecast.yaml"
        betriebskonto = _operating_account(
            (_yb.safe_load(fpfad.read_text(encoding="utf-8")) or {}) if fpfad.exists() else {})
        floor, floor_source = setting(c2, "salary_floor_cents", default_floor)
        # Je Verkaufszeile: Restschuld, Netto, Frist -- dieselbe Rechnung wie
        # auf /immobilien, nur mit Termin und Preis aus der Zeile.
        objekte = [dict(r) for r in c2.execute("SELECT * FROM properties ORDER BY id")]
        verkauf_zeilen = {}
        for s_ in scenarios:
            for i_, z_ in enumerate(s_.lines):
                if z_.kind != "verkauf" or not z_.property_id:
                    continue
                prop_ = next((o for o in objekte if o["id"] == z_.property_id), None)
                if prop_ is None or not z_.start:
                    continue
                kx = _verkauf_kontext(c2, {**prop_, "planned_sale_on": z_.start.isoformat(),
                                           "sale_price_cents": z_.amount_cents})
                kx["netto_cents"] = (kx["netto_cents"] or 0) - (z_.kosten_cents or 0)
                verkauf_zeilen[f"{s_.id}-{i_}"] = kx
        # Die beobachtete Spanne wird GEMESSEN und nicht in den Text
        # geschrieben. Sie stand dort als "4.809-7.304" und war damit ein
        # Satz, der jeden Monat ein Stueck unwahrer wird, ohne dass ihn
        # jemand anfasst.
        #
        # Gemessen wird je MONAT, nicht je Buchung: ein Monat mit zwei
        # Zahlungen ergibt sonst zwei kleine statt einer richtigen Zahl, und
        # die Spanne begaenne bei 1.596 statt bei 4.599. Der laufende Monat
        # bleibt draussen, solange er unvollstaendig ist.
        spanne = c2.execute(
            "SELECT MIN(summe), MAX(summe) FROM ("
            "  SELECT SUM(s.amount_cents) AS summe"
            "  FROM splits s JOIN transactions t ON t.id = s.transaction_id"
            "  WHERE s.mgmt_category_id = 'einkommen/gehalt'"
            "    AND t.booking_date >= date('now', '-24 months')"
            "    AND substr(t.booking_date, 1, 7) < strftime('%Y-%m', 'now')"
            "  GROUP BY substr(t.booking_date, 1, 7))").fetchone()
    finally:
        c2.close()
    gehalt_min, gehalt_max = (spanne or (None, None))

    pflichten_klammern = [x for x in scenarios if x.obligation]
    plaene = [x for x in scenarios if not x.obligation]

    return TEMPLATES.TemplateResponse(request, "planung.html", {
        "abgleich": ab,
        "klammern": pflichten_klammern,
        # Der Streifen oben braucht BEIDE Sorten: die Frage "was rechnet
        # gerade mit" unterscheidet nicht zwischen Pflicht und Plan.
        "alle_klammern": scenarios,
        "offen": offen, "scenarios": plaene, "kinds": _sz_kinds(),
        "impact": impact, "categories": cats,
        "messungen": messungen, "zuordnung": zuordnung,
        # Namen statt Kennungen in den Buchungslisten, wie im Filterfeld darueber.
        "kategorie_namen": {c["id"]: c["label"] for c in cats},
        "accounts": accounts, "betriebskonto": betriebskonto,
        "frequencies": _sz.FREQUENCIES, "fire": fire_rows,
        "floor": floor, "floor_source": floor_source,
        "gehalt_min": gehalt_min, "gehalt_max": gehalt_max,
        "floor_default": default_floor, "today": today.isoformat(),
        "objekte": [{"id": o["id"], "name": o["name"]} for o in objekte],
        "verkauf_zeilen": verkauf_zeilen})


@router.post("/api/szenario")
async def api_szenario(request: Request):
    """Create, rename, toggle or delete a whole scenario."""
    body = await request.json()
    spec = _read_szenarien()
    items = spec.get("szenarien") or []
    sid = (body.get("id") or "").strip()

    if body.get("neu"):
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "Name fehlt"}, status_code=400)
        sid = _slug(name)
        if any(x.get("id") == sid for x in items):
            return JSONResponse({"error": f"{sid} gibt es schon"}, status_code=400)
        entry = {"id": sid, "name": name, "aktiv": bool(body.get("pflicht")),
                 "zeilen": []}
        if body.get("pflicht"):
            entry["pflicht"] = True
        items.append(entry)
    else:
        target = next((x for x in items if x.get("id") == sid), None)
        if target is None:
            return JSONResponse({"error": "unbekanntes Szenario"}, status_code=404)
        # Eine LEERE Verpflichtungsklammer darf weg. Sonst ist eine versehentlich
        # als Verpflichtung angelegte Klammer für immer da -- die Sperre soll
        # davor schützen, eine Schuld wegzuklicken, nicht davor, einen Vertipper
        # zu korrigieren. Sobald Positionen darin stehen, greift sie wieder:
        # dann müssen die einzeln raus, und das ist die bewusste Geste.
        # Die Sperre gilt dem ABSCHALTEN und dem Loeschen, nicht jeder
        # Aenderung. Einen Titel zu korrigieren ist kein Wegklicken einer
        # Schuld, und die Sperre auf alles zu legen hiess, dass ausgerechnet
        # die Klammer mit den echten Verpflichtungen fuer immer den Namen
        # behaelt, den sie bei der Anlage bekam.
        schutz = "aktiv" in body or body.get("loeschen")
        if (target.get("pflicht") and schutz
                and (target.get("zeilen") or not body.get("loeschen"))):
            return JSONResponse(
                {"error": "Verpflichtungen lassen sich nicht abschalten. Zum "
                          "Entfernen zuerst die einzelnen Positionen löschen -- "
                          "eine Schuld wegzuklicken darf kein Versehen sein."},
                status_code=400)
        if body.get("loeschen"):
            items = [x for x in items if x.get("id") != sid]
        else:
            if "aktiv" in body:
                target["aktiv"] = bool(body["aktiv"])
            if body.get("name"):
                target["name"] = str(body["name"]).strip()

    spec["szenarien"] = items
    _write_szenarien(spec)
    return {"ok": True, "id": sid}


def _verkaufszeile(body: dict, label: str):
    """Eine Verkaufszeile, geprueft: Objekt, Monat, Preis."""
    import re as _re

    objekt = (body.get("objekt") or "").strip()
    start = (body.get("start") or "").strip()
    c = conn()
    try:
        bekannt = c.execute("SELECT 1 FROM properties WHERE id = ?", (objekt,)).fetchone()
    finally:
        c.close()
    if not bekannt:
        return JSONResponse({"error": f"unbekanntes Objekt {objekt or '—'}"}, status_code=400)
    if not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])(-\d{2})?", start):
        return JSONResponse({"error": "Monat als JJJJ-MM"}, status_code=400)
    try:
        preis = int(body.get("amount_cents"))
        kosten = int(body.get("verkaufskosten_cents") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"error": "Preis fehlt"}, status_code=400)
    if preis <= 0 or kosten < 0:
        return JSONResponse({"error": "Preis muss positiv sein, Kosten nicht negativ"},
                            status_code=400)
    eintrag = {"label": label, "art": "verkauf", "objekt": objekt,
               "amount_cents": preis, "frequenz": "einmalig", "start": start[:7]}
    if kosten:
        eintrag["verkaufskosten_cents"] = kosten
    return eintrag


def _teilzeitzeile(body: dict, label: str):
    """Eine Teilzeitzeile, geprueft: Anteil zwischen 0 und 100 %, Monat."""
    import re as _re

    start = (body.get("start") or "").strip()
    ende = (body.get("ende") or "").strip()
    for wert in (start, ende):
        if wert and not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", wert[:7]):
            return JSONResponse({"error": "Monat als JJJJ-MM"}, status_code=400)
    if not start:
        return JSONResponse({"error": "Startmonat fehlt"}, status_code=400)
    try:
        anteil = float(body.get("anteil"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "Anteil fehlt"}, status_code=400)
    if not 0 <= anteil < 1:
        return JSONResponse({"error": "Anteil zwischen 0 und 100 %"}, status_code=400)
    eintrag = {"label": label, "art": "teilzeit", "anteil": round(anteil, 4),
               "frequenz": "monatlich", "start": start[:7]}
    if ende:
        eintrag["ende"] = ende[:7]
    return eintrag


_SONDERZEILEN = {"verkauf": _verkaufszeile, "teilzeit": _teilzeitzeile}


@router.post("/api/szenario-zeile")
async def api_szenario_zeile(request: Request):
    """Add or remove one cost line inside a scenario."""
    from finctl.forecast import szenarien as _sz

    body = await request.json()
    spec = _read_szenarien()
    # Die Klammer kommt als Kennung ODER als Name -- das Feld ist ein Textfeld
    # mit Vorschlagsliste, damit "bestehende ergaenzen" und "neue anlegen"
    # derselbe Handgriff sind. Zwei getrennte Formulare dafuer hiessen, sich
    # vor dem Tippen entscheiden zu muessen.
    ziel = (body.get("szenario") or body.get("klammer") or "").strip()
    if not ziel:
        return JSONResponse({"error": "Klammer fehlt"}, status_code=400)
    items = spec.setdefault("szenarien", [])
    target = next((x for x in items
                   if x.get("id") == ziel
                   or str(x.get("name", "")).strip().lower() == ziel.lower()), None)
    if target is None:
        if body.get("loeschen") or body.get("ersetzen"):
            return JSONResponse({"error": "unbekannte Klammer"}, status_code=404)
        # Neu anlegen. Die Art entscheidet nur hier: eine Verpflichtung laesst
        # sich danach nicht mehr abschalten, und das soll eine bewusste
        # Eingabe sein und keine Voreinstellung.
        pflicht = (body.get("klammerart") or "plan") == "pflicht"
        target = {"id": _slug(ziel), "name": ziel, "aktiv": True, "zeilen": []}
        if pflicht:
            target["pflicht"] = True
        items.append(target)

    lines = target.get("zeilen") or []
    if body.get("loeschen"):
        idx = body.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(lines):
            return JSONResponse({"error": "Zeile nicht gefunden"}, status_code=404)
        lines.pop(idx)
    else:
        label = (body.get("label") or "").strip()
        freq = body.get("frequenz") or "monatlich"
        if not label:
            return JSONResponse({"error": "Bezeichnung fehlt"}, status_code=400)
        if (body.get("art") or "") in _SONDERZEILEN:
            eintrag = _SONDERZEILEN[body["art"]](body, label)
            if isinstance(eintrag, JSONResponse):
                return eintrag
            idx = body.get("index")
            if body.get("ersetzen") and isinstance(idx, int):
                if not 0 <= idx < len(lines):
                    return JSONResponse({"error": "Zeile nicht gefunden"}, status_code=404)
                lines[idx] = eintrag
            else:
                lines.append(eintrag)
            target["zeilen"] = lines
            _write_szenarien(spec)
            return {"ok": True, "count": len(lines)}
        if freq not in _sz.FREQUENCIES:
            return JSONResponse({"error": f"unbekannte Frequenz {freq}"},
                                status_code=400)
        art = body.get("art") or "betrag"
        if art not in _sz_kinds():
            return JSONResponse({"error": f"unbekannte Art {art}"}, status_code=400)
        # Ein Wegfall trägt keinen Betrag: der wird gemessen. Einen zu
        # verlangen hiesse, genau die Behauptung einzuladen, die er vermeidet.
        if art == "betrag" and body.get("amount_cents") in (None, ""):
            return JSONResponse({"error": "Betrag fehlt"}, status_code=400)
        if not (body.get("start") or "").strip():
            return JSONResponse({"error": "Startdatum fehlt"}, status_code=400)
        # A category is REQUIRED on every line. Without it a scenario is a
        # lump sum with a name, and the moment it is switched on the forecast
        # gains money movements that no report can place. It is also what lets
        # a planned obligation and a scenario line be the same thing.
        kategorie = (body.get("kategorie") or "").strip()
        if not kategorie:
            return JSONResponse({"error": "Kategorie fehlt"}, status_code=400)
        entry = {"label": label, "art": art,
                 "amount_cents": int(body["amount_cents"] or 0)
                                 if art == "betrag" else 0,
                 "frequenz": "monatlich" if art == "wegfall" else freq,
                 "start": str(body["start"]).strip(),
                 "kategorie": kategorie}
        # Which account it is debited from. Optional, and the fallback is the
        # operating account -- but saying it matters, because Konten warns per
        # account and per day. A rate charged to Trade Republic that the model
        # books against DKB shows no breach where a real one would happen.
        if (body.get("konto") or "").strip():
            entry["konto"] = str(body["konto"]).strip()
        if (body.get("ende") or "").strip() and freq != "einmalig":
            entry["ende"] = str(body["ende"]).strip()
        if (body.get("konto") or "").strip():
            entry["konto"] = str(body["konto"]).strip()
        # ERSETZEN statt anhaengen, wenn ein Index mitkommt. Eine Annahme zu
        # korrigieren darf nicht heissen, die Zeile zu loeschen und neu
        # anzulegen -- dabei verliert sie ihre Stellung in der Klammer, und
        # bei einer Verpflichtung war Loeschen ohnehin die falsche Geste.
        idx = body.get("index")
        if body.get("ersetzen") and isinstance(idx, int):
            if not 0 <= idx < len(lines):
                return JSONResponse({"error": "Zeile nicht gefunden"},
                                    status_code=404)
            # Bei einem Wegfall bleibt der Betrag, wo er herkommt: aus dem
            # Ledger. Ihn hier zu uebernehmen machte aus einer Messung eine
            # Behauptung.
            if art == "wegfall":
                entry["amount_cents"] = 0
                entry["frequenz"] = "monatlich"
            # Die zugeordneten Buchungen gehoeren zur Zeile, nicht zum Formular:
            # wer den Titel korrigiert, soll die Zuordnung nicht verlieren.
            if lines[idx].get("buchungen"):
                entry["buchungen"] = lines[idx]["buchungen"]
            lines[idx] = entry
        else:
            lines.append(entry)

    target["zeilen"] = lines
    _write_szenarien(spec)
    return {"ok": True, "count": len(lines)}
