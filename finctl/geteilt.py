"""Geteilt: Projekte, Aufteilung auf Personen, Salden je Person und Projekt.

Ein PROJEKT sammelt Ausgaben, die zusammengehoeren -- eine Reise, ein Umbau,
ein Geschenk. Mit Personen wird es geteilt, ohne Personen sammelt es nur die
Kosten. Kategorie und Steuer bleiben davon unberuehrt: das Projekt ist eine
eigene Achse neben ihnen und aendert keine einzige Buchung im Ledger.

Zugeordnet wird je Aufteilungsteil, geschluesselt ueber `dedup_hash` und die
Nummer des Teils -- wie `overrides.yaml`, damit alles einen Neuaufbau der
Datenbank uebersteht.

Rueckzahlungen werden NICHT verfolgt. Was andere fuer einen ausgelegt haben,
weiss die App ohnehin nicht; ein Saldo waere nur halb richtig. Stattdessen
traegt jede Person je Projekt einen Haken "ausgeglichen", und der merkt sich
den Stand beim Abhaken. Kommt danach eine Ausgabe dazu, ist genau die
Differenz wieder offen -- ein Haken macht spaetere Ausgaben nicht unsichtbar.

Alles liegt in `config/geteilt_custom.yaml`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from finctl.pfade import CONFIG_DIR

PFAD = CONFIG_DIR / "geteilt_custom.yaml"
ARTEN = ("gleich", "betrag", "prozent", "gewicht", "nur-ich")
ICH = "ich"

KOPF = """\
# Geteilt -- geschrieben von den Seiten unter "Geteilt".
#
# personen: wer mitmacht. projekte: was zusammengehoert, mit wem, und je
# Person der Stand beim Abhaken "ausgeglichen". buchungen: welcher Teil
# welcher Buchung zu welchem Projekt gehoert, geschluesselt ueber dedup_hash
# wie overrides.yaml; ohne `teilung` gleich auf alle Projektpersonen und mich.

