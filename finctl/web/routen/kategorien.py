"""Kategorien und das Umhaengen von Regeln darauf."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from finctl.web.basis import (
    TEMPLATES,
    conn,
)

router = APIRouter()


@router.get("/kategorien", response_class=HTMLResponse)
def kategorien(request: Request):
    c = conn()
    try:
        rows = [dict(r) for r in c.execute("""
            SELECT child.id, child.name, child.active,
                   parent.id AS parent_id, parent.name AS parent,
                   (SELECT COUNT(*) FROM splits s WHERE s.mgmt_category_id = child.id) AS n
            FROM      mgmt_categories child
            JOIN      mgmt_categories parent ON parent.id = child.parent_id
            ORDER BY  parent.sort_order, child.sort_order
        """)]
        parents = [dict(r) for r in c.execute(
            "SELECT id, name FROM mgmt_categories WHERE parent_id IS NULL "
            "AND active=1 ORDER BY sort_order")]
    finally:
        c.close()
    from finctl.tax.taxonomy import rules_referencing
    for r in rows:
        r["rules"] = rules_referencing(r["id"])
    return TEMPLATES.TemplateResponse(request, "kategorien.html",
                                      {"rows": rows, "parents": parents})


@router.post("/api/category")
async def api_category(request: Request):
    """Add, rename or retire a category.

    Retiring does two things that must happen together: move the splits, and
    warn about rules still naming the old id. Doing only the first is what bit
    us with Konsum/Haustier -- the splits migrated and the next categorize put
    them straight back.
    """
    import re as _re

    from finctl.tax import taxonomy as tx

    def apply_now(c):
        """Reload the taxonomy so the change is visible immediately.

        Writing the YAML alone left every edit invisible until the next
        `finctl init` -- which looked exactly like the rename silently failing,
        because from the page nothing happened.
        """
        tx.load_into_db(c)
        moved = tx.apply_migrations(c)
        tx.deactivate_missing(c)
        return sum(moved.values())

    body = await request.json()
    action = body.get("action")
    c = conn()
    try:
        if action == "add":
            parent, label = body.get("parent"), (body.get("name") or "").strip()
            if not parent or not label:
                return JSONResponse({"error": "parent and name required"}, status_code=400)
            leaf = _re.sub(r"[^a-z0-9]+", "-",
                           label.lower().replace("ä", "ae").replace("ö", "oe")
                                .replace("ü", "ue").replace("ß", "ss")).strip("-")
            if not leaf:
                return JSONResponse({"error": "name yields an empty id"}, status_code=400)
            full = f"{parent}/{leaf}"
            if c.execute("SELECT 1 FROM mgmt_categories WHERE id=?", (full,)).fetchone():
                return JSONResponse({"error": f"{full} already exists"}, status_code=400)
            tx.save_custom("management", parent, leaf, label)
            apply_now(c)
            return {"ok": True, "id": full}

        if action == "rename":
            cid, label = body.get("id"), (body.get("name") or "").strip()
            if not cid or not label:
                return JSONResponse({"error": "id and name required"}, status_code=400)
            parent, leaf = cid.split("/", 1)
            tx.save_custom("management", parent, leaf, label)
            apply_now(c)
            new = c.execute("SELECT name FROM mgmt_categories WHERE id = ?",
                            (cid,)).fetchone()
            return {"ok": True, "name": new["name"] if new else label}

        if action == "retire":
            cid, target = body.get("id"), body.get("target")
            if not cid or not target:
                return JSONResponse(
                    {"error": "a target category is required: splits have to go "
                              "somewhere, or they vanish from every report"},
                    status_code=400)
            if target == cid:
                return JSONResponse({"error": "target must differ"}, status_code=400)
            # The target must be DECLARED, not merely present in the database.
            # Migrating into a category that the next taxonomy load will retire
            # orphans the splits: they keep their amount but leave every report.
            spec = tx.load_spec()
            declared = set()
            for parent_id, node in (spec.get("management") or {}).items():
                declared.update(f"{parent_id}/{leaf}"
                                for leaf in (node.get("children") or {}))
            if target not in declared:
                return JSONResponse(
                    {"error": f"{target} is not a declared category, so migrating "
                              f"into it would orphan the splits. Create it first."},
                    status_code=400)
            using = tx.rules_referencing(cid)
            if using and not body.get("force"):
                return JSONResponse({
                    "error": f"rules still point at {cid}: {', '.join(using)}. "
                             f"Repoint them in config/rules.yaml first, or the next "
                             f"categorize puts these splits straight back.",
                    "rules": using}, status_code=409)
            parent, leaf = cid.split("/", 1)
            tx.add_migration(cid, target)
            tx.save_custom("management", parent, leaf, None, remove=True)
            moved = apply_now(c)
            return {"ok": True, "migrated_to": target, "moved_splits": moved}

        if action == "delete":
            # Deleting outright is only safe when nothing points at the
            # category. `retire` exists for the other case and demands a target,
            # because splits that lose their category keep their amount and
            # leave every report -- the worst of both.
            cid = body.get("id")
            if not cid:
                return JSONResponse({"error": "id required"}, status_code=400)
            n = c.execute("SELECT COUNT(*) FROM splits WHERE mgmt_category_id = ?",
                          (cid,)).fetchone()[0]
            if n:
                return JSONResponse({
                    "error": f"{n} Buchung{'' if n == 1 else 'en'} hängen noch an "
                             f"{cid}. Zum Auflösen die Kategorie stilllegen und "
                             f"ein Ziel angeben -- dann wandern die Buchungen mit.",
                    "splits": n}, status_code=409)
            using = tx.rules_referencing(cid)
            if using:
                return JSONResponse({
                    "error": f"Regeln zeigen noch auf {cid}: {', '.join(using)}. "
                             f"Ohne Kategorie würde die nächste Kategorisierung "
                             f"auf einen Fremdschlüsselfehler laufen.",
                    "rules": using}, status_code=409)
            parent, leaf = cid.split("/", 1)
            tx.save_custom("management", parent, leaf, None, remove=True)
            apply_now(c)
            c.execute("DELETE FROM mgmt_categories WHERE id = ?", (cid,))
            c.commit()
            return {"ok": True, "deleted": cid}

        return JSONResponse({"error": f"unknown action {action}"}, status_code=400)
    finally:
        c.close()


@router.post("/api/rule-repoint")
async def api_rule_repoint(request: Request):
    """Apply the change the conflict report suggests.

    Without this the report was a dead end: it named the rule and the category
    but the only way to act was editing YAML the dashboard cannot reach.

    Still an explicit action, never automatic -- volume and unanimity are
    evidence, not proof, and only the user can weigh a genuine exception
    against a rule that is simply wrong.
    """
    from finctl.rules.engine import repoint_rule

    body = await request.json()
    rule_id, new_cat = body.get("rule"), body.get("category")
    if not rule_id or not new_cat:
        return JSONResponse({"error": "rule and category required"}, status_code=400)

    c = conn()
    try:
        if not c.execute("SELECT 1 FROM mgmt_categories WHERE id=? AND active=1",
                         (new_cat,)).fetchone():
            return JSONResponse({"error": f"unknown category {new_cat}"},
                                status_code=400)
    finally:
        c.close()

    if not repoint_rule(rule_id, "mgmt", new_cat):
        return JSONResponse(
            {"error": f"could not find a 'mgmt:' action in rule '{rule_id}'. "
                      f"It may set the category through a split template."},
            status_code=400)

    # The overrides that prompted this are now redundant: the rule produces the
    # right answer on its own, and leaving them would hide the next real
    # disagreement behind stale ones.
    import yaml as _y

    from finctl.rules.categorize import overrides_pfad
    path = overrides_pfad()
    dropped = 0
    if path.exists():
        text = path.read_text(encoding="utf-8")
        spec = _y.safe_load(text) or {}
        keep = []
        for entry in spec.get("overrides", []) or []:
            was = entry.get("was") or {}
            parts = entry.get("parts") or []
            if (was.get("rule") == rule_id and len(parts) == 1
                    and parts[0].get("mgmt") == new_cat):
                dropped += 1
                continue
            keep.append(entry)
        if dropped:
            path.write_text(text.split("overrides:")[0] + _y.safe_dump(
                {"overrides": keep}, allow_unicode=True, sort_keys=False),
                encoding="utf-8")
    # Re-categorize immediately. Leaving this to a terminal command meant the
    # page reloaded with identical data, which is indistinguishable from the
    # button doing nothing -- and that is exactly how it was reported.
    from finctl.rules.categorize import categorize
    c = conn()
    try:
        result = categorize(c, recompute=True)
    finally:
        c.close()
    return {"ok": True, "rule": rule_id, "category": new_cat,
            "overrides_cleared": dropped,
            "recategorized": result.matched, "queue": result.unmatched}
