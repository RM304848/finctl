"""Manuelle Entscheidungen von der Bankzeile auf die PayPal-Zahlung umhaengen.

Solange PayPal ein Haendler war, stand der Anlass einer Zahlung nirgends im
Ledger, und der Eigentuemer hat ihn ueber zwei Jahre 51 Mal von Hand
eingetragen: "PAYPAL *konzertkasse" ist ein Konzert, "PAYPAL *max.beispiel"
ist ein Restaurant. Diese Arbeit ist die einzige Stelle im Werkzeug, an der
Wissen steckt, das aus keiner Datei zurueckzurechnen ist.

Seit PayPal ein Konto ist, sitzt sie am falschen Platz. Die Bankzeile ist nur
noch die DECKUNG -- eine Umbuchung zwischen eigenen Konten --, und der Vorgang
steht daneben auf PayPal, mit Namen. Bliebe die Entscheidung auf der Bankzeile,
stuende derselbe Betrag zweimal im Ledger.

Umgehaengt statt neu erfunden, und der Weg ist exakt: PayPals Export verbindet
jede Deckung ueber den zugehoerigen Transaktionscode mit ihrer Zahlung. Nur der
Sprung von der Bank zur Deckung geht ueber Betrag und Datum -- die Bank bucht
nach PayPal, nie davor.

SCHLAEGT NICHTS VOR UND SCHREIBT NICHTS VON SELBST. Was sich nicht eindeutig
zuordnen laesst, bleibt liegen, wo es ist: eine falsch umgehaengte Entscheidung
waere schlimmer als eine, die man noch einmal trifft.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import yaml

from finctl.ledger.db import now_iso
from finctl.rules.categorize import overrides_pfad

# Die Zeilen, die auf PayPal kein Vorgang sind, sondern Geldbewegung zwischen
# eigenen Konten. Deckungsrichtung beides: herein von Karte oder Bank, hinaus
# als Auszahlung.
DECKUNGEN = ("Allgemeine Gutschrift auf Kreditkarte",
             "Bankgutschrift auf PayPal-Konto",
             "Von Nutzer eingeleitete Abbuchung")

_ZUGEHOERIG = re.compile(r"\bzu ([A-Z0-9]{10,})\s*$")

# Wie lange die Bank hinter PayPal herbucht. Zehn Tage statt der naheliegenden
# zwei: ueber Weihnachten lag ein Paar sechs Tage auseinander, und ein Fenster,
# das praezise aussieht, laesst genau die liegen.
NACHLAUF = timedelta(days=10)
VORLAUF = timedelta(days=1)


def abdeckung(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """Welchen Zeitraum die vorliegenden PayPal-Auszuege abdecken."""
    row = conn.execute(
        "SELECT MIN(period_start) AS von, MAX(period_end) AS bis FROM statements "
        "WHERE account_id = 'paypal' AND status = 'imported'").fetchone()
    return (row["von"], row["bis"]) if row and row["von"] else None


def _zahlungen(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Die PayPal-Zahlungen, nach ihrem Transaktionscode."""
    platzhalter = ",".join("?" * len(DECKUNGEN))
    return {r["customer_ref"]: r for r in conn.execute(
        f"""SELECT id, booking_date, amount_cents, counterparty, customer_ref,
                   raw_text
            FROM   transactions
            WHERE  account_id = 'paypal' AND purpose NOT IN ({platzhalter})""",
        DECKUNGEN)}


def _deckungen(conn: sqlite3.Connection) -> list[tuple[sqlite3.Row, str | None]]:
    """Die Deckungen, je mit dem Code der Zahlung, zu der sie gehoeren."""
    platzhalter = ",".join("?" * len(DECKUNGEN))
    rows = conn.execute(
        f"""SELECT id, booking_date, amount_cents, raw_text
            FROM   transactions
            WHERE  account_id = 'paypal' AND purpose IN ({platzhalter})""",
        DECKUNGEN).fetchall()
    out = []
    for r in rows:
        treffer = _ZUGEHOERIG.search(r["raw_text"] or "")
        out.append((r, treffer.group(1) if treffer else None))
    return out


