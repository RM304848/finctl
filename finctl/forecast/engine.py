"""Monthly cash-flow projection with intra-month liquidity.

The month-end balance is the wrong number to steer by. Costs land at the start
of the month and salary at the end, so the lowest point inside a month sits
well below where it closes. A projection that only reports month-end can show
a comfortable year while you are repeatedly in the Dispokredit.

This reproduces the sequence the Haushaltsbuch already modelled by hand:

    vor Gehalt  ->  minus all costs  ->  nach Kosten  ->  plus income  ->  nach Gehalt
                                         ^^^^^^^^^^^
                                         the trough that matters

`nach Gehalt` carries into the next month as `vor Gehalt`. The Dispo warning
fires on the trough, never on the closing balance.

Assuming every cost precedes every receipt is deliberately pessimistic. It is
the right bias for a liquidity guard: being warned about a trough that does
not materialise costs nothing, while missing one costs Dispo interest.
"""

from __future__ import annotations

import itertools
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from finctl.realestate.loan import Schedule

# Days used when an item carries none. Pessimistic by design: costs assumed
# at the start of the month, receipts at the end.
UNDATED_COST_DAY, UNDATED_INCOME_DAY = 1, 28


def add_months(d: date, months: int) -> date:
    index = d.month - 1 + months
    return date(d.year + index // 12, index % 12 + 1, 1)


@dataclass(slots=True)
class RecurringItem:
    label: str
    account_id: str
    amount_cents: int              # signed: income positive, cost negative
    category: str | None = None
    # Which object produced it, where one did. Carried so a FIRE calculation can
    # exclude a leasehold's income without excluding rent as a whole.
    property_id: str | None = None
    starts: date | None = None
    ends: date | None = None
    every_months: int = 1
    #: Jaehrliche Steigerung, als Anteil. Null heisst flach.
    #:
    #: Ueber zwoelf Monate ist eine flache Fortschreibung vertretbar. Ueber
    #: sechzig nicht: die Stromrechnung von 2031 ist nicht die von heute, und
    #: eine Prognose, die das behauptet, sieht in Monat 50 praezise aus und
    #: liegt systematisch zu niedrig.
    escalation_pa: float = 0.0
    #: Der Monat, ab dem gerechnet statt gemessen wird. Nur zur Anzeige --
    #: `/konten` markiert damit, wo die Zahlen aufhoeren, aus Auszuegen zu
    #: stammen.
    measured_until: date | None = None
    #: Woher der Betrag kommt: {"quelle", "herleitung", "verweis"}. Die
    #: Quellen sind derselbe feste Satz wie in herkunft.QUELLEN.
    herkunft: dict | None = None

    def amount_in(self, month: date, anchor: date) -> int:
        """Der Betrag in diesem Monat, mit der Steigerung seit `anchor`.

        Jaehrlich gestuft statt monatlich verzinst: ein Abo steigt zum
        Vertragsjahr, nicht kontinuierlich, und eine glatte Kurve wuerde eine
        Genauigkeit vortaeuschen, die die Sache nicht hat.
        """
        if not self.escalation_pa:
            return self.amount_cents
        jahre = (month.year - anchor.year) + (month.month - anchor.month) / 12.0
        if jahre <= 0:
            return self.amount_cents
        return int(round(self.amount_cents * (1.0 + self.escalation_pa) ** int(jahre)))

    def due_in(self, month: date) -> bool:
        if self.starts and month < self.starts.replace(day=1):
            return False
        if self.ends and month > self.ends.replace(day=1):
            return False
        if self.every_months == 1:
            return True
        anchor = (self.starts or month).replace(day=1)
        delta = (month.year - anchor.year) * 12 + (month.month - anchor.month)
        return delta >= 0 and delta % self.every_months == 0


def planned_amount_cents(item: dict) -> int:
    """The euro amount of a planned item, converting where it is not in euro.

    A foreign purchase is contracted in its own currency. Writing a euro
    estimate into planned.yaml hides that: the figure sitting there was
    65.000 against an obligation of 885.000.000 IDR, which implies
    13.615 IDR/EUR -- a rate that does not exist. It was an independent guess dressed as an amount.

    Recording the foreign amount and the rate separately keeps the exposure
    visible and makes re-pricing one number rather than arithmetic done by
    hand. At 17.000 the same obligation is 52.059, at 18.500 it is 47.838 --
    a 4.200 swing on a single payment, in a plan whose October headroom is
    smaller than that.
    """
    if item.get("amount_foreign") is not None:
        rate = item.get("fx_rate")
        if not rate:
            raise ValueError(
                f"planned item {item.get('label')!r} is in "
                f"{item.get('currency', '?')} but declares no fx_rate")
        return int(round(float(item["amount_foreign"]) / float(rate) * 100))
    return int(item["amount_cents"])


def funding_legs(item: dict) -> list[dict]:
    """The internal transfer that pays for a planned item.

    A large payment is booked against the account it leaves from, but the money
    is not sitting there -- it gets moved over from savings a few days earlier.
    Modelling only the outflow makes the paying account's forecast permanently
    alarming: the giro account projected to -58.715 for the instalments of a
    purchase, which is not what will happen and is exactly the warning that
    teaches you to ignore warnings.

    Declared once on the payment, as `funded_from` and `funded_on`, so the
    amount cannot drift between the payment and the transfer that covers it.
    Returns both legs -- out of savings, into the paying account -- and nothing
    at all when no funding is declared.

    Household-level forecasts must NOT expand these: a transfer between your
    own accounts nets to zero there, and adding both legs would only add noise.
    """
    funding = item.get("funding") or []
    if not funding:
        return []

    default_when = str(item.get("date") or item.get("month") or "")
    label = f"Deckung: {item.get('label', 'geplante Zahlung')}"
    legs: list[dict] = []
    for part in funding:
        # `when`, deliberately not `on`: YAML 1.1 reads a bare `on` as the
        # boolean True, so `on: 2026-10-07` produces the key True and the date
        # is silently unreachable. That cost an afternoon -- every transfer
        # quietly fell back to the payment date and the trough stayed wrong.
        when = str(part.get("when") or default_when)
        amount = abs(int(part["amount_cents"]))
        if not when or not amount:
            continue
        legs.append({"account": part["from"], "when": when,
                     "amount_cents": -amount, "label": label})
        legs.append({"account": item.get("account"), "when": when,
                     "amount_cents": amount, "label": label})
    return legs


@dataclass(slots=True)
class OneOff:
    """A dated obligation or receipt the forecast cannot infer.

    `day` places it inside the month so the trough reflects real sequence. A
    65.000 payment on the 15th funded by a transfer on the 1st never dips; the
    same two amounts with no days look like a 65.000 hole.

    `reduces_cost` ist fuer den einen Fall, in dem das Vorzeichen die falsche
    Auskunft gibt: ein WEGFALL ist positiv, weil Geld aufhoert zu fliessen --
    aber er ist keine Einnahme, sondern eine negative Ausgabe. Nach Vorzeichen
    einsortiert landete er bei den Einnahmen, und die kommen per Konvention
    NACH den Kosten. Der Monatstiefpunkt blieb dadurch unveraendert, obwohl
    genau er sich verbessert: faellt der Sprit weg, wird er trotzdem noch als
    bezahlt dargestellt. Das ist die Zahl, fuer die die Trough-Mechanik
    ueberhaupt existiert.
    """

    label: str
    account_id: str
    month: date
    amount_cents: int
    day: int | None = None          # None -> sequenced pessimistically
    #: Positiv, aber eine Kostenminderung -- siehe Klassendoku.
    reduces_cost: bool = False
    #: Woher der Betrag kommt, wie bei RecurringItem.
    herkunft: dict | None = None


@dataclass(slots=True)
class MonthRow:
    month: date
    opening_cents: int             # vor Gehalt
    costs_cents: int               # negative
    trough_cents: int              # nach Kosten
    income_cents: int              # positive
    closing_cents: int             # nach Gehalt
    breaches_dispo: bool
    # Was über dem Deckel lag und deshalb abgeräumt wird. Der Deckel ist kein
    # Zustand, den man beobachtet, sondern ein Auftrag: alles darüber gehört
    # aufs Tagesgeld, wo es Zinsen bringt.
    swept_cents: int = 0
    detail: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class Projection:
    account_id: str
    dispo_threshold_cents: int
    rows: list[MonthRow] = field(default_factory=list)

    @property
    def worst(self) -> MonthRow | None:
        return min(self.rows, key=lambda r: r.trough_cents) if self.rows else None

    @property
    def breaches(self) -> list[MonthRow]:
        return [r for r in self.rows if r.breaches_dispo]


def _monatsabstand(a: date, b: date) -> int:
    """Wie viele Monate zwischen zwei Monatsersten liegen."""
    return (b.year - a.year) * 12 + (b.month - a.month)


@dataclass(frozen=True, slots=True)
class _Ableitung:
    """Die Einstellungen, unter denen eine Kategorie abgeleitet wird.

    Sie stehen zusammen, weil sie zusammen gelten: die vier Wege, auf denen
    aus gemessenen Monaten ein Posten wird, brauchen fast dieselben Werte, und
    jeder Weg als Funktion mit elf Parametern waere eine Liste, die man beim
    Aufruf abzaehlen muss.
    """

    konto: str
    rate: float
    ends: dict[str, date]
    letzter_monat: date | None
    min_occurrences: int
    since: str
    since_fixkosten: str | None
    ende_monat: str
    fenster_monate: int
    variabel_als_schnitt: bool
    is_fixed: set[str]

    def endet(self, kategorie: str) -> date | None:
        return self.ends.get(kategorie)

    def posten(self, kategorie: str, cents: int, herkunft: dict,
               **weitere) -> RecurringItem:
        """Ein Posten mit den Feldern, die auf allen vier Wegen gleich sind."""
        return RecurringItem(
            label=kategorie, account_id=self.konto, amount_cents=cents,
            category=kategorie, escalation_pa=self.rate,
            ends=self.endet(kategorie), measured_until=self.letzter_monat,
            herkunft=herkunft, **weitere)


def _ausschluss(ohne: list[dict] | None, until: str | None) -> tuple[str, list]:
    """WAS ERKLAERT IST, WIRD NICHT GEMESSEN.

    Ein Posten, dessen Termin in `config/abos.yaml` steht, ersetzt seine Spur
    im Ledger, statt neben ihr zu stehen -- sonst zaehlt derselbe Betrag
    zweimal. Je Kategorie UND Gegenpartei, weil eine Kategorie mehrere Abos
    haelt: die ganze Kategorie auszuschliessen loeschte zwei Posten, um einen
    zu ersetzen.
    """
    bedingungen: list[str] = []
    weitere: list = []
    for regel in (ohne or []):
        # Zugeordnete Buchungen, genau diese und keine anderen: bei Amazon
        # stehen Einkauf und Abo unter demselben Namen, und nur die
        # angehakte Buchung gehoert zum Abo.
        if regel.get("dedup_hashes"):
            bedingungen.append(
                f"t.dedup_hash NOT IN ({','.join('?' * len(regel['dedup_hashes']))})")
            weitere.extend(regel["dedup_hashes"])
            continue
        teile = []
        if regel.get("kategorie"):
            teile.append("s.mgmt_category_id = ?")
            weitere.append(regel["kategorie"])
        if regel.get("gegenpartei"):
            teile.append("t.counterparty_norm LIKE ?")
            weitere.append(f"%{regel['gegenpartei']}%")
        if regel.get("betrag_cents") is not None:
            teile.append("s.amount_cents = ?")
            weitere.append(int(regel["betrag_cents"]))
        if teile:
            bedingungen.append("NOT (" + " AND ".join(teile) + ")")
    if until:
        bedingungen.append("t.booking_date <= ?")
        weitere.append(until)
    trenner = "\n          AND       "
    return (trenner + trenner.join(bedingungen) if bedingungen else ""), weitere


def _nach_kategorie(rows, is_fixed: set[str],
                    since: str) -> tuple[dict[str, list[int]],
                                         dict[str, list[str]], date | None]:
    """Die gemessenen Monate je Kategorie, und der letzte davon."""
    by_category: dict[str, list[int]] = {}
    months_seen: dict[str, list[str]] = {}
    letzter_monat: date | None = None
    for row in rows:
        by_category.setdefault(row["cat"], []).append(row["cents"])
        months_seen.setdefault(row["cat"], []).append(row["month"])
        # Das lange Fenster gilt nur fuer Fixkosten und zaehlt nicht fuer
        # den letzten gemessenen Monat der uebrigen.
        if row["cat"] not in is_fixed and row["month"] < since[:7]:
            by_category[row["cat"]].pop()
            months_seen[row["cat"]].pop()
            if not by_category[row["cat"]]:
                del by_category[row["cat"]], months_seen[row["cat"]]
            continue
        monat = date(int(row["month"][:4]), int(row["month"][5:7]), 1)
        letzter_monat = monat if letzter_monat is None else max(letzter_monat, monat)
    return by_category, months_seen, letzter_monat


def _steigerung(escalation_pa: float | None) -> float:
    """Die Steigerung kommt aus assumptions.yaml, wenn der Aufrufer keine nennt.

    An genau einer Stelle, wie jede andere Annahme auch.
    """
    if escalation_pa is not None:
        return float(escalation_pa)
    from finctl import assumptions as _ann

    try:
        return float(_ann.inflation_pa())
    except Exception:
        return 0.0


def _verrutschter_buchungstag(kategorie: str, paare: list[tuple[str, int]],
                              cfg: _Ableitung) -> RecurringItem | None:
    """Monatlich mit verrutschtem Buchungstag: Summe ueber die Monate seit der
    ersten Buchung. Ein Median naehme die Monate mit ZWEI Abbuchungen als
    Betrag."""
    from finctl.forecast.herkunft import eur_genau, prozent

    ende_d = date(int(cfg.ende_monat[:4]), int(cfg.ende_monat[5:7]), 1)
    monate = [date(int(m[:4]), int(m[5:7]), 1) for m, _ in paare]
    spanne = _monatsabstand(monate[0], ende_d) + 1
    betrag = round(sum(c for _, c in paare) / spanne)
    if not betrag:
        return None
    text = (f"Fixkosten mit verrutschtem Buchungstag: "
            f"{eur_genau(sum(c for _, c in paare))} über "
            f"{spanne} Monate seit {_mm(paare[0][0])} = "
            f"{eur_genau(betrag)} je Monat ("
            + ", ".join(f"{_mm(m)} {eur_genau(c)}" for m, c in paare)
            + ")" + (f"; steigt jährlich um {prozent(cfg.rate)}"
                     if cfg.rate else ""))
    return cfg.posten(kategorie, betrag,
                      {"quelle": "gemessen", "herleitung": text,
                       "verweis": kategorie})


def _langes_fenster(kategorie: str, monate: list[str], betraege: list[int],
                    cfg: _Ableitung) -> tuple[list[tuple[str, int]] | None,
                                              bool, RecurringItem | None]:
    """DAS LANGE FENSTER NUR, WO ES ETWAS FINDET, DAS DAS KURZE NICHT SIEHT.

    Drei Faelle, in dieser Reihenfolge:

    1. Im kurzen Fenster regelmaessig: Median dort, wie bisher. Eine
       Beitragserhoehung im Maerz soll nicht vom alten Beitrag ueberstimmt
       werden.
    2. Im langen Fenster in fast jedem Monat (hoechstens ein Monat Luecke) UND
       im letzten Monat gebucht: Summe durch die Monate seit der ersten
       Buchung. Spotify kommt mal am 1., mal am 31. und hat im kurzen Fenster
       nur vier Monate mit Buchung.
    3. Sonst Jahresposten ueber das lange Fenster -- die KFZ-Praemie aus dem
       Januar, die Grundsteuer im Quartal, auch wenn eine Nachbuchung sie auf
       fuenf Monate bringt.

    Regelmaessig, aber nicht mehr gebucht, faellt heraus: die C24-Abos, die im
    August zu Trade Republic gewandert sind, kaemen sonst als Monatskosten auf
    C24 zurueck.

    Zurueck kommen die weiter zu verwendenden Paare, ob das lange Fenster
    gilt, und ein fertiger Posten, wo dieser Weg hier schon endet.
    """
    paare = sorted(zip(monate, betraege, strict=True))
    kurz = [(m, c) for m, c in paare if m >= cfg.since[:7]]
    if len(kurz) >= cfg.min_occurrences:
        return kurz, False, None

    if len(paare) >= cfg.min_occurrences:
        if paare[-1][0] < cfg.ende_monat:
            return None, False, None
        monate_d = [date(int(m[:4]), int(m[5:7]), 1) for m, _ in paare]
        luecke = max(_monatsabstand(a, b) for a, b in itertools.pairwise(monate_d))
        if luecke <= 2:
            return None, False, _verrutschter_buchungstag(kategorie, paare, cfg)

    return paare, True, None


def _schnittposten(kategorie: str, monate: list[str], betraege: list[int],
                   cfg: _Ableitung) -> RecurringItem | None:
    """Kosten ohne fixkosten-Flag als Schnitt ueber alle Monate des Fensters."""
    schnitt = round(sum(betraege) / cfg.fenster_monate)
    if not schnitt:
        return None
    from finctl.forecast.herkunft import eur_genau, prozent

    verlauf = sorted(zip(monate, betraege, strict=True))
    text = (f"Schnitt {eur_genau(schnitt)} über {cfg.fenster_monate} vollständige "
            f"Monate ({len(verlauf)} mit Buchung): "
            + ", ".join(f"{_mm(m)} {eur_genau(c)}" for m, c in verlauf)
            + (f"; steigt jährlich um {prozent(cfg.rate)}" if cfg.rate else ""))
    return cfg.posten(kategorie, schnitt,
                      {"quelle": "gemessen", "herleitung": text,
                       "verweis": kategorie})


def _bloecke(fenster: list[tuple[date, int]]) -> list[list[tuple[date, int]]]:
    """BENACHBARTE Monate sind eine Zahlung in Raten und kein eigener Termin.

    Die KFZ-Versicherung kam im Januar und im Februar, das ist eine
    Jahrespraemie und keine halbjaehrliche.
    """
    gruppen: list[list[tuple[date, int]]] = [[fenster[0]]]
    for monat, cents in fenster[1:]:
        if _monatsabstand(gruppen[-1][-1][0], monat) <= 1:
            gruppen[-1].append((monat, cents))
        else:
            gruppen.append([(monat, cents)])
    return gruppen


def _takt(bloecke: list[list[tuple[date, int]]]) -> int:
    """WELCHER TAKT.

    Ein Jahresposten war lange die einzige Antwort, und fuer die Grundsteuer
    ist sie falsch: die kommt vierteljaehrlich, und vier Quartale zu einem
    Jahresklumpen zusammenzuziehen laesst drei Monate leer stehen und den
    vierten um das Dreifache zu hoch.

    Auf einen Teiler des Jahres gerundet: ein Bescheid kommt zum Quartal, auch
    wenn eine Lastschrift einmal zwei Wochen spaeter gebucht wurde. Bei
    Gleichstand der kuerzere Takt -- zu frueh erwartet ist fuer die Senke der
    harmlosere Fehler.
    """
    if len(bloecke) <= 1:
        return 12
    abstaende = sorted(_monatsabstand(bloecke[i][0][0], bloecke[i + 1][0][0])
                       for i in range(len(bloecke) - 1))
    gemessen = abstaende[len(abstaende) // 2]
    return min((2, 3, 4, 6, 12), key=lambda t: (abs(t - gemessen), t))


def _jahresposten(kategorie: str, monate: list[str], betraege: list[int],
                  cfg: _Ableitung, *, fix_lang: bool) -> RecurringItem | None:
    """JAEHRLICHE FIXKOSTEN FALLEN SONST GANZ HERAUS.

    Eine Jahrespraemie erscheint in einem Monat von neun und unterschreitet
    damit jede sinnvolle Mindestzahl. KFZ-Steuer, KFZ-Versicherung, Hausrat,
    PHV und Risiko-LV zusammen sind rund 1.060 im Jahr echter Verpflichtung,
    von denen die Kontoprognose schlicht nichts wusste. Das Haushaltsmodell
    hatte das behoben, die Kontoprognose nie -- dieselbe Asymmetrie wie beim
    Sparda-Darlehen.

    NUR fuer Kategorien mit fixkosten-Flag: eine Praemie kehrt wieder, weil
    ein Vertrag es sagt, waehrend ein Einzelkauf, der zufaellig zweimal
    vorkam, es nicht tut. Periodizitaet aus zwei Beobachtungen gewoehnlicher
    Ausgaben zu raten erfaende Verpflichtungen.
    """
    if kategorie not in cfg.is_fixed or not betraege:
        return None

    # SUMME JE BLOCK, nicht Median ueber die Buchungen. Zwei Zahlungen auf
    # einen Vertrag sind zusammen die Praemie; ihr Median ist keine von beiden
    # und die Jahreskosten von gar nichts. Eine Hausratpraemie von 580,40 in zwei
    # Raten meldete der Median als 94,70.
    #
    # Auf die letzten zwoelf beobachteten Monate begrenzt, damit ein zweites
    # Jahr Auszuege die Praemie nicht verdoppelt.
    letzte = max(monate)
    grenze = add_months(date(int(letzte[:4]), int(letzte[5:7]), 1), -11)
    fenster = sorted(
        (date(int(monat[:4]), int(monat[5:7]), 1), cents)
        for cents, monat in zip(betraege, monate, strict=True)
        if date(int(monat[:4]), int(monat[5:7]), 1) >= grenze)
    if not fenster:
        return None

    bloecke = _bloecke(fenster)
    summen = sorted(sum(c for _, c in block) for block in bloecke)
    betrag = summen[len(summen) // 2]
    if not betrag:
        return None

    takt = _takt(bloecke)
    anker = bloecke[-1][-1][0]
    # Ein Takt, dessen naechster Termin im langen Fenster schon verstrichen
    # ist, ist beendet: das Abo auf C24, das im Dezember zuletzt kam, liefe
    # sonst alle zwei Monate weiter.
    if fix_lang and cfg.ende_monat and add_months(anker, takt) <= date(
            int(cfg.ende_monat[:4]), int(cfg.ende_monat[5:7]), 1):
        return None
    return cfg.posten(
        kategorie, betrag,
        _herkunft_jahresposten(kategorie, bloecke, betrag, takt,
                               add_months(anker, takt), cfg.rate,
                               cfg.endet(kategorie)),
        # Klumpig statt auf ein Zwoelftel geglaettet: die Senke ist das, was
        # die Dispowarnung liest, und eine 640er Hausratpraemie in einem Monat
        # ist eine andere Liquiditaetstatsache als 53 im Monat, die nie
        # stattfinden.
        starts=add_months(anker, takt), every_months=takt)


def _medianposten(kategorie: str, monate: list[str], betraege: list[int],
                  cfg: _Ableitung, *, fix_lang: bool) -> RecurringItem | None:
    """Der Median der beobachteten Monate, der Normalfall."""
    verlauf = sorted(zip(monate, betraege, strict=True))
    sortiert = sorted(betraege)
    median = sortiert[len(sortiert) // 2]
    if median == 0:
        return None
    return cfg.posten(
        kategorie, median,
        _herkunft_median(kategorie, verlauf, median,
                         (cfg.since_fixkosten or cfg.since) if fix_lang else cfg.since,
                         cfg.min_occurrences, cfg.rate, cfg.endet(kategorie)))


def _als_schnitt(kategorie: str, betraege: list[int], cfg: _Ableitung) -> bool:
    return (cfg.variabel_als_schnitt and kategorie not in cfg.is_fixed
            and sum(betraege) < 0 and bool(cfg.fenster_monate))


def _posten_fuer_kategorie(kategorie: str, monate: list[str], betraege: list[int],
                           cfg: _Ableitung) -> RecurringItem | None:
    """Welcher der vier Wege fuer diese Kategorie gilt."""
    fix_lang = False
    if kategorie in cfg.is_fixed and cfg.since_fixkosten:
        kurz, fix_lang, fertig = _langes_fenster(kategorie, monate, betraege, cfg)
        if fertig is not None:
            return fertig
        if not kurz:
            return None
        monate = [m for m, _ in kurz]
        betraege = [c for _, c in kurz]

    if _als_schnitt(kategorie, betraege, cfg):
        return _schnittposten(kategorie, monate, betraege, cfg)
    if len(betraege) < cfg.min_occurrences or fix_lang:
        return _jahresposten(kategorie, monate, betraege, cfg, fix_lang=fix_lang)
    return _medianposten(kategorie, monate, betraege, cfg, fix_lang=fix_lang)


def derive_recurring(
    conn: sqlite3.Connection,
    account_id: str,
    *,
    since: str = "2026-01-01",
    min_occurrences: int = 5,
    exclude: set[str] | None = None,
    include_transfers: bool = False,
    durchlaufend: set[str] | None = None,
    escalation_pa: float | None = None,
    ends: dict[str, date] | None = None,
    ohne: list[dict] | None = None,
    until: str | None = None,
    variabel_als_schnitt: bool = False,
    since_fixkosten: str | None = None,
) -> list[RecurringItem]:
    """Build a recurring baseline from what actually happened.

    Uses the MEDIAN of observed months per category rather than the mean, so a
    single unusual month does not set the run rate.

    Abgeleitete Positionen bekommen eine STEIGERUNG und, wo eines bekannt ist,
    ein ENDE. Beides ist ueber zwoelf Monate entbehrlich und ueber sechzig
    nicht: ein flach fortgeschriebener Median zeigt ab Monat 13 etwas, das
    plausibel aussieht und nicht stimmt -- ein Handyvertrag, der 2028
    auslaeuft, bucht sonst bis 2031 weiter, und die Stromrechnung von 2031 ist
    die von heute.

    Vertraglich bekannte Positionen sind davon nicht betroffen: Kreditplaene
    und Szenariozeilen tragen ihre Enddaten ohnehin.

    `include_transfers` is the difference between the two questions this gets
    asked. At HOUSEHOLD level a transfer is not income and not a cost -- a euro
    moved from DKB to Scalable appears on both sides and counting it would
    inflate income and spending equally. At ACCOUNT level the same euro is
    entirely real: C24 receives 100 every month and spends about 70, and
    dropping the inflow projected it to -356 by February on an account that is
    in fact stable. That is the budget model the owner ran in the spreadsheet
    -- a fixed allocation in, observed spending out -- and it only works if the
    allocation is allowed to count.

    `variabel_als_schnitt` rechnet Kosten OHNE fixkosten-Flag als Schnitt
    ueber alle Monate des Fensters, Monate ohne Buchung als null -- auch wenn
    sie seltener als `min_occurrences` vorkamen. Der Median aus den Monaten
    MIT Buchung liess Kleidung, Geschenke und Events ganz fallen und nahm bei
    gerader Anzahl den kleineren Wert; der Treffer-Check zeigte die
    Konsumprognose dadurch Monat fuer Monat um rund 1.000 zu niedrig. Fixkosten
    bleiben beim Median bzw. Jahresposten, Einnahmen brauchen weiter die
    Mindestzahl -- eine seltene Gutschrift ist kein Einkommen, auf das eine
    Dispo-Warnung bauen darf.

    `since_fixkosten` gibt Kategorien MIT fixkosten-Flag ein eigenes, laengeres
    Fenster. Mit einem Fenster fuer alles sah die Jahrespostenregel nur sechs
    Monate: KFZ-Versicherung und Haftpflicht, gebucht im Januar, lagen im
    September ausserhalb und fehlten Anfang 2027 in der Prognose -- rund 860
    im Jahr. Variable Kosten und Einnahmen bleiben beim kurzen Fenster, damit
    ein beendetes Muster schnell herausfaellt.

    `until` schneidet das Fenster hinten ab (einschliesslich). Der
    Treffer-Check rechnet damit eine Prognose nach, wie sie am Monatsende
    davor ausgesehen haette -- ohne eine Buchung aus dem Monat, den sie
    vorhersagen soll.
    """
    filter_sql, weitere = _ausschluss(ohne, until)

    # EINE UMBUCHUNG AUF EIN DURCHLAUFENDES KONTO IST EINE AUSGABE.
    #
    # Sonst ist sie es nicht: ein Euro von DKB aufs Tagesgeld ist auf beiden
    # Seiten real und auf keiner ein Verbrauch. Geld auf PayPal dagegen ist
    # weg -- das Konto haelt nichts, es reicht im selben Moment weiter. Die
    # Karte zu belasten, um eine PayPal-Zahlung zu decken, aus dem Budget
    # herauszurechnen, machte die Ausgabe unsichtbar, obwohl sie stattfand.
    # Nur wenn es solche Konten gibt: sonst bleibt die Abfrage, wie sie war.
    durchlauf_sql = (
        f"\n                      OR s.transfer_account_id IN "
        f"({','.join('?' * len(durchlaufend))})" if durchlaufend else "")

    rows = conn.execute(
        f"""
        SELECT s.mgmt_category_id AS cat,
               substr(t.booking_date, 1, 7) AS month,
               SUM(s.amount_cents) AS cents
        FROM        splits s
        JOIN        transactions t ON t.id = s.transaction_id
        LEFT JOIN   mgmt_categories c ON c.id = s.mgmt_category_id
        WHERE       t.account_id = ?
          AND       t.booking_date >= ?
          AND       s.mgmt_category_id IS NOT NULL
          AND       (? = 1 OR c.kind IS NULL OR c.kind <> 'transfer'{durchlauf_sql}){filter_sql}
        GROUP BY    cat, month
        """,
        (account_id, min(since, since_fixkosten or since), 1 if include_transfers else 0,
         *sorted(durchlaufend or []), *weitere),
    ).fetchall()

    # Welche Kategorien vertraglich wiederkehren. Entscheidet, ob eine
    # Position mit zwei Buchungen eine Jahreszahlung ist oder ein Zufall --
    # und welches Fenster fuer sie gilt.
    is_fixed = {r[0] for r in conn.execute(
        "SELECT id FROM mgmt_categories WHERE fixkosten = 1")}

    by_category, months_seen, letzter_monat = _nach_kategorie(rows, is_fixed, since)

    cfg = _Ableitung(
        konto=account_id,
        rate=_steigerung(escalation_pa),
        ends=ends or {},
        letzter_monat=letzter_monat,
        min_occurrences=min_occurrences,
        since=since,
        since_fixkosten=since_fixkosten,
        ende_monat=(until[:7] if until else
                    (f"{letzter_monat.year}-{letzter_monat.month:02d}"
                     if letzter_monat else "")),
        fenster_monate=_fenstermonate(conn, account_id, since, until, letzter_monat),
        variabel_als_schnitt=variabel_als_schnitt,
        is_fixed=is_fixed,
    )

    skip = exclude or set()
    items: list[RecurringItem] = []
    for category, values in by_category.items():
        if category in skip:
            continue
        if posten := _posten_fuer_kategorie(category, months_seen[category],
                                            values, cfg):
            items.append(posten)
    return items


def _fenstermonate(conn, account_id: str, since: str, until: str | None,
                   letzter: date | None) -> int:
    """Wie viele Monate das Fenster umfasst -- ab dem ersten Auszug des Kontos."""
    if letzter is None and until is None:
        return 0
    von = date.fromisoformat(since).replace(day=1)
    try:
        erster = conn.execute(
            "SELECT MIN(period_start) FROM statements WHERE account_id = ? "
            "AND status = 'imported'", (account_id,)).fetchone()[0]
    except sqlite3.OperationalError:
        erster = None
    if erster:
        e = date.fromisoformat(erster)
        voll = e if e.day == 1 else add_months(e.replace(day=1), 1)
        von = max(von, voll)
    bis = date.fromisoformat(until).replace(day=1) if until else letzter
    return max(0, (bis.year - von.year) * 12 + bis.month - von.month + 1)


def _mm(monat: str) -> str:
    return f"{monat[5:7]}/{monat[:4]}"


def _herkunft_median(kategorie: str, verlauf: list[tuple[str, int]], median: int,
                     since: str, mindestens: int, rate: float,
                     ende: date | None) -> dict:
    """Wie ein Median entstand -- mit den Monatswerten, aus denen er stammt."""
    from finctl.forecast.herkunft import eur_genau as eur
    from finctl.forecast.herkunft import prozent

    werte = ", ".join(f"{_mm(m)} {eur(c)}" for m, c in verlauf)
    text = (f"Median {eur(median)} aus {len(verlauf)} Monaten mit Buchungen "
            f"(Fenster ab {_mm(since)}, mindestens {mindestens}): {werte}")
    if rate:
        text += f"; steigt jährlich um {prozent(rate)}"
    if ende:
        text += f"; endet {ende.month:02d}/{ende.year}"
    return {"quelle": "gemessen", "herleitung": text, "verweis": kategorie}


def _herkunft_jahresposten(kategorie: str, bloecke, betrag: int, takt: int,
                           naechster: date, rate: float,
                           ende: date | None) -> dict:
    """Wie ein seltener Fixkostenposten zu Betrag und Takt kam."""
    from finctl.forecast.herkunft import eur_genau as eur
    from finctl.forecast.herkunft import prozent

    zahlungen = ", ".join(
        f"{b[0][0].month:02d}/{b[0][0].year} {eur(sum(c for _, c in b))}"
        for b in bloecke)
    text = (f"Fixkosten unter der Mindestzahl: {len(bloecke)} Zahlungen in zwölf "
            f"Monaten ({zahlungen}), Median {eur(betrag)} alle {takt} Monate, "
            f"nächste {naechster.month:02d}/{naechster.year}")
    if rate:
        text += f"; steigt jährlich um {prozent(rate)}"
    if ende:
        text += f"; endet {ende.month:02d}/{ende.year}"
    return {"quelle": "gemessen", "herleitung": text, "verweis": kategorie}


def project(
    *,
    account_id: str,
    opening_cents: int,
    start: date,
    months: int,
    recurring: list[RecurringItem],
    one_offs: list[OneOff] | None = None,
    loan_schedules: list[Schedule] | None = None,
    dispo_threshold_cents: int = 0,
    income_overrides: dict[str, int] | None = None,
    sweep_above_cents: int | None = None,
) -> Projection:
    """Project forward, splitting each month into costs then income.

    `sweep_above_cents` models the operating account actually being emptied
    down to its ceiling each month. Without it the projection shows 41.517
    piling up on an account that pays no interest -- a number that is only
    true if you decide not to act on it, and the whole point of the ceiling
    is that you do.
    """
    projection = Projection(account_id=account_id,
                            dispo_threshold_cents=dispo_threshold_cents)
    balance = opening_cents
    events = one_offs or []
    overrides = income_overrides or {}

    for offset in range(months):
        month = add_months(start.replace(day=1), offset)
        detail: dict[str, int] = {}
        # (Tag, Rang innerhalb des Tages, Betrag). Der Rang existiert fuer
        # genau einen Fall: ein WEGFALL ist die Abwesenheit einer Zahlung,
        # nicht eine spaeter eintreffende Gutschrift. Er muss deshalb VOR den
        # Kosten desselben Tages stehen, sonst wird der Tiefpunkt gebildet,
        # bevor die Entlastung ankommt -- und faellt der Sprit weg, zeigt die
        # Prognose den Tiefpunkt weiter so, als wuerde er bezahlt.
        sequence: list[tuple[int, int, int]] = []
        costs = income = 0

        def add(day: int, amount: int, label: str,
                cost_side: bool = False, zuerst: bool = False,
                # An den Monat gebunden, in dem die Funktion entsteht.
                detail: dict = detail, sequence: list = sequence) -> None:
            nonlocal costs, income
            detail[label] = detail.get(label, 0) + amount
            sequence.append((day, 0 if cost_side or zuerst else 1, amount))
            # `cost_side` gewinnt ueber das Vorzeichen: ein Wegfall ist positiv
            # und gehoert trotzdem zu den Kosten, sonst verbessert er den
            # Monatstiefpunkt nicht.
            if amount < 0 or cost_side:
                costs += amount
            else:
                income += amount

        for item in recurring:
            if not item.due_in(month):
                continue
            # Ein Override ist eine gesetzte Zahl und wird NICHT gesteigert:
            # wer den Gehaltsfloor auf 4.671 setzt, meint 4.671 und nicht
            # 4.671 mit Tariferhoehung.
            amount = overrides.get(item.label)
            if amount is None:
                amount = item.amount_in(month, start)
            add(UNDATED_COST_DAY if amount < 0 else UNDATED_INCOME_DAY,
                amount, item.label)

        for schedule in loan_schedules or []:
            for payment in schedule.payments:
                if payment.month.year == month.year and payment.month.month == month.month:
                    add(payment.month.day, -payment.payment_cents,
                        f"loan:{schedule.loan_id}")

        for event in events:
            if event.account_id != account_id:
                continue
            if event.month.year == month.year and event.month.month == month.month:
                day = event.day
                if day is None:
                    day = (UNDATED_COST_DAY
                           if event.amount_cents < 0 or getattr(event, "reduces_cost", False)
                           else UNDATED_INCOME_DAY)
                # EIN DATIERTER EINGANG STEHT VOR DEN KOSTEN SEINES TAGES.
                # Die Auffuellung am 1. existiert, damit die Kosten vom 1.
                # gedeckt sind. Nach ihnen einsortiert, bildete sich der
                # Tiefpunkt vor dem Geld: Trade Republic fiel mit Auffuellung
                # auf -735 und die Warnung blieb stehen, obwohl die Regel sie
                # beheben sollte. Undatierte Eingaenge bleiben am 28.
                add(day, event.amount_cents, event.label,
                    cost_side=getattr(event, "reduces_cost", False),
                    zuerst=event.day is not None and event.amount_cents > 0)

        # The trough is the running minimum through the month in date order --
        # not simply "balance after every cost". Ordering is what distinguishes
        # a funded payment from an overdraft.
        running = balance
        trough = balance
        for _, _, amount in sorted(sequence, key=lambda t: (t[0], t[1])):
            running += amount
            trough = min(trough, running)
        closing = running
        swept = 0
        if sweep_above_cents is not None and closing > sweep_above_cents:
            swept = closing - sweep_above_cents
            closing = sweep_above_cents
        projection.rows.append(
            MonthRow(
                month=month,
                opening_cents=balance,
                costs_cents=costs,
                trough_cents=trough,
                income_cents=income,
                closing_cents=closing,
                breaches_dispo=trough < dispo_threshold_cents,
                swept_cents=swept,
                detail=detail,
            )
        )
        balance = closing

    return projection
