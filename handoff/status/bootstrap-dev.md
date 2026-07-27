# Status: bootstrap-dev — Welle 0a

**pretix-Version (exakt ermittelt):** `2026.6.0`

```
$ python -c "import pretix; print(pretix.__version__, pretix.__file__)"
2026.6.0 D:\Projekte\juki\pretix\src\pretix\__init__.py
$ python -c "import importlib_metadata as m; print(m.version('pretix'))"
2026.6.0
```

Python 3.12.6, Django 5.2.16, venv `D:\Projekte\juki\venv`.
`compatibility = "pretix==2026.6.0"` — exakt gepinnt, siehe ADR 0000 Abschnitt 1.

---

## Erledigt

**Paketstruktur** — vollständig, alle `__init__.py` inklusive der leeren:

```
pretix_custom_reports/__init__.py        __version__ = "0.1.0", importfrei
                     /apps.py           PluginConfig + PretixPluginMeta
                     /signals.py        nav_event-Receiver + Platzhalterliste
                     /urls.py           eine Route
                     /models.py         leer   -> persistence-dev
                     /forms.py          leer   -> persistence-dev
                     /exporters.py      leer   -> exporter-dev
                     /contracts/__init__.py
                     /registry/__init__.py
                     /query/__init__.py
                     /portability/__init__.py
                     /views/__init__.py         <- der kritische Punkt
                     /views/placeholder.py      Walking-Skeleton-View
                     /migrations/__init__.py    KEINE Migration
                     /templates/pretix_custom_reports/placeholder.html
                     /static/pretix_custom_reports/.gitkeep
                     /locale/.gitkeep
tests/__init__.py, conftest.py, test_smoke.py, fixtures/definitions/{,invalid/}
docs/adr/0000-setup.md
handoff/{status,requests}/
```

**Paketierung/Tooling** — `pyproject.toml`, `setup.py`, `setup.cfg`,
`MANIFEST.in`, `Makefile`, `pretixplugin.toml`, `LICENSE`, `README.rst`,
`.gitignore`, `.github/workflows/{style,tests}.yml`.

**Walking Skeleton** — Plugin installierbar, per Event aktivierbar,
Navigationseintrag „Exports" auf Event-Ebene, Platzhalterseite dahinter,
rechteabhängig.

**Vorlagen** — `pretix/pretix-plugin-cookiecutter` (HEAD `9ef6054`) als primäre
Referenz, `pretix/pretix-passbook` als Gegenprobe eines real veröffentlichten
externen Plugins, `pretix/plugins/webcheckin` und `pretix/plugins/reports` im
Source für Signal-, URL- und Kategorie-Konventionen.

---

## Definition of Done — Belege

### 1. `pip install -e .` läuft durch — ERFÜLLT

```
$ pip install -e .
Successfully built pretix-custom-reports
Successfully installed pretix-custom-reports-0.1.0
```

Zusätzlich: `python setup.py sdist` erzeugt
`dist/pretix_custom_reports-0.1.0.tar.gz` (danach wieder entfernt).
`check-manifest .` → `lists of files in version control and sdist match`
(gemessen nach temporärem `git add -A`, danach `git reset` — das Tool vergleicht
gegen die Versionskontrolle, und in Welle 0a ist nichts committet).

### 2. Plugin erscheint unter „Plugins" und lässt sich aktivieren — ERFÜLLT

Live gegen den Dev-Server auf Port 8000, eingeloggt als `admin@localhost`,
Veranstaltung `demo/demo-event`:

```
PASS plugin settings page reachable -- status=200
PASS DoD2a plugin listed under Plugins -- looked for 'Custom Reports'
PASS DoD2a listed in category 'Output and export formats'
PASS DoD2b activation POST accepted -- status=200
PASS DoD2c plugin is now enabled (disable button offered)
```

Zusätzlich in einem zweiten Lauf direkt gegen die Dev-Datenbank geprüft, dass die
Aktivierung wirklich persistiert:

```
PASS DoD2b plugin active in Event.get_plugins()
     plugins='pretix.plugins.banktransfer,pretix.plugins.ticketoutputpdf,
              pretix.plugins.statistics,pretix_custom_reports'
```

Das Plugin ist damit in `demo/demo-event` **dauerhaft aktiviert** — die nächsten
Wellen können sofort dagegen arbeiten. In `demo/demo-serie` ist es nicht aktiv
(nützlich als Negativfall).

### 3. Menüpunkt „Exports" sichtbar und öffnet die Platzhalterseite — ERFÜLLT

