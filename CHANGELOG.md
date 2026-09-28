# Änderungen

Was sich von Version zu Version ändert, in Worten für die, die die App
benutzen. Neues kommt unter **Unveröffentlicht**; `werkzeuge/release.py`
macht daraus beim Veröffentlichen die neue Version.

## Unveröffentlicht

- Die Einrichtung sagt oben in drei Sätzen, wozu die App da ist, und
  verweist ins Handbuch. Derselbe Text steht oben im Handbuch.
- Rückblick: Der Fluss ist die erste Ansicht, danach Alle Kategorien,
  Fixkosten, Vorjahr.
- **Beenden** und die Speichern-Knöpfe der Einrichtung sind gefüllt und gehen
  nicht mehr unter.
- Kredite: **Neues Annuitätendarlehen** statt „Neuer Ratenkredit“ – gerechnet
  wird genau das, feste Rate mit wachsendem Tilgungsanteil.
- Die Einrichtung weist darauf hin, die Annahmen durchzugehen, solange noch
  keine angepasst ist – sie sind mitgelieferte Schätzungen.
- Neue Startwerte: Tagesgeld 2,5 %, Depot 7 % im Jahr (vorher je 5 %). Gilt
  für neue Installationen; eigene Annahmen bleiben, wie sie sind.
- Handbuch: Die Auszüge liegen in `data/statements/`, je Konto ein Ordner.

## 0.2.1 – 2026-09-27

- Eine Sicherung lässt sich in der App wiederherstellen: Einrichtung → **Aus
  einer Sicherung wiederherstellen**. Aus dem Sicherungsordner oder als
  hochgeladene Datei, etwa auf einem neuen Rechner. Die Sicherung landet in
  einem neuen Ordner neben dem bisherigen; umgeschaltet wird erst mit
  **Diesen Stand verwenden**, und der alte Ordner bleibt unverändert.
- Beim ersten Öffnen – solange es kein Konto gibt – startet die App in der
  Einrichtung statt im Monatsabschluss.

## 0.2.0 – 2026-09-27

- Die App gibt es zum Doppelklicken: `.dmg` für den Mac (Apple-Chip), `.exe`
  für Windows. Sie richtet sich beim ersten Start selbst ein und lässt sich
  über **Beenden** oben rechts schließen.
- Die Versionsnummer steht unten auf jeder Seite.
- Neue Akzentfarbe: Petrol statt Blau.
- Lizenz: PolyForm Strict 1.0.0 – privat und nicht-kommerziell nutzbar. Die
  Lizenzen der mitgelieferten Bibliotheken liegen als `DRITTLIZENZEN.txt` bei.
- Hinweis in der Einrichtung: Die App rechnet, sie berät nicht.

## 0.1.0 – 2026-09-27

Die erste Version, die auf einem anderen Rechner laufen kann.

- Konten, Auszüge einlesen (PDF-Profile einiger Banken, CSV jeder Bank über die
  Spaltenzuordnung), Kategorien und Regeln, Rückblick.
- Module zum Zuschalten: Verträge, Energie, Geteiltes, Kredite, Immobilien,
  Steuer, Prognose, Ziele.
- Hochrechnung bis zur Lebenserwartung mit Renten und festem Ziel Rentenlücke.
- Die Daten liegen im Datenordner des Systems, getrennt vom Programm.
