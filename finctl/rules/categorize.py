"""Apply the rulebook to transactions.

Two guarantees this module exists to keep:

* **Manual overrides are sticky.** `--recompute` rebuilds every rule-derived
  split from scratch and never touches a split whose source is 'manual'. You
  can always correct a single transaction without the next import undoing it.
* **Determinism.** Running this twice from the same ledger and rulebook
  produces identical splits, which is the regression test for the
  no-AI-at-runtime guarantee.

A transaction that matches nothing still gets one split, carrying the full
amount with no category. That keeps `sum(splits) == amount` true for every
transaction and puts the row in the review queue, rather than leaving a hole
in the ledger that reports would silently skip.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml
from filelock import FileLock

from finctl.ledger.db import now_iso, parse_de_amount
from finctl.pfade import CONFIG_DIR
from finctl.rules import engine

OVERRIDES_PATH = CONFIG_DIR / "overrides.yaml"


def overrides_pfad() -> Path:
    """Beim Aufruf nachgeschlagen, nicht beim Laden des Moduls.

    Als Standardwert eines Parameters war der Pfad beim Import festgenagelt,
    und die Tests konnten ihn nicht umbiegen: jeder Lauf der Aufteilungstests
    schrieb in die echte overrides.yaml.
    """
    return OVERRIDES_PATH

# German banks print the interest/principal split on a loan payment. Using it
# matters for tax, not tidiness: interest on a rental property is deductible
# against Anlage V, principal repayment is not. Booking the whole annuity as
# interest over-claims the deduction every single month.
_DECLARED_SPLIT = (
    # DKB and Sparda: "RECHN.ZINS 412 ,50 TILG./ENTG. 127,50"
    # The stray space is DKB's kerning, the same defect as in the dates.
    re.compile(r"RECHN\.ZINS\s+(?P<zins>[\d.]+\s?,\s?\d{2})\s+"
               r"TILG\./ENTG\.\s+(?P<tilgung>[\d.]+\s?,\s?\d{2})"),
    # DKB, zweite Form: "Tilgung 250,00 Zinsen 150,00"
    re.compile(r"Tilgung\s+(?P<tilgung>[\d.]+,\d{2})\s+Zinsen\s+(?P<zins>[\d.]+,\d{2})"),
)


def declared_split(raw_text: str) -> tuple[int, int] | None:
    """Interest and principal as the bank itself stated them, in cents."""
    for pattern in _DECLARED_SPLIT:
        m = pattern.search(raw_text or "")
        if m:
            return parse_de_amount(m.group("zins")), parse_de_amount(m.group("tilgung"))
    return None


@dataclass(slots=True)
class CategorizeResult:
    matched: int = 0
    unmatched: int = 0
    flagged: int = 0
    declared_splits: int = 0
    replayed: int = 0
    manual_preserved: int = 0
    by_rule: dict[str, int] = None

    def __post_init__(self) -> None:
        if self.by_rule is None:
            self.by_rule = {}


def _declared_parts(rule: engine.Rule, tx, total_cents: int) -> list[tuple[int, dict]] | None:
    """Expand a loan payment into its declared interest and principal parts.

    Returns None when the bank did not print a split, so the caller falls back
    to the rule's ordinary behaviour rather than inventing a breakdown.
    """
    found = declared_split(tx.raw_text)
    if not found:
        return None
    zins, tilgung = found
    sign = -1 if total_cents < 0 else 1

    # Only trust the declaration if it accounts for the whole payment; a
    # partial match would silently lose or duplicate money.
    if zins + tilgung != abs(total_cents):
        return None

    interest_actions = {**rule.actions,
                        "mgmt": "kredit/zinsen",
                        "tax": rule.actions.get("tax_interest", "anlage_v/zinsen"),
                        "note": "Zinsanteil laut Bankbeleg"}
    principal_actions = {**rule.actions,
                         "mgmt": "kredit/tilgung",
                         # Principal repayment is NOT deductible -- it buys
                         # equity rather than paying for anything.
                         "tax": rule.actions.get("tax_principal",
                                                 "privat/nicht-abzugsfaehig"),
                         "note": "Tilgungsanteil laut Bankbeleg"}
    for a in (interest_actions, principal_actions):
        a.pop("split_declared", None)
        a.pop("tax_interest", None)
        a.pop("tax_principal", None)
    return [(sign * zins, interest_actions), (sign * tilgung, principal_actions)]


_SCHEDULES: dict[str, dict] = {}

#: Wer Tilgungsplaene kennt, meldet sich hier an: eine Funktion von der
#: Kreditkennung auf {"JJJJ-MM": (Zinsen, Tilgung, Grundlage)}. Die Regeln
#: kennen keinen Kredit -- sie gehoeren zur Basis, die Kredite sind ein Modul
#: (finctl/module.py). Angemeldet wird in finctl/kredite.py, angeschlossen in
#: der App (finctl/ops.py), die jeden Kategorisierungslauf ausloest.
_TILGUNGSPLAENE: list = []


def tilgungsplan_anmelden(quelle) -> None:
    """Eine Quelle fuer Tilgungsplaene eintragen; doppelt zaehlt einmal."""
    if quelle not in _TILGUNGSPLAENE:
        _TILGUNGSPLAENE.append(quelle)
        _SCHEDULES.clear()


def _schedule_for(loan_id: str) -> dict:
    """Month -> (interest, principal, basis) for one loan, built once per process.

    Ohne angemeldete Quelle gibt es keinen Plan, und die Rate bleibt ganz --
    dieselbe Folge wie ein Plan, der zum Betrag nicht passt.
    """
    if loan_id in _SCHEDULES:
        return _SCHEDULES[loan_id]
    table: dict[str, tuple[int, int, str]] = {}
    for quelle in _TILGUNGSPLAENE:
        table.update(quelle(loan_id))
    if _TILGUNGSPLAENE:
        _SCHEDULES[loan_id] = table
    return table


def _scheduled_parts(rule: engine.Rule, tx, total_cents: int) -> list[tuple[int, dict]] | None:
    """Split a loan payment using the amortisation schedule.

    The fallback for banks that debit an annuity without printing what is
    interest and what is principal. Booking the whole payment as Tilgung
    understates the Anlage V deduction; booking it as Zinsen overstates it.
    Neither is acceptable, and the schedule knows the answer.

    Returns None unless the schedule's annuity for that month matches the
    payment. A schedule that has drifted from reality must not be allowed to
    quietly restate what the account actually paid -- a mismatch means the
    segment is wrong, and the payment stays whole until it is fixed.
    """
    loan_id = rule.actions.get("loan")
    if not loan_id:
        return None
    entry = _schedule_for(str(loan_id)).get(tx.booking_date[:7])
    if not entry:
        return None
    interest, principal, basis = entry
    if interest + principal != abs(total_cents):
        return None

    sign = -1 if total_cents < 0 else 1
    provisional = basis != "actual"
    note = ("Zinsanteil laut Tilgungsplan"
            + (" -- Zinssatz noch unbestätigt" if provisional else ""))
    interest_actions = {**rule.actions, "mgmt": "kredit/zinsen",
                        "tax": rule.actions.get("tax_interest", "anlage_v/zinsen"),
                        "note": note}
    principal_actions = {**rule.actions, "mgmt": "kredit/tilgung",
                         "tax": rule.actions.get("tax_principal",
                                                 "privat/nicht-abzugsfaehig"),
                         "note": note.replace("Zinsanteil", "Tilgungsanteil")}
    for a in (interest_actions, principal_actions):
        for key in ("split_declared", "tax_interest", "tax_principal"):
            a.pop(key, None)
    return [(sign * interest, interest_actions), (sign * principal, principal_actions)]


def _split_amounts(rule: engine.Rule, total_cents: int) -> list[tuple[int, dict]]:
    """Expand a rule's split template.

    Percentages are resolved against the transaction total, with the final
    split absorbing the rounding remainder so the parts always sum exactly to
    the whole -- a split that is off by a cent would break the ledger invariant.
    """
    if not rule.splits:
        return [(total_cents, rule.actions)]

    parts: list[tuple[int, dict]] = []
    allocated = 0
    for index, spec in enumerate(rule.splits):
        actions = {**rule.actions, **{k: v for k, v in spec.items()
                                      if k not in ("pct", "amount")}}
        if index == len(rule.splits) - 1:
            cents = total_cents - allocated
        elif "pct" in spec:
            cents = int(round(total_cents * float(spec["pct"]) / 100.0))
        else:
            cents = int(round(float(spec["amount"]) * 100))
        allocated += cents
        parts.append((cents, actions))
    return parts


def capture_prior(conn: sqlite3.Connection, transaction_id: int) -> dict | None:
    """What the rulebook decided, before a manual correction replaces it."""
    row = conn.execute(
        "SELECT rule_id, mgmt_category_id FROM splits "
        "WHERE transaction_id = ? AND source IN ('rule','token','default') "
        "ORDER BY seq LIMIT 1", (transaction_id,)).fetchone()
    if row is None or not row["rule_id"]:
        return None
    return {"rule": row["rule_id"], "category": row["mgmt_category_id"]}


def rule_conflicts(path: Path | None = None) -> list[dict]:
    """Rules the user keeps disagreeing with.

    One override is an exception -- groceries bought at a petrol station. The
    same rule overridden repeatedly, usually to the same category, is a rule
    that is simply wrong, and no amount of per-transaction correcting will fix
    it for the next occurrence.

    Reported rather than applied. A rule that rewrote itself from a single
    correction would silently repoint a rule that was right forty times.
    """
    path = path or overrides_pfad()
    if not path.exists():
        return []
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    by_rule: dict[str, dict] = {}

    for entry in spec.get("overrides", []) or []:
        was = entry.get("was")
        if not was or not was.get("rule"):
            continue
        parts = entry.get("parts") or []
        if len(parts) != 1:
            continue                      # a split is not a disagreement
        new_cat = parts[0].get("mgmt")
        if not new_cat or new_cat == was.get("category"):
            continue
        bucket = by_rule.setdefault(was["rule"], {
            "rule": was["rule"], "from": was.get("category"),
            "count": 0, "to": {}, "examples": []})
        bucket["count"] += 1
        bucket["to"][new_cat] = bucket["to"].get(new_cat, 0) + 1
        if len(bucket["examples"]) < 3:
            bucket["examples"].append(
                {"when": entry.get("when"), "text": (entry.get("text") or "")[:70]})

    out = []
    for bucket in by_rule.values():
        winner, hits = max(bucket["to"].items(), key=lambda kv: kv[1])
        out.append({**bucket, "most_common": winner, "most_common_count": hits,
                    "unanimous": hits == bucket["count"]})
    return sorted(out, key=lambda b: -b["count"])


@contextlib.contextmanager
def _exclusive(path: Path):
    """Serialise the read-modify-write on the overrides file.

    Recording an override rewrites the WHOLE file: read 873 entries, add one,
    write 874 back. Two writers overlapping means the second one's read
    predates the first one's write, so the first entry is silently dropped --
    and there are routinely two writers here, because the dashboard runs as a
    long-lived server while `finctl` is used from the shell at the same time.

    This is not hypothetical. One decision was lost exactly this way and only
    surfaced because `validate` compares the ledger against this file.

    A reader is protected as well, by the atomic replace in the caller: without
    it a concurrent read can land on a half-written file and parse as a much
    shorter list, which looks like data loss and is impossible to tell apart
    from the real thing afterwards.

    Ueber `filelock` und nicht ueber `fcntl`: fcntl gibt es auf Windows nicht,
    und der Import stand hier ganz oben -- das Werkzeug lief dort bis zu dem
    Moment, in dem jemand eine Kategorie zuordnete. Eine eigene Fallunter-
    scheidung waere moeglich gewesen, aber der Windows-Zweig liesse sich von
    hier aus nicht ausprobieren; eine Bibliothek, die ihn selbst prueft, ist
    die ehrlichere Wahl.

    Mit Zeitgrenze statt unbegrenztem Warten: eine Sperre, die nach einem
    Absturz liegen bleibt, soll eine Fehlermeldung ergeben und nicht ein
    Programm, das stumm haengt.
    """
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(lock, timeout=30):
        yield


def _write_atomically(path: Path, text: str) -> None:
    """Replace the file in one step, so no reader ever sees it half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def record_override(conn: sqlite3.Connection, transaction_id: int,
                    path: Path | None = None,
                    prior: dict | None = None) -> None:
    """Persist a manual decision outside the database.

    Manual splits used to live only in the ledger, which quietly broke the
    guarantee this project is built on: rebuild from statements and you get an
    identical result. That held for rules, taxonomy and loans -- all declared
    in config -- but not for the user's own decisions, so deleting the database
    destroyed them. It did, once.

    Keyed on dedup_hash rather than transaction id, because ids are assigned at
    import and shift on every rebuild, while the hash is derived from the
    transaction's own content and does not.
    """
    path = path or overrides_pfad()
    tx = conn.execute(
        "SELECT dedup_hash, booking_date, account_id, amount_cents, "
        "substr(raw_text,1,70) AS raw FROM transactions WHERE id = ?",
        (transaction_id,)).fetchone()
    if tx is None:
        return
    parts = [
        {"amount_cents": r["amount_cents"],
         "mgmt": r["mgmt_category_id"], "tax": r["tax_category_id"],
         "property": r["property_id"], "note": r["note"]}
        for r in conn.execute(
            "SELECT amount_cents, mgmt_category_id, tax_category_id, property_id, note "
            "FROM splits WHERE transaction_id = ? AND source = 'manual' ORDER BY seq",
            (transaction_id,))
    ]

    # Everything from here to the write is one critical section: reading the
    # existing entries and writing the amended set must not interleave with
    # another process doing the same, or one of the two decisions is lost.
    with _exclusive(path):
        _amend(path, tx, parts, prior)


