"""The monthly routine, callable from anywhere.

Importing statements, re-applying the rulebook and writing a backup were each
implemented inside a Typer command, which meant the dashboard could only have
them by shelling out to the CLI or by keeping a second copy. Both age badly:
the copy drifts, and a subprocess turns a clear exception into an exit code
and a wall of text.

So the work lives here and the CLI and the web API are two thin callers. No
command in this module calls a language model, and none of them touches the
network -- that is asserted by a test, not merely intended.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from finctl import pfade as _p

# Die Kontoprognose steht in finctl/forecast/konten.py.
from finctl.forecast.konten import (  # noqa: F401 -- weitergereicht, siehe dort
    _FREQ,
    HORIZON_DEFAULT,
    HORIZON_LONG,
    _anzeigenamen,
    _as_month,
    _erster_offener_monat,
    _floor_for,
    _herkunft_konto,
    _herkunft_planzeile,
    _income_overrides,
    _operating_account,
    _planposten,
    _rate_floor,
    _sweep_ceiling,
    abgeleitete_posten,
    account_forecast,
    date_vor,
    forecast_config,
    household_accounts,
)
from finctl.forecast.stichtag import saldo_zum as _saldo_zum  # noqa: F401 -- alter Name
from finctl.forecast.stichtag import stichtag as _stichtag  # noqa: F401 -- alter Name
from finctl.ledger import db as ledger
from finctl.pfade import CONFIG_DIR

DATA_DIR = _p.DATA_DIR
DB_PATH = DATA_DIR / "finance.db"

#: Was in ein Archiv gehoert, und unter welchem Namen.
ARCHIVTEILE = ("finance.sql", "requirements.lock", "stand.yaml", "config")


def ingest_all(conn: sqlite3.Connection, *, account: str | None = None,
               force: bool = False) -> list[dict]:
    """Import every statement under data/statements/<account>/.

    One broken file must not abort the batch: a statement that fails to parse
    is recorded as an error and the rest still import, because the alternative
    is that a single bad PDF blocks the whole month.
    """
    from finctl.ingest import importer

    results: list[dict] = []
    accounts = conn.execute(
        "SELECT id, statement_folder FROM accounts "
        "WHERE ingest_mode='parsed' AND active=1"
        + (" AND id = ?" if account else ""),
        (account,) if account else (),
    ).fetchall()

    for row in accounts:
        folder = DATA_DIR / "statements" / (row["statement_folder"] or row["id"])
        if not folder.is_dir():
            continue
        # Auch CSV: PayPal liefert kein PDF. Gross- und Kleinschreibung
        # beidseitig, weil PayPals Export .CSV heisst und macOS den Unterschied
        # nicht meldet.
        dateien = sorted({d for muster in ("*.pdf", "*.PDF", "*.csv", "*.CSV")
                          for d in folder.glob(muster)})
        for pdf in dateien:
            try:
                outcome = importer.import_statement(conn, pdf, row["id"], force=force)
            except Exception as exc:
                results.append({
                    "file": pdf.name, "account": row["id"], "status": "error",
                    "message": f"{type(exc).__name__}: {exc}",
                })
                continue
            results.append({
                "file": pdf.name,
                "account": outcome.account_id,
                "status": outcome.status,
                "profile": outcome.profile,
                "transactions": outcome.transactions,
                "period": list(outcome.period) if outcome.period else None,
                "delta_cents": outcome.reconciliation.delta_cents
                               if outcome.reconciliation else None,
                "message": outcome.message,
            })
    return results


def ingest_summary(results: list[dict]) -> dict:
    return {
        "results": results,
        "imported": sum(1 for r in results if r["status"] == "imported"),
        "rejected": sum(1 for r in results if r["status"] in ("rejected", "error")),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
    }


def update(conn: sqlite3.Connection) -> dict:
    """The whole monthly routine in one call: import, categorize, group, verify.

    Verification is not optional here. Importing and categorizing without
    checking leaves you believing a number that a rejected statement or a gap
    in the chain has already invalidated, and the point of the reconciliation
    gate is that you find out immediately rather than at tax time.

    The taxonomy is loaded FIRST, and that ordering is not cosmetic. A rule may
    name a category that config/taxonomy.yaml has but the database has not seen
    yet; categorizing before the load then dies on a foreign key, and the whole
    update fails at a point where nothing explains why. Loading is idempotent,
    so paying for it on every run costs a few milliseconds and removes the one
    step you would otherwise have to remember by hand.
    """
    from finctl.ledger import gruppen as gr
    from finctl.rules import categorize as cz
    from finctl.tax import taxonomy as tx

    loaded = tx.load_into_db(conn)
    ingest = ingest_summary(ingest_all(conn))
    result = cz.categorize(conn)
    # Vorgaenge (config/groups.yaml) haengen an den Kategorien der Splits und
    # kommen deshalb erst nach categorize. Ohne diesen Schritt verlor ein
    # Neuaufbau jede Zuordnung, bis jemand `finctl group apply` von Hand rief.
    vorgaenge = gr.apply(conn)

    problems = {
        "split_imbalances": [dict(r) for r in ledger.split_imbalances(conn)],
        "orphaned_splits": [dict(r) for r in ledger.orphaned_splits(conn)],
        "statement_gaps": ledger.statement_chain_gaps(conn),
        "unrecorded_manual": cz.unrecorded_manual_splits(conn),
        "rejected_statements": [dict(r) for r in conn.execute(
            "SELECT source_name, account_id, reconcile_delta_cents "
            "FROM statements WHERE status='rejected'")],
    }
    return {
        "taxonomy": loaded,
        "ingest": ingest,
        "categorize": {
            "matched": result.matched,
            "unmatched": result.unmatched,
            "flagged": result.flagged,
            "replayed": result.replayed,
            "manual_preserved": result.manual_preserved,
        },
        "vorgaenge": vorgaenge,
        "problems": problems,
        "ok": not any(problems.values()),
    }


# ------------------------------------------------------------------ backup

#: Wie viele Staende aufbewahrt werden, wenn backup.yaml nichts sagt.
STAENDE = 12


class KeinBackupZielError(RuntimeError):
    """Es ist nicht eingetragen, wohin gesichert werden soll."""


def backup_settings() -> tuple[Path, int]:
    """Where backups go and how many to keep, from config/backup.yaml.

    In config rather than in code: a folder that changes when the cloud drive
    moves or the machine is replaced is not a reason to edit Python.

    KEINE VORGABE IM CODE. Hier stand ein fester Pfad in die eigene Cloud --
    `~/Library/CloudStorage/<Cloud>/...`. Auf einem anderen Rechner
    ist das bestenfalls ein Ordner, den niemand kennt, und auf Windows ein
    sinnloser `C:\\Users\\...\\Library\\CloudStorage\\...`. Wohin gesichert
    wird, ist eine Frage an den Nutzer, und sie gehoert in die Einrichtung.

    Fehlt die Angabe, bricht es hier ab. Eine geratene Vorgabe waere
    schlimmer: die Sicherung liefe scheinbar, und im Ernstfall stuende man
    vor einem Ordner, den man nie gesehen hat.
    """
    import yaml

    from finctl import overlays

    path = CONFIG_DIR / "backup.yaml"
    spec = {}
    if path.exists():
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Was im Dashboard gesetzt wurde, gewinnt -- aber die Basisdatei behaelt
    # ihre Kommentare, statt beim ersten Klick ueberschrieben zu werden.
    spec = {**spec, **overlays.sicherung()}
    directory = spec.get("directory")
    if not directory:
        raise KeinBackupZielError(
            f"In {path} steht kein `directory`. Trag dort ein, wohin "
            f"gesichert werden soll -- ein Cloud-Ordner, eine externe Platte, "
            f"ein USB-Stick. Ohne Ziel wuerde die Sicherung irgendwohin "
            f"laufen, und im Ernstfall faende sie niemand wieder.")
    return Path(directory).expanduser(), int(spec.get("keep", STAENDE))


def restore(archive: Path | str, to: Path | str) -> dict:
    """Ein Archiv auspacken und die Datenbank daraus bauen.

    Der Weg, den man im Ernstfall geht -- als Befehl, damit er im Ernstfall
    nicht erfunden werden muss. Legt NICHTS an einer bestehenden Installation
    an: das Ziel muss leer oder neu sein, sonst waere der erste Versuch einer
    Wiederherstellung der Moment, in dem man den letzten guten Stand
    ueberschreibt.

    Berichtet am Ende, was da ist und was fehlt. Ein Archiv ohne Auszuege ist
    richtig so -- die liegen in OneDrive --, aber wer das nicht weiss, sucht.
    """
    ziel = Path(to).expanduser()
    quelle = Path(archive).expanduser()
    if not quelle.exists():
        raise FileNotFoundError(f"{quelle} gibt es nicht")
    if ziel.exists() and any(ziel.iterdir()):
        raise FileExistsError(
            f"{ziel} ist nicht leer. Eine Wiederherstellung schreibt nie in "
            f"eine bestehende Installation -- sonst ist der erste Versuch der "
            f"Moment, in dem der letzte gute Stand verloren geht.")
    ziel.mkdir(parents=True, exist_ok=True)

    with tarfile.open(quelle, "r:gz") as tar:
        # Kein Mitglied darf aus dem Zielverzeichnis herausgreifen.
        for member in tar.getmembers():
            pfad = (ziel / member.name).resolve()
            if not str(pfad).startswith(str(ziel.resolve())):
                raise ValueError(f"Archiv enthaelt einen Pfad ausserhalb: {member.name}")
        tar.extractall(ziel, filter="data")

    dump = ziel / "finance.sql"
    zeilen = 0
    if dump.exists():
        db = ziel / "data" / "finance.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        try:
            conn.executescript(dump.read_text(encoding="utf-8"))
            conn.commit()
            zeilen = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        finally:
            conn.close()

    vorhanden = {name: (ziel / name).exists() for name in ARCHIVTEILE}
    return {"ziel": str(ziel), "transaktionen": zeilen,
            "vorhanden": vorhanden,
            "fehlend": sorted(k for k, v in vorhanden.items() if not v),
            "werkzeug": _werkzeug_aus(ziel / "stand.yaml"),
            "hinweis": f"Das ist ein Datenordner, keine Installation. Das "
                       f"Programm kommt aus der Installation; dieser Ordner "
                       f"wird ihm mit `{_p.aus_umgebung('finctl')} ort "
                       f"--setzen {ziel}` genannt. Kontoauszuege sind nicht "
                       f"im Archiv -- sie liegen im Sicherungsordner und "
                       f"werden fuer den Betrieb nicht gebraucht."}


_STEMPEL = re.compile(r"finance-os_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}\.tar\.gz$")


def _stempel(name: str) -> str | None:
    """"2026-09-27 21.31" aus dem Namen eines Archivs -- mit Punkt, weil
    Windows keinen Doppelpunkt in Ordnernamen erlaubt."""
    treffer = _STEMPEL.match(name)
    if not treffer:
        return None
    j, m, t, h, mi = treffer.groups()
    return f"{j}-{m}-{t} {h}.{mi}"


def sicherungen() -> list[dict]:
    """Die Staende im eingetragenen Sicherungsordner, der neueste zuerst.

    Leer ohne Ziel oder ohne Ordner: auf einem neuen Rechner ist beides der
    Normalfall, und dort kommt die Sicherung als hochgeladene Datei.
    """
    try:
        ziel, _ = backup_settings()
    except KeinBackupZielError:
        return []
    if not ziel.is_dir():
        return []
    return [{"name": a.name, "bytes": a.stat().st_size,
             "am": (_stempel(a.name) or "").replace(".", ":")}
            for a in sorted(ziel.glob("finance-os_*.tar.gz"), reverse=True)]


def wiederherstellen_neben(archiv: Path | str, name: str | None = None) -> dict:
    """Eine Sicherung in einen NEUEN Ordner neben dem jetzigen Datenordner.

    Aus der Einrichtung heraus, fuer alle ohne Terminal. Drei Dinge sind
    anders als bei `restore` allein:

    * Der Ordner wird gewaehlt, nicht erfragt: "<Datenordner> (Sicherung
      2026-09-27 21.31)" -- sichtbar neben dem alten, und am Namen erkennbar.
    * Das Hauptbuch wird danach geprueft (Splits gehen auf, keine Waisen).
      Eine Sicherung, die sich einlesen laesst, aber nicht stimmt, soll das
      sagen, bevor jemand auf sie umschaltet.
    * Umgeschaltet wird NICHT. Das tut erst "Diesen Stand verwenden", und es
      gilt ab dem naechsten Start. Bis dahin arbeitet die App unveraendert
      weiter, und der alte Ordner bleibt, wie er war.
    """
    stempel = _stempel(name or Path(archiv).name) or datetime.now().strftime("%Y-%m-%d %H.%M")
    jetzt = _p.DATEN.resolve()
    ziel = jetzt.parent / f"{jetzt.name} (Sicherung {stempel})"
    nummer = 2
    while ziel.exists():
        ziel = jetzt.parent / f"{jetzt.name} (Sicherung {stempel}) {nummer}"
        nummer += 1

    bericht = restore(archiv, ziel)
    db = ziel / "data" / "finance.db"
    unstimmig = 0
    if db.exists():
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            unstimmig = len(ledger.split_imbalances(conn)) + len(ledger.orphaned_splits(conn))
        finally:
            conn.close()
    return {**bericht, "stimmig": db.exists() and not unstimmig, "unstimmig": unstimmig}


def _werkzeug_aus(stand: Path) -> str | None:
    """Mit welcher Version das Archiv geschrieben wurde.

    Ohne diese Angabe waere die haeufigste Frage nach einer Wiederherstellung
    -- passt das Programm zu diesen Daten? -- nur durch Ausprobieren zu
    beantworten.
    """
    if not stand.exists():
        return None
    for zeile in stand.read_text(encoding="utf-8").splitlines():
        if zeile.startswith("werkzeug:"):
            return zeile.split(":", 1)[1].strip()
    return None


def _abhaengigkeiten() -> list[str]:
    """Die Namen, von denen dieses Werkzeug abhaengt.

    Aus den Metadaten der Installation und nicht aus `pyproject.toml`: eine
    installierte App hat die Datei nicht neben sich liegen, und dann trug das
    Archiv ausgerechnet dann keine Versionen, wenn es am noetigsten war --
    naemlich auf einem Rechner ohne Quellcode.
    """
    from importlib import metadata

    try:
        roh = metadata.requires("finctl") or []
    except metadata.PackageNotFoundError:
        roh = []
    return sorted({re.split(r"[<>=!\[; ]", eintrag, maxsplit=1)[0]
                   for eintrag in roh})


def _versionen_einfrieren() -> str:
    """Die Versionen, mit denen dieses Backup entstanden ist.

    Gelesen aus der laufenden Umgebung und nicht aus den Untergrenzen in
    pyproject.toml: hier steht, was nachweislich funktioniert hat.
    """
    from importlib import metadata

    namen = _abhaengigkeiten()
    zeilen = [
        "# Die Versionen, mit denen dieses Backup entstanden ist.",
        "#",
        "# Die Abhaengigkeiten des Werkzeugs nennen nur Untergrenzen. Wer",
        "# dieses Archiv in fuenf Jahren auspackt, soll nicht raten muessen,",
        "# mit welchem FastAPI es lief -- Hauptversionen brechen, und dann",
        "# stuende man mit vollstaendigen Daten und einem Programm da, das",
        "# nicht startet.",
        f"# Python {sys.version.split()[0]} auf {sys.platform}",
        "",
    ]
    if not namen:
        zeilen.append("# Beim Sichern war finctl nicht als Paket installiert.")
    for name in namen:
        try:
            zeilen.append(f"{name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            zeilen.append(f"# {name}: nicht installiert")
    return "\n".join(zeilen) + "\n"


def _stand() -> str:
    """Das Blatt, das dem Archiv beiliegt.

    Ein Archiv, das nur Daten enthaelt, sieht fuer den, der es in Jahren
    auspackt, aus wie ein unvollstaendiges. Es ist keins -- aber das muss
    darinstehen, nicht in einem Handbuch, das er nicht hat.
    """
    from importlib import metadata

    try:
        version = metadata.version("finctl")
    except metadata.PackageNotFoundError:
        version = "unbekannt"

    return (
        "# Was in diesem Archiv steckt -- und was nicht.\n"
        "#\n"
        "# ENTHALTEN ist der Datenordner: alle Handentscheidungen als YAML\n"
        "# unter config/, dazu das Hauptbuch als SQL-Dump. Das ist alles,\n"
        "# was nur hier existiert und sich nirgends wiederbeschaffen laesst.\n"
        "#\n"
        "# NICHT ENTHALTEN ist das Programm. Es kommt aus der Installation.\n"
        "# Ein Archiv, das seinen eigenen Quelltext mitschleppt, sichert bei\n"
        "# jedem Lauf dieselben Dateien und verwechselt zwei Dinge, die\n"
        "# getrennt gehoeren: was du entschieden hast, und womit du es\n"
        "# aufgeschrieben hast. Die Versionen stehen in requirements.lock.\n"
        "#\n"
        "# NICHT ENTHALTEN sind die Kontoauszuege. Sie liegen neben diesem\n"
        "# Archiv im Sicherungsordner und werden fuer den Betrieb nicht\n"
        "# gebraucht -- aus config/ und den Auszuegen allein entsteht\n"
        "# derselbe Stand noch einmal.\n"
        "#\n"
        "# So kommst du zurueck:\n"
        f"#   {_p.aus_umgebung('finctl')} restore <dieses archiv> --to <ordner>\n"
        f"#   {_p.aus_umgebung('finctl')} ort --setzen <ordner>\n"
        f"#   {_p.aus_umgebung('finctl')} validate\n"
        "\n"
        f"werkzeug: {version}\n"
        f"python: {sys.version.split()[0]}\n"
        f"system: {sys.platform}\n"
        f"erstellt: {datetime.now().isoformat(timespec='seconds')}\n"
    )


def write_backup(to: Path | None = None, *, keep: int | None = None) -> dict:
    """Den Datenordner sichern -- und nur den.

    Drin: das Hauptbuch als SQL-Dump, die komplette `config/`, die Versionen,
    mit denen es lief, und ein Blatt, das beides erklaert.

    NICHT DRIN IST DER CODE. Bis zum 23.09.2026 packte jedes Archiv `finctl/`
    und `tests/` mit, also bei jedem Lauf dieselben Dateien, die ohnehin in
    der Versionsverwaltung liegen. Das verwechselt zwei Dinge, die getrennt
    gehoeren: was der Nutzer entschieden hat, und womit er es aufgeschrieben
    hat. Wiederherstellen heisst jetzt: App installieren, Archiv einlesen.

    Auch nicht drin sind die Kontoauszuege -- sie liegen im selben
    Sicherungsordner und wuerden das Archiv vervielfachen. Das ist deshalb
    ungefaehrlich, weil geprueft ist, dass `config/` plus die Auszuege den
    Stand noch einmal erzeugen; der SQL-Dump ist nur die Abkuerzung.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError("no database to back up")
    # `--to` soll auch dann gehen, wenn noch kein Ziel eingetragen ist: das
    # ist der erste Sicherungslauf, bevor die Einrichtung durch ist.
    if to is not None:
        target = Path(to).expanduser()
        keep = STAENDE if keep is None else keep
    else:
        target, konfigurierte = backup_settings()
        keep = konfigurierte if keep is None else keep
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = target / f"finance-os_{stamp}.tar.gz"

    def ohne_ballast(ti):
        """Was in ein Archiv nicht gehoert.

        `__pycache__` ist Bytecode einer bestimmten Python-Version und auf
        dem Zielrechner bestenfalls nutzlos. `.DS_Store` ist Finder-Krimskrams
        und war bisher drin -- drei Dateien, kein Schaden, aber ein Archiv
        soll nur enthalten, was es wiederherstellt.
        """
        name = ti.name.rsplit("/", 1)[-1]
        return None if "__pycache__" in ti.name or name == ".DS_Store" else ti

    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "finance.sql"
        conn = ledger.connect(DB_PATH)
        try:
            with dump.open("w", encoding="utf-8") as fh:
                for line in conn.iterdump():
                    fh.write(f"{line}\n")
        finally:
            conn.close()

        # Was TATSAECHLICH lief, nicht was pyproject.toml erlaubt. Die
        # Abhaengigkeiten dort haben offene Untergrenzen (`fastapi>=0.115`);
        # ein Restore in fuenf Jahren zieht die Versionen von dann, und
        # Hauptversionen brechen. Dann stuende man mit vollstaendigen Daten
        # und einem Programm da, das nicht startet.
        lock = Path(tmp) / "requirements.lock"
        lock.write_text(_versionen_einfrieren(), encoding="utf-8")

        stand = Path(tmp) / "stand.yaml"
        stand.write_text(_stand(), encoding="utf-8")

        with tarfile.open(archive, "w:gz") as tar:
            tar.add(dump, arcname="finance.sql")
            tar.add(lock, arcname="requirements.lock")
            tar.add(stand, arcname="stand.yaml")
            if CONFIG_DIR.exists():
                tar.add(CONFIG_DIR, arcname="config", filter=ohne_ballast)

    # Old snapshots are pruned rather than left to accumulate. A OneDrive
    # folder with two hundred archives is a folder nobody looks in, and the
    # value of a backup is entirely in whether the newest one is findable.
    pruned = []
    existing = sorted(target.glob("finance-os_*.tar.gz"), reverse=True)
    for old in existing[keep:]:
        old.unlink()
        pruned.append(old.name)

    return {"archive": str(archive), "bytes": archive.stat().st_size,
            "pruned": pruned, "kept": min(len(existing), keep)}


