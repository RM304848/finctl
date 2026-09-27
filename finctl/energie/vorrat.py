"""Ein Vorrat: Heizoel, Pellets, Fluessiggas -- geliefert, gelagert, verheizt.

Anders als ein Zaehler hat ein Tank keinen Versorger, der abrechnet, und keinen
Abschlag. Man bezahlt bei der Lieferung, auf einmal und viel. Die Fragen sind
deshalb andere:

- **Wie viel geht im Jahr durch?** Zwischen zwei Peilungen ist verbraucht, was
  vorher drin war, plus was geliefert wurde, minus was jetzt drin ist. Ueber
  das Heizprofil (`finctl/energie/profil.py`) wird daraus ein Normjahr: eine
  Peilung von Mai bis September sagt wenig, und linear hochgerechnet saehe der
  Sommer wie ein sparsames Jahr aus.
- **Wie lange reicht es?** Vom letzten Stand an, Tag fuer Tag mit dem Gewicht
  des Tages, bis der Mindestbestand erreicht ist.
- **Was muss ich im Monat zuruecklegen,** damit die naechste Lieferung nicht
  auf einen Schlag das Konto leert? Jahresverbrauch mal Preis der letzten
  Lieferung, durch zwoelf, auf volle Euro aufgerundet.

Mengen in der Einheit des Vorrats (Liter, Kilogramm), Geld in Cent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from finctl.energie import profil as _profil
from finctl.energie.zaehler import lies_datum, lies_zahl

#: Art -> (Name, Einheit)
ARTEN = {"heizoel": ("Heizöl", "l"), "pellets": ("Pellets", "kg"),
         "fluessiggas": ("Flüssiggas", "l")}

#: Weiter als zehn Jahre rechnet die Reichweite nicht -- darueber ist die
#: Antwort "reicht" und nicht ein Datum.
REICHWEITE_MAX_TAGE = 3650


@dataclass(frozen=True, slots=True)
class Lieferung:
    datum: date
    menge: float
    cents: int

    @property
    def preis_ct(self) -> float | None:
        """Cent je Einheit."""
        return self.cents / self.menge if self.menge else None


@dataclass(frozen=True, slots=True)
class Stand:
    datum: date
    menge: float


@dataclass(slots=True)
class Vorrat:
    id: str
    art: str
    name: str
    einheit: str
    kapazitaet: float | None = None
    mindestbestand: float = 0.0
    profil: str = "heizung"
    grundlast: float = 0.0
    lieferungen: list[Lieferung] = field(default_factory=list)
    staende: list[Stand] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Abschnitt:
    """Verbrauch zwischen zwei Peilungen."""
    von: date
    bis: date
    verbrauch: float
    geliefert: float
    #: Anteil dieses Abschnitts an einem Normjahr.
    gewicht: float


@dataclass(slots=True)
class Auswertung:
    vorrat: Vorrat
    abschnitte: list[Abschnitt]
    jahresverbrauch: float | None
    #: Vom letzten Stand an fortgeschrieben, mit Lieferungen danach.
    stand_heute: float | None
    reicht_bis: date | None
    #: Laenger als REICHWEITE_MAX_TAGE: reicht, ohne Datum.
    reicht_lange: bool
    preis_ct: float | None
    ruecklage_monat_cents: int | None
    jahreskosten_cents: int | None


# ------------------------------------------------------------------ Lesen

def aus_roh(vid: str, roh: dict) -> Vorrat:
    art = str(roh.get("art") or "")
    if art not in ARTEN:
        raise ValueError(f"{vid}: unbekannte Art {art!r}, erwartet: {', '.join(ARTEN)}")
    name, einheit = ARTEN[art]
    kap = roh.get("kapazitaet")
    return Vorrat(
        id=vid, art=art, name=str(roh.get("name") or name),
        einheit=str(roh.get("einheit") or einheit),
        kapazitaet=None if kap in (None, "") else lies_zahl(kap, "Kapazität"),
        mindestbestand=lies_zahl(roh.get("mindestbestand") or 0, "Mindestbestand"),
        profil=str(roh.get("profil") or "heizung"),
        grundlast=lies_zahl(roh.get("grundlast") or 0, "Grundlast"),
        lieferungen=sorted((Lieferung(lies_datum(x.get("datum"), "Lieferung"),
                                      lies_zahl(x.get("menge"), "Liefermenge"),
                                      round(lies_zahl(x.get("cents"), "Rechnungsbetrag")))
                            for x in roh.get("lieferungen") or []), key=lambda x: x.datum),
        staende=sorted((Stand(lies_datum(x.get("datum"), "Stand"),
                              lies_zahl(x.get("menge"), "Füllstand"))
                        for x in roh.get("staende") or []), key=lambda x: x.datum))


def als_roh(v: Vorrat) -> dict:
    roh: dict = {"art": v.art, "name": v.name, "einheit": v.einheit}
    if v.kapazitaet is not None:
        roh["kapazitaet"] = v.kapazitaet
    if v.mindestbestand:
        roh["mindestbestand"] = v.mindestbestand
    roh["profil"] = v.profil
    if v.grundlast:
        roh["grundlast"] = v.grundlast
    roh["lieferungen"] = [{"datum": x.datum.isoformat(), "menge": x.menge, "cents": x.cents}
                          for x in v.lieferungen]
    roh["staende"] = [{"datum": x.datum.isoformat(), "menge": x.menge} for x in v.staende]
    return roh


# ------------------------------------------------------------------ Pruefen

def pruefen(v: Vorrat) -> None:
    """Was nicht stimmen kann, gar nicht erst speichern."""
    _einstellungen_pruefen(v)
    _eintraege_pruefen(v)


def _einstellungen_pruefen(v: Vorrat) -> None:
    if v.profil not in _profil.PROFILE:
        raise ValueError(f"Unbekanntes Profil {v.profil!r}.")
    _profil.grundlast_pruefen(v.grundlast)
    if v.kapazitaet is not None and v.kapazitaet <= 0:
        raise ValueError("Die Kapazität liegt über null.")
    if v.mindestbestand < 0:
        raise ValueError("Der Mindestbestand ist nicht negativ.")


def _eintraege_pruefen(v: Vorrat) -> None:
    for x in v.lieferungen:
        if x.menge <= 0 or x.cents < 0:
            raise ValueError(f"Lieferung vom {x.datum:%Y-%m-%d}: Menge über null, "
                             "Betrag nicht negativ.")
    for x in v.staende:
        if x.menge < 0:
            raise ValueError(f"Stand vom {x.datum:%Y-%m-%d} ist negativ.")
        if v.kapazitaet is not None and x.menge > v.kapazitaet:
            raise ValueError(f"Stand vom {x.datum:%Y-%m-%d} liegt über der Kapazität "
                             f"({_menge(v.kapazitaet, v.einheit)}).")
    if len({x.datum for x in v.staende}) != len(v.staende):
        raise ValueError("Für ein Datum gibt es nur einen Stand.")
    for a in _abschnitte(v):
        if a.verbrauch < 0:
            raise ValueError(f"Zwischen {a.von:%Y-%m-%d} und {a.bis:%Y-%m-%d} wäre "
                             f"mehr im Tank als hineinkam — fehlt eine Lieferung?")


# ------------------------------------------------------------------ Rechnen

def _geliefert(v: Vorrat, nach: date, bis: date) -> float:
    """Was nach der einen Peilung und bis zur anderen geliefert wurde.

    Eine Lieferung am Tag einer Peilung zaehlt zum Abschnitt davor: gepeilt
    wird nach der Lieferung, wer es vorher tut, traegt den Tag davor ein.
    """
    return sum(x.menge for x in v.lieferungen if nach < x.datum <= bis)


def _abschnitte(v: Vorrat) -> list[Abschnitt]:
    out = []
    for a, b in zip(v.staende, v.staende[1:], strict=False):
        geliefert = _geliefert(v, a.datum, b.datum)
        tage = (b.datum - a.datum).days
        out.append(Abschnitt(a.datum, b.datum, a.menge + geliefert - b.menge, geliefert,
                             _profil.gewicht(a.datum, tage, v.profil, v.grundlast)))
    return out


def _reichweite(v: Vorrat, ab: date, stand: float, jahres: float) -> tuple[date | None, bool]:
    if jahres <= 0:
        return None, True
    tag = ab
    for _ in range(REICHWEITE_MAX_TAGE):
        if stand <= v.mindestbestand:
            return tag, False
        stand -= jahres * _profil.tagesgewicht(tag, v.profil, v.grundlast)
        tag += timedelta(days=1)
    return None, True


def auswerten(v: Vorrat, heute: date) -> Auswertung:
    abschnitte = _abschnitte(v)
    gewicht = sum(a.gewicht for a in abschnitte)
    jahres = sum(a.verbrauch for a in abschnitte) / gewicht if gewicht > 0 else None

    stand_heute = reicht_bis = None
    reicht_lange = False
    if v.staende and jahres is not None:
        letzter = v.staende[-1]
        tage = max((heute - letzter.datum).days, 0)
        stand_heute = max(letzter.menge + _geliefert(v, letzter.datum, heute)
                          - jahres * _profil.gewicht(letzter.datum, tage, v.profil, v.grundlast),
                          0.0)
        reicht_bis, reicht_lange = _reichweite(v, heute, stand_heute, jahres)

    preis = v.lieferungen[-1].preis_ct if v.lieferungen else None
    kosten = ruecklage = None
    if jahres is not None and preis is not None:
        kosten = round(jahres * preis)
        ruecklage = math.ceil(kosten / 12 / 100) * 100
    return Auswertung(v, abschnitte, jahres, stand_heute, reicht_bis, reicht_lange,
                      preis, ruecklage, kosten)


def _menge(menge: float, einheit: str) -> str:
    return f"{menge:,.0f} {einheit}".replace(",", ".")