def _amend(path: Path, tx, parts: list[dict], prior: dict | None) -> None:
    """The read-modify-write itself. Call only while holding the lock."""
    spec = {}
    if path.exists():
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = {e["dedup_hash"]: e for e in spec.get("overrides", []) or []}

    # What the rulebook had decided before the correction. Kept so repeated
    # disagreement with one rule can be surfaced: the fix for a systematically
    # wrong rule is to change the rule, not to override it forever.
    previous = prior or entries.get(tx["dedup_hash"], {}).get("was")

    if not parts:
        entries.pop(tx["dedup_hash"], None)
    else:
        entry = {
            "dedup_hash": tx["dedup_hash"],
            "when": tx["booking_date"], "account": tx["account_id"],
            "amount_cents": tx["amount_cents"], "text": tx["raw"],
            "parts": parts,
        }
        if previous:
            entry["was"] = previous
        entries[tx["dedup_hash"]] = entry

    _write_atomically(
        path,
        "# Manual categorization decisions, replayed after every categorize.\n"
        "#\n"
        "# Keyed on dedup_hash, not transaction id: ids are assigned at import\n"
        "# and shift on every rebuild, the hash is derived from the transaction\n"
        "# itself and does not. This is what makes your own decisions survive a\n"
        "# `rm data/finance.db` -- they did not, once, and roughly thirty were\n"
        "# lost.\n"
        "#\n"
        "# Written automatically. Safe to edit or to keep in git.\n\n"
        + yaml.safe_dump({"overrides": list(entries.values())},
                         allow_unicode=True, sort_keys=False))