def vorschlaege(conn: sqlite3.Connection,
                path: Path | None = None) -> list[dict]:
    """Welche Handentscheidung auf welche PayPal-Zahlung gehoert.

    Jede Zeile traegt ihren Grund mit: was nicht zugeordnet werden konnte,
    steht mit `warum` daneben statt einfach zu fehlen.
    """
    path = path or overrides_pfad()
    zeitraum = abdeckung(conn)
    if not zeitraum:
        return []
    von, bis = zeitraum

    spec = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    eintraege = (spec or {}).get("overrides") or []

    zahlungen = _zahlungen(conn)
    deckungen = _deckungen(conn)
    # Eine Deckung deckt genau eine Bankzeile. Ohne das Merken griffen zwei
    # gleich hohe Zahlungen am selben Tag beide auf dieselbe Deckung zu.
    vergeben: set[int] = set()

    out: list[dict] = []
    for e in eintraege:
        text = e.get("text") or ""
        if "paypal" not in text.lower() or e.get("account") == "paypal":
            continue
        wann = str(e.get("when") or "")
        if not (von <= wann <= bis):
            out.append({**_kopf(e), "warum": "ausserhalb der PayPal-Abdeckung"})
            continue

        gebucht = date.fromisoformat(wann)
        passend = [
            (d, code) for d, code in deckungen
            if d["id"] not in vergeben
            and d["amount_cents"] == -int(e["amount_cents"])
            and -VORLAUF <= gebucht - date.fromisoformat(d["booking_date"]) <= NACHLAUF
        ]
        if not passend:
            out.append({**_kopf(e),
                        "warum": "keine Deckung mit diesem Betrag im Zeitfenster "
                                 "— bei Fremdwährung erwartbar, der Kurs steht "
                                 "nur auf der Kartenabrechnung"})
            continue
        deckung, code = passend[0]
        zahlung = zahlungen.get(code) if code else None
        if zahlung is None:
            # EINE AUSZAHLUNG TRAEGT KEINEN CODE. Sie gehoert zu keiner
            # einzelnen Zahlung, sondern raeumt das Guthaben ab -- und damit
            # zu allem, was sich seit dem letzten Nullstand angesammelt hat.
            # Das ist aus dem laufenden Saldo exakt rekonstruierbar und muss
            # nicht geraten werden.
            gedeckt = _seit_nullstand(conn, deckung)
            if len(gedeckt) == 1:
                zahlung = gedeckt[0]
            else:
                out.append({**_kopf(e), "warum": (
                    f"Auszahlung deckt {len(gedeckt)} Zahlungen auf einmal"
                    if gedeckt else
                    "Deckung gefunden, aber keine zugehörige Zahlung")})
                continue
        vergeben.add(deckung["id"])
        out.append({**_kopf(e), "zahlung_id": zahlung["id"],
                    "zahlung_datum": zahlung["booking_date"],
                    "zahlung_betrag": zahlung["amount_cents"],
                    "gegenpartei": zahlung["counterparty"],
                    "parts": e.get("parts") or [],
                    "warum": None})
    return out


