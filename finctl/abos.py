"""Abos mit Termin -- angelegt, geteilt und eingesammelt im Frontend.

Der Kostenmedian stimmt im Jahresschnitt und ist in jedem einzelnen Monat
falsch. Wann etwas abgeht, ist eine andere Frage als wieviel -- und es ist die
Frage, die die Tabellenkalkulation vorher beantwortet hat.

ZWEI DATEIEN, dasselbe Overlay-Muster wie ueberall: `config/abos.yaml` ist von
Hand geschrieben und traegt die Begruendung, `config/abos_custom.yaml` schreibt
die Seite `/abos`. Beim Lesen gewinnt die zweite, Feld fuer Feld; die erste
wird nie angefasst, sonst waere die Begruendung beim ersten Klick weg.

DER DOPPELZAEHL-SCHUTZ ist der eigentliche Inhalt. Ein Abo ERSETZT seine
Buchungen in der Prognose, statt neben ihnen zu stehen. Welche das sind,
entscheidet der Eigentuemer, indem er sie auf der Seite ZUORDNET -- keine
Gegenpartei zum Eintippen, die er nirgends sehen kann, sondern Buchungen zum
Anhaken. Gemerkt wird ihr `dedup_hash`, derselbe Schluessel, auf dem
`overrides.yaml` jede Handentscheidung ueber einen Neuaufbau rettet.

GETEILT heisst: eine BESETZUNG als Vorlage und ZEITRAEUME, die beim Anlegen
eine Kopie davon bekommen. Aendert sich die Besetzung, bleibt ein alter
Zeitraum, wie er war -- wer 2026 mitgezahlt hat, hat 2026 mitgezahlt.
Eingesammelt wird nicht abgehakt, sondern abgeglichen: Eingaenge mit dem
Namen einer Person, in der Kategorie des Abos, rund um den Zeitraum.
"""

from __future__ import annotations

import itertools
import os
import re
import sqlite3
from datetime import date
from pathlib import Path

import yaml

from finctl.ingest.base import normalize_counterparty
from finctl.kalender import plus_monate
from finctl.ledger.db import format_eur, parse_de_amount
from finctl.pfade import CONFIG_DIR

# ZWEI ARTEN, EINE MASCHINE. Versicherungen verhalten sich wie Abos --
# Termin, Takt, zugeordnete Buchungen ersetzen den Median, teilen --, stehen
# aber auf einer eigenen Seite und in eigenen Dateien, wie vorher in der
# Tabellenkalkulation. Je Art: Basisdatei, Seitendatei, Schluessel darin.
ARTEN = {
    "abo": ("abos.yaml", "abos_custom.yaml", "abos"),
    "versicherung": ("versicherungen.yaml", "versicherungen_custom.yaml",
                     "versicherungen"),
}


def dateien(art: str = "abo") -> tuple[Path, Path]:
    basis, custom, _ = ARTEN[art]
    return CONFIG_DIR / basis, CONFIG_DIR / custom

TAKTE = (1, 2, 3, 4, 6, 12)
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
_MONAT = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

CUSTOM_KOPF = """# Auf der Seite /{seite} angelegt und geaendert.
#
# Ueberschreibt config/{basis} Feld fuer Feld, je Kennung. Dort steht,
# WARUM ein Vertrag erklaert werden muss; hier steht, was im Frontend eingetragen
# wurde: Betrag, Termin, Besetzung, Zeitraeume. `entfernt: true` blendet ein
# Eintrag aus der Basisdatei aus, ohne deren Begruendung zu loeschen.
#
# Betraege in Cent. Monate als JJJJ-MM.

"""


# ------------------------------------------------------------------ laden

def laden(basis: Path | None = None, custom: Path | None = None,
          art: str = "abo") -> list[dict]:
    """Beide Dateien, zusammengefuehrt und in eine Form gebracht."""
    basis, custom = basis or dateien(art)[0], custom or dateien(art)[1]
    grund = _roh_basis(basis, art)
    eigen = _roh_custom(custom, art)
    out = []
    ids = list(grund) + [k for k in eigen if k not in grund]
    for abo_id in ids:
        roh = {**grund.get(abo_id, {}), **eigen.get(abo_id, {}), "id": abo_id}
        if roh.get("entfernt"):
            continue
        a = normalisieren(roh)
        a["herkunft"] = ("eigen" if abo_id not in grund
                         else "geaendert" if abo_id in eigen else "basis")
        a["art"] = art
        out.append(a)
    return out


