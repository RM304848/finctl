"""Konten und Bestaende: Untergrenzen, Rollen, getippte Staende."""

from __future__ import annotations

import datetime as _dtm

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl import konten as _kn
from finctl.ledger import db as ledger
from finctl.web import diagramm as _dg
from finctl.web.basis import (
    CONFIG_DIR,
    NOTIZ_MAX,
    TEMPLATES,
    conn,
    umleiten,
)
from finctl.web.verkauf import _verkaeufe_aus_plan

router = APIRouter()

#: Geld, das am selben Tag verfuegbar ist. Ein Depot zaehlt in der Summe aller
#: Konten nicht mit: was dort liegt, muss erst verkauft werden.
LIQUIDE = ("giro", "tagesgeld")
#: Die Wahl "alle Konten zusammen" im Verlauf.
ZUSAMMEN = "zusammen"


def _monat(text: str) -> _dtm.date:
    return _dtm.date.fromisoformat(text[:7] + "-01")


def _diagramm_ohne_objekt(ob: dict):
    """Gemessen und vorausgerechnet in einem Bild: der Bruch zwischen beiden bleibt sichtbar."""
    ziel = ob["ziel_cents"]
    monate = ob["monate"]
    balken = [_dg.Balken(m.cents, "--muted" if not m.gemessen
                         else ("--bad" if m.cents < ziel else "--accent"),
                         "gemessen" if m.gemessen else "vorausgerechnet") for m in monate]
    legende = [("gemessen", "--accent"), ("vorausgerechnet", "--muted")]
    if any(b.farbe == "--bad" for b in balken):
        legende.append(("gemessen, unter Ziel", "--bad"))
    return _dg.saeulen(
        f"Cashflow ohne {ob['label']}", [m.monat for m in monate], "Cashflow", balken,
        legende=legende, grenzen=[_dg.Grenze("Ziel", ziel, "--ink")] if ziel else [],
        zusatz=[_dg.Linie("Gemeinschaftskonto", "--muted",
                          [m.gemeinschaftskonto_cents for m in monate])],
        untertitel="je Monat, in €")


def _diagramm_konto(v: dict):
    """Tiefpunkt und Endstand eines Kontos gegen seine Grenzen."""
    rows = v.get("rows") or []
    titel = f"Verlauf {v['id']}"
    if v.get("error") or not rows:
        return _dg.leer(titel, "Keine Daten für dieses Konto.")
    grenzen = [_dg.Grenze("Untergrenze", v.get("dispo_threshold_cents") or 0, "--bad",
                          flaeche=True)]
    if v.get("ceiling_cents"):
        grenzen.append(_dg.Grenze("Deckel", v["ceiling_cents"], "--warn"))
    unter = frozenset(i for i, r in enumerate(rows) if r["breaches_dispo"])
    return _dg.linien(
        titel, [_monat(r["month"]) for r in rows],
        [_dg.Linie("nach Kosten", "--accent", [r["trough_cents"] for r in rows],
                   art="beides", auffaellig=unter),
         _dg.Linie("nach Gehalt", "--muted", [r["closing_cents"] for r in rows])],
        grenzen=grenzen, mit_null=True, auffaellig_name="unter Grenze",
        untertitel="Tiefpunkt im Monat und Stand am Monatsende, in €")