def ziel_pruefen(ordner: Path | str) -> str:
    """Leer, wenn dorthin gesichert werden kann -- sonst der Grund.

    Geprueft wird durch SCHREIBEN, nicht durch Nachdenken ueber Rechte. Ein
    Cloud-Ordner, der gerade nicht eingehaengt ist, sieht vorhanden aus und
    nimmt trotzdem nichts an; `os.access` sagt dazu das Falsche.
    """
    ziel = Path(ordner).expanduser()
    if not ziel.is_absolute():
        return (f"{ziel} ist kein vollstaendiger Pfad. Ein relativer haengt "
                f"am Arbeitsverzeichnis und zeigt nach dem naechsten Start "
                f"woanders hin.")
    try:
        ziel.mkdir(parents=True, exist_ok=True)
        probe = ziel / ".finctl-schreibprobe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


def sicherungsstand() -> dict:
    """Was die Seite ueber die Sicherung zeigt: Ziel, Anzahl, letzter Stand.

    Ohne Ziel ist das kein Fehler, sondern der Zustand einer frischen
    Installation -- die Seite fragt danach, statt abzustuerzen.
    """
    try:
        ziel, anzahl = backup_settings()
    except KeinBackupZielError as exc:
        return {"ziel": None, "keep": STAENDE, "grund": str(exc),
                "erreichbar": False, "letzter": None, "anzahl_staende": 0}

    fehler = ziel_pruefen(ziel) if ziel.exists() else ""
    staende = sorted(ziel.glob("finance-os_*.tar.gz"), reverse=True) \
        if ziel.is_dir() else []
    letzter = None
    if staende:
        stat = staende[0].stat()
        letzter = {"name": staende[0].name, "bytes": stat.st_size,
                   "am": datetime.fromtimestamp(stat.st_mtime).isoformat(" ", "seconds")}
    return {"ziel": str(ziel), "keep": anzahl, "grund": fehler,
            "erreichbar": ziel.is_dir() and not fehler,
            "letzter": letzter, "anzahl_staende": len(staende)}


# ------------------------------------------------------------------ Module

def module_anschliessen() -> None:
    """Was ein Modul der Basis beisteuert, bei der Basis anmelden.

    Hier und nicht in der Basis selbst: die Basis darf kein Modul kennen,
    die App alle (finctl/module.py). Jeder Weg, der Buchungen zuordnet --
    Update, CLI, Seite --, geht durch die App und findet damit den
    Tilgungsplan der Kredite, ob das Modul sichtbar ist oder nicht.
    """
    from finctl import kredite as _kredite

    _kredite.anmelden()


module_anschliessen()
