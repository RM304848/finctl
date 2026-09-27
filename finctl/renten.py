"""Was im Ruhestand hereinkommt: gesetzliche Rente, Fondsrenten, Policen.

Jede Quelle steht in `config/renten.yaml` mit ihrer Herleitung. Betrag und
Stand kommen aus einem Schreiben, das jedes Jahr neu kommt -- Renteninformation,
Standmitteilung --, und werden im Monatsabschluss eingetragen. Das landet in
`renten_custom.yaml` und gewinnt beim Lesen; nur was abweicht, steht dort.

KAUFKRAFT. Die DRV nennt ihre Zahl in heutiger Kaufkraft ("ohne
Rentenanpassungen"), eine Standmitteilung meist nominal. Beides wird so
eingetragen, wie es im Schreiben steht, und erst beim Rechnen nominal gemacht
(`nominal_cents`). Eine Inflationsannahme in die Quelle zu schreiben hiesse,
eine Unterlage zu verfaelschen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from finctl.pfade import CONFIG_DIR

BASIS = "renten.yaml"
EIGEN = "renten_custom.yaml"
ARTEN = {"rente": "Monatsrente", "kapital": "Kapital"}
KAUFKRAFT = ("heute", "nominal")

KOPF = """# Im Monatsabschluss eingetragen: Betrag und Stand je Rentenquelle.
#
# Ueberschreibt config/renten.yaml je Quelle, wo die Herleitung steht. Nur
# was davon abweicht, steht hier.

