"""Der erste Start: drei Fragen, in dieser Reihenfolge -- und eine Wahl.

1. **Wo die Daten liegen.** Muss zuerst kommen, weil die beiden anderen
   Antworten dorthin geschrieben werden.
2. **Wohin gesichert wird.** Vor den Konten, weil ab dem ersten Import etwas
   da ist, das verloren gehen kann.
3. **Welche Konten es gibt.** Ohne Konto kein Import, ohne Import kein
   Hauptbuch -- und bis hierher hat die App nichts zu zeigen.
4. **Wer plant.** Geburtsdatum, Krankenversicherung, Rentenalter: daraus
   werden Rentenbeginn und die Frist fuer die Rueckkehr in die GKV.
5. **Was die App zeigen soll.** Keine Pflicht: die Basis laeuft immer, und
   eine neue Einrichtung beginnt mit ihr. Hier schaltet man Module dazu
   (`finctl/module.py`). Deshalb zaehlt dieser Schritt nie als offen.

KEIN ZWANG UND KEINE UMLEITUNG. Die Seite steht in der Navigation wie jede
andere, und die Startseite weist auf sie hin, solange etwas fehlt. Ein
Assistent, der sich vor die App schiebt, ist beim zweiten Mal im Weg -- und
wer nur nachsehen will, wohin gesichert wird, soll nicht durch drei Schritte
klicken muessen.

WAS ERLEDIGT IST, ENTSCHEIDEN DIE DATEN. Kein Haken zum Anklicken, wie auf
/monatsabschluss: ein Haken waere eine Behauptung neben den Daten, die falsch
werden kann, ohne aufzufallen.
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from finctl import module as _module
from finctl import pfade as _p
from finctl.web.basis import TEMPLATES, conn

router = APIRouter()


def _konten_zahl() -> int:
    """Wie viele Konten es gibt. Null heisst: die App hat nichts zu zeigen."""
    c = conn()
    try:
        return int(c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
    except Exception:
        # Vor dem ersten `init` gibt es die Tabelle nicht. Das ist kein Fehler,
        # sondern genau der Zustand, den diese Seite behandelt.
        return 0
    finally:
        c.close()


def _sicherungsziel() -> str:
    """Der eingetragene Sicherungsordner, oder leer."""
    from finctl import ops

    try:
        ziel, _ = ops.backup_settings()
    except ops.KeinBackupZielError:
        return ""
    return str(ziel)


def schritte() -> list[dict]:
    """Die drei Fragen mit ihrem Stand -- gemessen, nicht abgehakt."""
    from finctl import person as _person

    ordner, grund = _p.wurzel_mit_grund()
    ziel = _sicherungsziel()
    person = _person.angaben()
    konten = _konten_zahl()
    return [
        {"nr": 1, "id": "datenordner", "titel": "Wo die Daten liegen",
         "fertig": bool(_p.ort()) or grund.startswith("config/"),
         "stand": f"{ordner} — {grund}"},
        {"nr": 2, "id": "sicherung", "titel": "Wohin gesichert wird",
         "fertig": bool(ziel),
         "stand": ziel or "noch kein Ziel eingetragen"},
        {"nr": 3, "id": "konten", "titel": "Welche Konten es gibt",
         "fertig": konten > 0,
         "stand": f"{konten} angelegt" if konten else "noch keins angelegt"},
        {"nr": 4, "id": "person", "titel": "Wer plant",
         "fertig": bool(person["geburtsdatum"] and person["krankenversicherung"]),
         "stand": _person_stand(person)},
        {"nr": 5, "id": "module", "titel": "Was die App zeigen soll",
         "fertig": True, "stand": _module_stand()},
    ]


def _person_stand(person: dict) -> str:
    if not person["geburtsdatum"]:
        return "noch kein Geburtsdatum"
    kv = person["krankenversicherung"] or "Krankenversicherung fehlt"
    return (f"geboren {person['geburtsdatum'].isoformat()}, {kv}, "
            f"Rente mit {person['rente_ab_alter']}")


def _module_stand() -> str:
    an = _module.aktive()
    dazu = [m.name for m in _module.MODULE if not m.basis and m.id in an]
    return "Basis" + (f" und {len(dazu)} Module" if dazu else " allein")


def module_zeilen() -> list[dict]:
    """Je Modul: an oder aus, was es braucht, und ob es Eintraege hat."""
    an = _module.aktive()
    zeilen = []
    for m in _module.MODULE:
        braucht = [_module.NACH_ID[b].name for b in m.benoetigt]
        eintraege = _module.eingetragen(m) if m.id not in an else []
        zeilen.append({"id": m.id, "name": m.name, "frage": m.frage, "basis": m.basis,
                       "an": m.id in an, "braucht": braucht, "eintraege": eintraege})
    return zeilen


def _anzahl(n: int, eins: str, mehr: str) -> str:
    return f"{n} {eins if n == 1 else mehr}" if n else "noch keine"


def _annahmen_angepasst() -> tuple[int, int]:
    """(abweichend, alle): wie viele Stellschrauben nicht mehr die Startwerte sind."""
    import yaml

    from finctl import assumptions as _ann
    from finctl.vorgaben import ORDNER

    start = yaml.safe_load((ORDNER / "assumptions.yaml").read_text(encoding="utf-8")) or {}

    def startwert(pfad):
        knoten = start
        for teil in pfad:
            knoten = (knoten or {}).get(teil) if isinstance(knoten, dict) else None
        return knoten

    schluessel = list(_ann.UEBERSCHREIBBAR.values())
    def jetzt(pfad):
        try:
            return _ann.get(*pfad)
        except KeyError:              # in einem aelteren Datenordner nie eingetragen
            return None

    # Was nie eingetragen wurde, ist auch nicht angepasst.
    anders = sum(1 for pfad in schluessel
                 if (wert := jetzt(pfad)) is not None and wert != startwert(pfad))
    return anders, len(schluessel)


def _angepasste_annahmen() -> str:
    anders, alle = _annahmen_angepasst()
    return f"{anders} von {alle} angepasst"


def eigene_angaben() -> list[dict]:
    """Was ein neuer Nutzer durch seine eigenen Angaben ersetzt -- und wo.

    Persoenliches steht nur in config/. Wer das Werkzeug uebernimmt, beginnt
    mit Startwerten und traegt hier Bereich fuer Bereich seine eigenen ein.
    Der Stand kommt aus den Daten, wie bei den Schritten oben. Ausgeschaltete
    Module stehen nicht in der Liste.
    """
    import yaml

    from finctl import objekte as _obj
    from finctl import person as _person
    from finctl import renten as _renten
    from finctl.forecast import szenarien as _sz
    from finctl.forecast import ziele as _ziele
    from finctl.pfade import CONFIG_DIR
    from finctl.realestate.loan import lade_kredite

    def lies(name: str) -> dict:
        pfad = CONFIG_DIR / name
        return (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}) if pfad.exists() else {}

    person = _person.angaben()
    renten = _renten.quellen()
    objekte = _obj.vorhandene()
    plaene = _sz.load(lies("szenarien.yaml"))
    zeilen = [
        ("Konten und Auszüge", "/einrichtung#konten", "accounts.yaml",
         _anzahl(_konten_zahl(), "Konto", "Konten")),
        ("Geburtsdatum, Krankenversicherung, Rente", "/einrichtung#person",
         "lebensplan.yaml", _person_stand(person)),
        ("Regeln für die Kategorien", "/regeln", "rules.yaml",
         _anzahl(len(lies("rules.yaml").get("rules") or []), "Regel", "Regeln")),
        ("Annahmen: Inflation, Rendite, Gehalt, Ruhestand", "/annahmen",
         "assumptions.yaml", _angepasste_annahmen()),
        ("Depots, Krypto, Rentenversicherungen", "/monatsabschluss#vermoegen",
         "balances.yaml", _anzahl(len(lies("balances.yaml").get("balances") or []),
                                  "Position", "Positionen")),
        ("Renten laut Mitteilung",
         "/monatsabschluss#renten" if renten else "/monatsabschluss#rente-neu", "renten.yaml",
         _anzahl(len(renten), "Quelle", "Quellen")
         + (f", {sum(1 for q in renten if not q.stand)} ohne Stand"
            if any(not q.stand for q in renten) else "")),
        ("Objekte und ihre Prognose", "/immobilien", "properties.yaml",
         _anzahl(len(objekte), "Objekt", "Objekte")),
        ("Kredite", "/kredite", "loans.yaml",
         _anzahl(len(lade_kredite(CONFIG_DIR)), "Kredit", "Kredite")),
        ("Pläne und Verpflichtungen", "/planung", "szenarien.yaml",
         _anzahl(len(plaene), "Klammer", "Klammern")),
        ("Ziele", "/ziele", "goals.yaml", _anzahl(
            len(_ziele.merge_edits(lies("goals.yaml"), lies("ziele_custom.yaml"))
                .get("ziele") or []), "Ziel", "Ziele")),
    ]
    return [{"was": was, "wo": wo, "datei": f"config/{datei}", "stand": stand}
            for was, wo, datei, stand in zeilen
            if _module.seite_an(wo.split("#")[0])]


def offen() -> int:
    """Wie viele der Fragen offen sind -- fuer den Hinweis auf /."""
    return sum(1 for s in schritte() if not s["fertig"])


def konten_zeilen() -> list[dict]:
    """Das Kontenregister fuer Schritt 3: welche Konten es gibt, wie ihre
    Auszuege gelesen werden und wo sie liegen."""
    from finctl import konten as _kn

    return [{**k, "ordner": str(_kn.auszugsordner(k)),
             "ohne_backup": _kn.eigener_pfad(k),
             "aktiv": not (k.get("active") is False or k.get("closed_on"))}
            for k in sorted(_kn.laden(), key=lambda k: str(k["id"]))]


@router.get("/einrichtung", response_class=HTMLResponse)
def einrichtung(request: Request):
    from finctl import konten as _kn
    from finctl import person as _person

    stand = schritte()
    return TEMPLATES.TemplateResponse(request, "einrichtung.html", {
        "schritte": stand,
        "zuerst": next((s["id"] for s in stand if not s["fertig"]), ""),
        "vorgabe": str(_p.standard()),
        "zeiger": str(_p.zeiger()),
        # Fuer `_neues_konto.html`.
        "arten": _kn.ARTEN, "modi": _kn.MODI, "profile": _kn.profile(),
        "konten": konten_zeilen(),
        "statements_dir": str(_p.STATEMENTS_DIR),
        "module": module_zeilen(),
        "person": _person.angaben(), "kv_arten": _person.KRANKENVERSICHERUNG,
        "eigene": eigene_angaben(),
        "sicherungen": _sicherungen(),
        # Die mitgelieferten Annahmen sind Schaetzungen; solange keine davon
        # angepasst ist, rechnet jede Prognose mit fremden Zahlen.
        "annahmen_unveraendert": _module.seite_an("/annahmen")
                                 and _annahmen_angepasst()[0] == 0,
    })


def _sicherungen() -> list[dict]:
    from finctl import ops

    return ops.sicherungen()


def _wiederhergestellt(archiv, name: str) -> JSONResponse | dict:
    from finctl import ops

    try:
        bericht = ops.wiederherstellen_neben(archiv, name)
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as fehler:
        return JSONResponse({"error": str(fehler)}, status_code=400)
    except Exception as fehler:          # ein kaputtes Archiv: tarfile, sqlite
        return JSONResponse({"error": f"Nicht lesbar: {fehler}"}, status_code=400)
    return {"ok": True, "ziel": bericht["ziel"], "transaktionen": bericht["transaktionen"],
            "werkzeug": bericht["werkzeug"], "fehlend": bericht["fehlend"],
            "stimmig": bericht["stimmig"]}


@router.post("/api/wiederherstellen")
async def api_wiederherstellen(request: Request):
    """Einen Stand aus dem eingetragenen Sicherungsordner wiederherstellen.

    Nur ein Name aus der Liste, kein Pfad: sonst liesse sich ueber diese
    Schnittstelle jede Datei des Rechners als Archiv anbieten.
    """
    from finctl import ops

    body = {}
    with contextlib.suppress(Exception):
        body = await request.json()
    name = str(body.get("name") or "")
    if name not in {s["name"] for s in ops.sicherungen()}:
        return JSONResponse({"error": "Diesen Stand gibt es im Sicherungsordner nicht."},
                            status_code=400)
    ordner, _ = ops.backup_settings()
    return _wiederhergestellt(ordner / name, name)


#: Eine Sicherung ist meist unter einem Megabyte. Groesser ist ein Irrtum.
SICHERUNG_MAX = 200 * 1024 * 1024


@router.post("/api/wiederherstellen-datei")
async def api_wiederherstellen_datei(datei: UploadFile = File(...)):
    """Eine Sicherung von woanders -- etwa auf einem neuen Rechner, auf dem
    noch kein Sicherungsordner eingetragen ist."""
    import tempfile
    from pathlib import Path

    name = Path(datei.filename or "").name
    if not name.endswith(".tar.gz"):
        return JSONResponse({"error": "Erwartet wird eine Sicherung (finance-os_….tar.gz)."},
                            status_code=400)
    inhalt = await datei.read(SICHERUNG_MAX + 1)
    if len(inhalt) > SICHERUNG_MAX:
        return JSONResponse({"error": "Größer als 200 MB -- das ist keine Sicherung."},
                            status_code=400)
    with tempfile.TemporaryDirectory() as tmp:
        archiv = Path(tmp) / name
        archiv.write_bytes(inhalt)
        return _wiederhergestellt(archiv, name)


@router.post("/api/person")
async def api_person(request: Request):
    """Geburtsdatum, Krankenversicherung, Rentenalter -- nach
    lebensplan_custom.yaml, nur was von lebensplan.yaml abweicht."""
    from finctl import person as _person

    body = await request.json()
    try:
        _person.setzen({k: body[k] for k in
                        ("geburtsdatum", "krankenversicherung", "rente_ab_alter")
                        if k in body})
    except ValueError as fehler:
        return JSONResponse({"error": str(fehler)}, status_code=400)
    return {"ok": True}


@router.post("/api/module")
async def api_module(request: Request):
    """Module ein- und ausschalten. Nimmt Benoetigtes mit (finctl/module.py)."""
    body = {}
    with contextlib.suppress(Exception):
        body = await request.json()
    wahl = body.get("wahl")
    if not isinstance(wahl, dict):
        return JSONResponse({"error": "Keine Auswahl angegeben."}, status_code=400)
    an = _module.setzen({str(k): bool(v) for k, v in wahl.items()})
    return {"ok": True, "an": sorted(an)}


@router.get("/api/datenordner")
def api_datenordner():
    """Wo die Daten liegen, und warum dort."""
    ordner, grund = _p.wurzel_mit_grund()
    return {"ordner": str(ordner), "grund": grund,
            "eingetragen": str(_p.ort()) if _p.ort() else None,
            "vorgabe": str(_p.standard()), "zeiger": str(_p.zeiger())}


@router.post("/api/datenordner")
async def api_datenordner_setzen(request: Request):
    """Einen Datenordner eintragen. VERSCHIEBT NICHTS.

    Die laufende App arbeitet weiter mit dem Ordner, den sie beim Start
    gelesen hat -- die Pfadkonstanten stehen seit dem Import fest. Das ist
    kein Mangel: Ein Programm, das mitten im Betrieb sein Hauptbuch wechselt,
    haette zwei halb geschriebene Zustaende. Die Antwort sagt deshalb
    ausdruecklich, dass es beim naechsten Start gilt.
    """
    body = {}
    with contextlib.suppress(Exception):
        body = await request.json()

    roh = str(body.get("ordner") or "").strip()
    if not roh:
        return JSONResponse({"error": "Kein Ordner angegeben."}, status_code=400)

    from pathlib import Path

    from finctl import ops

    ziel = Path(roh).expanduser()
    # Dieselbe Pruefung wie beim Sicherungsziel: durch Schreiben. Ein Ordner,
    # in den nicht geschrieben werden kann, ist als Datenordner noch
    # wertloser -- dort landet das Hauptbuch.
    if grund := ops.ziel_pruefen(ziel):
        return JSONResponse({"error": grund}, status_code=400)

    from finctl.web import basis

    _p.ort_setzen(ziel)
    wann = ("Gilt, sobald du die App beendest und neu öffnest"
            if basis.app_modus() else "Gilt beim nächsten Start")
    return {"ok": True, "ordner": str(ziel), "zeiger": str(_p.zeiger()),
            "hinweis": f"Eingetragen. {wann}; vorhandene Daten werden nicht mitgenommen."}
