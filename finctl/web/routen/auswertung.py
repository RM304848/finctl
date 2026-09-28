"""Auswertende Seiten: Rueckblick, Steuer, Monatsabschluss."""

from __future__ import annotations

import contextlib
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from finctl.web.basis import (
    CAPITAL_PREFIXES,
    CONFIG_DIR,
    TEMPLATES,
    adresse,
    basis_clause,
    conn,
    umleiten,
)

# Dieselben Kacheln wie auf der Startseite: der Monatsabschluss stellt
# dieselbe Frage noch einmal, nur fuer einen abgeschlossenen Monat.
from finctl.web.routen import einrichtung as _einrichtung
from finctl.web.routen.start import _kacheln

router = APIRouter()


# Vier Blicke zurueck auf dieselben Buchungen. Jeder Reiter hat seine eigene
# Adresse, und die alten Seiten leiten dorthin um. Der Fluss steht vorn: er
# zeigt auf einen Blick, wohin das Geld gegangen ist; die anderen gehen von
# dort ins Einzelne.
RUECKBLICK = {"fluss": "Fluss", "alle": "Alle Kategorien", "fixkosten": "Fixkosten",
              "vorjahr": "Vorjahr"}


@router.get("/rueckblick", response_class=HTMLResponse)
def rueckblick(request: Request, ansicht: str = "fluss", year: str = "", vs: str = "",
               basis: str = "laufend", konto: str = "all"):
    ansicht = ansicht if ansicht in RUECKBLICK else "fluss"
    if ansicht == "vorjahr":
        daten = _vergleich(year, vs, basis)
    elif ansicht == "fluss":
        daten = _fluss(year, konto, basis)
    else:
        daten = _kosten(year or "all", ansicht == "alle", konto)
    reiter = [{"id": k, "label": name, "href": adresse(request, ansicht=k)}
              for k, name in RUECKBLICK.items()]
    return TEMPLATES.TemplateResponse(request, "rueckblick.html", {
        **daten, "ansicht": ansicht, "reiter": reiter})


def _fluss(year: str, konto: str, basis: str) -> dict:
    """Woher das Geld eines Jahres kam und wohin es ging, als Flussdiagramm.

    Netto je Subkategorie wie ueberall im Rueckblick: eine Erstattung mindert
    ihre Position. Was netto hereinkommt, ist links eine Quelle (je
    Subkategorie -- Gehalt und Mieteinnahmen sind verschiedene Fragen), was
    netto hinausgeht, rechts eine Senke (je Oberkategorie, sonst zerfaellt die
    rechte Seite in sechzig Striche). Umbuchungen zaehlen nicht: sie sind
    Geld, das die Seite wechselt.

    Jeder Knoten fuehrt zu seinen Buchungen -- Jahr, Konto und genau die
    Kategorien dahinter, auch bei "übrige". Was nicht ausgegeben wurde, steht
    nicht als "gespart" da, sondern als das, was damit geschah: ins Depot, in
    einen Objektkauf, oder auf den Konten geblieben.
    """
    from datetime import date as _d

    from starlette.datastructures import QueryParams

    from finctl.web import sankey

    c = conn()
    try:
        years = [r[0] for r in c.execute(
            "SELECT DISTINCT substr(booking_date,1,4) FROM transactions ORDER BY 1 DESC")]
        year = year if year in years else (years[0] if years else str(_d.today().year))
        konten = [r[0] for r in c.execute(
            "SELECT DISTINCT account_id FROM transactions ORDER BY 1")]
        umfeld, args = "", [year]
        if konto and konto != "all":
            umfeld, args = " AND t.account_id = ?", [*args, konto]
        zeilen = [dict(r) for r in c.execute(f"""
            SELECT COALESCE(p.id, cat.id, '__none__')   AS parent_id,
                   COALESCE(p.name, cat.name, 'Unklar') AS parent_name,
                   COALESCE(cat.id, '__none__')         AS cat_id,
                   COALESCE(cat.name, 'ohne Kategorie') AS cat_name,
                   SUM(s.amount_cents) AS cents
            FROM        splits s
            JOIN        transactions t ON t.id = s.transaction_id
            LEFT JOIN   mgmt_categories cat ON cat.id = s.mgmt_category_id
            LEFT JOIN   mgmt_categories p   ON p.id = cat.parent_id
            WHERE       COALESCE(cat.kind,'') <> 'transfer'
              AND       substr(t.booking_date,1,4) = ?{umfeld}
            GROUP BY    COALESCE(p.id, cat.id, '__none__'), COALESCE(cat.id, '__none__')
            """, args)]
        bis = c.execute("SELECT MAX(booking_date) FROM transactions "
                        "WHERE substr(booking_date,1,4) = ?", (year,)).fetchone()[0] or ""
    finally:
        c.close()

    filter_ = [("start", f"{year}-01-01"), ("end", f"{year}-12-31")]
    if konto and konto != "all":
        filter_.append(("account", konto))

    def link(kategorien: list[str]) -> str:
        return "/transactions?" + str(QueryParams(
            [("category", k) for k in kategorien] + filter_))

    quellen, senken, kapital = _fluss_knoten(zeilen)
    alle_konten = not konto or konto == "all"
    konten_rest = alle_konten and basis == "alles"
    wohin = []
    if basis == "alles":
        quellen += [k for k in kapital if k.cents > 0]
        senken += [sankey.Posten(k.label, -k.cents, art="rest", kategorien=k.kategorien)
                   for k in kapital if k.cents < 0]
    else:
        # Kapital bleibt draussen -- ein Objektkauf waere sonst die Haelfte des
        # Bildes --, der Tooltip des Ueberschusses sagt, wohin er ging.
        wohin = [f"{k.label} {sankey.euro_rund(abs(k.cents))}" for k in kapital]
    return {"fluss": sankey.layout(
                quellen, senken, link=link, rest_teile=wohin,
                rest_raus="auf den Konten geblieben" if konten_rest else "Überschuss",
                rest_rein="von den Konten genommen" if konten_rest else "Fehlbetrag",
                rest_href="/monatsabschluss#konten" if konten_rest else ""),
            "year": year, "years": years, "konto": konto or "all", "konten": konten,
            "basis": basis, "bis": bis[:7] if bis[:4] == str(_d.today().year) else ""}


