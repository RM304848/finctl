# Finance OS für andere nutzbar machen

Stand: 24.09.2026. **Phase 1 bis 3 sind fertig:** Code und Daten sind
getrennt, in Code, Tests und Vorlagen steht kein persönlicher Name mehr, und
die Einrichtung läuft im Frontend. Was bleibt, ist Verteilen — und dafür
braucht es einen Windows-Rechner. Erledigtes ist unten jeweils markiert.

## Ziel

- Keine persönlichen Daten in YAML, Frontend, Code, Tests oder Doku, die mit
  dem Code weitergegeben werden.
- Konten, Kredite, Objekte und das Backup-Ziel werden über eine
  Einrichtungsmaske im Frontend angelegt, nicht von Hand in YAML.
- Installation über eine gängige Datei (.dmg, .exe) oder durch Einlesen eines
  Backups.

## Entscheidung: ein Code, getrennte Daten, keine Kopie

Eine Kopie „für einen allgemeinen Nutzer" hieße zwei Codebasen, die sofort
auseinanderlaufen: Jede neue Seite müsste von Hand hinübergetragen werden.
Stattdessen bleibt dieses Projekt das Arbeitsrepository. Der Code wird von den
Daten getrennt, und veröffentlicht wird später aus genau diesem Code.

Der Git-Verlauf enthält Beträge, Namen und Verträge und **bleibt privat**. Die
Veröffentlichung ist ein neues Repository mit frischem Verlauf, das mit einer
einzigen Anfangsversion aus dem bereinigten Stand beginnt.

Die Architektur passt schon dazu:
- alles lokal, YAML und SQLite, zur Laufzeit keine KI,
- das Muster Basisdatei plus `_custom`-Datei für Handeingaben,
- Designtests, die eine Liste verbotener Namen kennen.

## Bestandsaufnahme (18.09.2026)

| Wo | Befund |
|---|---|
| `config/` | 27 verfolgte Dateien, rund 16.900 Zeilen. Allgemeines steht neben Persönlichem: `taxonomy.yaml` und `rules.yaml` neben `loans.yaml`, `properties.yaml`, `szenarien.yaml`, `strom.yaml`, `backup.yaml` mit dem eigenen Cloud-Pfad |
| Code `finctl/` | Personen-, Objekt- und Vertragsnamen in rund 15 Dateien, fast nur in Kommentaren und Docstrings |
| Tests | echte Namen als Testdaten in etwa 8 Dateien |
| Vorlagen | fast sauber; `verbotene_namen` in `docs/design_conventions.yaml` prüft bisher nur den sichtbaren Text der Vorlagen |
| Doku | `HANDBUCH.md` und `CLAUDE.md` nennen an einigen Stellen echte Namen |
| Pfade | `CONFIG_DIR = Path("config")` steht zehnmal im Code, dazu weitere `Path("config/…")`, jeweils relativ zum Arbeitsverzeichnis. Es gibt noch keinen Datenordner. |
| Parser | Die deutschen Bank- und Brokerprofile in `finctl/ingest/profiles/` sind für andere in Deutschland direkt brauchbar |
| Update | Codeupdates laufen heute über git; in einer installierten App geht das nicht |

## Was steht, und was davon belegt ist

Drei Stufen, und der Unterschied zwischen den letzten beiden ist der, an dem
man sich sonst täuscht: **gebaut** heißt, der Code ist da; **belegt** heißt,
ein Test fällt um, wenn jemand es kaputt macht.