def alle_vertraege() -> list[dict]:
    """Abos und Versicherungen zusammen -- was die Prognose ersetzt.

    Eine Kennung in beiden Dateien waere derselbe Posten zweimal, oder zwei
    Posten, von denen die Seite nur einen aendern kann. Das faellt beim
    Laden auf statt im Kontostand.
    """
    out = [a for art in ARTEN for a in laden(art=art)]
    doppelt = sorted({a["id"] for a in out if sum(b["id"] == a["id"] for b in out) > 1})
    if doppelt:
        raise ValueError(f"Kennung in Abos und Versicherungen: {', '.join(doppelt)}")
    return out


def _roh_basis(pfad: Path, art: str = "abo") -> dict[str, dict]:
    if not pfad.exists():
        return {}
    spec = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
    return {str(e["id"]): dict(e) for e in spec.get(ARTEN[art][2]) or [] if e.get("id")}


def _roh_custom(pfad: Path, art: str = "abo") -> dict[str, dict]:
    if not pfad.exists():
        return {}
    spec = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
    return {str(k): dict(v or {}) for k, v in (spec.get(ARTEN[art][2]) or {}).items()}


def normalisieren(roh: dict) -> dict:
    """Eine Form fuer beide Quellen, mit geprueften Feldern.

    Ein unvollstaendiger Eintrag faellt auf, statt still zu wirken: ein Abo
    ohne Termin waere ein Betrag ohne Datum und damit genau das, was der
    Median schon kann.
    """
    fehlend = [f for f in ("id", "name", "kategorie", "konto", "takt", "faellig")
               if roh.get(f) in (None, "")]
    if roh.get("betrag_cents") is None and roh.get("betrag") is None:
        fehlend.append("betrag")
    if fehlend:
        raise ValueError(f"Abo {roh.get('id') or '?'}: es fehlt {', '.join(fehlend)}")
    takt = int(roh["takt"])
    if takt not in TAKTE:
        raise ValueError(f"Abo {roh['id']}: Takt {takt} ist kein Teiler des Jahres")
    betrag = (int(roh["betrag_cents"]) if roh.get("betrag_cents") is not None
              else _cents(roh["betrag"]))
    if betrag >= 0:
        # Ein vergessenes Minus machte auf /planung aus einer Rate eine
        # Einnahme. Hier kann es das nicht geben: ein Vertrag mit Termin ist
        # immer Geld raus, Rueckzahlungen der Mitzahler laufen getrennt.
        raise ValueError(f"{roh['id']}: Betrag ist eine Ausgabe, also negativ")

    geteilt = roh.get("geteilt") or {}
    return {
        "id": str(roh["id"]),
        "name": str(roh["name"]),
        "kategorie": str(roh["kategorie"]),
        "konto": str(roh["konto"]),
        "betrag_cents": betrag,
        "takt": takt,
        "faellig": _datum(roh["faellig"]),
        "vorrat_bis": _datum(roh["vorrat_bis"]) if roh.get("vorrat_bis") else None,
        "gekuendigt_zum": (_datum(roh["gekuendigt_zum"])
                           if roh.get("gekuendigt_zum") else None),
        "weg": roh.get("weg") or "",
        "warum": (roh.get("warum") or "").strip(),
        "buchungen": [str(h) for h in roh.get("buchungen") or [] if h],
        "ignoriert": [str(h) for h in roh.get("ignoriert") or [] if h],
        "geteilt": {
            "besetzung": [_person(p) for p in geteilt.get("besetzung") or []],
            "zeitraeume": [_zeitraum(z, roh["id"])
                           for z in geteilt.get("zeitraeume") or []],
        },
    }


def _person(p: dict) -> dict:
    if not p.get("name"):
        raise ValueError("Person ohne Namen")
    anteil = (int(p["anteil_cents"]) if p.get("anteil_cents") is not None
              else _cents(p.get("anteil") or 0))
    if anteil < 0:
        raise ValueError(f"{p['name']}: negativer Anteil")
    seit = str(p.get("seit") or "")
    if seit and not _MONAT.match(seit):
        raise ValueError(f"{p['name']}: seit braucht JJJJ-MM")
    return {"name": str(p["name"]).strip(), "anteil_cents": anteil,
            "aliase": [str(a).strip() for a in p.get("aliase") or [] if str(a).strip()],
            "zahlungen": [str(h) for h in p.get("zahlungen") or [] if h],
            "seit": seit or None}


def _zeitraum(z: dict, abo_id: str) -> dict:
    for feld in ("von", "bis", "einsammeln"):
        if not _MONAT.match(str(z.get(feld) or "")):
            raise ValueError(f"Abo {abo_id}: Zeitraum braucht {feld} als JJJJ-MM")
    if str(z["bis"]) < str(z["von"]):
        raise ValueError(f"Abo {abo_id}: Zeitraum endet vor seinem Beginn")
    return {"von": str(z["von"]), "bis": str(z["bis"]),
            "einsammeln": str(z["einsammeln"]),
            "personen": [_person(p) for p in z.get("personen") or []]}


