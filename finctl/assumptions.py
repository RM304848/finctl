"""Read config/assumptions.yaml -- the one place a guess is allowed to live.

Everything the forecast computes comes from here. The distinction the file
exists to draw: an ANNAHME is something somebody chose, a FAKT is something a
document says. A loan rate from a contract, an AfA base from a tax assessment,
a policy value from a Standmitteilung -- those stay in loans.yaml,
properties.yaml and lebensplan.yaml, where their derivation sits beside them.

A scenario is a copy of this file with different values. Not a feature, not a
table, not a screen.

Accessed through this module rather than by reading the YAML wherever it is
needed, so that there is exactly one answer to "what inflation rate is this
projection using". The previous arrangement had the figure implied in three
places -- goals.yaml, lebensplan.yaml and a nominal return that already had it
baked in -- and finctl/config_checks.py existed solely to notice when they
drifted apart.
"""

from __future__ import annotations

import copy
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from finctl.pfade import CONFIG_DIR

PATH = CONFIG_DIR / "assumptions.yaml"


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{path} fehlt. Ohne Annahmen kann nichts projiziert werden -- "
            f"eine leere Datei waere schlimmer, weil dann Vorgabewerte "
            f"rechnen wuerden, die niemand gewaehlt hat.")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


#: Welche Annahmen sich im Dashboard drehen lassen, und wo sie in der Datei
#: stehen. Nur diese: es sind die, an denen der ganze Plan haengt und die
#: eine Meinung sind, keine Messung und keine Unterlage. Eine Standmitteilung
#: der GRV gehoert nicht in ein Eingabefeld, was ein Objekt abwirft, steht am
#: Objekt (`prognose:` in properties.yaml). Teilzeit hat beides: ihre Werte
#: sind eine Annahme und stehen hier, ob sie gilt, schaltet die Klammer
#: "Teilzeit" auf /planung.
UEBERSCHREIBBAR: dict[str, tuple[str, ...]] = {
    "inflation_pa": ("inflation_pa",),
    "rendite_nominal_pa": ("kapital", "rendite_nominal_pa"),
    "rendite_depot_pa": ("kapital", "rendite_depot_pa"),
    "kostenschwelle_pa": ("kapital", "kostenschwelle_pa"),
    "gehalt_steigerung_pa": ("erwerbseinkommen", "gehalt_steigerung_pa"),
    "salary_floor_cents": ("erwerbseinkommen", "netto_floor_monatlich_cents"),
    "teilzeit_anteil": ("erwerbseinkommen", "teilzeit_anteil"),
    "teilzeit_ab": ("erwerbseinkommen", "teilzeit_ab"),
    "lebenserwartung": ("ruhestand", "lebenserwartung"),
    "abzug_renten": ("ruhestand", "abzug_renten"),
    "aufbrauchen": ("ruhestand", "aufbrauchen"),
}


def _overlay() -> dict:
    from finctl import overlays

    return overlays.einstellungen()


def load(path: Path | str = PATH) -> dict:
    """Die ganze Datei, mit dem gelegt, was im Dashboard gesetzt wurde.

    Ein Szenario ist ein anderer Pfad -- und bekommt KEIN Overlay: eine
    Szenariodatei beschreibt eine andere Welt, und sie mit den Einstellungen
    dieser Welt zu ueberschreiben hiesse, beide zu vermischen.
    """
    spec = _load(str(path))
    if str(path) != str(PATH):
        return spec
    ueber = _overlay()
    if not ueber:
        return spec
    # Kopieren, nicht in den zwischengespeicherten Baum schreiben.
    spec = copy.deepcopy(spec)
    for key, pfad in UEBERSCHREIBBAR.items():
        if key not in ueber:
            continue
        knoten = spec
        for teil in pfad[:-1]:
            knoten = knoten.setdefault(teil, {})
        knoten[pfad[-1]] = ueber[key]
    return spec


def basiswert(key: str, path: Path | str = PATH):
    """Was OHNE Overlay in der Datei steht -- die dokumentierte Vorgabe.

    None, wo die Datei schweigt: eine frische Installation traegt nur die
    Startwerte, und die Seite soll sagen "nicht eingetragen", statt mit einem
    KeyError abzubrechen.
    """
    knoten = _load(str(path))
    for teil in UEBERSCHREIBBAR[key]:
        if not isinstance(knoten, dict) or teil not in knoten:
            return None
        knoten = knoten[teil]
    return knoten


def reset_cache() -> None:
    """Forget what was read. For tests and for a file edited while serving."""
    _load.cache_clear()