#: Kapital, benannt nach Richtung: (hinaus, herein).
_KAPITAL_NAMEN = {"investment/": ("ins Depot", "aus dem Depot"),
                  "immobilie/kaufnebenkosten": ("in einen Objektkauf", "aus einem Objektkauf")}


def _fluss_knoten(zeilen: list[dict]):
    """Quellen je Subkategorie, Senken je Oberkategorie, Kapital fuer sich.

    Kapital (Depotkaeufe, Kaufnebenkosten) ist kein Verbrauch und steht nie
    als Senke einer Oberkategorie; der Aufrufer entscheidet, ob es als
    eigener Knoten erscheint. Das Vorzeichen eines Kapitalknotens bleibt das
    des Ledgers: negativ hinaus, positiv herein.
    """
    from finctl.web import sankey

    quellen: list = []
    senken: dict[str, sankey.Posten] = {}
    kapital: dict[str, sankey.Posten] = {}
    for z in sorted(zeilen, key=lambda z: z["cents"]):
        pr = next((p for p in CAPITAL_PREFIXES if z["cat_id"].startswith(p)), None)
        if pr:
            k = kapital.setdefault(pr, sankey.Posten(pr, 0, art="rest"))
            k.cents += z["cents"]
            k.kategorien.append(z["cat_id"])
        elif z["cents"] > 0:
            quellen.append(sankey.Posten(z["cat_name"], z["cents"], kategorien=[z["cat_id"]]))
        elif z["cents"] < 0:
            s = senken.setdefault(z["parent_id"], sankey.Posten(z["parent_name"], 0))
            s.cents -= z["cents"]
            s.kategorien.append(z["cat_id"])
            s.teile.append(f"{z['cat_name']} {sankey.euro_rund(-z['cents'])}")
    for pr, k in kapital.items():
        raus, rein = _KAPITAL_NAMEN.get(pr, (pr, pr))
        k.label = raus if k.cents < 0 else rein
    return quellen, list(senken.values()), [k for k in kapital.values() if k.cents]


@router.get("/report")
def report(request: Request):
    return umleiten(request, "/rueckblick", ansicht="vorjahr")


@router.get("/monitor")
def monitor(request: Request, umfang: str = "fixkosten"):
    return umleiten(request, "/rueckblick", umfang="",
                    ansicht="alle" if umfang == "alle" else "fixkosten")