| | Stand | Belegt durch |
|---|---|---|
| Pfade an einer Wurzel, `FINCTL_DATEN` | belegt | `test_pfade.py` — hält die eigenen Pfade auf null |
| Datenordner je System, Zeiger `ort.txt` | belegt | `test_pfade.py` (8) — jede der vier Regeln im eigenen Prozess, mit eigenem Heimatverzeichnis |
| Backup ohne Code | belegt | `test_web.py` — die obersten Ebenen des Archivs sind genau `ops.ARCHIVTEILE`; `finctl/` und `tests/` sind nicht dabei |
| Erster Start ohne fremde Daten | belegt | `test_erstlauf.py` (2) — leerer Datenordner, `init`, dann jede Seite der Navigation auf 200 |
| Kontenregister im Frontend | belegt | `test_konten_anlegen.py` (13), `test_konten_register.py` (2) |
| Kredite anlegen und entfernen | belegt | `test_kredite_anlegen.py` (11) |
| Namen aus Code und Tests | belegt | `test_namen.py` (2) — null Fundstellen, und der geduldete Rest muss leer bleiben |
| Keine Unix-Module im Code | belegt | `test_plattform.py` (2) — Importprobe und AST-Lauf |
| Sperre über `filelock` | **gebaut, nicht auf Windows belegt** | die Importprobe läuft auf macOS; Verhalten dort ist ungeprüft |
| Kein Backup-Ziel im Code | belegt | `test_ops.py` — fehlendes Ziel bricht ab, `--to` geht trotzdem |
| Anleitungen mit `.venv\Scripts` | belegt | `test_pfade.py` — beide Systeme |
| Backup-Ziel im Frontend | belegt | `test_sicherung.py` (7) — ablehnen vor dem Speichern, Basisdatei bleibt unberührt |
| Einrichtungsassistent | belegt | `test_einrichtung.py` (11) — jeder der drei Schritte lässt sich als der offene rendern, und der Hinweis auf /monatsabschluss kommt und geht |
| Paket vollständig | belegt | `test_paket.py` (4) — baut ein Rad und zählt jede Vorlage, Startdatei und das Handbuch nach |
| Tests ohne eigene Daten | belegt | `tests/musterhaushalt/` — ein erfundener Haushalt mit jeder Funktion; die ganze Suite läuft darauf (`FINCTL_TESTDATEN=muster`, ohne `config/` von selbst) |
| Startdateien in `finctl/vorgaben/` | belegt | `test_erstlauf.py` (3) — eine frische Einrichtung startet **und** kann zuordnen: 110 Kategorien in der Datenbank, nicht nur eine kopierte Datei |

## Fahrplan

### Phase 1: Code und Daten trennen
- **Erledigt (22.09.2026):** Alle Pfade laufen über `finctl/pfade.py`. Die
  dreiundzwanzig eigenen Stellen -- zehn `CONFIG_DIR`, neun Dateikonstanten,
  vier auf `data/` -- hängen an einer Wurzel, die `FINCTL_DATEN` verschiebt;
  ohne die Variable bleibt alles, wo es war. `tests/test_pfade.py` hält die
  Zahl der eigenen Pfade auf null und prüft beide Fälle.
- **Erledigt (23.09.2026): Der Datenordner ist bezogen.** Vier Regeln
  entscheiden, welcher es ist, in dieser Reihenfolge: `FINCTL_DATEN`, der
  eingetragene Ort, ein `config/` im Arbeitsverzeichnis, sonst die Vorgabe des
  Systems (`~/Library/Application Support/Finance OS`, `%APPDATA%\Finance OS`,
  `~/.local/share/finance-os`).
  - Der **Zeiger** ist eine Datei `ort.txt` an der Systemstelle, eine Zeile
    lang. Er steht VOR dem Arbeitsverzeichnis, sonst entschiede ein fremdes
    `config/` im gerade offenen Ordner darüber, welches Hauptbuch gemeint ist.
  - Die dritte Regel ist der Grund, warum eine bestehende Einrichtung eine
    neue Version überlebt: Wer `config/` neben sich hat, arbeitet weiter damit.
  - `finctl ort` nennt den Ordner **und welche Regel gegriffen hat** — die
    einzige nützliche Auskunft, wenn ein leeres Hauptbuch aufgeht.
    `--setzen` trägt einen Ordner ein und verschiebt nichts.
