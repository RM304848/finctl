# Prüfliste: erster echter Lauf auf Windows

Stand 25.09.2026. Abzuarbeiten auf einem Windows-Rechner, ohne dass jemand
mitliest. Was hier steht, ist alles, was sich auf macOS **nicht** prüfen ließ.

Geprüft wird **das Paket**, nicht das Repository: Genau das bekommt jemand,
dem du das Werkzeug gibst. Auf dem Mac ist der Lauf am 25.09.2026
durchgespielt worden — alles unten hat dort funktioniert. Weicht etwas ab,
liegt es an Windows.

**Zeitbedarf:** Teil A rund 20 Minuten, Teil B rund 15.

---

## Vorher: zwei Dateien auf den Windows-Rechner

Gebraucht werden genau zwei: das **Rad** (`finctl-0.1.0-py3-none-any.whl`) und
diese Liste. Wie sie dorthin kommen, ist egal — Cloud-Ordner, USB-Stick, Mail
an dich selbst. Über einen Cloud-Ordner geht es auf dem Mac etwa so:

```bash
cp dist/finctl-0.1.0-py3-none-any.whl <cloud-ordner>/
cp docs/windows_pruefliste.md <cloud-ordner>/
```

**Wo sie auf dem Windows-Rechner landen, musst du nicht vorher wissen** — das
wird unten einmal eingetragen. Ein anderer Rechner hat OneDrive woanders oder
gar nicht.

**Das Rad enthält keine Daten von dir** — kein `config/`, keine Datenbank,
keine Auszüge. Nur Programm, Vorlagen, Startdateien und das Handbuch.

---

## Alles mitschreiben

**PowerShell öffnen** (Windows Terminal, nicht `cmd`) und als Erstes:

```powershell
Start-Transcript -Path "$HOME\Desktop\finctl-windows.txt"
$PSVersionTable.PSVersion
[Console]::OutputEncoding.WebName
```

## Einmal festlegen, wo was liegt

Alles Weitere benutzt diese drei Namen. Trag ein, was auf DIESEM Rechner
stimmt — der Desktop ist nur ein Vorschlag:

```powershell
$Rad    = "$HOME\OneDrive\finctl-0.1.0-py3-none-any.whl"
$Arbeit = "$HOME\Desktop\finctl-probe"
$Sicher = "$HOME\Desktop\finctl-sicherung"
```

Wo OneDrive auf diesem Rechner liegt, sagt `$env:OneDrive`. Liegt das Rad
woanders — Downloads, USB-Stick —, trag einfach das ein. Prüfen:

```powershell
Test-Path $Rad
```

**Muss `True` sagen.** Sagt es `False`, stimmt der Pfad nicht, und alles
Weitere wäre eine Fehlersuche am falschen Ende.

> Diese drei Namen gelten nur in DIESEM Fenster. Schließt du PowerShell
> zwischendurch, setze sie neu — sonst laufen die Befehle ins Leere.

Am Ende `Stop-Transcript`. Die eine Datei vom Desktop reicht mir — darin steht
jeder Befehl mit seiner Ausgabe.

Die beiden Zeilen danach sind wichtig: **PowerShell 5.1 benutzt die
Codepage des Fensters, PowerShell 7 benutzt UTF-8.** Wenn Umlaute kaputt
aussehen, entscheidet diese Zeile, ob es am Programm liegt oder am Fenster.

---

# Teil A — ohne eigene Daten

## A1 Python

```powershell
py -0
```

Es braucht **3.12 oder neuer**. Fehlt es:

```powershell
winget install Python.Python.3.12
```

Danach PowerShell neu öffnen.

- [ ] Python 3.12+ vorhanden

## A2 Installieren

```powershell
mkdir $Arbeit -Force
cd $Arbeit
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install $Rad
```