def _vergleich(year: str, vs: str, basis: str) -> dict:
    """Kategorie and Subkategorie, this year against last.

    The overview answers "where does it go". This answers "what changed",
    which is the only one of the two that suggests an action: a number on its
    own is just a fact, while the same number 40% higher than last year is a
    question.

    Compared over the SAME MONTHS of both years. Nine months of 2026 against
    twelve of 2025 would report a saving on every single line, which is worse
    than no comparison at all.
    """
    from datetime import date as _d

    c = conn()
    try:
        years = [r["y"] for r in c.execute(
            "SELECT DISTINCT substr(booking_date,1,4) AS y FROM transactions ORDER BY y DESC")]
        # Ein Jahr, das es nicht gibt -- "all" aus dem Fixkosten-Reiter --,
        # faellt auf das neueste zurueck statt auf eine leere Tabelle.
        year = year if year in years else (years[0] if years else str(_d.today().year))
        vs = vs or (years[1] if len(years) > 1 else "")

        # The month the current year's data actually stops at, applied to both
        # sides so the windows are comparable.
        last_month = c.execute(
            "SELECT MAX(substr(booking_date,6,2)) FROM transactions "
            "WHERE substr(booking_date,1,4) = ?", (year,)).fetchone()[0] or "12"
        basis_sql, basis_args = basis_clause(basis)

        def totals(y: str) -> dict[str, dict]:
            if not y:
                return {}
            return {
                (r["parent_id"], r["cat_id"]): dict(r) for r in c.execute(f"""
                SELECT COALESCE(p.id, cat.id, '__none__')   AS parent_id,
                       COALESCE(p.name, cat.name, 'Unklar') AS parent_name,
                       COALESCE(cat.id, '__none__')         AS cat_id,
                       COALESCE(cat.name, 'ohne Kategorie') AS cat_name,
                       COUNT(*) AS n, SUM(s.amount_cents) AS cents
                FROM        splits s
                JOIN        transactions t ON t.id = s.transaction_id
                LEFT JOIN   mgmt_categories cat ON cat.id = s.mgmt_category_id
                LEFT JOIN   mgmt_categories p   ON p.id = cat.parent_id
                WHERE       COALESCE(cat.kind,'') <> 'transfer'
                  AND       substr(t.booking_date,1,4) = ?
                  AND       substr(t.booking_date,6,2) <= ?
                  AND       ({basis_sql})
                -- The expressions, not the aliases: mgmt_categories has its own
                -- parent_id column, so `GROUP BY parent_id` is ambiguous.
                GROUP BY    COALESCE(p.id, cat.id, '__none__'),
                            COALESCE(cat.id, '__none__')
                """, (y, last_month, *basis_args))}

        now, before = totals(year), totals(vs)
    finally:
        c.close()

    # One row per subcategory that appears in either year. A line that stopped
    # is as interesting as one that grew -- more so, if it was a subscription.
    rows = []
    for key in set(now) | set(before):
        a = now.get(key) or {}
        b = before.get(key) or {}
        meta = a or b
        rows.append({
            "parent_id": key[0], "parent": meta["parent_name"],
            "cat_id": key[1], "name": meta["cat_name"],
            "now": a.get("cents", 0), "before": b.get("cents", 0),
            "n": a.get("n", 0),
            "delta": a.get("cents", 0) - b.get("cents", 0),
        })

    groups: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["now"]):
        g = groups.setdefault(r["parent_id"], {
            "name": r["parent"], "rows": [], "now": 0, "before": 0})
        g["rows"].append(r)
        g["now"] += r["now"]
        g["before"] += r["before"]
    for g in groups.values():
        g["delta"] = g["now"] - g["before"]
    ordered = dict(sorted(groups.items(), key=lambda kv: kv[1]["now"]))

    # What actually moved. Costs only -- an extra 3.000 of rent received is not
    # something to optimise, and mixing directions makes the list unreadable.
    movers = sorted(
        [r for r in rows if r["before"] < 0 or r["now"] < 0],
        key=lambda r: r["delta"])
    return {
        "groups": ordered, "year": year, "vs": vs, "years": years,
        "last_month": last_month, "basis": basis,
        "worse": [r for r in movers if r["delta"] < -1000][:12],
        "better": [r for r in reversed(movers) if r["delta"] > 1000][:12],
    }