def _cents(wert) -> int:
    # Zahl oder Text: in der Datei steht -84.00, von Hand getippt gern
    # "-84,00". Beides meint dasselbe.
    if isinstance(wert, (int, float)):
        return int(round(float(wert) * 100))
    return parse_de_amount(str(wert))


def _datum(wert) -> date:
    return wert if isinstance(wert, date) else date.fromisoformat(str(wert))


# -------------------------------------------------------------- schreiben

def speichern(abo: dict, basis: Path | None = None, custom: Path | None = None,
              art: str = "abo") -> dict:
    """Ein Abo aus dem Frontend ablegen.

    Geprueft wird VOR dem Schreiben, mit derselben Funktion, die beim Lesen
    prueft: was hier durchgeht, laesst sich auch wieder laden. Sonst koennte
    ein Klick die Datei so hinterlassen, dass keine Prognose mehr rechnet.
    """
    basis, custom = basis or dateien(art)[0], custom or dateien(art)[1]
    abo_id = str(abo.get("id") or "").strip()
    if not _ID.match(abo_id):
        raise ValueError("Kennung nur aus Kleinbuchstaben, Ziffern und Bindestrich")
    eintrag = {
        "name": abo.get("name"), "kategorie": abo.get("kategorie"),
        "konto": abo.get("konto"), "betrag_cents": abo.get("betrag_cents"),
        "takt": abo.get("takt"), "faellig": str(abo.get("faellig") or ""),
        "vorrat_bis": str(abo["vorrat_bis"]) if abo.get("vorrat_bis") else None,
        "gekuendigt_zum": (str(abo["gekuendigt_zum"])
                           if abo.get("gekuendigt_zum") else None),
        "weg": abo.get("weg") or None,
        "warum": abo.get("warum") or None,
        "buchungen": sorted({str(h) for h in abo.get("buchungen") or [] if h}) or None,
        "ignoriert": sorted({str(h) for h in abo.get("ignoriert") or [] if h}) or None,
        "geteilt": None,
    }
    geteilt = abo.get("geteilt") or {}
    besetzung = [_person(p) for p in geteilt.get("besetzung") or []]
    zeitraeume = [_zeitraum(z, abo_id) for z in geteilt.get("zeitraeume") or []]
    if besetzung or zeitraeume:
        eintrag["geteilt"] = {
            "besetzung": [_person_yaml(p) for p in besetzung],
            "zeitraeume": [{**{k: z[k] for k in ("von", "bis", "einsammeln")},
                            "personen": [_person_yaml(p) for p in z["personen"]]}
                           for z in zeitraeume],
        }
    eintrag = {k: v for k, v in eintrag.items() if v not in (None, "", [])}
    neu = normalisieren({**eintrag, "id": abo_id})     # wirft, wenn unvollstaendig

    # NUR WAS VON DER BASIS ABWEICHT. Die Seite schickt immer das ganze Abo;
    # alles davon abzulegen hiesse, jedes Feld der Basisdatei einzufrieren.
    # Genau das ist passiert: nach einer Aenderung der Anteile stand der
    # alte Betrag, das alte Konto und der alte Termin in der Kopie, und als
    # Microsoft 365 und OneDrive in abos.yaml zusammengefuehrt wurden, kam
    # davon nichts mehr an. Ein Feld, das der Basis gleicht, bleibt deshalb
    # weg -- und folgt ihr, wenn sie sich aendert.
    grund = _roh_basis(basis, art).get(abo_id)
    alle = _roh_custom(custom, art)
    if grund:
        alt = normalisieren({**grund, "id": abo_id})
        felder = {"betrag": "betrag_cents"}
        eintrag = {k: v for k, v in eintrag.items()
                   if neu[felder.get(k, k)] != alt[felder.get(k, k)]}
        if "geteilt" not in eintrag and neu["geteilt"] != alt["geteilt"]:
            eintrag["geteilt"] = {"besetzung": [], "zeitraeume": []}
        for feld in ("buchungen", "ignoriert"):
            if feld not in eintrag and set(neu[feld]) != set(alt[feld]):
                eintrag[feld] = []
    if eintrag:
        alle[abo_id] = eintrag
    else:
        alle.pop(abo_id, None)
    _schreiben(custom, alle, art)
    return eintrag


def _person_yaml(p: dict) -> dict:
    out = {"name": p["name"], "anteil_cents": p["anteil_cents"]}
    if p["aliase"]:
        out["aliase"] = p["aliase"]
    if p["zahlungen"]:
        out["zahlungen"] = sorted(set(p["zahlungen"]))
    if p.get("seit"):
        out["seit"] = p["seit"]
    return out