```
PASS DoD3a 'Exports' nav entry present in sidebar
     href=/control/event/demo/demo-event/customreports/
PASS DoD3a nav label 'Exports' rendered
PASS DoD3b placeholder page opens -> 200
PASS DoD3b placeholder marker present          (id="customreports-placeholder")
PASS DoD3b placeholder says it is empty        ("not implemented yet")
```

Zum Anklicken: <http://localhost:8000/control/event/demo/demo-event/customreports/>

### 4. Menüpunkt für Nutzer ohne Rechte NICHT sichtbar — ERFÜLLT

```
PASS DoD4a limited user can load an event page -- status=200
PASS DoD4b 'Exports' nav entry NOT rendered for limited user
PASS DoD4c placeholder view itself rejects limited user -- status=403
```

Und gegen die DB:

```
limited user permission set: ['can_change_items', 'event.items:write']
PASS DoD4 limited user really lacks event.orders:read
PASS DoD4 limited user does hold some other permission
```

Der Testnutzer wurde dafür in der Dev-DB angelegt (Details unten). Wichtig:
Punkt 4a ist der Kern des Nachweises. Ein Nutzer ganz ohne Team hätte nur ein 404
auf das Event belegt, nicht das *Verstecken* des Menüpunkts.

### 5. `pytest` grün, Lint sauber — ERFÜLLT

```
$ pytest
14 passed, 3 warnings

$ pytest -m "not performance"
14 passed

$ flake8 .       -> exit 0
$ isort -c .     -> exit 0 (Skipped 6 files)
$ black --check . -> 17 files would be left unchanged
$ docformatter --check --diff .  -> exit 0   (nicht Teil des Gates, s. ADR 7)
$ python -m pretix makemigrations --check --dry-run -> "No changes detected"
```

Die 3 Warnungen stammen ausschließlich aus pretix core (Django-5.2-Deprecation,
zwei `nav_organizer`/`register_multievent_data_exporters`-Hinweise aus
`stripe`/`ticketoutputpdf`), nicht aus diesem Plugin.

Die Smoke-Tests prüfen: Modulimport, Entry-Point-Registrierung, App-Config,
Vollständigkeit der `PretixPluginMeta`, Übereinstimmung von `compatibility` mit
der installierten pretix-Version, Auftauchen in `get_all_plugins(event=...)`,
Auflösbarkeit der `navigation_links`, Platzhalterseite 200 / 403, Menüpunkt
sichtbar / versteckt / stumm bei deaktiviertem Plugin, Gültigkeit des
Permission-Keys und Abwesenheit einer Migration.

### 6. Statusbericht — diese Datei

---

## Nicht erledigt (und warum)

- **Keine Migration.** Ausschließlich `persistence-dev` (harte Regel).
  `migrations/` existiert als Paket, ist aber leer.
- **Keine Fachlogik.** `models.py`, `forms.py`, `exporters.py` sind leer, alle
  Fachpakete leer. Jede vorweggenommene Struktur würde die Contracts aus
  Welle 0c binden, bevor sie geprüft sind.
- **Kein Organizer-Menüpunkt, kein `nav_organizer`.** Laut Auftrag Sache des
  `integrator`.
- **Kein `de`-Katalog, kein `locale/de/`.** Nur `locale/.gitkeep`. Quellstrings
  sind englisch; den Katalog pflegt ausschließlich der `integrator`
  (`CLAUDE.md` Regel 8). `make` ist auf diesem System nicht installiert und
  `msgfmt` fehlt (ENVIRONMENT.md Stolperstein 2 und 3) — das `Makefile` ist
  angelegt, aber hier nicht ausführbar.
- **`[project.urls]` fehlt.** Es gibt kein veröffentlichtes Repository; eine
  erfundene URL wäre schlechter als keine. Nachzutragen vom `integrator`.
- **Kein `git commit`.** Der Orchestrator committet zwischen den Wellen.

---

## Getroffene Entscheidungen

Alle in **ADR 0000** (`docs/adr/0000-setup.md`) mit Begründung und Fundstelle im
pretix-Source. Kurzfassung:

| # | Entscheidung |
|---|---|
| 1 | pretix-Zielversion exakt `2026.6.0` — bei Mismatch ruft pretix `sys.exit(1)` |
| 2 | Python 3.12.6, deklariert `requires-python = ">=3.11"` (von pretix übernommen) |
| 3 | Cookiecutter-Layout, `PretixPluginMeta` in `apps.py`, Kategorie `FORMAT`, **null** Laufzeitabhängigkeiten, Version 0.1.0 |
| 4 | **Lizenz Apache-2.0** — offene Frage aus `SPEC.md:33` hiermit entschieden |
| 5 | Verdrahtung: URL-Wurzel-Präfix, `event.orders:read`, `nav_event`, `navigation_links`-Tupelform |
| 6 | Testkonventionen: `pretix.testutils.settings`, Marker `performance`, `scopes_disabled()` selbst setzen, Teams statt `is_staff` |
| 7 | flake8/isort wörtlich aus dem Cookiecutter, `black` dazu, `docformatter` mit abgeschaltetem Wrapping und **nicht** im Gate; `scripts/**` und `.claude/**` ausgeschlossen |
| 8 | CI: pretix gepinnt, Action-Majors angehoben, Linter in einem Job, zusätzlicher `migrations`-Job |
| 9 | Bewusst nicht entschieden: Datenmodell, Registry, Schema — gehört Welle 0c |

**Contract-Abweichungen: KEINE.** In Welle 0a existieren noch keine Contracts;
es wurde auch nichts vorweggenommen, was sie einschränken würde.

---

## Für die nächsten Agents wichtig

Drei Dinge, an denen man sich sonst verlässlich stößt (Details in ADR 0000
Abschnitt 5 und 6):

1. **Permission-Keys.** `SPEC.md:105` nennt noch `can_view_orders` /
   `can_change_event_settings`. In 2026.6.0 gilt die Doppelpunkt-Form. Nutze
   `pretix_custom_reports.signals.VIEW_PERMISSION` (`event.orders:read`), nicht
   den Legacy-String. **`event.items:read` existiert nicht** — gültige Keys sind
   `{group}:{action}` aus den in `pretix/base/permissions.py` deklarierten
   `actions`; `assert_valid_event_permission` wirft sonst eine Exception.
2. **URLs.** Plugin-`urlpatterns` hängen an der **URL-Wurzel**, nicht unter
   `/control/`. Neue Control-Routen brauchen den vollen Präfix
   `^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/…`. Namespace:
   `plugins:pretix_custom_reports:`.
3. **`django-scopes` in Tests.** Der `scopes_disabled()`-Automatismus aus
   `pretix/src/tests/conftest.py` gilt für uns **nicht**. Fixtures, die
   `Event` oder darunter anlegen, brauchen ein explizites `scopes_disabled()` —
   `tests/conftest.py` macht das vor.

**Paket-`__init__.py` bitte importfrei lassen.** `views/__init__.py`,
`contracts/__init__.py` usw. enthalten nur Docstrings mit der
Eigentümer-Übersicht. Re-Exports dort machen sie wieder zu gemeinsamen
Schreibzielen — genau die Kollision, die sie verhindern sollen. Module direkt
importieren: `from .views.crud import ...`.

**Eigentumsübergang ab Welle 1** — Kopfkommentar steht in jeder Datei:

| Datei | neuer Eigentümer |
|---|---|
| `apps.py`, `signals.py`, `urls.py`, `__init__.py` | `integrator` |
| `setup.py`, `pyproject.toml`, `setup.cfg`, `Makefile`, `.github/**`, `README.rst` | `integrator` |
| `models.py`, `forms.py`, `migrations/**` | `persistence-dev` |
| `exporters.py` | `exporter-dev` |
| `contracts/**` | `contract-architect` |
| `tests/conftest.py` | `test-engineer` |
| `tests/test_smoke.py` | `integrator` |
| `views/placeholder.py` | `frontend-dev` (darf sie beim Bau des Editors löschen) |

`signals.py` enthält am Ende eine kommentierte Liste der noch fehlenden
Registrierungen mit Welle und zuständigem Agent — als Landkarte für den
`integrator`.

---

## Offene Anforderungen an andere

Keine Datei unter `handoff/requests/` angelegt. Zwei Punkte zur Kenntnis:

- **An `env-setup`:** `scripts/**` ist in `setup.cfg` (flake8, isort) und
  `pyproject.toml` (black, docformatter) von den Gate-Befehlen **ausgeschlossen**.
  Grund: `flake8 .` vom Repo-Root hätte sonst `scripts/seed_demo.py` und
  `scripts/verify/*.py` erfasst — fremdes Gebiet (`CLAUDE.md` Regel 9). Wenn du
  deine Skripte mit prüfen lassen willst, ist der Ausschluss zu entfernen und
  die Skripte einmal zu formatieren.
- **An `integrator`:** `[project.urls]` in `pyproject.toml` fehlt und ist beim
  Release nachzutragen. `black` und `docformatter` sind bewusst nicht als
  `optional-dependencies` deklariert, damit du die Release-Metadaten frei
  gestalten kannst.