def _diagramm_zusammen(views: list[dict]):
    """Reicht das liquide Geld fuer alle Untergrenzen?

    Die Linie ist die Summe der Tiefpunkte aller Giro- und Tagesgeldkonten; die
    Grenze die Summe der Untergrenzen ALLER Konten -- gedeckt sein muessen auch
    die uebrigen.
    """
    mit = [v for v in views if not v.get("error") and v.get("rows")]
    grenze = sum(v.get("dispo_threshold_cents") or 0 for v in mit)
    summen: dict[str, int] = {}
    for v in mit:
        if v.get("account_type") in LIQUIDE:
            for r in v["rows"]:
                summen[r["month"]] = summen.get(r["month"], 0) + r["trough_cents"]
    monate = sorted(summen)
    werte = [summen[m] for m in monate]
    return _dg.linien(
        "Giro + Tagesgeld zusammen", [_monat(m) for m in monate],
        [_dg.Linie("Giro + Tagesgeld", "--accent", werte, art="beides",
                   auffaellig=frozenset(i for i, w in enumerate(werte) if w < grenze))],
        grenzen=[_dg.Grenze("alle Untergrenzen", grenze, "--bad", flaeche=True)],
        kappen=True, auffaellig_name="unter den Untergrenzen",
        untertitel="Summe der Tiefpunkte, in €")


def _verlauf(views: list[dict], konto: str) -> tuple[str, object]:
    """Das gewaehlte Konto und sein Diagramm.

    Ohne Wahl das Konto mit dem tiefsten Punkt: die Seite soll mit dem Problem
    aufmachen, nicht mit dem alphabetisch ersten Konto.
    """
    ids = [v["id"] for v in views]
    if konto not in ids and konto != ZUSAMMEN:
        mit_tief = [v for v in views
                    if not v.get("error") and v.get("worst_trough_cents") is not None]
        tiefstes = min(mit_tief, key=lambda v: v["worst_trough_cents"], default=None)
        konto = tiefstes["id"] if tiefstes else (ids[0] if ids else ZUSAMMEN)
    if konto == ZUSAMMEN:
        return konto, _diagramm_zusammen(views)
    return konto, _diagramm_konto(next(v for v in views if v["id"] == konto))


