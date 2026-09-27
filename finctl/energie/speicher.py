"""Wo Zaehler und Vorraete stehen.

Der Strom steht in `config/strom.yaml`, wie bisher (`finctl/strom.py`). Alles
andere -- weitere Zaehler, jeder Vorrat -- in `config/energie.yaml`, geschrieben
von der Seite /energie. Keine Datenbank: die Dateien gehen mit `config/` ins
Backup, und von Hand aendern geht auch.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from finctl import strom as _strom
from finctl.energie import profil as _profil
from finctl.energie import vorrat as _vorrat
from finctl.energie import zaehler as _z
from finctl.pfade import CONFIG_DIR

PFAD = CONFIG_DIR / "energie.yaml"

KOPF = """\
# Energie ausser Strom -- geschrieben von der Seite /energie.
#
# zaehler: je Zaehler Art, Einheit, Profil und dieselben Zeitraeume und
#   Ablesungen wie config/strom.yaml. Gas rechnet m³ mit Brennwert und
#   Zustandszahl (beide auf der Rechnung) in kWh um.
# vorraete: je Tank oder Lager die Lieferungen (Menge, Betrag in Cent) und
#   die Peilungen (Fuellstand am Tag).
# profil: linear, oder heizung (Gradtagzahlen); grundlast ist der Anteil
#   ohne Jahresgang, etwa Warmwasser.

"""

#: Art -> (Name, Zaehlereinheit, Abrechnungseinheit, Profil)
ZAEHLERARTEN = {
    "strom": ("Strom", "kWh", "kWh", "linear"),
    "gas": ("Gas", "m³", "kWh", "heizung"),
    "fernwaerme": ("Fernwärme", "kWh", "kWh", "heizung"),
    "wasser": ("Wasser", "m³", "m³", "linear"),
}


@dataclass(slots=True)
class Zaehler:
    id: str
    art: str
    name: str
    messung: _z.Messung
    zeitraeume: list[_z.Zeitraum] = field(default_factory=list)
    ablesungen: list[_z.Ablesung] = field(default_factory=list)
    #: Nur Gas: kWh je m³ und die Zustandszahl, beide von der Rechnung.
    brennwert: float | None = None
    zustandszahl: float | None = None


def zaehler_aus(zid: str, roh: dict) -> Zaehler:
    art = str(roh.get("art") or "")
    if art not in ZAEHLERARTEN:
        raise ValueError(f"{zid}: unbekannte Art {art!r}, erwartet: {', '.join(ZAEHLERARTEN)}")
    name, einheit, abrechnung, profil = ZAEHLERARTEN[art]
    brennwert = zustandszahl = None
    faktor = 1.0
    if art == "gas":
        brennwert = _z.lies_zahl(roh.get("brennwert"), "Brennwert")
        zustandszahl = _z.lies_zahl(roh.get("zustandszahl"), "Zustandszahl")
        faktor = brennwert * zustandszahl
    messung = _z.Messung(einheit=einheit, abrechnung=abrechnung, faktor=faktor,
                         profil=str(roh.get("profil") or profil),
                         grundlast=_z.lies_zahl(roh.get("grundlast") or 0, "Grundlast"))
    zeitraeume, ablesungen = _z.aus_roh(roh)
    return Zaehler(zid, art, str(roh.get("name") or name), messung, zeitraeume, ablesungen,
                   brennwert, zustandszahl)


def zaehler_als_roh(z: Zaehler) -> dict:
    roh: dict = {"art": z.art, "name": z.name, "profil": z.messung.profil}
    if z.messung.grundlast:
        roh["grundlast"] = z.messung.grundlast
    if z.art == "gas":
        roh["brennwert"], roh["zustandszahl"] = z.brennwert, z.zustandszahl
    return {**roh, **_z.als_roh(z.zeitraeume, z.ablesungen)}


def einstellungen_pruefen(z: Zaehler) -> None:
    if z.messung.profil not in _profil.PROFILE:
        raise ValueError(f"Unbekanntes Profil {z.messung.profil!r}.")
    _profil.grundlast_pruefen(z.messung.grundlast)
    if z.art == "gas" and not (0 < (z.brennwert or 0) < 20 and 0 < (z.zustandszahl or 0) <= 1.2):
        raise ValueError("Brennwert (kWh/m³, meist um 10 bis 12) und Zustandszahl "
                         "(meist knapp unter 1) stehen auf der Gasrechnung.")


def kennung(name: str, vergeben: set[str]) -> str:
    basis = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()
                   .replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
                   .replace("ß", "ss")).strip("-") or "zaehler"
    k, n = basis, 2
    while k in vergeben:
        k, n = f"{basis}-{n}", n + 1
    return k


# ------------------------------------------------------------ Laden, Schreiben

@dataclass(slots=True)
class Bestand:
    """Alles, was auf /energie steht. Der Strom ist immer der erste Zaehler."""
    zaehler: dict[str, Zaehler]
    vorraete: dict[str, _vorrat.Vorrat]


def strom_zaehler(pfad: Path | None = None) -> Zaehler:
    zeitraeume, ablesungen = _strom.laden(pfad or _strom.PFAD)
    return Zaehler("strom", "strom", "Strom", _z.STROM, zeitraeume, ablesungen)


def laden(pfad: Path | None = None) -> Bestand:
    pfad = pfad or PFAD
    roh = (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}) if pfad.exists() else {}
    zaehler = {"strom": strom_zaehler()}
    for zid, z in (roh.get("zaehler") or {}).items():
        if str(zid) == "strom":
            raise ValueError("Die Kennung strom gehört config/strom.yaml.")
        zaehler[str(zid)] = zaehler_aus(str(zid), z or {})
    vorraete = {str(vid): _vorrat.aus_roh(str(vid), v or {})
                for vid, v in (roh.get("vorraete") or {}).items()}
    return Bestand(zaehler, vorraete)


def schreiben(bestand: Bestand, geaendert: str, pfad: Path | None = None) -> None:
    """Die Datei schreiben, in der `geaendert` steht -- und nur die.

    Der Strom geht nach strom.yaml, alles andere nach energie.yaml. Eine
    Ablesung am Gaszaehler schreibt strom.yaml nicht neu.
    """
    if geaendert == "strom":
        strom = bestand.zaehler["strom"]
        _strom.schreiben(strom.zeitraeume, strom.ablesungen, _strom.PFAD)
        return
    pfad = pfad or PFAD
    daten = {"zaehler": {k: zaehler_als_roh(z) for k, z in bestand.zaehler.items()
                         if k != "strom"},
             "vorraete": {k: _vorrat.als_roh(v) for k, v in bestand.vorraete.items()}}
    neu = pfad.with_suffix(pfad.suffix + ".neu")
    neu.write_text(KOPF + yaml.safe_dump(daten, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    os.replace(neu, pfad)
