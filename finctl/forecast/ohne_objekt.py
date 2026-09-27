"""Cashflow ohne das eine Objekt: traegt sich der Haushalt auch ohne es?

Wer ein Objekt hat, dessen Ertrag vollstaendig in Puffer und Depot fliessen
soll, braucht die Gegenprobe: der Rest -- Gehalt, Mieten, Kredite, Fixkosten,
Konsum -- muss fuer sich mindestens bei null stehen. Weder /ziele noch /konten
zeigte diese Zahl: die Jahresrechnung mischt die Objektannahme in die
Sparrate, und /konten zeigt Salden je Konto, in denen Umbuchungen und
Kaufraten stecken.

ZWEI SEITEN, DIESELBE ABGRENZUNG:

* GEMESSEN je vollstaendigem Monat aus dem Ledger, mit der Blockzuordnung aus
  abgleich. Heraus fallen der Objektblock, die Kaufraten (`sondereffekt`) und
  Investments; Umbuchungen zaehlen dort ohnehin nicht.
* VORAUSGERECHNET je Monat aus den Posten von /konten ueber alle Konten.
  Heraus fallen die Zeilen der zugehoerigen Planklammer und alle
  Geldbewegungen zwischen eigenen Konten -- Zuweisungen, Auffuellung,
  Abraeumen --, die sich im Haushalt ohnehin aufheben. Das Gehalt ist dort die
  Untergrenze, nicht der Median: die Prognose ist bewusst vorsichtig.

WELCHES Objekt gemeint ist, steht in der Konfiguration, nicht hier: die
Kennung in assumptions.yaml, Ziel, Soll und Planklammer in forecast.yaml. Ohne
beides rechnet das Modul den Cashflow ohne Sondereffekte und Investments --
eine sinnvolle Zahl auch fuer den, der kein Objekt hat.

Daneben das Gemeinschaftskonto gegen sein Soll aus forecast.yaml, weil es der
groesste Posten ist, der 2026 gewachsen ist.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from finctl.forecast import abgleich as ag

#: Bloecke, die gemessen NICHT zum Cashflow ohne das Objekt zaehlen. Der
#: Objektblock steht nur in der Liste, wenn eines eingetragen ist -- sonst
#: gibt es ihn nicht, und die Kennung laeuft ins Leere statt etwas zu treffen.
GEMESSEN_OHNE = frozenset({ag.OBJEKT, "sondereffekt", "investment"})
GEMEINSCHAFTSKONTO = "familie/gemeinschaftskonto"
#: Etiketten aus /konten, die Geld zwischen eigenen Konten bewegen.
_UMBUCHUNG = ("budget:", "budget/", "Auffüllung ", "Übertrag von ",
              "transfer/", "investment/")


@dataclass(frozen=True, slots=True)
class Monat:
    monat: date
    cents: int
    gemeinschaftskonto_cents: int
    gemessen: bool


def einstellungen(fcfg: dict) -> dict:
    """Ziel, Soll, Bezeichnung und Zuordnung aus forecast.yaml, mit Vorgaben.

    Auch der NAME kommt aus der Konfiguration -- hier oder, wenn hier nichts
    steht, aus properties.yaml. Fest in die Vorlage geschrieben waere er ein
    Wort, das nur ein Editor aendern kann, und ein Objekt, das verkauft ist,
    stuende noch Jahre als Ueberschrift auf der Seite.

    `klammer` sagt, welche Planklammer die Zeilen des Objekts traegt. Leer
    heisst: keine, und dann faellt aus der Vorausrechnung nur heraus, was
    ohnehin kein Cashflow ist.
    """
    roh = fcfg.get("cashflow_ohne_objekt") or {}
    return {"ziel_cents": int(roh.get("ziel_cents", 0) or 0),
            "gemeinschaftskonto_soll_cents":
                int(roh.get("gemeinschaftskonto_soll_cents", 0) or 0),
            "label": str(roh.get("label") or ag.objekt_name()),
            "klammer": str(roh.get("klammer") or "") or None}


def gemessen(conn: sqlite3.Connection, monate: int = 12) -> list[Monat]:
    """Die letzten `monate` vollstaendigen Monate, je Monat gemessen."""
    f = ag.fenster(conn, monate)
    if f is None:
        return []
    out = []
    for m in f.monatsliste():
        kat = ag.measure_kategorien(
            conn, ag.Fenster(von=m, bis=ag._month_end(m), monate=1))
        out.append(Monat(
            monat=m,
            cents=sum(c for blk, je in kat.items() if blk not in GEMESSEN_OHNE
                      for c in je.values()),
            gemeinschaftskonto_cents=sum(je.get(GEMEINSCHAFTSKONTO, 0)
                                         for je in kat.values()),
            gemessen=True))
    return out


def zaehlt(label: str, herkunft: dict | None,
           klammer: str | None = None) -> bool:
    """Gehoert ein Posten aus /konten zum Cashflow ohne das Objekt?"""
    if label.startswith(_UMBUCHUNG):
        return False
    herkunft = herkunft or {}
    if herkunft.get("quelle") != "plan":
        return True
    # Planzeilen tragen ihre Kategorie: eine Umbuchung zwischen eigenen
    # Konten oder ein Depotverkauf ist auch als Planzeile kein Cashflow.
    kategorie = str(herkunft.get("kategorie") or "")
    if kategorie.startswith(("transfer/", "investment/")) \
            or kategorie == "immobilie/kaufnebenkosten":
        return False
    if not klammer:
        return True
    return str(herkunft.get("verweis") or "").split(":", 1)[0] != klammer


def vorausgerechnet(views: list[dict],
                    klammer: str | None = None) -> list[Monat]:
    """Je Prognosemonat die Summe der zaehlenden Posten ueber alle Konten.

    `views` ist die Rueckgabe von ops.household_accounts.
    """
    summe: dict[str, int] = {}
    gemeinschaft: dict[str, int] = {}
    for v in views:
        if v.get("error"):
            continue
        herkunft = v.get("herkunft") or {}
        for row in v.get("rows") or []:
            monat = row["month"][:7]
            summe.setdefault(monat, 0)
            gemeinschaft.setdefault(monat, 0)
            for label, cents in (row.get("detail") or {}).items():
                if not zaehlt(label, herkunft.get(label), klammer):
                    continue
                summe[monat] += cents
                if label == GEMEINSCHAFTSKONTO:
                    gemeinschaft[monat] += cents
    return [Monat(monat=date.fromisoformat(m + "-01"), cents=summe[m],
                  gemeinschaftskonto_cents=gemeinschaft[m], gemessen=False)
            for m in sorted(summe)]


def _schnitt(werte: list[int]) -> int | None:
    return round(sum(werte) / len(werte)) if werte else None


def kennzahl(conn: sqlite3.Connection, views: list[dict], fcfg: dict,
             monate_gemessen: int = 12) -> dict:
    """Alles, was die Seiten zeigen: Monate, Schnitte, Ziel und Soll."""
    ein = einstellungen(fcfg)
    ist = gemessen(conn, monate_gemessen)
    prog = vorausgerechnet(views, ein["klammer"])
    naechste = prog[:12]
    letzter = ist[-1] if ist else None
    return {
        **ein,
        "monate": ist + prog,
        "letzter": letzter,
        "schnitt_gemessen_cents": _schnitt([m.cents for m in ist]),
        "gemessen_anzahl": len(ist),
        "gemessen_erreicht": sum(1 for m in ist if m.cents >= ein["ziel_cents"]),
        "schnitt_prognose_cents": _schnitt([m.cents for m in naechste]),
        "prognose_anzahl": len(naechste),
        "prognose_erreicht": sum(1 for m in naechste
                                 if m.cents >= ein["ziel_cents"]),
        "gemeinschaft_schnitt_cents": _schnitt(
            [m.gemeinschaftskonto_cents for m in ist]),
    }