@router.get("/konten", response_class=HTMLResponse)
# 12 als Literal, weil `ops` hier bewusst erst in den Funktionen importiert
# wird. Die Begruendung fuer die beiden Horizonte steht bei ops.HORIZON_LONG.
def konten(request: Request, months: int = 12, konto: str = ""):
    """Will any account dip below its floor, and in which month.

    The household view says whether there is enough money in total. It cannot
    say whether it is in the right account on the right day, and that is what
    a Dispo actually costs you. This is the per-account intra-month trough --
    "nach Kosten", not the month-end balance, because costs land at the start
    of a month and salary at the end.
    """
    from finctl import ops

    c = conn()
    try:
        views = ops.household_accounts(c, months=months)
        # Ab wann gerechnet statt gemessen wird.
        #
        # Derselbe Stichtag, an dem die Prognose beginnt: das Ende des letzten
        # Monats, den alle Konten ganz abdecken. Spaetere Buchungen zaehlen
        # nicht, auch wenn schon ein Auszug bis zum 4. da ist.
        gemessen_bis = ops._stichtag(c).isoformat()
        # Ob sich der Haushalt ohne das Objekt traegt -- gemessen und aus denselben
        # Posten vorausgerechnet, die unten je Konto stehen.
        from finctl import kontenregeln as _krk
        from finctl.forecast import ohne_objekt as _ob

        # Ueber kontenregeln, nicht an der Basisdatei vorbei: sonst kaeme eine
        # Aenderung aus forecast_custom.yaml hier nie an.
        ohne_objekt = _ob.kennzahl(c, views, _krk.wirksam())
    finally:
        c.close()

    # Je Konto, was gilt und was ohne Overlay gaelte -- dieselbe Anzeige wie
    # bei den Bestaenden: geaendert, und daneben steht, wovon.
    from finctl import kontenregeln as _kr

    regeln = _kr.wirksam()
    for v in views:
        rolle = dict((regeln.get("account_roles") or {}).get(v["id"]) or {})
        rolle["budget_cents"] = (regeln.get("account_budgets") or {}).get(v["id"])
        v["regel"] = rolle
        v["regel_basis"] = _kr.basiswerte(v["id"])
        v["regel_eigen"] = sorted(f for f, w in rolle.items()
                                  if w != v["regel_basis"].get(f))
    rollen_namen = ["operating", "consumption", "budget", "servicing", "savings",
                    "durchlaufend"]

    order = {"operating": 0, "consumption": 1, "budget": 2,
             "servicing": 3, "savings": 4}
    views.sort(key=lambda v: (order.get(v.get("role"), 9), v["id"]))
    # Die Regel je Konto: den Bearbeitungsbereich der Tabelle gibt es nur
    # einmal, und er wird beim Oeffnen aus diesen Werten gefuellt. Die Monate
    # gehen nicht mit -- das Diagramm zeichnet der Server.
    konten_daten = [{"id": v["id"],
                     "dispo_threshold_cents": v.get("dispo_threshold_cents"),
                     "ceiling_cents": v.get("ceiling_cents"),
                     "regel": v.get("regel") or {},
                     "regel_eigen": v.get("regel_eigen") or [],
                     "breach_months": v.get("breach_months") or [],
                     "over_ceiling_months": v.get("over_ceiling_months") or [],
                     "idle_cents": v.get("idle_cents") or 0}
                    for v in views]
    konto, verlauf = _verlauf(views, konto)
    # Das Fenster des laufenden Medians steht in forecast.yaml und wird hier
    # gelesen statt in den Text geschrieben -- sonst stimmt der Hinweis nach
    # der ersten Aenderung nicht mehr.
    import yaml as _y

    _fp = CONFIG_DIR / "forecast.yaml"
    median_monate = int(((_y.safe_load(_fp.read_text(encoding="utf-8")) or {})
                         .get("window_months") or 0) if _fp.exists() else 0)
    # Ein geplanter Verkauf im Horizont ist hier NICHT eingerechnet -- er wirkt
    # nur in der Jahresrechnung. Das muss dastehen, sonst laeuft die Rate auf
    # dieser Seite weiter, ohne dass jemand weiss, warum.
    c = conn()
    try:
        _grenze = _dtm.date.today().replace(day=1)
        _bis = _dtm.date(_grenze.year + (_grenze.month - 1 + months) // 12,
                         (_grenze.month - 1 + months) % 12 + 1, 1)
        verkauf_hinweis = [v for v in _verkaeufe_aus_plan(c).values()
                           if v.ab < _bis]
    finally:
        c.close()
    return TEMPLATES.TemplateResponse(request, "konten.html", {
        "verkauf_hinweis": verkauf_hinweis, "ohne_objekt": ohne_objekt,
        "rollen_namen": rollen_namen, "rollen_text": ROLLEN_TEXT,
        "views": views, "konten_daten": konten_daten, "gemessen_bis": gemessen_bis,
        "konto": konto, "zusammen": ZUSAMMEN, "verlauf": verlauf,
        "ohne_objekt_diagramm": _diagramm_ohne_objekt(ohne_objekt),
        "median_monate": median_monate,
        # Das Endjahr wird gerechnet, nicht getippt: im Text stand "2026 +
        # horizon_long // 12", und die 2026 darin waere im Januar falsch
        # gewesen, ohne dass jemand die Datei anfasst.
        "horizont_jahr": _dtm.date.today().year + ops.HORIZON_LONG // 12,
        "horizon_long": ops.HORIZON_LONG, "horizon_default": ops.HORIZON_DEFAULT,
        "months": months})


@router.get("/kontenregister")
def kontenregister(request: Request):
    """Das Register steht in der Einrichtung, Schritt 3 -- dort, wo man ein
    Konto zum ersten Mal anlegt, und nicht auf einer zweiten Seite daneben."""
    return umleiten(request, "/einrichtung", anker="konten")


