"""Wie gut haette die Prognose getroffen?

Nachgerechnet aus dem Ledger, ohne gespeicherte Stände: fuer jeden der
letzten vollstaendigen Monate wird die Prognose so gebaut, wie sie am Ende des
Vormonats ausgesehen haette -- mit Daten nur bis dahin --, und gegen das Ist
dieses Monats gehalten.

Zwei Pruefungen, weil es zwei Rechnungen gibt:

* JAHRESBASIS: die laufenden Bloecke als Zwoelfmonatsschnitt vor dem Monat,
  das Gehalt als Median der 24 Monate davor, die Kreditraten aus dem
  Tilgungsplan. Das ist, womit die Jahresrechnung jedes Jahr fortschreibt.
* KONTEN: je Konto die abgeleiteten Posten mit Fenster bis zum Vormonat, dazu
  Kreditraten und Abos mit Termin, gegen die Buchungen des Monats.

Was niemand vorhersagen kann -- Kaufnebenkosten, eine Anzahlung -- steht in
einer eigenen Spalte, statt als Fehler der Prognose zu zaehlen. Umbuchungen
und Investments zaehlen auf keiner Seite.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from finctl.forecast import abgleich as ag

#: Diese Bloecke vergleicht die Jahresbasis. `sondereffekt` ist nicht
#: vorhersehbar, `investment` keine Ausgabe, das Objekt steht als Annahme
#: daneben.
BLOECKE = ("gehalt", "miete", "einkommen_sonst", "kredit", "immobilie",
           "fixkosten", "steuern", "konsum", ag.OBJEKT)


@dataclass(slots=True)
class Monat:
    monat: date
    soll: dict[str, int] = field(default_factory=dict)
    ist: dict[str, int] = field(default_factory=dict)
    #: Was im Monat gebucht und von keiner Prognose erwartet war.
    unvorhersehbar_cents: int = 0
    #: Bloecke, fuer die es in diesem Monat keine Prognose gibt, gegen die man
    #: halten koennte -- ein Tilgungsplan, der erst spaeter beginnt.
    nicht_vergleichbar: set[str] = field(default_factory=set)

    def _zaehlt(self, block: str) -> bool:
        return block not in self.nicht_vergleichbar

    @property
    def sparrate_soll(self) -> int:
        return sum(v for b, v in self.soll.items() if self._zaehlt(b))

    @property
    def sparrate_ist(self) -> int:
        return sum(self.ist.get(b, 0) for b in self.soll if self._zaehlt(b))

    def delta(self, block: str) -> int | None:
        if not self._zaehlt(block):
            return None
        return self.ist.get(block, 0) - self.soll.get(block, 0)


@dataclass(slots=True)
class KontoMonat:
    monat: date
    konto: str
    soll_cents: int
    ist_cents: int
    ohne_kredit: bool = False

    @property
    def delta_cents(self) -> int:
        return self.ist_cents - self.soll_cents


def letzte_monate(conn: sqlite3.Connection, anzahl: int = 6) -> list[date]:
    ende = ag.letzter_vollstaendiger_monat(conn)
    if ende is None:
        return []
    letzter = date(ende.year, ende.month, 1)
    return [ag._plus_monate(letzter, -i) for i in range(anzahl - 1, -1, -1)]


def fenster_vor(conn: sqlite3.Connection, monat: date, monate: int = 12):
    """Die `monate` vollstaendigen Monate VOR `monat` -- ohne ihn selbst."""
    bis = ag._month_end(ag._plus_monate(monat, -1))
    von = ag._plus_monate(monat, -monate)
    erster = conn.execute(
        "SELECT MIN(period_start) FROM statements WHERE status = 'imported'"
    ).fetchone()[0]
    if erster:
        e = date.fromisoformat(erster)
        voll = e if e.day == 1 else ag._plus_monate(e.replace(day=1), 1)
        von = max(von, voll)
    if von > bis:
        return None
    anzahl = (bis.year * 12 + bis.month) - (von.year * 12 + von.month) + 1
    return ag.Fenster(von=von, bis=bis, monate=anzahl)


def jahresbasis(conn: sqlite3.Connection, monate: list[date], *,
                plaene: list | None = None, objekt_cents=None) -> list[Monat]:
    """Die Jahresbasis gegen das Ist, Monat fuer Monat.

    `plaene` sind die Tilgungsplaene (siehe jahre._tilgungsplaene); ohne
    Angabe die wirksamen aus loans.yaml. `objekt_cents(monat)` liefert die
    Annahme fuer das Objekt; ohne Angabe die aus assumptions.yaml.
    """
    from finctl.forecast import jahre as jm
    from finctl.forecast import szenarien as sz

    if plaene is None:
        plaene = jm._tilgungsplaene(sz.aktive_ids())
    if objekt_cents is None:
        objekt_cents = _objekt_annahme
    out = []
    for m in monate:
        f = fenster_vor(conn, m)
        eintrag = Monat(monat=m)
        if f is not None:
            gemessen = ag.measure(conn, f)
            for b in jm.FROM_BASE:
                eintrag.soll[b] = round(gemessen.get(b, 0) / f.monate)
        median = jm.gehalt_median_cents(conn, vor=m)
        eintrag.soll["gehalt"] = median or 0
        eintrag.soll["kredit"] = -sum(
            p.payment_cents for _loan, sched in plaene for p in sched.payments
            if (p.month.year, p.month.month) == (m.year, m.month))
        # Die Tilgungsplaene sind am zuletzt beobachteten Saldo verankert und
        # koennen fruehere Monate nicht nachbilden. Beginnt einer spaeter, ist
        # die Kreditzeile in diesem Monat kein Treffer und kein Fehler.
        if any(sched.payments and sched.payments[0].month > ag._month_end(m)
               for _loan, sched in plaene):
            eintrag.nicht_vergleichbar.add("kredit")
        eintrag.soll[ag.OBJEKT] = objekt_cents(m)

        ist = ag.measure(conn, ag.Fenster(von=m, bis=ag._month_end(m), monate=1))
        eintrag.ist = {b: ist.get(b, 0) for b in BLOECKE}
        eintrag.unvorhersehbar_cents = ist.get("sondereffekt", 0)
        out.append(eintrag)
    return out


def _objekt_annahme(m: date) -> int:
    """Was die Objekte mit Prognose in diesem Monat abwerfen sollten."""
    from finctl import objekte

    return sum(p.netto_cents for p in objekte.prognosen() if p.laeuft(m))


def konten(conn: sqlite3.Connection, monate: list[date]) -> list[KontoMonat]:
    """Die Kontoprognose je Konto gegen die Buchungen des Monats.

    Verglichen wird der Saldo aus Kosten und Einnahmen ohne Umbuchungen und
    ohne Investments -- das, was die abgeleiteten Posten beschreiben.
    Deklarierte Zuweisungen zwischen eigenen Konten zaehlen deshalb auf
    keiner Seite.
    """

    from finctl import abos as _ab
    from finctl.forecast import engine as fc
    from finctl.forecast import konten as ops
    from finctl.realestate.loan import amortise, lade_kredite, opening_balance_cents, segments_from

    fcfg = ops.forecast_config()
    abos = _ab.alle_vertraege()
    overrides = ops._income_overrides(fcfg)
    kredite = []
    for loan in lade_kredite(ops.CONFIG_DIR):
        if not loan.get("segments") or not loan.get("servicing_account_id"):
            continue
        try:
            kredite.append((loan["servicing_account_id"], amortise(
                str(loan["id"]), opening_balance_cents(loan), segments_from(loan))))
        except Exception:
            continue
    durchlaufend = {k for k, v in (fcfg.get("account_roles") or {}).items()
                    if (v or {}).get("role") == "durchlaufend"}
    konten_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM accounts WHERE ingest_mode='parsed' AND active=1 ORDER BY id")
        if r["id"] not in durchlaufend]

    out = []
    for m in monate:
        naechster = ag._plus_monate(m, 1)
        for konto in konten_ids:
            soll = 0
            for item in ops.abgeleitete_posten(conn, konto, fcfg, abos, stichtag=m):
                if not item.due_in(m):
                    continue
                soll += overrides.get(item.label, item.amount_cents)
            # Wie in der Jahresbasis: ein Tilgungsplan, der erst spaeter
            # beginnt, kann diesen Monat nicht nachbilden. Dann bleiben die
            # Kreditbuchungen auf BEIDEN Seiten draussen.
            eigene = [sched for servicing, sched in kredite if servicing == konto]
            ohne_kredit = any(sched.payments and sched.payments[0].month > ag._month_end(m)
                              for sched in eigene)
            if not ohne_kredit:
                for sched in eigene:
                    soll -= sum(p.payment_cents for p in sched.payments
                                if (p.month.year, p.month.month) == (m.year, m.month))
            for termin in _ab.posten(abos, konto, ab=m, bis=fc.add_months(m, 1)):
                if termin["faellig"] < naechster:
                    soll += termin["betrag_cents"]
            ist = conn.execute(
                "SELECT COALESCE(SUM(s.amount_cents), 0) FROM splits s "
                "JOIN transactions t ON t.id = s.transaction_id "
                "LEFT JOIN mgmt_categories c ON c.id = s.mgmt_category_id "
                "WHERE t.account_id = ? AND t.booking_date >= ? AND t.booking_date < ? "
                "  AND COALESCE(s.mgmt_category_id, '') NOT LIKE 'transfer/%' "
                "  AND COALESCE(s.mgmt_category_id, '') NOT LIKE 'investment/%' "
                "  AND COALESCE(s.mgmt_category_id, '') <> 'immobilie/kaufnebenkosten' "
                "  AND COALESCE(c.kind, '') <> 'transfer'"
                + ("  AND COALESCE(s.mgmt_category_id, '') NOT LIKE 'kredit/%'"
                   if ohne_kredit else ""),
                (konto, m.isoformat(), naechster.isoformat())).fetchone()[0]
            if soll or ist:
                out.append(KontoMonat(monat=m, konto=konto, soll_cents=soll,
                                      ist_cents=int(ist or 0), ohne_kredit=ohne_kredit))
    return out


def pruefen(conn: sqlite3.Connection, anzahl: int = 6) -> dict:
    """Beide Pruefungen, fertig fuer Seite und CLI."""
    monate = letzte_monate(conn, anzahl)
    if not monate:
        return {"monate": [], "jahres": [], "konten": [], "bloecke": BLOECKE}
    jahres = jahresbasis(conn, monate)
    kontozeilen = konten(conn, monate)
    n = len(jahres)
    mittel = {}
    for b in BLOECKE:
        werte = [abs(j.delta(b)) for j in jahres if j.delta(b) is not None]
        mittel[b] = round(sum(werte) / len(werte)) if werte else None
    mittel["sparrate"] = round(sum(abs(j.sparrate_ist - j.sparrate_soll)
                                   for j in jahres) / n)
    je_konto: dict[str, list[int]] = {}
    for k in kontozeilen:
        je_konto.setdefault(k.konto, []).append(abs(k.delta_cents))
    return {
        "monate": monate, "jahres": jahres, "konten": kontozeilen,
        "bloecke": BLOECKE, "mittel": mittel,
        "konten_mittel": {k: round(sum(v) / len(v)) for k, v in je_konto.items()},
        "konten_ids": sorted(je_konto),
    }