def _default_tax(conn: sqlite3.Connection, mgmt: str | None) -> str | None:
    """The tax position a management category implies, if it declares one.

    Correcting a category in the dashboard should not silently drop the tax
    position with it. Four Anlage V costs were saved by hand as Nebenkosten and
    came back with no tax category at all, which removes them from the export
    while leaving them in the ledger -- the failure mode that is hardest to
    notice, because every household total still adds up.
    """
    if not mgmt:
        return None
    row = conn.execute(
        "SELECT default_tax_id FROM mgmt_categories WHERE id = ?", (mgmt,)
    ).fetchone()
    return row["default_tax_id"] if row else None


def replay_overrides(conn: sqlite3.Connection,
                     path: Path | None = None) -> int:
    """Re-apply saved manual decisions to a freshly built ledger."""
    path = path or overrides_pfad()
    if not path.exists():
        return 0
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    stamp = now_iso()
    applied = 0

    for entry in spec.get("overrides", []) or []:
        row = conn.execute("SELECT id FROM transactions WHERE dedup_hash = ?",
                           (entry["dedup_hash"],)).fetchone()
        if row is None:
            continue                     # that statement is not loaded
        tx_id = row["id"]
        parts = entry.get("parts") or []
        if parts:
            conn.execute("DELETE FROM splits WHERE transaction_id = ?", (tx_id,))
            for seq, part in enumerate(parts):
                conn.execute(
                    "INSERT INTO splits (transaction_id, seq, amount_cents, "
                    "mgmt_category_id, tax_category_id, property_id, note, source, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?, 'manual', ?, ?)",
                    (tx_id, seq, part["amount_cents"], part.get("mgmt"),
                     part.get("tax") or _default_tax(conn, part.get("mgmt")),
                     part.get("property"), part.get("note"),
                     stamp, stamp))
            applied += 1
    conn.commit()
    return applied