def get(*keys: str, path: Path | str = PATH, default=None):
    """One value, by its path through the file.

    Raises rather than returning None for a missing key without a stated
    default: a projection that silently falls back to zero inflation produces
    a confident, wrong answer, and nothing about the output would say so.
    """
    node = load(path)
    walked: list[str] = []
    for key in keys:
        walked.append(key)
        if not isinstance(node, dict) or key not in node:
            if default is not None:
                return default
            raise KeyError(
                f"{'.'.join(walked)} fehlt in {path}. Entweder eintragen oder "
                f"einen Vorgabewert angeben -- stillschweigend weiterrechnen "
                f"waere die schlechtere Antwort.")
        node = node[key]
    return node


def inflation_pa(path: Path | str = PATH) -> float:
    return float(get("inflation_pa", path=path))


def rendite_nominal_pa(path: Path | str = PATH) -> float:
    """Das Tagesgeld. Depot und Policen haben ihren eigenen Satz."""
    return float(get("kapital", "rendite_nominal_pa", path=path))


def rendite_depot_pa(path: Path | str = PATH) -> float:
    """Depot und fondsgebundene Policen, vor Steuern -- in der Ansparphase wird nicht verkauft."""
    return float(get("kapital", "rendite_depot_pa", path=path))


def vorabpauschale(path: Path | str = PATH) -> tuple[float, float]:
    """(Anteil des Depotwerts, der als Vorabpauschale gilt; Steuer je Euro davon).

    Die Vorabpauschale ist Depotwert mal Basiszins mal 0,7. Versteuert werden
    davon die 70 %, die die Teilfreistellung uebrig laesst.
    """
    v = get("kapital", "vorabpauschale", path=path)
    return (float(v["basiszins_pa"]) * 0.7,
            (1.0 - float(v["teilfreistellung"])) * float(v["steuersatz"]))



def salary_floor_cents(path: Path | str = PATH) -> int:
    """Der schlechteste beobachtete Monat. Fuer die Liquiditaetsfrage."""
    return int(get("erwerbseinkommen", "netto_floor_monatlich_cents", path=path))


#: Ohne Eintrag: ab welchen Effektivkosten eine Police ein Warnzeichen traegt.
KOSTENSCHWELLE_VORGABE = 0.013


def kostenschwelle_pa(path: Path | str = PATH) -> float:
    """Effektivkosten, ab denen eine Police markiert wird -- eine Meinung, keine Norm."""
    return float(get("kapital", "kostenschwelle_pa", path=path,
                     default=KOSTENSCHWELLE_VORGABE))


def teilzeit_anteil(path: Path | str = PATH) -> float | None:
    """Der Anteil am vollen Gehalt in Teilzeit, 0,8 fuer 80 % -- oder None."""
    wert = get("erwerbseinkommen", "teilzeit_anteil", path=path, default="")
    return float(wert) if wert not in ("", None) else None


def teilzeit_ab(path: Path | str = PATH) -> date | None:
    """Der erste Monat in Teilzeit -- oder None, solange keiner eingetragen ist."""
    wert = str(get("erwerbseinkommen", "teilzeit_ab", path=path, default="") or "")
    if not wert:
        return None
    try:
        return date.fromisoformat(wert[:7] + "-01")
    except ValueError:
        return None


def salary_median_cents(path: Path | str = PATH) -> int:
    """Der mittlere Monat. Fuer die Aufbaufrage.

    Getrennt vom Floor, weil der schlechteste Monat ueber 228 Monate
    fortgeschrieben keine Vorsicht ist, sondern eine Behauptung.
    """
    return int(get("erwerbseinkommen", "netto_median_monatlich_cents", path=path))


def salary_growth_pa(path: Path | str = PATH) -> float:
    """Wie schnell das Gehalt steigt -- nicht zwingend wie die Inflation."""
    return float(get("erwerbseinkommen", "gehalt_steigerung_pa",
                     path=path, default=inflation_pa(path)))


def grow(cents: int, years: float, rate: float) -> int:
    """Einen Betrag mit einer beliebigen Rate fortschreiben."""
    return int(round(cents * (1.0 + rate) ** years))


def inflate(cents: int, years: float, path: Path | str = PATH) -> int:
    """Carry an amount forward at the inflation rate.

    Used for everything that rises with prices. NOT for a loan payment: an
    annuity is fixed in nominal terms and gets cheaper in real terms every
    year, which is the borrower's quiet advantage and must not be inflated
    away.
    """
    return int(round(cents * (1.0 + inflation_pa(path)) ** years))


def lebenserwartung(path: Path | str = PATH) -> int:
    """Bis zu welchem Alter gerechnet wird -- 95, wenn nichts eingetragen ist."""
    return int(get("ruhestand", "lebenserwartung", path=path, default=95))


def abzug_renten(path: Path | str = PATH) -> float:
    """Anteil einer Rente, der fuer Steuer und Krankenversicherung abgeht."""
    return float(get("ruhestand", "abzug_renten", path=path, default=0.25))