"""


@dataclass(frozen=True, slots=True)
class Quelle:
    id: str
    name: str
    art: str                      # "rente" oder "kapital"
    cents: int                    # je Monat bzw. einmalig, wie im Schreiben
    kaufkraft: str = "heute"
    #: Bezug ab: ab wann gezahlt wird -- bei einer Police oft waehlbar (62
    #: oder 67). None heisst Rentenbeginn aus der Einrichtung.
    ab: date | None = None
    basis_ab: date | None = None
    konto: str | None = None
    stand: date | None = None     # Datum des Schreibens
    herleitung: str = ""
    notiz: str = ""
    eigen: bool = False           # im Monatsabschluss geaendert
    basis_cents: int | None = None

    def aktuell(self, heute: date) -> bool:
        """Ein Schreiben kommt einmal im Jahr: aktuell, wenn aus diesem Jahr."""
        return bool(self.stand and self.stand.year == heute.year)


def _yaml(pfad: Path) -> dict:
    if not pfad.exists():
        return {}
    return yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}


def _datum(wert) -> date | None:
    if not wert:
        return None
    if isinstance(wert, date):
        return wert
    text = str(wert)
    return date.fromisoformat(text + "-01" if len(text) == 7 else text[:10])


def quellen(config_dir: Path | None = None) -> list[Quelle]:
    """Alle Quellen, das Overlay Feld fuer Feld ueber der Basisdatei."""
    config_dir = config_dir or CONFIG_DIR
    eigen = _yaml(config_dir / EIGEN).get("renten") or {}
    aus = []
    for roh in _yaml(config_dir / BASIS).get("renten") or []:
        kennung = str(roh.get("id") or "")
        if not kennung:
            continue
        ueber = eigen.get(kennung) or {}
        art = str(roh.get("art") or "rente")
        kaufkraft = str(roh.get("kaufkraft") or "heute")
        if art not in ARTEN or kaufkraft not in KAUFKRAFT:
            raise ValueError(f"renten.yaml, {kennung}: art {ARTEN.keys()}, "
                             f"kaufkraft {KAUFKRAFT}")
        aus.append(Quelle(
            id=kennung, name=str(roh.get("name") or kennung), art=art,
            cents=int(ueber.get("cents", roh.get("cents")) or 0),
            kaufkraft=kaufkraft, ab=_datum(ueber.get("ab", roh.get("ab"))),
            konto=roh.get("konto") or None,
            stand=_datum(ueber.get("stand", roh.get("stand"))),
            herleitung=str(roh.get("herleitung") or ""),
            notiz=str(ueber.get("notiz") or ""),
            eigen=bool(ueber), basis_cents=roh.get("cents"),
            basis_ab=_datum(roh.get("ab"))))
    return aus


def beginn(q: Quelle) -> date | None:
    """Ab wann die Quelle zahlt -- ohne eigenes Datum ab dem Rentenbeginn."""
    if q.ab:
        return q.ab
    from finctl import person

    return person.rentenbeginn()


def nominal_cents(q: Quelle, im_jahr: int, inflation_pa: float,
                  basis_jahr: int | None = None) -> int:
    """Der Betrag in Geld des Jahres `im_jahr`.

    Heutige Kaufkraft wird mit der Inflation fortgeschrieben, vom Jahr des
    Schreibens an; eine nominale Zahl bleibt, wie sie ist. 2.109 heute werden
    bei 2 % und Rentenbeginn in 35 Jahren rund 4.200.
    """
    if q.kaufkraft == "nominal":
        return q.cents
    basis = basis_jahr or (q.stand.year if q.stand else date.today().year)
    return int(round(q.cents * (1.0 + inflation_pa) ** (im_jahr - basis)))


def _betrag(wert, q: Quelle):
    cents = int(wert)
    if cents < 0:
        raise ValueError("Betrag ohne Vorzeichen")
    return None if cents == q.basis_cents else cents


def _bezug_ab(wert, q: Quelle):
    ab = str(wert or "").strip()
    if ab and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", ab):
        raise ValueError("Bezug ab als JJJJ-MM")
    # Leer heisst: zurueck auf die Basisdatei -- dort leer bedeutet
    # Rentenbeginn.
    if not ab or (q.basis_ab and ab == q.basis_ab.strftime("%Y-%m")):
        return None
    return ab


def _stand(wert, _q: Quelle):
    if not wert:
        return None
    stand = _datum(wert)
    if stand > date.today():
        raise ValueError("Stand liegt in der Zukunft")
    return stand.isoformat()


def _notiz(wert, _q: Quelle):
    return str(wert or "").strip()[:2000] or None


#: Je Feld: wie es geprueft wird. None heisst "wie die Basisdatei" und nimmt
#: das Feld aus dem Overlay (nur-abweichungen-speichern).
_FELDER = {"cents": _betrag, "ab": _bezug_ab, "stand": _stand, "notiz": _notiz}


def setzen(kennung: str, felder: dict, config_dir: Path | None = None) -> None:
    """Aus dem Monatsabschluss: Betrag, Bezug ab, Stand und Notiz einer Quelle.

    `entfernen` nimmt die Quelle aus dem Overlay -- dann gilt wieder die
    Basisdatei. Ein leerer Stand laesst den gespeicherten stehen.
    """
    config_dir = config_dir or CONFIG_DIR
    basis = {q.id: q for q in quellen(config_dir)}
    if kennung not in basis:
        raise ValueError(f"Keine Rentenquelle „{kennung}“")
    roh = _yaml(config_dir / EIGEN)
    eigen = dict(roh.get("renten") or {})
    eintrag = {} if felder.get("entfernen") else dict(eigen.get(kennung) or {})
    for feld, pruefen in _FELDER.items():
        if feld not in felder or (feld == "stand" and not felder[feld]):
            continue
        wert = pruefen(felder[feld], basis[kennung])
        if wert is None:
            eintrag.pop(feld, None)
        else:
            eintrag[feld] = wert
    if eintrag:
        eigen[kennung] = eintrag
    else:
        eigen.pop(kennung, None)
    roh["renten"] = eigen
    (config_dir / EIGEN).write_text(
        KOPF + yaml.safe_dump(roh, allow_unicode=True, sort_keys=True),
        encoding="utf-8")
