"""Wer darf, wenn die App nicht mehr nur auf localhost hoert.

Eine Anmeldung, kein Benutzerkonto: es gibt genau eine Person, und die soll
einmal auf dem Telefon ein Passwort eingeben und danach nie wieder. Was hier
verhindert werden soll, ist nicht ein gezielter Angriff, sondern dass die
Huerde exakt null ist -- Gaeste im WLAN, mitbenutzte Rechner, jedes Geraet
mit fragwuerdiger Firmware.

Das Passwort steht NICHT im Klartext in der Konfiguration. Gespeichert wird
ein scrypt-Hash mit Salz; `finctl passwort` fragt danach und schreibt ihn.
Eine Datei im Projektordner, die das Passwort lesbar enthaelt, waere schlimmer
als keine Anmeldung, weil sie Sicherheit behauptet.

KEIN TLS. Auf dem Weg Telefon -> Router verschluesselt das VPN des Besitzers,
der Rest ist sein eigenes Heimnetz. Ein selbstsigniertes Zertifikat, das jeder
Browser anmeckert, kostet mehr Vertrauen als es schafft -- und ein Nutzer, der
gelernt hat, Zertifikatswarnungen wegzuklicken, ist schlechter dran als vorher.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from finctl.pfade import CONFIG_DIR

PFAD = CONFIG_DIR / "server.yaml"
COOKIE = "finctl_auth"
#: Ein Jahr. Wer sich auf seinem eigenen Telefon jede Woche neu anmelden
#: muss, schaltet die Anmeldung ab -- das waere das schlechtere Ergebnis.
GUELTIG_SEKUNDEN = 365 * 24 * 3600

KOPF = """# Wie der Server erreichbar ist.
#
# `host` steht bewusst hier und nicht nur als Kommandozeilenschalter: ein
# Schalter wird vergessen, eine Datei nicht. 127.0.0.1 heisst "nur dieser
# Rechner"; 0.0.0.0 heisst "jedes Geraet im Netz", und dann MUSS ein Passwort
# gesetzt sein -- `finctl serve` verweigert sonst den Start.
#
# `passwort_hash` und `geheimnis` schreibt `finctl passwort`. Das Passwort
# selbst steht nirgends; gespeichert wird ein scrypt-Hash mit Salz.

"""


def laden() -> dict:
    import yaml

    if not PFAD.exists():
        return {}
    return yaml.safe_load(PFAD.read_text(encoding="utf-8")) or {}


def _schreiben(spec: dict) -> None:
    import yaml

    PFAD.write_text(KOPF + yaml.safe_dump(spec, allow_unicode=True, sort_keys=True),
                    encoding="utf-8")


def bindung() -> tuple[str, int]:
    spec = laden()
    return str(spec.get("host") or "127.0.0.1"), int(spec.get("port") or 8765)


def passwort_gesetzt() -> bool:
    return bool(laden().get("passwort_hash"))


def passwort_setzen(passwort: str) -> None:
    """Hash und Sitzungsgeheimnis schreiben. Leeres Passwort entfernt beides."""
    spec = laden()
    if not passwort:
        spec.pop("passwort_hash", None)
        spec.pop("geheimnis", None)
    else:
        salz = os.urandom(16)
        hash_ = hashlib.scrypt(passwort.encode(), salt=salz, n=2**14, r=8, p=1)
        spec["passwort_hash"] = (base64.b64encode(salz).decode() + "$"
                                 + base64.b64encode(hash_).decode())
        # Ein neues Geheimnis meldet alle Geraete ab. Das ist gewollt: wer das
        # Passwort wechselt, will genau das.
        spec["geheimnis"] = secrets.token_urlsafe(32)
    spec.setdefault("host", "127.0.0.1")
    spec.setdefault("port", 8765)
    _schreiben(spec)


def passwort_stimmt(passwort: str) -> bool:
    gespeichert = laden().get("passwort_hash") or ""
    if "$" not in gespeichert:
        return False
    salz_b64, hash_b64 = gespeichert.split("$", 1)
    erwartet = base64.b64decode(hash_b64)
    versuch = hashlib.scrypt(passwort.encode(), salt=base64.b64decode(salz_b64),
                             n=2**14, r=8, p=1)
    return hmac.compare_digest(versuch, erwartet)


def _geheimnis() -> bytes:
    return str(laden().get("geheimnis") or "").encode()


def token_bauen() -> str:
    ablauf = str(int(time.time()) + GUELTIG_SEKUNDEN)
    sig = hmac.new(_geheimnis(), ablauf.encode(), hashlib.sha256).hexdigest()
    return f"{ablauf}.{sig}"


def token_gilt(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    ablauf, sig = token.rsplit(".", 1)
    erwartet = hmac.new(_geheimnis(), ablauf.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, erwartet):
        return False
    try:
        return int(ablauf) > time.time()
    except ValueError:
        return False


def herkunft_passt(origin: str | None, host: str | None) -> bool:
    """Kommt der schreibende Aufruf von dieser Seite?

    Ohne diese Pruefung kann eine beliebige Webseite, die im selben Browser
    offen ist, einen POST auf /api/categorize schicken -- Formulardaten loesen
    keine Vorabanfrage aus. Lesen kann sie die Antwort nicht, eine Kategorie
    umbiegen schon.

    FEHLT der Origin-Kopf, wird durchgelassen: Aufrufe aus der Kommandozeile
    und aus Tests haben keinen. Der Angriff, um den es geht, kommt aus einem
    Browser, und der setzt ihn immer.
    """
    if not origin:
        return True
    if not host:
        return False
    return origin.split("//", 1)[-1].rstrip("/") == host
