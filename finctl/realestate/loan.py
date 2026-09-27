"""Annuity loan amortisation, driven by dated segments.

Refinancing is not a special case here: it is simply the next segment. A
Zinsbindung expiry therefore produces a computed schedule rather than an
assumed one, which matters because the composition changes more than the
payment does.

A loan rolling off a cheap fixed period is the clearest example. The payment
rises by under 40 %, but interest roughly triples while amortisation falls by
a third. Paying more repays LESS principal -- invisible in any model that
tracks only the payment amount.

The schedule is validated against real statements: Sparda prints the
interest/principal split on every payment, so `verify_against_actuals` can
check the engine rather than trusting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from finctl.pfade import CONFIG_DIR


def opening_balance_cents(loan: dict) -> int:
    """Where a loan's schedule starts, in order of how well it is known.

    A balance read off a statement beats the original principal, because
    everything between them has already been repaid. `opening_balance_cents`
    is the general form; the 2026-01 name is kept for the loans that were
    written against it.
    """
    return (loan.get("opening_balance_cents")
            or loan.get("observed_balance_2026_01_cents")
            or loan["principal_cents"])


def an_szenario_gebunden(loan: dict) -> str | None:
    """Die Klammer, an der ein geplanter Kredit haengt -- oder None.

    Ein Kredit fuer eine Anschaffung, die es noch nicht gibt, darf nicht
    mitrechnen, solange die Anschaffung nicht eingeschaltet ist. Sonst zahlt
    die Prognose ab 2030 eine Rate fuer ein Haus, das niemand gekauft hat.
    """
    wert = loan.get("szenario")
    return str(wert) if wert else None


def nur_wirksame(loans: list[dict], aktive_klammern: set[str]) -> list[dict]:
    """Kredite ohne Klammer, plus die, deren Klammer eingeschaltet ist.

    Damit ist "Eigenheim 2030" gegen "Eigenheim 2034" eine Frage von zwei
    Klammern und zwei Kreditsaetzen, nicht von auskommentiertem YAML.
    """
    return [kredit for kredit in loans
            if not an_szenario_gebunden(kredit)
            or an_szenario_gebunden(kredit) in aktive_klammern]


def _als_datum(wert) -> date | None:
    if wert is None or wert == "":
        return None
    if isinstance(wert, date):
        return wert
    text = str(wert)
    return date.fromisoformat(text if len(text) > 7 else text + "-01")


def mit_vorlage(loan: dict, vorlage: dict) -> dict:
    """Die Grundkonditionen eines Vorlagenkredits ersetzen.

    Nur fuer Kredite, die an einer Klammer haengen. Die drei echten Vertraege
    sind aus Unterlagen rekonstruiert, teils rueckwaerts aus beobachteten
    Zahlungen -- sie im Dashboard ueberschreibbar zu machen hiesse, eine
    Tatsache durch eine Eingabe ersetzen zu koennen.

    Aus den Grundkonditionen wird EIN Segment. Die Zinsbindung kommt, wenn sie
    gebraucht wird, ueber dieselbe Anschluss-Eingabe wie bei den echten
    Krediten -- so muss die Vorlage nicht nachbauen, was es schon gibt.

    Fehlt die Rate, wird sie aus dem Enddatum gerechnet. Das ist die Richtung,
    in der man bei einem Hauskauf denkt: das Ende ist eine Entscheidung, die
    Rate ihr Preis.
    """
    if not vorlage:
        return loan
    neu = dict(loan)
    betrag = int(vorlage.get("principal_cents") or opening_balance_cents(loan))
    zins = float(vorlage.get("annual_rate_pct")
                 or (segments_from(loan)[0].annual_rate_pct
                     if segments_from(loan) else 0.0))
    von = _als_datum(vorlage.get("start")) or _als_datum(loan.get("start_date"))
    bis = _als_datum(vorlage.get("ende"))
    if von is None:
        return loan
    rate = vorlage.get("annuitaet_cents")
    if not rate and bis is not None:
        rate = annuitaet_fuer_monate(betrag, zins, monate_zwischen(von, bis))
    if not rate:
        rate = loan.get("payment_cents")
    if not rate or bis is None:
        return loan

    neu["principal_cents"] = betrag
    neu["opening_balance_cents"] = betrag
    neu["opening_balance_as_of"] = von.isoformat()
    neu["payment_cents"] = int(rate)
    neu["start_date"] = von.isoformat()
    # Datumsobjekte, keine Zeichenketten: `segments_from` reicht sie direkt an
    # `Segment` weiter, und YAML liefert an dieser Stelle ueberall date.
    neu["segments"] = [{"start": von, "end": bis,
                        "annual_rate_pct": zins, "annuity_cents": int(rate),
                        "basis": "annahme"}]
    return neu


def lade_kredite(config_dir: Path | str = CONFIG_DIR) -> list[dict]:
    """Alle Kredite, Vorlagen mit ihren im Dashboard gesetzten Konditionen.

    Eine Stelle statt sechs: loans.yaml wurde an sechs Orten eingelesen, und
    eine Ueberschreibung, die nur an fuenfen ankommt, ist schlimmer als gar
    keine -- die Prognose rechnete dann mit anderen Zahlen als die Seite, auf
    der man sie eingetragen hat.
    """
    import yaml

    basis = Path(config_dir) / "loans.yaml"
    spec = (yaml.safe_load(basis.read_text(encoding="utf-8")) or {}) \
        if basis.exists() else {}
    eigen = Path(config_dir) / "loans_custom.yaml"
    roh = (yaml.safe_load(eigen.read_text(encoding="utf-8")) or {}) \
        if eigen.exists() else {}
    vorlagen = roh.get("vorlagen") or {}
    # Was auf /kredite angelegt oder entfernt wurde. `entfernt: true` blendet
    # einen Kredit der Basisdatei aus, ohne deren Begruendung zu loeschen --
    # und ohne die zu loeschen, die ein anderer Nutzer nie haben wollte.
    eigene = dict(roh.get("kredite") or {})
    out = []
    for loan in spec.get("loans") or []:
        kennung = str(loan.get("id"))
        felder = eigene.pop(kennung, None)
        if (felder or {}).get("entfernt"):
            continue
        if felder:
            loan = {**loan, **felder}
        if an_szenario_gebunden(loan):
            loan = mit_vorlage(loan, vorlagen.get(kennung) or {})
        out.append(loan)
    # Was nur in der eigenen Datei steht, gibt es trotzdem.
    for kennung, felder in eigene.items():
        if not (felder or {}).get("entfernt"):
            out.append({**felder, "id": kennung})
    return out


def segments_from(loan: dict) -> list[Segment]:
    """Build a loan's segments from its loans.yaml entry.

    One place, because there were five copies of this mapping and adding a
    field meant finding all five. The first forward offer to be entered needed
    exactly that, and three of the copies would have silently ignored it.
    """
    segments = [
        Segment(start=s["start"], end=s["end"],
                annual_rate_pct=s["annual_rate_pct"],
                annuity_cents=s.get("annuity_cents"),
                term_years=s.get("term_years"),
                basis=s.get("basis", "assumption"),
                opening_balance_cents=s.get("opening_balance_cents"))
        for s in loan.get("segments") or []
    ]
    follow = loan.get("anschluss")
    if follow and segments:
        segments.append(_followup_segment(segments[-1], follow))
    return segments


def _followup_segment(last: Segment, follow: dict) -> Segment:
    """The period after the Zinsbindung, quoted the German way.

    A German Annuitätendarlehen is offered as a rate PLUS an initial
    repayment percentage -- "3,8 % Zins, 2 % Tilgung" -- and the monthly
    payment follows from the two: (Zins + Tilgung) x Restschuld / 12.

    That is also the answer to whether an end date is still needed: it is not.
    The term is a RESULT of those two numbers, not an input. Asking for it as
    well would let someone enter a combination that cannot repay the loan by
    the date they typed, and the schedule would then simply disagree with the
    form. So the end here is only a horizon, generous enough that amortise()
    stops when the balance reaches zero rather than when the calendar does.
    """
    rate = float(follow["zins_pct"])
    tilgung = float(follow["tilgung_pct"])
    start = follow.get("ab") or _add_months(last.end, 1)
    if isinstance(start, str):
        start = date.fromisoformat(start)
    # The balance the annuity is computed on is not known until the schedule
    # has been run, so it is left to amortise() to carry forward; the percentage
    # is stored and applied there.
    return Segment(start=start, end=date(start.year + 60, 12, 31),
                   annual_rate_pct=rate, annuity_cents=None,
                   tilgung_pct=tilgung, basis=follow.get("basis", "assumption"))


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp to month length: a payment on the 31st still falls in February.
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0)
                      else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


@dataclass(slots=True)
class Segment:
    """One interest-rate period of a loan."""

    start: date
    end: date
    annual_rate_pct: float
    annuity_cents: int | None = None      # None -> derive from term_years
    term_years: int | None = None
    basis: str = "assumption"             # 'actual' | 'offer' | 'assumption'
    # Anfängliche Tilgung in percent a year, the German way of quoting a
    # prolongation: "3,8 % Zins, 2 % Tilgung". The annuity then follows as
    # (rate + tilgung) x balance / 12, and the term is a RESULT of the two
    # rather than something to be entered alongside them.
    tilgung_pct: float | None = None
    # The balance this segment starts from, where the lender has stated it.
    #
    # A prolongation offer names its own Darlehensbetrag, and that figure beats
    # anything carried forward through a year of modelled months: the model and
    # the lender's own quote for the same date differ by a few hundred euro on
    # a six-figure balance. That is a good model -- but the bank's number is
    # the one the contract will use.
    opening_balance_cents: int | None = None


@dataclass(slots=True)
class Payment:
    month: date
    payment_cents: int
    interest_cents: int
    principal_cents: int
    balance_cents: int
    segment_index: int
    basis: str


@dataclass(slots=True)
class Schedule:
    loan_id: str
    payments: list[Payment] = field(default_factory=list)

    def between(self, start: date, end: date) -> list[Payment]:
        return [p for p in self.payments if start <= p.month <= end]

    def total_interest_cents(self) -> int:
        return sum(p.interest_cents for p in self.payments)

    def payoff_month(self) -> date | None:
        for payment in self.payments:
            if payment.balance_cents <= 0:
                return payment.month
        return None


def monate_zwischen(von: date, bis: date) -> int:
    """Zahl der Raten von `von` bis `bis`, beide eingeschlossen."""
    return (bis.year - von.year) * 12 + (bis.month - von.month) + 1


def annuitaet_fuer_monate(balance_cents: int, annual_rate_pct: float,
                          months: int) -> int:
    """Rate, die `balance_cents` in genau `months` Raten tilgt.

    Die Umkehrung des Tilgungsplans, und die Richtung, in der man bei einem
    Hauskauf tatsaechlich denkt: nicht "was tilgt 1.800 im Monat", sondern
    "was kostet es im Monat, wenn der Kredit weg sein soll, bevor ich in Rente
    gehe". Ein Enddatum ist eine Entscheidung, eine Rate ist deren Preis.
    """
    if months <= 0:
        return 0
    monthly = annual_rate_pct / 100.0 / 12.0
    if monthly == 0:
        return -(-balance_cents // months)
    factor = (1 + monthly) ** months
    exakt = balance_cents * monthly * factor / (factor - 1)
    # AUFgerundet, auf zehn Cent. Kaufmaennisch gerundet bleibt ein Rest von
    # ein paar Euro stehen, und dann ist der Kredit am Stichtag formal nicht
    # getilgt -- die Seite zeigt "—" statt eines Datums, obwohl 3,05 offen
    # sind. Aufrunden macht daraus eine kleinere Schlussrate, und genau so
    # steht es in jedem echten Tilgungsplan: "364 Raten à 745,00 +
    # Schlussrate 514,83".
    return int(-(-exakt // 10) * 10)


def annuity_for(balance_cents: int, annual_rate_pct: float, years: int) -> int:
    """Monthly annuity that clears `balance_cents` over `years`."""
    months = years * 12
    monthly = annual_rate_pct / 100.0 / 12.0
    if monthly == 0:
        return -(-balance_cents // months)
    factor = (1 + monthly) ** months
    return int(round(balance_cents * monthly * factor / (factor - 1)))


def amortise(
    loan_id: str,
    opening_balance_cents: int,
    segments: list[Segment],
    *,
    max_months: int = 600,
    sondertilgung: dict[date, int] | None = None,
) -> Schedule:
    """Build a monthly schedule across all segments.

    Interest is charged on the balance outstanding at the start of each month;
    the remainder of the payment reduces principal. A final payment is trimmed
    so the balance lands exactly on zero rather than overshooting.
    """
    schedule = Schedule(loan_id=loan_id)
    balance = opening_balance_cents
    extra = sondertilgung or {}
    emitted = 0

    for index, segment in enumerate(segments):
        monthly_rate = segment.annual_rate_pct / 100.0 / 12.0
        # A stated opening balance replaces what the previous segment carried
        # forward. Applied before the annuity is derived, so a segment given a
        # term rather than a payment is sized against the real figure.
        if segment.opening_balance_cents is not None:
            balance = segment.opening_balance_cents

        annuity = segment.annuity_cents
        if annuity is None and segment.tilgung_pct is not None:
            # The German quotation: rate plus initial repayment, both as a
            # percentage of the balance AT THE START of the period. The
            # payment stays flat from there and the repayment share grows on
            # its own, which is what makes it an annuity.
            annuity = int(round(
                balance * (segment.annual_rate_pct + segment.tilgung_pct)
                / 100.0 / 12.0))
        if annuity is None:
            if not segment.term_years:
                raise ValueError(
                    f"segment {index} of {loan_id} has neither annuity nor term"
                )
            annuity = annuity_for(balance, segment.annual_rate_pct, segment.term_years)

        # Each month is computed from the segment's anchor date, never from
        # the previous (possibly clamped) month. Advancing iteratively lets a
        # short month drag the payment day down permanently: a loan paid on the
        # 30th becomes the 28th after February and stays there.
        offset = 0
        month = segment.start
        while month <= segment.end and balance > 0 and emitted < max_months:
            interest = int(round(balance * monthly_rate))
            principal = annuity - interest

            # Never let a payment increase the debt: if the annuity does not
            # even cover interest, say so rather than silently compounding.
            if principal <= 0:
                raise ValueError(
                    f"{loan_id} segment {index}: annuity {annuity} does not cover "
                    f"interest {interest} at {segment.annual_rate_pct}%"
                )

            principal += extra.get(month, 0)
            if principal > balance:
                principal = balance

            balance -= principal
            schedule.payments.append(
                Payment(
                    month=month,
                    payment_cents=interest + principal,
                    interest_cents=interest,
                    principal_cents=principal,
                    balance_cents=balance,
                    segment_index=index,
                    basis=segment.basis,
                )
            )
            emitted += 1
            offset += 1
            month = _add_months(segment.start, offset)

        if balance <= 0:
            break

    return schedule


def verify_against_actuals(
    schedule: Schedule,
    actuals: list[tuple[date, int, int]],
    *,
    tolerance_cents: int = 200,
) -> list[dict]:
    """Compare a computed schedule against observed interest/principal splits.

    Sparda prints `RECHN.ZINS ... TILG./ENTG. ...` on every payment, so the
    engine can be checked rather than trusted. Returns the rows that differ by
    more than the tolerance; an empty list means the model matches reality.

    A small tolerance is expected: banks compute interest on day-count
    conventions this engine approximates monthly.
    """
    by_month = {p.month: p for p in schedule.payments}
    problems: list[dict] = []
    for month, interest, _principal in actuals:
        key = date(month.year, month.month, 1)
        match = next(
            (p for m, p in by_month.items()
             if m.year == key.year and m.month == key.month), None
        )
        if match is None:
            problems.append({"month": month.isoformat(), "issue": "no computed payment"})
            continue
        if abs(match.interest_cents - interest) > tolerance_cents:
            problems.append({
                "month": month.isoformat(),
                "issue": "interest mismatch",
                "computed_cents": match.interest_cents,
                "actual_cents": interest,
                "delta_cents": match.interest_cents - interest,
            })
    return problems