@router.get("/steuer", response_class=HTMLResponse)
def steuer(request: Request, year: str = "", anlage: str = ""):
    """The splits a tax return is actually filled in from.

    This is the one view organised by the TAX axis rather than the management
    tree. Anlage V is per property, Anlage N is per Werbungskosten position,
    and neither maps onto "where did the money go" -- which is exactly why the
    two axes were kept independent.

    Every figure drills down to the splits behind it, because a number you
    cannot trace is a number you cannot defend to a Finanzamt.
    """
    from datetime import date as _d

    year = year or str(_d.today().year - 1)   # the year you are filing for
    c = conn()
    try:
        where = ["substr(t.booking_date,1,4) = ?", "s.tax_category_id IS NOT NULL"]
        args: list = [year]
        if anlage:
            where.append("COALESCE(parent.anlage,'privat') = ?")
            args.append(anlage)

        rows = [dict(r) for r in c.execute(f"""
            SELECT parent.id   AS anlage_id,
                   parent.name AS anlage_name,
                   COALESCE(parent.anlage, 'privat') AS anlage_code,
                   tax.id      AS pos_id,
                   tax.name    AS pos_name,
                   tax.deductible, tax.requires_property,
                   s.property_id, p.name AS property_name,
                   COUNT(*)            AS n,
                   SUM(s.amount_cents) AS cents
            FROM        splits s
            JOIN        transactions t   ON t.id = s.transaction_id
            JOIN        tax_categories tax    ON tax.id = s.tax_category_id
            JOIN        tax_categories parent ON parent.id = tax.parent_id
            LEFT JOIN   properties p     ON p.id = s.property_id
            WHERE       {' AND '.join(where)}
            GROUP BY    tax.id, s.property_id
            ORDER BY    parent.sort_order, tax.sort_order, s.property_id
        """, args)]

        # Grouped the way the forms are: one block per Anlage, and inside
        # Anlage V one column per object, because that is one form each.
        groups: dict[str, dict] = {}
        for row in rows:
            g = groups.setdefault(row["anlage_id"], {
                "name": row["anlage_name"], "code": row["anlage_code"],
                "rows": [], "cents": 0, "deductible_cents": 0})
            g["rows"].append(row)
            g["cents"] += row["cents"]
            if row["deductible"]:
                g["deductible_cents"] += row["cents"]

        years = [r["y"] for r in c.execute(
            "SELECT DISTINCT substr(booking_date,1,4) AS y FROM transactions ORDER BY y DESC")]
        anlagen = [r["a"] for r in c.execute(
            "SELECT DISTINCT COALESCE(anlage,'privat') AS a FROM tax_categories "
            "WHERE parent_id IS NULL ORDER BY sort_order")]

        # What is NOT on a form yet. A split with a management category but no
        # tax position is invisible to every export, and silently so.
        untaxed = c.execute("""
            SELECT COUNT(*) AS n, COALESCE(SUM(s.amount_cents),0) AS cents
            FROM   splits s JOIN transactions t ON t.id = s.transaction_id
            LEFT JOIN mgmt_categories m ON m.id = s.mgmt_category_id
            WHERE  substr(t.booking_date,1,4) = ?
              AND  s.tax_category_id IS NULL
              AND  COALESCE(m.kind,'') <> 'transfer'
        """, (year,)).fetchone()
    finally:
        c.close()
    return TEMPLATES.TemplateResponse(request, "steuer.html", {
        "groups": groups, "year": year, "years": years,
        "anlage": anlage, "anlagen": anlagen, "untaxed": dict(untaxed),
    })


