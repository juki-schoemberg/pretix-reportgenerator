# ENVIRONMENT.md — Entwicklungsumgebung für `pretix-custom-reports`

Aufgebaut von Agent `env-setup` (Welle 0-env). Diese Datei ist die verbindliche
Beschreibung der laufenden Umgebung. Verantwortlich: `env-setup`.

---

## Der eine Befehl

```bash
bash scripts/start-dev.sh
```

Danach im Browser: **http://localhost:8000/control/** — Login `admin@localhost` / `admin`.

Stoppen mit `Ctrl+C`.

---

## Gepinnte Versionen

| Was | Wert |
|---|---|
| pretix | **v2026.6.0** (Release vom 2026-07-01, neuestes stabiles Release) |
| pretix Commit | `fd565ecdb29c55a3e82dc15d94a848d193664caa` |
| Python | **3.11.0 (`C:\Python311`, in Git Bash `/c/Python311/python`)**, `requires-python = ">=3.11"` laut `pretix/pyproject.toml` |
| Django | 5.2.17 |
| Celery | 5.6.3 (installiert, aber **kein Broker konfiguriert** — siehe unten) |
| Node / npm | 24.16.0 / 11.13.0 |
| Datenbank | SQLite (`data/db.sqlite3`) — kein PostgreSQL, kein Redis |

Der Klon steht auf einem **Tag**, nicht auf `main`:

```bash
cd ../pretix && git describe --tags        # -> v2026.6.0
```