- **Erledigt (23.09.2026): Das Backup enthält nur noch den Datenordner.**
  `finance.sql`, `config/`, `requirements.lock` und ein Blatt `stand.yaml`,
  das erklärt, was fehlt und wie man zurückkommt. Statt 1.008 KB nun 498 KB.
  - Wiederherstellen heißt jetzt: `finctl restore`, dann
    `finctl ort --setzen <ziel>`, dann `finctl validate`. Am 23.09.2026 am
    eigenen Bestand durchgespielt: „All invariants hold" über 3.375
    Transaktionen, aus einem Archiv ohne eine Zeile Quelltext.
  - Bedingung, die das erst vertretbar macht: Der Code muss anderswo liegen.
    Heute ist das `finance-git.bundle` neben den Archiven, später das
    Installationspaket aus Phase 4.
- **Erledigt (24.09.2026): `config/` ist geteilt.** Sieben Startdateien
  liegen in `finctl/vorgaben/`, vorher vier. Neu sind `taxonomy.yaml`,
  `groups.yaml` und `goals.yaml`.
  - **Die Taxonomie ist die wichtige.** Vorher startete eine frische
    Einrichtung mit null Kategorien: Alle Seiten antworteten, aber es liess
    sich keine einzige Buchung zuordnen — das Werkzeug lief und tat nichts.
    Jetzt kommen **110 Kategorien und 46 Steuerpositionen** mit, davon 19 mit
    hinterlegter Steuerposition. `test_erstlauf.py` prüft die **Datenbank**,
    nicht die Datei: eine Vorlage, die kopiert wird und beim Laden durchfällt,
    wäre genauso nutzlos.
  - Möglich wurde das durch eine Umbenennung: `familie/fixkosten-<vorname>` heisst
    jetzt `familie/fixkosten-partner`, ebenso die fünf Regeln davor. Über
    `taxonomy_migrations.yaml` gewandert, vier Splits umgezogen, Summen vorher
    und nachher identisch (3.527 Splits, 2.490.288 Cent).
  - `groups.yaml` und `goals.yaml` gehen als **Kopf ohne Inhalt** mit: Die
    Einträge sind persönlich, die Begründung darüber nicht. Wer die Datei
    öffnet, liest, warum es keine Renditeannahme gibt und warum das
    Datumsfenster einer Gruppe der Punkt ist und kein Zusatz.

### Phase 2: Persönliches aus Code, Tests und Doku
- **Erledigt (22.09.2026):** `tests/test_namen.py` zählt über das ganze
  Repository, nach dem Muster von `ZU_VERZWEIGT` in `tests/test_lint.py`: Die
  Liste darf nur kürzer werden, eine neue Datei ist ein Fehler.
- **Erledigt (23.09.2026): null Fundstellen** (Start: 219 in 37). Der Test ist
  damit keine Ratsche mehr, sondern eine **Sperre** — weil nichts geduldet
  ist, fällt jede einzelne neue Stelle auf, statt in einem Restposten
  unterzugehen. Er prüft sich seither selbst mit.
- Die letzten 103 verschwanden in drei Mustern, keins davon
  Suchen-und-Ersetzen:
  1. **Als Beleg gedachte Namen wurden zum Sachverhalt.** Ein Kommentar, der
     sagt „bei dieser Wohnung steigt die Rate und tilgt dabei weniger", belegt
     nicht die Wohnung, sondern den Effekt. Die Begründung ist geblieben.
  2. **Feste Kennungen werden aus der Konfiguration gelesen.** Ein Test, der
     ein bestimmtes Objekt, eine bestimmte Regel oder eine bestimmte Klammer
     nennt, ist zugleich brüchig UND persönlich. Mehrere prüfen seither
     **mehr** als vorher: nicht eine Regel, sondern alle ihrer Form — und der
     Verkaufstest nicht ein Objekt, sondern das erste mit laufendem Kredit.
  3. **Beschriftungen und eingereichte Zahlen sind nach `config/` gewandert:**
     die Policen nach `renten.policen` in `assumptions.yaml`, die
     eingereichten Schuldzinsen nach `filed_<jahr>.schuldzinsen_cents` in
     `properties.yaml`. Sie stehen jetzt neben der Unterlage, aus der sie
     stammen — und ein anderer Nutzer trägt dort seine eigenen ein.