def entfernen(abo_id: str, basis: Path | None = None, custom: Path | None = None,
              art: str = "abo") -> None:
    """Ein Abo aus der Liste nehmen.

    Stammt es aus der Basisdatei, wird es nur ausgeblendet: deren Begruendung
    ist von Hand geschrieben und soll einen Fehlklick ueberleben.
    """
    basis, custom = basis or dateien(art)[0], custom or dateien(art)[1]
    alle = _roh_custom(custom, art)
    if abo_id in _roh_basis(basis, art):
        alle[abo_id] = {"entfernt": True}
    else:
        alle.pop(abo_id, None)
    _schreiben(custom, alle, art)


def _schreiben(pfad: Path, alle: dict, art: str = "abo") -> None:
    # Erst in eine Nachbardatei, dann umbenennen: bricht der Schreibvorgang ab,
    # bleibt die alte Datei ganz und nicht halb.
    neu = pfad.with_suffix(pfad.suffix + ".neu")
    kopf = CUSTOM_KOPF.format(seite=ARTEN[art][2], basis=ARTEN[art][0])
    neu.write_text(kopf + yaml.safe_dump({ARTEN[art][2]: alle}, allow_unicode=True,
                                         sort_keys=False),
                   encoding="utf-8")
    os.replace(neu, pfad)


# --------------------------------------------------------------- Prognose

def ausschluesse(conn, abos: list[dict]) -> list[dict]:
    """Welche Buchungen die erklaerten Vertraege in der Prognose ersetzen.

    NICHT NUR DIE ANGEHAKTEN, sondern ihre ganze Reihe -- siehe
    `finctl/ledger/reihe.py`. Ein Haken ist ein Beispiel, keine Inventur:
    wer einen Vertrag erklaert, meint alle seine Buchungen, nicht die paar,
    die er angeklickt hat. Vorher blieb der Rest im Median stehen, waehrend
    der Vertrag seinen Termin zusaetzlich buchte.

    `conn` darf None sein; dann bleibt es bei den angehakten Buchungen.
    """
    from finctl.ledger import reihe as _reihe

    alle = {h for a in abos for h in a["buchungen"]}
    if not alle:
        return []
    if conn is not None:
        for a in abos:
            # Je Vertrag mit SEINEM Betrag: derselbe Empfaenger kann in
            # derselben Kategorie zweierlei sein, und der Betrag trennt das.
            alle |= set(_reihe.hashes(conn, a["buchungen"], a.get("betrag_cents")))
    return [{"dedup_hashes": sorted(alle)}]


def posten(abos: list[dict], konto: str, *, ab: date, bis: date) -> list[dict]:
    """Die faelligen Termine eines Kontos im Fenster.

    Vom erklaerten Termin aus in beide Richtungen: `faellig` ist der NAECHSTE
    Termin, nicht der erste. Nach einer Kuendigung kommt nichts mehr, und vor
    dem Ende eines Vorrats auch nicht -- der ist bezahlt.
    """
    out = []
    for a in abos:
        if a["konto"] != konto:
            continue
        termin = a["faellig"]
        while termin > bis:
            termin = plus_monate(termin, -a["takt"])
        while termin < ab:
            termin = plus_monate(termin, a["takt"])
        while termin <= bis:
            gekuendigt = a["gekuendigt_zum"] and termin > a["gekuendigt_zum"]
            vorrat = a["vorrat_bis"] and termin < a["vorrat_bis"]
            if not gekuendigt and not vorrat:
                out.append({"abo": a, "faellig": termin,
                            "betrag_cents": a["betrag_cents"]})
            termin = plus_monate(termin, a["takt"])
    return sorted(out, key=lambda p: p["faellig"])


def rueckzahlungen(conn: sqlite3.Connection, abos: list[dict], konto: str, *,
                   ab: date, bis: date, heute: date | None = None) -> list[dict]:
    """Was von Mitzahlern noch erwartet wird, im Einsammel-Monat.

    Nur der OFFENE Teil. Ist das Geld da, verschwindet der Posten von selbst --
    sonst stuende eine laengst eingegangene Rueckzahlung weiter als erwartet
    in der Prognose. Ein verstrichener Einsammel-Monat rueckt in den ersten
    Prognosemonat: das Geld wird ja weiter erwartet, nur spaeter.
    """
    out = []
    for a in abos:
        if a["konto"] != konto:
            continue
        for z in abrechnung(conn, a, heute=heute):
            if z["offen"] <= 0:
                continue
            monat = _monat_erster(z["einsammeln"])
            monat = max(monat, ab.replace(day=1))
            if monat <= bis:
                out.append({"abo": a, "monat": monat, "cents": z["offen"],
                            "zeitraum": z})
    return out


