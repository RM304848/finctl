"""Wie viel Prozent von Barista FIRE stehen 2045?

Eine Tragfaehigkeitsrechnung, und sie braucht keine einzige Zeile -- zwanzig
Jahreszeilen genuegen. Das unterscheidet sie von der Liquiditaetsprognose in
ops.py, die Zeilen je Konto und die Intramonatssenke braucht, um "passt die
Rate in den Monat" zu beantworten.

Sie benutzt DIESELBEN Bloecke wie die Abstimmzeile. Das ist der Grund, warum
die Abstimmzeile zuerst gebaut wurde: waere hier eine zweite Einteilung, dann
prueft die Abstimmzeile etwas anderes als das, was gerechnet wird, und ihre
Null-Differenzen saeaen Vertrauen, das sie nicht gedeckt haben.

Reine Funktion. Keine Tabelle, kein Cache, nichts zu regenerieren -- die
Rechnung ist billig genug, um sie bei jedem Aufruf zu wiederholen, und was
nicht gespeichert wird, kann nicht veralten.

ALLES NOMINAL. Kosten und Einnahmen werden mit der Inflationsrate aus
assumptions.yaml fortgeschrieben; Kreditraten NICHT, denn eine Annuitaet ist
nominal fest und wird real jedes Jahr billiger. Das ist der stille Vorteil des
Kreditnehmers und darf nicht weginflationiert werden.

DREI TOEPFE statt eines Kapitalstocks. Tagesgeld, Depot und Policen wachsen
verschieden schnell, und ein Satz fuer alles haette entweder das Depot mit
Tagesgeldzinsen gerechnet oder das Tagesgeld mit Aktienrendite. Die Sparrate
fuellt das Tagesgeld bis zum Tagesgeld-Ziel, alles darueber geht ins Depot --
siehe `kaskade` und den Abschnitt kapital in assumptions.yaml.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from finctl import assumptions as ann
from finctl import objekte as _objekte
from finctl import person as _person
from finctl import renten as _renten
from finctl.forecast import abgleich as ag
from finctl.forecast import szenarien as _sz
from finctl.forecast.herkunft import Posten, eur, faktor, monat, prozent, summe
from finctl.pfade import CONFIG_DIR

#: Bloecke, deren Jahreswert aus dem gemessenen Basisjahr kommt und dann mit
#: der Inflation mitlaeuft.
FROM_BASE = ("miete", "einkommen_sonst", "fixkosten", "immobilie",
             "konsum", "steuern")

#: Bloecke mit eigener Quelle. Der Basiswert waere hier falsch.
#:
#: `kredit` kommt aus den Tilgungsplaenen -- exakt bis zur Tilgung, und eine
#: fortgeschriebene Durchschnittsrate wuerde das Auslaufen eines Kredits
#: verschweigen.
#:
#: `sondereffekt` sind datierte Einmalvorgaenge. Sie fortzuschreiben hiesse,
#: einmalige Kaufraten jedes Jahr erneut zu zahlen.
#:
#: `investment` faellt ganz heraus: ein Kauf verschiebt Geld vom Konto ins
#: Depot, und beide zaehlen zum Ziel. Ihn als Ausgabe zu fuehren zoege
#: dasselbe Geld zweimal ab.
#: Der Objektblock haette aus dem Basisjahr einen einzelnen Mietmonat auf
#: acht verteilt und diese Verwaesserung zwanzig Jahre fortgeschrieben --
#: 163 im Monat statt 1.420.
SPECIAL = ("gehalt", "kredit", "sondereffekt", ag.OBJEKT)
IGNORED = ("investment",)

#: Bloecke, deren laufender Saldo als "rein" zaehlt.
_EINNAHMEBLOECKE = ("gehalt", "miete", "einkommen_sonst", ag.OBJEKT, "rente")

#: Was im Ruhestand hereinkommt. Kein Block des Ledgers -- gebucht ist noch
#: keine Rente --, sondern aus config/renten.yaml.
RENTE = "rente"


@dataclass(slots=True)
class Year:
    year: int
    blocks: dict[str, int] = field(default_factory=dict)
    opening_cents: int = 0
    #: Rendite aller Toepfe, die Vorabpauschale schon abgezogen.
    return_cents: int = 0
    #: Die Toepfe am Jahresende. Ihre Summe ist closing_cents.
    tagesgeld_cents: int = 0
    depot_cents: int = 0
    policen_cents: int = 0
    #: Bis wohin das Tagesgeld gefuellt wird: eine Jahresausgabe.
    puffer_grenze_cents: int = 0
    #: Was vom Tagesgeld ins Depot ging. Negativ: aus dem Depot gedeckt.
    ins_depot_cents: int = 0
    #: Die Vorabpauschale dieses Jahres.
    steuer_cents: int = 0
    #: Der Rechenweg: je Block die Posten, aus denen er summiert ist, dazu
    #: `rendite` (ergibt return_cents) und `kaskade` (ergibt ins_depot_cents).
    posten: dict[str, list[Posten]] = field(default_factory=dict)

    @property
    def saving_cents(self) -> int:
        """Einnahmen minus Ausgaben. Vorzeichen wie im Ledger."""
        return sum(self.blocks.values())

    @property
    def closing_cents(self) -> int:
        return self.opening_cents + self.return_cents + self.saving_cents

    def cashflow(self) -> dict[str, int]:
        """Rein und raus in Summe, laufend und einmalig getrennt.

        Die vier Zahlen ergeben zusammen die Sparrate. Laufend wird je Block
        verrechnet: eine Erstattung in den Fixkosten mindert die Kosten und
        ist keine Einnahme, ein wegfallender Tankposten ebenso. Einmalig heisst
        eine Planzeile mit Frequenz einmalig oder ein Verkaufserloes.
        """
        out = {"laufend_rein": 0, "laufend_raus": 0,
               "einmalig_rein": 0, "einmalig_raus": 0}
        for block, liste in self.posten.items():
            if block not in self.blocks:
                continue
            netto = sum(p.cents for p in liste if not p.einmalig)
            if block in _EINNAHMEBLOECKE:
                out["laufend_rein"] += netto
            elif block == "sondereffekt":
                out["laufend_rein" if netto > 0 else "laufend_raus"] += netto
            else:
                out["laufend_raus"] += netto
            for p in liste:
                if p.einmalig:
                    out["einmalig_rein" if p.cents > 0 else "einmalig_raus"] += p.cents
        return out

    @property
    def frei_cents(self) -> int:
        """Endkapital ohne den Notgroschen -- was davon Zielkapital ist.

        Der Notgroschen liegt im Tagesgeld-Topf, bis zur Grenze der Kaskade.
        """
        return self.closing_cents - min(max(self.tagesgeld_cents, 0),
                                        self.puffer_grenze_cents)


#: Was auch ohne Erwerbsarbeit weiterfliesst.
#:
#: `gehalt` fehlt hier, und das ist der ganze Punkt. `sondereffekt` ebenfalls:
#: eine einmalige Zahlung deckt keinen laufenden Bedarf, und sie
#: mitzuzaehlen liesse ein Jahr mit einer grossen Erstattung wie erreichte
#: Unabhaengigkeit aussehen.
PASSIV = ("miete", ag.OBJEKT, "einkommen_sonst", "rente")

#: Was laufend zu decken ist. Kreditraten gehoeren dazu -- sie laufen weiter,
#: ob gearbeitet wird oder nicht, und sie enden von selbst.
BEDARF = ("fixkosten", "immobilie", "konsum", "steuern", "kredit")


@dataclass(slots=True)
class Projection:
    years: list[Year] = field(default_factory=list)
    base_year: int = 0

    @property
    def reicht_bis(self) -> int | None:
        """Das letzte Jahr, bevor das Tagesgeld ins Minus faellt -- oder None,
        wenn das Kapital bis zum Ende der Rechnung reicht.

        Im Ruhestand fuellt das Depot das Tagesgeld auf; faellt es trotzdem
        ins Minus, ist das Depot leer.
        """
        for y in self.years:
            if y.tagesgeld_cents < 0:
                return y.year - 1
        return None
    target_cents: int = 0

    @property
    def final_cents(self) -> int:
        return self.years[-1].closing_cents if self.years else 0

    @property
    def pct_of_target(self) -> float:
        if not self.target_cents:
            return 0.0
        # Gegen das Ziel zaehlt, was ohne den Notgroschen da ist.
        frei = self.years[-1].frei_cents if self.years else 0
        return 100.0 * frei / self.target_cents

    def year(self, which: int) -> Year:
        return next(y for y in self.years if y.year == which)

    def zum(self, stichtag: date, wert) -> int:
        """Ein Wert zum Stichtag, zwischen zwei Jahresenden anteilig.

        Die Jahresrechnung kennt nur Jahresenden. Ein Ziel zum 01.01.2027 gegen
        das Ende von 2027 zu halten, zaehlte ein ganzes Jahr mit, das am
        Stichtag noch nicht war: die Kaufraten vom Oktober 2026 waeren darin
        laengst wieder aufgefuellt, und der Balken stuende gruen, wo er rot
        sein muss. Also der Stand am Ende des Vorjahres, plus der Teil des
        Stichtagsjahres, der bis dahin vergangen ist.

        `wert` macht aus einer Jahreszeile die Zahl, um die es geht -- ein
        Topf, eine Summe von Toepfen, das Kapital ohne Notgroschen.
        """
        erstes, letztes = self.years[0].year, self.years[-1].year
        if stichtag.year > letztes:
            return wert(self.years[-1])
        if stichtag.year <= erstes:
            return wert(self.years[0])
        vorher = wert(self.year(stichtag.year - 1))
        dann = wert(self.year(stichtag.year))
        beginn = date(stichtag.year, 1, 1)
        anteil = (stichtag - beginn).days / (date(stichtag.year + 1, 1, 1) - beginn).days
        return round(vorher + (dann - vorher) * anteil)

    @property
    def crossing_year(self) -> int | None:
        """Ab wann laufendes Einkommen ohne Gehalt den Bedarf deckt.

        Die Zahl, um die es eigentlich geht -- und eine andere als "wie viel
        steht 2045". Ein Endkapital beantwortet, ob man aufhoeren KANN; das
        hier beantwortet, ab wann man nicht mehr MUSS.

        Das erste Jahr, ab dem es dauerhaft gilt, nicht das erste, in dem es
        zufaellig gilt: ein einzelnes gutes Jahr zwischen zwei schlechten ist
        kein Wendepunkt. Deshalb muss die Deckung bis zum Ende der Projektion
        halten.
        """
        for i, row in enumerate(self.years):
            if all(_covers(spaeter) for spaeter in self.years[i:]):
                return row.year
        return None

    def coverage_pct(self, which: int) -> float:
        row = self.year(which)
        bedarf = -sum(row.blocks.get(b, 0) for b in BEDARF)
        if bedarf <= 0:
            return 100.0
        return 100.0 * sum(row.blocks.get(p, 0) for p in PASSIV) / bedarf


def kaskade(tagesgeld: int, depot: int, policen: int, *, sparrate: int,
            grenze: int, satz_tagesgeld: float, satz_depot: float,
            vorab_satz: float, steuer_quote: float, anteil: float = 1.0,
            abfluss_gewichtet: int = 0,
            depot_abfluss: int = 0, entnahme: bool = False,
            einstand: int | None = None) -> dict[str, int]:
    """Ein Jahr der Kapitalkaskade. Rein, damit sie ohne Ledger pruefbar ist.

    Rendite auf den Anfangsbestand. Bewusst nicht auf den Zufluss des Jahres:
    der kommt ueber das Jahr verteilt, und ihn voll zu verzinsen schmeichelt
    der Rechnung.

    Die Sparrate landet auf dem Tagesgeld. Liegt dort danach mehr als die
    Grenze, geht der Rest ins Depot. Liegt weniger, bleibt alles liegen -- der
    Puffer fuellt sich zuerst wieder auf, und das Depot wird dafuer nicht
    verkauft.

    AUCH EIN MINUS im Tagesgeld deckt sich nicht von selbst aus dem Depot.
    Frueher tat es das, und beim Eigenheim leerte es unbemerkt das ganze
    Depot -- eine Entnahme, die niemand eingetragen hatte. Wer Anteile
    verkaufen will, schreibt eine Planzeile auf ein Depotkonto; die kommt als
    `depot_abfluss` hier an. Bleibt das Tagesgeld negativ, steht es negativ
    da: das ist die Aussage, dass der Plan so nicht finanziert ist.

    IM RUHESTAND IST DAS ANDERS (`entnahme`): dort ist die Entnahme aus dem
    Depot der Plan und nichts, was jemand eintragen muss. Ab Rentenbeginn
    fuellt das Depot das Tagesgeld bis zur Grenze wieder auf, solange es
    reicht.

    JEDER VERKAUF KOSTET STEUER auf seinen Gewinnanteil: Wert minus
    `einstand` (was eingezahlt und schon als Vorabpauschale versteuert ist),
    im Verhaeltnis zum Depotwert. Gezahlt wird sie zusaetzlich aus dem Depot.
    Ohne `einstand` gilt der Anfangsbestand als Einstand: was bis heute
    gewachsen ist, kennt die Rechnung nicht.

    Die Vorabpauschale faellt jedes Jahr an, auch ohne Verkauf, und hoechstens
    auf den tatsaechlichen Wertzuwachs.

    Die Policen sind fondsgebunden und wachsen mit dem Depotsatz -- aber ohne
    Vorabpauschale, der Versicherungsmantel schuetzt sie davor. Aus der
    Sparrate gespeist werden sie nicht.

    `anteil` ist der Teil des Jahres, der noch projiziert wird. Im Basisjahr
    liefen nur die Restmonate, verzinst wurde aber der Anfangsbestand ein
    ganzes Jahr -- im September gerechnet vier Monate Rendite als zwoelf,
    rund 3.000 zu viel.

    `abfluss_gewichtet` sind datierte Einmalzahlungen des Jahres, jede mit
    dem Teil des Jahres gewichtet, in dem sie schon weg ist. Die Kaufraten
    im Oktober nahmen dem Tagesgeld 66.000, und verzinst wurde trotzdem der
    Stand davor. Einnahmen werden nicht umgekehrt gewichtet: sie kommen wie
    die Sparrate uebers Jahr und bleiben unverzinst.
    """
    basis_tagesgeld = max(round(max(tagesgeld, 0) * anteil) - abfluss_gewichtet, 0)
    zins_tagesgeld = round(basis_tagesgeld * satz_tagesgeld)
    zins_policen = round(policen * satz_depot * anteil)
    gewinn = round(depot * satz_depot * anteil)
    vorab = (round(depot * min(vorab_satz, max(satz_depot, 0.0)) * anteil)
             if depot > 0 else 0)
    steuer = round(vorab * steuer_quote)

    tg = tagesgeld + zins_tagesgeld + sparrate
    wert = depot + gewinn - steuer
    # Die eingetragene Entnahme wirkt VOR der Kaskade: was heute vom Depot
    # genommen wird, kann heute nicht mehr aus dem Tagesgeld dorthin fliessen.
    dp = wert + depot_abfluss
    ins_depot = 0
    if tg > grenze:
        ins_depot = tg - max(grenze, 0)
    elif entnahme and tg < grenze and dp > 0:
        ins_depot = -min(grenze - tg, dp)
    verkauf, verkaufsteuer, einstand_neu = _verkaufsteuer(
        wert, depot if einstand is None else einstand, vorab, depot_abfluss,
        ins_depot, steuer_quote)
    verkaufsteuer = min(verkaufsteuer, max(dp + ins_depot, 0))
    return {"tagesgeld": tg - ins_depot, "depot": dp + ins_depot - verkaufsteuer,
            "policen": policen + zins_policen,
            "rendite": zins_tagesgeld + zins_policen + gewinn - steuer - verkaufsteuer,
            "steuer": steuer, "ins_depot": ins_depot,
            "zins_tagesgeld": zins_tagesgeld, "zins_policen": zins_policen,
            "gewinn_depot": gewinn, "basis_tagesgeld": basis_tagesgeld,
            "tagesgeld_vor_kaskade": tg, "verkauf": verkauf,
            "verkaufsteuer": verkaufsteuer, "einstand": einstand_neu}


def _verkaufsteuer(wert: int, einstand: int, vorab: int, depot_abfluss: int,
                   ins_depot: int, steuer_quote: float) -> tuple[int, int, int]:
    """(verkauft, Steuer darauf, Einstand danach) fuer ein Jahr.

    Der Gewinnanteil ist, was am Depotwert nicht Einstand ist; die schon
    gezahlte Vorabpauschale zaehlt zum Einstand, sie wird nicht zweimal
    versteuert. Ein Verkauf nimmt seinen Anteil am Einstand mit, ein Kauf
    legt seinen Betrag dazu.
    """
    einstand = max(einstand, 0) + vorab
    verkauf = max(-depot_abfluss, 0) + max(-ins_depot, 0)
    kauf = max(depot_abfluss, 0) + max(ins_depot, 0)
    if wert <= 0:
        return verkauf, 0, kauf
    gewinnanteil = max(0.0, 1.0 - einstand / wert)
    steuer = round(verkauf * gewinnanteil * steuer_quote)
    rest = einstand * max(0.0, 1.0 - verkauf / wert)
    return verkauf, steuer, round(rest) + kauf


def _covers(row: Year) -> bool:
    """Deckt das laufende Einkommen dieses Jahres den laufenden Bedarf?"""
    passiv = sum(row.blocks.get(p, 0) for p in PASSIV)
    bedarf = -sum(row.blocks.get(b, 0) for b in BEDARF)
    return passiv >= bedarf


def _base_kategorien(conn: sqlite3.Connection, f,
                     ohne: set[str] | None = None, *,
                     auch_abgewaehlte: bool = False) -> dict[str, dict[str, int]]:
    """Der gemessene Jahreswert je Block und Kategorie.

    Der Blockwert ist die Summe seiner Kategorien. Rundungsdifferenzen zum
    frueheren Blockwert sind Cent je Kategorie und der Preis dafuer, dass der
    Rechenweg exakt aufgeht.

    Was unter Prognosebasis (/annahmen) abgewaehlt ist, faellt heraus -- in den laufenden
    Bloecken. Gehalt, Kredit und Sondereffekte haben ihre eigene Quelle.
    """
    from finctl.forecast import prognosebasis as _pb

    if f is None:
        return {}
    aus = set() if auch_abgewaehlte else _pb.nicht_fortschreiben()
    return {block: {k: round(c / f.monate * 12) for k, c in kat.items()
                    if not (block in FROM_BASE and k in aus)}
            for block, kat in ag.measure_kategorien(conn, f, ohne).items()}


def _tilgungsplaene(aktive_klammern: set[str] | None) -> list[tuple[dict, object]]:
    """Die wirksamen Kredite mit ihrem Tilgungsplan -- einmal gerechnet."""
    from finctl.realestate.loan import (
        amortise,
        lade_kredite,
        nur_wirksame,
        opening_balance_cents,
        segments_from,
    )

    out = []
    for loan in nur_wirksame(lade_kredite(CONFIG_DIR), aktive_klammern or set()):
        try:
            sched = amortise(loan["id"], opening_balance_cents(loan),
                             segments_from(loan))
        except Exception:
            continue
        out.append((loan, sched))
    return out


def _kreditposten(plaene: list[tuple[dict, object]], year: int,
                  after_month: int = 0,
                  endet: dict[str, date] | None = None) -> list[Posten]:
    """Je Kredit die Raten dieses Jahres, mit Anzahl, Rate und Ende."""
    out = []
    for loan, sched in plaene:
        schluss = (endet or {}).get(loan["id"])
        raten = [p for p in sched.payments
                 if p.month.year == year and p.month.month > after_month
                 and not (schluss and p.month >= schluss)]
        if not raten:
            continue
        cents = -sum(p.payment_cents for p in raten)
        betraege = sorted({p.payment_cents for p in raten})
        rate = (eur(betraege[0]) if len(betraege) == 1
                else f"{eur(betraege[0])}–{eur(betraege[-1])}")
        text = (f"{len(raten)} Raten × {rate} laut Tilgungsplan "
                f"({monat(raten[0].month)}–{monat(raten[-1].month)})")
        if schluss and schluss.year == year:
            text += f"; endet {monat(schluss)} durch Verkauf"
        elif raten[-1].month == sched.payments[-1].month:
            text += "; letzte Rate des Plans"
        name = str(loan.get("name") or loan["id"]).split(" --")[0]
        out.append(Posten(label=name, cents=cents, quelle="vertrag",
                          herleitung=text, verweis=str(loan["id"])))
    return out


@dataclass(slots=True)
class Verkauf:
    """Ein geplanter Verkauf, so wie er in der Jahresrechnung ankommt."""

    property_id: str
    name: str
    ab: date                      # erster Monat OHNE das Objekt
    erloes_cents: int = 0         # was der Verkauf einbringt, brutto
    restschuld_cents: int = 0     # was davon die Bank bekommt
    loan_id: str | None = None
    miete_jahr_cents: int = 0     # gemessen, Vorzeichen wie im Ledger
    kosten_jahr_cents: int = 0
    #: Was vom Preis vorher abgeht: Makler, Vorfaelligkeitsentschaedigung.
    verkaufskosten_cents: int = 0
    #: Aus welcher Planklammer, wenn der Verkauf dort steht; None heisst
    #: aus den Objektdaten auf /immobilien.
    klammer: str | None = None
    klammer_id: str | None = None
    #: Auf welchen Monat sich die Restschuld bezieht, wenn der Tilgungsplan
    #: nicht bis zum Verkauf reicht.
    restschuld_stand: date | None = None

    @property
    def netto_cents(self) -> int:
        return self.erloes_cents - self.verkaufskosten_cents - self.restschuld_cents


def restschuld(loan: dict, zum: date) -> tuple[int, date]:
    """Was von einem Kredit offen ist, und auf welchen Monat sich das bezieht.

    Aus dem Tilgungsplan, nicht geschaetzt: die Zahl entscheidet, wie viel von
    einem Verkaufserloes tatsaechlich beim Verkaeufer ankommt, und bei einem
    Objekt mit anfangs unter 1 % Tilgung im Jahr ist der Unterschied zwischen
    Kaufpreis und Nettozufluss der halbe Preis.

    Reicht der Plan nicht bis zum gefragten Monat, kommt der letzte bekannte
    Stand zurueck, zusammen mit seinem Datum. Ein geplanter Verkauf nach dem
    Ende der Zinsbindung ist genau dieser Fall: der Plan endet dort, der
    Verkauf liegt spaeter, und was die Anschlusskondition bis dahin tilgt, weiss
    niemand. Null zurueckzugeben waere die gefaehrliche Luege -- sie machte
    aus einem Verkauf mit 115.000 Restschuld einen ohne.
    """
    from finctl.realestate.loan import amortise, opening_balance_cents, segments_from

    sched = amortise(loan["id"], opening_balance_cents(loan), segments_from(loan))
    if not sched.payments:
        return 0, zum
    offen = [p for p in sched.payments if p.month >= zum]
    if offen:
        return offen[0].balance_cents + offen[0].principal_cents, offen[0].month
    letzte = sched.payments[-1]
    return letzte.balance_cents, letzte.month


def restschuld_cents(loan: dict, zum: date) -> int:
    """Nur die Zahl. Siehe `restschuld` fuer den Stichtag dazu."""
    return restschuld(loan, zum)[0]


def verkaeufe(conn: sqlite3.Connection, base_year: int,
              szenarien: Path | str | None = None) -> list[Verkauf]:
    """Geplante Verkaeufe: aus Planklammern und aus den Objektdaten.

    EINE Quelle fuer die Rechnung. Eine Verkaufszeile in einer
    eingeschalteten Klammer gewinnt ueber Termin und Preis auf /immobilien --
    dort sind sie ein Vorschlag, in der Klammer eine schaltbare Entscheidung,
    und zwei Verkaeufe desselben Objekts gibt es nicht.

    Aus den Objektdaten wird ohne Preis nichts modelliert. Das ist die
    vorsichtige Richtung: den Kredit enden zu lassen, ohne den Erloes zu
    kennen, hiesse eine Rate streichen und nichts dafuer zahlen -- die
    Rechnung saehe besser aus, weil eine Zahl FEHLT.
    """
    from finctl.realestate.loan import lade_kredite

    namen = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM properties")}
    out: dict[str, Verkauf] = {}
    for s, _i, line in _sz.verkaufszeilen(_szenarien_laden(szenarien)):
        if line.property_id in out or line.property_id not in namen:
            continue
        out[line.property_id] = Verkauf(
            property_id=line.property_id, name=namen[line.property_id],
            ab=date(line.start.year, line.start.month, 1),
            erloes_cents=int(line.amount_cents),
            verkaufskosten_cents=int(line.kosten_cents or 0),
            klammer=s.name, klammer_id=s.id)

    for r in conn.execute(
            "SELECT id, name, planned_sale_on, sale_price_cents FROM properties "
            "WHERE planned_sale_on IS NOT NULL AND sold_on IS NULL "
            "AND sale_price_cents IS NOT NULL AND sale_price_cents > 0"):
        if r["id"] in out:
            continue
        ab = date.fromisoformat(str(r["planned_sale_on"]))
        out[r["id"]] = Verkauf(property_id=r["id"], name=r["name"],
                               ab=date(ab.year, ab.month, 1),
                               erloes_cents=int(r["sale_price_cents"] or 0))
    if not out:
        return []

    kredite = {k.get("property_id"): k for k in lade_kredite(CONFIG_DIR)}
    f = ag.fenster(conn)
    for v in out.values():
        loan = kredite.get(v.property_id)
        if loan:
            v.loan_id = loan["id"]
            try:
                v.restschuld_cents, stand = restschuld(loan, v.ab)
                if (stand.year, stand.month) != (v.ab.year, v.ab.month):
                    v.restschuld_stand = stand
            except Exception:
                v.restschuld_cents = 0
        # Miete und laufende Kosten aus DEMSELBEN Fenster wie die Basisbloecke,
        # sonst subtrahiert man eine anders gemessene Groesse von ihnen.
        gemessen = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN s.mgmt_category_id IN "
            "  ('immobilie/mieteinnahme','einkommen/mieteinnahmen') "
            "  THEN s.amount_cents END), 0) AS miete, "
            " COALESCE(SUM(CASE WHEN s.mgmt_category_id LIKE 'immobilie/%' "
            "  AND s.mgmt_category_id NOT IN "
            "  ('immobilie/mieteinnahme','einkommen/mieteinnahmen') "
            "  THEN s.amount_cents END), 0) AS kosten "
            "FROM splits s JOIN transactions t ON t.id = s.transaction_id "
            "WHERE s.property_id = ? AND t.booking_date BETWEEN ? AND ?",
            (v.property_id, f.von.isoformat() if f else "",
             f.bis.isoformat() if f else "")).fetchone()
        monate = f.monate if f else 12
        v.miete_jahr_cents = round((gemessen["miete"] or 0) / monate * 12)
        v.kosten_jahr_cents = round((gemessen["kosten"] or 0) / monate * 12)
    return sorted(out.values(), key=lambda v: v.ab)


def _monate_ohne(v: Verkauf, year: int, after_month: int) -> int:
    """Wie viele der noch zu projizierenden Monate dieses Jahres ohne Objekt."""
    return sum(1 for m in range(after_month + 1, 13) if date(year, m, 1) >= v.ab)


def _szenarien_laden(spec_path: Path | str | None = None) -> list:
    import yaml

    path = Path(spec_path) if spec_path else CONFIG_DIR / "szenarien.yaml"
    if not path.exists():
        return []
    return _sz.load(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


_FREQ_TEXT = {"monatlich": "monatlich", "quartalsweise": "quartalsweise",
              "jaehrlich": "jährlich"}


def _planposten(geladen: list, messungen: dict, year: int, after_month: int,
                depotkonten: frozenset[str] = frozenset()) -> tuple[
                    list[Posten], int, list[Posten]]:
    """Je Planzeile ein Posten -- und die Einmalabfluesse, fuer die Rendite.

    Der Betrag kommt aus `szenarien.jahreswirkung`, die Herleitung wird
    daneben nur beschrieben. So bleibt es EINE Rechnung.

    Die zweite Zahl sind die einmaligen Auszahlungen des Jahres, jede mit dem
    Teil des Jahres gewichtet, in dem das Geld schon weg ist -- siehe
    `kaskade`.

    Die dritte Liste sind die Zeilen, die auf einem DEPOTKONTO stehen. Sie
    gehoeren nicht aufs Tagesgeld: wer fuer den Hauskauf Anteile verkauft,
    belastet das Depot, und bis hierher war das Konto einer Planzeile in
    dieser Rechnung ohne jede Wirkung -- eine Zeile auf dem Depot minderte
    stillschweigend das Tagesgeld. Sie zaehlen deshalb auch nicht in den
    gewichteten Abfluss, der nur die Tagesgeldzinsen betrifft.
    """
    horizon = date(year, 12, 1)
    leer = {"cents": 0, "monate": 0, "buchungen": [], "quelle": "keine"}
    out: list[Posten] = []
    aus_depot: list[Posten] = []
    abfluss = 0.0
    for s in _sz.active(geladen):
        for i, line in enumerate(s.lines):
            m = messungen.get((s.id, i), leer)
            cents = _sz.jahreswirkung(line, m, year, after_month, horizon)
            if not cents:
                continue
            n = m.get("monate") or 0
            termine = [w for w in line.months(horizon)
                       if w.year == year and w.month > after_month]
            aufs_depot = line.account_id in depotkonten
            if line.frequency == "einmalig":
                text = f"einmalig {monat(line.start)}"
                if cents < 0 and not aufs_depot:
                    abfluss += -cents * sum(13 - w.month for w in termine) / 12
            elif line.kind == "wegfall":
                aktiv = sum(1 for mm in range(after_month + 1, 13)
                            if line.start and date(year, mm, 1) >= line.start
                            and (line.end is None or date(year, mm, 1) <= line.end))
                text = (f"entfällt: {eur(m['cents'])} gemessen über {n} Monate "
                        f"({len(m.get('buchungen') or [])} zugeordnete Buchungen) "
                        f"× {aktiv}/{n}")
            else:
                text = (f"{len(termine)} × {eur(line.amount_cents)} "
                        f"{_FREQ_TEXT.get(line.frequency, line.frequency)}")
                if n and m["cents"]:
                    text += (f", abzüglich des schon gemessenen Teils "
                             f"({eur(m['cents'])} in {n} Monaten)")
            if aufs_depot:
                text += f", vom Depot ({line.account_id})"
            (aus_depot if aufs_depot else out).append(
                Posten(label=f"{s.name}: {line.label}", cents=cents,
                       quelle="plan", herleitung=text,
                       verweis=f"{s.id}:{i}",
                       einmalig=line.frequency == "einmalig"))
    return out, round(abfluss), aus_depot


def _vertragsenden(conn: sqlite3.Connection, f, geladen: list,
                   base: dict) -> list[dict]:
    """Gekuendigte Vertraege, gemessen -- ohne dass jemand eine Zeile schreibt.

    EIN SACHVERHALT, EINE ANTWORT. Das Ende eines Vertrags steht schon am
    Vertrag. Die Kontoprognose las es und hoerte am Stichtag auf zu buchen;
    diese Rechnung kannte das Feld nicht und sah die Kuendigung erst, wenn die
    alten Buchungen aus dem Messfenster gelaufen waren -- zwoelf Monate lang
    eine Ersparnis, die es laengst gab. Wer sie frueher sehen wollte, musste
    dieselbe Kuendigung ein zweites Mal als Wegfall-Zeile eintragen.

    Gemessen wird wie bei einer Wegfall-Zeile: die Reihe des Vertrags im
    Fenster. Damit schrumpft der Abzug von selbst, sobald das Fenster ueber
    das Ende hinweglaeuft -- der Betrag steckt dann ja nicht mehr in der
    Basis, die er kuerzt.

    Wer beides angelegt hat -- Kuendigung am Vertrag UND Wegfall-Zeile --,
    bekommt den Abzug einmal: was eine eingeschaltete Zeile schon zaehlt,
    bleibt hier draussen.
    """
    from finctl import abos as _ab
    from finctl.ledger import reihe as _reihe

    if f is None:
        return []
    schon = {b["dedup_hash"]
             for s in _sz.active(geladen) for line in s.lines
             if line.kind == "wegfall"
             for b in (_sz.messung(conn, line, f).get("buchungen") or [])}
    block_je_kategorie = {k: block for block, kats in base.items() for k in kats}

    out = []
    for a in _ab.alle_vertraege():
        block = block_je_kategorie.get(a.get("kategorie"))
        if not a.get("gekuendigt_zum") or not a["buchungen"] or block not in FROM_BASE:
            continue
        treffer = [h for h in _reihe.hashes(conn, a["buchungen"], a["betrag_cents"])
                   if h not in schon]
        if not treffer:
            continue
        # Nur die Teile in den Kategorien der Reihe: eine aufgeteilte Buchung
        # traegt auch Teile, die den Vertrag nichts angehen, und die faellt
        # eine Kuendigung nicht weg.
        kategorien = {k for _, k in _reihe.paare(conn, a["buchungen"])}
        cents = conn.execute(
            "SELECT COALESCE(SUM(s.amount_cents), 0) FROM transactions t "
            "JOIN splits s ON s.transaction_id = t.id "
            f"WHERE t.dedup_hash IN ({','.join('?' * len(treffer))}) "
            f"  AND s.mgmt_category_id IN ({','.join('?' * len(kategorien))}) "
            "  AND t.booking_date BETWEEN ? AND ?",
            (*treffer, *sorted(kategorien),
             f.von.isoformat(), f.bis.isoformat())).fetchone()[0]
        if not cents:
            continue
        # NIE MEHR VERSPRECHEN, ALS DER VERTRAG KOSTET. Gemessen wird, was in
        # der Basis steckt -- aber eine angehakte Einmalzahlung (ein Geraet
        # zum Tarif) hebt den Monatsschnitt weit ueber die Rate. Die Ersparnis
        # sieht dann doppelt so gross aus wie sie ist, und das ist die
        # gefaehrliche Richtung. Der Rest bleibt in der Basis stehen, wo er
        # als unregelmaessiger Posten hingehoert.
        gemessen = -round(cents / f.monate * 12)
        laut_vertrag = -a["betrag_cents"] * 12 // (a["takt"] or 1)
        gedeckelt = bool(laut_vertrag) and abs(gemessen) > abs(laut_vertrag)
        out.append({"id": a["id"], "name": a["name"], "block": block,
                    "ende": a["gekuendigt_zum"], "cents": cents,
                    "jahr": laut_vertrag if gedeckelt else gemessen,
                    "gedeckelt": gedeckelt, "monate": f.monate,
                    "buchungen": len(treffer)})
    return out


def _vertragsende_posten(enden: list[dict], year: int, after_month: int,
                         seit: float, assumptions) -> dict[str, list[Posten]]:
    """Je Block die Abzuege dieses Jahres aus gekuendigten Vertraegen."""
    out: dict[str, list[Posten]] = {}
    for e in enden:
        aktiv = [mm for mm in range(after_month + 1, 13)
                 if date(year, mm, 1) > e["ende"]]
        if not aktiv:
            continue
        # Mit derselben Inflation wie die Basis, die er kuerzt. Sonst bliebe
        # nach einer Kuendigung ein wachsender Rest einer Zahlung stehen, die
        # es nicht mehr gibt.
        cents = round(ann.inflate(e["jahr"], seit, assumptions) * len(aktiv) / 12)
        if not cents:
            continue
        woher = (f"auf die Vertragsrate begrenzt; gemessen waren {eur(e['cents'])} "
                 f"in {e['monate']} Monaten, darin steckt Einmaliges"
                 if e["gedeckelt"] else
                 f"{eur(e['cents'])} gemessen über {e['monate']} Monate "
                 f"({e['buchungen']} Buchungen der Reihe)")
        out.setdefault(e["block"], []).append(Posten(
            label=f"{e['name']} gekündigt", cents=cents, quelle="vertrag",
            verweis=f"abo:{e['id']}",
            herleitung=(f"gekündigt zum {monat(e['ende'])}: {woher} "
                        f"× {len(aktiv)}/12 Monate ohne Vertrag")))
    return out


def gehalt_median_cents(conn: sqlite3.Connection, monate: int = 24,
                        vor: date | None = None) -> int | None:
    """Der Median der Gehaltsmonate, aus dem Ledger.

    GEMESSEN und nicht eingetragen. In assumptions.yaml stand 5.410 mit der
    Notiz "neu zu messen, wenn ein Jahr voll ist" -- die Notiz war schon
    abgelaufen, der Wert stammte aus 2026 allein, und ueber vierundzwanzig
    Monate liegt der Median bei 5.067,50. Das sind 126.576 Unterschied im
    Endkapital 2046, und niemand haette es bemerkt.

    Je MONAT, nicht je Buchung: ein Monat mit zwei Zahlungen ergaebe sonst
    zwei kleine statt einer richtigen Zahl. Der laufende Monat bleibt
    draussen, solange er unvollstaendig ist.

    Gibt None zurueck, wenn nichts zu messen ist -- eine frische Installation
    hat keine Gehaltsmonate, und dann gilt die Vorgabe aus der Datei.
    """
    import statistics

    if vor is not None:
        # Wie am Monatsersten `vor` gemessen -- fuer den Treffer-Check.
        von = ag._plus_monate(date(vor.year, vor.month, 1), -int(monate))
        werte = [r[0] for r in conn.execute(
            "SELECT SUM(s.amount_cents) FROM splits s "
            "JOIN transactions t ON t.id = s.transaction_id "
            "WHERE s.mgmt_category_id = 'einkommen/gehalt' "
            "  AND t.booking_date >= ? AND t.booking_date < ? "
            "GROUP BY substr(t.booking_date, 1, 7)",
            (von.isoformat(), date(vor.year, vor.month, 1).isoformat())) if r[0]]
        return int(round(statistics.median(werte))) if werte else None
    werte = [r[0] for r in conn.execute(
        "SELECT SUM(s.amount_cents) FROM splits s "
        "JOIN transactions t ON t.id = s.transaction_id "
        "WHERE s.mgmt_category_id = 'einkommen/gehalt' "
        f"  AND t.booking_date >= date('now', '-{int(monate)} months') "
        "  AND substr(t.booking_date, 1, 7) < strftime('%Y-%m', 'now') "
        "GROUP BY substr(t.booking_date, 1, 7)") if r[0]]
    return int(round(statistics.median(werte))) if werte else None


def _salary(year: int, base_year: int, path: Path | str,
            median_cents: int | None = None) -> int:
    """Das volle Monatsgehalt dieses Jahres, fortgeschrieben.

    Der MEDIAN, nicht der Floor. Der Floor beantwortet "gehe ich ins Dispo" --
    dort zaehlt der schlechteste Monat. Hier wird gefragt, was sich anhaeuft,
    und dafuer ist der schlechteste Monat keine Vorsicht, sondern eine
    Behauptung: ihn 228 Monate fortzuschreiben heisst, jeder kommende Monat
    werde der schlechteste der letzten 24. Der Unterschied war 386.000 im
    Endkapital, also mehr als die gesamte Renditeannahme wert ist.

    Fortgeschrieben mit der GEHALTSsteigerung, nicht mit der Inflation. Beide
    auf denselben Wert zu setzen unterstellt, dass die Inflation dauerhaft
    eingeholt wird -- moeglich, aber eine Annahme, die sichtbar sein soll.

    Teilzeit zieht davon ab, als eigener Posten aus der Planung
    (`_teilzeitposten`).
    """
    monthly = median_cents or ann.salary_median_cents(path)
    return ann.grow(monthly, year - base_year, ann.salary_growth_pa(path))


def _teilzeitposten(zeilen: list, year: int, after_month: int,
                    monatlich: int, rente_ab: date | None = None) -> list[Posten]:
    """Was die Teilzeit dieses Jahr vom Gehalt wegnimmt, je Planzeile.

    Monatsgenau: ein Wechsel im Juli laesst sechs Monate voll. Negativ und im
    Block `gehalt`, damit das Gehalt im Rechenweg voll dasteht und die
    Entscheidung daneben -- mit ihrem Preis.
    """
    je: dict[tuple[str, int], list] = {}
    for mm in range(after_month + 1, 13):
        if rente_ab is not None and date(year, mm, 1) >= rente_ab:
            break                       # ab der Rente gibt es kein Gehalt mehr
        z = _sz.teilzeit_im_monat(zeilen, date(year, mm, 1))
        if z is not None:
            je.setdefault((z[0].id, z[1]), [z, 0])[1] += 1
    out = []
    for (sid, i), ((s, _i, line), n) in je.items():
        weniger = monatlich - round(monatlich * line.anteil)
        if not weniger:
            continue
        out.append(Posten(
            label=f"{s.name}: {line.label}", cents=-weniger * n, quelle="plan",
            verweis=f"{sid}:{i}",
            herleitung=(f"{eur(monatlich)} je Monat × {round(100 - line.anteil * 100)} % "
                        f"weniger × {n} Monate ({round(line.anteil * 100)} % "
                        f"ab {monat(line.start)})")))
    return out


def _gehaltsposten(year: int, base_year: int, nach: int, share: float,
                   assumptions: Path | str, median: int | None,
                   rente_ab: date | None) -> list[Posten]:
    """Das volle Gehalt des Jahres -- bis zum Monat vor dem Rentenbeginn."""
    monatlich = _salary(year, base_year, assumptions, median)
    arbeit = _arbeitsmonate(year, nach, rente_ab)
    voll = arbeit == 12 - nach
    gehalt = round(monatlich * 12 * share) if voll else monatlich * arbeit
    if not gehalt:
        return []
    basis = median or ann.salary_median_cents(assumptions)
    wachstum = ann.salary_growth_pa(assumptions)
    text = ((f"Median {eur(basis)} je Monat (24 Monate gemessen)" if median
             else f"{eur(basis)} je Monat aus assumptions.yaml")
            + f" × {faktor((1 + wachstum) ** (year - base_year))} Gehaltssteigerung"
            + (" × 12" + (f" × {12 - nach}/12 Restmonate" if share < 1 else "") if voll
               else f" × {arbeit} Monate bis zum Rentenbeginn {monat(rente_ab)}"))
    return [Posten(label="Gehalt", cents=gehalt,
                   quelle="gemessen" if median else "annahme",
                   herleitung=text, verweis="einkommen/gehalt")]


def _anteile_fortschreiben(anteile: dict[str, int], vorher: int,
                           nachher: int) -> dict[str, int]:
    """Jede Police waechst mit dem Topf, damit ihre Auszahlung ihren Anteil
    nimmt und nicht den Durchschnitt."""
    if not vorher:
        return anteile
    return {kn: round(v * nachher / vorher) for kn, v in anteile.items()}


def _im_ruhestand(rente_ab: date | None, year: int) -> bool:
    return rente_ab is not None and year >= rente_ab.year


def _arbeitsmonate(year: int, after_month: int, rente_ab: date | None) -> int:
    """Monate dieses Jahres mit Gehalt: nach den gebuchten, vor der Rente."""
    return sum(1 for mm in range(after_month + 1, 13)
               if rente_ab is None or date(year, mm, 1) < rente_ab)


def _rentenposten(quellen: list, year: int, after_month: int, inflation: float,
                  abzug: float) -> list[Posten]:
    """Je Rente ein Posten: der Betrag des Schreibens, nominal und nach Abzug.

    Heutige Kaufkraft waechst mit der Inflation -- Rentenanpassungen sind
    hier die Inflation, nicht mehr. Ein Kapital ohne Police im Bestand kommt
    einmal, ohne Abzug: versteuert wird nur sein Ertrag.
    """
    out = []
    for q in quellen:
        beginn = _renten.beginn(q)
        if beginn is None:
            continue
        verweis = f"rente:{q.id}"
        if q.art == "kapital":
            if q.konto is None and beginn.year == year and beginn.month > after_month:
                cents = _renten.nominal_cents(q, year, inflation)
                netto = round(cents * (1 - abzug))
                out.append(Posten(label=q.name, cents=netto, quelle="vertrag",
                                  verweis=verweis, einmalig=True,
                                  herleitung=(f"Kapital laut Schreiben {eur(cents)}, "
                                              f"{monat(beginn)} − {round(abzug * 100)} % "
                                              "Steuer und KV")))
            continue
        n = sum(1 for mm in range(after_month + 1, 13) if date(year, mm, 1) >= beginn)
        if not n:
            continue
        je_monat = _renten.nominal_cents(q, year, inflation)
        netto = round(je_monat * (1 - abzug))
        out.append(Posten(
            label=q.name, cents=netto * n, quelle="vertrag", verweis=verweis,
            herleitung=(f"{eur(q.cents)} laut Schreiben"
                        + (f" × {faktor(je_monat / q.cents)} Inflation" if q.cents
                           and q.kaufkraft == "heute" else "")
                        + f" − {round(abzug * 100)} % Steuer und KV = {eur(netto)}"
                        f" je Monat × {n} Monate ab {monat(beginn)}")))
    return out


def _policenwechsel(quellen: list, year: int, anteile: dict[str, int],
                    abzug: float = 0.0) -> tuple[list[Posten], list[Posten], int, int]:
    """Policen, die dieses Jahr auszahlen: ihr Anteil verlaesst den Topf.

    Als Kapital geht er ins Depot -- eine Umschichtung, kein Geldfluss. Als
    Rente wird er verrentet: er verlaesst das Vermoegen, und die Rente kommt
    dafuer als Einnahme. Die Police waechst bis dahin mit dem Depotsatz; der
    Betrag des Schreibens steht zum Vergleich daneben.

    Auf ein ausgezahltes Kapital geht derselbe Abzug wie auf eine Rente --
    bewusst vorsichtig, ohne die Schichten und Altvertraege zu unterscheiden.
    Er wird aus dem Tagesgeld bezahlt und steht deshalb im Block `rente`.

    Gibt zurueck: Posten fuer den Block `rente` (verrentet, Abzug), Posten
    zum Lesen (umgeschichtet), was ins Depot und was aufs Tagesgeld geht.
    """
    verrentet, umgeschichtet, ins_depot, aufs_tagesgeld = [], [], 0, 0
    for q in quellen:
        beginn = _renten.beginn(q)
        wert = anteile.get(q.konto or "")
        if not wert or beginn is None or beginn.year != year:
            continue
        anteile[q.konto] = 0
        if q.art == "kapital":
            ins_depot += wert
            if abzug:
                verrentet.append(Posten(
                    label=f"{q.name}: Steuer und KV auf die Auszahlung",
                    cents=-round(wert * abzug), quelle="annahme", verweis=f"rente:{q.id}",
                    einmalig=True,
                    herleitung=f"{eur(wert)} × {round(abzug * 100)} %, aus dem Tagesgeld"))
            umgeschichtet.append(Posten(
                label=f"{q.name} ausgezahlt ins Depot", cents=wert, quelle="regel",
                verweis=f"rente:{q.id}",
                herleitung=(f"Policenwert fortgeschrieben mit dem Depotsatz, "
                            f"{monat(beginn)}; laut Schreiben {eur(q.cents)}"
                            + (" in heutiger Kaufkraft" if q.kaufkraft == "heute" else ""))))
        else:
            aufs_tagesgeld += wert
            verrentet.append(Posten(
                label=f"{q.name}: Guthaben verrentet", cents=-wert, quelle="regel",
                verweis=f"rente:{q.id}", einmalig=True,
                herleitung=f"Policenwert zum Rentenbeginn {monat(beginn)}; "
                           "dafuer kommt die Monatsrente"))
    return verrentet, umgeschichtet, ins_depot, aufs_tagesgeld


def target_under(assumptions: Path | str, *, declared_cents: int,
                 declared_inflation: float, years: int) -> int:
    """Das Ziel unter den Annahmen DIESES Laufs.

    Ohne diese Umrechnung ist der Szenarienvergleich unstimmig, und zwar auf
    eine Art, die wie ein Rechenfehler aussieht: bei 3 % statt 2 % Inflation
    steigen Einnahmen UND Kosten schneller, die Kreditrate bleibt nominal
    fest, und als Nettosparer mit Festzinsdarlehen kommt er nominal besser
    heraus. Das pessimistische Szenario lieferte damit mehr Endkapital als die
    Basis.

    Oekonomisch stimmt das -- Inflation begünstigt Schuldner. Falsch war, dass
    ein in heutigen Preisen gedachtes Ziel als feste nominale Zahl stehen
    blieb: 831.000 kaufen bei 3 % Inflation weniger als bei 2 %. Das Ziel wird
    deshalb mit der Inflationsdifferenz skaliert, und dann faellt der Vergleich
    wieder richtig herum aus.
    """
    scenario = ann.inflation_pa(assumptions)
    if declared_inflation <= -1:
        return declared_cents
    factor = ((1.0 + scenario) / (1.0 + declared_inflation)) ** years
    return int(round(declared_cents * factor))


def _objekt(p, year: int, base_year: int, path: Path | str,
            after_month: int = 0) -> tuple[int, int]:
    """Miete minus laufende Kosten eines Objekts, fuer die Monate, die zaehlen.

    Eine eigene Quelle statt des gemessenen Basisjahrs: dort steht genau ein
    Mietmonat, und ueber acht vollstaendige Monate gemittelt waere das ein
    Bruchteil des Ertrags. Diese Verwaesserung zwanzig Jahre fortzuschreiben
    waere der groesste stille Fehler im ganzen Modell gewesen.

    Endet die Vermietung zu einem festen Datum (ein Leasehold), ist danach
    null -- kein Restwert, keine Einnahme. Gibt (Betrag, Monate) zurueck.
    """
    monate = sum(1 for m in range(after_month + 1, 13) if p.laeuft(date(year, m, 1)))
    if not monate:
        return 0, 0
    # Mit der Inflation fortgeschrieben wie die uebrigen laufenden Posten:
    # Miete und Kosten bewegen sich, und beide in heutigen Euro einzufrieren
    # waere eine stillere Annahme als sie mitlaufen zu lassen.
    return ann.inflate(p.netto_cents, year - base_year, path) * monate, monate


def _objektposten(prognosen, year: int, base_year: int, path: Path | str,
                  after_month: int, inflation: float) -> list[Posten]:
    """Je Objekt mit Prognose ein Posten, samt Rechenweg."""
    aus = []
    for p in prognosen:
        cents, _monate = _objekt(p, year, base_year, path, after_month)
        if not cents:
            continue
        aus.append(Posten(
            label=f"{p.name} netto", cents=cents, quelle="annahme",
            verweis=p.id,
            herleitung=(f"{eur(p.netto_cents)} je Monat "
                        f"(Miete {eur(p.miete_cents)} − Kosten {eur(p.kosten_cents)})"
                        f" × {faktor((1 + inflation) ** (year - base_year))} Inflation"
                        + (f", bis {monat(p.bis)}" if p.bis else ""))))
    return aus


def project(conn: sqlite3.Connection, *, base_year: int | None = None,
            end_year: int = 2045, opening_cents: int = 0,
            target_cents: int = 0,
            assumptions: Path | str = ann.PATH,
            szenarien: Path | str | None = None,
            toepfe: dict[str, int] | None = None,
            puffer_cents: int | None = None,
            policen_je_konto: dict[str, int] | None = None,
            depot_gewinn_cents: int = 0) -> Projection:
    """Jahr fuer Jahr bis zum Stichtag. Ein Szenario ist ein anderer Pfad.

    `toepfe` teilt den Anfangsbestand auf (siehe `ziele.toepfe`). Ohne sie
    liegt `opening_cents` ganz auf dem Tagesgeld.

    `policen_je_konto` teilt den Topf `policen` auf (siehe
    `ziele.policen_je_konto`): ohne ihn bleibt eine auszahlende Police im Topf.

    `puffer_cents` ist das Tagesgeld-Ziel, von Hand gesetzt (siehe
    `ziele.puffer_cents`). Was darueber liegt, geht ins Depot. Ohne Ziel
    bleibt alles auf dem Tagesgeld -- lieber zu wenig Rendite als eine, fuer
    die niemand eine Grenze gezogen hat.
    """
    if toepfe is None:
        toepfe = {"tagesgeld": opening_cents}
    tg, dp, po = (int(toepfe.get(k, 0)) for k in ("tagesgeld", "depot", "policen"))
    # Einstand ist, was heute im Depot steht, abzueglich des Gewinns, den der
    # Monatsabschluss nennt ("davon Gewinn"). Ohne Angabe gilt der ganze Wert
    # als Einstand: dann wird nur versteuert, was ab heute dazukommt.
    einstand = max(dp - int(depot_gewinn_cents or 0), 0)
    satz_depot = ann.rendite_depot_pa(assumptions)
    vorab_satz, steuer_quote = ann.vorabpauschale(assumptions)
    grenze = max(int(puffer_cents), 0) if puffer_cents is not None else None
    base_year = base_year or date.today().year
    # Gemessen wird ueber die letzten zwoelf vollstaendigen Monate, projiziert
    # ab heute. Einmalige Planpositionen, die schon gebucht sind, gehoeren
    # nicht in die Basis -- sonst kaeme die Anzahlung jedes Jahr wieder.
    # Welche Konten ein Depot sind. Dieselbe Sprache wie die Toepfe der
    # Bestaende (ziele.TOPF): `depot` ist eines, `broker` NICHT -- aus
    # Trade Republic wird laufend bezahlt, das waere ein stiller Umweg.
    depotkonten = frozenset(r[0] for r in conn.execute(
        "SELECT id FROM accounts WHERE active = 1 AND account_type = 'depot'"))
    f = ag.fenster(conn)
    geladen = _szenarien_laden(szenarien)
    base = _base_kategorien(conn, f, _sz.einmalig_zugeordnet(geladen))
    messungen = {(s.id, i): _sz.messung(conn, line, f)
                 for s in geladen for i, line in enumerate(s.lines)}
    enden = _vertragsenden(conn, f, geladen, base)
    rate = ann.rendite_nominal_pa(assumptions)
    inflation = ann.inflation_pa(assumptions)
    fenster_text = (f"{monat(f.von)}–{monat(f.bis)}" if f else "ohne Messung")

    def seit_messung(year: int) -> float:
        # Die Messung ist im Mittel ein halbes Jahr alt. Fortgeschrieben wird
        # von der Mitte des Fensters zur Mitte des Jahres; ab heute gerechnet
        # laege jede laufende Position rund ein Prozent zu niedrig.
        return (year + 0.5 - f.mitte) if f else float(year - base_year)

    # Vom Basisjahr sind Monate bereits vergangen, und sie stecken schon im
    # Anfangsbestand. Sie ein zweites Mal zu projizieren zaehlt sie doppelt --
    # bei acht von zwoelf Monaten ist das kein Rundungsfehler, sondern zwei
    # Drittel eines Jahres. Das Basisjahr laeuft deshalb nur ueber seine
    # RESTmonate, jedes weitere voll.
    # Nur was im LAUFENDEN Jahr schon gebucht ist, zaehlt als vergangen. Liegt
    # die Messung in einem frueheren Jahr, ist vom laufenden noch nichts
    # abgedeckt und es zaehlt voll.
    ende = ag.letzter_vollstaendiger_monat(conn)
    elapsed = ende.month if ende and ende.year == base_year else 0
    remaining = max(0, 12 - elapsed)
    verkauft = verkaeufe(conn, base_year, szenarien)
    kredit_endet = {v.loan_id: v.ab for v in verkauft if v.loan_id}
    # Ein geplanter Kredit zaehlt nur, solange seine Klammer an ist. Dieselbe
    # Klammer traegt die Anzahlung und den Wegfall der Miete, also bewegt EIN
    # Schalter die ganze Entscheidung statt dreier Stellen in zwei Dateien.
    aktive = _sz.aktive_ids(szenarien)
    plaene = _tilgungsplaene(aktive)
    # Einmal messen, nicht je Jahr: die Zahl haengt am Ledger und nicht am
    # projizierten Jahr.
    median = gehalt_median_cents(conn)
    teilzeit = _sz.teilzeitzeilen(geladen)
    rente_ab = _person.rentenbeginn()
    rentenquellen = _renten.quellen()
    rentenabzug = ann.abzug_renten(assumptions)
    anteile = dict(policen_je_konto or {})
    # Ein Szenario ist eine andere Annahmedatei; was es je Objekt anders
    # annimmt, steht dort unter immobilien.objekte.
    prognosen = _objekte.prognosen(None if str(assumptions) == str(ann.PATH)
                                   else assumptions)

    out = Projection(base_year=base_year, target_cents=target_cents)
    for year in range(base_year, end_year + 1):
        share = (remaining / 12) if year == base_year else 1.0
        anteil_text = f" × {remaining}/12 Restmonate" if share < 1 else ""
        nach = elapsed if year == base_year else 0
        posten: dict[str, list[Posten]] = {}

        fakt = (1.0 + inflation) ** seit_messung(year)
        # Was gekuendigt ist, kuerzt den Block, in dem es gemessen wurde.
        gekuendigt = _vertragsende_posten(enden, year, nach,
                                          seit_messung(year), assumptions)
        for block_id in FROM_BASE:
            liste = posten.setdefault(block_id, [])
            liste.extend(gekuendigt.get(block_id, ()))
            for kategorie, jahr_cents in sorted(base.get(block_id, {}).items()):
                grown = ann.inflate(jahr_cents, seit_messung(year), assumptions)
                cents = round(grown * share)
                if not cents:
                    continue
                hoch = (f" (aus {f.monate} Monaten aufs Jahr)"
                        if f and f.monate != 12 else "")
                liste.append(Posten(
                    label=kategorie or "(ohne Kategorie)", cents=cents,
                    quelle="gemessen", verweis=kategorie or None,
                    herleitung=(f"{eur(jahr_cents)} gemessen {fenster_text}{hoch}"
                                f" × {faktor(fakt)} Inflation{anteil_text}")))

        posten["gehalt"] = _gehaltsposten(
            year, base_year, nach, share, assumptions, median,
            rente_ab) + _teilzeitposten(
                teilzeit, year, nach, _salary(year, base_year, assumptions, median),
                rente_ab)
        verrentet, posten["policen"], zum_depot, zum_tagesgeld = _policenwechsel(
            rentenquellen, year, anteile, rentenabzug)
        posten[RENTE] = _rentenposten(rentenquellen, year, nach, inflation,
                                      rentenabzug) + verrentet

        # Kredite und datierte Posten tragen ihr eigenes Datum, sie brauchen
        # keinen Anteil -- aber im Basisjahr zaehlen nur die Monate, die noch
        # kommen, denn die frueheren stehen schon im Ledger.
        posten["kredit"] = _kreditposten(plaene, year, nach, kredit_endet)
        (posten["sondereffekt"], abfluss,
         posten["depotentnahme"]) = _planposten(geladen, messungen, year, nach,
                                                depotkonten)

        posten[ag.OBJEKT] = _objektposten(prognosen, year, base_year,
                                          assumptions, nach, inflation)

        # Ein verkauftes Objekt hoert auf, Miete zu bringen und Kosten zu
        # machen, und sein Erloes kommt einmal. Die Rate verschwindet schon in
        # _kreditposten. Alle drei gehoeren zusammen: nur die Rate zu
        # streichen liesse den Verkauf wie einen Gewinn aussehen, nur die
        # Miete zu streichen wie einen Verlust.
        for v in verkauft:
            weg = _monate_ohne(v, year, nach)
            if weg:
                for block_id, jahr_cents, was in (
                        ("miete", v.miete_jahr_cents, "Miete"),
                        ("immobilie", v.kosten_jahr_cents, "Kosten")):
                    cents = -round(ann.inflate(jahr_cents, seit_messung(year),
                                               assumptions) * weg / 12)
                    if cents:
                        posten[block_id].append(Posten(
                            label=f"Verkauf {v.name}: {was} entfällt", cents=cents,
                            quelle="plan", verweis=v.property_id,
                            herleitung=(f"{eur(jahr_cents)} gemessen {fenster_text}"
                                        f" × {faktor(fakt)} Inflation × {weg}/12"
                                        f", verkauft ab {monat(v.ab)}")))
            if year == v.ab.year and v.ab.month > nach:
                posten["sondereffekt"].append(Posten(
                    label=f"Verkauf {v.name}: Nettoerlös", cents=v.netto_cents,
                    quelle="plan", einmalig=True,
                    verweis=(f"{v.klammer_id}" if v.klammer_id else v.property_id),
                    herleitung=(f"Preis {eur(v.erloes_cents)}"
                                + (f" − Verkaufskosten {eur(v.verkaufskosten_cents)}"
                                   if v.verkaufskosten_cents else "")
                                + f" − Restschuld {eur(v.restschuld_cents)}"
                                + (f" (Stand {monat(v.restschuld_stand)}, "
                                   f"Tilgungsplan endet vorher)"
                                   if v.restschuld_stand else f" ({monat(v.ab)})")
                                + (f"; Klammer {v.klammer}" if v.klammer
                                   else "; Termin und Preis aus /immobilien"))))

        # Die Blocksummen AUS den Posten, nicht daneben gerechnet.
        blocks = {b: summe(posten[b]) for b in
                  (*FROM_BASE, "gehalt", "kredit", "sondereffekt", ag.OBJEKT, RENTE)}

        # Auszahlende Policen verlassen den Topf, bevor verzinst wird.
        anfang = tg + dp + po
        po -= zum_depot + zum_tagesgeld
        dp += zum_depot
        einstand += zum_depot
        tg += zum_tagesgeld
        k = kaskade(tg, dp, po, sparrate=sum(blocks.values()),
                    depot_abfluss=summe(posten["depotentnahme"]),
                    grenze=grenze if grenze is not None else 2**62,
                    satz_tagesgeld=rate, satz_depot=satz_depot,
                    vorab_satz=vorab_satz, steuer_quote=steuer_quote,
                    anteil=share, abfluss_gewichtet=abfluss,
                    entnahme=_im_ruhestand(rente_ab, year), einstand=einstand)

        abzug = (f" − {eur(abfluss)} Einmalzahlungen, anteilig nach Monaten ohne das Geld"
                 if abfluss else "")
        rendite = [
            Posten(label="Tagesgeld", cents=k["zins_tagesgeld"], quelle="rendite",
                   verweis="tagesgeld",
                   herleitung=(f"({eur(max(tg, 0))}{anteil_text}{abzug}) "
                               f"= {eur(k['basis_tagesgeld'])} × {prozent(rate)}")),
            Posten(label="Depot", cents=k["gewinn_depot"], quelle="rendite",
                   verweis="depot",
                   herleitung=f"{eur(dp)} × {prozent(satz_depot)}{anteil_text}"),
            Posten(label="Policen", cents=k["zins_policen"], quelle="rendite",
                   verweis="policen",
                   herleitung=f"{eur(po)} × {prozent(satz_depot)}{anteil_text}"),
            Posten(label="Vorabpauschale", cents=-k["steuer"], quelle="regel",
                   verweis="depot",
                   herleitung=(f"{eur(dp)} × {prozent(min(vorab_satz, max(satz_depot, 0.0)))}"
                               f"{anteil_text} × {prozent(steuer_quote)} Steuer"
                               ", ohne Teilfreistellung")),
            Posten(label="Steuer auf Depotverkauf", cents=-k["verkaufsteuer"],
                   quelle="regel", verweis="depot",
                   herleitung=(f"{eur(k['verkauf'])} verkauft, davon Gewinnanteil laut "
                               f"Einstand × {prozent(steuer_quote)}, ohne Teilfreistellung")),
        ]
        posten["rendite"] = [p for p in rendite if p.cents]
        posten["kaskade"] = []
        if k["ins_depot"]:
            vor = k["tagesgeld_vor_kaskade"]
            posten["kaskade"].append(Posten(
                label=("Tagesgeld über dem Ziel ins Depot" if k["ins_depot"] > 0
                       else "Im Ruhestand aus dem Depot entnommen"),
                cents=k["ins_depot"], quelle="regel", verweis="tagesgeld-puffer",
                herleitung=(f"Tagesgeld nach Sparrate und Zinsen {eur(vor)}, "
                            f"Grenze {eur(grenze or 0)}")))

        out.years.append(Year(
            year=year, blocks=blocks, opening_cents=anfang,
            return_cents=k["rendite"], tagesgeld_cents=k["tagesgeld"],
            depot_cents=k["depot"], policen_cents=k["policen"],
            puffer_grenze_cents=grenze or 0, ins_depot_cents=k["ins_depot"],
            steuer_cents=k["steuer"], posten=posten))
        anteile = _anteile_fortschreiben(anteile, po, k["policen"])
        tg, dp, po, einstand = k["tagesgeld"], k["depot"], k["policen"], k["einstand"]
    return out