@router.post("/api/floor/{account_id}")
async def api_floor(account_id: str, request: Request):
    """Set an account's warning floor."""
    body = await request.json()
    try:
        cents = int(round(float(body["cents"])))
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "cents required"}, status_code=400)
    c = conn()
    try:
        if not c.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone():
            return JSONResponse({"error": "unknown account"}, status_code=404)
        # Into `settings`, not the accounts table: init rewrites that table from
        # accounts.yaml on every run, so a floor set here was silently back to
        # its old value after the next import.
        #
        # Und zusaetzlich in settings_custom.yaml, denn `settings` selbst ist
        # aus nichts wiederherstellbar: ein Mindestniveau, das der Eigentuemer
        # festgelegt hat, steht in keinem Auszug.
        from finctl import overlays as _ov
        _ov.einstellung_setzen(f"floor_cents:{account_id}", cents)
        # `updated_at` ist NOT NULL und fehlte hier: das Setzen einer
        # Kontountergrenze schlug mit einem 500er fehl, seit es den Knopf
        # gibt. Aufgefallen erst, als der Aufruf zum ersten Mal in einem Test
        # lief -- im Browser sah man nur, dass sich nichts tat.
        c.execute("INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                  "updated_at = excluded.updated_at",
                  (f"floor_cents:{account_id}", str(cents), ledger.now_iso()))
        c.commit()
    finally:
        c.close()
    return {"ok": True, "cents": cents}


@router.post("/api/konto/{account_id}")
async def api_konto(account_id: str, request: Request):
    """Die Kontenregeln eines Kontos setzen: Zweck, Rolle, Grenzen, Budget.

    Sie sind keine Prognoseannahme, sondern die Regel, wie Geld zwischen den
    eigenen Konten wandert -- deshalb stehen sie hier und nicht auf
    /annahmen. Die Untergrenze bleibt in `settings`, weil sie dort schon von
    /api/floor gepflegt wird; alles andere geht nach forecast_custom.yaml.
    """
    from finctl import kontenregeln as _kr

    body = await request.json()
    c = conn()
    try:
        if not c.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone():
            return JSONResponse({"error": "unknown account"}, status_code=404)
    finally:
        c.close()

    felder: dict = {}
    for feld, art in _kr.FELDER.items():
        if feld not in body or feld == "floor_cents":
            continue
        wert = body[feld]
        if art == "cents":
            felder[feld] = None if wert in (None, "") else int(wert)
        elif art == "schalter":
            felder[feld] = None if wert is None else bool(wert)
        else:
            felder[feld] = (str(wert).strip()[:NOTIZ_MAX] or None)
    if "budget_cents" in body:
        felder["budget_cents"] = (None if body["budget_cents"] in (None, "")
                                  else int(body["budget_cents"]))
    if "floor_cents" in body and body["floor_cents"] not in (None, ""):
        from finctl import overlays as _ov
        cents = int(body["floor_cents"])
        _ov.einstellung_setzen(f"floor_cents:{account_id}", cents)
        c = conn()
        try:
            c.execute("INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                      "updated_at = excluded.updated_at",
                      (f"floor_cents:{account_id}", str(cents), ledger.now_iso()))
            c.commit()
        finally:
            c.close()
    return {"ok": True, "regel": _kr.setzen(account_id, felder)}


@router.post("/api/konto-neu")
async def api_konto_neu(request: Request):
    """Ein Konto anlegen -- der erste Schritt, den es bisher nur im Editor gab.

    Ohne Konto kein Import, ohne Import kein Hauptbuch und keine Warnung vor
    dem Tiefpunkt. Das war die Stelle, an der ein neuer Nutzer eine YAML-Datei
    von Hand schreiben musste, bevor die App ihm irgendetwas zeigen konnte.
    """

    body = await request.json()
    konto = {f: body.get(f) for f in _kn.FELDER if body.get(f) not in (None, "")}
    for feld in ("id", "display_name", "institution", "statement_folder"):
        if konto.get(feld):
            konto[feld] = str(konto[feld]).strip()
    if body.get("dispo_threshold_cents") not in (None, ""):
        try:
            konto["dispo_threshold_cents"] = int(body["dispo_threshold_cents"])
        except (TypeError, ValueError):
            return JSONResponse({"error": "Schwelle: keine Zahl"}, status_code=400)

    c = conn()
    try:
        angelegt = _kn.anlegen(c, konto)
    except ValueError as fehler:
        return JSONResponse({"error": str(fehler)}, status_code=400)
    finally:
        c.close()
    return {"ok": True, "konto": angelegt,
            "ordner": str(_kn.auszugsordner(angelegt)),
            "ohne_backup": _kn.eigener_pfad(angelegt)}


