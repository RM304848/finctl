"""Die Release-Notizen einer Version: ihr Abschnitt aus CHANGELOG.md.

    python werkzeuge/paket/notizen.py 0.2.0 > notizen.md
"""

from __future__ import annotations

import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent.parent


def abschnitt(text: str, nummer: str) -> str:
    """Was unter `## <nummer> – <datum>` steht, bis zur naechsten Version."""
    kopf = f"\n## {nummer} "
    if kopf not in text:
        raise SystemExit(f"Version {nummer} steht nicht in CHANGELOG.md")
    rest = text.split(kopf, 1)[1].split("\n", 1)[1]
    return rest.split("\n## ", 1)[0].strip()


if __name__ == "__main__":
    print(abschnitt((WURZEL / "CHANGELOG.md").read_text(encoding="utf-8"), sys.argv[1]))
    print("\nInstallation: siehe docs/installation.md im Repository.")
