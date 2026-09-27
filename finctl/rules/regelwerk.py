"""Regeln aus dem Dashboard pflegen.

rules.yaml ist von Hand geschrieben und traegt die Begruendung jeder Regel in
Kommentaren; ein YAML-Schreiber wuerde jede davon loeschen. Die Seite /regeln
schreibt deshalb nach rules_custom.yaml, und nur, was von rules.yaml abweicht
-- dasselbe Muster wie bei den Abos. Zusammengefuehrt wird in
`engine.rohe_regeln`, damit Kategorisierung und Seite dieselben Regeln sehen.

Die Seite bearbeitet die haeufigen Felder. Alles andere einer Regel -- any_of,
transfer_account, Aufteilungen -- bleibt beim Speichern unangetastet stehen und
wird angezeigt, nicht verworfen.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from finctl.rules import engine

MATCH_FELDER = ("text", "text_all", "counterparty", "account", "sign",
                "amount_abs_between", "date_from", "date_to")
SET_FELDER = ("mgmt", "tax", "property", "note")
_LISTEN = {"text", "text_all", "counterparty", "account"}
_BETRAEGE = {"amount_between", "amount_abs_between"}
#: Obergrenze, wenn nur "Betrag ab" angegeben ist -- in Euro, wie rules.yaml.
OBEN = 1_000_000_000
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DATUM = re.compile(r"^\d{4}-\d{2}-\d{2}$")

KOPF = """# Auf der Seite /regeln angelegt und geaendert.
#
# Je Kennung nur, was von config/rules.yaml abweicht: ein Feld hier ersetzt das
# gleichnamige dort (match und set als Ganzes). `entfernt: true` blendet eine
# Regel aus rules.yaml aus. Kennungen, die es dort nicht gibt, sind eigene
# Regeln. Von Hand bearbeiten geht; die Seite liest es genauso.

