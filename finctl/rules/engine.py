"""Deterministic rule engine.

Rules are declarative YAML, evaluated in priority order, first match wins.
Nothing here calls a language model: AI may *author* a rule, but execution is
pure Python and reproducible. Running categorize twice from scratch must
produce byte-identical splits.

Matching defaults to the full transaction text rather than the counterparty
field, for two reasons found in the real data:

* Counterparty extraction from a PDF is best-effort. `Beispiel Vertrieb GmbH`
  comes out as `Vertrieb GmbH`, and some rows yield only the town.
* The notebook this replaces recorded the same lesson in comments --
  "Sparkasse nur über Kundenreferenz sichtbar", "Grundsteuer nur über
  Verwendungszweck sichtbar". The identifying string is not always in the name.

Text comparison ignores whitespace entirely, because DKB's kerning makes
pdfplumber emit `Erika Mus termann` and `Auszahlu ng`. Matching `mustermann`
must succeed against both.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from finctl.ledger.db import now_iso
from finctl.pfade import CONFIG_DIR

RULES_PATH = CONFIG_DIR / "rules.yaml"
# Was die Seite /regeln aendert, je Kennung nur die Abweichung. rules.yaml
# traegt die Begruendung jeder Regel in Kommentaren, und ein YAML-Schreiber
# wuerde jede davon loeschen -- dasselbe Overlay-Muster wie bei den Abos.
RULES_CUSTOM_PATH = CONFIG_DIR / "rules_custom.yaml"

MATCHERS = frozenset({
    "text", "text_all", "regex", "counterparty", "iban", "iban_is_own", "account",
    "tx_type", "sign", "amount_between", "amount_abs_between", "date_from",
    "date_to", "covered_by", "any_of", "all_of", "none_of",
})


def squash(value: str | None) -> str:
    """Casefold, strip accents and remove every non-alphanumeric character.

    `Erika Mus termann` and `Erika Mustermann` both become `erikamustermann`,
    which is what makes text matching survive the PDF's spacing defects.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value).casefold().replace("ß", "ss")
    return "".join(ch for ch in text if ch.isalnum())


@dataclass(slots=True)
class TxContext:
    """Everything a rule may match against."""

    id: int
    account_id: str
    booking_date: str
    amount_cents: int
    raw_text: str
    counterparty_norm: str
    counterparty_iban: str | None
    tx_type: str
    haystack: str = ""

    def __post_init__(self) -> None:
        self.haystack = squash(self.raw_text)


@dataclass(slots=True)
class Rule:
    id: str
    priority: int
    name: str
    match: dict[str, Any]
    actions: dict[str, Any]
    provenance: str = "handwritten"
    splits: list[dict[str, Any]] = field(default_factory=list)


class RuleError(ValueError):
    """A rulebook that cannot be trusted must fail loudly, not silently skip."""


# ------------------------------------------------------------------ loading


def repoint_rule(rule_id: str, field: str, new_value: str,
                 path: Path = RULES_PATH) -> bool:
    """Change one action value in one rule, in place.

    A targeted text edit rather than a YAML round-trip, because rules.yaml
    carries the reasoning for each rule in comments and dumping the parsed
    structure would delete all of it.

    Returns False if the rule or the field is not found, so the caller can say
    so instead of silently doing nothing.
    """
    # Hat die Seite /regeln die Aktionen dieser Regel schon ueberschrieben,
    # gilt die Kopie -- eine Aenderung in rules.yaml kaeme dort nicht mehr an.
    if path == RULES_PATH and RULES_CUSTOM_PATH.exists():
        from finctl.rules import regelwerk
        eigen = regelwerk.custom_lesen(RULES_CUSTOM_PATH)
        eintrag = eigen.get(rule_id) or {}
        if field in (eintrag.get("set") or {}):
            eintrag["set"][field] = new_value
            regelwerk.custom_schreiben(RULES_CUSTOM_PATH, eigen)
            return True

    text = path.read_text(encoding="utf-8")
    start = text.find(f"- id: {rule_id}\n")
    if start == -1:
        return False
    # The rule block runs to the next top-level list item at the same indent.
    nxt = text.find("\n  - id: ", start)
    end = len(text) if nxt == -1 else nxt + 1
    block = text[start:end]

    pattern = re.compile(rf"(\b{re.escape(field)}:\s*)([^,}}\n]+)")
    if not pattern.search(block):
        return False
    updated = pattern.sub(lambda m: m.group(1) + new_value, block, count=1)
    path.write_text(text[:start] + updated + text[end:], encoding="utf-8")
    return True


