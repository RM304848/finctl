"""Does the forecast describe the same world as the ledger?

A projection that misses the current year misses 2045 too -- it just does not
look like it. So before anything is projected twenty years out, the same block
definitions are run against both sides for the year in progress and the
difference is shown.

A difference means one of two things, and both are worth knowing: a rule is
missing, so spending is landing somewhere the plan does not look, or an
assumption is wrong. Neither announces itself.

BLOCKS ARE ORDERED AND FIRST MATCH WINS, and that is not a detail. The naive
definitions overlap: `kredit/tilgung` and `kredit/zinsen` both carry the
`fixkosten` flag, so a fixed-cost total that simply sums the flag counts the
1.715,86 of loan payments a second time -- 4.111,65 where the honest figure is
2.395,79. Ordering makes the overlap impossible rather than merely documented.

The last block catches whatever no earlier one claimed, so the blocks always
partition the ledger completely. That is what makes the sum test meaningful:
if the parts do not add up to the whole, something is being counted twice or
dropped, and the test says so instead of the totals quietly disagreeing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from finctl.pfade import CONFIG_DIR

# Internal movements are not income or cost on either side, so they are
# excluded from the partition entirely rather than given a block.
EXCLUDED_PREFIX = "transfer/"


@dataclass(frozen=True, slots=True)
class Block:
    id: str
    label: str
    #: SQL fragment over `s` (splits), `m` (mgmt_categories) and `t`
    #: (transactions). Evaluated in order; the first match claims the split.
    where: str
    #: Income blocks read better positive, cost blocks positive-as-magnitude.
    income: bool = False


#: Kennung des Objektblocks: alle Objekte, deren Ertrag angenommen statt
#: gemessen wird. WELCHE das sind, sagt `prognose:` je Objekt in
#: properties.yaml -- hier steht nur, dass sie einen Block teilen.
OBJEKT = "objekt"


def _prognosen() -> list:
    from finctl import objekte

    return objekte.prognosen()


def _objekt_planned() -> dict[str, Planned]:
    """Die Objektzeile des Abgleichs -- leer, wenn kein Objekt eine Prognose hat.

    Eigene Funktion, damit `planned` nicht um eine Verzweigung waechst: die
    Ratsche in tests/test_lint.py haelt sie bei elf.
    """
    liste = _prognosen()
    if not liste:
        return {}
    teile = [(f"{p.name}: " if len(liste) > 1 else "")
             + _eur(p.miete_cents) + " Miete minus " + _eur(p.kosten_cents) + " Kosten"
             for p in liste]
    return {OBJEKT: Planned(
        sum(p.netto_cents for p in liste),
        note="Annahme: " + "; ".join(teile)
             + "; gemessen ist, was bisher gebucht wurde")}


def objekt_name() -> str:
    """Der Name fuer Beschriftungen: das eine Objekt, sonst "Objekte"."""
    liste = _prognosen()
    return liste[0].name if len(liste) == 1 else "Objekte"


def _objekt_block() -> Block | None:
    """Die Objekte, deren Ertrag angenommen statt gemessen wird.

    EIGENER BLOCK, Mieten und laufende Kosten zusammen. Es stand genau ein
    Mietmonat im Ledger. Ueber acht vollstaendige Monate gemittelt ist das
    ein Achtel dessen, was das Objekt tatsaechlich abwirft. Bliebe es in
    `miete` und `immobilie`, wuerde die Jahresrechnung genau diese
    Verwaesserung zwanzig Jahre fortschreiben.

    Herausgeloest ist der Block im Abgleich sichtbar und in der
    Jahresrechnung durch eine erklaerte Annahme ersetzbar, statt still aus
    einem einzelnen Monat hochgerechnet zu werden. Die Kennungen prueft
    `objekte.prognosen`, bevor sie hier in SQL eingehen.
    """
    liste = _prognosen()
    if not liste:
        return None
    kennungen = ", ".join(f"'{p.id}'" for p in liste)
    return Block(OBJEKT, objekt_name(), f"s.property_id IN ({kennungen})")


def bloecke() -> tuple[Block, ...]:
    """Die Bloecke in ihrer Reihenfolge; der erste Treffer gewinnt.

    Eine Funktion und keine Konstante, weil genau einer der Bloecke aus der
    Konfiguration kommt. Wer kein Objekt mit Prognose hat, bekommt die Liste
    ohne ihn -- und sonst aendert sich nichts.
    """
    objekt = _objekt_block()
    return (
        Block("gehalt", "Gehalt",
              "s.mgmt_category_id LIKE 'einkommen/gehalt%'", income=True),
        # Einmalige Objektkosten zuerst: Kaufraten wuerden jeden Monatsschnitt
        # dominieren und den Vergleich unbrauchbar machen.
        Block("sondereffekt", "Sondereffekte",
              "s.mgmt_category_id = 'immobilie/kaufnebenkosten'"),
        *((objekt,) if objekt else ()),
        Block("miete", "Mieteinnahmen",
              "s.mgmt_category_id = 'immobilie/mieteinnahme'", income=True),
        Block("einkommen_sonst", "sonstige Einnahmen",
              "s.mgmt_category_id LIKE 'einkommen/%'", income=True),
        # Vor den Fixkosten, weil Tilgung und Zinsen das fixkosten-Flag tragen.
        Block("kredit", "Kreditraten",
              "s.mgmt_category_id LIKE 'kredit/%'"),
        Block("immobilie", "Immobilienkosten",
              "s.mgmt_category_id LIKE 'immobilie/%'"),
        Block("fixkosten", "Fixkosten",
              "COALESCE(m.fixkosten, 0) = 1"),
        Block("investment", "Investment",
              "s.mgmt_category_id LIKE 'investment/%'"),
        Block("steuern", "Steuern",
              "s.mgmt_category_id LIKE 'steuern/%'"),
        # Faengt alles ab, was keiner der vorigen beansprucht hat. Ohne ihn
        # waere der Summentest nicht aussagekraeftig.
        Block("konsum", "Konsum und Rest", "1 = 1"),
    )


@dataclass(slots=True)
class Planned:
    """A planned figure and the months it actually covers.

    The window matters as much as the number. A loan schedule is anchored at
    the most recently observed balance -- some months into the year -- so it
    describes the future correctly and cannot reproduce the months before that
    anchor at all. Comparing its three covered months against eight measured
    ones puts one payment against eight and invites the conclusion that
    something is broken. Nothing is: the two sides were being asked about
    different months.
    """

    cents_per_month: int
    months: int | None = None      # None -> alle vollstaendigen Monate
    note: str = ""


@dataclass(slots=True)
class Row:
    block: Block
    measured_cents: int = 0
    planned_cents: int = 0
    #: Ueber wie viele Monate beide Seiten verglichen wurden.
    window_months: int = 0
    note: str = ""

    @property
    def delta_cents(self) -> int:
        return self.planned_cents - self.measured_cents


@dataclass(slots=True)
class Abgleich:
    #: Erster und letzter Tag des Messfensters, fuer Links auf die
    #: Transaktionsseite.
    von: str = ""
    bis: str = ""
    months: int = 0
    rows: list[Row] = field(default_factory=list)
    unassigned_cents: int = 0

    def by_id(self, block_id: str) -> Row:
        return next(r for r in self.rows if r.block.id == block_id)

def _eur(cents: int) -> str:
    """Deutsche Schreibweise. f-strings liefern 2,900 statt 2.900."""
    from finctl.ledger.db import format_eur

    return format_eur(cents)


def _case_expression() -> str:
    """One CASE that assigns every split to the first block it matches."""
    arms = "\n".join(f"        WHEN {b.where} THEN '{b.id}'" for b in bloecke())
    return f"CASE\n{arms}\n    END"


@dataclass(frozen=True, slots=True)
class Fenster:
    """Die letzten vollstaendigen Monate, ueber die gemessen wird.

    ROLLIEREND statt Kalenderjahr. Ueber das angebrochene Jahr gemessen und
    auf zwoelf Monate hochgerechnet zaehlte jede Jahreszahlung, die zufaellig
    in die bisherigen Monate fiel, anderthalbfach -- die KFZ-Versicherung aus
    Januar und Februar mit 920 statt 613 --, und was erst im Herbst faellig
    ist, fehlte ganz. Ueber zwoelf Monate steht jede Jahreszahlung genau einmal
    drin, ohne Hochrechnen. Und am 1. Januar gibt es kein leeres Jahr mehr,
    das man abfangen muss.
    """

    von: date          # erster Tag des ersten Monats
    bis: date          # letzter Tag des letzten Monats
    monate: int

    @property
    def mitte(self) -> float:
        """Die Mitte des Fensters als Jahreszahl, fuer die Inflation."""
        return self.von.year + (self.von.month - 1) / 12 + self.monate / 24

    def monatsliste(self) -> list[date]:
        return [_plus_monate(self.von, i) for i in range(self.monate)]


def _plus_monate(d: date, n: int) -> date:
    idx = d.year * 12 + d.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


def _month_end(day: date) -> date:
    nxt = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def letzter_vollstaendiger_monat(conn: sqlite3.Connection) -> date | None:
    """Der letzte Monat, den ALLE Konten vollstaendig abdecken.

    Fuer jedes Konto ist das der Monat seines letzten Auszugsendes, wenn das
    auf den Monatsletzten faellt, sonst der Monat davor: der DKB-Auszug bis
    04.09. deckt vom September ein Fuenftel ab, und das Gehalt dafuer ist noch
    nicht gezahlt. Ein Monat ist erst vollstaendig, wenn er es ueberall ist.
    """
    rows = conn.execute(
        "SELECT account_id, MAX(period_end) AS last FROM statements "
        "WHERE status = 'imported' GROUP BY account_id").fetchall()
    if not rows:
        return None
    enden = []
    for row in rows:
        last = date.fromisoformat(row["last"])
        if last != _month_end(last):
            last = date.fromordinal(last.replace(day=1).toordinal() - 1)
        enden.append(last)
    return min(enden) if enden else None


def fenster(conn: sqlite3.Connection, monate: int = 12) -> Fenster | None:
    """Die letzten `monate` vollstaendigen Monate -- oder weniger, wenn das
    Ledger juenger ist. None, wenn nichts gemessen ist."""
    ende = letzter_vollstaendiger_monat(conn)
    if ende is None:
        return None
    von = _plus_monate(ende.replace(day=1), -(monate - 1))
    erster = conn.execute(
        "SELECT MIN(period_start) FROM statements WHERE status = 'imported'"
    ).fetchone()[0]
    if erster:
        e = date.fromisoformat(erster)
        voll = e if e.day == 1 else _plus_monate(e.replace(day=1), 1)
        von = max(von, voll)
    if von > ende:
        return None
    anzahl = (ende.year * 12 + ende.month) - (von.year * 12 + von.month) + 1
    return Fenster(von=von, bis=ende, monate=anzahl)


def _ohne(ohne: set[str] | None) -> tuple[str, list[str]]:
    if not ohne:
        return "", []
    return f" AND t.dedup_hash NOT IN ({','.join('?' * len(ohne))})", sorted(ohne)


def measure(conn: sqlite3.Connection, f: Fenster,
            ohne: set[str] | None = None) -> dict[str, int]:
    """What the ledger says, per block, over the window.

    `ohne` nimmt einzelne Buchungen heraus -- die einer einmaligen
    Planposition. Eine Anzahlung, die im Fenster gebucht ist, wuerde sonst
    als laufender Posten jedes Jahr wiederkehren.
    """
    extra, args = _ohne(ohne)
    rows = conn.execute(f"""
        SELECT {_case_expression()} AS block, SUM(s.amount_cents) AS cents
        FROM   splits s
        JOIN   transactions t ON t.id = s.transaction_id
        LEFT   JOIN mgmt_categories m ON m.id = s.mgmt_category_id
        WHERE  t.booking_date BETWEEN ? AND ?
          AND  COALESCE(s.mgmt_category_id, '') NOT LIKE '{EXCLUDED_PREFIX}%'{extra}
        GROUP  BY block
    """, (f.von.isoformat(), f.bis.isoformat(), *args)).fetchall()
    return {r["block"]: int(r["cents"] or 0) for r in rows}


def measure_kategorien(conn: sqlite3.Connection, f: Fenster,
                       ohne: set[str] | None = None) -> dict[str, dict[str, int]]:
    """Wie `measure`, aber je Block noch einmal je Kategorie aufgeteilt.

    DIESELBE Zuordnung und dieselben Ausschluesse, damit die Kategorien exakt
    auf die Blocksumme aufgehen. Eine zweite, aehnliche Abfrage wuerde sich
    irgendwann um eine Buchung von der ersten unterscheiden, und dann erklaert
    der Rechenweg eine andere Zahl als die angezeigte.

    Eine Buchung ohne Kategorie steht unter dem leeren Schluessel.
    """
    extra, args = _ohne(ohne)
    rows = conn.execute(f"""
        SELECT {_case_expression()} AS block,
               COALESCE(s.mgmt_category_id, '') AS kategorie,
               SUM(s.amount_cents) AS cents
        FROM   splits s
        JOIN   transactions t ON t.id = s.transaction_id
        LEFT   JOIN mgmt_categories m ON m.id = s.mgmt_category_id
        WHERE  t.booking_date BETWEEN ? AND ?
          AND  COALESCE(s.mgmt_category_id, '') NOT LIKE '{EXCLUDED_PREFIX}%'{extra}
        GROUP  BY block, kategorie
    """, (f.von.isoformat(), f.bis.isoformat(), *args)).fetchall()
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(r["block"], {})[r["kategorie"]] = int(r["cents"] or 0)
    return out


def total_excluding_transfers(conn: sqlite3.Connection, f: Fenster,
                              ohne: set[str] | None = None) -> int:
    extra, args = _ohne(ohne)
    row = conn.execute(f"""
        SELECT COALESCE(SUM(s.amount_cents), 0)
        FROM   splits s JOIN transactions t ON t.id = s.transaction_id
        WHERE  t.booking_date BETWEEN ? AND ?
          AND  COALESCE(s.mgmt_category_id, '') NOT LIKE '{EXCLUDED_PREFIX}%'{extra}
    """, (f.von.isoformat(), f.bis.isoformat(), *args)).fetchone()
    return int(row[0] or 0)


def build(conn: sqlite3.Connection, planned: dict | None = None,
          ohne: set[str] | None = None) -> Abgleich:
    """The measured side, and the planned side where one is supplied.

    `planned` is per-block and per MONTH, like the measured column, so the two
    are read side by side without anyone converting in their head.
    """
    f = fenster(conn)
    if f is None:
        return Abgleich()
    measured = measure(conn, f, ohne)
    planned = planned or {}
    out = Abgleich(von=f.von.isoformat(), bis=f.bis.isoformat(), months=f.monate)
    for block in bloecke():
        entry = planned.get(block.id)
        if isinstance(entry, Planned):
            window = entry.months or f.monate
            note, planned_cents = entry.note, entry.cents_per_month
        else:
            window, note = f.monate, ""
            planned_cents = int(entry or 0)

        if window == f.monate:
            measured_cents = round(measured.get(block.id, 0) / f.monate)
        else:
            # Beide Seiten ueber DASSELBE Fenster, sonst vergleicht man
            # verschiedene Monate und nennt die Differenz einen Fehler.
            tail = Fenster(von=_plus_monate(f.bis.replace(day=1), -(window - 1)),
                           bis=f.bis, monate=window)
            measured_cents = round(measure(conn, tail, ohne).get(block.id, 0) / window)

        out.rows.append(Row(block=block, measured_cents=measured_cents,
                            planned_cents=planned_cents,
                            window_months=window, note=note))
    # Was die Bloecke zusammen NICHT erklaeren. Muss null sein: der letzte
    # Block faengt alles ab, also ist ein Rest hier ein Fehler in der Abfrage
    # und keine Eigenschaft der Daten.
    out.unassigned_cents = (total_excluding_transfers(conn, f, ohne)
                            - sum(measured.values()))
    return out


# ------------------------------------------------------------ die Planseite

def planned(conn: sqlite3.Connection, f: Fenster | None = None,
            ohne: set[str] | None = None) -> dict[str, Planned]:
    """What the plan says, per block and per month, over the window.

    Drawn from the sources the annual projection will use, so that what is
    compared here is what will be projected forward -- not a second
    calculation that happens to agree today.

    Three of the blocks have a source INDEPENDENT of the ledger, and those are
    the ones this comparison actually tests:

      Gehalt        the median -- the figure the annual projection uses.
                    Comparing against the FLOOR instead showed a gap that was
                    not an error but a deliberate margin, and a line that
                    always disagrees stops being read.
      Kreditraten   the amortisation schedules in loans.yaml
      Objekt        the assumption, against what has actually been booked

    The rest -- fixed costs, consumption, property costs -- is the measured
    average, because that is what a base budget is. Their delta is small by
    construction and says little; the honest reading is that those rows
    confirm the blocks partition cleanly.
    """
    from finctl import assumptions as ann
    from finctl.forecast import szenarien as sz
    from finctl.realestate.loan import (
        amortise,
        lade_kredite,
        nur_wirksame,
        opening_balance_cents,
        segments_from,
    )

    f = f or fenster(conn)
    if f is None:
        return {}
    out: dict[str, Planned] = {}

    out["gehalt"] = Planned(ann.salary_median_cents(),
                            note="Median; der Floor gilt nur der Liquidität")
    out.update(_objekt_planned())

    monate = {(m.year, m.month) for m in f.monatsliste()}
    per_month: dict[tuple[int, int], int] = {}
    starts: list[tuple[int, int]] = []
    spaet: list[str] = []
    # Nur Kredite, die gelten: ein Vorlagenkredit haengt an seiner Klammer und
    # zaehlt nicht, solange sie aus ist -- sonst stand die Kreditzeile wegen
    # eines Eigenheims ab 2030 dauerhaft auf "nicht vergleichbar".
    for loan in nur_wirksame(lade_kredite(CONFIG_DIR), sz.aktive_ids()):
        if not loan.get("segments"):
            continue
        try:
            sched = amortise(loan["id"], opening_balance_cents(loan),
                             segments_from(loan))
        except Exception:
            continue
        if not sched.payments:
            continue
        erste = (sched.payments[0].month.year, sched.payments[0].month.month)
        starts.append(erste)
        if erste > (f.von.year, f.von.month):
            name = str(loan.get("name") or loan.get("id")).split(" --")[0]
            spaet.append(f"{name} ab {erste[0]}-{erste[1]:02d}")
        for p in sched.payments:
            schluessel = (p.month.year, p.month.month)
            if schluessel in monate:
                per_month[schluessel] = per_month.get(schluessel, 0) + p.payment_cents
    # Nur die Monate, in denen ALLE Plaene laufen: ein Plan, der im September
    # anfaengt, sagt ueber den Maerz nichts, und ihn mit null einzurechnen
    # waere eine Aussage, die niemand getroffen hat.
    spaetester = max(starts) if starts else (f.von.year, f.von.month)
    full = sorted(m for m in per_month if m >= spaetester)
    if full:
        window = len(full)
        avg = -round(sum(per_month[m] for m in full) / window)
        note = ("" if window == f.monate
                else f"alle Pläne erst ab {full[0][0]}-{full[0][1]:02d}")
        out["kredit"] = Planned(avg, months=window, note=note)
    else:
        out["kredit"] = Planned(
            0, months=f.monate,
            note="nicht vergleichbar — " + ", ".join(spaet)
                 + "; am zuletzt beobachteten Saldo verankert")

    measured = measure(conn, f, ohne)
    for block in bloecke():
        out.setdefault(block.id, Planned(round(measured.get(block.id, 0) / f.monate)))
    return out
