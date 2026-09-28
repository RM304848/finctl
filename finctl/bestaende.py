"""Bestaende: getippt, wo kein Auszug etwas weiss -- belegt, wo einer es tut.

KONTEN MIT EINGELESENEN AUSZUEGEN KOMMEN NUR AUS DEM AUSZUG -- die
Girokonten und das Tagesgeld bei Scalable. Ihr Wert in balances.yaml war ein
getippter Livestand -- "3.487,38 Auszugsende + 7.870 Verkaufserloes" --, und
ein getippter Stand eines Kontos, dessen Auszuege eingelesen werden, ist eine
zweite Wahrheit neben der belegten. /konten rechnete vom Auszug, /ziele vom
getippten Wert, und beide hiessen "Stand".

Wert und Stichtag eines solchen Kontos sind deshalb der Saldo am STICHTAG: dem
letzten Monatsende, das die Auszuege ALLER Konten ganz abdecken -- derselbe
Tag, an dem /konten beginnt. Wie weit der neueste Auszug schon reicht, steht
daneben, zaehlt aber nicht. Ueberschreiben laesst sich beides nicht.

Welche das sind, sagt nicht die Art (giro, tagesgeld), sondern ob das Konto
geparst wird: Scalable ist Tagesgeld und hat trotzdem Auszuege, und ein
getippter Stand von 64.117 neben dem belegten war dieselbe zweite Wahrheit.
Alle anderen Positionen -- Depots, Krypto, Renten, Konten ohne Parser --
bleiben genannt und ueberschreibbar wie bisher.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from finctl.pfade import CONFIG_DIR


def geparste_konten(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT id FROM accounts WHERE ingest_mode = 'parsed'")}


def aus_auszug(item: dict, geparst: set[str]) -> bool:
    """Kommt diese Position aus dem Auszug -- und ist damit nicht ueberschreibbar?"""
    return str(item.get("account_id") or "") in geparst


def auszugsstaende(conn: sqlite3.Connection, konten: list[str]) -> dict[str, dict]:
    """Je Konto: Saldo am Stichtag, der Stichtag, und bis wann der Auszug reicht."""
    from finctl.forecast import stichtag as _st

    stichtag = _st.stichtag(conn)
    out = {}
    for konto in konten:
        bis = conn.execute(
            "SELECT MAX(period_end) FROM statements WHERE account_id = ? "
            "AND status = 'imported'", (konto,)).fetchone()[0]
        out[konto] = {
            "cents": _st.saldo_zum(conn, konto, stichtag) if bis else None,
            "as_of": stichtag.isoformat() if bis else "",
            "auszug_bis": bis or "",
        }
    return out


def kennung(name: str) -> str:
    """Der Schluessel einer Verpflichtung: ihr Name, kleingeschrieben."""
    import re

    return re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")


def overlay(pfad: Path | None = None) -> dict:
    """Was auf der Bestaendeseite eingetragen wurde.

    An einer Stelle gelesen statt in jedem Aufrufer: vier Seiten lasen die
    Datei je fuer sich, und der Monatsabschluss las sie gar nicht -- dort
    zaehlte ein gerade abgelegter Stand deshalb nicht als abgelegt.
    """
    import yaml

    quelle = pfad or CONFIG_DIR / "balances_custom.yaml"
    spec = (yaml.safe_load(quelle.read_text(encoding="utf-8")) or {}) \
        if quelle.exists() else {}
    return {"overrides": spec.get("overrides") or {},
            "verpflichtungen": spec.get("verpflichtungen") or {}}


def verpflichtungen(basis: dict, eigene: dict | None = None) -> list[dict]:
    """Die Verbindlichkeiten aus balances.yaml, mit den eigenen Aenderungen.

    Geschluesselt ueber den Namen, weil die Basisdatei eine Liste fuehrt und
    eine Liste keinen Schluessel hat. `geloescht` streicht eine Zeile, ohne
    sie aus der Basisdatei zu nehmen -- eine getilgte Schuld verschwindet,
    ihre Begruendung bleibt.
    """
    eigene = {kennung(k): v for k, v in (eigene or {}).items()}
    out, gesehen = [], set()
    for eintrag in basis.get("obligations") or []:
        k = kennung(eintrag.get("name") or "")
        gesehen.add(k)
        eigen = eigene.get(k) or {}
        if eigen.get("geloescht"):
            continue
        out.append({**eintrag, **{f: eigen[f] for f in ("cents", "note", "name")
                                  if f in eigen},
                    "kennung": k, "eigen": bool(eigen)})
    for k, eigen in eigene.items():
        if k in gesehen or not eigen or eigen.get("geloescht"):
            continue
        out.append({"name": eigen.get("name") or k, "cents": eigen.get("cents"),
                    "note": eigen.get("note", ""), "kennung": k, "eigen": True,
                    "neu": True})
    return out


def zusammenfuehren(conn: sqlite3.Connection, basis: dict,
                    overrides: dict | None = None,
                    eigene_verpflichtungen: dict | None = None) -> dict:
    """balances.yaml mit den Overlays -- und Konten mit Auszug aus dem Auszug.

    Eine Stelle statt vier: /ziele, /annahmen, der Monatsabschluss und die
    Bestaendeseite fuehrten dieselbe Zusammenfuehrung je fuer sich. Ein
    Overlay fuer ein Konto mit Auszug wird ignoriert.
    """
    gespeichert = overlay()
    if overrides is None:
        overrides = gespeichert["overrides"]
    if eigene_verpflichtungen is None:
        eigene_verpflichtungen = gespeichert["verpflichtungen"]
    overrides = {str(k): v for k, v in (overrides or {}).items()}
    items = basis.get("balances") or []
    geparst = geparste_konten(conn)
    staende = auszugsstaende(conn, [i["account_id"] for i in items
                                    if aus_auszug(i, geparst)])
    out = []
    for item in items:
        row = dict(item)
        over = overrides.get(row.get("account_id") or row.get("name") or "") or {}
        if aus_auszug(row, geparst):
            stand = staende[row["account_id"]]
            row.update(cents=stand["cents"], as_of=stand["as_of"],
                       auszug_bis=stand["auszug_bis"], aus_auszug=True)
        elif over:
            row["cents"] = over.get("cents", row.get("cents"))
            if over.get("as_of"):
                row["as_of"] = over["as_of"]
            # Wie viel vom Depotwert Gewinn ist: gehoert zum Wert und kommt
            # mit ihm aus demselben Blick in die Depot-App.
            if "gewinn_cents" in over:
                row["gewinn_cents"] = over["gewinn_cents"]
        # DIE NOTIZ GILT AUCH FUER KONTEN MIT AUSZUG. Der Wert ist belegt, die
        # Notiz ist eine Aussage des Eigentuemers -- "davon gehoert ein Teil
        # jemand anderem" steht in keinem Auszug und muss trotzdem aenderbar
        # sein, sonst lebt sie fuer immer in einer Datei.
        if "note" in over:
            row["note"] = over["note"]
        out.append(row)
    return {**basis, "balances": out,
            "obligations": verpflichtungen(basis, eigene_verpflichtungen)}