def rohe_regeln(path: Path | None = None,
                custom_path: Path | None = None) -> list[dict]:
    """Die Regeln als Eintraege, mit den Aenderungen von der Seite /regeln.

    Ohne Pfad: rules.yaml samt rules_custom.yaml. Mit ausdruecklichem Pfad nur
    diese Datei, ausser custom_path ist mitgegeben -- ein Test mit eigener
    Regeldatei soll nicht die Aenderungen des Eigentuemers mitlesen.

    Ein Feld der Kopie ersetzt das gleichnamige der Basis (match und set als
    Ganzes). Jeder Eintrag traegt `herkunft`: basis, geaendert oder eigen.
    """
    if path is None:
        path = RULES_PATH
        if custom_path is None:
            custom_path = RULES_CUSTOM_PATH
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    eigen: dict[str, dict] = {}
    if custom_path is not None and custom_path.exists():
        roh = (yaml.safe_load(custom_path.read_text(encoding="utf-8")) or {}).get("regeln") or {}
        eigen = {str(k): dict(v or {}) for k, v in roh.items()}

    out: list[dict] = []
    basis_ids: set[str] = set()
    for entry in spec.get("rules", []) or []:
        rule_id = entry.get("id")
        basis_ids.add(rule_id)
        aenderung = eigen.get(rule_id)
        if aenderung is None:
            out.append({**entry, "herkunft": "basis"})
        elif not aenderung.get("entfernt"):
            out.append({**entry, **aenderung, "id": rule_id, "herkunft": "geaendert"})
    for rule_id, entry in eigen.items():
        if rule_id not in basis_ids and not entry.get("entfernt"):
            out.append({**entry, "id": rule_id, "herkunft": "eigen"})
    return out


def load_rules(path: Path | None = None, custom_path: Path | None = None) -> list[Rule]:
    return regeln_aus(rohe_regeln(path, custom_path))


def regeln_aus(entries: list[dict]) -> list[Rule]:
    rules: list[Rule] = []
    seen: set[str] = set()

    for entry in entries:
        rule_id = entry.get("id")
        if not rule_id:
            raise RuleError(f"rule without an id: {entry}")
        if rule_id in seen:
            raise RuleError(f"duplicate rule id: {rule_id}")
        seen.add(rule_id)

        actions = entry.get("set", {}) or {}
        splits = entry.get("split", []) or []
        # Tags gibt es nicht mehr. Eine Regel, die noch welche setzt, soll
        # laut scheitern statt still ohne Wirkung zu bleiben.
        if "tags" in actions or any("tags" in (s or {}) for s in splits):
            raise RuleError(f"rule '{rule_id}' sets tags -- Tags gibt es nicht mehr; "
                            "Projekte werden unter Geteilt zugeordnet")
        if not actions and not splits:
            raise RuleError(f"rule '{rule_id}' sets nothing")

        rules.append(
            Rule(
                id=rule_id,
                priority=int(entry.get("priority", 100)),
                name=entry.get("name", rule_id),
                match=entry.get("match", {}) or {},
                actions=actions,
                provenance=entry.get("provenance", "handwritten"),
                splits=splits,
            )
        )

    # Ascending priority: 1 beats 100. Ties break on id so the order is stable
    # across runs -- determinism matters more than the tie-break itself.
    rules.sort(key=lambda r: (r.priority, r.id))
    return rules


