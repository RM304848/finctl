"""Die Reihe, zu der eine zugeordnete Buchung gehoert.

EINE DEFINITION FUER BEIDE SEITEN -- Vertraege wie Planzeilen. Eine
Handentscheidung zeigt auf eine Buchung: "das hier ist die PKV", "das hier ist
die Miete, die wegfaellt". Gemeint ist nie die eine Buchung, sondern die
Reihe: derselbe Zahlungsempfaenger in derselben Kategorie, Monat fuer Monat.

Ohne diesen Schritt haengt die Prognose daran, wie viele Haken jemand gesetzt
hat. Ein Vertrag, von dem nur eine Handvoll Buchungen angehakt war, liess den
ganzen Rest im Median stehen und buchte seinen Termin trotzdem: derselbe
Beitrag zweimal, Monat fuer Monat, ohne dass irgendeine Seite es gesagt haette.

Erkannt wird die Reihe an `counterparty_norm` und NICHT am Betrag allein: ein
Beitrag steigt, ein Tarif wechselt, der Empfaenger bleibt. Begrenzt ist sie
auf die Kategorie der angehakten Buchung.

`betrag_cents` engt weiter ein, wo einer bekannt ist -- ein Vertrag kennt
seinen. Derselbe Empfaenger in derselben Kategorie ist naemlich nicht immer
dieselbe Sache: bei Amazon liegen Einkauf und Abo unter einem Namen, und nur
das Abo gehoert zum Vertrag. Wer einen Betrag mitgibt, bekommt nur, was ihm
nahekommt.

Eine Buchung ohne Gegenpartei zieht nichts nach sich. Sie steht fuer sich, und
alles andere waere geraten.

Liegt im Hauptbuch und nicht bei der Prognose, weil Vertraege (`abos.py`) und
Prognose sie beide brauchen -- und keins der beiden Module das andere
voraussetzen darf (`finctl/module.py`).
"""

from __future__ import annotations

import sqlite3

#: Wie weit eine Buchung vom Vertragsbetrag abweichen darf und noch als
#: derselbe Posten gilt. Ein Viertel ist grosszuegig fuer eine Beitrags-
#: erhoehung -- die bewegt sich im Messfenster im niedrigen Prozentbereich --
#: und weist trotzdem ab, was nur zufaellig beim selben Empfaenger liegt: ein
#: Einkauf neben einem Abo ist meist um ein Vielfaches daneben, nicht um ein
#: Viertel. Ein Vorzeichenwechsel, also eine Erstattung, liegt immer draussen.
TOLERANZ = 0.25

_PAARE = """
    SELECT DISTINCT t.counterparty_norm AS gegenpartei,
           s.mgmt_category_id AS kategorie
    FROM   transactions t JOIN splits s ON s.transaction_id = t.id
    WHERE  t.dedup_hash IN ({platz})
"""

_REIHE = """
    SELECT t.dedup_hash, s.amount_cents
    FROM   transactions t JOIN splits s ON s.transaction_id = t.id
    WHERE  t.counterparty_norm = ? AND s.mgmt_category_id IS ?
"""


def paare(conn: sqlite3.Connection,
          buchungen) -> list[tuple[str, str | None]]:
    """Die (Gegenpartei, Kategorie)-Paare der angehakten Buchungen."""
    buchungen = sorted(set(buchungen or []))
    if not buchungen or conn is None:
        return []
    platz = ",".join("?" * len(buchungen))
    return [(r[0], r[1]) for r in
            conn.execute(_PAARE.format(platz=platz), buchungen) if r[0]]


def hashes(conn: sqlite3.Connection, buchungen,
           betrag_cents: int | None = None) -> list[str]:
    """Die angehakten Buchungen UND die uebrigen derselben Reihe.

    Was hier herauskommt, ist die Menge, die eine Erklaerung aus der Messung
    nimmt: der Vertrag ersetzt seine ganze Spur im Ledger, nicht die Haelfte
    davon. Angehaktes bleibt immer drin -- eine Handentscheidung schlaegt
    jede Regel.
    """
    alle = set(buchungen or [])
    grenze = abs(betrag_cents) * TOLERANZ if betrag_cents else None
    for gegenpartei, kategorie in paare(conn, alle):
        for dedup_hash, cents in conn.execute(_REIHE, (gegenpartei, kategorie)):
            if grenze is None or abs(cents - betrag_cents) <= grenze:
                alle.add(dedup_hash)
    return sorted(alle)
