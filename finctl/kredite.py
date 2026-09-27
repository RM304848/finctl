"""Kredite anlegen und entfernen -- ohne YAML von Hand.

`config/loans.yaml` traegt vier Immobiliendarlehen mit Zinsbindung,
Anschlussfinanzierung und beobachteten Restschulden: dreissig Felder je
Eintrag. Das ist die Form, die diese vier Vertraege brauchen, und die Form,
die ein Ratenkredit nicht braucht.

ZWEI ARTEN, EIN SPEICHER. Ein RATENKREDIT ist mit neun Feldern beschrieben
-- Restschuld, Zins, Rate, Laufzeit --, und daraus baut `segments_from` den
Tilgungsplan, den die Prognose ohnehin rechnet. Ein IMMOBILIENDARLEHEN
bleibt vorerst Handarbeit in der Basisdatei: sein Modell in ein Formular zu
pressen machte die Seite unbrauchbar, und bearbeiten laesst es sich auf
/kredite schon.

ENTFERNEN IST DER WICHTIGERE TEIL. Wer dieses Werkzeug uebernimmt, erbt
sonst fremde Vertraege in seiner Prognose. `entfernt: true` blendet einen
Eintrag der Basisdatei aus, ohne sie anzufassen -- dieselbe Mechanik, mit
der `accounts_custom.yaml` ein Konto ausblendet.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

from finctl.pfade import CONFIG_DIR

BASIS = "loans.yaml"
EIGEN = "loans_custom.yaml"

KOPF = """# Auf der Seite /kredite angelegt und geaendert.
#
# `kredite:` sind die Eintraege dieser Seite, je Kennung. Sie ueberschreiben
# config/loans.yaml Feld fuer Feld; `entfernt: true` blendet einen Kredit der
# Basisdatei aus, ohne deren Begruendung zu loeschen.
#
# `vorlagen:` sind die Konditionen szenariogebundener Kredite.
#
# Betraege in Cent, Zinssatz in Prozent je Jahr.

