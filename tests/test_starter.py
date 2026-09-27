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

from finctl import starter
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
    finally:
        if lauf.poll() is None:
            lauf.kill()


def test_the_first_start_opens_the_setup_and_later_ones_the_overview(monkeypatch):
    from finctl import konten

    monkeypatch.setattr(konten, "laden", lambda *a, **k: [])
    assert starter.startseite() == "einrichtung"
    monkeypatch.setattr(konten, "laden", lambda *a, **k: [{"id": "giro"}])
    assert starter.startseite() == ""