#: Die Rollen heissen in accounts- und forecast-Konfiguration englisch; auf der
#: Seite stehen sie deutsch (oberflaeche-auf-deutsch). Gespeichert wird weiter
#: der Schluessel.
ROLLEN_TEXT = {"operating": "Betrieb", "consumption": "Konsum", "budget": "Budget",
               "servicing": "Kreditbedienung", "savings": "Sparen",
               "durchlaufend": "durchlaufend"}


@router.get("/bestaende")
def bestaende(request: Request):
    """Die Bestaende sind Zeilen des Monatsabschlusses: dort wird geprueft,
    welcher Stand veraltet ist, und in derselben Zeile wird er abgelegt."""
    return umleiten(request, "/monatsabschluss", anker="konten")


def bestaende_daten() -> dict:
    """Holdings the statements cannot see: depots, equities, crypto, pensions.

    Everything here is stated rather than parsed, which is exactly why it
    needs a screen. A depot value that is only editable in YAML gets updated
    once, at the moment it is written down, and is then quietly wrong for a
    year -- while the goal progress that depends on it keeps looking precise.

    The as-of date is shown per row and deliberately not defaulted to today:
    a stale figure that says when it went stale is usable, one that claims to
    be current is not.
    """
    from datetime import date as _date

    import yaml as _y

    base = CONFIG_DIR / "balances.yaml"
    spec = (_y.safe_load(base.read_text(encoding="utf-8")) or {}) if base.exists() else {}
    custom_path = CONFIG_DIR / "balances_custom.yaml"
    custom = (_y.safe_load(custom_path.read_text(encoding="utf-8")) or {}) \
        if custom_path.exists() else {}
    overrides = {str(k): v for k, v in (custom.get("overrides") or {}).items()}

    # Grouped by kind, because that is how they are maintained: a depot has one
    # statement with one total, a policy has its own Standmitteilung with its
    # own maturity. Listing depot holdings individually and lumping the two
    # policies into one line had it exactly backwards.
    # Krypto steht bei den Depots (finctl/forecast/ziele.py, GLEICHE_ART).
    from finctl.forecast.ziele import art as _art

    labels = {"giro": "Girokonten", "tagesgeld": "Tagesgeld", "depot": "Depots",
              "rentenversicherung": "Rentenversicherungen", "": "Sonstiges"}
    order = ["giro", "tagesgeld", "depot", "rentenversicherung", ""]

    from finctl import bestaende as _best

    c = conn()
    try:
        merged = _best.zusammenfuehren(c, spec, overrides)
    finally:
        c.close()
    basis = {(i.get("account_id") or i.get("name") or ""): i
             for i in spec.get("balances") or []}
    rows = []
    for item in merged["balances"]:
        key = item.get("account_id") or item.get("name") or ""
        aus_auszug = bool(item.get("aus_auszug"))
        rows.append({
            "key": key,
            "label": item.get("name") or item.get("account_id"),
            "account_id": item.get("account_id") or "",
            "kind": _art(item.get("kind")),
            "cents": item.get("cents"),
            "gewinn_cents": item.get("gewinn_cents"),
            "as_of": str(item.get("as_of") or ("" if aus_auszug else spec.get("as_of"))
                         or ""),
            # Konten mit Auszug: Wert und Stichtag belegt, nicht aenderbar.
            "aus_auszug": aus_auszug,
            "auszug_bis": item.get("auszug_bis") or "",
            "overridden": not aus_auszug and key in overrides,
            "base_cents": (basis.get(key) or {}).get("cents"),
            "note": (item.get("note") or "").strip(),
            # Steht die Notiz im Overlay, gilt sie statt der aus der
            # Basisdatei -- und die Zeile sagt das, wie sie es beim Betrag tut.
            "note_eigen": "note" in (overrides.get(key) or {}),
            "base_note": ((basis.get(key) or {}).get("note") or "").strip(),
        })

    groups = []
    for kind in order:
        members = [r for r in rows if r["kind"] == kind]
        if not members:
            continue
        stated = [r["cents"] for r in members if r["cents"] is not None]
        groups.append({"kind": kind, "label": labels.get(kind, kind),
                       "rows": members, "total": sum(stated),
                       "unknown": len(members) - len(stated)})

    today = _date.today().isoformat()
    stale = [r for r in rows if not r["aus_auszug"] and r["as_of"] and r["as_of"] < today[:8] + "01"
             and r["as_of"][:7] < today[:7]]
    return {"rows": rows, "groups": groups, "today": today, "stale": len(stale),
            # Verbindlichkeiten werden nicht mehr hier gepflegt, sondern als
            # Verpflichtung in der Planung. Was noch in balances.yaml steht,
            # zieht weiter ab -- der Abschnitt sagt das, bis es gestrichen ist.
            "obligations": merged["obligations"]}


