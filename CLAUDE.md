# finance

Lokales Finanzwerkzeug: Python 3.12, SQLite, Typer-CLI `finctl`, FastAPI-Dashboard.
Start: `.venv/bin/finctl serve`, dann http://127.0.0.1:8765.

## Feste Rahmen

- Alles bleibt lokal; Daten verlassen den Rechner nicht.
- Parser und Betrieb laufen ohne KI. KI schreibt nur Regeln und Code.
- Geld in Cent, negativ = raus, positiv = rein.
- Handentscheidungen leben in `config/*_custom.yaml` und `config/overrides.yaml`,
  nie nur in der Datenbank.
- Persönliches steht nur in `config/`: keine echten Namen, Verträge, Anbieter
  oder Beträge in Code, Tests, Kommentaren und Vorlagen. Testdaten sind erfunden.
  Grund: Das Werkzeug soll später anderen dienen, und alles, was jetzt im Code
  landet, muss dann einzeln wieder heraus.
- Module bauen Pfade nicht selbst, sondern nehmen sie aus `finctl/pfade.py`:
  `CONFIG_DIR`, `DATA_DIR`, `STATEMENTS_DIR`, `DB_PATH`. Dort hängt alles an
  einer Wurzel, dem Datenordner (`finctl ort` sagt, welcher und warum).
  Seit dem 27.09.2026 liegen `config/` und `data/` nicht mehr im Repository,
  sondern unter `~/Library/Application Support/Finance OS` -- der Vorgabe des
  Systems, wie bei jedem anderen Nutzer. `FINCTL_DATEN` stellt ihn um.
  `tests/test_pfade.py` hält die Zahl der eigenen Pfade auf null.
- Beide Regeln gehören zum Plan, das Werkzeug später weiterzugeben:
  `docs/Generalization_plan.md`.

## Oberfläche

**Vor jeder Änderung an `finctl/web/templates/` `docs/design_conventions.yaml` lesen.**
Dort steht, wie Datumsfelder, Beträge, Farben, Konten, Überschriften, Hinweise,
Suchfelder, Speichern und Texte in dieser App aussehen — mit Begründung.
Eine neue Konvention wird dort eingetragen, nicht nur im Code.
`tests/test_design.py` prüft, was sich maschinell prüfen lässt.

Die Seiten liegen in `finctl/web/routen/`, je Seitengruppe ein Modul mit
eigenem `APIRouter`. `finctl/web/server.py` hält nur noch App, Anmeldung und
das Zusammenhängen; geteilte Bausteine stehen in `finctl/web/basis.py`.

## Tests

`.venv/bin/python -m pytest -q` — alle grün, vor jedem Commit. Zwischendurch
`-m "not langsam"`: Sekunden statt Minuten. Die Tests laufen parallel und in einer
Kopie der Daten (`tests/conftest.py`); nichts davon schreibt in `config/` oder `data/`.

`FINCTL_TESTDATEN=muster` laesst dieselbe Suite auf dem erfundenen Haushalt in
`tests/musterhaushalt/` laufen; ohne `config/` im Projekt geschieht das von
selbst. Beide Laeufe muessen gruen sein. Ein Test nennt deshalb kein Konto,
keinen Kredit und keinen Betrag, sondern liest sie aus `config/` -- Muster:
`_rolle` und `_bindung_im_abschnitt` in `tests/test_web.py`. Braucht ein Test
etwas, das der Musterhaushalt nicht hat, gehoert es dorthin.

`tests/referenz.py pruefen` baut aus `config/` und den Auszuegen neu und vergleicht
Hauptbuch und Seiten mit `data/referenz/`. Ein Umbau darf daran nichts aendern.
Aendert sich ein Ergebnis mit Absicht, wird die Referenz in einem EIGENEN Schritt
neu geschrieben (`schreiben`, die alte wandert nach `data/referenz_archiv/`).

Darin läuft `ruff` mit (`tests/test_lint.py`): null Funde, nicht „wenige".
Was geduldet wird, gehört mit Begründung in `[tool.ruff.lint]` in
`pyproject.toml`, nicht in eine Ausnahmeliste im Test.

`tests/test_namen.py` steht seit dem 23.09.2026 bei **null Fundstellen** und
ist damit eine Sperre, keine Ratsche: Jede neue Stelle mit einem echten Namen
lässt ihn umfallen, es gibt keinen Restposten mehr, in dem sie untergeht.
Braucht ein Test eine Kennung, liest er sie zur Laufzeit aus `config/` --
Muster: `_ein_objekt` in `tests/test_web.py`, `_geteilte_regeln` in
`tests/test_rules.py`.

Die Liste der Namen steht in `config/verbotene_namen.yaml` im Datenordner,
nicht im Repository -- sie besteht aus genau den Namen, die sie verbietet.
`tests/test_leck.py` nimmt das Hauptbuch selbst als Liste: keine Gegenpartei,
kein Betrag mit Cent, keine lange Nummer aus `config/` und niemand, der einen
Anteil traegt, darf im Code, in Tests oder Doku stehen. Beispiele sind erfunden
und rund; Befunde und Notizen gehoeren nach `notizen/` im Datenordner.

`ZU_VERZWEIGT` in `tests/test_lint.py` führt die Funktionen mit einem
Verzweigungsgrad über 10. Die Liste darf kürzer werden und nichts
dazubekommen — wie `ausnahmen`/`offen` in `docs/design_conventions.yaml`.