> Der Punkt vor `\.venv` ist Pflicht. PowerShell führt nichts aus dem aktuellen
> Ordner aus, wenn der Pfad nicht mit `.\` anfängt.

**Erwartet:** `Successfully installed finctl-0.1.0 ...` und eine Liste von
rund zwölf Abhängigkeiten.

**Worauf zu achten ist:** Zieht `pip` irgendwo einen **Compiler**? Dann steht
da `building wheel for ...` statt `Downloading ... .whl`. Auf macOS gab es
fertige Pakete für alles; sollte Windows für `pdfplumber`, `pillow` oder
`cryptography` bauen wollen, notiere den Namen — das wäre eine Hürde für
jeden, dem du das Werkzeug gibst.

- [ ] Installation ohne Compiler durchgelaufen

## A3 Kodierung — der Test, der auf macOS nicht möglich war

```powershell
.\.venv\Scripts\finctl.exe --help
```

**Erwartet:** die Befehlsliste. Darin stehen deutsche Sätze mit Umlauten und
Sonderzeichen, unter anderem:

| Befehl | Text, der stimmen muss |
|---|---|
| `treffer` | Wie gut h**ä**tte die Prognose getroffen? |
| `groups` | Vorg**ä**nge: was zu einer Sache geh**ö**rt. |

**Wenn dort `hÃ¤tte` oder `h?tte` steht:** genau das ist der Fund. Schreib
dazu, was `[Console]::OutputEncoding.WebName` oben gemeldet hat.

- [ ] Umlaute in `--help` korrekt

## A4 Wo die Daten landen

```powershell
.\.venv\Scripts\finctl.exe ort
```

**Erwartet** — vier Zeilen, sinngemäß:

```
Datenordner: C:\Users\<du>\AppData\Roaming\Finance OS
  weil: Vorgabe dieses Systems
  Zeiger: C:\Users\<du>\AppData\Roaming\Finance OS\ort.txt (nicht vorhanden)
  Dort liegt noch kein config/ -- `finctl init` legt es an.
```

Zwei Dinge sind hier der Punkt: **Backslashes** und das **Leerzeichen** in
„Finance OS". Steht dort ein Mischmasch aus `/` und `\`, oder bricht der Pfad
am Leerzeichen — notieren.

- [ ] Pfad sieht aus wie ein Windows-Pfad
- [ ] `%APPDATA%` und nicht `Library/Application Support`

## A5 Erster Start

```powershell
.\.venv\Scripts\finctl.exe init
```

**Erwartet:** `Initialised ...\Finance OS\data\finance.db`, darunter **sieben**
Zeilen `config/… angelegt`, zuletzt `Noch kein Konto.`

```powershell
.\.venv\Scripts\finctl.exe validate
```

**Erwartet:** `All invariants hold.`

- [ ] sieben Startdateien angelegt
- [ ] `All invariants hold.`

## A6 Das Dashboard

```powershell
.\.venv\Scripts\finctl.exe serve --ohne-passwort
```

Dann im Browser <http://127.0.0.1:8765> öffnen.

> Fragt die **Windows-Firewall**, ob sie das Netz freigeben soll: **Nein.**
> Der Server hört auf 127.0.0.1, er braucht keine Freigabe. Notiere trotzdem,
> dass gefragt wurde — das würde jeden neuen Nutzer erschrecken.

**Erwartet:** die Monatsabschluss-Seite, oben ein roter Hinweis
„Die Einrichtung ist noch nicht fertig — 3 von 3 Fragen offen."

- [ ] Seite lädt, Hinweis ist da
- [ ] Umlaute im Browser korrekt (Überblick, Rückblick, Vorausschau)

## A7 Die Einrichtung durchklicken

Auf **Einrichtung öffnen →**. Dann der Reihe nach:

1. **Schritt 1 aufklappen.** Im Feld steht schon der Ordner aus A4. Auf
   **Prüfen und eintragen**. Nach einem Moment lädt die Seite neu, Zeile 1
   steht auf **ja**.
2. **Schritt 2 aufklappen.** Den Ordner aus `$Sicher` eintragen — der volle
   Pfad, den `Write-Host $Sicher` ausgibt —, dann **Prüfen und speichern**.
   Der Ordner darf noch nicht existieren; er wird angelegt. Zeile 2 steht
   auf **ja**.
3. **Schritt 3 aufklappen**, **Neues Konto**, ausfüllen:
   Kennung `muster-giro`, Anzeigename `Muster Giro`, Institut `Musterbank`,
   Einlesen auf **summary** stellen. **Anlegen.** Zeile 3 steht auf **ja**.

Zurück auf **Monatsabschluss**: der rote Hinweis ist weg.

- [ ] alle drei Schritte auf „ja"
- [ ] Hinweis verschwunden

## A8 Jede Seite einmal

Im Kopf jede Gruppe aufklappen und **jede** Seite einmal öffnen. Es sind 26.
Keine darf einen Fehler zeigen; leer ist in Ordnung.

Dann das **Handbuch**, das nicht in der Navigation steht: unten auf jeder
Seite **Konfiguration** aufklappen, erste Zeile `HANDBUCH.md#…` anklicken —
oder direkt <http://127.0.0.1:8765/handbuch>.

