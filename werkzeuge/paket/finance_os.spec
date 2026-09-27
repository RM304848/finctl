# PyInstaller-Bauplan fuer die App zum Doppelklicken -- Mac und Windows.
#
#     pyinstaller --noconfirm werkzeuge/paket/finance_os.spec
#
# Mac: dist/Finance OS.app (daraus macht der Bau-Workflow die .dmg).
# Windows: dist/FinanceOS.exe, eine einzige Datei mit Konsolenfenster --
# das Fenster IST die laufende App, schliessen beendet sie.
#
# Mitgenommen wird, was die App zur Laufzeit von der Platte liest und was
# kein Import verraet: Vorlagen, Startdateien, Schema, Handbuch (wie
# package-data in pyproject.toml), die Zeichentabellen von pdfminer und
# die Module, die erst per Name geladen werden (Parserprofile, uvicorn).
# tests/test_paket.py prueft dasselbe fuer das Rad.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

WURZEL = Path(SPECPATH).parent.parent  # noqa: F821 -- von PyInstaller gesetzt
MAC = sys.platform == "darwin"

daten = (collect_data_files("finctl", includes=["**/*.html", "**/*.yaml", "**/*.sql", "*.md"])
         + collect_data_files("pdfminer")
         + copy_metadata("finctl"))
# Die eigene Lizenz und die der mitgelieferten Bibliotheken reisen mit
# (drittlizenzen.py erzeugt die zweite Datei vor dem Bau).
for datei in ("LICENSE.md", "DRITTLIZENZEN.txt"):
    if (WURZEL / datei).exists():
        daten.append((str(WURZEL / datei), "."))
versteckt = (collect_submodules("finctl") + collect_submodules("uvicorn")
             + ["multipart", "python_multipart"])

a = Analysis(  # noqa: F821
    [str(WURZEL / "werkzeuge" / "paket" / "start.py")],
    pathex=[str(WURZEL)],
    datas=daten,
    hiddenimports=versteckt,
    excludes=["tkinter", "pytest", "ruff"],
)
pyz = PYZ(a.pure)  # noqa: F821

if MAC:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Finance OS",  # noqa: F821
              console=False)
    sammlung = COLLECT(exe, a.binaries, a.datas, name="Finance OS")  # noqa: F821
    app = BUNDLE(  # noqa: F821
        sammlung,
        name="Finance OS.app",
        bundle_identifier="de.finance-os.app",
        info_plist={
            # Kein Symbol im Dock: die App hat kein Fenster, ihr Fenster ist
            # der Browser. Ein Dock-Symbol liesse sich "beenden", ohne dass
            # der Server davon erfuhr. Beendet wird ueber den Knopf in der App.
            "LSUIElement": True,
            "CFBundleShortVersionString": __import__("finctl").__version__,
        },
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="FinanceOS",  # noqa: F821
              console=True)