def _kosten(year: str, alle: bool, konto: str) -> dict:
    """Everything flagged as a fixed cost, wherever it sits in the tree.

    It used to be "Versicherung and Abo", which is how the Excel tracked it and
    which quietly omitted KFZ-Steuer and KFZ-Versicherung -- those are fixed
    costs, but they are also mobility costs, and moving them into Versicherung
    to make this page complete would have emptied them out of Mobilität. The
    fixkosten flag lets both be true at once.
    """
    from datetime import date as _d

    # The year filter matters more here than on most pages: a fixed cost that
    # ended in 2025 keeps showing up in an all-time view and inflates the
    # monthly obligation with something no longer being paid.
    # Dieselbe Rechnung, zweimal: einmal auf die Fixkosten gefiltert, einmal
    # ueber alles. Eine zweite Rechnung waere eine zweite Stelle, an der
    # Gruppierung, Monatsteiler und Jahresfilter gepflegt werden muessten --
    # und die Faelle, in denen sie auseinanderlaufen, sind genau die, in denen
    # die beiden Reiter sich widersprechen.
    #
    # Der Wert der Vollansicht ist ein anderer als der der gefilterten: hier
    # faellt auf, was FALSCH als Fixkosten markiert ist, weil man dieselbe
    # Position in beiden Listen sieht.
    # Transfers bleiben auch in der Vollansicht draussen. Sie sind weder
    # Einnahme noch Ausgabe, nur Geld, das die Seite wechselt -- und mit
    # 83.982 im Jahr wuerden sie jede Sortierung anfuehren und die Kopfzahl
    # bedeutungslos machen. Dieselbe Konvention wie in der Abstimmzeile.
    where = ("cat.parent_id <> 'transfer'" if alle else "cat.fixkosten = 1")
    # Jahr und Konto schraenken ZAEHLER UND NENNER ein. Ein Konto, das erst
    # seit Juni bucht, hat keine zwoelf Monate; durch zwoelf geteilt saehe
    # seine Belastung halb so gross aus wie sie ist. Deshalb steht der Filter
    # einmal da und wird zweimal eingesetzt -- mit Praefix fuer den Verbund,
    # ohne fuer die Monatszaehlung auf `transactions` allein.
    umfeld, args = "", []
    if year and year != "all":
        umfeld += " AND substr({p}booking_date,1,4) = ?"
        args.append(year)
    if konto and konto != "all":
        umfeld += " AND {p}account_id = ?"
        args.append(konto)
    where += umfeld.format(p="t.")
    # Der laufende Monat ist kein Monat. Am 21. September standen drei Wochen
    # September in der Summe, geteilt wurde aber durch neun -- jede Ø-Zeile
    # des Jahres fiel dadurch um ein Neuntel zu niedrig aus, und zwar umso
    # mehr, je frueher im Monat man die Seite aufschlug. Gerechnet wird
    # deshalb nur ueber abgeschlossene Monate, Zaehler wie Nenner: die Spalte
    # Summe zeigt das ganze Jahr, der Schnitt nur den Teil bis zum Ersten.
    grenze = _d.today().replace(day=1).isoformat()

    c = conn()
    try:
        years = [r[0] for r in c.execute(
            "SELECT DISTINCT substr(booking_date,1,4) FROM transactions "
            "ORDER BY 1")]
        konten = [r[0] for r in c.execute(
            "SELECT DISTINCT account_id FROM transactions ORDER BY 1")]
        rows = [dict(r) for r in c.execute(f"""
            SELECT parent.id AS parent_id, parent.name AS parent,
                   cat.name AS sub, cat.id AS cat_id,
                   COUNT(*) AS n, SUM(s.amount_cents) AS cents,
                   SUM(CASE WHEN t.booking_date < ? THEN s.amount_cents
                            ELSE 0 END) AS voll,
                   MIN(t.booking_date) AS first, MAX(t.booking_date) AS last
            FROM        splits s
            JOIN        transactions t ON t.id = s.transaction_id
            JOIN        mgmt_categories cat ON cat.id = s.mgmt_category_id
            JOIN        mgmt_categories parent ON parent.id = cat.parent_id
            WHERE       {where}
            GROUP BY    cat.id ORDER BY SUM(s.amount_cents)
        """, [grenze, *args])]
        groups: dict[str, dict] = {}
        for row in rows:
            bucket = groups.setdefault(row["parent_id"],
                                       {"name": row["parent"], "rows": [],
                                        "cents": 0, "voll": 0})
            bucket["rows"].append(row)
            bucket["cents"] += row["cents"]
            bucket["voll"] += row["voll"]
        # Heaviest block first: the question this page answers is what the
        # monthly obligation is, and a loan outweighs every subscription.
        groups = dict(sorted(groups.items(), key=lambda kv: kv[1]["cents"]))

        counterparties = [dict(r) for r in c.execute(f"""
            SELECT t.counterparty AS cp, s.mgmt_category_id AS cat,
                   COUNT(*) AS n, SUM(s.amount_cents) AS cents,
                   MAX(t.booking_date) AS last
            FROM        splits s JOIN transactions t ON t.id = s.transaction_id
            JOIN        mgmt_categories cat ON cat.id = s.mgmt_category_id
            WHERE       {where}
            GROUP BY    t.counterparty_norm, s.mgmt_category_id
            ORDER BY    SUM(s.amount_cents)
        """, args)]
        # Divide by the months actually in view, not by every month on record:
        # a 2024 filter over a 33-month ledger would report a third of the
        # real monthly obligation.
        def _monate(bis: str) -> int:
            return c.execute(
                "SELECT COUNT(DISTINCT substr(booking_date,1,7)) FROM "
                "transactions WHERE booking_date < ?" + umfeld.format(p=""),
                [bis, *args]).fetchone()[0]

        months, laufend = _monate(grenze), False
        if not months:
            # Ein Jahr ohne abgeschlossenen Monat -- der Januar eines neuen
            # Jahres. Lieber der angefangene Monat als eine leere Spalte; die
            # Ueberschrift sagt dann, dass er angefangen ist.
            months, laufend = _monate("9999-12-31") or 1, True
            for row in rows:
                row["voll"] = row["cents"]
            for g in groups.values():
                g["voll"] = g["cents"]
    finally:
        c.close()
    # In der gefilterten Ansicht ist jede Position eine Ausgabe, die Summe
    # also eine Kostenzahl. In der Vollansicht stehen Einnahmen daneben, und
    # dieselbe Summe ist ein SALDO -- das muss die Ueberschrift sagen, sonst
    # liest sich ein Ueberschuss von 878 wie monatliche Kosten von 878.
    total = sum(g["cents"] for g in groups.values())
    return {
        "groups": groups, "counterparties": counterparties,
        "total": total, "months": months, "year": year, "years": years,
        "alle": alle, "laufend": laufend,
        "konto": konto, "konten": konten,
        "monthly": sum(g["voll"] for g in groups.values()) // months,
    }