Kommt dort Text, oder ist die Seite leer? Das Handbuch geht seit heute mit
dem Paket, und Windows ist der erste Ort, an dem sich zeigt, ob es gefunden
wird. Der Anker muss auch stimmen: Der Link springt zum Abschnitt der Seite,
von der du kamst, nicht an den Anfang.

- [ ] alle 26 Seiten ohne Fehler
- [ ] Handbuch zeigt Text, Anker springt richtig

## A9 Sichern

Zurück auf **Monatsabschluss**, Knopf **Sichern**.

**Erwartet:** `Backup geschrieben — <dein $Sicher>\finance-os_….tar.gz`

```powershell
Get-ChildItem $Sicher
```

- [ ] Archiv liegt in `$Sicher`

---

# Teil B — mit einem echten Auszug

**Erst lesen, dann entscheiden:** Hierfür landet **ein echter Kontoauszug**
auf diesem Windows-Rechner. Auf deinem eigenen Rechner ist das unkritisch,
auf einem fremden nicht. Teil A ist auch allein aussagekräftig.

Das ist der einzige Weg, zwei Dinge zu prüfen, die sonst ungetestet bleiben:
den **PDF-Parser** und die **Dateisperre** (`filelock` — der ganze Grund,
warum Windows überhaupt zum Thema wurde).

## B1 Auszug hinlegen

Eine einzelne DKB-Giro-PDF aus `data/statements/dkb-giro/` auf den
Windows-Rechner bringen, dann:

```powershell
# Der Datenordner, den A4 gemeldet hat. Steht dort etwas anderes: hier ändern.
$Daten = "$env:APPDATA\Finance OS"
mkdir "$Daten\data\statements\dkb-giro" -Force
# die PDF dorthin kopieren
```

Auf **Kontenregister** ein Konto anlegen: Kennung `dkb-giro`, Einlesen
**parsed**, Parserprofil **dkb_giro**.

## B2 Einlesen — der Parser

```powershell
.\.venv\Scripts\finctl.exe ingest run
.\.venv\Scripts\finctl.exe categorize
.\.venv\Scripts\finctl.exe validate
```

**Erwartet:** `imported 1`, `rejected 0`, danach eine Zahl zugeordneter
Buchungen und `All invariants hold.`

`rejected 1` heißt: Der Auszug ging nicht auf. Auf macOS geht derselbe Auszug
auf — dann liegt es daran, wie `pdfplumber` unter Windows die Zeichenabstände
liest. **Das wäre der wichtigste Fund des Abends.** Notiere die volle Meldung.

- [ ] `rejected 0`
- [ ] `All invariants hold.`

## B3 Die Dateisperre

`serve` starten, auf **Review** oder **Transaktionen** gehen und **einer
Buchung von Hand eine Kategorie zuweisen**.

Das ist der Griff, der die Sperre auslöst: Die Entscheidung wird nach
`overrides.yaml` geschrieben, und davor legt `filelock` eine `.lock`-Datei an.
Bis zum 23.09.2026 stand an dieser Stelle `fcntl` — das Werkzeug lief unter
Windows bis genau zu diesem Klick und brach dann ab.

**Erwartet:** Die Zuordnung wird übernommen, kein Fehler.

```powershell
Get-ChildItem "$Daten\config" | Select-Object Name
```

**Erwartet:** `overrides.yaml` ist da. Eine liegengebliebene
`overrides.yaml.lock` wäre ein Fund.

- [ ] Zuordnung ohne Fehler
- [ ] `overrides.yaml` geschrieben

---

## Zum Schluss

```powershell
Stop-Transcript
```

Und aufräumen, falls du den Rechner nicht behältst:

```powershell
Remove-Item -Recurse -Force "$env:APPDATA\Finance OS"   # oder $Daten
Remove-Item -Recurse -Force $Arbeit
Remove-Item -Recurse -Force $Sicher
```

Die Datei `finctl-windows.txt` vom Desktop mitbringen. Daraus lässt sich jeder
Fund nachvollziehen, auch die, die du nicht als solche erkannt hast.

---

## Ergebnis

| | Ergebnis | Notiz |
|---|---|---|
| A2 Installation | | |
| A3 Kodierung | | |
| A4 Pfade | | |
| A5 Erster Start | | |
| A6 Dashboard | | |
| A7 Einrichtung | | |
| A8 Alle Seiten | | |
| A9 Sichern | | |
| B2 Parser | | |
| B3 Dateisperre | | |

**PowerShell-Version:** ______  **Codepage:** ______
