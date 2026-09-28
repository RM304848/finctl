"""Ziele: anlegen, messen, umbenennen, sortieren."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl.web.basis import (
    CONFIG_DIR,
    TEMPLATES,
    _slug,
    conn,
)

router = APIRouter()


@router.get("/ziele", response_class=HTMLResponse)
def ziele(request: Request):
    """Declared targets and what is actually saved against them.

    The editable limits that used to sit below are gone. Every one of them was
    either a second way to say something a goal already says, or an editor
    that wrote to the wrong place:

    * Tagesgeld target and the sister's obligation are goals now, with a
      progress bar each. Keeping a second numeric field for them meant two
      sources for one figure and a silent drift between them.
    * The two percentage targets drove a SECOND Barista FIRE definition that
      never touched the bar.
    * The Dispo fields wrote accounts.dispo_threshold_cents, which
      `_floor_for` consults LAST and `finctl init` overwrites. Typing a number
      there did nothing visible and was gone by the next import. Konten writes
      the same limit to `settings`, where it survives, and shows it back.
    * The salary floor is a planning input, not a target, and now lives with
      the scenarios.
    """
    from datetime import date as _date

    import yaml as _y

    from finctl.forecast import ziele as _ziele

    def read(name):
        path = CONFIG_DIR / name
        return (_y.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}

    goals = _ziele.merge_edits(read("goals.yaml"), read("ziele_custom.yaml"))
    from finctl import bestaende as _best
    from finctl.forecast import jahre as _jm
    from finctl.forecast import rentenluecke as _rl

    today = _date.today()
    c = conn()
    try:
        merged = _best.zusammenfuehren(
            c, read("balances.yaml"), read("balances_custom.yaml").get("overrides"))
        # Der Horizont folgt dem spaetesten Stichtag, statt die Ziele auf ihn
        # zurechtzustutzen -- und reicht bis ins erste volle Rentenjahr, aus
        # dem die Rentenluecke ihren Bedarf nimmt.
        lauf = _jm.project(c, opening_cents=_ziele.liquid_cents(merged),
                           toepfe=_ziele.toepfe(merged),
                           puffer_cents=_ziele.puffer_cents(goals),
                           policen_je_konto=_ziele.policen_je_konto(merged),
                           end_year=max([2045, *_ziel_jahre(goals), *_rentenjahr()]))
        goals, luecke = _rl.mit_ziel(goals, lauf)
        targets = []
        for prog in _ziele.progress(goals, merged, today=today):
            declared = next((g for g in (goals.get("ziele") or [])
                             if g.get("id") == prog.goal_id), {})
            targets.append({
                "id": prog.goal_id, "notiz": declared.get("notiz") or "",
                "eigen": bool(declared.get("eigen")), "fest": bool(declared.get("fest")),
                "rechenweg": luecke.rechenweg if declared.get("fest") and luecke else [],
                "name": prog.name, "have": prog.have_cents,
                "basis": prog.basis, "basis_text": prog.basis_text,
                "ohne_wert": list(prog.ohne_wert), "abzug": prog.abzug_cents,
                "covered": prog.covered_cents, "uncovered": prog.uncovered_cents,
                "target": prog.target_cents, "pct": prog.pct,
                "due": prog.due.isoformat() if prog.due else None,
                "monthly": prog.monthly_needed_cents(today),
            })
        # Der Crossing Point: ab wann laufendes Einkommen OHNE Gehalt den
        # laufenden Bedarf deckt.
        #
        # Eine andere Frage als die Ziele darueber, und keine ersetzt die
        # andere: ein Zielbetrag sagt, ob man aufhoeren KANN -- Kapital, das
        # sich verzehren laesst --, der Crossing Point sagt, ab wann man nicht
        # mehr MUSS. Man kann das eine erreichen und das andere nie.
        naechstes = lauf.years[1].year if len(lauf.years) > 1 else lauf.base_year

        # Die Hochrechnung neben den Fortschritt. ZWEI Fragen, zwei Zahlen:
        # der Balken sagt, was heute da ist, die Hochrechnung, wo es am
        # Stichtag steht, wenn die Regeln halten.
        #
        # Beide zu zeigen ist der Punkt. Nur der Balken beantwortet nicht, ob
        # man ankommt -- 11,6 % bei neunzehn Jahren Laufzeit sagt fuer sich
        # genommen nichts. Nur die Hochrechnung verleitet dazu, ein kaum
        # begonnenes Depot fuer halb fertig zu halten.
        # Jedes Ziel wird auf SEINEN Stichtag hochgerechnet, nicht auf das
        # Ende der Projektion. Ein Ziel ohne Stichtag bekommt gar keine
        # Hochrechnung: der Tagesgeld-Puffer gegen das Kapital von 2045
        # gemessen stuende bei 3.174 %, und das beantwortet keine Frage, die
        # jemand gestellt hat.
        erstes, letztes = lauf.years[0].year, lauf.years[-1].year
        for t in targets:
            jahr = int(t["due"][:4]) if t["due"] else None
            # Ein Ziel gegen EINEN Topf bekommt die Hochrechnung dieses Topfs:
            # die Jahresrechnung fuehrt Tagesgeld, Depot und Policen getrennt.
            # Eine Basis quer dazu -- einzelne Konten, ein halber Topf --
            # bekommt weiter keine, statt Tagesgeld als Depotfortschritt
            # auszuweisen.
            toepfe_ziel = _ziele.toepfe_fuer_basis(t["basis"]) if t["basis"] else None
            if t["basis"] and toepfe_ziel is None:
                jahr = None
            if jahr is None or not t["target"]:
                t["projiziert"] = t["projiziert_jahr"] = None
                t["projiziert_pct"] = 0.0
                continue
            ohne_puffer = (t["id"] != _ziele.PUFFER_ZIEL
                           and (not toepfe_ziel or "tagesgeld" in toepfe_ziel))

            def wert(zeile, toepfe_ziel=toepfe_ziel, ohne_puffer=ohne_puffer):
                v = (sum(getattr(zeile, f"{topf}_cents") for topf in toepfe_ziel)
                     if toepfe_ziel else zeile.closing_cents)
                # Der Notgroschen ist Grundstock, kein Zielkapital -- auch in
                # der Hochrechnung nicht. Er liegt im Tagesgeld-Topf, und nur
                # ein Ziel, das diesen Topf zaehlt, gibt ihn ab.
                if ohne_puffer:
                    v -= min(max(zeile.tagesgeld_cents, 0), zeile.puffer_grenze_cents)
                return v

            # Zum Stichtag selbst, nicht zum Ende seines Jahres.
            t["projiziert"] = lauf.zum(_date.fromisoformat(t["due"]), wert)
            t["projiziert_jahr"] = min(max(jahr, erstes), letztes)
            t["projiziert_pct"] = 100.0 * t["projiziert"] / t["target"]
        kreuzung = {
            "jahr": lauf.crossing_year,
            "jetzt": lauf.coverage_pct(naechstes),
            "jetzt_jahr": naechstes,
            "ende": lauf.coverage_pct(lauf.years[-1].year),
            "endjahr": lauf.years[-1].year,
        }
        horizont_jahr = lauf.years[-1].year
        kontenamen = [dict(r) for r in c.execute(
            "SELECT id, display_name FROM accounts")]
    finally:
        c.close()
    # Woraus eine Basis bestehen KANN: erst die Arten, dann die einzelnen
    # Positionen. Beides in einer Liste, weil der Eigentuemer in Konten denkt
    # und nicht in Kategorien -- sein Notgroschen ist "DKB, TR, C24, Scalable,
    # Sparda" und nicht "alles Tagesgeld".
    # Die Konten heissen im Dashboard anders als in balances.yaml -- dort
    # steht die Kennung, hier soll "DKB Girokonto" stehen. Ein Haken, den man
    # setzt, muss so heissen wie das, was man meint.
    anzeige = {r["id"]: (r["display_name"] or r["id"]) for r in kontenamen}
    arten, gesehen = [], set()
    for row in merged["balances"]:
        art = _ziele.art(row.get("kind"))
        if art and art not in gesehen:
            gesehen.add(art)
            arten.append({"id": art,
                          "label": "alle " + _ziele.BASIS_LABEL.get(art, art)})
    # Einzelne Positionen bleiben in goals.yaml moeglich und werden richtig
    # gerechnet -- die SEITE bietet sie nur nicht an. Zwoelf Haken fuer eine
    # Frage, die mit fuenf beantwortet ist, sind keine bessere Bedienung.
    beschriftung = {
        str(row.get("account_id") or row.get("name") or "").lower():
        (anzeige.get(str(row.get("account_id") or "").lower())
         or row.get("name") or row.get("account_id"))
        for row in merged["balances"] if row.get("account_id") or row.get("name")}
    beschriftung.update({a["id"]: a["label"].removeprefix("alle ") for a in arten})
    for t in targets:
        if t["basis"]:
            t["basis_text"] = " + ".join(beschriftung.get(k, k) for k in t["basis"])
    return TEMPLATES.TemplateResponse(request, "ziele.html",
                                      {"targets": targets, "kreuzung": kreuzung,
                                       "horizont_jahr": horizont_jahr,
                                       "basis_arten": arten,
                                       # Die Rentenluecke ist immer ein Ziel;
                                       # ohne Geburtsdatum sagt die Seite, was fehlt.
                                       "rentenluecke_fehlt": luecke is None})


#: Kennungen, die /api/ziel nicht bearbeitet: keine -- oder die gerechnete
#: Rentenluecke, die sonst als eigenes Ziel ein zweites Mal entstuende.
_ID_FEHLER = {"": "id fehlt",
              "rentenluecke": "Die Rentenlücke wird gerechnet, nicht eingetragen"}


def _ziel_jahre(goals: dict) -> list[int]:
    return [int(str(g["stichtag"])[:4]) for g in goals.get("ziele") or []
            if g.get("stichtag")]


def _rentenjahr() -> list[int]:
    """Das erste volle Rentenjahr -- bis dorthin muss die Rechnung reichen."""
    from finctl import person as _person

    beginn = _person.rentenbeginn()
    return [beginn.year + 1] if beginn else []


@router.post("/api/ziel")
async def api_ziel(request: Request):
    """Edit a declared goal: amount, date and the reasoning beside it.

    The reasoning is a first-class field rather than a comment in the YAML,
    because it is the part that decays fastest. A target of 1.092.608 with no
    note is unauditable in a year -- nobody will remember it came from 30.000
    times 25 inflated over nineteen years, and the number will get "corrected"
    by someone who cannot reconstruct it.
    """
    from datetime import date as _date

    import yaml as _y

    body = await request.json()
    path = CONFIG_DIR / "ziele_custom.yaml"
    spec = (_y.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    edits = spec.get("ziele") or {}
    basisdatei = CONFIG_DIR / "goals.yaml"
    basis = ((_y.safe_load(basisdatei.read_text(encoding="utf-8")) or {})
             if basisdatei.exists() else {})
    basis_ids = {g.get("id") for g in (basis.get("ziele") or [])}

    # Ein neues Ziel: nur hier angelegt, in ziele_custom.yaml. Betrag und
    # Herleitung werden eingetragen, nicht berechnet.
    neu = bool(body.get("neu"))
    name = str(body.get("name") or "").strip()
    if neu:
        if not name:
            return JSONResponse({"error": "Name fehlt"}, status_code=400)
        if body.get("cents") in (None, ""):
            return JSONResponse({"error": "Betrag fehlt"}, status_code=400)
        gid = _slug(name)
        if gid in basis_ids or gid in edits:
            return JSONResponse({"error": f"Ein Ziel {gid} gibt es schon"}, status_code=400)
    else:
        gid = (body.get("id") or "").strip()
        fehler = _ID_FEHLER.get(gid)
        if fehler:
            return JSONResponse({"error": fehler}, status_code=400)
        if gid not in basis_ids and gid not in edits:
            return JSONResponse({"error": f"unbekanntes Ziel {gid}"}, status_code=404)
    when = str(body.get("stichtag") or "").strip()
    if when:
        try:
            _date.fromisoformat(when)
        except ValueError:
            return JSONResponse({"error": f"Stichtag {when}: bitte als 2035-12-31"},
                                status_code=400)

    if body.get("loeschen"):
        # Ein Ziel aus goals.yaml wird ausgeblendet, nicht aus der Datei
        # geloescht -- dort steht seine Herleitung. Ein eigenes faellt ganz weg.
        if gid in basis_ids:
            edits[gid] = {"entfernt": True}
        else:
            edits.pop(gid, None)
    elif body.get("zuruecksetzen"):
        if gid not in basis_ids:
            return JSONResponse({"error": "Ein eigenes Ziel wird gelöscht, nicht zurückgesetzt."},
                                status_code=400)
        edits.pop(gid, None)
    else:
        entry = dict(edits.get(gid) or ({"name": name} if neu else {}))
        if body.get("cents") not in (None, ""):
            entry["cents"] = int(body["cents"])
        if "basis" in body:
            # Leere Auswahl heisst "gegen alles Liquide", also faellt der
            # Eintrag weg statt als leere Liste stehenzubleiben -- sonst
            # zaehlte das Ziel gegen nichts.
            roh = body.get("basis") or []
            gewaehlt = [str(x).strip().lower() for x in roh if str(x).strip()]
            if gewaehlt:
                entry["basis"] = gewaehlt
            else:
                entry.pop("basis", None)
        if "stichtag" in body:
            when = (body.get("stichtag") or "").strip()
            if when:
                entry["stichtag"] = when
            else:
                entry.pop("stichtag", None)
        if "notiz" in body:
            entry["notiz"] = str(body.get("notiz") or "").strip()
        # Umbenennen aendert die Beschriftung, nicht die Kennung: an der
        # haengen das Overlay, die Reihenfolge und die Stellen, die ein Ziel
        # im Code suchen. Ein leeres Feld loescht den Namen nicht -- sonst
        # stuende ein Ziel ohne Bezeichnung in der Liste.
        if "name" in body and str(body["name"]).strip():
            entry["name"] = str(body["name"]).strip()[:80]
        if not entry:
            return JSONResponse({"error": "nichts zu speichern"}, status_code=400)
        edits[gid] = entry

    _ziele_custom_schreiben(path, edits, spec.get("reihenfolge"))
    return {"ok": True, "id": gid}


def _ziele_custom_schreiben(path, edits: dict, reihenfolge: list | None) -> None:
    import yaml as _y

    inhalt = {"ziele": edits}
    if reihenfolge:
        inhalt["reihenfolge"] = list(reihenfolge)
    path.write_text(
        "# Im Dashboard bearbeitete Ziele.\n"
        "#\n"
        "# Überschreibt goals.yaml je Ziel. Die Basisdatei bleibt unangetastet,\n"
        "# weil dort die Herleitung jeder Zahl steht -- und die Herleitung ist\n"
        "# das, was am schnellsten verfällt. Ein Ziel von 1.092.608 ohne Notiz\n"
        "# ist in einem Jahr nicht mehr nachvollziehbar.\n"
        "#\n"
        "# Ziele, die es in goals.yaml nicht gibt, wurden hier angelegt;\n"
        "# `entfernt: true` blendet eines aus goals.yaml aus. `reihenfolge` ist\n"
        "# die auf der Seite gezogene Anordnung.\n\n"
        + _y.safe_dump(inhalt, allow_unicode=True, sort_keys=True),
        encoding="utf-8")


@router.post("/api/ziele/reihenfolge")
async def api_ziele_reihenfolge(request: Request):
    """Die auf /ziele gezogene Reihenfolge."""
    import yaml as _y

    body = await request.json()
    ids = [str(x).strip() for x in (body.get("ids") or []) if str(x).strip()]
    if not ids or len(ids) != len(set(ids)):
        return JSONResponse({"error": "Reihenfolge ohne Ziele oder mit Doppelten"},
                            status_code=400)
    path = CONFIG_DIR / "ziele_custom.yaml"
    spec = (_y.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    _ziele_custom_schreiben(path, spec.get("ziele") or {}, ids)
    return {"ok": True}
