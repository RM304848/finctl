"""Wer plant: Geburtstag, Krankenversicherung, Rentenbeginn.

Drei Angaben, aus denen jede Altersschwelle eine Jahreszahl wird. Sie stehen
von Hand in `config/lebensplan.yaml` (mit ihrer Herleitung) und lassen sich
auf /einrichtung setzen; das landet in `lebensplan_custom.yaml` und gewinnt
beim Lesen. Wer das Werkzeug uebernimmt, traegt hier seine eigenen ein.

Die Fristen sind Gesetz, keine Annahme, und stehen deshalb hier und nicht in
einer Datei: wer 55 ist und die letzten fuenf Jahre nicht gesetzlich
versichert war, kommt nicht mehr in die GKV zurueck (§6 Abs. 3a SGB V). Die
Frist gibt es nur fuer Privatversicherte -- wer gesetzlich versichert ist,
bekommt keine.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from finctl.pfade import CONFIG_DIR

BASIS = "lebensplan.yaml"
EIGEN = "lebensplan_custom.yaml"

#: Wie die Seite die Arten nennt. Leer heisst: nicht angegeben.
KRANKENVERSICHERUNG = {"gesetzlich": "gesetzlich", "privat": "privat"}
#: Regelaltersgrenze ab Jahrgang 1964.
RENTE_AB_ALTER = 67
#: §6 Abs. 3a SGB V.
GKV_SPERRE_ALTER = 55

KOPF = """# Auf /einrichtung gesetzt: Geburtstag, Krankenversicherung, Rentenalter.
#
# Ueberschreibt config/lebensplan.yaml, wo die Herleitung steht. Nur was
# davon abweicht, steht hier.

"""


def _yaml(pfad: Path) -> dict:
    if not pfad.exists():
        return {}
    return yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}


def _datum(wert) -> date | None:
    if not wert:
        return None
    return wert if isinstance(wert, date) else date.fromisoformat(str(wert)[:10])


def _basis(config_dir: Path) -> dict:
    roh = _yaml(config_dir / BASIS)
    person = roh.get("person") or {}
    return {"geburtsdatum": _datum(person.get("birth_date")),
            "krankenversicherung": str(person.get("krankenversicherung") or ""),
            "rente_ab_alter": int((roh.get("schwellen") or {}).get("grv_ab_alter")
                                  or RENTE_AB_ALTER)}


def angaben(config_dir: Path | None = None) -> dict:
    """Die drei Angaben, das Overlay ueber der Basisdatei."""
    config_dir = config_dir or CONFIG_DIR
    aus = _basis(config_dir)
    eigen = _yaml(config_dir / EIGEN).get("person") or {}
    if eigen.get("geburtsdatum"):
        aus["geburtsdatum"] = _datum(eigen["geburtsdatum"])
    if eigen.get("krankenversicherung"):
        aus["krankenversicherung"] = str(eigen["krankenversicherung"])
    if eigen.get("rente_ab_alter"):
        aus["rente_ab_alter"] = int(eigen["rente_ab_alter"])
    return aus


def geburtstag(config_dir: Path | None = None) -> date | None:
    return angaben(config_dir)["geburtsdatum"]


def _mit_alter(g: date, alter: int) -> date:
    # Am 29. Februar Geborene werden im Nicht-Schaltjahr am 28. aelter.
    try:
        return g.replace(year=g.year + alter)
    except ValueError:
        return g.replace(year=g.year + alter, day=28)


def rentenbeginn(config_dir: Path | None = None) -> date | None:
    """Der Monat NACH dem Geburtstag, an dem das Rentenalter erreicht ist --
    so legt die Rentenversicherung den Beginn fest."""
    a = angaben(config_dir)
    if a["geburtsdatum"] is None:
        return None
    tag = _mit_alter(a["geburtsdatum"], a["rente_ab_alter"])
    return date(tag.year + tag.month // 12, tag.month % 12 + 1, 1)


def pkv_sperre(config_dir: Path | None = None) -> date | None:
    """Ab wann die Rueckkehr in die GKV ausgeschlossen ist -- nur bei PKV."""
    a = angaben(config_dir)
    if a["krankenversicherung"] != "privat" or a["geburtsdatum"] is None:
        return None
    return _mit_alter(a["geburtsdatum"], GKV_SPERRE_ALTER)


def pkv_stichtag(config_dir: Path | None = None) -> date | None:
    """Ein Jahr davor. Eine Frist, die man am Tag ihres Ablaufs bemerkt, ist
    verpasst -- ein Kassenwechsel braucht Antrag, Fristen und Vorlauf."""
    sperre = pkv_sperre(config_dir)
    return _mit_alter(sperre, -1) if sperre else None


def setzen(felder: dict, config_dir: Path | None = None) -> dict:
    """Von /einrichtung: nur, was von der Basisdatei abweicht."""
    config_dir = config_dir or CONFIG_DIR
    basis = _basis(config_dir)
    eigen = dict(_yaml(config_dir / EIGEN).get("person") or {})
    if "geburtsdatum" in felder:
        wert = _datum(felder["geburtsdatum"]) if felder["geburtsdatum"] else None
        if wert and not date(1900, 1, 1) <= wert <= date.today():
            raise ValueError("Geburtsdatum als JJJJ-MM-TT, in der Vergangenheit")
        _abweichung(eigen, "geburtsdatum", wert, basis, str)
    if "krankenversicherung" in felder:
        wert = str(felder["krankenversicherung"] or "")
        if wert and wert not in KRANKENVERSICHERUNG:
            raise ValueError("Krankenversicherung: gesetzlich oder privat")
        _abweichung(eigen, "krankenversicherung", wert, basis, str)
    if "rente_ab_alter" in felder:
        wert = int(felder["rente_ab_alter"]) if felder["rente_ab_alter"] else None
        if wert is not None and not 50 <= wert <= 75:
            raise ValueError("Rentenalter zwischen 50 und 75")
        _abweichung(eigen, "rente_ab_alter", wert, basis, int)
    roh = _yaml(config_dir / EIGEN)
    if eigen:
        roh["person"] = eigen
    else:
        roh.pop("person", None)
    (config_dir / EIGEN).write_text(
        KOPF + yaml.safe_dump(roh, allow_unicode=True, sort_keys=True),
        encoding="utf-8")
    return angaben(config_dir)


def _abweichung(eigen: dict, feld: str, wert, basis: dict, art) -> None:
    if wert in (None, "") or wert == basis.get(feld):
        eigen.pop(feld, None)
    else:
        eigen[feld] = art(wert) if not isinstance(wert, date) else wert.isoformat()
