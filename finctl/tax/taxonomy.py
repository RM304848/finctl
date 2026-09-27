"""Load the management and tax taxonomies from config/taxonomy.yaml.

Both trees are exactly two levels deep. That is a deliberate constraint rather
than an incidental one: deeper trees make rules fiddly to write and reports
awkward to read, and the free-tag axis already absorbs the long tail.

Anlage V positions carry `requires_property`. They are stored once and scoped
by the split's property_id, so a fifth property needs no new categories, and
one object's cost cannot be filed under another object's category.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from finctl.pfade import CONFIG_DIR

TAXONOMY_PATH = CONFIG_DIR / "taxonomy.yaml"
MIGRATIONS_PATH = CONFIG_DIR / "taxonomy_migrations.yaml"
# Categories added or renamed from the dashboard. A separate file because
# taxonomy.yaml carries the reasoning in comments and a YAML writer drops
# every one of them.
CUSTOM_PATH = CONFIG_DIR / "taxonomy_custom.yaml"


def apply_migrations(conn: sqlite3.Connection,
                     path: Path = MIGRATIONS_PATH) -> dict[str, int]:
    """Move splits off retired categories before the tree is reloaded.

    Renaming or removing a category without this orphans its splits: they keep
    their amount in the ledger but disappear from every report that joins on
    the category, so totals silently stop adding up.
    """
    if not path.exists():
        return {}
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    moved: dict[str, int] = {}

    remap = spec.get("remap") or {}
    for old, new in remap.items():
        cur = conn.execute(
            "UPDATE splits SET mgmt_category_id = ? WHERE mgmt_category_id = ?",
            (new, old))
        if cur.rowcount:
            moved[f"{old} -> {new}"] = cur.rowcount
        conn.execute("UPDATE mgmt_categories SET active = 0 WHERE id = ?", (old,))

    # A category id lives in three places, and migrating only the database
    # leaves the other two to undo it: the rulebook (warned about when
    # retiring) and the saved manual overrides, which are replayed AFTER
    # categorize and therefore put the retired id straight back.
    moved_overrides = remap_overrides(remap)
    if moved_overrides:
        moved["manual overrides"] = moved_overrides

    for dead in (spec.get("retire") or []):
        left = conn.execute(
            "SELECT COUNT(*) FROM splits WHERE mgmt_category_id = ?", (dead,)
        ).fetchone()[0]
        if left:
            raise ValueError(
                f"refusing to retire {dead}: {left} splits still point at it")
        conn.execute("UPDATE mgmt_categories SET active = 0 WHERE id = ?", (dead,))

    conn.commit()
    return moved


def remap_overrides(remap: dict[str, str],
                     path: Path | None = None) -> int:
    """Repoint saved manual decisions at renamed categories."""
    from finctl.rules.categorize import overrides_pfad

    path = path or overrides_pfad()
    if not path.exists() or not remap:
        return 0
    text = path.read_text(encoding="utf-8")
    spec = yaml.safe_load(text) or {}
    entries = spec.get("overrides", []) or []
    changed = 0
    for entry in entries:
        for part in entry.get("parts") or []:
            if part.get("mgmt") in remap:
                part["mgmt"] = remap[part["mgmt"]]
                changed += 1
    if changed:
        header = text.split("overrides:")[0]
        path.write_text(header + yaml.safe_dump({"overrides": entries},
                                                allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    return changed


def deactivate_missing(conn: sqlite3.Connection, path: Path = TAXONOMY_PATH) -> int:
    """Hide categories no longer declared, without deleting history."""
    spec = load_spec(path)
    declared = set()
    for parent, node in (spec.get("management") or {}).items():
        declared.add(parent)
        declared.update(f"{parent}/{leaf}" for leaf in (node.get("children") or {}))
    rows = conn.execute("SELECT id FROM mgmt_categories WHERE active = 1").fetchall()
    n = 0
    for row in rows:
        if row["id"] not in declared:
            conn.execute("UPDATE mgmt_categories SET active = 0 WHERE id = ?", (row["id"],))
            n += 1
    conn.commit()
    return n


def load_spec(path: Path = TAXONOMY_PATH) -> dict:
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not CUSTOM_PATH.exists():
        return spec
    custom = yaml.safe_load(CUSTOM_PATH.read_text(encoding="utf-8")) or {}

    for axis in ("management", "tax"):
        for parent, node in (custom.get(axis) or {}).items():
            base = spec.setdefault(axis, {}).setdefault(
                parent, {"name": node.get("name", parent), "kind": node.get("kind", "expense"),
                         "children": {}})
            if node.get("name"):
                base["name"] = node["name"]
            base.setdefault("children", {}).update(node.get("children") or {})
    return spec


def save_custom(axis: str, parent: str, leaf: str, label, *,
                remove: bool = False, path: Path = CUSTOM_PATH) -> None:
    """Record a dashboard category change alongside the declared taxonomy."""
    spec = {}
    if path.exists():
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    node = spec.setdefault(axis, {}).setdefault(parent, {"children": {}})
    children = node.setdefault("children", {})
    if remove:
        children.pop(leaf, None)
        if not children:
            spec[axis].pop(parent, None)
    else:
        children[leaf] = label
    path.write_text(
        "# Categories added or renamed from the dashboard.\n"
        "#\n"
        "# Merged on top of taxonomy.yaml, which stays the documented base with\n"
        "# its reasoning intact -- a YAML writer would strip every comment there.\n"
        "# Safe to edit by hand or to keep in git.\n\n"
        + yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
        encoding="utf-8")


def add_migration(old: str, new: str, path: Path = MIGRATIONS_PATH) -> None:
    """Record where a retired category's splits should move."""
    spec = {}
    if path.exists():
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    header = ""
    if path.exists():
        text = path.read_text(encoding="utf-8")
        header = text.split("remap:")[0] if "remap:" in text else ""
    spec.setdefault("remap", {})[old] = new
    spec.setdefault("retire", [])
    path.write_text(header + yaml.safe_dump(
        {"remap": spec["remap"], "retire": spec["retire"]},
        allow_unicode=True, sort_keys=False), encoding="utf-8")


