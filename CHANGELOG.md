# Änderungen

Was sich von Version zu Version ändert, in Worten für die, die die App
benutzen. Neues kommt unter **Unveröffentlicht**; `werkzeuge/release.py`
macht daraus beim Veröffentlichen die neue Version.

## Unveröffentlicht

- **Prognose, tiefster Stand im Monat:** Eine Planzeile, die eine
  fortgeschriebene Position derselben Kategorie aufhebt (etwa „keine
  Zinsen“ gegen die Zinsen), steht jetzt am selben Tag wie diese Position.
  Bisher lag sie am anderen Ende des Monats, und der tiefste Stand fiel
  jeden Monat um ihren Betrag zu tief aus.
- **Wegfall** nimmt in der Kontoprognose genau heraus, was die Prognose für
  die Reihe bucht – auch bei Jahresposten und auf jedem Konto, auf dem sie
  liegt –, statt eines Durchschnitts der letzten zwölf Monate.
- Neu an der Wegfall-Zeile auf /planung: der Haken **„nur Kontoprognose“**.
  Für Ausgaben, die nur das Konto wechseln: Die Reihe verlässt das Konto
  der Zeile, die Jahresrechnung bleibt unberührt.
- `discontinued` in `forecast.yaml` gibt es nicht mehr. Was durch eine
  Entscheidung aufhört, steht als Wegfall auf /planung oder als Kündigung
  auf /abos. Wer `discontinued` benutzt hat, trägt diese Posten dort ein.

## 0.4.0 – 2026-10-02

- **DKB-CSV** ist an einem echten Export geprüft und berichtigt: Ganze
  Beträge ohne Komma (`2.000`) wurden als 2,00 € gelesen, jetzt als
  2.000,00 €. Der Zeitraum kommt aus der Zeile „Zeitraum:“.
- Die DKB nennt den Kontostand vom Tag des Exports. Deshalb: den Export
  **bis heute** wählen. Die Buchungen von heute bleiben für den nächsten
  Export, der an diesem Tag beginnt; ein Export, der früher endet, wird mit
  einer Meldung abgelehnt.
- Mandatsreferenz und Gläubiger-ID eines CSV-Exports erreichen die Regeln,
  wie im PDF-Auszug.
- Auf dem Mac steht Finance OS jetzt **in der Menüleiste** (ein Euro im
  Kreis): „Finance OS öffnen“ holt die Seite zurück, wenn der Tab zu ist,
  „Beenden“ beendet die App.
- **Rückblick → Fluss** zeigt auf Wunsch einen einzelnen Monat statt des
  ganzen Jahres; die Knoten führen zu den Buchungen dieses Monats.
- **Rückblick → Vorjahr** zeigt jedes Jahr nebeneinander, mit der
  Veränderung gegen das Jahr davor. Verglichen werden nur Monate, die in
  beiden Jahren abgeschlossen sind: kein Teiljahr gegen ein volles, und der
  laufende Monat nie.
- **Transaktionen** und **Buchungen zuordnen** haben eine Zeile „Zeitraum“
  (dieser Monat, letzter Monat, 90 Tage, dieses Jahr, letztes Jahr). Jede
  Wahl bei Zeitraum, Konto und Quelle sagt vorher, wie viele Buchungen übrig
  blieben; was nichts übrig ließe, ist blass.
- **Monatsabschluss**: Einnahmen, Ausgaben und Saldo führen zum
  Vorjahresvergleich. Neu ist „Bald fällig“ aus Verträgen und Krediten –
  größere Zahlungen 30 Tage vorher, das Ende einer Zinsbindung ein Jahr
  vorher –, als Kalenderdatei (.ics) je Frist oder alle zusammen.
- Fehlt einem Konto der Vormonat, nennt der Monatsabschluss trotzdem, bis
  wann der letzte Auszug reicht.
- Die Diagramme auf **Konten** zeichnet jetzt der Server: mit Legende,
  Tooltip (auch per Tastatur) und einer Tabelle mit denselben Werten.
- **CSV-Exporte** reichen so weit, wie die Bank im Kopf der Datei sagt,
  nicht nur bis zur letzten Buchung – ein ganzer Monat gilt damit als
  vollständig. Passt der genannte Zeitraum nicht zu den Buchungen, gelten
  die Buchungen, mit einer Meldung.
- **Sparda**: Der CSV-Export wird über das Atruvia-Profil eingelesen.
- **Scalable**: Auszüge mit einer Buchung am Monatsersten wurden abgelehnt,
  weil der Anfangsstand sie schon enthält. Jetzt passen sie.
- Kürzere Seiten: Monatsabschluss, Rückblick und Transaktionen tragen
  keine erklärenden Unterzeilen mehr; wann ein Stand aktuell ist, steht
  beim Haken.

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