Kein Eintrag in `handoff/blockers.md` — die Punkte 2 und 3 der DoD sind erfüllt,
die nächste Welle kann starten.

---

## Änderungen außerhalb des Repos (transparent, weil andere sie sehen)

1. **Dev-Server auf Port 8000 wurde neu gestartet.** Er lief seit vor
   `pip install -e .` und konnte den neuen Entry Point daher nicht kennen
   (ENVIRONMENT.md: „Neu registrierte Plugins brauchen einen echten Neustart") —
   er lieferte 404 auf die Plugin-URL. Nach dem Neustart über
   `bash scripts/start-dev.sh` sind alle Prüfungen grün. Der Server läuft.
   *Nebeneffekt:* Der Vite-Prozess des alten Servers hängt noch auf Port 5173
   (Beenden wurde vom Berechtigungssystem abgelehnt). Unkritisch — er bedient
   denselben pretix-Checkout, der neue Vite hat sich einen freien Port genommen.
   Wer aufräumen will: `taskkill //F //IM node.exe`.
2. **Zwei venv-Pakete nachinstalliert:** `black 26.5.1`, `docformatter 1.7.8`,
   dazu `check-manifest 0.51` und `build 1.5.0`. Reine Dev-Werkzeuge, **keine**
   Laufzeitabhängigkeit des Plugins (`dependencies = []`).
3. **Ein Nutzer und ein Team in der Dev-DB angelegt** (idempotentes Skript), für
   den Negativtest aus DoD 4:

   | | |
   |---|---|
   | E-Mail | `pcr-limited@localhost` |
   | Passwort | `limited-pw-4711` |
   | Team | „PCR-Test (nur Produkte, KEIN Bestell-Leserecht)" bei `demo` |
   | Rechte | `all_events=True`, `limit_event_permissions={"event.items:write": True}` |

   Damit kann jeder Agent das Verstecken von Menüpunkten selbst nachprüfen. Ein
   `bash scripts/reset-dev.sh` entfernt den Nutzer wieder — dann
   `python D:/Projekte/juki/staging/make_limited_user.py` erneut ausführen (siehe
   Kopfkommentar dort).
4. **Plugin in `demo/demo-event` aktiviert** (persistiert), in `demo/demo-serie`
   nicht.
5. **Wegwerf-Skripte in `D:\Projekte\juki\staging\`** — nicht im Repo, weil
   `scripts/**` `env-setup` gehört:
   `verify_bootstrap.py` (HTTP-Prüfung gegen den laufenden Server, 15 Checks),
   `verify_bootstrap_db.py` (dieselben Prüfungen direkt gegen die Dev-DB über
   `django.test.Client`, 20 Checks), `make_limited_user.py`.

   Wiederholbar:
   ```bash
   python D:/Projekte/juki/staging/verify_bootstrap.py            # Server muss laufen
   ```
   Falls diese Prüfungen dauerhaft nützlich erscheinen, gehören sie als
   Anforderung an `env-setup` nach `scripts/verify/`.

---

## Tests

**14 passed, 0 failed** (`pytest`, gleiches Ergebnis mit
`-m "not performance"`).
Zusätzlich 15 Live-HTTP-Prüfungen und 20 Prüfungen gegen die Dev-Datenbank, alle
PASS.

---

## Nächster Schritt

Welle 0a ist abgeschlossen und die Gates sind erfüllt. Vor Welle 0b:

1. **Selbst im Browser prüfen** (ORCHESTRIERUNG.md Abschnitt 8 sieht hier einen
   Stopp vor): <http://localhost:8000/control/event/demo/demo-event/> —
   „Exports" muss in der Seitenleiste stehen und die Platzhalterseite öffnen.
   Gegenprobe mit `pcr-limited@localhost` / `limited-pw-4711` auf
   <http://localhost:8000/control/event/demo/demo-event/items/> — dort darf
   „Exports" **nicht** erscheinen.
2. **Committen** (nur der Orchestrator). Erst danach ist `check-manifest`
   dauerhaft ohne `git add`-Trick grün.
3. Dann **Welle 0b: `pretix-researcher`** → `docs/pretix-api-notes.md`.
   Die vier Verdrahtungsdetails aus ADR 0000 Abschnitt 5 sind dort belastbar und
   können übernommen werden; offen und für Welle 0b/0c relevant sind vor allem
   `ListExporter`/`MultiSheetListExporter`, `ScheduledEventExport`,
   `register_data_exporters` und das Event-Copy-Signal.
