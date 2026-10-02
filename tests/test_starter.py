"""Der Starter der App zum Doppelklicken (finctl/starter.py)."""

from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finctl import menueleiste, starter
from finctl.web.server import app

WURZEL = Path(__file__).resolve().parent.parent
client = TestClient(app)


def _belegt() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s


def test_a_free_port_is_taken_as_asked():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        frei = s.getsockname()[1]
    assert starter.port_waehlen(frei) == (frei, False)


def test_a_port_taken_by_something_else_gives_way_to_a_free_one():
    fremd = _belegt()
    try:
        port, laeuft = starter.port_waehlen(fremd.getsockname()[1])
        assert port != fremd.getsockname()[1] and not laeuft
    finally:
        fremd.close()


def test_a_second_double_click_only_opens_the_browser():
    class Antwort(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Antwort)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert starter.port_waehlen(server.server_address[1]) == (server.server_address[1], True)
    finally:
        server.shutdown()


def test_without_the_starter_there_is_nothing_to_quit():
    assert client.post("/api/beenden").status_code == 404
    html = client.get("/monatsabschluss").text
    assert "appBeenden()\"" not in html.replace("function appBeenden()", "")


def test_every_page_names_its_version():
    import finctl

    assert f"Finance OS {finctl.__version__}" in client.get("/monatsabschluss").text


@pytest.mark.langsam
def test_the_app_sets_itself_up_starts_and_quits(tmp_path):
    """Ein leerer Rechner: Doppelklick, Seite antwortet, Beenden beendet."""
    daten = tmp_path / "daten"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    umgebung = {**os.environ, "FINCTL_DATEN": str(daten), "FINCTL_KEIN_BROWSER": "1",
                "FINCTL_APP_PORT": str(port), "HOME": str(tmp_path),
                "USERPROFILE": str(tmp_path), "APPDATA": str(tmp_path / "appdata")}
    lauf = subprocess.Popen([sys.executable, "-m", "finctl.starter"], cwd=tmp_path,
                            env=umgebung, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8")
    try:
        adresse = f"http://127.0.0.1:{port}"
        for _ in range(150):
            try:
                with urllib.request.urlopen(f"{adresse}/healthz", timeout=1) as a:
                    assert json.loads(a.read()) == {"ok": True}
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("der Server kam nicht hoch")
        assert (daten / "data" / "finance.db").exists()
        with urllib.request.urlopen(f"{adresse}/einrichtung", timeout=5) as a:
            seite = a.read().decode("utf-8")
        assert "appBeenden()" in seite and "Beenden</button>" in seite
        anfrage = urllib.request.Request(f"{adresse}/api/beenden", method="POST")
        with urllib.request.urlopen(anfrage, timeout=5) as a:
            assert json.loads(a.read()) == {"ok": True}
        assert lauf.wait(timeout=20) == 0
        # Auf dem Mac lief das ueber das Symbol in der Menueleiste -- und
        # endete trotzdem sauber, mit Python, das seine Ausgabe noch schrieb.
        if menueleiste.verfuegbar():
            assert "in der Menueleiste" in lauf.stdout.read()
    finally:
        if lauf.poll() is None:
            lauf.kill()


def test_the_first_start_opens_the_setup_and_later_ones_the_overview(monkeypatch):
    from finctl import konten

    monkeypatch.setattr(konten, "laden", lambda *a, **k: [])
    assert starter.startseite() == "einrichtung"
    monkeypatch.setattr(konten, "laden", lambda *a, **k: [{"id": "giro"}])
    assert starter.startseite() == ""


def test_without_appkit_there_is_no_menu_bar(monkeypatch):
    """Windows oder ein Python ohne pyobjc: der Starter laeuft wie vorher."""
    monkeypatch.setitem(sys.modules, "AppKit", None)
    assert menueleiste.verfuegbar() is False


@pytest.mark.skipif(not menueleiste.verfuegbar(), reason="nur auf dem Mac mit pyobjc")
def test_the_menu_opens_and_quits(monkeypatch):
    """Das Menue selbst, ohne Symbol in der Leiste: das zeigte jeder Testlauf."""
    import AppKit

    class Server:
        should_exit = False

    geoeffnet = []
    monkeypatch.setattr(menueleiste.webbrowser, "open", geoeffnet.append)
    ziel = menueleiste._ziel().alloc().init()
    ziel.adresse, ziel.server = "http://127.0.0.1:1/", Server()
    menue = menueleiste._menue(ziel, "Finance OS")

    titel = [menue.itemAtIndex_(i).title() for i in range(menue.numberOfItems())]
    assert titel == ["Finance OS öffnen", "", "Beenden"]
    assert menue.itemAtIndex_(1).isSeparatorItem()
    for i in (0, 2):
        punkt = menue.itemAtIndex_(i)
        AppKit.NSApplication.sharedApplication().sendAction_to_from_(
            punkt.action(), punkt.target(), punkt)
    assert geoeffnet == ["http://127.0.0.1:1/"] and ziel.server.should_exit is True
    # Ein SF Symbol, das dieses System kennt -- sonst stuende nur der Ersatz da.
    assert AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        menueleiste.SYMBOL, None) is not None
