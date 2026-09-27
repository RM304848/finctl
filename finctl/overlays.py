"""Das Overlay-Muster an einer Stelle.

Zwei Dateien je Sache. Die eine ist von Hand geschrieben und traegt die
Begruendung -- warum ein Teil des Notgroschens jemand anderem gehoert, wie
eine Anfangsrestschuld rueckwaerts gerechnet wurde. Die andere schreibt das
Dashboard, und sie enthaelt nur Zahlen. Beim Lesen gewinnt die zweite; die
erste wird nie angefasst, sonst waere die Begruendung beim ersten Klick weg.

`balances.yaml` / `balances_custom.yaml`, `goals.yaml` / `ziele_custom.yaml`,
`taxonomy.yaml` / `taxonomy_custom.yaml`, `loans.yaml` / `loans_custom.yaml` --
viermal dasselbe, jedesmal neu geschrieben. Hier steht es einmal.

Drei Stellen hatten es NICHT und schrieben direkt in die Datenbank: die
Objektdaten (Kaufpreis, Eigenkapital, Verkaufspreis), die Kontountergrenzen
und die Gehaltsuntergrenze. Datenbank weg hiess: diese Zahlen weg, obwohl
jede YAML und jeder Kontoauszug noch dalag. Genau die Zahlen, die man nicht
aus einem Auszug zurueckgewinnen kann, weil sie nirgends gebucht sind.
"""

from __future__ import annotations

from typing import Any

from finctl.pfade import CONFIG_DIR


def lesen(datei: str, schluessel: str) -> dict[str, Any]:
    import yaml

    pfad = CONFIG_DIR / datei
    if not pfad.exists():
        return {}
    spec = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
    return spec.get(schluessel) or {}


def schreiben(datei: str, schluessel: str, daten: dict[str, Any],
              kopf: str) -> None:
    """Mit Kopf, damit die Datei erklaert, was sie ist.

    Wer sie in einem Jahr im Editor oeffnet, soll nicht raten muessen, warum
    dieselbe Zahl an zwei Stellen steht und welche gewinnt.
    """
    import yaml

    sauber = {k: v for k, v in daten.items() if v not in (None, {}, "")}
    (CONFIG_DIR / datei).write_text(
        kopf + yaml.safe_dump({schluessel: sauber}, allow_unicode=True,
                              sort_keys=True),
        encoding="utf-8")


EINSTELLUNGEN_KOPF = """# Im Dashboard gesetzte Stellschrauben.
#
# Ueberschreibt, was config/assumptions.yaml und config/forecast.yaml
# dokumentiert vorgeben. Die Basisdateien bleiben unangetastet, weil dort die
# Begruendung zu jeder Zahl steht -- und die Begruendung ist das, was am
# schnellsten verfaellt.
#
# `salary_floor_cents`   : die Untergrenze, mit der die Prognose rechnen darf
# `floor_cents:<konto>`  : das Mindestniveau eines einzelnen Kontos

"""

OBJEKTE_KOPF = """# Im Dashboard erfasste Objektdaten.
#
# Ueberschreibt config/properties.yaml je Objekt. Dort stehen die Fakten mit
# ihrer Herleitung -- AfA-Basis aus der Feststellung, Anschaffungsdatum aus
# dem Notarvertrag --, hier stehen die Zahlen, die in keiner Unterlage stehen
# und die nur der Eigentuemer kennt: was er gezahlt hat, was er einsetzt, was
# er beim Verkauf erwartet.
#
# Ohne diese Datei laegen sie ausschliesslich in der Datenbank. Die laesst
# sich aus Auszuegen wiederherstellen -- diese Zahlen nicht, denn sie sind
# nirgends gebucht.

"""


def einstellungen() -> dict[str, Any]:
    return lesen("settings_custom.yaml", "einstellungen")


def einstellung_setzen(key: str, value: Any) -> None:
    daten = einstellungen()
    if value in (None, ""):
        daten.pop(key, None)
    else:
        daten[key] = value
    schreiben("settings_custom.yaml", "einstellungen", daten, EINSTELLUNGEN_KOPF)


def objekte() -> dict[str, dict]:
    return lesen("properties_custom.yaml", "objekte")


def objekt_setzen(property_id: str, felder: dict[str, Any]) -> None:
    daten = objekte()
    eintrag = dict(daten.get(property_id) or {})
    for k, v in felder.items():
        if v in (None, ""):
            eintrag.pop(k, None)
        else:
            eintrag[k] = v
    if eintrag:
        daten[property_id] = eintrag
    else:
        daten.pop(property_id, None)
    schreiben("properties_custom.yaml", "objekte", daten, OBJEKTE_KOPF)


def anwenden(conn) -> None:
    """Die Overlays in die Datenbank spiegeln.

    Beim `init` aufgerufen, damit eine frisch gebaute Datenbank denselben
    Stand hat wie die verlorene. Die Datenbank bleibt damit das, was sie sein
    soll: eine ableitbare Sicht, keine Ablage.
    """
    from finctl.ledger.db import now_iso

    for key, value in einstellungen().items():
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (str(key), str(value), now_iso()))
    erlaubt = {"purchase_price_cents", "incidental_costs_cents", "equity_cents",
               "land_share_pct", "afa_rate_pct", "afa_start", "acquired_on",
               "planned_sale_on", "sale_price_cents", "sold_on"}
    for pid, felder in objekte().items():
        setzbar = {k: v for k, v in felder.items() if k in erlaubt}
        if not setzbar:
            continue
        zuweisung = ", ".join(f"{k} = ?" for k in setzbar)
        conn.execute(f"UPDATE properties SET {zuweisung} WHERE id = ?",
                     (*setzbar.values(), pid))
    conn.commit()


SICHERUNG_KOPF = """# Im Dashboard gesetztes Backup-Ziel.
#
# Ueberschreibt config/backup.yaml. Die Basisdatei bleibt unangetastet, weil
# dort steht, WARUM nur zwoelf Staende aufbewahrt werden und was das Archiv
# enthaelt -- und weil sie auf einer frischen Installation die Vorlage ist.
#
# `directory` : wohin gesichert wird. Cloud-Ordner, externe Platte, USB-Stick.
# `keep`      : wie viele Staende bleiben. Aeltere werden beim Schreiben
#               geloescht.
"""


def sicherung() -> dict[str, Any]:
    return lesen("backup_custom.yaml", "backup")


def sicherung_setzen(felder: dict[str, Any]) -> None:
    daten = sicherung()
    for k, v in felder.items():
        if v in (None, ""):
            daten.pop(k, None)
        else:
            daten[k] = v
    schreiben("backup_custom.yaml", "backup", daten, SICHERUNG_KOPF)
