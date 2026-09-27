"""Objekte anlegen und entfernen -- ohne YAML von Hand.

`config/properties.yaml` traegt zu jedem Objekt seine Herleitung: die
AfA-Basis aus der Feststellungserklaerung, das Anschaffungsdatum aus dem
Notarvertrag, bei einem Leasehold den Restwert null am Ende der Laufzeit.
Dreissig Zeilen Begruendung je Eintrag, und genau deshalb schreibt diese
Seite die Datei nicht neu.

DREI FELDER. Ein Objekt anzulegen heisst hier: ihm eine Kennung geben, damit
Buchungen ihm zugeordnet werden koennen. Mehr nicht.

Der erste Entwurf fragte zusaetzlich nach dem Anschaffungsdatum und lehnte
ohne es ab, mit der Zehnjahresfrist des §23 EStG als Begruendung. Das war zu
viel: WAS ein Objekt gekostet hat, steht als einmaliger Betrag in der
Planung, und die kann das laengst -- `immobilie/kaufnebenkosten` mit
`frequenz: einmalig`. Zwei Stellen fuer dieselbe Zahl waeren zwei Wahrheiten,
und wer ein Objekt anlegt, hat den Notarvertrag nicht neben sich liegen.

Steuerfelder und Datum bleiben auf /immobilie, neben der Unterlage, aus der
sie stammen. Wer sie braucht, traegt sie dort ein; wer nicht, wird nicht
danach gefragt.

ENTFERNEN IST DER WICHTIGERE TEIL, wie bei den Krediten. Wer dieses Werkzeug
uebernimmt, erbt sonst fremde Wohnungen in seiner Vermoegensrechnung.
`entfernt: true` blendet einen Eintrag der Basisdatei aus, ohne sie
anzufassen.

Geschrieben wird nach `properties_custom.yaml` unter `objekte:` -- dieselbe
Stelle, an der /immobilie die getippten Zahlen ablegt. Zwei Schluessel fuer
dasselbe Objekt waeren zwei Wahrheiten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from finctl.pfade import CONFIG_DIR

BASIS = "properties.yaml"
EIGEN = "properties_custom.yaml"

#: Was ein Objekt sein kann, und wie es auf der Seite heisst. Die Kennungen
#: stehen so in der Datenbank und in properties.yaml; die Beschriftung ist
#: deutsch, weil die Oberflaeche es ist.
ZUSTAENDE = {
    "rented": "vermietet",
    "owner_occupied": "selbst bewohnt",
    "construction": "im Bau",
    "sold": "verkauft",
}

#: Die Felder dieser Maske, in der Reihenfolge der Seite.
FELDER = ("id", "name", "status")

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def _yaml(pfad: Path) -> dict:
    if not pfad.exists():
        return {}
    return yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}


def objekt(felder: dict) -> dict:
    """Aus den Angaben der Maske ein Eintrag, wie ihn `init` einliest."""
    return {
        "id": str(felder.get("id") or "").strip(),
        "name": str(felder.get("name") or "").strip(),
        "status": str(felder.get("status") or "").strip(),
    }


def vorhandene(config_dir: Path | None = None) -> dict[str, dict]:
    """Alle Objekte, Basis und Eigene zusammengelegt -- ohne die entfernten.

    Dieselbe Reihenfolge wie beim Lesen: die Basisdatei stellt, das Overlay
    ueberschreibt Feld fuer Feld, `entfernt: true` blendet aus.
    """
    config_dir = config_dir or CONFIG_DIR
    aus = {str(p.get("id")): dict(p)
           for p in (_yaml(config_dir / BASIS).get("properties") or [])
           if p.get("id")}
    for kennung, eintrag in (_yaml(config_dir / EIGEN).get("objekte") or {}).items():
        eintrag = eintrag or {}
        if eintrag.get("entfernt"):
            aus.pop(kennung, None)
            continue
        basis = aus.get(kennung, {"id": kennung})
        aus[kennung] = {**basis, **eintrag}
        # Die Prognose Feld fuer Feld: wer auf der Seite nur die Miete tippt,
        # soll das Ende der Laufzeit aus der Basisdatei behalten.
        if "prognose" in eintrag:
            aus[kennung]["prognose"] = {**(basis.get("prognose") or {}),
                                        **(eintrag["prognose"] or {})}
    return aus


def pruefen(eintrag: dict, vergeben: set[str]) -> list[str]:
    """Was an diesem Objekt nicht stimmt, in Worten. Leer heisst: in Ordnung."""
    fehler = []
    kennung = eintrag.get("id") or ""
    if not _ID.match(kennung):
        fehler.append("Kennung: klein, Ziffern und Bindestriche, "
                      "höchstens 41 Zeichen")
    elif kennung in vergeben:
        fehler.append(f"Kennung „{kennung}“ ist schon vergeben")
    if not eintrag.get("name"):
        fehler.append("Name fehlt")
    if eintrag.get("status") not in ZUSTAENDE:
        fehler.append("Zustand: " + ", ".join(ZUSTAENDE.values()))
    return fehler


# ------------------------------------------------------------------ schreiben

KOPF = """# Im Dashboard erfasste Objektdaten.
#
# Ueberschreibt config/properties.yaml je Objekt. Dort stehen die Fakten mit
# ihrer Herleitung -- AfA-Basis aus der Feststellung, Anschaffungsdatum aus
# dem Notarvertrag --, hier stehen die Zahlen, die in keiner Unterlage stehen
# und die nur der Eigentuemer kennt: was er gezahlt hat, was er einsetzt, was
# er beim Verkauf erwartet. Dazu die auf /immobilien angelegten Objekte.
#
# `entfernt: true` blendet ein Objekt der Basisdatei aus, ohne deren
# Begruendung zu loeschen.
#
# Ohne diese Datei laegen die getippten Zahlen ausschliesslich in der
# Datenbank. Die laesst sich aus Auszuegen wiederherstellen -- diese Zahlen
# nicht, denn sie sind nirgends gebucht.