# ----------------------------------------------------------------- matching


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _eur_to_cents(value: Any) -> int:
    return int(round(float(value) * 100))


@dataclass(frozen=True, slots=True)
class _Umfeld:
    """Was ein Matcher ausser der Buchung selbst noch braucht."""

    own_ibans: dict[str, str]
    abdeckung: dict[str, list[tuple[str, str]]]


# Ein Matcher je Schluessel, jeder mit derselben Signatur (Wert, Buchung,
# Umfeld) -> bool. Frueher war das eine if/elif-Kette ueber achtzehn
# Schluessel: wer einen neuen Matcher brauchte, musste in der Mitte einer
# Funktion mit Verzweigungsgrad 37 die richtige Stelle finden. Jetzt ist es
# eine Funktion und ein Eintrag in _PRUEFER, und der unbekannte Schluessel
# faellt weiterhin auf, weil das Nachschlagen fehlschlaegt.
#
# `_` als Parametername steht fuer "braucht dieser Matcher nicht" -- die
# einheitliche Signatur ist es wert.


def _p_any_of(value: Any, tx: TxContext, umfeld: _Umfeld) -> bool:
    return any(_passt(sub, tx, umfeld) for sub in value)


def _p_all_of(value: Any, tx: TxContext, umfeld: _Umfeld) -> bool:
    return all(_passt(sub, tx, umfeld) for sub in value)


def _p_none_of(value: Any, tx: TxContext, umfeld: _Umfeld) -> bool:
    return not any(_passt(sub, tx, umfeld) for sub in value)