@router.get("/monatsabschluss", response_class=HTMLResponse)
def monatsabschluss(request: Request):
    """Was am Monatsanfang zu tun ist, in der Reihenfolge, in der es Sinn ergibt.

    Der Ablauf stand bisher nur im Kopf des Eigentuemers: Review, Planungen
    validieren, Kontenvorschau, Fortschritt. Vier Seiten, die man sich merken
    musste -- und davor die Frage, welche Staende ueberhaupt noch aktuell
    sind, die bisher nirgends stand.
    """
    from datetime import date as _d

    import yaml as _y

    from finctl import monatsabschluss as _ma

    heute = _d.today()
    periode, jahr = f"{heute:%Y-%m}", f"{heute:%Y}"

    from finctl import bestaende as _best

    bal = _y.safe_load((CONFIG_DIR / "balances.yaml").read_text(encoding="utf-8")) or {}
    c = conn()
    try:
        balances = _best.zusammenfuehren(c, bal)["balances"]
        geparst = {r[0] for r in c.execute(
            "SELECT id FROM accounts WHERE ingest_mode = 'parsed'")}
        posten = (_ma.konten(c, heute, periode, balances)
                  + _ma.bestaende(balances, heute, periode, jahr, geparst))
        # Renten rechnen nur in der Prognose; ohne sie gibt es nichts zu pflegen.
        from finctl import module as _mod

        if _mod.seite_an("/hochrechnung"):
            posten += _ma.renten(heute, jahr)
        auszug = _best.auszugsstaende(c, sorted(geparst))
        posten += _ma.planung_offen(c, heute, periode)
        # Eine kaputte abos.yaml soll den Monatsabschluss nicht mitreissen;
        # /abos zeigt den Fehler an der Stelle, wo er zu beheben ist.
        with contextlib.suppress(ValueError):
            posten += _ma.abos_offen(c, heute, periode)
        from finctl import ops as _ops
        from finctl.forecast import ohne_objekt as _ob

        try:
            views = _ops.household_accounts(c, months=12)
        except Exception:
            views = []
        schritte = _ma.schritte(c, views=views)
        fortschritt = _fortschritt_zeile(c)
        kacheln = _kacheln(c, jahr)
        _fpk = CONFIG_DIR / "forecast.yaml"
        ohne_objekt = _ob.kennzahl(c, views, (_y.safe_load(
            _fpk.read_text(encoding="utf-8")) or {}) if _fpk.exists() else {})
    finally:
        c.close()
    schritte.append(fortschritt)
    # Aendern laesst sich ein Stand nur mit der Prognose: sie ist es, die mit
    # ihnen rechnet. Ohne sie bleibt die Pruefung, ob er alt ist.
    from finctl import module as _module

    bestand = None
    if _module.seite_an("/bestaende"):
        from finctl.web.routen.konten import bestaende_daten

        bestand = bestaende_daten()

    gruppen = [
        ("abos", "Geteilte Abos",
         ("Erscheint ab dem Einsammel-Monat und verschwindet, sobald die "
          "Rückzahlungen im Import stehen.")),
        ("planung", "Planung",
         ("Begonnene Planzeilen mit passenden Buchungen, aber ohne Zuordnung. "
          "Einmal zuordnen, dann zählt genau diese Reihe.")),
    ]
    return TEMPLATES.TemplateResponse(request, "monatsabschluss.html", {
        "posten": posten, "schritte": schritte, "gruppen": gruppen,
        "staende": _staende(posten, bestand, auszug, jahr, _MONATE[heute.month],
                            _MONATE[12 if heute.month == 1 else heute.month - 1]),
        "kacheln": kacheln, "ohne_objekt": ohne_objekt, "bestand": bestand,
        "periode": periode, "jahr": jahr, "heute": heute,
        "monat_name": _MONATE[heute.month],
        "offen": sum(1 for p in posten if not p.erledigt),
        # Die erste Seite, die ein neuer Nutzer sieht. Sie sagt, wenn die
        # Einrichtung noch nicht durch ist -- statt einer Umleitung, die beim
        # zweiten Mal im Weg steht.
        "einrichtung_offen": _einrichtung.offen(),
        "gesamt": len(posten),
    })