#: Ein Kopf fuer balances_custom.yaml, gleich welcher Knopf schreibt. Mit
#: zwei Koepfen ersetzte jeder Stand und jede Verbindlichkeit den der anderen,
#: und die Datei zeigte Diffs, in denen sich nichts geaendert hatte.
OVERLAY_KOPF = (
    "# Bestände und Verbindlichkeiten, die im Dashboard erfasst wurden.\n"
    "#\n"
    "# Überschreibt balances.yaml je Position — Wert, Stichtag und Notiz.\n"
    "# Die Basisdatei bleibt unangetastet, weil dort die Begründung zu\n"
    "# jeder Position steht. Ein Dashboard, das sie neu schreibt, löscht\n"
    "# das beim ersten Klick.\n"
    "#\n"
    "# as_of gehört zu jedem Wert. Ein veralteter Bestand, der sagt, wann\n"
    "# er veraltet ist, bleibt benutzbar; einer, der Aktualität behauptet,\n"
    "# nicht.\n\n")


def _overlay_schreiben(path, spec: dict) -> None:
    import yaml as _y

    path.write_text(OVERLAY_KOPF + _y.safe_dump(spec, allow_unicode=True, sort_keys=True),
                    encoding="utf-8")


#: Eine Bestandsnotiz ist oft die Begruendung aus balances.yaml, mehrere
#: Absaetze lang. Mit NOTIZ_MAX schnitt das Speichern sie nach 200 Zeichen ab.
BESTAND_NOTIZ_MAX = 2000


def _leer_heisst_weg(entry: dict, feld: str, wert) -> None:
    """Ein leerer Wert nimmt das Feld aus dem Overlay, statt ihn leer zu speichern."""
    if wert in (None, ""):
        entry.pop(feld, None)
    else:
        entry[feld] = wert


def _bestand_eintrag(entry: dict, body: dict) -> dict | None:
    """Der Overlay-Eintrag einer Position nach dem Speichern -- None, wenn der Betrag fehlt.

    Eine leere Notiz loescht die Notiz, statt die Begruendung aus balances.yaml
    unsichtbar zu machen. Ein leerer Gewinn heisst "nicht bekannt": dann gilt
    der Wert als Einstand.
    """
    if "note" in body:
        _leer_heisst_weg(entry, "note", str(body.get("note") or "").strip()[:BESTAND_NOTIZ_MAX])
    if "cents" in body:
        if body.get("cents") in (None, ""):
            return None
        entry["cents"] = int(body["cents"])
    elif "note" not in body and "gewinn_cents" not in body:
        return None
    if body.get("as_of"):
        entry["as_of"] = str(body["as_of"])
    if "gewinn_cents" in body:
        wert = body["gewinn_cents"]
        _leer_heisst_weg(entry, "gewinn_cents", None if wert in (None, "") else int(wert))
    return entry