- **Gegenprobe gemacht:** ein eingeschleuster Name in `ops.py` lässt den Test
  umfallen, ein falscher Teilbetrag in `rules.yaml` den Aufteilungstest.
- **Und dann fiel auf, dass „null" zu wenig hieß.** Der Test kannte zwölf
  Namen und prüfte `.py` und `.html`. Beides war zu eng:
  - Der **eigene Nachname** stand in vier Modulen als Beispieltext für die
    Kerning-Probleme des PDF-Parsers — er war schlicht nie auf der Liste.
  - `.sql` und `.yaml` lagen außerhalb der Reichweite. Darunter
    `finctl/vorgaben/rules.yaml`, also ausgerechnet die **Startvorlage**, die
    ein neuer Nutzer als Erstes bekommt: Sie nannte eine Straße.
  - Beides ist behoben; die Liste hat jetzt 18 Einträge, der Scan fünf
    Endungen. Banknamen bleiben bewusst erlaubt — die Auszugsprofile heißen
    nach ihrer Bank und sind für andere in Deutschland genau deshalb nützlich.
  - **Die Lehre steht in der Konvention selbst:** Eine Liste verbotener Namen
    belegt nur, dass DIESE Namen fehlen. Über die, die niemand eingetragen
    hat, sagt sie nichts.
- Die Namensliste selbst liegt noch in `docs/design_conventions.yaml` und soll
  später in den Datenordner, damit auch sie nicht mit dem Code weitergeht.

### Phase 3: Einrichtung im Frontend
- **Assistent beim ersten Start.** Diese drei Fragen stehen am Anfang, und
  keine davon hat eine Vorgabe im Code (Entscheidung 23.09.2026):
  1. **Wo die App samt aller Daten liegen soll** — der Datenordner
  2. **Wohin gesichert wird** — Ordner (Cloud-Laufwerk, externe Platte,
     USB-Stick) und wie viele Stände aufbewahrt werden
  3. **Welche Konten es gibt** — Name, Art, Bankprofil, Kennung
- Danach, optional: Kredite, Objekte, Annahmen, erste Kontoauszüge einlesen.
- **Erledigt (23.09.2026):** Die Backup-Vorgabe im Code ist weg. Dort stand ein
  fester Pfad in die eigene Cloud; fehlt die Angabe, bricht `backup_settings`
  jetzt mit einer Meldung ab, statt still irgendwohin zu sichern.
- **Erledigt (23.09.2026): Backup-Einstellungen im Frontend**, neben dem
  Knopf statt auf einer eigenen Seite — gefragt wird danach einmal, geändert
  wird es dort, wo gesichert wird. Ordner und Anzahl der Stände, der letzte
  Stand mit Datum und Größe, und **geprüft wird durch Schreiben**: ein
  Cloud-Ordner, der gerade nicht eingehängt ist, sieht vorhanden aus und nimmt
  trotzdem nichts an; `os.access` sagt dazu das Falsche. Geschrieben wird nach
  `backup_custom.yaml`, damit die Begründungen in `backup.yaml` bleiben.
  Der Knopf hieß „Backup → OneDrive" und heißt jetzt „Sichern".
  - Einen nativen Ordnerdialog gibt es weiter erst in der installierten App
    (Phase 4), weil ein Browser einer Webseite keinen Pfad herausgibt.
- **Erledigt (22.09.2026):** Der Kontoeditor steht unter Stammdaten
  (`kontenregister.html`), Kredite lassen sich auf /kredite anlegen und
  entfernen. Beides über das Overlay-Muster, die Basisdateien bleiben unberührt.