"""


def _speichern(config_dir: Path, eigene: dict) -> None:
    roh = _yaml(config_dir / EIGEN)
    roh["objekte"] = eigene
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / EIGEN).write_text(
        KOPF + yaml.safe_dump(roh, allow_unicode=True, sort_keys=True),
        encoding="utf-8")


def anlegen(felder: dict, config_dir: Path | None = None) -> dict:
    """Ein Objekt anlegen. Die Basisdatei bleibt unangetastet."""
    config_dir = config_dir or CONFIG_DIR
    eintrag = objekt(felder)
    fehler = pruefen(eintrag, set(vorhandene(config_dir)))
    if fehler:
        raise ValueError("; ".join(fehler))

    eigene = dict(_yaml(config_dir / EIGEN).get("objekte") or {})
    eigene[eintrag["id"]] = {f: v for f, v in eintrag.items()
                             if f != "id" and v is not None}
    _speichern(config_dir, eigene)
    return eintrag


def entfernen(kennung: str, config_dir: Path | None = None) -> None:
    """Ein Objekt ausblenden.

    Wer hier selbst angelegt hat, bekommt den Eintrag geloescht. Wer eines
    der Basisdatei entfernt, bekommt `entfernt: true` -- die Datei traegt
    eine Begruendung, die niemand per Klick verlieren soll.
    """
    config_dir = config_dir or CONFIG_DIR
    if kennung not in vorhandene(config_dir):
        raise ValueError(f"Kein Objekt „{kennung}“")

    aus_basis = kennung in {str(p.get("id")) for p
                            in (_yaml(config_dir / BASIS).get("properties") or [])}
    eigene = dict(_yaml(config_dir / EIGEN).get("objekte") or {})
    if aus_basis:
        eigene[kennung] = {"entfernt": True}
    else:
        eigene.pop(kennung, None)
    _speichern(config_dir, eigene)


# ------------------------------------------------------------------ Prognose

#: Die Felder einer Objektprognose, wie sie auf /immobilie/<id> stehen und in
#: `prognose:` eines Objekts gespeichert werden.
PROGNOSE_FELDER = ("miete_monatlich_cents", "kosten_monatlich_cents", "ab", "bis")


@dataclass(frozen=True, slots=True)
class Prognose:
    """Was ein Objekt kuenftig abwirft -- angenommen statt gemessen.

    Ein Objekt bekommt eine Prognose, wenn die Messung es nicht tragen kann:
    ein Monat Miete im Ledger, ueber ein Jahr gemittelt, waere ein Zwoelftel
    des Ertrags, und zwanzig Jahre fortgeschrieben der groesste stille Fehler
    der Hochrechnung. Objekte ohne Prognose bleiben gemessen.
    """

    id: str
    name: str
    miete_cents: int
    kosten_cents: int
    #: Erster und letzter Monat. None heisst: offen.
    ab: date | None = None
    bis: date | None = None
    saison: tuple[float, ...] = (1.0,) * 12

    @property
    def netto_cents(self) -> int:
        return self.miete_cents - self.kosten_cents

    def laeuft(self, monat: date) -> bool:
        erster = date(monat.year, monat.month, 1)
        if self.ab and erster < date(self.ab.year, self.ab.month, 1):
            return False
        return not (self.bis and erster > date(self.bis.year, self.bis.month, 1))


def als_monat(wert) -> date | None:
    """YAML liefert ein Datum mal geparst, mal als "2026-09"."""
    if not wert:
        return None
    if isinstance(wert, date):
        return wert
    text = str(wert)
    return date.fromisoformat(text + "-01" if len(text) == 7 else text[:10])


def saison(roh, wo: str = "prognose.saison") -> tuple[float, ...]:
    """Zwoelf Monatsfaktoren, die sich auf 12 summieren.

    Saisonalitaet verschiebt Geld zwischen Monaten, sie erfindet und
    vernichtet keines. Eine Kurve, die sich nebenbei auf 13 summiert, waere
    eine verdeckte Ertragsannahme.
    """
    faktoren = tuple(float(x) for x in (roh if roh is not None else [1.0] * 12))
    if len(faktoren) != 12:
        raise ValueError(f"{wo} hat {len(faktoren)} Werte, gebraucht werden 12 "
                         f"-- einer je Monat.")
    if abs(sum(faktoren) - 12.0) > 0.01:
        raise ValueError(f"{wo} summiert sich auf {sum(faktoren):.2f} statt auf 12. "
                         f"Saisonalitaet verschiebt Geld zwischen Monaten; eine "
                         f"abweichende Summe waere eine verdeckte Ertragsannahme.")
    return faktoren


def _szenario_objekte(szenario: Path | str | None) -> dict[str, dict]:
    """Was eine Szenariodatei je Objekt anders annimmt.

    Unter `immobilien.objekte.<kennung>` in einer Kopie von assumptions.yaml.
    Nur die Groessen, die das Szenario bewegt -- Beginn und Ende der
    Vermietung sind Fakten des Objekts und bleiben dort.
    """
    if szenario is None:
        return {}
    roh = _yaml(Path(szenario)).get("immobilien") or {}
    return dict(roh.get("objekte") or {})


def prognosen(szenario: Path | str | None = None,
              config_dir: Path | None = None) -> list[Prognose]:
    """Alle Objekte mit Prognose, in der Reihenfolge der Konfiguration.

    Basis `properties.yaml`, darueber was auf /immobilie/<id> getippt wurde
    (`properties_custom.yaml`), darueber ein Szenario. Wer kein Objekt hat
    oder keines mit Prognose, bekommt eine leere Liste -- der Normalfall.
    """
    anders = _szenario_objekte(szenario)
    aus = []
    for kennung, eintrag in vorhandene(config_dir).items():
        if eintrag.get("status") == "sold":
            continue
        p = {**(eintrag.get("prognose") or {}), **(anders.get(kennung) or {})}
        if not p:
            continue
        if not _ID.match(kennung):
            raise ValueError(f"Objekt {kennung!r}: die Kennung geht in SQL ein -- "
                             f"erlaubt sind Kleinbuchstaben, Ziffern und Strich.")
        aus.append(Prognose(
            id=kennung, name=str(eintrag.get("name") or kennung),
            miete_cents=int(p.get("miete_monatlich_cents") or 0),
            kosten_cents=int(p.get("kosten_monatlich_cents") or 0),
            ab=als_monat(p.get("ab")), bis=als_monat(p.get("bis")),
            saison=saison(p.get("saison"), f"{kennung}.prognose.saison")))
    return aus


def _prognosewert(feld: str, wert):
    if wert in (None, ""):
        return None
    if feld in ("ab", "bis"):
        if not re.fullmatch(r"\d{4}-\d{2}", str(wert)):
            raise ValueError(f"{feld}: Monat als JJJJ-MM")
        return str(wert)
    cents = int(wert)
    if cents < 0:
        raise ValueError(f"{feld}: ohne Vorzeichen eintragen")
    return cents


def prognose_setzen(kennung: str, felder: dict,
                    config_dir: Path | None = None) -> dict:
    """Die Prognose eines Objekts aus der Seite speichern.

    Nach `properties_custom.yaml`, Feld fuer Feld ueber die Basisdatei gelegt.
    Ein leeres Feld nimmt die Angabe aus dem Overlay heraus -- dann gilt
    wieder, was in properties.yaml steht.
    """
    config_dir = config_dir or CONFIG_DIR
    if kennung not in vorhandene(config_dir):
        raise ValueError(f"Kein Objekt „{kennung}“")
    basis = next((dict(p.get("prognose") or {}) for p
                  in (_yaml(config_dir / BASIS).get("properties") or [])
                  if str(p.get("id")) == kennung), {})
    eigene = dict(_yaml(config_dir / EIGEN).get("objekte") or {})
    eintrag = dict(eigene.get(kennung) or {})
    prognose = dict(eintrag.get("prognose") or {})
    for feld in PROGNOSE_FELDER:
        if feld not in felder:
            continue
        wert = _prognosewert(feld, felder[feld])
        # Nur was von der Basisdatei abweicht (nur-abweichungen-speichern):
        # sonst stuende ihr Wert als Kopie hier, und eine Korrektur dort
        # kaeme nicht mehr an.
        vorgabe = basis.get(feld)
        if feld in ("ab", "bis") and vorgabe:
            vorgabe = als_monat(vorgabe).strftime("%Y-%m")
        if wert is None or wert == vorgabe:
            prognose.pop(feld, None)
        else:
            prognose[feld] = wert
    if prognose:
        eintrag["prognose"] = prognose
    else:
        eintrag.pop("prognose", None)
    if eintrag:
        eigene[kennung] = eintrag
    else:
        eigene.pop(kennung, None)
    _speichern(config_dir, eigene)
    return prognose
