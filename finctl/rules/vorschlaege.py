"""Regelvorschlaege ueber ein Sprachmodell -- ohne dass die App eines aufruft.

Der Ablauf geht ueber die Zwischenablage, und jeder Schritt liegt beim Nutzer:

1. `offene_texte` sammelt die Gegenparteien der offenen Buchungen, bereinigt
   um alles, was eine Nummer ist (IBAN, Betraege, Referenzen). Auf der Seite waehlt
   man ab, was nicht hinaus soll.
2. `prompt` baut daraus einen festen Auftrag samt der erlaubten Kategorien.
   Er wird kopiert und in Claude eingefuegt -- die App schickt nichts.
3. Die Antwort kommt als Tabelle `Suchtext ; Kategorie` zurueck.
   `antwort_lesen` nimmt sie tolerant auseinander, `pruefen` rechnet gegen
   das Hauptbuch, was jede Zeile faenge und wo sie einer Zuordnung
   widerspraeche.
4. `uebernehmen` legt angehakte Zeilen als gewoehnliche Textregeln an.

So bleibt es bei "KI schreibt nur Regeln": ausgefuehrt wird wie immer von
der Regel-Engine, deterministisch.
"""

from __future__ import annotations

import re
import sqlite3

from finctl.rules import engine

#: Wo die Regeln aus Vorschlaegen stehen: hinter den meisten eigenen, vor der
#: Grundschicht.
PRIORITAET = 80
#: Ein Betrag: Ziffer, Komma oder Punkt, Ziffer.
_BETRAG = re.compile(r"\d[.,]\d")
_TRENNER = re.compile(r"\s*[;|\t]\s*")


def _nummer(wort: str) -> bool:
    """IBAN, Betrag, Datum, Referenz: drei Ziffern oder mehr, oder ein Betrag.
    "O2" oder "3M" bleiben -- ein Name mit einer Ziffer verraet nichts."""
    return sum(c.isdigit() for c in wort) >= 3 or bool(_BETRAG.search(wort))


def bereinigt(text: str | None) -> str:
    """Der Text ohne alles, was eine Nummer ist -- auf 60 Zeichen."""
    return " ".join(w for w in str(text or "").split() if not _nummer(w))[:60]


def offene_texte(conn: sqlite3.Connection) -> list[dict]:
    """Je bereinigter Gegenpartei der offenen Buchungen: Text und Anzahl."""
    zaehler: dict[str, int] = {}
    for gegen, roh in conn.execute(
            "SELECT counterparty, raw_text FROM v_review_queue"):
        text = bereinigt(gegen) or bereinigt(roh)
        if len(text) >= 3:
            zaehler[text] = zaehler.get(text, 0) + 1
    return [{"text": t, "anzahl": n}
            for t, n in sorted(zaehler.items(), key=lambda x: (-x[1], x[0]))]


def prompt(kategorien: list[dict], texte: list[str], regeln_je: dict[str, int]) -> str:
    """Der Auftrag an das Sprachmodell. Nur Kategorien und die gewaehlten Texte."""
    zeilen = [
        "Ordne Buchungstexte von deutschen Kontoauszügen Kategorien zu.",
        "",
        "Antworte NUR mit einer Tabelle, eine Zeile je Vorschlag, ohne Kopfzeile:",
        "Suchtext ; Kategorie",
        "",
        "Regeln:",
        "- Suchtext ist ein kurzer, eindeutiger Teil des Buchungstexts, der auch",
        "  künftige Buchungen desselben Empfängers trifft (z. B. der Firmenname).",
        "- Kategorie ist GENAU eine Kennung aus der Liste unten, sonst nichts.",
        "- Bist du unsicher, lass die Zeile weg. Keine Erklärungen.",
        "",
        "Kategorien (Kennung – Name – vorhandene Regeln):",
    ]
    zeilen += [f"{k['id']} – {k['label']} – {regeln_je.get(k['id'], 0)}" for k in kategorien]
    zeilen += ["", "Buchungstexte:"] + [f"- {t}" for t in texte]
    return "\n".join(zeilen)


def antwort_lesen(text: str) -> list[dict]:
    """`Suchtext ; Kategorie` je Zeile. Aufzaehlungszeichen, Anfuehrungen und
    Markdown-Tabellen werden geduldet; was sich nicht lesen laesst, faellt weg."""
    out, gesehen = [], set()
    for zeile in str(text or "").splitlines():
        zeile = zeile.strip().strip("|").strip().lstrip("-*• ").strip()
        teile = [t.strip().strip("`\"'„“") for t in _TRENNER.split(zeile)]
        if len(teile) < 2 or not teile[0] or not teile[1]:
            continue
        suchtext, kategorie = teile[0][:60], teile[1].lower()
        if set(suchtext) <= set("-: ") or kategorie in ("kategorie", "---"):
            continue
        if (suchtext.lower(), kategorie) not in gesehen:
            gesehen.add((suchtext.lower(), kategorie))
            out.append({"text": suchtext, "kategorie": kategorie})
    return out


def pruefen(conn: sqlite3.Connection, vorschlaege: list[dict]) -> list[dict]:
    """Je Vorschlag: gibt es die Kategorie, wie viele offene Buchungen faengt
    er, und wie viele schon zugeordnete lagen in einer anderen Kategorie."""
    bekannt = {r[0] for r in conn.execute(
        "SELECT id FROM mgmt_categories WHERE active = 1")}
    offen = {r[0] for r in conn.execute("SELECT id FROM v_review_queue")}
    jetzt: dict[int, str] = {}
    for tx_id, kat in conn.execute(
            "SELECT transaction_id, mgmt_category_id FROM splits ORDER BY transaction_id, seq"):
        jetzt.setdefault(tx_id, kat)
    kontexte = engine.load_contexts(conn)
    out = []
    for v in vorschlaege:
        muster = engine.squash(v["text"])
        treffer = [tx.id for tx in kontexte if muster and muster in tx.haystack]
        anders = [i for i in treffer if i not in offen and jetzt.get(i)
                  and jetzt.get(i) != v["kategorie"]]
        out.append({**v, "bekannt": v["kategorie"] in bekannt,
                    "offen": sum(1 for i in treffer if i in offen),
                    "anders": len(anders), "zu_kurz": len(muster) < 3})
    return out


def uebernehmen(vorschlaege: list[dict]) -> list[str]:
    """Angehakte Vorschlaege als gewoehnliche Textregeln speichern."""
    from finctl.rules import regelwerk as rw

    vorhanden = {r["id"] for r in rw.laden()}
    ids = []
    for v in vorschlaege:
        rid = rw.neue_kennung(v["text"], vorhanden)
        vorhanden.add(rid)
        rw.speichern({"id": rid, "name": v["text"], "priority": PRIORITAET,
                      "match": {"text": [v["text"]]}, "set": {"mgmt": v["kategorie"]}})
        ids.append(rid)
    return ids