# ------------------------------------------------------------- Abrechnung

def abrechnung(conn: sqlite3.Connection, abo: dict,
               heute: date | None = None) -> list[dict]:
    """Je Zeitraum: wer schuldet was, was kam zurueck, was ist offen.

    ZWEI WEGE, und der von Hand gewinnt. Eine ZUGEORDNETE Zahlung zaehlt fuer
    genau die Person und den Zeitraum, dem sie zugeordnet ist -- egal in
    welcher Kategorie sie steht und wann sie kam: Eine Mitnutzerin hat 2024 ueber PayPal
    gezahlt, unter konsum/sonstiges und weit ausserhalb jedes Fensters.
    AUTOMATISCH zugeordnet wird nur, was niemand von Hand vergeben hat: ein
    Eingang mit dem Namen der Person, in der Kategorie des Abos oder ohne
    Kategorie, rund um den Zeitraum. Ein falscher automatischer Treffer wird
    verworfen und bleibt verworfen.

    Automatisches wird je Person gedeckelt: was ueber den Anteil hinausgeht,
    zaehlt fuer den naechsten Zeitraum. Von Hand Zugeordnetes nie -- wer es
    einem Zeitraum zuordnet, meint diesen.
    """
    heute = heute or date.today()
    zeitraeume = sorted(abo["geteilt"]["zeitraeume"], key=lambda z: z["von"])
    if not zeitraeume:
        return []

    von_hand = {h for z in zeitraeume for p in z["personen"] for h in p["zahlungen"]}
    verworfen = set(abo.get("ignoriert") or [])
    zugeordnet = _nach_hash(conn, von_hand)
    eingaenge = [e for e in _eingaenge(conn, abo["kategorie"])
                 if e["hash"] not in von_hand and e["hash"] not in verworfen]
    vergeben: set[str] = set()
    uebertrag: dict[str, list[dict]] = {}
    out = []
    for z in zeitraeume:
        von = plus_monate(_monat_erster(min(z["von"], z["einsammeln"])), -3)
        bis = plus_monate(_monat_erster(max(z["bis"], z["einsammeln"])), 4)
        personen = []
        for p in z["personen"]:
            soll = p["anteil_cents"]
            schluessel = _schluessel(p)
            genutzt = [{**zugeordnet[h], "art": "zugeordnet"}
                       for h in p["zahlungen"] if h in zugeordnet]
            zurueck = sum(t["cents"] for t in genutzt)
            automatisch = list(uebertrag.pop(p["name"], []))
            for e in eingaenge:
                if e["hash"] in vergeben or not (von <= e["datum"] < bis):
                    continue
                if any(s in e["suchtext"] for s in schluessel):
                    automatisch.append({k: e[k] for k in ("id", "hash", "datum", "cents", "konto")}
                                       | {"art": "automatisch"})
                    vergeben.add(e["hash"])
            for t in sorted(automatisch, key=lambda t: t["datum"]):
                if zurueck >= soll:
                    uebertrag.setdefault(p["name"], []).append(t)
                    continue
                zurueck += t["cents"]
                genutzt.append(t)
            personen.append({
                "name": p["name"], "aliase": p["aliase"],
                "soll": p["anteil_cents"], "zurueck": zurueck,
                "offen": max(soll - zurueck, 0),
                "treffer": sorted(genutzt, key=lambda t: t["datum"])})
        offen = sum(p["offen"] for p in personen)
        faellig = _monat_erster(z["einsammeln"]) <= heute.replace(day=1)
        out.append({
            "von": z["von"], "bis": z["bis"], "einsammeln": z["einsammeln"],
            "personen": personen,
            "soll": sum(p["soll"] for p in personen),
            "zurueck": sum(p["zurueck"] for p in personen),
            "offen": offen,
            "status": ("abgerechnet" if offen == 0
                       else "faellig" if faellig else "offen"),
        })

    # Vorschlaege je Person: Eingaenge mit ihrem Namen, die noch nirgends
    # zaehlen -- in jeder Kategorie und zu jedem Datum. Genau die, die der
    # automatische Abgleich nicht anfasst.
    gezaehlt = von_hand | verworfen | {t["hash"] for z in out for p in z["personen"]
                                       for t in p["treffer"]}
    alle = _alle_eingaenge(conn)
    for z, roh in zip(out, zeitraeume, strict=True):
        for p, rp in zip(z["personen"], roh["personen"], strict=True):
            schluessel = _schluessel(rp)
            p["vorschlaege"] = [
                {k: e[k] for k in ("hash", "datum", "cents", "konto", "gegenpartei", "kategorie")}
                for e in alle
                if e["hash"] not in gezaehlt and any(s in e["suchtext"] for s in schluessel)][:5]
    return out


