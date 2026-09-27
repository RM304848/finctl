"""Die App zum Doppelklicken: Server starten, Browser oeffnen, fertig.

Was `finctl serve` fuer jemanden mit Terminal ist, ist das hier fuer alle
anderen. Der Starter ist der Einstieg der Pakete (.dmg, .exe) und laeuft
genauso als `finctl app`.

WAS ER ANDERS MACHT ALS `serve`:

* Er richtet ein, wenn noch nichts da ist. Ein Neuling hat kein Hauptbuch,
  und "Run `finctl init` first" ist fuer jemanden ohne Terminal kein Rat.
* Er bindet immer an 127.0.0.1, egal was server.yaml sagt. Wer die App im
  Netz will, nimmt `serve` und setzt ein Passwort.
* Er laeuft auf einem eigenen Port (8777), nicht auf 8765: Wer daneben
  `finctl serve` benutzt, soll nicht zwei Programme auf einer Adresse haben.
  Ist der Port belegt und antwortet dort schon Finance OS, wird nur der
  Browser geoeffnet -- ein zweiter Doppelklick startet keinen zweiten Server.
  Antwortet dort etwas anderes, nimmt er einen freien Port.
* Er laesst sich aus der App heraus beenden (Knopf "Beenden"): Auf dem Mac
  gibt es kein Fenster, das man schliessen koennte.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
import webbrowser

PORT = 8777
HOST = "127.0.0.1"


def _antwortet_finance_os(port: int) -> bool:
    """Laeuft auf dem Port schon diese App? Auch hinter der Anmeldung."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/healthz", timeout=2) as antwort:
            text = antwort.read(4096).decode("utf-8", "replace")
    except OSError:
        return False
    return '"ok"' in text or "Finance OS" in text


def _frei(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def _freier_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def port_waehlen(wunsch: int = PORT) -> tuple[int, bool]:
    """(Port, laeuft schon). Laeuft schon: nur den Browser oeffnen."""
    if _frei(wunsch):
        return wunsch, False
    if _antwortet_finance_os(wunsch):
        return wunsch, True
    return _freier_port(), False


def einrichten_wenn_noetig() -> bool:
    """Beim allerersten Start: Hauptbuch und Startdateien anlegen."""
    from finctl.pfade import DB_PATH

    if DB_PATH.exists():
        return False
    from finctl import cli

    cli.init(json=False)
    return True


def _browser_wenn_bereit(server, adresse: str) -> None:
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.1)
    if not os.environ.get("FINCTL_KEIN_BROWSER"):
        webbrowser.open(adresse)


def main() -> None:
    port, laeuft = port_waehlen(int(os.environ.get("FINCTL_APP_PORT") or PORT))
    adresse = f"http://{HOST}:{port}/"
    if laeuft:
        print(f"Finance OS laeuft schon: {adresse}")
        if not os.environ.get("FINCTL_KEIN_BROWSER"):
            webbrowser.open(adresse)
        return

    einrichten_wenn_noetig()

    import uvicorn

    from finctl.web import basis
    from finctl.web.server import app

    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning"))

    def beenden() -> None:
        server.should_exit = True

    basis.BEENDEN = beenden
    print(f"Finance OS laeuft: {adresse}\n"
          "Beenden: in der App oben rechts -- oder dieses Fenster schliessen.")
    threading.Thread(target=_browser_wenn_bereit, args=(server, adresse), daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