def _p_text(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    return any(squash(needle) in tx.haystack for needle in _as_list(value))


def _p_text_all(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    return all(squash(needle) in tx.haystack for needle in _as_list(value))


def _p_regex(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    return bool(re.search(value, tx.raw_text, re.IGNORECASE))


def _p_counterparty(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    return any(squash(n) in tx.counterparty_norm for n in _as_list(value))


def _p_iban(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    return tx.counterparty_iban in set(_as_list(value))


def _p_iban_is_own(value: Any, tx: TxContext, umfeld: _Umfeld) -> bool:
    return bool(value) == (tx.counterparty_iban in umfeld.own_ibans)


def _p_account(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    return tx.account_id in set(_as_list(value))


def _p_tx_type(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    return any(squash(t) in squash(tx.tx_type) for t in _as_list(value))


def _p_sign(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    if value == "+":
        return tx.amount_cents > 0
    if value == "-":
        return tx.amount_cents < 0
    return True


def _p_amount_between(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    low, high = value
    return _eur_to_cents(low) <= tx.amount_cents <= _eur_to_cents(high)


def _p_amount_abs_between(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    low, high = value
    return _eur_to_cents(low) <= abs(tx.amount_cents) <= _eur_to_cents(high)


def _p_date_from(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    return tx.booking_date >= str(value)


def _p_date_to(value: Any, tx: TxContext, _: _Umfeld) -> bool:
    return tx.booking_date <= str(value)


def _p_covered_by(value: Any, tx: TxContext, umfeld: _Umfeld) -> bool:
    """Nur, wo ein importierter Auszug des genannten Kontos das Datum abdeckt.

    Die PayPal-Belastung auf der Bank ist nur dann eine Umbuchung, wenn der
    Gegenposten auf PayPal auch eingelesen ist -- sonst verschwaende die
    Ausgabe. Frueher standen dafuer feste Datumsgrenzen in rules.yaml, die bei
    jedem Export von Hand nachgezogen werden mussten.
    """
    spannen = umfeld.abdeckung.get(str(value), [])
    return any(von <= tx.booking_date <= bis for von, bis in spannen)


_PRUEFER = {
    "any_of": _p_any_of,
    "all_of": _p_all_of,
    "none_of": _p_none_of,
    "text": _p_text,
    "text_all": _p_text_all,
    "regex": _p_regex,
    "counterparty": _p_counterparty,
    "iban": _p_iban,
    "iban_is_own": _p_iban_is_own,
    "account": _p_account,
    "tx_type": _p_tx_type,
    "sign": _p_sign,
    "amount_between": _p_amount_between,
    "amount_abs_between": _p_amount_abs_between,
    "date_from": _p_date_from,
    "date_to": _p_date_to,
    "covered_by": _p_covered_by,
}


def _passt(rule_match: dict[str, Any], tx: TxContext, umfeld: _Umfeld) -> bool:
    for key, value in rule_match.items():
        pruefer = _PRUEFER.get(key)
        if pruefer is None:
            raise RuleError(f"unknown matcher: {key!r}")
        if not pruefer(value, tx, umfeld):
            return False
    return True


def matches(rule_match: dict[str, Any], tx: TxContext, own_ibans: dict[str, str],
            abdeckung: dict[str, list[tuple[str, str]]] | None = None) -> bool:
    """Evaluate one match block. Keys are ANDed; `any_of` is ORed."""
    return _passt(rule_match, tx, _Umfeld(own_ibans, abdeckung or {}))


def first_match(
    rules: list[Rule], tx: TxContext, own_ibans: dict[str, str],
    abdeckung: dict[str, list[tuple[str, str]]] | None = None,
) -> Rule | None:
    # Das Umfeld einmal bauen, nicht je Regel: bei tausend Buchungen und
    # hundert Regeln sind das hunderttausend Objekte, die nichts tun.
    umfeld = _Umfeld(own_ibans, abdeckung or {})
    for rule in rules:
        if _passt(rule.match, tx, umfeld):
            return rule
    return None


def abdeckung_map(conn: sqlite3.Connection) -> dict[str, list[tuple[str, str]]]:
    """Welche Zeitraeume je Konto durch importierte Auszuege belegt sind."""
    out: dict[str, list[tuple[str, str]]] = {}
    for konto, von, bis in conn.execute(
            "SELECT account_id, period_start, period_end FROM statements "
            "WHERE status = 'imported'"):
        out.setdefault(konto, []).append((von, bis))
    return out


# --------------------------------------------------------------- persisting


def sync_rules_to_db(conn: sqlite3.Connection, rules: list[Rule]) -> None:
    """Mirror the YAML rulebook into the database for joins and audit."""
    conn.execute("DELETE FROM rules")
    for rule in rules:
        conn.execute(
            """
            INSERT INTO rules (id, priority, name, definition_json, enabled,
                               provenance, loaded_at)
            VALUES (?,?,?,?,1,?,?)
            """,
            (
                rule.id, rule.priority, rule.name,
                json.dumps({"match": rule.match, "set": rule.actions,
                            "split": rule.splits}, ensure_ascii=False),
                rule.provenance, now_iso(),
            ),
        )
    conn.commit()


def own_iban_map(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["iban"]: row["id"]
        for row in conn.execute(
            "SELECT id, iban FROM accounts WHERE iban IS NOT NULL AND iban <> ''"
        )
    }


def load_contexts(conn: sqlite3.Connection) -> list[TxContext]:
    return [
        TxContext(
            id=row["id"],
            account_id=row["account_id"],
            booking_date=row["booking_date"],
            amount_cents=row["amount_cents"],
            raw_text=row["raw_text"] or "",
            counterparty_norm=row["counterparty_norm"] or "",
            counterparty_iban=row["counterparty_iban"],
            tx_type=row["tx_type"] or "",
        )
        for row in conn.execute(
            "SELECT id, account_id, booking_date, amount_cents, raw_text, "
            "counterparty_norm, counterparty_iban, tx_type FROM transactions "
            "ORDER BY id"
        )
    ]