def rules_referencing(category_id: str, path: Path | None = None) -> list[str]:
    """Rules pointing at a category.

    Retiring a category without moving its rules is the mistake that bit us
    once already: the splits migrate, then the next categorize puts them
    straight back through a rule still naming the old id.

    Reads the merged rulebook, including what /regeln changed -- a rule edited
    there points wherever its copy says, not where rules.yaml says.
    """
    from finctl.rules.engine import rohe_regeln

    try:
        eintraege = rohe_regeln(path)
    except FileNotFoundError:
        return []
    hits = []
    for rule in eintraege:
        blob = yaml.safe_dump(rule.get("set", {}) or {})
        if category_id in blob:
            hits.append(rule.get("id", "?"))
    return hits


def load_into_db(conn: sqlite3.Connection, path: Path = TAXONOMY_PATH) -> dict[str, int]:
    spec = load_spec(path)
    counts = {"mgmt": 0, "tax": 0}

    for order, (parent_id, node) in enumerate(spec.get("management", {}).items()):
        kind = node.get("kind", "expense")
        # A parent may declare `fix: true` for all its children -- every
        # insurance and every subscription is a fixed cost, and repeating the
        # flag on each one would just be noise to keep in sync.
        parent_fix = bool(node.get("fix", False))
        conn.execute(
            """
            INSERT INTO mgmt_categories (id, parent_id, name, kind, sort_order,
                                         fixkosten, active)
            VALUES (?, NULL, ?, ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name, kind = excluded.kind,
                sort_order = excluded.sort_order,
                fixkosten = excluded.fixkosten,
                -- Re-declaring revives: without this a category that was once
                -- retired stays dead forever, and any split migrated into it is
                -- silently orphaned.
                active = 1
            """,
            (parent_id, node["name"], kind, order, int(parent_fix)),
        )
        counts["mgmt"] += 1
        for child_order, (leaf, value) in enumerate(node.get("children", {}).items()):
            # A child is either a plain label or a mapping that also declares
            # the tax position the category implies and whether it is fixed.
            if isinstance(value, dict):
                label, default_tax = value.get("name", leaf), value.get("tax")
                fix = bool(value.get("fix", parent_fix))
            else:
                label, default_tax, fix = value, None, parent_fix
            conn.execute(
                """
                INSERT INTO mgmt_categories (id, parent_id, name, kind, sort_order,
                                             default_tax_id, fixkosten, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name, parent_id = excluded.parent_id,
                    kind = excluded.kind, sort_order = excluded.sort_order,
                    default_tax_id = excluded.default_tax_id,
                    fixkosten = excluded.fixkosten,
                    active = 1
                """,
                (f"{parent_id}/{leaf}", parent_id, label, kind, child_order,
                 default_tax, int(fix)),
            )
            counts["mgmt"] += 1

    for order, (parent_id, node) in enumerate(spec.get("tax", {}).items()):
        anlage = node.get("anlage")
        conn.execute(
            """
            INSERT INTO tax_categories
                (id, parent_id, name, anlage, deductible, requires_property, sort_order, active)
            VALUES (?, NULL, ?, ?, 0, 0, ?, 1)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name, anlage = excluded.anlage,
                sort_order = excluded.sort_order, active = 1
            """,
            (parent_id, node["name"], anlage, order),
        )
        counts["tax"] += 1
        for child_order, (leaf, meta) in enumerate(node.get("children", {}).items()):
            conn.execute(
                """
                INSERT INTO tax_categories
                    (id, parent_id, name, anlage, deductible, requires_property,
                     sort_order, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name, parent_id = excluded.parent_id,
                    anlage = excluded.anlage, deductible = excluded.deductible,
                    requires_property = excluded.requires_property,
                    sort_order = excluded.sort_order, active = 1
                """,
                (
                    f"{parent_id}/{leaf}", parent_id, meta["name"], anlage,
                    int(bool(meta.get("deductible", False))),
                    int(bool(meta.get("property", False))),
                    child_order,
                ),
            )
            counts["tax"] += 1

    conn.commit()
    return counts


def display_name(
    conn: sqlite3.Connection, tax_category_id: str, property_id: str | None
) -> str:
    """Render a tax position the way it should read in a report.

    Property-scoped positions come out as "<Objekt> - Erhaltungsaufwendungen";
    everything else keeps its own name.
    """
    row = conn.execute(
        "SELECT name, requires_property FROM tax_categories WHERE id = ?",
        (tax_category_id,),
    ).fetchone()
    if row is None:
        return tax_category_id
    if not row["requires_property"] or not property_id:
        return row["name"]
    prop = conn.execute(
        "SELECT name FROM properties WHERE id = ?", (property_id,)
    ).fetchone()
    label = prop["name"] if prop else property_id
    return f"{label} - {row['name']}"