"""

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def _yaml(pfad: Path) -> dict:
    if not pfad.exists():
        return {}
    return yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}


def _tag(wert) -> date | None:
    if isinstance(wert, date):
        return wert
    try:
        return date.fromisoformat(str(wert))
    except (TypeError, ValueError):
        return None


def ratenkredit(felder: dict) -> dict:
    """Aus neun Angaben ein Kredit, den `segments_from` rechnen kann.

    Ein Segment, fester Zins, feste Rate. Genau das ist ein Ratenkredit --
    und mehr anzubieten hiesse, nach Zahlen zu fragen, die im Vertrag nicht
    stehen.
    """
    beginn, ende = _tag(felder.get("start")), _tag(felder.get("ende"))
    return {
        "id": str(felder.get("id") or "").strip(),
        "name": str(felder.get("name") or "").strip(),
        "lender": str(felder.get("lender") or "").strip(),
        "servicing_account_id": felder.get("servicing_account_id") or None,
        "status": "active",
        "opening_balance_cents": felder.get("opening_balance_cents"),
        "opening_balance_as_of": beginn.isoformat() if beginn else None,
        "payment_cents": felder.get("annuity_cents"),
        "segments": [{
            "start": beginn.isoformat() if beginn else None,
            "end": ende.isoformat() if ende else None,
            "annual_rate_pct": felder.get("annual_rate_pct"),
            "annuity_cents": felder.get("annuity_cents"),
            "opening_balance_cents": felder.get("opening_balance_cents"),
            # Getippt, nicht aus einem Auszug gelesen -- dieselbe Kennzeichnung
            # wie bei den uebrigen Konditionen, die jemand von Hand eintraegt.
            "basis": "assumption",
        }],
    }


def _stammdaten_fehler(kredit: dict, vergeben: set[str]) -> list[str]:
    """Wer, wie er heisst und von welchem Konto die Rate abgeht."""
    fehler = []
    kennung = str(kredit.get("id") or "").strip()
    if not _ID.match(kennung):
        fehler.append("Kennung: klein, Ziffern und Bindestriche, "
                      "höchstens 41 Zeichen")
    elif kennung in vergeben:
        fehler.append(f"Kennung „{kennung}“ ist schon vergeben")
    if not str(kredit.get("name") or "").strip():
        fehler.append("Name fehlt")
    if not str(kredit.get("lender") or "").strip():
        fehler.append("Gläubiger fehlt")
    if not kredit.get("servicing_account_id"):
        fehler.append("Konto fehlt: ohne es weiß die Prognose nicht, "
                      "wo die Rate abgeht")
    return fehler


def _kondition_fehler(seg: dict) -> list[str]:
    """Restschuld, Rate, Zins und Laufzeit -- woraus der Plan gerechnet wird."""
    fehler = []
    rest, rate = seg.get("opening_balance_cents"), seg.get("annuity_cents")
    satz = seg.get("annual_rate_pct")
    if not isinstance(rest, int) or rest <= 0:
        fehler.append("Restschuld: ein Betrag größer null")
    if not isinstance(rate, int) or rate <= 0:
        fehler.append("Monatsrate: ein Betrag größer null")
    if not isinstance(satz, (int, float)) or not 0 <= satz < 100:
        fehler.append("Zinssatz: zwischen 0 und 100 Prozent")
    beginn, ende = _tag(seg.get("start")), _tag(seg.get("end"))
    if beginn is None:
        fehler.append("Beginn: ein Datum als JJJJ-MM-TT")
    if ende is None:
        fehler.append("Ende: ein Datum als JJJJ-MM-TT")
    elif beginn and ende <= beginn:
        fehler.append("Das Ende liegt vor dem Beginn")
    # Eine Rate unter dem Monatszins tilgt nie: der Kredit liefe unendlich,
    # und der Tilgungsplan zeigte eine wachsende Restschuld als Prognose.
    if not fehler and rate <= rest * satz / 100 / 12:
        fehler.append("Die Rate deckt nicht einmal den Monatszins — "
                      "so tilgt der Kredit nie")
    return fehler


def pruefen(kredit: dict, vergeben: set[str]) -> list[str]:
    """Was an diesem Kredit nicht stimmt, in Worten. Leer heisst: in Ordnung."""
    [seg] = kredit.get("segments") or [{}]
    return _stammdaten_fehler(kredit, vergeben) + _kondition_fehler(seg)


# ------------------------------------------------------------------ schreiben

def _speichern(config_dir: Path, eigene: dict) -> None:
    roh = _yaml(config_dir / EIGEN)
    roh["kredite"] = eigene
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / EIGEN).write_text(
        KOPF + yaml.safe_dump(roh, allow_unicode=True, sort_keys=True),
        encoding="utf-8")


def anlegen(felder: dict, config_dir: Path | None = None) -> dict:
    """Einen Ratenkredit anlegen. Die Basisdatei bleibt unangetastet."""
    from finctl.realestate.loan import lade_kredite

    config_dir = config_dir or CONFIG_DIR
    kredit = ratenkredit(felder)
    fehler = pruefen(kredit, {str(k.get("id")) for k in lade_kredite(config_dir)})
    if fehler:
        raise ValueError("; ".join(fehler))

    eigene = dict(_yaml(config_dir / EIGEN).get("kredite") or {})
    eigene[kredit["id"]] = {f: v for f, v in kredit.items()
                            if f != "id" and v is not None}
    _speichern(config_dir, eigene)
    return kredit


def entfernen(kennung: str, config_dir: Path | None = None) -> None:
    """Einen Kredit ausblenden.

    Wer hier selbst angelegt hat, bekommt den Eintrag geloescht. Wer einen
    Kredit der Basisdatei entfernt, bekommt `entfernt: true` -- die Datei
    traegt eine Begruendung, die niemand per Klick verlieren soll.
    """
    from finctl.realestate.loan import lade_kredite

    config_dir = config_dir or CONFIG_DIR
    if kennung not in {str(k.get("id")) for k in lade_kredite(config_dir)}:
        raise ValueError(f"Kein Kredit „{kennung}“")

    aus_basis = kennung in {str(k.get("id"))
                            for k in (_yaml(config_dir / BASIS).get("loans") or [])}
    eigene = dict(_yaml(config_dir / EIGEN).get("kredite") or {})
    if aus_basis:
        eigene[kennung] = {"entfernt": True}
    else:
        eigene.pop(kennung, None)
    _speichern(config_dir, eigene)


# ------------------------------------------------------------ Tilgungsplan

def tilgungsplan(loan_id: str) -> dict[str, tuple[int, int, str]]:
    """Monat -> (Zinsen, Tilgung, Grundlage) eines Kredits aus loans.yaml.

    Fuer die Aufteilung einer Rate, deren Bank Zins und Tilgung nicht
    druckt (finctl/rules/categorize.py). Gelesen wird die Basisdatei, wie
    die Aufteilung es immer tat.
    """
    from finctl.realestate.loan import amortise, opening_balance_cents, segments_from

    table: dict[str, tuple[int, int, str]] = {}
    pfad = CONFIG_DIR / BASIS
    spec = (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}) if pfad.exists() else {}
    for loan in spec.get("loans", []) or []:
        if str(loan.get("id")) != loan_id or not loan.get("segments"):
            continue
        sched = amortise(loan_id, opening_balance_cents(loan), segments_from(loan))
        for pay in sched.payments:
            table[pay.month.strftime("%Y-%m")] = (
                pay.interest_cents, pay.principal_cents, pay.basis)
    return table


def anmelden() -> None:
    """Den Tilgungsplan bei den Regeln anmelden -- aufgerufen von der App."""
    from finctl.rules import categorize as _cz

    _cz.tilgungsplan_anmelden(tilgungsplan)
