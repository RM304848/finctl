"""Das Werkzeug soll auch auf Windows laufen.

Gemessen am 23.09.2026: `finctl.cli` und `finctl.web.server` liessen sich
ohne `fcntl` importieren, `finctl.rules.categorize` nicht -- und an dem Modul
haengt jeder Schreibweg. Auf Windows startete die App also, zeigte ihre Seiten
und brach in dem Moment, in dem jemand eine Kategorie zuordnete.

Es war EINE Zeile. Genau deshalb steht hier ein Test: ein `import fcntl`, das
jemand beilaeufig ergaenzt, faellt sonst erst auf, wenn es beim Nutzer bricht
-- und der ist dann auf einem Rechner, auf dem niemand von uns nachsehen kann.

Geprueft wird der Import, nicht das Verhalten. Ob `filelock` unter Windows
richtig sperrt, kann diese Datei nicht wissen; dafuer wurde die Bibliothek
gewaehlt, statt die Fallunterscheidung selbst zu schreiben.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent

#: Was es auf Windows nicht gibt. `fcntl` und `termios` fehlen dort ganz,
#: `pwd` und `grp` ebenso -- sie beschreiben Unix-Benutzerverwaltung.
NUR_UNIX = ("fcntl", "pwd", "grp", "termios", "resource", "posix", "pty")

#: Die Einstiege, die ein Nutzer tatsaechlich anfasst, und das Modul hinter
#: jedem Schreibweg. Laeuft der Import, laeuft auch der erste Klick.
EINSTIEGE = (
    "finctl.cli",
    "finctl.web.server",
    "finctl.rules.categorize",
    "finctl.ingest.importer",
    "finctl.ops",
)

#: Ein Prozess, der die Unix-Module sperrt und dann importiert. In einem
#: eigenen Prozess, weil `fcntl` im laufenden laengst geladen waere.
#:
#: Gesperrt wird nur fuer UNSEREN Code: die Standardbibliothek holt sich auf
#: Unix selbst `posix` und `grp`, und auf Windows nimmt sie dort etwas
#: anderes. Wer importiert, steht in `globals["__name__"]`.
PROBE = """
import builtins
echt = builtins.__import__
GESPERRT = {gesperrt!r}
def ohne(name, globals=None, *a, **k):
    wer = (globals or {{}}).get("__name__") or ""
    if name.split(".")[0] in GESPERRT and wer.split(".")[0] == "finctl":
        raise ModuleNotFoundError(
            "No module named " + repr(name) + " (gesperrt fuer " + wer + ")")
    return echt(name, globals, *a, **k)
builtins.__import__ = ohne
import {modul}
print("ok")
"""


@pytest.mark.parametrize("modul", EINSTIEGE)
def test_the_entry_points_import_without_unix_only_modules(modul):
    fertig = subprocess.run(
        [sys.executable, "-c", PROBE.format(gesperrt=set(NUR_UNIX), modul=modul)],
        capture_output=True, text=True, cwd=WURZEL)
    assert fertig.returncode == 0, (
        f"{modul} braucht ein Modul, das es auf Windows nicht gibt:\n"
        + fertig.stderr[-1500:])


def _quellen() -> list[Path]:
    return [p for p in (WURZEL / "finctl").rglob("*.py")
            if "__pycache__" not in str(p)]


def test_no_module_imports_a_unix_only_module():
    """Auch dort, wo der Import in einer Funktion steht.

    Der Importtest oben laeuft nur die fuenf Einstiege ab. Ein `import fcntl`
    tief in einem selten benutzten Zweig faende er erst, wenn jemand genau
    diesen Zweig betritt -- beim Nutzer, auf Windows.
    """
    treffer = []
    for pfad in sorted(_quellen()):
        baum = ast.parse(pfad.read_text(encoding="utf-8"), filename=str(pfad))
        for knoten in ast.walk(baum):
            namen = []
            if isinstance(knoten, ast.Import):
                namen = [a.name for a in knoten.names]
            elif isinstance(knoten, ast.ImportFrom) and knoten.module:
                namen = [knoten.module]
            for name in namen:
                if name.split(".")[0] in NUR_UNIX:
                    rel = pfad.relative_to(WURZEL).as_posix()
                    treffer.append(f"{rel}:{knoten.lineno}: {name}")
    assert not treffer, (
        "Diese Importe gibt es auf Windows nicht:\n  " + "\n  ".join(treffer))


# ------------------------------------------------------------------ Kodierung

def _binaer(aufruf: ast.Call) -> bool:
    modus = [a for a in aufruf.args[:2] if isinstance(a, ast.Constant)]
    modus += [k.value for k in aufruf.keywords
              if k.arg == "mode" and isinstance(k.value, ast.Constant)]
    return any(isinstance(m.value, str) and "b" in m.value for m in modus)


def test_every_text_file_is_read_and_written_as_utf8():
    """Ohne `encoding=` nimmt Python die Kodierung des Systems.

    Auf macOS ist das UTF-8, auf einem deutschen Windows cp1252. Eine Datei,
    die so geschrieben und als UTF-8 gelesen wird, bricht beim ersten
    Umlaut -- gefunden im ersten Windows-Lauf, an "Miete entfällt". Gilt fuer
    App und Tests: ein Test, der anders schreibt als die App, prueft nichts.
    """
    namen = {"read_text", "write_text", "open", "NamedTemporaryFile", "TemporaryFile"}
    fremd = {"pdfplumber", "tarfile", "zipfile", "webbrowser", "os"}
    treffer = []
    for pfad in sorted([*(WURZEL / "finctl").rglob("*.py"), *(WURZEL / "tests").rglob("*.py")]):
        for knoten in ast.walk(ast.parse(pfad.read_text(encoding="utf-8"))):
            if not isinstance(knoten, ast.Call):
                continue
            f = knoten.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name not in namen or _binaer(knoten):
                continue
            if isinstance(f, ast.Attribute) and getattr(f.value, "id", "") in fremd:
                continue
            if not any(k.arg == "encoding" for k in knoten.keywords):
                treffer.append(f"{pfad.relative_to(WURZEL).as_posix()}:{knoten.lineno}")
    assert not treffer, "Ohne encoding=:\n  " + "\n  ".join(treffer)