def categorize(conn: sqlite3.Connection, *, recompute: bool = True) -> CategorizeResult:
    rules = engine.load_rules()
    own_ibans = engine.own_iban_map(conn)
    abdeckung = engine.abdeckung_map(conn)
    result = CategorizeResult()

    manual_tx_ids = {
        row["transaction_id"]
        for row in conn.execute(
            "SELECT DISTINCT transaction_id FROM splits WHERE source = 'manual'"
        )
    }
    result.manual_preserved = len(manual_tx_ids)

    # Order matters: rule-derived splits reference rules, so they are cleared
    # before the rulebook is rewritten.
    if recompute:
        conn.execute("DELETE FROM splits WHERE source <> 'manual'")
        conn.execute(
            "DELETE FROM rule_applications WHERE split_id NOT IN (SELECT id FROM splits)"
        )
    engine.sync_rules_to_db(conn, rules)

    stamp = now_iso()
    for tx in engine.load_contexts(conn):
        if tx.id in manual_tx_ids:
            continue

        rule = engine.first_match(rules, tx, own_ibans, abdeckung)
        if rule is None:
            result.unmatched += 1
            conn.execute(
                """
                INSERT INTO splits (transaction_id, seq, amount_cents, source,
                                    created_at, updated_at)
                VALUES (?,0,?, 'default', ?, ?)
                """,
                (tx.id, tx.amount_cents, stamp, stamp),
            )
            continue

        # A rule may deliberately decline to categorize: some payments are
        # aggregates (PayPal covering several merchants) where any single
        # category would be a guess. Flagging routes them to the review queue
        # WITH the reason attached, which is more useful than leaving them
        # unmatched and indistinguishable from genuinely unknown rows.
        if rule.actions.get("review"):
            result.flagged += 1
            conn.execute(
                """
                INSERT INTO splits (transaction_id, seq, amount_cents, note,
                                    source, rule_id, created_at, updated_at)
                VALUES (?,0,?,?, 'default', ?, ?, ?)
                """,
                (tx.id, tx.amount_cents,
                 rule.actions.get("note") or f"flagged by {rule.id}",
                 rule.id, stamp, stamp),
            )
            continue

        result.matched += 1
        result.by_rule[rule.id] = result.by_rule.get(rule.id, 0) + 1

        parts = None
        if rule.actions.get("split_declared"):
            parts = _declared_parts(rule, tx, tx.amount_cents)
            # The bank's own figures first, the schedule only where it printed
            # none: a modelled split must never override a stated one.
            if parts is None:
                parts = _scheduled_parts(rule, tx, tx.amount_cents)
            if parts:
                result.declared_splits += 1
        if parts is None:
            parts = _split_amounts(rule, tx.amount_cents)

        for seq, (cents, actions) in enumerate(parts):
            # A rule may leave the tax position to the category it chooses.
            if actions.get("mgmt") and not actions.get("tax"):
                implied = conn.execute(
                    "SELECT default_tax_id FROM mgmt_categories WHERE id = ?",
                    (actions["mgmt"],)).fetchone()
                if implied and implied["default_tax_id"]:
                    actions = {**actions, "tax": implied["default_tax_id"]}

            transfer_account = actions.get("transfer_account")
            if transfer_account == "auto":
                transfer_account = own_ibans.get(tx.counterparty_iban or "")

            cur = conn.execute(
                """
                INSERT INTO splits
                    (transaction_id, seq, amount_cents, mgmt_category_id,
                     tax_category_id, property_id, loan_id, transfer_account_id,
                     note, source, rule_id, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tx.id, seq, cents,
                    actions.get("mgmt"), actions.get("tax"), actions.get("property"),
                    actions.get("loan"), transfer_account, actions.get("note"),
                    "token" if rule.provenance == "token" else "rule",
                    rule.id, stamp, stamp,
                ),
            )
            split_id = cur.lastrowid
            conn.execute(
                "INSERT INTO rule_applications (split_id, rule_id, applied_at) "
                "VALUES (?,?,?)",
                (split_id, rule.id, stamp),
            )

    # Replay saved decisions last, so they win over anything a rule produced.
    result.replayed = replay_overrides(conn)
    conn.commit()

    # Recount the queue AFTER the replay, not during the rule loop.
    #
    # `unmatched` was incremented while the rules ran, so it counted every
    # transaction no rule caught -- including the hundreds the owner had
    # already decided by hand. On an existing ledger that is invisible, since
    # those splits are already marked manual and excluded from the pass. On a
    # rebuild from statements it is badly wrong: the first honest test of the
    # rebuild guarantee reported "263 in the review queue" for a ledger whose
    # real queue was empty, which reads as data loss and is not.
    result.unmatched = conn.execute(
        "SELECT COUNT(*) FROM v_review_queue").fetchone()[0]
    return result


def vorschau(conn: sqlite3.Connection, rules: list[engine.Rule], rule_id: str,
             limit: int = 50) -> dict:
    """Was ein Regelwerk-Entwurf fuer EINE Regel aendern wuerde, ohne zu schreiben.

    Anders als `preview` mit dem ganzen Entwurf gerechnet: eine geaenderte
    Prioritaet verschiebt, wer gewinnt, und eine enger gefasste Regel gibt
    Buchungen ab. Beides zeigt sich nur, wenn alle Regeln so laufen wie nach
    dem Speichern. Eigene Entscheidungen zaehlen getrennt -- sie bleiben.
    """
    rule = next((r for r in rules if r.id == rule_id), None)
    if rule is None:
        raise engine.RuleError(f"no such rule: {rule_id}")
    own_ibans = engine.own_iban_map(conn)
    abdeckung = engine.abdeckung_map(conn)
    stand = {row["transaction_id"]: row for row in conn.execute(
        "SELECT transaction_id, MAX(source = 'manual') AS manuell, "
        "MIN(mgmt_category_id) AS mgmt, MIN(rule_id) AS rule_id "
        "FROM splits GROUP BY transaction_id")}

    treffer, verdeckt, eigen, verliert = [], 0, 0, 0
    for tx in engine.load_contexts(conn):
        jetzt = stand.get(tx.id)
        passt = engine.matches(rule.match, tx, own_ibans, abdeckung)
        if jetzt is not None and jetzt["manuell"]:
            eigen += int(passt)
            continue
        if passt:
            gewinner = engine.first_match(rules, tx, own_ibans, abdeckung)
            if gewinner is not None and gewinner.id == rule.id:
                treffer.append((tx, jetzt))
            else:
                verdeckt += 1
        elif jetzt is not None and jetzt["rule_id"] == rule.id:
            verliert += 1

    ziel = rule.actions.get("mgmt")
    zeilen = []
    for tx, jetzt in sorted(treffer, key=lambda p: p[0].booking_date, reverse=True):
        alt = jetzt["mgmt"] if jetzt is not None else None
        zeilen.append({"datum": tx.booking_date, "konto": tx.account_id,
                       "text": " ".join(tx.raw_text.split())[:140],
                       "amount_cents": tx.amount_cents, "kategorie_jetzt": alt,
                       "aendert": alt != ziel})
    return {"treffer": len(treffer), "aendert": sum(z["aendert"] for z in zeilen),
            "verdeckt": verdeckt, "eigen": eigen, "verliert": verliert,
            "summe_cents": sum(t.amount_cents for t, _ in treffer),
            "zeilen": zeilen[:limit]}


def preview(conn: sqlite3.Connection, rule_id: str) -> dict:
    """What a single rule would catch, without writing anything.

    Used before committing a rulebook change, so the blast radius of a new or
    edited rule is visible in advance rather than discovered afterwards.
    """
    rules = engine.load_rules()
    rule = next((r for r in rules if r.id == rule_id), None)
    if rule is None:
        raise engine.RuleError(f"no such rule: {rule_id}")

    own_ibans = engine.own_iban_map(conn)
    abdeckung = engine.abdeckung_map(conn)
    higher = [r for r in rules if (r.priority, r.id) < (rule.priority, rule.id)]

    hits, shadowed = [], 0
    for tx in engine.load_contexts(conn):
        if not engine.matches(rule.match, tx, own_ibans, abdeckung):
            continue
        if engine.first_match(higher, tx, own_ibans, abdeckung) is not None:
            shadowed += 1
            continue
        hits.append(tx)

    return {
        "rule": rule.id,
        "name": rule.name,
        "matched": len(hits),
        "shadowed_by_higher_priority": shadowed,
        "total_cents": sum(t.amount_cents for t in hits),
        "sample": [
            {"date": t.booking_date, "account": t.account_id,
             "amount_cents": t.amount_cents, "text": t.raw_text[:90]}
            for t in hits[:10]
        ],
    }


def unrecorded_manual_splits(conn: sqlite3.Connection,
                            path: Path | None = None) -> list[dict]:
    """Manual decisions that exist only in the database.

    A split marked 'manual' survives `categorize --recompute`, which makes it
    look safe. It does NOT survive a rebuild from statements -- and that is the
    failure overrides.yaml exists to prevent. One endpoint reintroduced it by
    marking splits manual without recording them, which put ten corrections,
    including two Casa Con settlements, one `rm data/finance.db` from gone.

    Reported as a violation because nothing about it is visible otherwise:
    every total is right until the day the ledger is rebuilt.

    Lives with the overrides, not in the ledger: the ledger is core and must
    not know about rules (`finctl/module.py`).
    """
    path = path or overrides_pfad()

    known: set[str] = set()
    if path.exists():
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        known = {e.get("dedup_hash") for e in (spec.get("overrides") or [])}
    return [
        dict(r) for r in conn.execute(
            """
            SELECT DISTINCT t.id, t.dedup_hash, t.booking_date, t.account_id,
                            t.amount_cents
            FROM   splits s JOIN transactions t ON t.id = s.transaction_id
            WHERE  s.source = 'manual'
            ORDER  BY t.booking_date
            """)
        if r["dedup_hash"] not in known
    ]