def _seit_nullstand(conn: sqlite3.Connection, auszahlung) -> list:
    """Die Zahlungen, die eine Auszahlung abraeumt.

    PayPals Saldo steht fast immer auf null und faellt nur zwischen einem
    Eingang und seiner Auszahlung davon ab. Alles zwischen dem letzten
    Nullstand und dieser Auszahlung gehoert zu ihr -- nicht geschaetzt,
    sondern am laufenden Saldo abgelesen.
    """
    zeilen = conn.execute(
        """SELECT id, booking_date, amount_cents, counterparty, purpose
           FROM   transactions
           WHERE  account_id = 'paypal' AND booking_date <= ?
           ORDER  BY booking_date, seq_in_statement""",
        (auszahlung["booking_date"],)).fetchall()
    # Bis genau zu dieser Auszahlung, nicht bis zum Tagesende: an einem Tag
    # koennen zwei davon stehen.
    bis = next((i for i, z in enumerate(zeilen) if z["id"] == auszahlung["id"]),
               None)
    if bis is None:
        return []

    saldo = 0
    letzte_null = 0
    for i, z in enumerate(zeilen[:bis]):
        saldo += z["amount_cents"]
        if saldo == 0:
            letzte_null = i + 1
    return [z for z in zeilen[letzte_null:bis] if z["purpose"] not in DECKUNGEN]


def _kopf(e: dict) -> dict:
    return {"dedup_hash": e["dedup_hash"], "konto": e.get("account"),
            "wann": str(e.get("when") or ""), "betrag": int(e["amount_cents"]),
            "text": (e.get("text") or "")[:60],
            "kategorien": [p.get("mgmt") for p in (e.get("parts") or [])]}


def uebernehmen(conn: sqlite3.Connection,
                path: Path | None = None) -> list[dict]:
    """Die eindeutigen Vorschlaege wirklich umhaengen.

    Der Betrag kommt von der ZAHLUNG, nicht aus der alten Entscheidung: bei
    einer teilweise aus Guthaben gedeckten Zahlung sind die beiden verschieden,
    und die Aufteilung muss zur Transaktion passen, an der sie haengt.
    """
    path = path or overrides_pfad()
    from finctl.rules.categorize import record_override

    fertig = [v for v in vorschlaege(conn, path) if v["warum"] is None]
    for v in fertig:
        posten = [dict(p) for p in v["parts"]]
        if len(posten) == 1:
            posten[0]["amount_cents"] = v["zahlung_betrag"]
        conn.execute("DELETE FROM splits WHERE transaction_id = ?",
                     (v["zahlung_id"],))
        for seq, p in enumerate(posten):
            conn.execute(
                """INSERT INTO splits (transaction_id, seq, amount_cents,
                       mgmt_category_id, tax_category_id, property_id, note,
                       source, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,'manual',?,?)""",
                (v["zahlung_id"], seq, p["amount_cents"], p.get("mgmt"),
                 p.get("tax"), p.get("property"), p.get("note"),
                 now_iso(), now_iso()))
        conn.commit()
        record_override(conn, v["zahlung_id"], path)
        _entfernen(path, v["dedup_hash"])
        # Und die alte Aufteilung auch AUS DER DATENBANK nehmen, nicht nur aus
        # der Datei. `categorize` uebernimmt bestehende Handaufteilungen
        # unbesehen -- bliebe sie stehen, traege die Bankzeile weiter ihre
        # Kategorie, obwohl die Entscheidung inzwischen nebenan haengt, und
        # der Betrag stuende zweimal im Ledger. Die Regel setzt sie beim
        # naechsten Lauf als Umbuchung neu.
        conn.execute(
            "DELETE FROM splits WHERE transaction_id IN "
            "(SELECT id FROM transactions WHERE dedup_hash = ?)",
            (v["dedup_hash"],))
        conn.commit()
    return fertig


def _entfernen(path: Path, dedup_hash: str) -> None:
    """Die alte Entscheidung von der Bankzeile nehmen.

    Erst NACH dem Schreiben der neuen: bricht etwas dazwischen ab, steht die
    Entscheidung zweimal da und nicht keinmal.
    """
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    eintraege = [e for e in spec.get("overrides", [])
                 if e["dedup_hash"] != dedup_hash]
    from finctl.rules.categorize import _write_atomically

    kopf = path.read_text(encoding="utf-8").split("overrides:", 1)[0]
    _write_atomically(path, kopf + yaml.safe_dump(
        {"overrides": eintraege}, allow_unicode=True, sort_keys=False))


