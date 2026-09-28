# Änderungen

Was sich von Version zu Version ändert, in Worten für die, die die App
benutzen. Neues kommt unter **Unveröffentlicht**; `werkzeuge/release.py`
macht daraus beim Veröffentlichen die neue Version.

## Unveröffentlicht

## 0.3.0 – 2026-09-29

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
- **Ziele** sind von Anfang an nicht leer: Der Notgroschen ist vorgegeben,
  mit drei Monaten Gehalts-Untergrenze, bis du einen eigenen Betrag
  einträgst. Die Rentenlücke steht immer da; ohne Geburtsdatum sagt sie, was
  fehlt.
- **Teilzeit**: Anteil und Beginn stehen wieder in den Annahmen. Die Klammer
  „Teilzeit“ auf **Planung** gibt es immer, auch frisch installiert; sie ist
  von Haus aus aus und schaltet die Teilzeit ein. Werte, die du in der
  Klammer einträgst, gehen vor; leer gelassen gelten die Annahmen.
- Neue Seite **Renten** (unter Verträge): gesetzliche Rente und Policen
  anlegen und pflegen – Betrag laut Schreiben (nominal, bei mehreren
  Szenarien der mit 7 % Wertentwicklung), Bezug ab, Stand. Bisher ließen sich
  nur vorhandene ändern. Der Monatsabschluss prüft nur noch, ob das Schreiben
  aktuell ist, und führt dorthin.
- **Effektivkosten** je Rente oder Police. Liegen sie über deiner Schwelle
  (Annahmen, Vorgabe 1,3 %), steht ein Warnzeichen daneben.
- **Steuern in der Hochrechnung**, bewusst vorsichtig: Verkäufe aus dem
  Depot zahlen 26,375 % auf ihren Gewinnanteil (bisher gar nichts), ohne
  Teilfreistellung – auch bei der Vorabpauschale. Auf Kapitalauszahlungen
  von Policen geht derselbe Abzug wie auf Renten. Was vereinfacht ist, steht
  auf der Hochrechnung.
- Geteilt: **Budgettöpfe** statt Projekte – derselbe Inhalt, ein passenderer Name.
- **Allgemeine Regeln** kommen mit: rund vierzig Regeln für Supermärkte,
  Tankstellen, Streaming, Mobilfunk, Steuern und mehr. Wer neu anfängt, muss
  nicht jede Buchung von Hand zuordnen. Eigene Regeln gehen immer vor; unter
  **Regeln** lässt sich die Grundschicht abschalten, und vorher steht dort, wie
  viele offene Buchungen sie zuordnen würde.
- **Regelvorschläge über Claude** (Regeln → Vorschläge über Claude): Die App
  stellt die offenen Buchungstexte zusammen – ohne Beträge, IBANs und andere
  Nummern, einzeln abwählbar – und kopiert einen fertigen Auftrag. Die Antwort
  von Claude (auch der kostenlosen Version) fügst du ein; die App prüft jede
  Zeile gegen deine Kategorien und dein Hauptbuch, übernommen wird nur, was
  du anhakst. Die App selbst schickt nichts ins Netz.
- Depots haben im Monatsabschluss ein Feld **davon Gewinn** (wie in der
  Depot-App). Die Hochrechnung versteuert damit auch den Gewinn, der heute
  schon im Depot steckt; ohne Angabe gilt der Wert als Einstand.
- Die Gehalts-Untergrenze steht nur noch unter **Annahmen**, nicht mehr
  zusätzlich auf **Planung**.
- Die Entnahmerate ist aus den Annahmen verschwunden: Gerechnet wurde mit ihr
  nirgends. Die Rentenlücke rechnet mit Lebenserwartung, Rendite und
  „Vermögen aufbrauchen“.

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
