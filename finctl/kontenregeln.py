"""Die Regeln zwischen den Konten -- an einer Stelle gelesen und geschrieben.

Untergrenze, Deckel, Abraeumen, Auffuellen, Dauerauftrag, Rolle und Zweck
standen in `config/forecast.yaml` und waren bis auf die Untergrenze nur im
Texteditor aenderbar. Sie bewegen jede Zahl auf /konten: ob ein Konto
aufgefuellt wird, ob Geld aufs Tagesgeld geht, wo der Tiefpunkt liegt. Genau
deshalb stand auf der Seite ein Absatz, der erklaerte, was Zufluss und Deckel
bedeuten -- Prosa als Ersatz fuer Felder, die es nicht gab.

Sie sind keine Prognoseannahme. Eine Annahme sagt, wie die Welt sich
entwickelt (Inflation, Rendite, Gehalt); eine Kontenregel sagt, wie DU dein
Geld zwischen deinen eigenen Konten verschiebst. Deshalb stehen sie auf der
Kontenseite und nicht bei den Annahmen.

GESPEICHERT WIRD NUR DIE ABWEICHUNG, in `config/forecast_custom.yaml`. Die
Basisdatei traegt die Begruendung zu jedem Wert -- warum der Deckel dort
liegt, warum ein Konto nicht aufgefuellt wird -- und ein Dashboard, das sie
neu schreibt, loescht das beim ersten Klick.
"""

from __future__ import annotations

from finctl.pfade import CONFIG_DIR

BASIS = "forecast.yaml"
EIGEN = "forecast_custom.yaml"

#: Was eine Kontenregel ausmacht, und wie der Wert gelesen wird.
FELDER: dict[str, str] = {
    "role": "text",
    "note": "text",
    "floor_cents": "cents",
    "ceiling_cents": "cents",
    "sweep_to": "text",
    "auffuellen": "schalter",
    "floor_from_loan": "schalter",
}


#: Was gilt, wenn die Basisdatei zu einem Schalter nichts sagt. Ohne diese
#: Standards zaehlte "auffuellen: true" als Abweichung von "steht da nicht"
#: und wurde gespeichert, obwohl sich nichts geaendert hat.
STANDARD: dict[str, object] = {"auffuellen": True, "floor_from_loan": False}


def _gleich(feld: str, wert, basis) -> bool:
    """Ist der Wert derselbe wie in der Basisdatei?

    Text wird ohne Rand verglichen: eine Notiz aus der YAML endet auf einem
    Zeilenumbruch, dieselbe Notiz aus dem Formular nicht -- ungetrimmt waere
    jedes Speichern eine Abweichung.
    """
    if FELDER.get(feld) == "text":
        return str(wert or "").strip() == str(basis or "").strip()
    if FELDER.get(feld) == "schalter":
        return bool(wert) == bool(basis if basis is not None else STANDARD.get(feld))
    return wert == basis


def _lesen(name: str) -> dict:
    import yaml

    pfad = CONFIG_DIR / name
    if not pfad.exists():
        return {}
    return yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}


def overlay() -> dict:
    """Was von der Kontenseite aus gesetzt wurde."""
    spec = _lesen(EIGEN)
    return {"account_roles": spec.get("account_roles") or {},
            "account_budgets": spec.get("account_budgets") or {}}


def zusammenfuehren(basis: dict, eigen: dict | None = None) -> dict:
    """forecast.yaml mit dem Overlay -- Konto fuer Konto, Feld fuer Feld.

    Eine Stelle statt sechs: Rolle, Untergrenze, Deckel, Abraeumen, Auffuellen
    und Budget werden an verschiedenen Punkten in `ops` gelesen, und ein
    Overlay, das nur an einem davon ankommt, aendert die Anzeige, ohne die
    Rechnung zu aendern.
    """
    if eigen is None:
        eigen = overlay()
    rollen = {k: dict(v or {}) for k, v in (basis.get("account_roles") or {}).items()}
    for konto, felder in (eigen.get("account_roles") or {}).items():
        rollen.setdefault(konto, {}).update(
            {k: v for k, v in (felder or {}).items() if k in FELDER})
    budgets = dict(basis.get("account_budgets") or {})
    for konto, cents in (eigen.get("account_budgets") or {}).items():
        if cents in (None, ""):
            budgets.pop(konto, None)
        else:
            budgets[konto] = int(cents)
    return {**basis, "account_roles": rollen, "account_budgets": budgets}


def wirksam() -> dict:
    """Die geltende Kontenkonfiguration: Basis plus Overlay."""
    return zusammenfuehren(_lesen(BASIS))


def basiswerte(konto: str) -> dict:
    """Was ohne Overlay gaelte -- fuer die Anzeige „ueberschrieben, Basis …"."""
    spec = _lesen(BASIS)
    rolle = dict((spec.get("account_roles") or {}).get(konto) or {})
    rolle["budget_cents"] = (spec.get("account_budgets") or {}).get(konto)
    return rolle


def setzen(konto: str, felder: dict) -> dict:
    """Ein Konto aendern. Was der Basisdatei entspricht, wird nicht gespeichert.

    Gibt die wirksame Regel des Kontos zurueck.
    """
    import yaml

    spec = _lesen(EIGEN)
    rollen = spec.get("account_roles") or {}
    budgets = spec.get("account_budgets") or {}
    basis = _lesen(BASIS)
    basis_rolle = (basis.get("account_roles") or {}).get(konto) or {}
    basis_budget = (basis.get("account_budgets") or {}).get(konto)

    eintrag = dict(rollen.get(konto) or {})
    for feld, wert in felder.items():
        if feld == "budget_cents":
            if wert is None or wert == basis_budget:
                budgets.pop(konto, None)
            else:
                budgets[konto] = int(wert)
            continue
        if feld not in FELDER:
            continue
        # Gleich der Basis heisst: keine Abweichung, also kein Eintrag. Sonst
        # steht der alte Wert doppelt da und eine Korrektur in forecast.yaml
        # kommt nie mehr an.
        if wert is None or _gleich(feld, wert, basis_rolle.get(feld)):
            eintrag.pop(feld, None)
        else:
            eintrag[feld] = wert.strip() if FELDER[feld] == "text" else wert
    if eintrag:
        rollen[konto] = eintrag
    else:
        rollen.pop(konto, None)

    spec["account_roles"] = rollen
    spec["account_budgets"] = budgets
    pfad = CONFIG_DIR / EIGEN
    if not any(spec.values()):
        # Keine Abweichung mehr: die Datei verschwindet, statt als leere Huelle
        # stehen zu bleiben und zu behaupten, hier sei etwas gesetzt.
        pfad.unlink(missing_ok=True)
        return (basis.get("account_roles") or {}).get(konto) or {}
    pfad.write_text(
        "# Kontenregeln, die auf /konten gesetzt wurden.\n"
        "#\n"
        "# Ueberschreibt forecast.yaml je Konto -- Rolle, Zweck, Untergrenze,\n"
        "# Deckel, Abraeumen, Auffuellen, Dauerauftrag. Gespeichert wird nur,\n"
        "# was von der Basisdatei abweicht: dort steht die Begruendung zu\n"
        "# jedem Wert, und die soll ein Klick nicht loeschen.\n\n"
        + yaml.safe_dump({k: v for k, v in spec.items() if v},
                         allow_unicode=True, sort_keys=True),
        encoding="utf-8")
    return (zusammenfuehren(basis, {"account_roles": rollen,
                                    "account_budgets": budgets})
            .get("account_roles", {}).get(konto) or {})
