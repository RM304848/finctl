"""Ein Zaehler: Ablesungen, Hochrechnung aufs Abrechnungsende, Abschlag.

Der Versorger rechnet einmal im Abrechnungszeitraum ab. Bis dahin weiss man
nur, was man abgelesen und was man vorausgezahlt hat. Diese Rechnung
schreibt den Verbrauch bis zum Ende des Zeitraums fort, bepreist ihn mit
Grund- und Arbeitspreis und stellt die Abschlaege dagegen.

EIN ZAEHLER FUER ALLES MIT ZAEHLERSTAND: Strom, Gas, Fernwaerme, Wasser. Was
sie unterscheidet, steht in `Messung`: worin der Zaehler zaehlt, worin
abgerechnet wird, wie das eine ins andere umgerechnet wird (Gas: m³ mal
Brennwert mal Zustandszahl gibt kWh), und wie sich der Verbrauch ueber das
Jahr verteilt (`finctl/energie/profil.py`). Strom ist die Vorgabe: kWh am
Zaehler, kWh auf der Rechnung, gleichmaessig ueber das Jahr.

Die `kwh_*`-Felder der Rechnung tragen die Menge in der ABRECHNUNGSeinheit --
beim Wasser also m³. Der Name blieb, weil Strom der erste Zaehler war.

Ein Zeitraum hat KEIN festes Jahr. Er dauert von `beginn` bis `ende`, wie sie
eingetragen sind: ein Vertrag kann mitten im Jahr enden, eine Preisgarantie
auslaufen. Und eine Ablesung am Stichtag ist KEINE Abrechnung -- abgerechnet
ist ein Zeitraum erst, wenn der Haken `abgerechnet` gesetzt ist. Bis dahin
ist jede Zahl hier eine eigene Rechnung.

Vorzeichen wie ueberall im Projekt: positiv ist Geld, das zurueckkommt
(Guthaben), negativ Geld, das noch raus muss (Nachzahlung).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from finctl.energie import profil as _profil
from finctl.kalender import plus_monate


@dataclass(frozen=True, slots=True)
class Messung:
    """Was ein Zaehler misst und wie abgerechnet wird."""
    #: Worin der Zaehler zaehlt.
    einheit: str = "kWh"
    #: Worin der Arbeitspreis gilt und die Rechnung rechnet.
    abrechnung: str = "kWh"
    #: Zaehlereinheit -> Abrechnungseinheit. Gas: Brennwert × Zustandszahl.
    faktor: float = 1.0
    #: linear | heizung (finctl/energie/profil.py)
    profil: str = "linear"
    #: Anteil ohne Jahresgang, nur bei `heizung`: Warmwasser, Kochen.
    grundlast: float = 0.0


STROM = Messung()


# ------------------------------------------------------------------ Daten

@dataclass(frozen=True, slots=True)
class Tarif:
    ab: date
    grundpreis_jahr_cents: int
    arbeitspreis_ct_kwh: float


@dataclass(frozen=True, slots=True)
class Abschlag:
    ab: date
    monat_cents: int


@dataclass(frozen=True, slots=True)
class Ablesung:
    datum: date
    stand: float


@dataclass(slots=True)
class Zeitraum:
    beginn: date
    ende: date
    zaehlerstand_beginn: float
    tarife: list[Tarif]
    abschlaege: list[Abschlag]
    anbieter: str = ""
    preisgarantie_bis: date | None = None
    abgerechnet: bool = False
    endabrechnung_kwh: float | None = None
    endabrechnung_cents: int | None = None
    #: Der Anfangsstand ist hochgerechnet, weil am Stichtag nicht abgelesen wurde.
    anfangsstand_geschaetzt: bool = False

    @property
    def tage(self) -> int:
        return (self.ende - self.beginn).days + 1

    def enthaelt(self, d: date) -> bool:
        """Eine Ablesung am Morgen nach dem Ende zaehlt noch als Stichtag."""
        return self.beginn <= d <= self.ende + timedelta(days=1)


def lies_datum(wert, feld: str) -> date:
    if isinstance(wert, date):
        return wert
    try:
        return date.fromisoformat(str(wert).strip())
    except ValueError:
        raise ValueError(f"{feld}: kein Datum ({wert!r}), erwartet JJJJ-MM-TT") from None


def lies_zahl(wert, feld: str) -> float:
    try:
        return float(wert)
    except (TypeError, ValueError):
        raise ValueError(f"{feld}: keine Zahl ({wert!r})") from None


def _cents(wert, feld: str) -> int:
    return round(lies_zahl(wert, feld))


def zeitraum_aus(roh: dict) -> Zeitraum:
    tarife = sorted((Tarif(lies_datum(t.get("ab"), "Tarif ab"),
                           _cents(t.get("grundpreis_jahr_cents"), "Grundpreis"),
                           lies_zahl(t.get("arbeitspreis_ct_kwh"), "Arbeitspreis"))
                     for t in roh.get("tarife") or []), key=lambda t: t.ab)
    abschlaege = sorted((Abschlag(lies_datum(a.get("ab"), "Abschlag ab"),
                                  _cents(a.get("monat_cents"), "Abschlag"))
                         for a in roh.get("abschlaege") or []), key=lambda a: a.ab)
    kwh, cents = roh.get("endabrechnung_kwh"), roh.get("endabrechnung_cents")
    garantie = roh.get("preisgarantie_bis")
    return Zeitraum(
        beginn=lies_datum(roh.get("beginn"), "Beginn"),
        ende=lies_datum(roh.get("ende"), "Ende"),
        zaehlerstand_beginn=lies_zahl(roh.get("zaehlerstand_beginn"), "Anfangsstand"),
        tarife=tarife, abschlaege=abschlaege,
        anbieter=str(roh.get("anbieter") or ""),
        preisgarantie_bis=lies_datum(garantie, "Preisgarantie") if garantie else None,
        abgerechnet=bool(roh.get("abgerechnet")),
        endabrechnung_kwh=None if kwh in (None, "") else lies_zahl(kwh, "abgerechnete kWh"),
        endabrechnung_cents=None if cents in (None, "") else _cents(cents, "Ergebnis"),
        anfangsstand_geschaetzt=bool(roh.get("anfangsstand_geschaetzt")))


def zeitraum_als_yaml(z: Zeitraum) -> dict:
    roh = {"beginn": z.beginn.isoformat(), "ende": z.ende.isoformat(),
           "zaehlerstand_beginn": z.zaehlerstand_beginn}
    if z.anfangsstand_geschaetzt:
        roh["anfangsstand_geschaetzt"] = True
    if z.anbieter:
        roh["anbieter"] = z.anbieter
    if z.preisgarantie_bis:
        roh["preisgarantie_bis"] = z.preisgarantie_bis.isoformat()
    roh["tarife"] = [{"ab": t.ab.isoformat(), "grundpreis_jahr_cents": t.grundpreis_jahr_cents,
                      "arbeitspreis_ct_kwh": t.arbeitspreis_ct_kwh} for t in z.tarife]
    roh["abschlaege"] = [{"ab": a.ab.isoformat(), "monat_cents": a.monat_cents}
                         for a in z.abschlaege]
    roh["abgerechnet"] = z.abgerechnet
    if z.endabrechnung_kwh is not None:
        roh["endabrechnung_kwh"] = z.endabrechnung_kwh
    if z.endabrechnung_cents is not None:
        roh["endabrechnung_cents"] = z.endabrechnung_cents
    return roh


def aus_roh(roh: dict) -> tuple[list[Zeitraum], list[Ablesung]]:
    """Zeitraeume und Ablesungen aus dem, was in der YAML-Datei steht."""
    zeitraeume = sorted((zeitraum_aus(z) for z in roh.get("zeitraeume") or []),
                        key=lambda z: z.beginn)
    ablesungen = sorted((Ablesung(lies_datum(a.get("datum"), "Ablesung"),
                                  lies_zahl(a.get("stand"), "Zaehlerstand"))
                         for a in roh.get("ablesungen") or []), key=lambda a: a.datum)
    return zeitraeume, ablesungen


def als_roh(zeitraeume: list[Zeitraum], ablesungen: list[Ablesung]) -> dict:
    return {"zeitraeume": [zeitraum_als_yaml(z)
                           for z in sorted(zeitraeume, key=lambda z: z.beginn)],
            "ablesungen": [{"datum": a.datum.isoformat(), "stand": a.stand}
                           for a in sorted(ablesungen, key=lambda a: a.datum)]}


# ---------------------------------------------------------------- Rechnung

@dataclass(frozen=True, slots=True)
class Posten:
    label: str
    herleitung: str
    #: Vorzeichen wie im Ledger: Abschlaege positiv, Kosten negativ.
    cents: int


@dataclass(frozen=True, slots=True)
class Schritt:
    """Eine Ablesung im Verlauf, mit der Hochrechnung, wie sie an dem Tag stand."""
    datum: date
    stand: float
    tage: int
    kwh: float
    kwh_tag: float | None
    kwh_erwartet: float | None
    saldo_cents: int | None


@dataclass(slots=True)
class Rechnung:
    zeitraum: Zeitraum
    #: laufend | ausstehend | abgerechnet
    zustand: str
    letzte: Ablesung | None
    kwh_bisher: float
    tage_bisher: int
    kwh_tag: float | None
    kwh_erwartet: float | None
    #: Die Ablesung reicht bis zum Stichtag; nichts ist hochgerechnet.
    abgelesen_bis_ende: bool
    posten: list[Posten] = field(default_factory=list)
    kwh_gedeckt: float | None = None
    empfehlung_cents: int | None = None
    #: Worauf die Empfehlung steht: "abgerechnet" oder "eigene Rechnung".
    empfehlung_grundlage: str = ""
    verlauf: list[Schritt] = field(default_factory=list)

    @property
    def saldo_cents(self) -> int | None:
        """Die eigene Rechnung: Abschlaege minus Kosten, aus den Posten."""
        return sum(p.cents for p in self.posten) if self.kwh_erwartet is not None else None

    @property
    def kwh_luft(self) -> float | None:
        if self.kwh_gedeckt is None or self.kwh_erwartet is None:
            return None
        return self.kwh_gedeckt - self.kwh_erwartet

    @property
    def abweichung_cents(self) -> int | None:
        """Versorger minus eigene Rechnung, sobald abgerechnet ist."""
        z = self.zeitraum
        if not z.abgerechnet or z.endabrechnung_cents is None or self.saldo_cents is None:
            return None
        return z.endabrechnung_cents - self.saldo_cents

    @property
    def garantie_endet_vorher(self) -> bool:
        """Nur solange nach dem Ende der Garantie noch kein neuer Preis steht."""
        z = self.zeitraum
        g = z.preisgarantie_bis
        return (g is not None and g < z.ende
                and not any(t.ab > g for t in z.tarife))


def _abschnitte(z: Zeitraum) -> list[tuple[Tarif, int]]:
    """Jeder Tarif mit den Tagen, die er im Zeitraum gilt."""
    out = []
    for i, t in enumerate(z.tarife):
        von = max(t.ab, z.beginn)
        bis = z.tarife[i + 1].ab - timedelta(days=1) if i + 1 < len(z.tarife) else z.ende
        tage = (min(bis, z.ende) - von).days + 1
        if tage > 0:
            out.append((t, tage))
    return out


def zahlungstermine(z: Zeitraum) -> list[tuple[date, int]]:
    """Ein Abschlag zu jedem Monatsbeginn des Zeitraums, mit dem dann gueltigen Betrag.

    Bei einem Zeitraum von fuenf Monaten sind es fuenf -- das Jahr wird nicht
    angenommen, sondern abgezaehlt.
    """
    termine, n = [], 0
    while (tag := plus_monate(z.beginn, n)) <= z.ende:
        gueltig = [a for a in z.abschlaege if a.ab <= tag]
        termine.append((tag, gueltig[-1].monat_cents if gueltig else 0))
        n += 1
    return termine


def _kosten(z: Zeitraum, kwh: float, m: Messung = STROM) -> list[Posten]:
    posten = []
    for t, tage in _abschnitte(z):
        grund = round(t.grundpreis_jahr_cents * tage / 365)
        posten.append(Posten(
            f"Grundpreis ab {max(t.ab, z.beginn):%Y-%m-%d}",
            f"{_eur(round(t.grundpreis_jahr_cents / 12))} im Monat, "
            f"{_eur(t.grundpreis_jahr_cents)} im Jahr × {tage} von 365 Tagen", -grund))
    for t, tage in _abschnitte(z):
        anteil = kwh * tage / z.tage
        posten.append(Posten(
            f"Arbeitspreis ab {max(t.ab, z.beginn):%Y-%m-%d}",
            f"{_menge(anteil, m.abrechnung)} ({tage} von {z.tage} Tagen) × "
            f"{_ct(t.arbeitspreis_ct_kwh, m.abrechnung)}",
            -round(anteil * t.arbeitspreis_ct_kwh)))
    return posten


def _abschlagsposten(z: Zeitraum) -> list[Posten]:
    termine = zahlungstermine(z)
    je_betrag: dict[int, int] = {}
    for _tag, cents in termine:
        je_betrag[cents] = je_betrag.get(cents, 0) + 1
    return [Posten("Abschläge", f"{n} × {_eur(cents)}", n * cents)
            for cents, n in je_betrag.items() if cents]


def _hochrechnung(z: Zeitraum, bis: Ablesung,
                  m: Messung = STROM) -> tuple[float, int, float | None, float]:
    """Menge bisher, Tage bisher, Menge je Tag, Menge bis zum Ende.

    Linear: der Schnitt je Tag, auf die restlichen Tage fortgeschrieben. Mit
    Heizprofil: der bisherige Verbrauch im Verhaeltnis der Gewichte -- wer im
    Oktober abliest, hat den Winter noch vor sich.
    """
    kwh = (bis.stand - z.zaehlerstand_beginn) * m.faktor
    tage = min((bis.datum - z.beginn).days, z.tage)
    if tage <= 0:
        return kwh, 0, None, kwh
    je_tag = kwh / tage
    if m.profil == "linear":
        return kwh, tage, je_tag, kwh + je_tag * (z.tage - tage)
    bisher = _profil.gewicht(z.beginn, tage, m.profil, m.grundlast)
    gesamt = _profil.gewicht(z.beginn, z.tage, m.profil, m.grundlast)
    return kwh, tage, je_tag, (kwh * gesamt / bisher if bisher > 0 else kwh)


def _durchschnittspreis(z: Zeitraum) -> float:
    abschnitte = _abschnitte(z)
    tage = sum(n for _t, n in abschnitte) or 1
    return sum(t.arbeitspreis_ct_kwh * n for t, n in abschnitte) / tage


def rechnen(z: Zeitraum, ablesungen: list[Ablesung], heute: date,
            m: Messung = STROM) -> Rechnung:
    eigene = [a for a in ablesungen if z.enthaelt(a.datum) and a.datum > z.beginn]
    letzte = eigene[-1] if eigene else None
    zustand = ("laufend" if heute <= z.ende
               else "abgerechnet" if z.abgerechnet else "ausstehend")

    if letzte is None:
        r = Rechnung(z, zustand, None, 0.0, 0, None, None, False)
    else:
        kwh, tage, je_tag, erwartet = _hochrechnung(z, letzte, m)
        r = Rechnung(z, zustand, letzte, kwh, tage, je_tag,
                     erwartet if je_tag is not None else None,
                     abgelesen_bis_ende=letzte.datum >= z.ende)

    abschlaege = _abschlagsposten(z)
    grund = sum(p.cents for p in _kosten(z, 0.0))
    preis = _durchschnittspreis(z)
    if preis > 0:
        r.kwh_gedeckt = (sum(p.cents for p in abschlaege) + grund) / preis
    if r.kwh_erwartet is not None:
        r.posten = abschlaege + _kosten(z, r.kwh_erwartet, m)

    # Die Empfehlung rechnet auf 365 Tage, nicht auf die Laenge dieses
    # Zeitraums: endet ein Vertrag nach fuenf Monaten, zahlt der naechste
    # trotzdem zwoelf Abschlaege.
    if z.abgerechnet and z.endabrechnung_kwh is not None:
        jahres_kwh, r.empfehlung_grundlage = _jahresmenge(
            z.endabrechnung_kwh, z.beginn, z.tage, m), "abgerechnet"
    elif r.kwh_tag is not None:
        jahres_kwh, r.empfehlung_grundlage = _jahresmenge(
            r.kwh_bisher, z.beginn, r.tage_bisher, m), "eigene Rechnung"
    else:
        jahres_kwh = None
    if jahres_kwh is not None and z.tarife:
        t = z.tarife[-1]
        monat = (jahres_kwh * t.arbeitspreis_ct_kwh + t.grundpreis_jahr_cents) / 12
        r.empfehlung_cents = math.ceil(monat / 100) * 100

    r.verlauf = _verlauf(z, eigene, m)
    return r


def _jahresmenge(menge: float, beginn: date, tage: int, m: Messung) -> float:
    """Die Menge eines Normjahres. Linear wie bisher: je Tag mal 365."""
    if m.profil == "linear":
        return menge / tage * 365
    anteil = _profil.gewicht(beginn, tage, m.profil, m.grundlast)
    return menge / anteil if anteil > 0 else 0.0


def _verlauf(z: Zeitraum, eigene: list[Ablesung], m: Messung = STROM) -> list[Schritt]:
    schritte, vorher = [], Ablesung(z.beginn, z.zaehlerstand_beginn)
    for a in eigene:
        _kwh_b, _t, je_tag, erwartet = _hochrechnung(z, a, m)
        tage = (a.datum - vorher.datum).days
        kwh = (a.stand - vorher.stand) * m.faktor
        saldo = None
        if je_tag is not None:
            saldo = sum(p.cents for p in _abschlagsposten(z) + _kosten(z, erwartet, m))
        schritte.append(Schritt(a.datum, a.stand, tage, kwh,
                                kwh / tage if tage > 0 else None,
                                erwartet if je_tag is not None else None, saldo))
        vorher = a
    return schritte


# ---------------------------------------------------------------- Pruefung

def zeitraum_fuer(zeitraeume: list[Zeitraum], d: date) -> Zeitraum | None:
    """Der Zeitraum, in den ein Datum faellt. Der Morgen nach dem Ende gehoert
    zum naechsten, wenn es einen gibt."""
    passend = [z for z in zeitraeume if z.enthaelt(d)]
    return passend[-1] if passend else None


def ablesung_pruefen(zeitraeume: list[Zeitraum], ablesungen: list[Ablesung],
                     neu: Ablesung, heute: date, m: Messung = STROM) -> None:
    if neu.datum > heute:
        raise ValueError("Das Datum liegt in der Zukunft.")
    if zeitraum_fuer(zeitraeume, neu.datum) is None:
        raise ValueError("Das Datum liegt in keinem Abrechnungszeitraum. "
                         "Erst den Zeitraum anlegen.")
    if any(a.datum == neu.datum for a in ablesungen):
        raise ValueError("Für dieses Datum gibt es schon eine Ablesung.")
    vorher = [a.stand for a in ablesungen if a.datum < neu.datum]
    vorher += [z.zaehlerstand_beginn for z in zeitraeume if z.beginn <= neu.datum]
    nachher = [a.stand for a in ablesungen if a.datum > neu.datum]
    if vorher and neu.stand < max(vorher):
        raise ValueError(f"Der Zählerstand liegt unter dem vorigen "
                         f"({_menge(max(vorher), m.einheit)}).")
    if nachher and neu.stand > min(nachher):
        raise ValueError(f"Der Zählerstand liegt über dem späteren "
                         f"({_menge(min(nachher), m.einheit)}).")


def zeitraum_pruefen(z: Zeitraum, andere: list[Zeitraum]) -> None:
    if z.ende <= z.beginn:
        raise ValueError("Das Ende muss nach dem Beginn liegen.")
    _tarife_pruefen(z)
    _abschlaege_pruefen(z)
    for o in andere:
        if z.beginn <= o.ende and o.beginn <= z.ende:
            raise ValueError(f"Überschneidet sich mit {o.beginn:%Y-%m-%d}–{o.ende:%Y-%m-%d}.")


def _tarife_pruefen(z: Zeitraum) -> None:
    if not z.tarife:
        raise ValueError("Mindestens ein Tarif gehört dazu.")
    # Ein Tarif oder Abschlag, der schon vor dem Beginn galt, gilt ab Beginn
    # weiter -- so steht es auf dem Vertrag, und so wird er abgeschrieben.
    if z.tarife[0].ab > z.beginn:
        raise ValueError("Der erste Tarif muss spätestens am Beginn gelten.")
    for t in z.tarife:
        if t.ab > z.ende:
            raise ValueError(f"Tarif ab {t.ab:%Y-%m-%d} beginnt erst nach dem Ende.")
        if t.arbeitspreis_ct_kwh <= 0 or t.grundpreis_jahr_cents < 0:
            raise ValueError("Der Arbeitspreis muss über null liegen, "
                             "der Grundpreis darf nicht negativ sein.")


def _abschlaege_pruefen(z: Zeitraum) -> None:
    for a in z.abschlaege:
        if a.ab > z.ende:
            raise ValueError(f"Abschlag ab {a.ab:%Y-%m-%d} beginnt erst nach dem Ende.")
        if a.monat_cents < 0:
            raise ValueError("Ein Abschlag ist nicht negativ.")


def naechster_zeitraum(z: Zeitraum, ablesungen: list[Ablesung], heute: date,
                       m: Messung = STROM) -> Zeitraum:
    """Der Vorschlag fuer das naechste Abrechnungsjahr.

    Anlegen geht, sobald das Ende vorbei ist -- die Abrechnung muss dafuer
    nicht da sein, denn der Verbrauch laeuft weiter. Ohne Ablesung am
    Stichtag ist der Anfangsstand hochgerechnet und als geschaetzt markiert.
    """
    if heute <= z.ende:
        raise ValueError(f"Der Zeitraum läuft noch bis {z.ende:%Y-%m-%d}.")
    r = rechnen(z, ablesungen, heute, m)
    beginn = z.ende + timedelta(days=1)
    stichtag = [a for a in ablesungen if a.datum in (z.ende, beginn)]
    if stichtag:
        stand, geschaetzt = stichtag[-1].stand, False
    elif r.kwh_erwartet is not None:
        stand, geschaetzt = round(z.zaehlerstand_beginn + r.kwh_erwartet / m.faktor, 1), True
    else:
        raise ValueError("Ohne Ablesung im alten Zeitraum lässt sich kein Anfangsstand schätzen.")
    tarif = z.tarife[-1]
    abschlag = r.empfehlung_cents if r.empfehlung_cents is not None else (
        z.abschlaege[-1].monat_cents if z.abschlaege else 0)
    return Zeitraum(
        beginn=beginn, ende=plus_monate(beginn, 12) - timedelta(days=1),
        zaehlerstand_beginn=stand, anfangsstand_geschaetzt=geschaetzt,
        tarife=[Tarif(beginn, tarif.grundpreis_jahr_cents, tarif.arbeitspreis_ct_kwh)],
        abschlaege=[Abschlag(beginn, abschlag)], anbieter=z.anbieter)


# ------------------------------------------------------------ Anzeige

def _eur(cents: int) -> str:
    from finctl.ledger.db import format_eur

    return format_eur(cents)


def _menge(menge: float, einheit: str = "kWh") -> str:
    return f"{menge:,.0f} {einheit}".replace(",", ".")


def _ct(ct: float, einheit: str = "kWh") -> str:
    return f"{ct:.2f} ct/{einheit}".replace(".", ",")
