"""Startet das gebaute Paket und prueft, dass es wirklich laeuft.

    python werkzeuge/paket/rauchtest.py "dist/Finance OS.app/Contents/MacOS/Finance OS"
    python werkzeuge/paket/rauchtest.py dist/FinanceOS.exe

In einem leeren Datenordner, ohne Browser: richtet es ein, antwortet es,
zeigt es seine Seiten, laesst es sich beenden? Ein Paket, dem eine Vorlage
fehlt, startet naemlich -- und stuerzt erst bei der ersten Seite ab.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8799
#: Seiten der Basis -- bei einem Neuling sind nur sie eingeschaltet.
SEITEN = ("/monatsabschluss", "/einrichtung", "/handbuch", "/kontenregister", "/transactions",
          "/regeln", "/kategorien")


def _holen(pfad: str, methode: str = "GET") -> tuple[int, str]:
    anfrage = urllib.request.Request(f"http://127.0.0.1:{PORT}{pfad}", method=methode)
    try:
        with urllib.request.urlopen(anfrage, timeout=10) as antwort:
            return antwort.status, antwort.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as fehler:
        return fehler.code, fehler.read().decode("utf-8", "replace")


def main() -> None:
    programm = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        umgebung = {**os.environ, "FINCTL_DATEN": str(Path(tmp) / "daten"),
                    "FINCTL_KEIN_BROWSER": "1", "FINCTL_APP_PORT": str(PORT)}
        lauf = subprocess.Popen([str(programm)], env=umgebung, cwd=tmp,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            zuletzt = "keine Antwort"
            for _ in range(120):
                try:
                    status, text = _holen("/healthz")
                    if status == 200 and json.loads(text) == {"ok": True}:
                        break
                    zuletzt = f"{status}: {text[:200]}"
                except (OSError, ValueError) as fehler:
                    zuletzt = repr(fehler)
                if lauf.poll() is not None:
                    sys.exit(f"Das Paket hat sich beendet ({lauf.returncode}).")
                time.sleep(0.5)
            else:
                sys.exit(f"Das Paket kam nicht hoch. Zuletzt: {zuletzt}")
            for seite in SEITEN:
                status, text = _holen(seite)
                if status != 200 or "Finance OS" not in text:
                    sys.exit(f"{seite}: {status}")
                print(f"  {seite}: ok")
            _holen("/api/beenden", "POST")
            if lauf.wait(timeout=30) != 0:
                sys.exit(f"Beendet mit {lauf.returncode}")
            print("Rauchtest bestanden.")
        finally:
            if lauf.poll() is None:
                lauf.kill()
            ausgabe = lauf.stdout.read().decode("utf-8", "replace") if lauf.stdout else ""
            if ausgabe.strip():
                print("--- Ausgabe des Pakets ---\n" + ausgabe[-4000:])


if __name__ == "__main__":
    main()