"""


def _pfade(basis: Path | None, custom: Path | None) -> tuple[Path, Path]:
    if basis is None:
        return engine.RULES_PATH, custom if custom is not None else engine.RULES_CUSTOM_PATH
    return basis, custom


def laden(basis: Path | None = None, custom: Path | None = None) -> list[dict]:
    basis, custom = _pfade(basis, custom)
    return engine.rohe_regeln(basis, custom)


def custom_lesen(pfad: Path) -> dict[str, dict]:
    if not pfad.exists():
        return {}
    roh = (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}).get("regeln") or {}
    return {str(k): dict(v or {}) for k, v in roh.items()}


def custom_schreiben(pfad: Path, alle: dict[str, dict]) -> None:
    pfad.write_text(KOPF + yaml.safe_dump({"regeln": alle}, allow_unicode=True,
                                          sort_keys=False), encoding="utf-8")


# ------------------------------------------------------------------ pruefen

def pruefen(e: dict) -> None:
    """Was hier durchgeht, laesst sich auch laden und fuehrt nichts Uebergrosses aus."""
    if not _ID.match(str(e.get("id") or "")):
        raise ValueError("Kennung nur aus Kleinbuchstaben, Ziffern und Bindestrich")
    if not str(e.get("name") or "").strip():
        raise ValueError("Name fehlt")
    m = e.get("match") or {}
    if not m:
        raise ValueError("Mindestens eine Bedingung — eine Regel ohne Bedingung fängt jede Buchung")
    _bedingungen(m)
    if not (e.get("set") or e.get("split")):
        raise ValueError("Die Regel setzt nichts — mindestens eine Kategorie angeben")


def _bedingungen(m: dict) -> None:
    for k, v in m.items():
        if k not in engine.MATCHERS:
            raise ValueError(f"unbekannte Bedingung {k}")
        if k in ("any_of", "all_of", "none_of"):
            for sub in v or []:
                _bedingungen(sub)
        elif k in _BETRAEGE and not (isinstance(v, list) and len(v) == 2):
            raise ValueError(f"{k} braucht zwei Beträge")
        elif k == "sign" and v not in ("+", "-"):
            raise ValueError("Richtung ist + oder -")
        elif k == "regex":
            try:
                re.compile(v)
            except re.error as fehler:
                raise ValueError(f"regex: {fehler}") from None


def _norm(v: Any, key: str = "") -> Any:
    """Vergleichsform, damit "c24" und ["c24"] oder 12 und 12.0 gleich sind."""
    if isinstance(v, dict):
        return {k: _norm(x, k) for k, x in v.items() if x not in (None, "", [], {})}
    if key in _LISTEN:
        return tuple(str(x) for x in engine._as_list(v))
    if key in _BETRAEGE:
        return tuple(round(float(x) * 100) for x in v)
    if isinstance(v, list):
        return tuple(_norm(x) for x in v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


# ---------------------------------------------------------------- Formular

def _liste(wert: Any) -> list[str]:
    return [x.strip() for x in str(wert or "").split(",") if x.strip()]


def aus_formular(alt: dict | None, f: dict) -> dict:
    """Die Formularfelder in eine Regel, alles andere von `alt` bleibt stehen."""
    e = {k: v for k, v in (alt or {}).items() if k != "herkunft"}
    match = dict(e.get("match") or {})
    setz = dict(e.get("set") or {})

    for k in ("text", "text_all", "counterparty"):
        match[k] = _liste(f.get(k)) or None
    konten = _liste(f.get("account"))
    match["account"] = (konten[0] if len(konten) == 1 else konten) or None
    match["sign"] = f.get("sign") or None
    von, bis = f.get("betrag_von_cents"), f.get("betrag_bis_cents")
    if von is None and bis is None:
        match["amount_abs_between"] = None
    else:
        match["amount_abs_between"] = [abs(int(von or 0)) / 100,
                                       abs(int(bis)) / 100 if bis is not None else OBEN]
    for feld, key in (("datum_von", "date_from"), ("datum_bis", "date_to")):
        wert = str(f.get(feld) or "").strip()
        if wert and not _DATUM.match(wert):
            raise ValueError(f"Datum {wert}: bitte als 2026-01-01")
        match[key] = wert or None

    for k in ("mgmt", "tax", "property", "note"):
        setz[k] = str(f.get(k) or "").strip() or None

    e["match"] = {k: v for k, v in match.items() if v is not None}
    e["set"] = {k: v for k, v in setz.items() if v is not None}
    e["name"] = str(f.get("name") or "").strip()
    prio = str(f.get("priority") or "").strip()
    try:
        e["priority"] = int(prio) if prio else int(e.get("priority", 100))
    except ValueError:
        raise ValueError("Priorität ist eine ganze Zahl — kleiner gewinnt") from None
    return e


def neue_kennung(name: str, vorhanden: set[str]) -> str:
    grund = str(name or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        grund = grund.replace(a, b)
    grund = re.sub(r"[^a-z0-9]+", "-", grund).strip("-") or "regel"
    kennung, n = grund, 2
    while kennung in vorhanden:
        kennung, n = f"{grund}-{n}", n + 1
    return kennung


def entwurf(f: dict, basis: Path | None = None,
            custom: Path | None = None) -> tuple[dict, list[dict]]:
    """Die Regel aus dem Formular und das ganze Regelwerk, wie es danach aussaehe."""
    alle = laden(basis, custom)
    rid = str(f.get("id") or "").strip()
    if f.get("neu"):
        rid = neue_kennung(f.get("name"), {r["id"] for r in alle})
        alt = None
    else:
        alt = next((r for r in alle if r["id"] == rid), None)
        if alt is None:
            raise ValueError(f"unbekannte Regel {rid}")
    e = aus_formular(alt, f)
    e["id"] = rid
    pruefen(e)
    return e, [r for r in alle if r["id"] != rid] + [e]


# -------------------------------------------------------------- schreiben

def speichern(e: dict, basis: Path | None = None, custom: Path | None = None) -> dict:
    """Nur, was von rules.yaml abweicht; eine Regel wie in der Basis loescht die Kopie."""
    basis, custom = _pfade(basis, custom)
    pruefen(e)
    rid = e["id"]
    grund = {r.get("id"): r for r in engine.rohe_regeln(basis, None)}
    eintrag = {k: e[k] for k in ("name", "priority", "match", "set")
               if e.get(k) not in (None, "", {})}
    if rid in grund:
        eintrag = {k: v for k, v in eintrag.items()
                   if _norm(v, k) != _norm(grund[rid].get(k), k)}

    vorher = custom.read_text(encoding="utf-8") if custom.exists() else None
    alle = custom_lesen(custom)
    if eintrag:
        alle[rid] = eintrag
    else:
        alle.pop(rid, None)
    custom_schreiben(custom, alle)
    try:
        engine.load_rules(basis, custom)
    except (ValueError, OSError) as fehler:
        if vorher is None:
            custom.unlink(missing_ok=True)
        else:
            custom.write_text(vorher, encoding="utf-8")
        raise ValueError(str(fehler)) from None
    return eintrag


def entfernen(rid: str, basis: Path | None = None, custom: Path | None = None) -> None:
    basis, custom = _pfade(basis, custom)
    grund_ids = {r.get("id") for r in engine.rohe_regeln(basis, None)}
    alle = custom_lesen(custom)
    if rid in grund_ids:
        alle[rid] = {"entfernt": True}
    elif rid in alle:
        alle.pop(rid)
    else:
        raise ValueError(f"unbekannte Regel {rid}")
    custom_schreiben(custom, alle)


# ---------------------------------------------------------------- Anzeige

def _eur(wert: Any) -> str:
    return f"{float(wert):,.2f}".replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


def beschreibung(m: dict) -> str:
    """Eine Regel in einer Zeile, so wie man sie liest."""
    teile = []
    for k, v in m.items():
        werte = [str(x) for x in engine._as_list(v)]
        if k == "text":
            teile.append(" oder ".join(f"„{x}“" for x in werte))
        elif k == "text_all":
            teile.append(" und ".join(f"„{x}“" for x in werte))
        elif k == "counterparty":
            teile.append("Gegenpartei " + " oder ".join(f"„{x}“" for x in werte))
        elif k == "account":
            teile.append("Konto " + ", ".join(werte))
        elif k == "sign":
            teile.append("nur Eingänge" if v == "+" else "nur Ausgänge")
        elif k in _BETRAEGE:
            lo, hi = v
            art = "Betrag" if k == "amount_abs_between" else "Betrag mit Vorzeichen"
            teile.append(f"{art} ab {_eur(lo)}" if float(hi) >= OBEN
                         else f"{art} {_eur(lo)}–{_eur(hi)}")
        elif k == "date_from":
            teile.append(f"ab {v}")
        elif k == "date_to":
            teile.append(f"bis {v}")
        else:
            teile.append(k)
    return " · ".join(teile) or "—"


def formwerte(e: dict) -> dict:
    m, s = e.get("match") or {}, e.get("set") or {}

    def liste(d: dict, k: str) -> str:
        return ", ".join(str(x) for x in engine._as_list(d.get(k)))

    von = bis = None
    if m.get("amount_abs_between"):
        lo, hi = m["amount_abs_between"]
        von = round(float(lo) * 100) or None
        bis = None if float(hi) >= OBEN else round(float(hi) * 100)
    weitere = ([k for k in m if k not in MATCH_FELDER]
               + [f"{k}: {s[k]}" for k in s if k not in SET_FELDER]
               + (["Aufteilung"] if e.get("split") else []))
    return {
        "name": e.get("name") or e.get("id") or "", "priority": int(e.get("priority", 100)),
        "text": liste(m, "text"), "text_all": liste(m, "text_all"),
        "counterparty": liste(m, "counterparty"), "account": liste(m, "account"),
        "sign": m.get("sign") or "", "betrag_von_cents": von, "betrag_bis_cents": bis,
        "datum_von": str(m.get("date_from") or ""), "datum_bis": str(m.get("date_to") or ""),
        "mgmt": s.get("mgmt") or "", "tax": s.get("tax") or "",
        "property": s.get("property") or "",
        "note": s.get("note") or "", "weitere": weitere, "herkunft": e.get("herkunft"),
    }


def vorlage_aus_buchung(t: dict) -> dict:
    """Eine neue Regel, vorbelegt aus einer Buchung.

    Gesucht wird im Text, nicht in der Gegenpartei: counterparty_norm ist
    normalisiert (Rechtsform weg), und ein daraus kopierter Name faende sich
    dort nicht wieder. Der volle Text enthaelt ihn immer.
    """
    stichwort = (t.get("counterparty") or "").strip()
    if not stichwort:
        stichwort = " ".join(str(t.get("purpose") or t.get("raw_text") or "").split()[:3])
    stichwort = stichwort.replace(",", " ").strip()
    return {
        "name": stichwort, "priority": 100, "text": stichwort, "text_all": "",
        "counterparty": "", "account": t["account_id"],
        "sign": "-" if t["amount_cents"] < 0 else "+",
        "betrag_von_cents": None, "betrag_bis_cents": None, "datum_von": "", "datum_bis": "",
        "mgmt": t.get("mgmt") or "", "tax": t.get("tax") or "",
        "property": t.get("property_id") or "", "note": "",
        "weitere": [], "herkunft": None,
    }
