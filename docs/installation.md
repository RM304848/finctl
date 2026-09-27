# Finance OS installieren

Für alle, die die App benutzen, nicht entwickeln. Alles läuft auf deinem
Rechner; deine Daten gehen nirgendwohin.

## Herunterladen

Unter **Releases** die neueste Version öffnen und die passende Datei laden:

- **Mac:** `FinanceOS-<version>-mac.dmg`
- **Windows:** `FinanceOS-<version>-windows.exe`

## Mac

1. Die `.dmg` öffnen und **Finance OS** in den Ordner **Programme** ziehen.
2. Beim ersten Start meldet macOS, die App sei von einem unbekannten Entwickler.
   Das stimmt: sie ist nicht bei Apple registriert. Einmal freigeben:
   **Systemeinstellungen → Datenschutz & Sicherheit**, ganz unten
   „Finance OS wurde blockiert“ → **Dennoch öffnen**.
3. Danach startet sie normal per Doppelklick.

## Windows

1. Die `.exe` an einen festen Ort legen, etwa `Dokumente\Finance OS`, und
   doppelklicken.
2. Windows zeigt „Der Computer wurde durch Windows geschützt“. Auf
   **Weitere Informationen** und dann **Trotzdem ausführen** klicken.
3. Ein schwarzes Fenster öffnet sich und bleibt offen: das ist die App.
   Schließt du es, ist sie beendet.

## Benutzen

Nach dem Start öffnet sich der Browser mit der App. Beim ersten Mal – und
solange noch kein Konto angelegt ist – öffnet sie die **Einrichtung**, die
durch alles Nötige führt. Beenden: **Beenden** oben rechts in der App.

Deine Daten liegen getrennt vom Programm, **nicht** dort, wo die `.exe` oder
die App liegt:

- Mac: `~/Library/Application Support/Finance OS`
- Windows: `C:\Users\<Name>\AppData\Roaming\Finance OS`

Das Programm kannst du deshalb verschieben, löschen oder durch eine neue
Version ersetzen, ohne dass etwas verloren geht. Wer die Daten lieber woanders
hat (etwa unter „Dokumente“), trägt den Ordner in der Einrichtung unter
**1 — Wo die Daten liegen** ein, beendet die App und öffnet sie neu.
Vorhandene Daten zieht die App dabei nicht mit um: am besten gleich beim
ersten Start entscheiden.

Sichern geht über den Knopf **Sichern** im Monatsabschluss; das Ziel legst du
in der Einrichtung fest.

## Neuer Rechner oder etwas ist schiefgegangen

App installieren und öffnen, dann in der Einrichtung **Aus einer Sicherung
wiederherstellen**: eine `finance-os_….tar.gz` aus deinem Sicherungsordner
hochladen. Sie wird in einen neuen Ordner ausgepackt und geprüft; mit
**Diesen Stand verwenden**, Beenden und erneutem Öffnen arbeitet die App mit
ihr. Kontoauszüge sind nicht in der Sicherung – für den Betrieb braucht es
sie nicht.

## Neue Version

Die neue Datei herunterladen und die alte ersetzen – auf dem Mac in
**Programme** überschreiben, unter Windows die alte `.exe` löschen. Deine
Daten bleiben, wo sie sind. Was sich geändert hat, steht unter Releases.

## Was die App ist und was nicht

Sie rechnet, sie berät nicht: Hinweise zu Steuern, Renten und Anlagen sind
Rechenhilfen ohne Gewähr und ersetzen keine Steuer- oder Anlageberatung.

Benutzen darfst du sie privat und für andere nicht-kommerzielle Zwecke
(Lizenz: PolyForm Strict 1.0.0). Weitergeben, verändern oder kommerziell
nutzen nur mit Zustimmung. Die Lizenzen der mitgelieferten Bibliotheken
stehen in `DRITTLIZENZEN.txt` neben der App (Mac: in der `.dmg`) und unter
Releases.

## Etwas geht nicht

Unter **Issues → New issue → Rückmeldung** beschreiben, was passiert ist.
Bitte ohne echte Beträge, Namen oder Auszüge.