_STAENDE_TEXT = {
    "konten": "Aus dem Auszug — aktuell, wenn er den {vormonat} abdeckt.",
    "vermoegen": "Getippt — aktuell, wenn der Stand aus dem {monat} ist.",
    "jaehrlich": "Getippt nach der Standmitteilung — aktuell, wenn sie aus {jahr} ist.",
    "renten": "Laut Renteninformation und Standmitteilung — aktuell, wenn aus {jahr}.",
}


def _staende(posten: list, bestand: dict | None, auszug: dict, jahr: str,
             monat: str, vormonat: str) -> list[dict]:
    """Konten, Depots, Renten: je Position EINE Zeile mit Haken, Wert und Notiz.

    Bis zum 26.09.2026 stand jede Position zweimal auf der Seite -- oben mit
    ihrem Beleg in der Checkliste, unten mit Wert und Notiz in den
    Bestaenden, jedes Konto dazu ein drittes Mal als Kachel. Gepflegt wird
    ein Stand dort, wo man bemerkt, dass er alt ist.
    """
    from finctl.forecast.ziele import art as _art

    zu = {r["key"]: r for r in (bestand or {}).get("rows", [])}
    # Ein Stichtag fuer alle Konten: das letzte Monatsende, das alle abdecken.
    stichtag = next((b['as_of'] for b in auszug.values()
                     if b["as_of"]), "")
    gruppen = []
    for gid in ("konten", "vermoegen", "jaehrlich", "renten"):
        zeilen = []
        if gid == "renten":
            zeilen = _rentenzeilen(posten)
        for p in (p for p in posten if p.gruppe == gid and gid != "renten"):
            key = p.schluessel or p.id.split(":", 1)[1]
            b = zu.get(key) or {}
            beleg = auszug.get(key) if gid == "konten" else None
            zeilen.append({
                "p": p, "key": key, "kind": _art(p.art),
                "cents": beleg["cents"] if beleg else p.cents,
                "as_of": str(p.stand or "") if not beleg else "",
                "auszug": beleg is not None,
                # Nur was in balances.yaml steht, hat eine Notiz: nur das
                # zaehlt zum Vermoegen.
                "notiz": key in zu, "note": b.get("note", ""),
                "eigen": bool(b.get("overridden") or b.get("note_eigen")),
                "base_cents": b.get("base_cents"), "base_note": b.get("base_note", ""),
            })
        if not zeilen:
            continue
        # Monatsrenten und Kapital zu addieren ergaebe keine Zahl.
        werte = [z["cents"] for z in zeilen if z["cents"] is not None
                 and gid != "renten"]
        gruppen.append({"id": gid, "titel": _staende_titel(gid, zeilen, jahr),
                        "erklaerung": _STAENDE_TEXT[gid].format(
                            vormonat=vormonat, monat=monat, jahr=jahr),
                        "zeilen": zeilen, "summe": sum(werte),
                        "ohne_wert": sum(1 for z in zeilen if z["cents"] is None),
                        "erledigt": sum(1 for z in zeilen if z["p"].erledigt),
                        "stichtag": stichtag})
    return gruppen