- **Erledigt (23.09.2026):** Objekte lassen sich auf /immobilien anlegen und
  entfernen — drei Felder, mehr braucht eine Kennung nicht. Was ein Objekt
  gekostet hat, steht als einmaliger Betrag in der Planung, nicht hier.
- **Erledigt (24.09.2026): der Assistent steht** — `/einrichtung`, die drei
  Fragen in ihrer Reihenfolge.
  - **Kein Zwang und keine Umleitung.** Die Seite steht in der Navigation wie
    jede andere; solange etwas offen ist, weist der Monatsabschluss darauf
    hin. Ein Assistent, der sich vor die App schiebt, ist beim zweiten Mal im
    Weg — und wer nur nachsehen will, wohin gesichert wird, soll nicht durch
    drei Schritte klicken müssen.
  - **Erledigt entscheiden die Daten**, nicht ein Haken: ein Zeiger auf einen
    Datenordner, ein eingetragenes Sicherungsziel, mindestens ein Konto.
    Dieselbe Entscheidung wie auf /monatsabschluss.
  - **Keine dritte Kopie einer Maske.** Das Sicherungsziel und das
    Kontoformular sind Teilvorlagen geworden (`_sicherungsziel.html`,
    `_neues_konto.html`) und stehen jeweils genau einmal im Code. Ein Test
    hält das fest: das Feld darf nur in seiner Teilvorlage vorkommen.
  - Neu ist nur **`/api/datenordner`**. Er trägt einen Ordner ein und
    verschiebt nichts; geprüft wird wie beim Sicherungsziel durch Schreiben.
    Die laufende App wechselt ihr Hauptbuch nicht mitten im Betrieb — die
    Antwort sagt das, statt es den Nutzer merken zu lassen.
- Vorbild für alle Editoren ist /regeln: aufklappbarer Bereich, Vorschau,
  sofort anwenden.

### Phase 4: Verteilen
- **Stufe 1:** `uv tool install` oder `pipx install`, dann `finctl init` und
  `finctl serve`. Reicht für technisch Versierte.
- **Erledigt (25.09.2026): das Paket ist vollständig — und war es vorher
  nicht.** Gemessen statt gelesen: ein Rad aus diesem Projekt enthielt genau
  **eine** Nicht-Python-Datei, `schema.sql`. Keine der vierzig Vorlagen, keine
  Startdatei, kein Handbuch. In `pyproject.toml` stand nichts Falsches — es
  stand nichts da. Jetzt 130 statt 82 Dateien, und `tests/test_paket.py` baut
  ein Rad und zählt **jede einzelne** Datei nach; eine Stichprobe hätte den
  nächsten Fehler nicht gefunden.
- **Erledigt (25.09.2026): installiert durchgespielt, auf macOS.** Das Rad in
  eine eigene Umgebung installiert und aus einem Ordner **ohne `config/`**
  gestartet — der Fall, für den Regel 4 des Datenordners, die Startdateien und
  der Handbuchpfad gebaut wurden und der nie gelaufen war.
  - `finctl ort` nennt die Systemvorgabe, `init` legt sieben Startdateien an,
    `validate` meldet „All invariants hold", alle 23 Seiten antworten mit 200.
  - Die Einrichtung einmal vollständig durchgeklickt: drei Schritte von
    „offen" auf „ja", danach ist der Hinweis auf dem Monatsabschluss weg.
  - **Zwei Funde dabei:** Wer die Vorgabe behalten wollte, bekam „Kein Ordner
    angegeben" — das Feld steht jetzt auf dem Ordner, der gerade gilt. Und
    eine gespeicherte Antwort ließ die Übersicht oben auf „offen" stehen;
    beide Masken melden jetzt, dass die Seite sich neu lesen soll.
