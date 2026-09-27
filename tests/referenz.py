"""Referenzstand: was ein Neuaufbau aus `config/` und den Auszuegen ergibt.

Das Sicherheitsnetz fuer den Umbau in Module. Jeder Schritt des Umbaus muss
aus denselben Eingaben dieselben Zahlen machen wie vorher -- nicht
ungefaehr, sondern Zeile fuer Zeile. Dieses Skript haelt fest, was "dieselben
Zahlen" heisst:

- `hauptbuch/` -- jede Tabelle des frisch aufgebauten Hauptbuchs, ohne
  Zeitstempel und ohne laufende Nummern (Buchungen ueber ihren `dedup_hash`).
- `seiten/`    -- der sichtbare Text jeder Seite und jeder GET-Schnittstelle
  ohne Pfadparameter. Dort stehen Prognose, Ziele, Kredite, Strom, Geteilt,
  Abos und Steuer so, wie man sie sieht.

WIE:
1. `config/` und `data/statements/` werden in einen Wegwerfordner kopiert,
   `FINCTL_DATEN` zeigt dorthin. Die echte Datenbank und die echte
   Konfiguration werden nur gelesen, nie geschrieben.
2. Der Tag wird festgenagelt (`--stichtag`, sonst heute). Ohne das waere
   jede Prognose morgen eine andere.
3. `init`, dann `ops.update` -- derselbe Weg wie der Update-Knopf. Einen
   Schritt dazu gibt es mit Absicht nicht: was der Neuaufbau braucht, muss
   der Knopf auch tun.

Der Stand landet in `data/referenz/`. Dort ist er, weil er echte Betraege
enthaelt: `data/` geht nie ins Repository (CLAUDE.md, "Persoenliches steht
nur in config/"). `tests/test_referenz.py` baut neu und vergleicht.

    python tests/referenz.py schreiben          # Referenz festhalten (alte ins Archiv)
    python tests/referenz.py pruefen            # neu bauen und vergleichen
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJEKT = Path(__file__).resolve().parent.parent
if str(PROJEKT) not in sys.path:
    sys.path.insert(0, str(PROJEKT))

# Spalten, die von der Uhr abhaengen. Dazu kommt jede ganzzahlige `id`: sie
# zaehlt nur mit, in welcher Reihenfolge eingelesen wurde. Eine Text-`id`
# ('dkb-giro', 'konsum/elektronik') ist dagegen ein Name und bleibt stehen.
FLUECHTIG = {"imported_at", "created_at", "updated_at", "loaded_at", "applied_at"}


# ------------------------------------------------------------ fester Tag

def tag_festnageln(stichtag: _dt.date) -> None:
    """`date.today()` und `datetime.now()` liefern ab jetzt den Stichtag.

    Am Modul `datetime` selbst, nicht an den einzelnen finctl-Modulen: viele
    importieren `date` erst in der Funktion oder als `_dtm.date`. Alles, was
    konstruiert wird, bleibt ein echtes `date` -- sonst stolpern YAML und
    sqlite, die ihre Typen genau nachschlagen.
    """
    if getattr(_dt.date, "_echt", None) is not None:
        return  # schon festgenagelt (geerbter Prozess)
    echt_date, echt_dt = _dt.date, _dt.datetime
    mittag = echt_dt(stichtag.year, stichtag.month, stichtag.day, 12, 0, 0)

    class _Wie(type):
        def __instancecheck__(cls, obj):
            return isinstance(obj, cls._echt)

        def __subclasscheck__(cls, sub):
            return issubclass(sub, cls._echt)

    class FesterTag(echt_date, metaclass=_Wie):
        _echt = echt_date

        def __new__(cls, *a, **k):
            return echt_date(*a, **k)

        @classmethod
        def today(cls):
            return echt_date(stichtag.year, stichtag.month, stichtag.day)

    class FesteZeit(echt_dt, metaclass=_Wie):
        _echt = echt_dt

        def __new__(cls, *a, **k):
            return echt_dt(*a, **k)

        @classmethod
        def now(cls, tz=None):
            return mittag if tz is None else mittag.replace(tzinfo=tz)

        @classmethod
        def today(cls):
            return mittag

    _dt.date = FesterTag
    _dt.datetime = FesteZeit


# ------------------------------------------------------------ Neuaufbau

def _text(roh: str) -> list[str]:
    """Sichtbarer Text einer Seite, eine Zeile je Block."""
    roh = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", "", roh)
    roh = re.sub(r"(?i)<br\s*/?>|</(tr|p|div|li|h\d|td|th|option|section|table)>", "\n", roh)
    roh = html.unescape(re.sub(r"<[^>]+>", " ", roh))
    return [z for z in (re.sub(r"\s+", " ", zeile).strip() for zeile in roh.splitlines()) if z]


def _relativ(pfad: str) -> str:
    """Auszugspfad relativ zum Datenordner -- das Hauptbuch speichert ihn mal
    so, mal absolut, je nachdem, von wo eingelesen wurde."""
    wurzel = os.environ.get("FINCTL_DATEN")
    p = Path(pfad)
    if wurzel and p.is_absolute():
        for w in (Path(wurzel), Path(wurzel).resolve()):
            if p.is_relative_to(w):
                return p.relative_to(w).as_posix()
    return p.as_posix()


def _hauptbuch(conn) -> dict[str, list]:
    """Jede Tabelle, sortiert und ohne fluechtige Spalten.

    Buchungen heissen nach ihrem `dedup_hash`, Auszuege nach ihrem
    `sha256` -- die laufenden Nummern verschieben sich, sobald ein Umbau in
    anderer Reihenfolge einliest, und waeren dann ein falscher Alarm.
    """
    namen = {r[0]: r[1] for r in conn.execute("SELECT id, dedup_hash FROM transactions")}
    auszuege = {r[0]: r[1] for r in conn.execute("SELECT id, file_sha256 FROM statements")}
    splits = {r[0]: f"{namen.get(r[1])}#{r[2]}"
              for r in conn.execute("SELECT id, transaction_id, seq FROM splits")}
    ersatz = {"transaction_id": namen, "statement_id": auszuege, "split_id": splits}

    aus: dict[str, list] = {}
    tabellen = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in sorted(tabellen):
        zaehler = {c[1] for c in conn.execute(f'PRAGMA table_info("{t}")')
                   if c[5] and c[2].upper() == "INTEGER"}
        zeilen = []
        for r in conn.execute(f'SELECT * FROM "{t}"'):
            d = {k: r[k] for k in r.keys()  # noqa: SIM118
                 if k not in FLUECHTIG and k not in zaehler}
            for k, tafel in ersatz.items():
                if k in d:
                    d[k] = tafel.get(d[k], d[k])
            if t == "statements":
                d["source_path"] = _relativ(d["source_path"])
            zeilen.append(d)
        zeilen.sort(key=lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False))
        aus[t] = zeilen
    return aus


# Schnittstellen, die ueber den Rechner Auskunft geben, nicht ueber das Geld:
# ob der Cloud-Ordner gerade erreichbar ist, wann zuletzt gesichert wurde, wo
# das System seine Daten ablegt. Sie aenderten sich mit jeder Sicherung und
# mit jedem Rechner, ohne dass sich an einer Zahl etwas geaendert haette.
RECHNERSACHE = {"/api/backup/ziel", "/api/datenordner"}
# Das Handbuch ist Text ueber die App, keine Ausgabe der App. Jede
# Formulierung darin schluege hier an und verdeckte, worum es geht.
NUR_TEXT = {"/handbuch"}


def _get_pfade(routen, praefix: str = "") -> set[str]:
    """Alle GET-Pfade, auch in eingebundenen Routern.

    Neuere FastAPI-Versionen legen `include_router` als eigenes Objekt ab,
    statt die Routen flach in die App zu kopieren.
    """
    aus: set[str] = set()
    for r in routen:
        unter = getattr(r, "original_router", None)
        if unter is not None:
            kontext = getattr(r, "include_context", None)
            aus |= _get_pfade(unter.routes, praefix + (getattr(kontext, "prefix", "") or ""))
        elif "GET" in (getattr(r, "methods", None) or set()):
            aus.add(praefix + r.path)
    return aus


def _seitenpfade() -> list[str]:
    from finctl.ledger import db as ledger
    from finctl.pfade import DB_PATH
    from finctl.web import server as srv

    pfade = sorted(p for p in _get_pfade(srv.app.routes)
                   if "{" not in p and p not in ("/docs", "/redoc", "/openapi.json")
                   and not p.startswith("/docs") and p not in RECHNERSACHE | NUR_TEXT)
    conn = ledger.connect(DB_PATH)
    try:
        objekte = [r[0] for r in conn.execute("SELECT id FROM properties ORDER BY id")]
    finally:
        conn.close()
    return pfade + [f"/immobilie/{o}" for o in objekte]


def _arbeiter_start(stichtag: str) -> None:
    """Jeder Hilfsprozess nagelt den Tag selbst fest -- er erbt es nicht."""
    _bibliotheken_vorab()
    tag_festnageln(_dt.date.fromisoformat(stichtag))


def _seiten_rendern(pfade: list[str]) -> dict[str, list[str]]:
    from fastapi.testclient import TestClient

    from finctl.web import auth
    from finctl.web import server as srv

    auth.PFAD = Path(os.environ["FINCTL_DATEN"]) / "_server_leer.yaml"  # kein Passwort
    client = TestClient(srv.app)
    aus: dict[str, list[str]] = {}
    for pfad in pfade:
        antwort = client.get(pfad)
        art = antwort.headers.get("content-type", "")
        if "json" in art:
            inhalt = json.dumps(antwort.json(), indent=1, sort_keys=True, ensure_ascii=False)
            zeilen = inhalt.splitlines()
        elif "text" in art:
            zeilen = _text(antwort.text)
        else:
            zeilen = [f"<{art}, {len(antwort.content)} Bytes>"]
        aus[pfad] = [f"HTTP {antwort.status_code}", *zeilen]
    return aus


def _seiten(stichtag: _dt.date) -> dict[str, list[str]]:
    """Alle Seiten, auf mehrere Prozesse verteilt.

    Die Seiten lesen nur; jede fuer sich zu rendern dauert eine Sekunde oder
    mehr, und hintereinander war das ein Drittel der ganzen Pruefung.
    """
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    pfade = _seitenpfade()
    anzahl = max(1, min(len(pfade), os.cpu_count() or 1))
    teile = [pfade[i::anzahl] for i in range(anzahl)]
    aus: dict[str, list[str]] = {}
    # "spawn" auf jedem System: ein frischer Prozess, der den Tag selbst
    # festnagelt. Unter Linux waere sonst "fork" die Vorgabe, auf dem Mac nicht.
    with ProcessPoolExecutor(anzahl, mp_context=multiprocessing.get_context("spawn"),
                             initializer=_arbeiter_start,
                             initargs=(stichtag.isoformat(),)) as pool:
        for teil in pool.map(_seiten_rendern, teile):
            aus.update(teil)
    return dict(sorted(aus.items()))


def _bibliotheken_vorab() -> None:
    """Fremde Bibliotheken VOR dem Festnageln laden.

    pydantic leitet eigene Klassen von `date` ab; traefe das auf den
    festgenagelten Tag, gaebe es einen Metaklassenkonflikt. Wer vorher geladen
    ist, behaelt das echte `date` -- gemeint ist ohnehin nur finctl.
    """
    import contextlib
    import importlib

    for name in ("pydantic", "pydantic.v1", "fastapi", "fastapi.testclient", "starlette",
                 "typer.testing", "yaml", "dateutil.parser", "dateutil.relativedelta",
                 "openpyxl", "pdfplumber", "jinja2", "filelock", "multipart"):
        with contextlib.suppress(ImportError):
            importlib.import_module(name)


def bauen(quelle: Path, ziel: Path, stichtag: _dt.date) -> None:
    """Im eigenen Prozess aufrufen: FINCTL_DATEN wird beim Import gelesen."""
    _bibliotheken_vorab()
    tag_festnageln(stichtag)
    from typer.testing import CliRunner

    from finctl import ops
    from finctl.cli import app
    from finctl.ledger import db as ledger
    from finctl.pfade import DB_PATH

    ergebnis = CliRunner().invoke(app, ["init", "--json"])
    if ergebnis.exit_code:
        raise SystemExit(f"init scheiterte:\n{ergebnis.output}")
    conn = ledger.connect(DB_PATH)
    try:
        bericht = ops.update(conn)
        conn.commit()
        buch = _hauptbuch(conn)
    finally:
        conn.close()

    (ziel / "hauptbuch").mkdir(parents=True)
    (ziel / "seiten").mkdir()
    for name, zeilen in buch.items():
        _schreiben(ziel / "hauptbuch" / f"{name}.json", zeilen)
    for pfad, zeilen in _seiten(stichtag).items():
        datei = (pfad.strip("/").replace("/", "__") or "start") + ".txt"
        (ziel / "seiten" / datei).write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    _schreiben(ziel / "update.json", bericht)
    _ort_ersetzen(ziel)
    _schreiben(ziel / "meta.json", {
        "stichtag": stichtag.isoformat(),
        "quelle": str(quelle),
        "zeilen": {k: len(v) for k, v in buch.items()},
    })


def _ort_ersetzen(ziel: Path) -> None:
    """Der Wegwerfordner heisst bei jedem Lauf anders -- in der Ausgabe nicht.

    Ebenso das Heimatverzeichnis: `/Users/<name>` auf dem Mac, anderswo etwas
    anderes. Als `~` geschrieben laesst sich eine Referenz von einem Rechner
    auf dem anderen pruefen.
    """
    wurzel = os.environ["FINCTL_DATEN"]
    ersatz = dict.fromkeys((wurzel, str(Path(wurzel).resolve())), "<DATEN>")
    ersatz[str(Path.home())] = "~"
    namen = sorted(ersatz, key=len, reverse=True)
    for datei in ziel.rglob("*"):
        if datei.is_file():
            text = datei.read_text(encoding="utf-8")
            neu = text
            for n in namen:
                neu = neu.replace(n, ersatz[n])
            if neu != text:
                datei.write_text(neu, encoding="utf-8")


def _schreiben(pfad: Path, daten) -> None:
    pfad.write_text(json.dumps(daten, indent=1, sort_keys=True, ensure_ascii=False, default=str)
                    + "\n", encoding="utf-8")


def neu_bauen(quelle: Path, stichtag: _dt.date, ziel: Path) -> None:
    """Kopie anlegen und `bauen` in einem frischen Prozess laufen lassen."""
    with tempfile.TemporaryDirectory(prefix="finctl-referenz-") as tmp:
        wurzel = Path(tmp)
        shutil.copytree(quelle / "config", wurzel / "config",
                        ignore=shutil.ignore_patterns("*.lock", ".DS_Store"))
        shutil.copytree(quelle / "data" / "statements", wurzel / "data" / "statements",
                        ignore=shutil.ignore_patterns(".DS_Store"))
        # Auch das Heimatverzeichnis im Wegwerfordner, wie in tests/conftest.py:
        # sonst liest der Neuaufbau einen eingetragenen Datenordner (ort.txt)
        # dieses Rechners, und die Referenz haengt davon ab, ob in der
        # Einrichtung jemand "Pruefen und eintragen" geklickt hat.
        heim = wurzel / "_heim"
        heim.mkdir()
        env = {**os.environ, "FINCTL_DATEN": str(wurzel), "HOME": str(heim),
               "USERPROFILE": str(heim), "APPDATA": str(heim / "appdata"),
               "XDG_DATA_HOME": str(heim / "share")}
        lauf = subprocess.run(
            [sys.executable, __file__, "_bauen", str(quelle), str(ziel), stichtag.isoformat()],
            env=env, cwd=wurzel, capture_output=True, text=True, check=False)
        if lauf.returncode:
            raise SystemExit(f"Neuaufbau scheiterte:\n{lauf.stdout}\n{lauf.stderr}")


# ------------------------------------------------------------ Vergleich

def unterschiede(alt: Path, neu: Path, *, zeilen: int = 30) -> list[str]:
    """Leer, wenn beide Staende gleich sind. Sonst je Datei ein kurzer Diff."""
    import difflib

    funde: list[str] = []
    dateien = sorted({p.relative_to(alt) for p in alt.rglob("*") if p.is_file()}
                     | {p.relative_to(neu) for p in neu.rglob("*") if p.is_file()})
    for rel in dateien:
        if rel.name == "meta.json":
            continue
        a, n = alt / rel, neu / rel
        if not a.exists() or not n.exists():
            funde.append(f"{rel}: {'fehlt jetzt' if a.exists() else 'ist neu'}")
            continue
        ta, tn = a.read_text(encoding="utf-8"), n.read_text(encoding="utf-8")
        if ta != tn:
            diff = list(difflib.unified_diff(ta.splitlines(), tn.splitlines(),
                                             "vorher", "jetzt", n=1, lineterm=""))
            rest = f"\n... {len(diff) - zeilen} Zeilen mehr" if len(diff) > zeilen else ""
            funde.append(f"{rel}:\n" + "\n".join(diff[:zeilen]) + rest)
    return funde


def _beiseite(ordner: Path) -> Path:
    """Die alte Referenz nicht loeschen, sondern ins Archiv daneben stellen.

    Wer die Referenz neu schreibt, will spaeter nachsehen koennen, wogegen
    vorher verglichen wurde -- und geloescht wird in diesem Werkzeug nichts,
    das sich nicht zurueckholen laesst.
    """
    meta = json.loads((ordner / "meta.json").read_text(encoding="utf-8"))
    archiv = ordner.parent / "referenz_archiv"
    archiv.mkdir(exist_ok=True)
    stempel = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    ziel = archiv / f"{stempel}_stichtag-{meta['stichtag']}"
    ordner.rename(ziel)
    print(f"Alte Referenz liegt jetzt in {ziel}")
    return ziel


def referenz_ordner(quelle: Path) -> Path:
    return quelle / "data" / "referenz"


def main() -> None:
    sys.path.insert(0, str(PROJEKT))
    if len(sys.argv) > 1 and sys.argv[1] == "_bauen":
        _, _, quelle, ziel, tag = sys.argv
        bauen(Path(quelle), Path(ziel), _dt.date.fromisoformat(tag))
        return

    from finctl.pfade import wurzel_mit_grund

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("aktion", choices=["schreiben", "pruefen"])
    p.add_argument("--stichtag", help="JJJJ-MM-TT; beim Pruefen der aus der Referenz")
    args = p.parse_args()

    quelle = wurzel_mit_grund()[0].resolve()
    ordner = referenz_ordner(quelle)
    if args.aktion == "schreiben":
        tag = _dt.date.fromisoformat(args.stichtag) if args.stichtag else _dt.date.today()
        if ordner.exists():
            _beiseite(ordner)
        neu_bauen(quelle, tag, ordner)
        meta = json.loads((ordner / "meta.json").read_text(encoding="utf-8"))
        print(f"Referenz geschrieben: {ordner}  (Stichtag {tag})")
        for name, n in meta["zeilen"].items():
            print(f"  {name:20} {n:6}")
        return

    meta = json.loads((ordner / "meta.json").read_text(encoding="utf-8"))
    tag = _dt.date.fromisoformat(meta["stichtag"])
    with tempfile.TemporaryDirectory(prefix="finctl-vergleich-") as tmp:
        neu = Path(tmp) / "stand"
        neu_bauen(quelle, tag, neu)
        funde = unterschiede(ordner, neu)
    if funde:
        print("\n\n".join(funde))
        raise SystemExit(f"\n{len(funde)} Dateien weichen von der Referenz ab.")
    print(f"Gleich: Neuaufbau entspricht der Referenz vom Stichtag {tag}.")


if __name__ == "__main__":
    main()
