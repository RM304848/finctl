"""Das Kontenregister -- gelesen, geprueft und geschrieben.

`config/accounts.yaml` war die einzige Stammdatei, die es nur im Texteditor
gab. Wer die App neu aufsetzt, kommt ohne Konto aber keinen Schritt weit: kein
Konto, kein Import, kein Hauptbuch, keine Tiefpunktwarnung. Ein Werkzeug, das
vor dem ersten nuetzlichen Bildschirm eine YAML-Datei von Hand verlangt, hat
an genau dieser Stelle seine Nutzer verloren.

ZWEI DATEIEN, wie ueberall: `accounts.yaml` ist von Hand geschrieben und
traegt die Begruendung -- warum PayPal ein Konto ist und kein Haendler, warum
ein Konto nicht geparst wird. `accounts_custom.yaml` schreibt die Seite, je
Kennung, und gewinnt Feld fuer Feld. Die erste wird nie angefasst; ein
Dashboard, das sie neu schriebe, loeschte die Begruendung beim ersten Klick.

DER UPSERT STEHT HIER, NICHT IN `cli.init`. Beide Wege -- die Seite /konten
und `finctl init` -- schreiben dasselbe Konto in dieselbe Tabelle. Zweimal
geschrieben hiesse: der Editor kennt ein Feld, das `init` beim naechsten Lauf
wieder ueberbuegelt.

WO DIE AUSZUEGE LIEGEN, ist eine Entscheidung mit zwei Seiten. Ein
ORDNERNAME (`sparkasse`) liegt unter `data/statements/` -- im Datenordner,
also im Backup und beim Umzug dabei. Ein VOLLER PFAD (`/Users/…/Downloads`)
ist bequemer, wird aber nicht mitgesichert und zeigt auf einem anderen
Rechner ins Leere. Deshalb ist der Ordnername der Normalfall und der volle
Pfad die Ausnahme, die die Seite benennt.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import yaml

from finctl.pfade import CONFIG_DIR, STATEMENTS_DIR

BASIS = "accounts.yaml"
EIGEN = "accounts_custom.yaml"

KOPF = """# Auf der Seite /konten angelegt und geaendert.
#
# Ueberschreibt config/accounts.yaml Feld fuer Feld, je Kennung. Dort steht,
# WARUM ein Konto so gefuehrt wird; hier steht, was im Frontend eingetragen
# wurde. `entfernt: true` blendet einen Eintrag der Basisdatei aus, ohne
# deren Begruendung zu loeschen.
#
# statement_folder: ein Ordnername liegt unter data/statements/ und ist damit
# im Backup. Ein voller Pfad wird NICHT mitgesichert.

"""

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")

#: Was ein Konto ist. `loan` ist ein Kredit, der als Konto gefuehrt wird.
ARTEN = ("giro", "tagesgeld", "depot", "broker", "credit_card", "loan")

#: `parsed` liest Auszuege Buchung fuer Buchung. `summary` tut das nicht --
#: so ein Konto wird nur ueber die Umbuchungen sichtbar, die es von einem
#: geparsten Konto erreichen. Ohne Parserprofil bleibt nur `summary`.
MODI = ("parsed", "summary")

#: Was ein Registereintrag tragen darf. Alles andere faellt beim Speichern weg.
FELDER = ("id", "display_name", "institution", "account_type", "iban",
          "account_no", "currency", "ingest_mode", "parser_profile",
          "statement_folder", "dispo_threshold_cents", "active", "closed_on")


def profile() -> list[str]:
    """Die Parserprofile, die es gibt -- aus dem Ordner, nicht aus einer Liste.

    Eine gepflegte Liste waere die zweite Stelle, an der ein neues Profil
    eingetragen werden muss, und die, die man vergisst.
    """
    import importlib
    import pkgutil

    from finctl.ingest import profiles

    namen = []
    # Ueber das Paket, nicht ueber `*.py` im Ordner: in der App zum
    # Doppelklicken liegen die Module in einem Archiv, und ein Ordner voller
    # .py-Dateien existiert dort nicht -- die Auswahl der Profile war leer.
    for info in pkgutil.iter_modules(profiles.__path__):
        if info.name.startswith("_"):
            continue
        # Eine Sammlung beschriebener Exporte (banken_csv) bietet ihre
        # Eintraege an, nicht sich selbst.
        modul = importlib.import_module(f"finctl.ingest.profiles.{info.name}")
        namen += list(getattr(modul, "PARSERS", {})) or [info.name]
    # Und die selbst beschriebenen Exporte aus config/.
    from finctl.ingest import zuordnung

    return sorted(namen) + sorted(zuordnung.eigene())


# ------------------------------------------------------------------ lesen

def _yaml(pfad: Path) -> dict:
    if not pfad.exists():
        return {}
    return yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}


def laden(config_dir: Path | None = None) -> list[dict]:
    """Beide Dateien, zusammengefuehrt -- die eigene gewinnt Feld fuer Feld."""
    config_dir = config_dir or CONFIG_DIR
    grund: dict[str, dict] = {}
    for eintrag in _yaml(config_dir / BASIS).get("accounts") or []:
        grund[str(eintrag["id"])] = dict(eintrag)
    for kennung, felder in (_yaml(config_dir / EIGEN).get("accounts") or {}).items():
        if (felder or {}).get("entfernt"):
            grund.pop(str(kennung), None)
            continue
        vorher = grund.get(str(kennung)) or {"id": str(kennung)}
        grund[str(kennung)] = {**vorher, **(felder or {}), "id": str(kennung)}
    return list(grund.values())


def auszugsordner(konto: dict, daten: Path | None = None) -> Path:
    """Wohin die Auszuege dieses Kontos gehoeren.

    Ein Ordnername wird unter `data/statements/` gelegt, ein voller Pfad
    genommen, wie er ist.
    """
    angabe = str(konto.get("statement_folder") or konto["id"]).strip()
    p = Path(angabe).expanduser()
    return p if p.is_absolute() else (daten or STATEMENTS_DIR) / p


def eigener_pfad(konto: dict) -> bool:
    """Liegt der Ordner ausserhalb des Datenordners -- also ohne Backup?"""
    angabe = str(konto.get("statement_folder") or "").strip()
    return bool(angabe) and Path(angabe).expanduser().is_absolute()


# ------------------------------------------------------------------ pruefen

def pruefen(konto: dict, vergeben: set[str]) -> list[str]:
    """Was an diesem Konto nicht stimmt, in Worten. Leer heisst: in Ordnung."""
    fehler = []
    kennung = str(konto.get("id") or "").strip()
    if not _ID.match(kennung):
        fehler.append("Kennung: klein, Ziffern und Bindestriche, "
                      "höchstens 41 Zeichen")
    elif kennung in vergeben:
        fehler.append(f"Kennung „{kennung}“ ist schon vergeben")
    if not str(konto.get("display_name") or "").strip():
        fehler.append("Anzeigename fehlt")
    if not str(konto.get("institution") or "").strip():
        fehler.append("Institut fehlt")
    if konto.get("account_type") not in ARTEN:
        fehler.append(f"Art: eine von {', '.join(ARTEN)}")
    if konto.get("ingest_mode") not in MODI:
        fehler.append(f"Einlesen: {' oder '.join(MODI)}")
    elif konto["ingest_mode"] == "parsed" and konto.get("parser_profile") not in profile():
        # Ohne Profil weiss der Import nicht, wie er die Datei lesen soll --
        # das Konto saehe eingerichtet aus und bliebe beim Import stumm.
        fehler.append("Ein geparstes Konto braucht ein Parserprofil")
    schwelle = konto.get("dispo_threshold_cents")
    if schwelle is not None and (not isinstance(schwelle, int) or schwelle < 0):
        fehler.append("Schwelle für die Warnung: nicht negativ")
    return fehler


# ------------------------------------------------------------------ schreiben

_SQL = """
    INSERT INTO accounts
        (id, display_name, institution, account_type, iban, account_no,
         currency, ingest_mode, parser_profile, statement_folder,
         dispo_threshold_cents, active, closed_on)
    VALUES
        (:id, :display_name, :institution, :account_type, :iban, :account_no,
         :currency, :ingest_mode, :parser_profile, :statement_folder,
         :dispo_threshold_cents, :active, :closed_on)
    ON CONFLICT(id) DO UPDATE SET
        display_name = excluded.display_name,
        institution  = excluded.institution,
        account_type = excluded.account_type,
        iban         = excluded.iban,
        account_no   = excluded.account_no,
        ingest_mode  = excluded.ingest_mode,
        parser_profile = excluded.parser_profile,
        statement_folder = excluded.statement_folder,
        dispo_threshold_cents = excluded.dispo_threshold_cents,
        active       = excluded.active,
        closed_on    = excluded.closed_on
