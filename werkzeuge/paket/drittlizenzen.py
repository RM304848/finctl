"""DRITTLIZENZEN.txt: die Lizenztexte von allem, was im Paket mitgeht.

    python werkzeuge/paket/drittlizenzen.py DRITTLIZENZEN.txt

MIT, BSD und Apache erlauben fast alles -- verlangen aber, dass wer die
Software WEITERGIBT, Copyright und Lizenztext mitliefert. Die .dmg und die
.exe enthalten rund vierzig Bibliotheken, dazu Python selbst und den
Startcode von PyInstaller. Gesammelt wird aus dem, was installiert ist, nicht
aus einer Liste: eine neue Abhaengigkeit ist dann ohne weiteres Zutun dabei.
"""

from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path

#: Was im Dateinamen einer Lizenzdatei steht.
_LIZENZDATEI = re.compile(r"(licen[cs]e|copying|notice|authors)", re.I)


def _abhaengigkeiten(start: str = "finctl") -> list[str]:
    """Alle Pakete, die `start` zur Laufzeit braucht, ohne Extras."""
    gesehen: set[str] = set()
    offen = [start]
    while offen:
        name = offen.pop()
        schluessel = name.lower().replace("_", "-")
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        try:
            anforderungen = md.requires(name) or []
        except md.PackageNotFoundError:
            continue
        offen += [re.split(r"[ ;<>=!~\[(]", a, maxsplit=1)[0]
                  for a in anforderungen if "extra ==" not in a]
    gesehen.discard(start)
    return sorted(gesehen)


def _lizenzname(meta) -> str:
    if meta.get("License-Expression"):
        return meta["License-Expression"]
    klassen = [k.split("::")[-1].strip() for k in meta.get_all("Classifier") or []
               if k.startswith("License")]
    return "; ".join(klassen) or (meta.get("License") or "siehe Text").splitlines()[0]


def _abschnitt(name: str) -> str | None:
    try:
        verteilung = md.distribution(name)
    except md.PackageNotFoundError:
        return None                      # nur auf einem anderen System noetig
    meta = verteilung.metadata
    teile = [f"{meta['Name']} {meta['Version']} -- {_lizenzname(meta)}",
             "=" * 78]
    for datei in sorted(verteilung.files or [], key=str):
        if not (_LIZENZDATEI.search(datei.name) or "licenses" in datei.parts):
            continue
        if datei.suffix in (".py", ".pyc", ".so", ".dll", ".pyd"):
            continue
        roh = Path(datei.locate()).read_bytes()
        # Aeltere Lizenzdateien sind Latin-1 (das Copyright-Zeichen).
        try:
            text = roh.decode("utf-8")
        except UnicodeDecodeError:
            text = roh.decode("latin-1")
        if text.strip():
            teile += [f"--- {datei.name} ---", text.strip(), ""]
    return "\n".join(teile)


def _python_lizenz() -> str:
    for kandidat in (Path(sys.base_prefix) / "LICENSE.txt",
                     Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}."
                     f"{sys.version_info.minor}" / "LICENSE.txt"):
        if kandidat.exists():
            return kandidat.read_text(encoding="utf-8")
    return "Python Software Foundation License Version 2 -- https://docs.python.org/3/license.html"


def sammeln() -> str:
    kopf = ("Finance OS enthaelt die folgenden Programme anderer, jeweils unter ihrer\n"
            "eigenen Lizenz. Finance OS selbst steht unter PolyForm Strict 1.0.0\n"
            "(LICENSE.md).\n")
    python = f"Python {sys.version.split()[0]} -- PSF-2.0"
    teile = [kopf, python, "=" * 78, _python_lizenz(), ""]
    namen = _abhaengigkeiten()
    # Der Startcode im Paket stammt von PyInstaller: GPL mit der ausdruecklichen
    # Ausnahme, damit gebaute Programme unter beliebiger Lizenz zu verteilen.
    namen += [n for n in ("pyinstaller",) if n not in namen]
    for name in namen:
        abschnitt = _abschnitt(name)
        if abschnitt:
            teile += [abschnitt, ""]
    return "\n".join(teile)


if __name__ == "__main__":
    ziel = Path(sys.argv[1] if len(sys.argv) > 1 else "DRITTLIZENZEN.txt")
    ziel.write_text(sammeln(), encoding="utf-8")
    print(f"{ziel}: {ziel.stat().st_size // 1024} KB")