@router.post("/api/bestand")
async def api_bestand(request: Request):
    """Record a stated holding, without touching the documented base file.

    Written to balances_custom.yaml for the same reason planned obligations
    are: balances.yaml carries the reasoning for every position -- why the
    ETFs are zero, what the sister's money is -- and a dashboard that
    rewrites it would destroy that on the first edit.
    """
    import yaml as _y

    body = await request.json()
    key = (body.get("key") or "").strip()
    if not key:
        return JSONResponse({"error": "key required"}, status_code=400)

    path = CONFIG_DIR / "balances_custom.yaml"
    spec = (_y.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    overrides = spec.get("overrides") or {}

    # Konten mit Auszug -- Girokonten, Scalable -- kommen aus dem Auszug. Ein
    # getippter Stand daneben waere eine zweite Wahrheit.
    from finctl import bestaende as _best

    c = conn()
    try:
        geparst = _best.geparste_konten(c)
    finally:
        c.close()
    # Gesperrt ist der WERT, nicht die Notiz: was ein Konto wert ist, sagt der
    # Auszug; was von dem Geld jemand anderem gehoert, sagt nur der Besitzer.
    if key in geparst and ("cents" in body or body.get("as_of")):
        return JSONResponse({"error": f"Wert und Stichtag von {key} kommen aus dem "
                                      "Kontoauszug"}, status_code=400)

    if body.get("remove"):
        overrides.pop(key, None)
    else:
        entry = _bestand_eintrag(dict(overrides.get(key) or {}), body)
        if entry is None:
            return JSONResponse({"error": "amount required"}, status_code=400)
        if entry:
            overrides[key] = entry
        else:
            overrides.pop(key, None)

    # Die ganze Datei zurueckschreiben, nicht nur die Staende: sonst ging mit
    # jedem gespeicherten Stand eine gestrichene Verbindlichkeit verloren, und
    # sie zog danach wieder ab.
    spec["overrides"] = overrides
    _overlay_schreiben(path, spec)
    return {"ok": True, "count": len(overrides)}


@router.post("/api/verpflichtung")
async def api_verpflichtung(request: Request):
    """Eine Verbindlichkeit anlegen, ändern oder streichen.

    Sie zieht vom liquiden Vermögen ab und liegt im selben Topf wie eigenes
    Geld -- genau deshalb muss sie sich pflegen lassen, ohne eine Datei zu
    öffnen. Getilgt wird sie gestrichen; ihre Begründung bleibt in
    balances.yaml stehen.
    """
    import yaml as _y

    from finctl import bestaende as _best

    body = await request.json()
    name = (body.get("name") or "").strip()
    kennung = _best.kennung(body.get("kennung") or name)
    if not kennung:
        return JSONResponse({"error": "Name fehlt"}, status_code=400)

    path = CONFIG_DIR / "balances_custom.yaml"
    spec = (_y.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    eigene = spec.get("verpflichtungen") or {}
    eintrag = dict(eigene.get(kennung) or {})

    if body.get("remove"):
        eintrag = {"geloescht": True}
    else:
        eintrag.pop("geloescht", None)
        if name:
            eintrag["name"] = name
        if "cents" in body:
            if body.get("cents") in (None, ""):
                return JSONResponse({"error": "Betrag fehlt"}, status_code=400)
            eintrag["cents"] = int(body["cents"])
        if "note" in body:
            notiz = str(body.get("note") or "").strip()[:NOTIZ_MAX]
            if notiz:
                eintrag["note"] = notiz
            else:
                eintrag.pop("note", None)
    eigene[kennung] = eintrag
    spec["verpflichtungen"] = eigene
    _overlay_schreiben(path, spec)
    return {"ok": True}