> **Achtung, flacher Klon:** Beim Neuaufbau am 2026-08-12 wurde mit
> `git clone --depth 1 --branch v2026.6.0` geklont (schneller, als Lesequelle
> gleichwertig). `git log` zeigt deshalb nur einen Commit („grafted"). Wer die
> Historie oder andere Tags braucht:
> ```bash
> cd ../pretix && git fetch --unshallow && git fetch --tags
> ```

Version wechseln (nur bewusst und danach `bash scripts/reset-dev.sh`):

```bash
cd ../pretix
git fetch --tags
git checkout v2026.7.0
cd ../pretix-reportgenerator && bash scripts/install-plugin.sh
```

---

## Verzeichnisaufbau

Arbeitswurzel ist `D:\Tobias\Desktop\Projekte\juki\`
(Git Bash: `/d/Tobias/Desktop/Projekte/juki/`), also **eine Ebene über dem
Plugin-Repo**. `~/dev/pretix-work/` aus `SETUP.md` war nur das Linux-Beispiel.

Die Skripte in `scripts/_common.sh` berechnen diese Wurzel selbst
(`REPO_DIR/..`) — ein Umzug des Repos braucht keine Skriptänderung, nur einen
neuen venv-/Klon-Aufbau daneben.

```
D:\Tobias\Desktop\Projekte\juki\
├── venv\                     Python-venv (Windows: venv\Scripts\activate)
├── pretix\                   Klon auf v2026.6.0 — Dev-Server UND Lesequelle
│   ├── src\pretix.cfg        Konfiguration dieser Umgebung (siehe unten)
│   ├── src\pretix\           ← verbindliche API-Referenz für alle Agents
│   └── node_modules\         JS-Abhängigkeiten (npm ci)
├── data\                     alles Veränderliche, nichts davon im Git
│   ├── db.sqlite3            Entwicklungsdatenbank
│   ├── media\                Uploads, Rechnungs-PDFs, Antwortdateien
│   ├── cache\                CachedFile-Ablage (Export-Downloads)
│   ├── logs\pretix.log       Anwendungslog
│   ├── exports\              manuell erzeugte Exportdateien (Verifikation)
│   └── *.log                 Protokolle des Setup-Laufs (pip, npm, migrate, seed)
├── tools\npm-shim\           Windows-Hilfspaket, siehe Stolpersteine
├── staging\                  Zwischenablage des env-setup-Laufs, gefahrlos löschbar
└── pretix-reportgenerator\   dieses Repo (das Plugin)
```

`data/` liegt **außerhalb** des Klons, damit `git status` im pretix-Klon sauber
bleibt und der Klon eine reine Lesequelle ist.

---

## Konfiguration

Eine einzige Datei: **`../pretix/src/pretix.cfg`**.

Sie wird auf zwei Wegen gefunden:

1. Alle Skripte in `scripts/` exportieren `PRETIX_CONFIG_FILE` mit absolutem Pfad.
2. pretix sucht ohne diese Variable u. a. `./pretix.cfg` **relativ zum aktuellen
   Verzeichnis** — deshalb funktioniert sie auch, wenn du in `../pretix/src/`
   arbeitest (so wie es `doc/development/setup.rst` vorsieht).

> **Stolperfalle:** Rufst du `python -m pretix ...` aus einem *anderen* Verzeichnis
> auf, ohne `PRETIX_CONFIG_FILE` zu setzen, legt pretix ein neues `data/` relativ
> zum cwd an und arbeitet gegen eine **leere** Datenbank. Immer entweder die
> Skripte benutzen oder:
> ```bash
> export PRETIX_CONFIG_FILE=/d/Tobias/Desktop/Projekte/juki/pretix/src/pretix.cfg
> ```

Wichtige Werte: `datadir=D:/Tobias/Desktop/Projekte/juki/data`, `backend=sqlite3`,
`locale default=de`, `timezone=Europe/Berlin`, `url=http://localhost:8000`.

---

## Zugangsdaten

| Feld | Wert |
|---|---|
| Backend | http://localhost:8000/control/ |
| E-Mail | `admin@localhost` |
| Passwort | `admin` |

Diese Zugangsdaten sind **nicht erfunden**: `manage.py migrate` legt sie in dieser
pretix-Version automatisch an (`doc/development/setup.rst`, Abschnitt „Working with
the code"). Superuser (`is_staff=True`), verifiziert per `check_password()`.

**Wichtig — Teams statt is_staff:** pretix leitet Backend-Rechte aus `Team`-Objekten
ab, nicht aus `is_staff`. Ein Superuser ohne Team sieht im Backend *keine*
Veranstaltungen (die Eventliste ist leer, Event-URLs liefern 404). `seed_demo.py`
legt deshalb das Team **„Demo-Team (alle Rechte)"** beim Veranstalter `demo` an
(`all_events=True`, `all_event_permissions=True`, `all_organizer_permissions=True`)
und nimmt alle aktiven Nutzer auf. Wenn du einen weiteren Nutzer anlegst, musst du
ihn diesem Team hinzufügen:
http://localhost:8000/control/organizer/demo/teams

Passwort ändern:

```bash
cd ../pretix/src && python manage.py changepassword admin@localhost
```

---

## Start und Stopp

```bash
bash scripts/start-dev.sh          # Port 8000
bash scripts/start-dev.sh 8080     # anderer Port
```

| URL | Inhalt |
|---|---|
| http://localhost:8000/control/ | Backend |
| http://localhost:8000/demo/demo-event/ | Shop, Einzeltermin |
| http://localhost:8000/demo/demo-serie/ | Shop, Veranstaltungsreihe |
| http://localhost:5173/ | Vite-Dev-Server (startet `runserver` selbst mit) |
| http://localhost:8000/control/event/demo/demo-event/customreports/ | **Plugin: Report-Liste** (Einzeltermin) |
| http://localhost:8000/control/event/demo/demo-serie/customreports/ | Plugin: Report-Liste (Reihe) |
| http://localhost:8000/control/organizer/demo/customreports/templates/ | Plugin: Vorlagen des Veranstalters |

Das Plugin ist in beiden Demo-Events bereits aktiviert (Stand 2026-08-12). Nach
einem `reset-dev.sh` muss es erneut aktiviert werden:
`/control/event/demo/demo-event/settings/plugins`.

**Stopp:** `Ctrl+C` im Terminal des Servers. Der Vite-Prozess wird über einen
`atexit`-Handler mit beendet. Bleibt bei einem harten Abbruch etwas hängen:

```bash
# Windows, letzte Rettung — beendet ALLE Python- bzw. Node-Prozesse:
taskkill //F //IM python.exe
taskkill //F //IM node.exe
```

Auto-Reload ist aktiv: Änderungen am Plugin-Code werden vom Django-Reloader
übernommen. Neu **registrierte** Plugins (neuer Entry Point) brauchen einen echten
Neustart.

---

## Datenbank zurücksetzen und neu befüllen

```bash
bash scripts/reset-dev.sh          # fragt nach
bash scripts/reset-dev.sh -y       # ohne Rückfrage
bash scripts/reset-dev.sh -y --orders 50   # weniger Bestellungen, schneller
```

Das löscht `data/db.sqlite3`, `data/media/`, `data/cache/`, migriert neu (dabei
entsteht `admin@localhost` / `admin` wieder) und seedet die Demo-Daten. Dauer im
Referenzlauf: **ca. 90 Sekunden**.

Nur die Demo-Daten neu bauen, DB behalten:

```bash
python scripts/seed_demo.py --reset          # löscht Veranstalter 'demo' und legt neu an
python scripts/seed_demo.py --list           # nur Übersicht ausgeben
python scripts/seed_demo.py --reset --seed 1234 --orders 500
```

`seed_demo.py` ist idempotent: ohne `--reset` bricht es ab, wenn der Veranstalter
`demo` existiert; mit `--reset` löscht es ihn in FK-sicherer Reihenfolge und legt
alles neu an. Der Zufalls-Seed ist fest (`20260727`), gleiche Parameter erzeugen
dieselben Daten.

---

## Was in den Demo-Daten steckt

Veranstalter `demo`, Team „Demo-Team (alle Rechte)".

| | `demo-event` (Einzeltermin) | `demo-serie` (Reihe) |
|---|---|---|
| Untertermine | – | 5 (einer mit Kontingent 8, einer mit Preis-Override) |
| Kategorien / Produkte | 4 / 7 (+5 Varianten) | 2 / 4 (+2 Varianten) |
| Steuersätze | 19 %, 7 %, 0 % | 19 %, 7 %, 0 % |
| Kontingente | 5 (u. a. Frühbucher 25, Workshop 15, eines unbegrenzt) | 10 (pro Termin) |
| Fragen | 13 — **alle 12 Typen** (`S,T,N,B,C,M,F,D,H,W,CC,TEL`) | 4 (`C,B,T,N`) |
| Gutscheine | 4 (`set`, `subtract`, `percent`) | 4 |
| Bestellungen | 130 | 70 |
| Positionen | 302 | 149 |
| Antworten | 1336 | 294 |
| Check-ins | 45 | 24 |
| Zahlungen / Erstattungen | 100 / 14 | 56 / 6 |
| Rechnungen (PDF) | 21 | 11 |
| **ohne Rechnungsadresse** | 43 | 24 |

> **Die Statusverteilung verschiebt sich nach dem ersten `runperiodic`.** pretix
> lässt dort `expire_orders` laufen: offene Bestellungen, deren `expires` in der
> Vergangenheit liegt, wechseln nach `expired`. Direkt nach dem Seeden sind es
> 47 `pending` / 17 `expired`, nach einem `runperiodic` 15 `pending` / 49 `expired`.
> Das ist echtes pretix-Verhalten und erwünscht — wer eine feste Verteilung braucht,
> seedet neu (`bash scripts/reset-dev.sh -y`).

Bewusst eingebaute Lücken und Sonderfälle — genau die Fälle, in denen ein
Report-Builder falsche Zeilen produziert:

- alle vier pretix-Status: `pending`, `paid`, `expired`, `canceled`
  (pretix kennt **kein** eigenes „erstattet": `STATUS_REFUNDED == STATUS_CANCELED`;
  Erstattungen sind `OrderRefund`-Objekte, teils an stornierten, teils an bezahlten
  Bestellungen — letzteres als Teilerstattung)
- Teilzahlungen (eine bestätigte + eine offene Zahlung, Summe < Gesamtbetrag)
- Bestellungen mit `require_approval`, im Testmodus, mit `checkin_attention`,
  mit Follow-up-Datum, mit internem Kommentar
- ca. 34 % **ohne** Rechnungsadresse; von den vorhandenen ein Teil geschäftlich mit
  USt-IdNr. (teils validiert), ein Teil privat
- ca. 22 % der Antworten fehlen — auch bei **Pflichtfragen** (`ALTER`, `AGB`,
  `SHIRTGROESSE`, `VORKENNTNISSE`)
- Positionen ganz ohne Antworten, Positionen ohne Teilnehmernamen
  (nicht personalisierte Produkte), Bestellungen ohne E-Mail-Adresse
- große Bestellungen mit 6–12 Positionen
- Gutscheine an einzelnen Positionen, Zahlungsgebühren (`OrderFee`)
- deutsche Namen und Adressen inkl. Umlauten, dazu AT- und CH-Adressen

Alle Frage-`identifier` sind gesetzt und sprechend (`FIRMA`, `ANMERKUNG`, `ALTER`,
`SHIRTGROESSE`, `ESSEN`, `ANREISE`, `ANKUNFTSZEIT`, `ABHOLUNG`, `NEWSLETTER`, `AGB`,
`AUSWEIS`, `HERKUNFTSLAND`, `MOBIL` / `VORKENNTNISSE`, `ONLINE`, `FRAGEN_VORAB`,
`BEGLEITUNG`) — `registry-dev` kann sie direkt als Testfälle verwenden.

---

## Hintergrundaufgaben — für `exporter-dev` der wichtigste Abschnitt

**Alle Hintergrundaufgaben laufen hier synchron im Request. Es gibt keinen Worker
und keinen Broker.**

In `../pretix/src/pretix/settings.py` gilt:

```python
HAS_CELERY = config.has_option('celery', 'broker')   # Zeile 364
...
else:
    CELERY_TASK_ALWAYS_EAGER = True                   # Zeile 413
```

`pretix.cfg` hat **absichtlich keinen `[celery]`-Abschnitt** — genau das verlangt
`doc/development/setup.rst`: „When running the local development webserver, ensure
Celery is not configured in `pretix.cfg`."

Konsequenzen, die man kennen muss:

| | Verhalten hier |
|---|---|
| `.apply_async()` / `.delay()` | wird sofort im Request ausgeführt |
| Export über die UI | `ExportDoView` erkennt `HAS_CELERY == False` und leitet direkt auf die Download-URL (`/download/<uuid>/`) weiter — kein Polling, keine Fortschrittsanzeige |
| Fehler in einer Task | schlagen als normale Exception durch (in der Entwicklung erwünscht) |
| `self.retry()` | funktioniert im Eager-Modus **nicht** wie in Produktion |
| Laufzeit | ein langsamer Export blockiert den Request bis zum Timeout des Browsers |

Wer *echtes* asynchrones Verhalten testen will (Retry-Pfade, Fortschritt,
`AsyncAction`-Polling), braucht Broker + Worker. Dann und nur dann:

```ini
# ../pretix/src/pretix.cfg
[celery]
broker=redis://localhost:6379/2
backend=redis://localhost:6379/2
```

```bash
# Terminal 2 — Worker (Code-Änderungen werden hier NICHT neu geladen)
cd ../pretix/src && celery -A pretix.celery_app worker -l info
```

Redis ist hier **nicht** installiert und wird für die Plugin-Entwicklung nicht
gebraucht. Wer den Abschnitt einträgt, muss ihn vor normaler Arbeit wieder
entfernen, sonst hängen alle Exporte, weil kein Worker läuft.

### Periodische Aufgaben

Es gibt keinen Scheduler. Der Auslöser ist ein einzelner Befehl (in Produktion per
Cron), siehe `doc/development/setup.rst`, „Working with periodic tasks":

```bash
cd ../pretix/src && python manage.py runperiodic
```

### Terminierte Exporte (Scheduled Exports)

Das ist die Anbindung, die `pretix-custom-reports` laut `CLAUDE.md` Regel 5
verwenden muss (kein eigener Scheduler). Ablauf in dieser Umgebung — verifiziert:

1. Terminierten Export im Backend anlegen:
   `/control/event/demo/demo-event/orders/export/` → Abschnitt für geplante Exporte
   (Model: `pretix.base.models.exports.ScheduledEventExport`, Feld
   `schedule_next_run`).
2. Fällig machen: `schedule_next_run` in die Vergangenheit setzen (oder warten).
3. `python manage.py runperiodic` ausführen.
4. pretix rendert den Export und **verschickt ihn per E-Mail** an Besitzer und
   Zusatzempfänger, danach setzt es `schedule_next_run` neu.

**Deshalb braucht `exporter-dev` einen Mail-Sink**, sonst schlägt der terminierte
Export mit `ConnectionRefusedError` fehl und `error_counter` steigt:

```bash
# Terminal 3 — Debug-SMTP-Server, zeigt jede Mail inklusive Anhang
python -m aiosmtpd -n -l localhost:1025
```

`aiosmtpd` ist im venv installiert; `pretix.cfg` zeigt mit `[mail] port=1025` schon
dorthin. (`python -m smtpd` aus der pretix-Doku existiert seit Python 3.12 nicht
mehr — das ist eine Abweichung von `setup.rst`.) `EMAIL_BACKEND` ist in pretix
hart auf SMTP verdrahtet (`settings.py:268`), es gibt keinen Console-Backend-Schalter.

---

## Logs

| Datei | Inhalt |
|---|---|
| `../data/logs/pretix.log` | Anwendungslog (Level INFO, bei DEBUG=1 auch DEBUG) |
| `../data/logs/csp.log` | Content-Security-Policy-Verstöße (nur bei `csp_log=True`) |
| Terminal des Dev-Servers | Requests, Tracebacks, Celery-Eager-Ausgaben, Vite |
| `../data/pip-install.log` usw. | Protokolle des Setup-Laufs (pip, npm, migrate, seed, reset) |

Zusätzlich protokolliert pretix fachliche Änderungen in der Datenbank
(`LogEntry`), sichtbar an jeder Bestellung im Backend.

---

## Bekannte Stolpersteine aus diesem Lauf (Windows 10 / Git Bash)

### 1. `scripts/preflight.sh` meldet auf Windows vier Fehler — drei sind Fehlalarm

```
✗ python3 nicht gefunden      -> es gibt Python 3.11.0 unter C:\Python311
✗ make fehlt                  -> nur für die Makefile-Ziele nötig, siehe unten
✗ gcc fehlt                   -> nicht nötig, alle Abhängigkeiten hatten Wheels
✗ msgfmt fehlt (gettext)      -> echt, betrifft nur Übersetzungen
```

Das Preflight ist für Debian/Ubuntu geschrieben. Der Aufbau lief trotz Exit-Code 1
durch; kein einziges Paket musste kompiliert werden (`libsass`, `lxml`, `Pillow`,
`psycopg2-binary`, `python-bidi`, `cryptography` kamen alle als Wheel).

**Zusatz aus dem Neuaufbau vom 2026-08-12:** `python` und `python3` im PATH zeigen
auf dieser Maschine auf den Windows-Store-Alias und finden gar kein Python. Das
venv deshalb **immer mit dem vollen Interpreterpfad** anlegen:

```bash
/c/Python311/python -m venv /d/Tobias/Desktop/Projekte/juki/venv
```

Innerhalb des aktivierten venv ist `python` danach korrekt (3.11.0).

### 2. `make` fehlt — Ersatzbefehle

`../pretix/src/Makefile` ist nur eine Hülle. Die Ziele einzeln:

| Makefile-Ziel | Befehl in `../pretix/src` bzw. `../pretix` |
|---|---|
| `make npminstall` | `npm ci` (im Wurzelverzeichnis des Klons) |
| `make npmbuild` | `npm run build` |
| `make localecompile` | `python manage.py compilemessages` (**braucht msgfmt**) |
| `make staticfiles` | `npm ci && npm run build && python manage.py compilejsi18n && python manage.py collectstatic --noinput` |
| `make test` | `py.test tests` |

`scripts/install-plugin.sh` ruft `make` nur, wenn es ein `Makefile` **und** `msgfmt`
gibt, und warnt sonst.

### 3. `msgfmt` fehlt — deutsche UI bleibt englisch

`make localecompile` wurde **nicht** ausgeführt. Die Oberfläche zeigt englische
Strings, obwohl `locale default=de` gesetzt ist. Datumsformate und Zahlenformate
sind trotzdem deutsch. Für die Plugin-Entwicklung unkritisch, für den `integrator`
(deutscher Katalog, `make`) relevant. Nachinstallieren — **eine** Zeile, mit
deiner Zustimmung, nicht vom Agent ausgeführt:

```powershell
winget install --id mlocati.GettextIconv
```

Danach neues Terminal öffnen und `cd ../pretix/src && python manage.py compilemessages`.

### 4. `runserver` stürzte auf Windows ab — `npm.exe`-Shim in `../tools/npm-shim`

`../pretix/src/pretix/base/management/commands/runserver.py` startet den
Vite-Dev-Server mit

```python
subprocess.Popen(["npm", "run", "dev:control"], ...)
```

Unter Windows gibt es kein `npm.exe`, nur `npm.cmd`. `CreateProcess` ergänzt
ausschließlich `.exe`, deshalb warf der Aufruf `FileNotFoundError [WinError 2]` und
**der Server startete gar nicht**.

Der Fehler ist beim Neuaufbau am 2026-08-12 mit Node 24.16.0 / npm 11.13.0 erneut
aufgetreten und wurde erneut so gelöst. Da `../tools/npm-shim` **nicht** im Git
liegt, wird es beim Neuaufbau der Umgebung neu erzeugt: ein Modul `npm_shim.py`
mit einer `main()`, die `node <npm-cli.js>` mit denselben Argumenten aufruft, plus
`pyproject.toml` mit `[project.scripts] npm = "npm_shim:main"`.

Lösung: das Mini-Paket `../tools/npm-shim` ist im venv installiert und stellt
`venv/Scripts/npm.exe` bereit, das an das echte npm (`node npm-cli.js`)
weiterleitet. Damit läuft `runserver` genau wie dokumentiert.

Falls das venv neu gebaut wird, muss der Shim mitinstalliert werden:

```bash
python -m pip install ../tools/npm-shim
```

Prüfen:

```bash
python -c "import subprocess; print(subprocess.call(['npm','--version']))"
```

### 5. Vite: Node zu alt und eine fehlende Plattform-Binary

> **Stand 2026-08-12 (Neuaufbau):** Mit **Node 24.16.0 / npm 11.13.0** trat weder
> das Binary-Problem noch die Node-Warnung auf — `npm ci` (2721 Dateien) reichte,
> Vite startete auf :5173 und optimierte die Abhängigkeiten. Der folgende Absatz
> gilt nur für ältere Node-/npm-Kombinationen.

`npm ci` installierte `@rolldown/binding-win32-x64-msvc` nicht (bekannter
npm-Bug mit optionalen Abhängigkeiten), Vite brach mit „Cannot find native
binding" ab. Nachgezogen mit:

```bash
cd ../pretix && npm i --no-save @rolldown/binding-win32-x64-msvc@1.0.3
```

**`--no-save` heißt: nach jedem erneuten `npm ci` ist das wieder weg.** Dann diese
Zeile wiederholen.

Zusätzlich warnt Vite: `You are using Node.js 20.17.0. Vite requires Node.js
20.19+ or 22.12+`. Es läuft trotzdem. Wer die Warnung loswerden will, aktualisiert
Node selbst (Systeminstallation, also deine Entscheidung):

```powershell
winget install --id OpenJS.NodeJS.LTS
```

### 6. Vite findet Plugins mit Vue-Komponenten unter Windows nicht

`../pretix/vite.config.ts` sucht Plugin-Assets über ein mehrzeiliges
`execSync('python -c "..."')`. Mehrzeilige Kommandos scheitern an `cmd.exe`, die
Funktion fängt den Fehler ab und meldet beim Start:

```
Failed to discover pretix plugins, skipping plugin entries: SyntaxError: Unexpected end of JSON input
```

**Folge:** Ein Plugin mit `[vite]`-Abschnitt in `pretixplugin.toml` (Vue-Komponenten)
würde unter Windows nicht eingebunden. Für `pretix-custom-reports` ist das
unkritisch — laut `CLAUDE.md`/Struktur liefert das Plugin klassische Templates und
`static/`-Dateien, kein Vite-Bundle. `frontend-dev` sollte es dabei belassen.

### 7. `DEBUG=False` bricht Seiten mit Vue-Komponenten

`VITE_DEV_MODE = DEBUG` ist in `settings.py` hart gekoppelt. Ohne DEBUG erwartet der
`vite`-Templatetag ein gebautes Manifest unter
`src/pretix/static.dist/vite/control/.vite/manifest.json`, das hier fehlt (kein
`npm run build`). In Management-Kommandos sieht man deshalb die harmlose Warnung
„Error reading vite manifest". Für den Dev-Server unerheblich, weil `runserver`
DEBUG automatisch einschaltet. **Nicht** `[django] debug=false` setzen.

### 8. E-Mail geht standardmäßig ins Leere

Ohne laufenden `aiosmtpd` (siehe Hintergrundaufgaben) endet jeder Mailversand in
`ConnectionRefusedError` auf `localhost:1025`. Betroffen: Bestellbestätigungen aus
dem Backend, Benachrichtigungen und **terminierte Exporte**.

### 9. Pfadlängen

Der Klon liegt unter `D:\Tobias\Desktop\Projekte\juki\pretix`. Das ist schon
deutlich tiefer als im ersten Lauf (`D:\Projekte\juki\pretix`); `npm ci`,
`collectstatic` und der Vite-Start liefen am 2026-08-12 trotzdem fehlerfrei
durch. Bei noch tieferen Ablagen laufen `node_modules` und `static.dist` in das
260-Zeichen-Limit von Windows. Dann hilft:
`git config --global core.longpaths true` und Long Paths in Windows aktivieren.

### 10. `--add-dir ..` ist Pflicht

Claude Code muss mit `claude --add-dir ..` gestartet werden, sonst sieht kein Agent
`../pretix/` — und laut `CLAUDE.md` Regel 1 ist genau dieser Source die
verbindliche API-Referenz.

---

## Abweichungen von `../pretix/doc/development/setup.rst`

Die Anleitung im Klon gilt; hier steht, wo die Umgebung notwendigerweise abweicht.

| `setup.rst` | Hier | Grund |
|---|---|---|
| `python3 -m venv env` im Klon | `/c/Python311/python -m venv` in `../venv` | vorgegebener Zielaufbau; `python`/`python3` im PATH sind hier nur der Windows-Store-Alias |
| `source env/bin/activate` | `. venv/Scripts/activate` | Windows-Layout |
| `curl … nodesource \| sudo bash` | übersprungen | Node 20.17.0 ist schon installiert; **kein sudo** |
| `make npminstall` | `npm ci` | kein `make` auf diesem System |
| `make localecompile` | übersprungen | kein `msgfmt`; UI bleibt englisch (Punkt 3) |
| Datenverzeichnis `src/data/` | `../data/` über `datadir` in `pretix.cfg` | Klon bleibt saubere Lesequelle |
| `python -m smtpd -n -c DebuggingServer localhost:1025` | `python -m aiosmtpd -n -l localhost:1025` | `smtpd` ist seit Python 3.12 aus der Standardbibliothek entfernt |
| — | zusätzlich `../tools/npm-shim` installiert | sonst startet `runserver` unter Windows nicht (Punkt 4) |
| — | zusätzlich `@rolldown/binding-win32-x64-msvc` | sonst startet Vite nicht (Punkt 5) |
| — | zusätzlich Team für `admin@localhost` | ohne Team sieht der Superuser keine Events |

Nicht eingerichtet, weil die Anleitung es nicht verlangt: PostgreSQL, Redis,
Celery-Worker, `npm run build`, Doku-Build (`doc/requirements.txt`), Playwright-Browser.

---

## Verifikation dieses Aufbaus

Alles davon wurde ausgeführt, nicht behauptet:

| Prüfung | Ergebnis |
|---|---|
| `python -c "import pretix; print(pretix.__version__, pretix.__file__)"` | `2026.6.0 D:\Tobias\Desktop\Projekte\juki\pretix\src\pretix\__init__.py` |
| `bash scripts/reset-dev.sh -y` komplett | Exit 0, ca. 90 s, identische Datenmengen bei erneutem Lauf |
| `bash scripts/start-dev.sh` | Server auf 8000, Vite auf 5173 |
| `GET /control/` ohne Login | 302 → `/control/login?next=/control/` |
| Login `admin@localhost` / `admin`, dann `GET /control/` | **200** |
| `GET /control/events/` | beide Events (`demo-event`, `demo-serie`) gelistet |
| Bestellliste `demo-event`, Unterterminliste `demo-serie`, Fragenliste | 200, mit Inhalten |
| `manage.py export demo demo-event orderlist … --parameters '{"_format":"orders:default"}'` | CSV mit **131 Zeilen** (130 Bestellungen + Kopfzeile) |
| Export über die Weboberfläche (`orders/export/do`, Blatt `orders`) | Redirect auf `/download/<uuid>/`, CSV mit 131 Zeilen |
| Export über die Weboberfläche (`demo-serie`, Blatt `positions`) | CSV mit 168 Zeilen, inkl. Untertermin-Spalte |
| `ScheduledEventExport` + `manage.py runperiodic` | Export gelaufen, `schedule_next_run` neu gesetzt, `error_counter=0`, Mail mit Anhang `demo-event_orders.csv` im Debug-SMTP-Server |
| `python scripts/seed_demo.py --reset` zweimal hintereinander | idempotent, keine Fehler, gleiche Zahlen |
| `git status` im pretix-Klon | sauber (`pretix.cfg`, `static.dist/`, `node_modules/` sind ignoriert) |

---

## Skripte

| Skript | Zweck |
|---|---|
| `scripts/preflight.sh` | Systemvoraussetzungen prüfen (Linux-orientiert, siehe Punkt 1) |
| `scripts/start-dev.sh` | venv aktivieren, Dev-Server starten, URLs und Zugangsdaten ausgeben |
| `scripts/reset-dev.sh` | DB und Medien löschen, migrieren, neu seeden |
| `scripts/seed_demo.py` | Demo-Daten (`--reset`, `--orders`, `--seed`, `--list`) |
| `scripts/install-plugin.sh` | `pip install -e .`, `make` (falls möglich), Neustart-Hinweis |
| `scripts/_common.sh` | gemeinsame Basis: venv finden/aktivieren, Pfade, Fehlermeldungen |
| `scripts/verify/run-all.sh` | die komplette Verifikation der Tabelle oben erneut ausführen |
| `scripts/verify/verify_http.py` | Login und Sichtbarkeit der Demo-Daten im Backend (16 Prüfungen) |
| `scripts/verify/verify_export.py` | eingebauter Export über die Weboberfläche (10 Prüfungen) |
| `scripts/verify/verify_scheduled.py` | terminierter Export über `runperiodic` (3 Prüfungen) |

Die Verifikation ist wiederholbar — Server starten, dann:

```bash
python -m aiosmtpd -n -l localhost:1025 &   # nur für verify_scheduled.py
bash scripts/verify/run-all.sh              # 29 Prüfungen
```

Alle Shell-Skripte laufen mit `set -euo pipefail` und brechen mit einer klaren
Meldung ab, wenn kein venv aktiv ist und keines unter `../venv` gefunden wird.