def _rentenzeilen(posten: list) -> list[dict]:
    """Die Rentenquellen als Zeilen derselben Tabelle -- gespeichert wird
    ueber /api/rente nach renten_custom.yaml."""
    from finctl import person as _person
    from finctl import renten as _renten

    je = {q.id: q for q in _renten.quellen()}
    rentenbeginn = _person.rentenbeginn()
    zeilen = []
    for p in (p for p in posten if p.gruppe == "renten"):
        q = je[p.schluessel]
        zeilen.append({"p": p, "key": q.id, "kind": q.art, "cents": q.cents,
                       "as_of": q.stand.isoformat() if q.stand else "",
                       "auszug": False, "notiz": True, "note": q.notiz or q.herleitung,
                       "eigen": q.eigen, "base_cents": q.basis_cents,
                       "base_note": q.herleitung, "api": "/api/rente",
                       "ab": q.ab.strftime("%Y-%m") if q.ab else "",
                       "ab_vorgabe": rentenbeginn.strftime("%Y-%m") if rentenbeginn else ""})
    return zeilen


def _staende_titel(gid: str, zeilen: list[dict], jahr: str) -> str:
    if gid == "konten":
        return "Konten"
    if gid == "renten":
        return f"Renten laut Mitteilung · {jahr}"
    if gid == "jaehrlich":
        return f"Rentenversicherungen · {jahr}"
    # Ein Konto ohne Parser wird getippt wie ein Depot und steht deshalb hier.
    return "Depots" if all(z["kind"] == "depot" for z in zeilen) \
        else "Depots und Konten ohne Auszug"


_MONATE = ("", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember")


def _fortschritt_zeile(c: sqlite3.Connection) -> dict:
    """Barista FIRE heute und hochgerechnet, als eine Zeile.

    Beide Zahlen, weil eine allein nichts sagt: der Bestand beantwortet nicht,
    ob man ankommt, und die Hochrechnung allein laesst ein kaum begonnenes
    Depot fuer halb fertig halten.
    """
    from datetime import date as _d

    import yaml as _y

    from finctl.forecast import jahre as _jm
    from finctl.forecast import ziele as _ziele

    def read(name):
        p = CONFIG_DIR / name
        return (_y.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}

    goals = _ziele.merge_edits(read("goals.yaml"), read("ziele_custom.yaml"))
    from finctl import bestaende as _best

    merged = _best.zusammenfuehren(
        c, read("balances.yaml"), read("balances_custom.yaml").get("overrides"))

    ziel = next((p for p in _ziele.progress(goals, merged, today=_d.today())
                 if p.goal_id == "barista-fire"), None)
    if ziel is None:
        return {"id": "ziele", "titel": "Fortschritt", "ziel": "/ziele",
                "text": "kein Barista-FIRE-Ziel eingetragen", "cents": None,
                "warnung": False}
    jahr = int(ziel.due.year) if ziel.due else 2045
    lauf = _jm.project(c, opening_cents=_ziele.liquid_cents(merged),
                       toepfe=_ziele.toepfe(merged),
                       puffer_cents=_ziele.puffer_cents(goals), end_year=max(2045, jahr),
                       policen_je_konto=_ziele.policen_je_konto(merged))
    kapital = lauf.year(min(jahr, lauf.years[-1].year)).frei_cents
    pct = 100.0 * kapital / ziel.target_cents if ziel.target_cents else 0.0
    return {"id": "ziele", "titel": "Fortschritt", "ziel": "/ziele",
            "text": (f"Barista FIRE heute {ziel.pct:.1f} %, "
                     f"hochgerechnet {jahr} {pct:.0f} %").replace(".", ","),
            "cents": None, "warnung": pct < 100}
