"""Die Buchungen selbst: Warteschlange, Liste, Einzelansicht, Aufteilung.

Die zwei Ansichten, die diese Oberflaeche ueberhaupt rechtfertigen, stehen
hier -- hundert Buchungen zuzuordnen oder eine PayPal-Belastung zu zerlegen
ist am Terminal muehsam und im Browser angenehm.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl.ledger import db as ledger
from finctl.web.basis import (
    TEMPLATES,
    _summe,
    adresse,
    categories,
    conn,
    euro,
    tax_categories,
    umleiten,
)

router = APIRouter()


# Die Warteschlange und die Liste des Aufzuteilenden sind Ansichten von
# /transactions: dieselbe Tabelle, dieselben Knoepfe, nur ein anderer Filter.
# Zwei eigene Seiten dafuer hiessen zwei Stellen, an denen Speichern, Objekt und
# Herkunftsmarke gepflegt werden muessen -- und sie liefen auseinander.
ANSICHTEN = {
    "offen": ("Offen", "t.id IN (SELECT id FROM v_review_queue)"),
    "aufteilen": ("Aufzuteilen", """EXISTS (
        SELECT 1 FROM splits f WHERE f.transaction_id = t.id AND f.source = 'default'
          AND f.rule_id IS NOT NULL AND f.mgmt_category_id IS NULL)"""),
}


@router.get("/review")
def review(request: Request):
    return umleiten(request, "/transactions", ansicht="offen")


@router.post("/api/categorize/{transaction_id}")
def api_categorize(transaction_id: int, mgmt: str = Form(""),
                   tax: str = Form(""),
                   # Der Feldname auf dem Draht bleibt "property" -- die Seiten
                   # schicken ihn so. Im Python heisst er anders, weil
                   # `property` dort eine eingebaute Funktion ist.
                   immobilie: str = Form("", alias="property")):
    """Assign a category by hand. Written as 'manual', so it is sticky."""
    c = conn()
    try:
        tx = c.execute("SELECT amount_cents FROM transactions WHERE id = ?",
                       (transaction_id,)).fetchone()
        if tx is None:
            return JSONResponse({"error": "no such transaction"}, status_code=404)
        if mgmt and not c.execute(
                "SELECT 1 FROM mgmt_categories WHERE id = ?", (mgmt,)).fetchone():
            return JSONResponse({"error": f"unknown category: {mgmt}"}, status_code=400)
        if not mgmt and not immobilie:
            return JSONResponse({"error": "nothing to change"}, status_code=400)

        # A property-only edit keeps whatever category is already there, rather
        # than blanking it -- otherwise correcting the object would silently
        # un-categorize the transaction.
        existing = c.execute(
            "SELECT mgmt_category_id, tax_category_id, property_id FROM splits "
            "WHERE transaction_id = ? ORDER BY seq LIMIT 1", (transaction_id,)).fetchone()
        if not mgmt and existing:
            mgmt = existing["mgmt_category_id"]
        if not tax and existing:
            tax = existing["tax_category_id"]

        # A category can imply its tax position. Choosing "Parken beruflich"
        # should not also require remembering "Anlage N / Fahrtkosten" -- that
        # is exactly how a deduction gets lost.
        if mgmt:
            implied = c.execute(
                "SELECT default_tax_id FROM mgmt_categories WHERE id = ?",
                (mgmt,)).fetchone()
            if implied and implied["default_tax_id"] and (
                    not tax or (existing and tax == existing["tax_category_id"]
                                and existing["mgmt_category_id"] != mgmt)):
                tax = implied["default_tax_id"]
        if not immobilie and existing:
            immobilie = existing["property_id"]

        from finctl.rules.categorize import capture_prior
        prior = capture_prior(c, transaction_id)
        stamp = ledger.now_iso()
        c.execute("DELETE FROM splits WHERE transaction_id = ?", (transaction_id,))
        c.execute(
            """
            INSERT INTO splits (transaction_id, seq, amount_cents, mgmt_category_id,
                                tax_category_id, property_id, source,
                                created_at, updated_at)
            VALUES (?,0,?,?,?,?, 'manual', ?, ?)
            """,
            (transaction_id, tx["amount_cents"], mgmt, tax or None,
             immobilie or None, stamp, stamp),
        )
        c.commit()
        # Persist outside the database so the decision survives a rebuild.
        from finctl.rules.categorize import record_override
        record_override(c, transaction_id, prior=prior)
        remaining = c.execute("SELECT COUNT(*) FROM v_review_queue").fetchone()[0]
        # Was jetzt wirklich dasteht, zurueck an die Seite. Sie soll die
        # Herkunftsmarke nicht raten muessen -- und bisher blieb dort "rule ·
        # w82a-hausgeld" stehen, obwohl die Zeile in dem Moment manuell war.
        neu = c.execute(
            "SELECT s.source, s.mgmt_category_id, m.name, p.name AS eltern "
            "FROM splits s LEFT JOIN mgmt_categories m ON m.id = s.mgmt_category_id "
            "LEFT JOIN mgmt_categories p ON p.id = m.parent_id "
            "WHERE s.transaction_id = ? AND s.seq = 0", (transaction_id,)).fetchone()
    finally:
        c.close()
    return {"ok": True, "remaining": remaining,
            "source": (neu["source"] if neu else "manual"),
            "kategorie": (neu["mgmt_category_id"] if neu else None),
            "label": (f"{neu['eltern']} / {neu['name']}"
                      if neu and neu["name"] and neu["eltern"] else None)}


@router.get("/tx/{transaction_id}", response_class=HTMLResponse)
def transaction(request: Request, transaction_id: int):
    c = conn()
    try:
        tx = c.execute(
            "SELECT id, account_id, booking_date, amount_cents, counterparty, "
            "purpose, raw_text FROM transactions WHERE id = ?",
            (transaction_id,)).fetchone()
        if tx is None:
            return HTMLResponse("<h1>not found</h1>", status_code=404)
        parts = [dict(r) for r in c.execute(
            "SELECT seq, amount_cents, mgmt_category_id, tax_category_id, property_id, "
            "note, source FROM splits WHERE transaction_id = ? ORDER BY seq",
            (transaction_id,))]
        cats, taxes = categories(c), tax_categories(c)
        props = [dict(r) for r in c.execute("SELECT id, name FROM properties ORDER BY id")]
    finally:
        c.close()
    return TEMPLATES.TemplateResponse(request, "split.html", {
        "tx": dict(tx), "parts": parts,
        "categories": cats, "tax_categories": taxes, "properties": props,
    })


@router.post("/api/split/{transaction_id}")
async def api_split(transaction_id: int, request: Request):
    """Replace a transaction's splits with a manual breakdown.

    Refuses anything that does not sum to the transaction: every report reads
    splits, so an unbalanced breakdown corrupts totals silently.
    """
    body = await request.json()
    parts = body.get("parts", [])
    c = conn()
    try:
        tx = c.execute("SELECT amount_cents FROM transactions WHERE id = ?",
                       (transaction_id,)).fetchone()
        if tx is None:
            return JSONResponse({"error": "no such transaction"}, status_code=404)

        total = sum(int(p["amount_cents"]) for p in parts)
        if total != tx["amount_cents"]:
            return JSONResponse({
                "error": (f"parts total {euro(total)} but the transaction is "
                          f"{euro(tx['amount_cents'])} "
                          f"(off by {euro(total - tx['amount_cents'])})")
            }, status_code=400)
        if not parts:
            return JSONResponse({"error": "at least one part is required"},
                                status_code=400)

        # Validate before writing. Without this an unknown category surfaces as
        # a 500 from a foreign-key violation, which tells the caller nothing --
        # and it happens naturally whenever a category is renamed.
        known_mgmt = {r["id"] for r in c.execute("SELECT id FROM mgmt_categories")}
        known_tax = {r["id"] for r in c.execute("SELECT id FROM tax_categories")}
        known_prop = {r["id"] for r in c.execute("SELECT id FROM properties")}
        for p in parts:
            for value, known, what in ((p.get("mgmt"), known_mgmt, "category"),
                                       (p.get("tax"), known_tax, "tax category"),
                                       (p.get("property"), known_prop, "property")):
                if value and value not in known:
                    return JSONResponse(
                        {"error": f"unknown {what}: {value}"}, status_code=400)

        stamp = ledger.now_iso()
        c.execute("DELETE FROM splits WHERE transaction_id = ?", (transaction_id,))
        for seq, p in enumerate(parts):
            c.execute(
                """
                INSERT INTO splits (transaction_id, seq, amount_cents,
                                    mgmt_category_id, tax_category_id, property_id,
                                    note, source, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?, 'manual', ?, ?)
                """,
                (transaction_id, seq, int(p["amount_cents"]),
                 p.get("mgmt") or None, p.get("tax") or None,
                 p.get("property") or None, p.get("note") or None, stamp, stamp),
            )
        c.commit()
        from finctl.rules.categorize import record_override
        record_override(c, transaction_id)
    finally:
        c.close()
    return {"ok": True, "parts": len(parts)}


@router.get("/flagged")
def flagged(request: Request):
    return umleiten(request, "/transactions", ansicht="aufteilen")


@router.get("/api/buchungen/suche")
def api_buchungen_suche(q: str = "", kategorie: str = "", limit: int = 60):
    """Buchungen zum Zuordnen: Ein- und Ausgaenge, nach Text und/oder Kategorie.

    Beides ist optional, aber eines muss da sein -- eine Suche ohne jede
    Einschraenkung waere das ganze Ledger.
    """
    q, kategorie = q.strip(), kategorie.strip()
    if len(q) < 2 and not kategorie:
        return {"rows": []}
    from finctl.ingest.base import normalize_counterparty

    bedingung, args = [], []
    if len(q) >= 2:
        bedingung.append("(t.raw_text LIKE ? OR t.counterparty_norm LIKE ?)")
        args += [f"%{q}%", f"%{normalize_counterparty(q) or q}%"]
    if kategorie:
        bedingung.append("s.mgmt_category_id = ?")
        args.append(kategorie)
    c = conn()
    try:
        rows = c.execute(
            f"""SELECT t.dedup_hash, t.booking_date, t.account_id, s.amount_cents,
                       substr(t.raw_text, 1, 80) AS text, s.mgmt_category_id AS kategorie
                FROM   transactions t JOIN splits s ON s.transaction_id = t.id
                WHERE  {' AND '.join(bedingung)}
                ORDER  BY t.booking_date DESC LIMIT ?""",
            [*args, max(1, min(int(limit), 200))]).fetchall()
    finally:
        c.close()
    return {"rows": [dict(r) for r in rows]}


def _baumfilter(werte: list[str], spalte: str) -> tuple[str, list[str]]:
    """Mehrere Kategorien oder Steuerpositionen, verodert.

    `__none__` sucht, was gar keine traegt. Ein Blatt trifft genau sich selbst,
    nicht per Praefix: "einkommen/gehalt" als Praefix zoege
    "einkommen/gehalt-nebentaetig" mit, ein anderes Einkommen mit anderer
    Steuer. Ein Ast trifft sich und alles darunter.
    """
    klauseln, args = [], []
    for wert in werte:
        if wert == "__none__":
            klauseln.append(f"{spalte} IS NULL")
        elif "/" in wert:
            klauseln.append(f"{spalte} = ?")
            args.append(wert)
        else:
            klauseln.append(f"({spalte} = ? OR {spalte} LIKE ?)")
            args += [wert, wert + "/%"]
    return "(" + " OR ".join(klauseln) + ")", args


@router.get("/transactions", response_class=HTMLResponse)
def transactions(request: Request, account: str = "", start: str = "", end: str = "",
                 category: list[str] = Query(default=[]), tax: list[str] = Query(default=[]),
                 source: str = "", q: str = "",
                 block: list[str] = Query(default=[]), ansicht: str = "",
                 limit: int = 200):
    """Browse and correct every transaction, however it was labelled.

    The review queue only surfaces what is UNlabelled. A wrong label -- from a
    rule that matched too broadly, or a mistake made by hand -- is invisible
    there, and those are exactly the ones worth finding.
    """
    where, args = ["1=1"], []
    # Ein BLOCK der Abstimmzeile, mit deren eigener Definition gefiltert.
    #
    # Nicht mit einer Kategorieliste nachgebaut: die Bloecke sind geordnet und
    # der erste Treffer gewinnt -- `kredit/tilgung` traegt das fixkosten-Flag
    # und gehoert trotzdem zu den Kreditraten, nicht zu den Fixkosten. Eine
    # zweite Definition wuerde genau dort abweichen, wo es darauf ankommt, und
    # der Link zeigte andere Zeilen als die Zahl, auf die man geklickt hat.
    #
    # Mehrere Bloecke sind ODER verknuepft, wie mehrere Kategorien: "übrige"
    # im Fluss der Hochrechnung fasst mehrere Bloecke zusammen und fuehrt zu
    # allen ihren Buchungen.
    from finctl.forecast import abgleich as _ag

    bekannt = {b.id for b in _ag.bloecke()}
    bloecke = [b for b in block if b in bekannt]
    if bloecke:
        # EXISTS ueber ALLE Teile, nicht der seq-0-Join des uebrigen
        # Filters -- dieselbe Begruendung wie beim Steuerfilter darunter.
        # Eine Annuitaet sind zwei Splits, Zinsen und Tilgung; auf seq 0
        # gefiltert faende man den einen und verloere den anderen, und die
        # Kreditzeile zeigte 7.651 statt der 13.727, auf die man geklickt
        # hat.
        where.append(f"""EXISTS (
            SELECT 1 FROM splits s2
            LEFT JOIN mgmt_categories m ON m.id = s2.mgmt_category_id
            WHERE s2.transaction_id = t.id
              AND COALESCE(s2.mgmt_category_id, '') NOT LIKE 'transfer/%'
              AND ({_ag._case_expression().replace('s.', 's2.')})
                  IN ({', '.join('?' * len(bloecke))}))""")
        args += bloecke
    # Die schlichten Gleichheitsfilter stehen als Tabelle beieinander, weil
    # sie sich nur in Spalte und Wert unterscheiden. Einzeln ausgeschrieben
    # waeren es dreimal dieselben drei Zeilen.
    for bedingung, wert in (("t.account_id = ?", account),
                            ("t.booking_date >= ?", start),
                            ("t.booking_date <= ?", end)):
        if wert:
            where.append(bedingung)
            args.append(wert)
    # Several categories are ORed together: "what did Lebensmittel, Gastronomie
    # and Konsum cost me" is one question, and answering it by running three
    # filters and adding up by hand is not an answer.
    chosen = [x for x in category if x]
    if chosen:
        klausel, cat_args = _baumfilter(chosen, "s.mgmt_category_id")
        where.append(klausel)
        args += cat_args

    # Same shape on the tax axis, so a figure on the Steuer page can hand you
    # the splits behind it. "__none__" finds what carries no tax position at
    # all, which is the set that silently misses every export.
    #
    # EXISTS over ALL parts, not the seq=0 join the rest of this query uses. A
    # loan payment is two splits with two different tax positions -- Zinsen on
    # Anlage V and Tilgung not deductible -- and filtering on seq 0 would find
    # the interest and hide the principal, or the reverse.
    chosen_tax = [x for x in tax if x]
    if chosen_tax:
        klausel, tax_args = _baumfilter(chosen_tax, "x.tax_category_id")
        where.append("EXISTS (SELECT 1 FROM splits x WHERE x.transaction_id = t.id "
                     f"AND {klausel})")
        args += tax_args
    for bedingung, wert in (("COALESCE(s.source,'') = ?", source),
                            ("t.raw_text LIKE ?", f"%{q}%" if q else "")):
        if wert:
            where.append(bedingung)
            args.append(wert)

    # Die Reiter zaehlen mit den uebrigen Filtern: wer ein Konto gewaehlt hat,
    # will wissen, wie viel auf DIESEM Konto offen ist.
    ohne_ansicht = list(where)
    ansicht = ansicht if ansicht in ANSICHTEN else ""
    where += [ANSICHTEN[ansicht][1]] if ansicht else []
    # Offenes nach Betrag: zuerst, was am meisten ausmacht. Alles andere nach
    # Datum, weil man dort eine Buchung sucht, die man kennt.
    ordnung = "ABS(t.amount_cents) DESC, t.id DESC" if ansicht else "t.booking_date DESC, t.id DESC"

    c = conn()
    try:
        zahlen = {k: c.execute(f"""
            SELECT COUNT(DISTINCT t.id) FROM transactions t
            LEFT JOIN splits s ON s.transaction_id = t.id AND s.seq = 0
            LEFT JOIN mgmt_categories m ON m.id = s.mgmt_category_id
            WHERE {' AND '.join([*ohne_ansicht, bed])}""", args).fetchone()[0]
            for k, (_, bed) in ANSICHTEN.items()}
        reiter = [{"id": "", "label": "Alle", "href": adresse(request, ansicht="")}] + [
            {"id": k, "label": name, "zahl": zahlen[k], "href": adresse(request, ansicht=k)}
            for k, (name, _) in ANSICHTEN.items()]
        rows = [dict(r) for r in c.execute(f"""
            SELECT t.id, t.booking_date, t.account_id, t.amount_cents, t.raw_text,
                   s.mgmt_category_id, s.property_id, s.source, s.rule_id, s.note,
                   (SELECT COUNT(*) FROM splits x WHERE x.transaction_id = t.id) AS n_parts
            FROM        transactions t
            LEFT JOIN   splits s ON s.transaction_id = t.id AND s.seq = 0
            -- Fuer den Blockfilter: dessen CASE liest das fixkosten-Flag.
            LEFT JOIN   mgmt_categories m ON m.id = s.mgmt_category_id
            WHERE       {' AND '.join(where)}
            ORDER BY    {ordnung}
            LIMIT ?
        """, (*args, limit))]
        # Summiert wird der TRANSAKTIONSbetrag, genau wie die Spalte "Betrag"
        # ihn zeigt -- ueber DISTINCT t.id, sonst zaehlt eine Transaktion mit
        # mehreren Teilen mehrfach. Bei einem Kategoriefilter ist das der volle
        # Betrag und nicht nur der passende Teil; dieselbe Unschaerfe hat die
        # Spalte schon, und eine zweite, anders gerechnete Zahl daneben waere
        # schlimmer als eine unscharfe.
        summe = _summe(c, f"""
            SELECT COUNT(*), COALESCE(SUM(a), 0),
                   COALESCE(SUM(CASE WHEN a < 0 THEN a END), 0),
                   COALESCE(SUM(CASE WHEN a > 0 THEN a END), 0)
            FROM (SELECT DISTINCT t.id AS tid, t.amount_cents AS a
                  FROM transactions t
                  LEFT JOIN splits s ON s.transaction_id = t.id AND s.seq = 0
                  LEFT JOIN mgmt_categories m ON m.id = s.mgmt_category_id
                  WHERE {' AND '.join(where)})""", args)
        total = summe["n"]
        cats = categories(c)
        props = [dict(r) for r in c.execute("SELECT id, name FROM properties ORDER BY id")]
        accounts = [r["id"] for r in c.execute(
            "SELECT id FROM accounts WHERE ingest_mode='parsed' ORDER BY id")]
        parents = [dict(r) for r in c.execute(
            "SELECT id, name FROM mgmt_categories WHERE parent_id IS NULL ORDER BY sort_order")]
        children: dict[str, list[dict]] = {}
        for r in c.execute(
                "SELECT id, parent_id, name FROM mgmt_categories "
                "WHERE parent_id IS NOT NULL AND active = 1 ORDER BY sort_order"):
            children.setdefault(r["parent_id"], []).append(dict(r))
        # Both levels in one searchable list: a parent filters its whole
        # branch, a leaf filters exactly itself.
        filterable = []
        for p in parents:
            filterable.append({"id": p["id"], "label": f"{p['name']} — alle"})
            for ch in children.get(p["id"], []):
                filterable.append({"id": ch["id"],
                                   "label": f"{p['name']} / {ch['name']}"})
        tax_filterable = [{"id": "__none__", "label": "— ohne Steuerposition —"}]
        tax_parents = [dict(r) for r in c.execute(
            "SELECT id, name, anlage FROM tax_categories WHERE parent_id IS NULL "
            "ORDER BY sort_order")]
        for tp in tax_parents:
            tax_filterable.append({"id": tp["id"], "label": f"{tp['name']} — alle"})
            for ch in c.execute(
                    "SELECT id, name FROM tax_categories WHERE parent_id = ? AND active = 1 "
                    "ORDER BY sort_order", (tp["id"],)):
                tax_filterable.append({"id": ch["id"],
                                       "label": f"{tp['name']} / {ch['name']}"})
    finally:
        c.close()

    from finctl.rules.categorize import rule_conflicts
    conflicts = rule_conflicts()
    # Read and write must agree: showing an id in one column while the control
    # beside it offers labels makes the obvious search fail.
    cat_label = {x["id"]: x["label"] for x in cats}
    return TEMPLATES.TemplateResponse(request, "transactions.html", {
        "rows": rows, "total": total, "summe": summe, "gezeigt": len(rows),
        "categories": cats, "properties": props,
        "accounts": accounts, "parents": parents, "children": children,
        "filterable": filterable, "tax_filterable": tax_filterable,
        "conflicts": conflicts, "cat_label": cat_label, "reiter": reiter,
        "f": {"account": account, "start": start, "end": end, "category": chosen,
              "tax": chosen_tax, "ansicht": ansicht,
              "source": source, "q": q},
    })


@router.post("/api/split-part/{split_id}")
async def api_split_part(split_id: int, request: Request):
    """Re-label a single split without touching the amounts.

    Distinct from /api/split, which replaces a whole breakdown: here the
    division of the money is already right and only its classification is
    wrong -- the common case on a property page.
    """
    body = await request.json()
    c = conn()
    try:
        row = c.execute("SELECT id, transaction_id FROM splits WHERE id = ?",
                        (split_id,)).fetchone()
        if row is None:
            return JSONResponse({"error": "no such split"}, status_code=404)
        for value, table, what in ((body.get("mgmt"), "mgmt_categories", "category"),
                                   (body.get("tax"), "tax_categories", "tax category")):
            if value and not c.execute(
                    f"SELECT 1 FROM {table} WHERE id = ?", (value,)).fetchone():
                return JSONResponse({"error": f"unknown {what}: {value}"},
                                    status_code=400)
        c.execute(
            "UPDATE splits SET mgmt_category_id = ?, tax_category_id = ?, "
            "source = 'manual', updated_at = ? WHERE id = ?",
            (body.get("mgmt") or None, body.get("tax") or None,
             ledger.now_iso(), split_id),
        )
        c.commit()
        # Persist it outside the database, like every other write path.
        #
        # This one did not, and marking a split 'manual' without recording it
        # means it survives categorize --recompute but NOT a rebuild from
        # statements. Every correction made on a property page was one
        # `rm data/finance.db` away from being gone -- the exact failure that
        # overrides.yaml exists to prevent, reintroduced by a later endpoint.
        from finctl.rules.categorize import record_override
        record_override(c, row["transaction_id"])
    finally:
        c.close()
    return {"ok": True}