def laufend(conn: sqlite3.Connection, abo: dict,
            heute: date | None = None) -> list[dict]:
    """Wer LAUFEND mitzahlt: je Takt seinen Anteil, ohne Zeitraum.

    Ein Zeitraum passt zum Vorstrecken und spaeter Einsammeln. Wer jeden
    Monat seinen Teil ueberweist, bekaeme dafuer jeden Monat einen eigenen
    -- deshalb traegt die Person in der Besetzung ein `seit`, und gezaehlt
    wird fortlaufend: Soll ist der Anteil je faelligem Takt seit diesem
    Monat, zurueck ist, was zugeordnet oder mit ihrem Namen eingegangen ist.
    Automatisch nur GENAU DER ANTEIL: wer mitzahlt, ueberweist oft auch
    anderes, und 200 Euro fuer etwas ganz anderes sind kein Handyanteil.

    In die Prognose geht davon nichts eigens: der Eingang steht in der
    Kategorie des Abos und zaehlt dort im Median mit, wie jeder andere.
    """
    heute = heute or date.today()
    personen = [p for p in abo["geteilt"]["besetzung"] if p.get("seit")]
    if not personen:
        return []
    anderswo = {h for z in abo["geteilt"]["zeitraeume"]
                for p in z["personen"] for h in p["zahlungen"]}
    von_hand = {h for p in personen for h in p["zahlungen"]}
    verworfen = set(abo.get("ignoriert") or [])
    zugeordnet = _nach_hash(conn, von_hand)
    eingaenge = [e for e in _eingaenge(conn, abo["kategorie"])
                 if e["hash"] not in von_hand | verworfen | anderswo]
    alle = _alle_eingaenge(conn)
    out = []
    for p in personen:
        beginn = _monat_erster(p["seit"])
        monate = (heute.year - beginn.year) * 12 + heute.month - beginn.month + 1
        soll = p["anteil_cents"] * max(0, -(-monate // abo["takt"]))
        schluessel = _schluessel(p)
        treffer = [{**zugeordnet[h], "art": "zugeordnet"}
                   for h in p["zahlungen"] if h in zugeordnet]
        treffer += [{k: e[k] for k in ("id", "hash", "datum", "cents", "konto")}
                    | {"art": "automatisch"}
                    for e in eingaenge
                    if e["datum"] >= beginn and e["cents"] == p["anteil_cents"]
                    and any(k in e["suchtext"] for k in schluessel)]
        gezaehlt = von_hand | verworfen | anderswo | {t["hash"] for t in treffer}
        zurueck = sum(t["cents"] for t in treffer)
        out.append({
            "name": p["name"], "seit": p["seit"], "soll": soll, "zurueck": zurueck,
            "offen": max(soll - zurueck, 0),
            "treffer": sorted(treffer, key=lambda t: t["datum"], reverse=True),
            "vorschlaege": [
                {k: e[k] for k in ("hash", "datum", "cents", "konto", "gegenpartei", "kategorie")}
                for e in alle
                if e["hash"] not in gezaehlt and any(k in e["suchtext"] for k in schluessel)][:5]})
    return out


def _schluessel(p: dict) -> list[str]:
    return [k for k in (normalize_counterparty(n) for n in [p["name"], *p["aliase"]])
            if k and len(k) >= 3]


def _eingaenge(conn: sqlite3.Connection, kategorie: str) -> list[dict]:
    """Eingaenge, die automatisch eine Rueckzahlung sein koennen.

    In der Kategorie des Abos ODER noch ohne Kategorie: eine Rueckzahlung, die
    heute eingelesen wurde, ist noch nicht einsortiert und soll trotzdem
    zaehlen. Umbuchungen nie -- die Auszahlung von PayPal aufs Girokonto
    traegt denselben Betrag wie die Rueckzahlung und waere sonst ein zweites
    Mal Geld zurueck.
    """
    rows = conn.execute(
        """SELECT t.id, t.dedup_hash, t.booking_date, t.account_id, s.amount_cents,
                  t.counterparty_norm, t.raw_text
           FROM   transactions t
           JOIN   splits s ON s.transaction_id = t.id
           WHERE  s.amount_cents > 0
             AND  (s.mgmt_category_id = ? OR s.mgmt_category_id IS NULL)
           ORDER  BY t.booking_date, t.id""", (kategorie,)).fetchall()
    return [{"id": r["id"], "hash": r["dedup_hash"],
             "datum": date.fromisoformat(r["booking_date"]),
             "konto": r["account_id"], "cents": r["amount_cents"],
             "suchtext": (r["counterparty_norm"] or "")
                         + (normalize_counterparty(r["raw_text"]) or "")}
            for r in rows]


def _alle_eingaenge(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT t.dedup_hash, t.booking_date, t.account_id, t.amount_cents,
                  t.counterparty_norm, t.raw_text, COALESCE(t.counterparty, '') AS gegenpartei,
                  s.mgmt_category_id AS kategorie
           FROM   transactions t JOIN splits s ON s.transaction_id = t.id AND s.seq = 0
           WHERE  t.amount_cents > 0
           ORDER  BY t.booking_date DESC""").fetchall()
    return [{"hash": r["dedup_hash"], "datum": date.fromisoformat(r["booking_date"]),
             "konto": r["account_id"], "cents": r["amount_cents"],
             "gegenpartei": r["gegenpartei"], "kategorie": r["kategorie"],
             "suchtext": (r["counterparty_norm"] or "")
                         + (normalize_counterparty(r["raw_text"]) or "")}
            for r in rows]


def _nach_hash(conn: sqlite3.Connection, hashes: set[str]) -> dict[str, dict]:
    if not hashes:
        return {}
    rows = conn.execute(
        f"""SELECT id, dedup_hash, booking_date, account_id, amount_cents
            FROM transactions WHERE dedup_hash IN ({','.join('?' * len(hashes))})""",
        sorted(hashes)).fetchall()
    return {r["dedup_hash"]: {"id": r["id"], "hash": r["dedup_hash"],
                              "datum": date.fromisoformat(r["booking_date"]),
                              "cents": r["amount_cents"], "konto": r["account_id"]}
            for r in rows}


def eingaenge(conn: sqlite3.Connection, suche: str, limit: int = 25) -> list[dict]:
    """Eingaenge zur Suche -- fuer eine Rueckzahlung, die von Hand zugeordnet wird."""
    muster = f"%{suche.strip()}%"
    kompakt = f"%{normalize_counterparty(suche) or suche.strip()}%"
    return [dict(r) for r in conn.execute(
        """SELECT t.dedup_hash, t.booking_date, t.account_id, t.amount_cents,
                  COALESCE(t.counterparty, '') AS gegenpartei,
                  substr(t.raw_text, 1, 90) AS text, s.mgmt_category_id AS kategorie
           FROM   transactions t JOIN splits s ON s.transaction_id = t.id AND s.seq = 0
           WHERE  t.amount_cents > 0 AND (t.raw_text LIKE ? OR t.counterparty_norm LIKE ?)
           ORDER  BY t.booking_date DESC LIMIT ?""", (muster, kompakt, limit))]


# -------------------------------------------------------- Seite: Buchungen

_BUCHUNG_SQL = """SELECT t.id, t.dedup_hash, t.booking_date, t.account_id,
                         t.amount_cents, t.counterparty_norm,
                         COALESCE(t.counterparty, '') AS gegenpartei,
                         substr(t.raw_text, 1, 90) AS text,
                         s.mgmt_category_id AS kategorie
                  FROM   transactions t
                  JOIN   splits s ON s.transaction_id = t.id AND s.seq = 0"""


def belege(conn: sqlite3.Connection, abo: dict) -> list[dict]:
    """Die zugeordneten Buchungen, juengste zuerst."""
    if not abo["buchungen"]:
        return []
    platz = ",".join("?" * len(abo["buchungen"]))
    return [dict(r) for r in conn.execute(
        f"{_BUCHUNG_SQL} WHERE t.dedup_hash IN ({platz}) "
        f"ORDER BY t.booking_date DESC", abo["buchungen"])]


def vorschlaege(conn: sqlite3.Connection, abo: dict, limit: int = 10) -> list[dict]:
    """Neuere Abbuchungen derselben Gegenpartei in der Kategorie des Abos.

    Die naechste Verlaengerung erscheint hier, sobald sie importiert ist.
    Solange sie nicht zugeordnet ist, zaehlt sie in der Prognose zusaetzlich
    -- deshalb steht sie sichtbar da, statt still mitzulaufen.

    DIE KATEGORIE IST NOETIG, nicht nur die Gegenpartei: unter
    "APPLE.COM/BILL" laufen Crunchyroll, iCloud und Claude gleichzeitig, und
    ohne sie stand bei Crunchyroll jeden Monat "neue Buchung". Und nur NACH
    dem juengsten Beleg -- was davor lag, ist keine neue Verlaengerung.
    """
    belegt = belege(conn, abo)
    namen = sorted({b["counterparty_norm"] for b in belegt if b["counterparty_norm"]})
    if not namen:
        return []
    return [dict(r) for r in conn.execute(
        f"{_BUCHUNG_SQL} WHERE t.amount_cents < 0 "
        f"AND t.counterparty_norm IN ({','.join('?' * len(namen))}) "
        f"AND s.mgmt_category_id = ? AND t.booking_date > ? "
        f"AND t.dedup_hash NOT IN ({','.join('?' * len(abo['buchungen']))}) "
        f"ORDER BY t.booking_date DESC LIMIT ?",
        [*namen, abo["kategorie"], belegt[0]["booking_date"], *abo["buchungen"], limit])]


def fruehere(conn: sqlite3.Connection, abo: dict) -> list[dict]:
    """Aeltere Abbuchungen derselben Gegenpartei in der Kategorie des Abos.

    Wer ein Abo anlegt, hakt meist die juengste Buchung an; die Monate davor
    zaehlen dann weiter im Kategoriemedian UND als Abo, also doppelt. Die
    Vorschlaege oben zeigen nur Neueres, deshalb stehen die frueheren hier --
    ohne Grenze, weil jede fehlende doppelt zaehlt. Ignorierte bleiben weg.
    """
    belegt = belege(conn, abo)
    namen = sorted({b["counterparty_norm"] for b in belegt if b["counterparty_norm"]})
    if not namen:
        return []
    bekannt = [*abo["buchungen"], *abo["ignoriert"]]
    return [dict(r) for r in conn.execute(
        f"{_BUCHUNG_SQL} WHERE t.amount_cents < 0 "
        f"AND t.counterparty_norm IN ({','.join('?' * len(namen))}) "
        f"AND s.mgmt_category_id = ? AND t.booking_date < ? "
        f"AND t.dedup_hash NOT IN ({','.join('?' * len(bekannt))}) "
        f"ORDER BY t.booking_date DESC",
        [*namen, abo["kategorie"], belegt[0]["booking_date"], *bekannt])]


def buchungen(conn: sqlite3.Connection, suche: str, limit: int = 25) -> list[dict]:
    """Abbuchungen zur Suche, jede mit einem Takt-Vorschlag."""
    muster = f"%{suche.strip()}%"
    kompakt = f"%{normalize_counterparty(suche) or suche.strip()}%"
    rows = conn.execute(
        f"{_BUCHUNG_SQL} WHERE t.amount_cents < 0 "
        f"AND (t.raw_text LIKE ? OR t.counterparty_norm LIKE ?) "
        f"ORDER BY t.booking_date DESC LIMIT ?", (muster, kompakt, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["takt"] = takt_vorschlag(conn, r["counterparty_norm"], r["amount_cents"])
        out.append(d)
    return out


def takt_vorschlag(conn: sqlite3.Connection, gegenpartei: str | None,
                   betrag_cents: int) -> int | None:
    """Wie oft dieselbe Abbuchung bisher kam, als Takt.

    Median der Abstaende zwischen gleich hohen Buchungen derselben
    Gegenpartei, auf einen Teiler des Jahres gerundet. Eine einzige Buchung
    ergibt keinen Takt -- dann schlaegt die Seite nichts vor, statt zu raten.
    """
    if not gegenpartei:
        return None
    monate = sorted({r[0] for r in conn.execute(
        "SELECT substr(booking_date, 1, 7) FROM transactions "
        "WHERE counterparty_norm = ? AND amount_cents = ?",
        (gegenpartei, betrag_cents))})
    if len(monate) < 2:
        return None
    idx = [int(m[:4]) * 12 + int(m[5:7]) for m in monate]
    abstaende = sorted(b - a for a, b in itertools.pairwise(idx))
    gemessen = abstaende[len(abstaende) // 2]
    return min(TAKTE, key=lambda t: (abs(t - gemessen), t))


def preisabweichung(conn: sqlite3.Connection, abo: dict) -> dict | None:
    """Der juengste Beleg, wenn er anders hoch ist als der Betrag des Abos.

    Das ist zugleich die Pruefung eines Kaufs, der noch nicht im Auszug
    stand: bis er importiert und zugeordnet ist, ist der juengste Beleg der
    alte -- und der Unterschied bleibt sichtbar.
    """
    letzte = belege(conn, abo)
    if letzte and letzte[0]["amount_cents"] != abo["betrag_cents"]:
        return {"erwartet": abo["betrag_cents"], "gebucht": letzte[0]["amount_cents"],
                "datum": letzte[0]["booking_date"]}
    return None


# ------------------------------------------------------------------ Hilfen

def _monat_erster(jjjj_mm: str) -> date:
    return date(int(jjjj_mm[:4]), int(jjjj_mm[5:7]), 1)


def euro(cents: int) -> str:
    return format_eur(cents)