"""


def als_zeile(konto: dict) -> dict:
    """Ein Registereintrag als Zeile der Tabelle `accounts`."""
    return {
        "id": konto["id"],
        "display_name": konto["display_name"],
        "institution": konto["institution"],
        "account_type": konto["account_type"],
        "iban": konto.get("iban"),
        "account_no": konto.get("account_no"),
        "currency": konto.get("currency", "EUR"),
        "ingest_mode": konto["ingest_mode"],
        "parser_profile": konto.get("parser_profile"),
        "statement_folder": konto.get("statement_folder"),
        "dispo_threshold_cents": konto.get("dispo_threshold_cents"),
        # Geschlossen bleibt in der Historie, faellt aber aus jeder Auswahl.
        # Vorher setzte init jedes Konto auf aktiv, und laengst gekuendigte
        # Konten standen Jahre spaeter noch in der Kontenliste.
        "active": 0 if konto.get("active") is False or konto.get("closed_on") else 1,
        "closed_on": str(konto["closed_on"]) if konto.get("closed_on") else None,
    }


def in_db(conn: sqlite3.Connection, konto: dict) -> int:
    """Ein Konto in die Tabelle schreiben oder auffrischen."""
    return conn.execute(_SQL, als_zeile(konto)).rowcount or 0


def anlegen(conn: sqlite3.Connection, konto: dict,
            config_dir: Path | None = None,
            statements: Path | None = None) -> dict:
    """Ein neues Konto: erst in die Datei, dann in die Tabelle.

    In dieser Reihenfolge, weil die Datei die Handentscheidung traegt und die
    Tabelle daraus jederzeit neu gebaut werden kann -- umgekehrt nicht.
    """
    config_dir = config_dir or CONFIG_DIR
    fehler = pruefen(konto, {str(k["id"]) for k in laden(config_dir)})
    if fehler:
        raise ValueError("; ".join(fehler))

    schlank = {f: konto[f] for f in FELDER if konto.get(f) not in (None, "")}
    eigen = _yaml(config_dir / EIGEN).get("accounts") or {}
    eigen[schlank["id"]] = {f: v for f, v in schlank.items() if f != "id"}
    pfad = config_dir / EIGEN
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(
        KOPF + yaml.safe_dump({"accounts": eigen}, allow_unicode=True,
                              sort_keys=True), encoding="utf-8")

    in_db(conn, schlank)
    conn.commit()
    # Der Ordner wird gleich angelegt: ein Konto, dessen Ablage erst beim
    # ersten Import entsteht, laesst den Nutzer raten, wohin die Datei soll.
    auszugsordner(schlank, statements).mkdir(parents=True, exist_ok=True)
    return schlank
