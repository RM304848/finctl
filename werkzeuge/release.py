"""Eine neue Version veroeffentlichen -- ein Befehl, ohne KI.

    .venv/bin/python werkzeuge/release.py 0.2.0

Was er tut, in dieser Reihenfolge, und er haelt beim ersten Problem an:

1. Prueft, dass du auf `main` bist, nichts Ungespeichertes herumliegt und die
   neue Nummer groesser ist als die alte.
2. Prueft, dass unter "Unveroeffentlicht" in CHANGELOG.md etwas steht. Eine
   Version ohne Beschreibung kann niemand einschaetzen.
3. Laesst alle Tests laufen, auf deinen Daten und auf dem Musterhaushalt.
   `--ohne-tests` ueberspringt das, fuer den Fall, dass sie gerade liefen.
4. Schreibt die Nummer nach finctl/__init__.py, macht aus "Unveroeffentlicht"
   die neue Version mit Datum, committet und setzt das Tag `v0.2.0`.
5. Fragt, ob hochgeladen werden soll. Mit dem Tag baut GitHub die Pakete
   (.dmg, .exe) und legt einen Release-Entwurf an; veroeffentlicht wird er
   erst mit deinem Klick auf "Publish release".

Rueckgaengig, solange nicht hochgeladen:  git tag -d v0.2.0 && git reset --hard HEAD~1
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
VERSION_DATEI = WURZEL / "finctl" / "__init__.py"
CHANGELOG = WURZEL / "CHANGELOG.md"
OFFEN = "## Unveröffentlicht"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=WURZEL, check=True,
                          capture_output=True, text=True).stdout.strip()


def _stopp(grund: str) -> None:
    sys.exit(f"Abgebrochen: {grund}")


def _teile(nummer: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", nummer):
        _stopp(f"„{nummer}“ ist keine Versionsnummer wie 0.2.0")
    return tuple(int(x) for x in nummer.split("."))


def aktuelle_version() -> str:
    treffer = re.search(r'__version__ = "([^"]+)"', VERSION_DATEI.read_text(encoding="utf-8"))
    return treffer.group(1)


def offene_aenderungen(text: str) -> str:
    """Was unter "Unveroeffentlicht" steht, bis zur naechsten Version."""
    if OFFEN not in text:
        _stopp(f"„{OFFEN}“ fehlt in CHANGELOG.md")
    rest = text.split(OFFEN, 1)[1]
    return rest.split("\n## ", 1)[0].strip()


def changelog_schreiben(text: str, nummer: str, heute: date) -> str:
    """"Unveroeffentlicht" wird zur Version, darueber ein neuer, leerer Abschnitt."""
    return text.replace(OFFEN, f"{OFFEN}\n\n## {nummer} – {heute.isoformat()}", 1)


def _vorpruefen(nummer: str) -> str:
    if _git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        _stopp("nicht auf main")
    if _git("status", "--porcelain"):
        _stopp("es gibt ungespeicherte Aenderungen (git status)")
    alt = aktuelle_version()
    if _teile(nummer) <= _teile(alt):
        _stopp(f"{nummer} ist nicht groesser als die aktuelle {alt}")
    if f"v{nummer}" in _git("tag").split():
        _stopp(f"das Tag v{nummer} gibt es schon")
    text = CHANGELOG.read_text(encoding="utf-8")
    if not offene_aenderungen(text):
        _stopp(f"unter „{OFFEN}“ in CHANGELOG.md steht nichts")
    return alt


def _testen() -> None:
    python = sys.executable
    for name, umgebung in (("deine Daten", {}), ("Musterhaushalt", {"FINCTL_TESTDATEN": "muster"})):
        print(f"Tests auf {name} ...")
        lauf = subprocess.run([python, "-m", "pytest", "-q"], cwd=WURZEL,
                              env={**os.environ, **umgebung}, check=False)
        if lauf.returncode:
            _stopp(f"Tests auf {name} sind rot")


def main() -> None:
    parser = argparse.ArgumentParser(description="Eine neue Version veroeffentlichen.")
    parser.add_argument("nummer", help="die neue Versionsnummer, etwa 0.2.0")
    parser.add_argument("--ohne-tests", action="store_true")
    args = parser.parse_args()

    alt = _vorpruefen(args.nummer)
    if not args.ohne_tests:
        _testen()

    VERSION_DATEI.write_text(
        VERSION_DATEI.read_text(encoding="utf-8").replace(
            f'__version__ = "{alt}"', f'__version__ = "{args.nummer}"'),
        encoding="utf-8")
    CHANGELOG.write_text(changelog_schreiben(CHANGELOG.read_text(encoding="utf-8"),
                                             args.nummer, date.today()), encoding="utf-8")
    _git("add", str(VERSION_DATEI), str(CHANGELOG))
    _git("commit", "-m", f"Version {args.nummer}")
    _git("tag", "-a", f"v{args.nummer}", "-m", f"Version {args.nummer}")
    print(f"Version {args.nummer} steht lokal (Commit und Tag v{args.nummer}).")

    if input("Jetzt hochladen? GitHub baut dann die Pakete. [j/N] ").strip().lower() == "j":
        _git("push", "origin", "main", f"v{args.nummer}")
        print("Hochgeladen. Der Release-Entwurf erscheint in etwa 15 Minuten unter\n"
              "  https://github.com/RM304848/finctl/releases")
    else:
        print(f"Nicht hochgeladen. Spaeter:  git push origin main v{args.nummer}")


if __name__ == "__main__":
    main()
