"""Vorgänge zuordnen: eine Gruppe je Transaktion.

Ein dritter Pass, getrennt von Parsen und Kategorisieren, und die Trennung ist
nicht kosmetisch. Die Kategorie beantwortet "was für eine Ausgabe war das",
die Gruppe "wozu gehörte sie" -- und dieselbe Kategorie gehört über die Jahre
zu verschiedenen Vorgängen, während dieselbe Gruppe über viele Kategorien
reicht.

Das Datumsfenster ist deshalb Pflicht und kein Zusatz: derselbe Versicherer
gehört vor und nach einem Fahrzeugwechsel in verschiedene Gruppen, und ohne
Fenster fielen zwanzig Jahre Beiträge in einen Topf.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from finctl.pfade import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "groups.yaml"


@dataclass(slots=True)
class Gruppe:
    id: str
    name: str
    von: date
    bis: date | None = None
    counterparty: tuple[str, ...] = ()
    #: Ein Praefix oder mehrere. Mehrere, weil ein Vorgang selten auf einem
    #: Ast sitzt: zum Auto gehoeren mobilitaet/kfz-* UND mobilitaet/kraftstoff,
    #: aber nicht mobilitaet/bahn-privat. Auf `mobilitaet/` zu verkuerzen
    #: zoege Bahn, Fahrrad und Parken mit hinein.
    kategorie_prefix: tuple[str, ...] = ()
    property_id: str | None = None
    konto: str | None = None

    def matches(self, row: sqlite3.Row) -> bool:
        """Ob diese Transaktion in den Vorgang gehört.

        Alle genannten Bedingungen müssen zutreffen. Eine Regel ohne jede
        Bedingung ausser dem Fenster fängt alles in diesem Zeitraum -- das ist
        erlaubt und manchmal richtig, aber es ist eine Entscheidung und keine
        Nachlässigkeit, weshalb `load` sie nicht verbietet.
        """
        gebucht = date.fromisoformat(row["booking_date"])
        if gebucht < self.von:
            return False
        if self.bis and gebucht > self.bis:
            return False
        if self.property_id and row["property_id"] != self.property_id:
            return False
        if self.konto and row["account_id"] != self.konto:
            return False
        if self.kategorie_prefix:
            cat = row["mgmt_category_id"] or ""
            if not any(cat.startswith(p) for p in self.kategorie_prefix):
                return False
        if self.counterparty:
            haystack = ((row["counterparty_norm"] or "")
                        + " " + (row["raw_text"] or "")).lower()
            if not any(needle.lower() in haystack for needle in self.counterparty):
                return False
        return True


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    text = str(value)
    return date.fromisoformat(text + "-01" if len(text) == 7 else text)


def load(path: Path | str = CONFIG_PATH) -> list[Gruppe]:
    import yaml

    p = Path(path)
    if not p.exists():
        return []
    spec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for raw in spec.get("gruppen") or []:
        match = raw.get("match") or {}
        cp = match.get("counterparty") or ()
        prefixe = match.get("kategorie_prefix") or ()
        if isinstance(prefixe, str):
            prefixe = (prefixe,)
        out.append(Gruppe(
            id=str(raw["id"]), name=str(raw.get("name") or raw["id"]),
            von=_as_date(raw["von"]),
            bis=_as_date(raw["bis"]) if raw.get("bis") else None,
            counterparty=tuple(cp) if isinstance(cp, (list, tuple)) else (cp,),
            kategorie_prefix=tuple(prefixe),
            property_id=match.get("property"),
            konto=match.get("konto")))
    return out


def apply(conn: sqlite3.Connection, gruppen: list[Gruppe] | None = None,
          *, dry_run: bool = False) -> dict[str, int]:
    """Jede Transaktion der ersten passenden Gruppe zuordnen.

    Erste Regel gewinnt, wie im Kategorien-Regelwerk -- damit lässt sich eine
    engere Regel vor eine weitere stellen.

    Vorher wird JEDE Zuordnung geleert, nicht nur die neu getroffenen. Sonst
    bliebe eine Transaktion in einer Gruppe, deren Regel inzwischen entfernt
    wurde, und der Zustand hinge davon ab, in welcher Reihenfolge die Regeln
    im Laufe der Zeit existiert haben.
    """
    gruppen = load() if gruppen is None else gruppen
    rows = conn.execute("""
        SELECT t.id, t.booking_date, t.account_id, t.counterparty_norm,
               t.raw_text, s.mgmt_category_id, s.property_id
        FROM   transactions t
        LEFT   JOIN splits s ON s.transaction_id = t.id AND s.seq = 0
        ORDER  BY t.booking_date, t.id
    """).fetchall()

    treffer: dict[str, int] = {g.id: 0 for g in gruppen}
    zuordnung: list[tuple[str, int]] = []
    for row in rows:
        for gruppe in gruppen:
            if gruppe.matches(row):
                treffer[gruppe.id] += 1
                zuordnung.append((gruppe.id, row["id"]))
                break

    if not dry_run:
        conn.execute("UPDATE transactions SET group_id = NULL")
        conn.executemany(
            "UPDATE transactions SET group_id = ? WHERE id = ?", zuordnung)
        conn.commit()
    return treffer


def summary(conn: sqlite3.Connection) -> list[dict]:
    """Je Vorgang: Umfang, Zeitraum, wie viele Kategorien er überspannt."""
    return [dict(r) for r in conn.execute("""
        SELECT t.group_id AS id, COUNT(DISTINCT t.id) AS n,
               SUM(s.amount_cents) AS cents,
               MIN(t.booking_date) AS von, MAX(t.booking_date) AS bis,
               COUNT(DISTINCT s.mgmt_category_id) AS kategorien
        FROM   transactions t JOIN splits s ON s.transaction_id = t.id
        WHERE  t.group_id IS NOT NULL
        GROUP  BY t.group_id ORDER BY SUM(s.amount_cents)
    """)]