"""

_KENNUNG = re.compile(r"[^a-z0-9]+")


# ------------------------------------------------------------------ Daten

@dataclass(slots=True)
class Projekt:
    id: str
    name: str
    personen: list[str] = field(default_factory=list)
    #: je Person: {"am": "JJJJ-MM-TT", "cents": Stand beim Abhaken}
    ausgeglichen: dict[str, dict] = field(default_factory=dict)


@dataclass(slots=True)
class Zuordnung:
    buchung: str
    teil: int
    projekt: str
    #: None = gleich auf alle Projektpersonen und mich
    teilung: dict | None = None


@dataclass(slots=True)
class Daten:
    personen: dict[str, str] = field(default_factory=dict)      # id -> Name
    projekte: dict[str, Projekt] = field(default_factory=dict)
    buchungen: dict[tuple[str, int], Zuordnung] = field(default_factory=dict)


def kennung(name: str, vorhanden: set[str]) -> str:
    """Aus einem Namen eine Kennung, die es noch nicht gibt."""
    ersetzt = (name.lower().replace("ä", "ae").replace("ö", "oe")
               .replace("ü", "ue").replace("ß", "ss"))
    basis = _KENNUNG.sub("-", ersetzt).strip("-") or "eintrag"
    kandidat, n = basis, 2
    while kandidat in vorhanden:
        kandidat, n = f"{basis}-{n}", n + 1
    return kandidat


def laden(pfad: Path | None = None) -> Daten:
    pfad = pfad or PFAD
    if not pfad.exists():
        return Daten()
    roh = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
    d = Daten()
    for p in roh.get("personen") or []:
        d.personen[str(p["id"])] = str(p.get("name") or p["id"])
    for p in roh.get("projekte") or []:
        d.projekte[str(p["id"])] = Projekt(
            str(p["id"]), str(p.get("name") or p["id"]),
            [str(x) for x in p.get("personen") or []],
            {str(k): dict(v) for k, v in (p.get("ausgeglichen") or {}).items()})
    for b in roh.get("buchungen") or []:
        z = Zuordnung(str(b["buchung"]), int(b.get("teil") or 0), str(b["projekt"]),
                      b.get("teilung") or None)
        d.buchungen[(z.buchung, z.teil)] = z
    return d


def schreiben(d: Daten, pfad: Path | None = None) -> None:
    pfad = pfad or PFAD
    roh = {
        "personen": [{"id": k, "name": v} for k, v in d.personen.items()],
        "projekte": [
            {"id": p.id, "name": p.name, "personen": p.personen,
             **({"ausgeglichen": p.ausgeglichen} if p.ausgeglichen else {})}
            for p in d.projekte.values()],
        "buchungen": [
            {"buchung": z.buchung, **({"teil": z.teil} if z.teil else {}),
             "projekt": z.projekt, **({"teilung": z.teilung} if z.teilung else {})}
            for z in d.buchungen.values()],
    }
    # Erst in eine Nachbardatei, dann umbenennen: bricht das Schreiben ab,
    # bleibt die alte Datei ganz und nicht halb.
    neu = pfad.with_suffix(pfad.suffix + ".neu")
    neu.write_text(KOPF + yaml.safe_dump(roh, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    os.replace(neu, pfad)


# ---------------------------------------------------------------- Anteile

def _groesster_rest(gesamt: int, gewichte: dict[str, float]) -> dict[str, int]:
    """`gesamt` Cent nach Gewichten verteilt, ohne einen Cent zu verlieren."""
    summe = sum(gewichte.values())
    if summe <= 0:
        raise ValueError("Die Gewichte ergeben zusammen null.")
    roh = {k: gesamt * g / summe for k, g in gewichte.items()}
    out = {k: int(v) for k, v in roh.items()}
    rest = gesamt - sum(out.values())
    for k in sorted(roh, key=lambda k: roh[k] - out[k], reverse=True)[:rest]:
        out[k] += 1
    return out


def anteile(cents: int, teilung: dict | None, projekt: Projekt) -> dict[str, int]:
    """Wer wie viel von einer Buchung traegt, in Cent, Summe genau `cents`.

    `cents` ist die Ausgabe positiv (was vom Konto ging); eine Erstattung ist
    negativ und verteilt sich genauso. Der Schluessel `ich` ist der eigene
    Anteil. Ohne `teilung` gleich auf alle Projektpersonen und mich.
    """
    if not projekt.personen:
        return {ICH: cents}
    t = teilung or {"art": "gleich"}
    art = t.get("art") or "gleich"
    if art not in ARTEN:
        raise ValueError(f"Unbekannte Aufteilung {art!r}.")
    werte = _werte(t, projekt)
    vorzeichen, betrag = (1 if cents >= 0 else -1), abs(cents)
    verteilt = _verteilen(art, betrag, werte, t, projekt)
    return {k: vorzeichen * v for k, v in verteilt.items() if v or k == ICH}


def _werte(t: dict, projekt: Projekt) -> dict[str, float]:
    werte = {str(k): float(v) for k, v in (t.get("personen") or {}).items()}
    fremd = [p for p in werte if p not in projekt.personen]
    if fremd:
        raise ValueError(f"Nicht im Projekt: {', '.join(fremd)}.")
    if any(v < 0 for v in werte.values()):
        raise ValueError("Negative Werte gibt es in einer Aufteilung nicht.")
    return werte


def _verteilen(art: str, betrag: int, werte: dict[str, float], t: dict,
               projekt: Projekt) -> dict[str, int]:
    if art == "nur-ich":
        return {ICH: betrag}
    if art == "gleich":
        ich = 1.0 if t.get("ich", True) not in (False, 0, "0") else 0.0
        return _groesster_rest(betrag, {ICH: ich,
                                        **dict.fromkeys(werte or projekt.personen, 1.0)})
    if art == "gewicht":
        return _groesster_rest(betrag, {ICH: float(t.get("ich") or 0), **werte})
    if art == "betrag":
        fest = {p: round(v) for p, v in werte.items()}
        if sum(fest.values()) > betrag:
            raise ValueError("Die Beträge der anderen sind höher als die Buchung.")
        return {**fest, ICH: betrag - sum(fest.values())}
    if sum(werte.values()) > 100:
        raise ValueError("Zusammen mehr als 100 %.")
    return _groesster_rest(betrag, {ICH: 100 - sum(werte.values()), **werte})


# ----------------------------------------------------------------- Salden

@dataclass(frozen=True, slots=True)
class Posten:
    """Ein zugeordneter Aufteilungsteil mit seinen Anteilen."""
    buchung: str
    teil: int
    teile: int
    datum: str
    konto: str
    text: str
    kategorie: str | None
    cents: int                       # Vorzeichen wie im Ledger
    anteile: dict[str, int]          # je Person und `ich`, Ausgabe positiv
    eigene_teilung: bool


def posten(daten: Daten, zeilen: list[dict]) -> dict[str, list[Posten]]:
    """Die zugeordneten Teile je Projekt.

    `zeilen` sind Aufteilungsteile aus dem Ledger mit `dedup_hash`, `seq`,
    `teile`, `booking_date`, `account_id`, `raw_text`, `mgmt_category_id` und
    `amount_cents`.
    """
    je_projekt: dict[str, list[Posten]] = {p: [] for p in daten.projekte}
    for z in zeilen:
        zu = daten.buchungen.get((z["dedup_hash"], z["seq"]))
        if zu is None or zu.projekt not in daten.projekte:
            continue
        projekt = daten.projekte[zu.projekt]
        je_projekt[zu.projekt].append(Posten(
            z["dedup_hash"], z["seq"], z["teile"], z["booking_date"], z["account_id"],
            z["raw_text"], z["mgmt_category_id"], z["amount_cents"],
            anteile(-z["amount_cents"], zu.teilung, projekt), zu.teilung is not None))
    return je_projekt


@dataclass(frozen=True, slots=True)
class Saldo:
    projekt: str
    person: str
    anteil_cents: int
    ausgeglichen_cents: int | None
    ausgeglichen_am: str | None

    @property
    def offen_cents(self) -> int:
        return self.anteil_cents - (self.ausgeglichen_cents or 0)

    @property
    def seit_ausgleich_cents(self) -> int | None:
        if self.ausgeglichen_cents is None:
            return None
        return self.anteil_cents - self.ausgeglichen_cents


def salden(daten: Daten, je_projekt: dict[str, list[Posten]]) -> list[Saldo]:
    """Je Person und Projekt: Anteil, Stand beim Abhaken, offen."""
    out = []
    for p in daten.projekte.values():
        for person in p.personen:
            anteil = sum(x.anteile.get(person, 0) for x in je_projekt.get(p.id, []))
            haken = p.ausgeglichen.get(person)
            out.append(Saldo(p.id, person, anteil,
                             int(haken["cents"]) if haken else None,
                             str(haken["am"]) if haken else None))
    return out


def ausgleichen(daten: Daten, projekt: str, person: str, an: bool,
                anteil_cents: int, heute: date) -> None:
    p = daten.projekte.get(projekt)
    if p is None or person not in p.personen:
        raise ValueError("Diese Person gehört nicht zu diesem Projekt.")
    if an:
        p.ausgeglichen[person] = {"am": heute.isoformat(), "cents": anteil_cents}
    else:
        p.ausgeglichen.pop(person, None)


# --------------------------------------------------------------- Pruefung

def projekt_setzen(daten: Daten, projekt_id: str | None, name: str,
                   personen: list[str]) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Ein Projekt braucht einen Namen.")
    fremd = [p for p in personen if p not in daten.personen]
    if fremd:
        raise ValueError(f"Unbekannte Person: {', '.join(fremd)}.")
    if projekt_id is None:
        projekt_id = kennung(name, set(daten.projekte))
        daten.projekte[projekt_id] = Projekt(projekt_id, name, list(personen))
        return projekt_id
    p = daten.projekte.get(projekt_id)
    if p is None:
        raise ValueError("Unbekanntes Projekt.")
    raus = [x for x in p.personen if x not in personen]
    genutzt = [z for z in daten.buchungen.values()
               if z.projekt == projekt_id and z.teilung
               and any(x in (z.teilung.get("personen") or {}) for x in raus)]
    if genutzt:
        raise ValueError("Wer eine eigene Teilung einer Buchung trägt, lässt sich "
                         "nicht entfernen; erst die Teilung ändern.")
    p.name, p.personen = name, list(personen)
    for x in raus:
        p.ausgeglichen.pop(x, None)
    return projekt_id


def person_setzen(daten: Daten, person_id: str | None, name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Eine Person braucht einen Namen.")
    if person_id is None:
        person_id = kennung(name, set(daten.personen) | {ICH})
    elif person_id not in daten.personen:
        raise ValueError("Unbekannte Person.")
    daten.personen[person_id] = name
    return person_id


def zuordnen(daten: Daten, teile: list[tuple[str, int]], projekt: str | None) -> int:
    """Teile einem Projekt zuordnen, oder mit `None` die Zuordnung loesen.

    Ein Wechsel des Projekts verwirft eine eigene Teilung: sie nannte Personen
    des alten Projekts.
    """
    if projekt is not None and projekt not in daten.projekte:
        raise ValueError("Unbekanntes Projekt.")
    for schluessel in teile:
        alt = daten.buchungen.get(schluessel)
        if projekt is None:
            daten.buchungen.pop(schluessel, None)
        elif alt is None or alt.projekt != projekt:
            daten.buchungen[schluessel] = Zuordnung(*schluessel, projekt)
    return len(teile)


def teilung_setzen(daten: Daten, schluessel: tuple[str, int], teilung: dict | None,
                   cents: int) -> dict[str, int]:
    """Eine eigene Teilung setzen (`None` = wieder gleich). Gibt die Anteile zurueck."""
    zu = daten.buchungen.get(schluessel)
    if zu is None:
        raise ValueError("Die Buchung gehört zu keinem Projekt.")
    if teilung and not daten.projekte[zu.projekt].personen:
        raise ValueError("Ein Projekt ohne Personen wird nicht geteilt.")
    ergebnis = anteile(cents, teilung, daten.projekte[zu.projekt])
    zu.teilung = teilung or None
    return ergebnis