- **Erledigt (23.09.2026): der bekannte Windows-Blocker ist weg.** `fcntl`
  stand als Import ganz oben in `finctl/rules/categorize.py` und damit hinter
  jedem Schreibweg — die App startete dort, brach aber beim ersten Zuordnen
  einer Kategorie. Jetzt sperrt `filelock`, und `tests/test_plattform.py` hält
  die Zahl der Unix-Module auf null.
  **Ungeprüft bleibt, ob es dort wirklich läuft:** getestet wurde auf macOS,
  indem die Unix-Module für den eigenen Code gesperrt wurden. Das findet
  Importfehler, nicht Verhaltensunterschiede. Der erste echte Lauf auf einem
  Windows-Rechner steht aus und gehört vor die erste Weitergabe.
- **Stufe 2, zum Doppelklicken:**
  - macOS: eine `.app` in einer `.dmg`, gebaut mit PyInstaller oder Briefcase.
    Ein kleiner Starter startet den Server auf 127.0.0.1 und öffnet den Browser.
  - Windows: eine `.exe` auf demselben Weg.
  - Ohne Apple-Entwicklerkonto (rund 99 $ im Jahr, für Signatur und
    Notarisierung) warnt macOS beim Öffnen. Unter Windows ist es mit SmartScreen
    ähnlich.
- **Updates:** Eine neue App-Version ersetzt den git-Weg, der Datenordner bleibt
  unberührt. Beim Start bringt ein Migrationsschritt die YAML-Dateien auf den
  neuen Stand; `config/taxonomy_migrations.yaml` zeigt das Muster schon.

### Phase 5: Veröffentlichen
- Neues Repository mit frischem Verlauf aus dem bereinigten Stand.
- Lizenz wählen.
- Hinweis aufnehmen, dass das Werkzeug rechnet und keine Anlageberatung ist.
- Festlegen, wie man Fehler meldet.

## Was noch fehlt (24.09.2026)

Nach Aufwand geordnet, nicht nach Wichtigkeit.

| | Phase | Größe |
|---|---|---|
| **Erster echter Lauf auf Windows** | 4 | klein an Arbeit, aber es braucht einen Windows-Rechner |
| **Paketieren, Stufe 2** — `.app` und `.exe` zum Doppelklicken | 4 | groß, dazu Signatur; Stufe 1 (`pip install`) steht und ist durchgespielt |
| **Veröffentlichen** — neues Repository, Lizenz, Fehlermeldungen | 5 | eigener Schritt, ganz am Ende |

## Größenordnung

- **Phase 1 und 2:** einige Sitzungen, mechanisch, durch Tests abgesichert.
  Sollten zuerst kommen, weil jedes neue Feature sonst weiteres Persönliches
  mitbringt.
- **Phase 3:** der größte fachliche Teil.
- **Phase 4:** überschaubar; die eigentliche Arbeit sind Signatur und Updates.

## Was schon gilt (seit 18.09.2026, in `CLAUDE.md`)

1. **Persönliches steht nur in `config/`.** Keine echten Namen, Verträge,
   Anbieter oder Beträge in Code, Tests, Kommentaren und Vorlagen. Testdaten
   sind erfunden.
2. **Neue Module lesen Pfade über `CONFIG_DIR` aus `finctl/pfade.py`.** Die
   Module mit eigenem `CONFIG_DIR` sind Altbestand für Phase 1, kein Vorbild.
   `finctl/strom.py` nutzt die neue Stelle bereits.

## Wenn es losgeht: Prüfung

- Ein frisch installierter Nutzer ohne Datenordner kommt durch den Assistenten
  bis zum ersten Import. Das lässt sich im Browser mit einem leeren
  `FINCTL_DATEN` testen.
- Der Test über das ganze Repository findet keine persönlichen Namen.
- Ein Backup vom eigenen Rechner, auf einem zweiten Benutzerkonto eingelesen,
  zeigt dieselben Seiten.
- Wird das Backup-Ziel im Frontend umgestellt, landet der nächste Stand im neuen
  Ordner. Ein nicht beschreibbarer Ordner wird vor dem Speichern abgelehnt.
