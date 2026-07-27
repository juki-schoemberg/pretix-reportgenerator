# ADR 0000 — Grundaufbau: Zielversion, Paketierung, Tooling, Testkonventionen

- **Status:** akzeptiert
- **Datum:** 2026-07-27
- **Autor:** `bootstrap-dev` (Welle 0a)
- **Betrifft:** alle Agents. Punkte 1, 3, 7 und 8 sind für spätere Wellen bindend.

---

## Kontext

Das Plugin wird von bis zu vier Agents parallel weitergebaut. Alles, was hier
nicht festgelegt ist, wird später mehrfach und widersprüchlich entschieden.
Diese ADR hält deshalb die Entscheidungen fest, die das Fundament betreffen —
und nur diese. Fachliche Entscheidungen (Datenmodell, Registry, Filterlogik)
gehören bewusst **nicht** hierher; sie entstehen in Welle 0c in `contracts/`.

Alle pretix-Aussagen unten wurden im installierten Source unter
`../pretix/src/pretix` verifiziert, nicht aus dem Gedächtnis übernommen
(CLAUDE.md Regel 1). Die jeweilige Fundstelle steht dabei.

---

## 1. pretix-Zielversion: exakt `2026.6.0`

```python
# pretix_custom_reports/apps.py
compatibility = "pretix==2026.6.0"
```

Ermittelt mit

```
$ python -c "import pretix; print(pretix.__version__, pretix.__file__)"
2026.6.0 D:\Projekte\juki\pretix\src\pretix\__init__.py
$ python -c "import importlib_metadata as m; print(m.version('pretix'))"
2026.6.0
```

**Warum exakt und nicht `>=`:** `PluginConfig.__init__` in
`pretix/base/plugins.py` prüft die Angabe beim Laden der App-Config und ruft bei
Nichterfüllung **`sys.exit(1)`**. Ein falscher Pin nimmt also nicht nur das
Plugin außer Betrieb, sondern den gesamten Server — inklusive aller anderen
Veranstaltungen. Ein offener oder geratener Pin ist deshalb keine
Bequemlichkeit, sondern ein Ausfallrisiko.

Das Cookiecutter schlägt `pretix>=<min_basever>` vor. Wir weichen bewusst ab:
solange es keine Kompatibilitätsmatrix gibt, die gegen mehrere pretix-Versionen
getestet ist, wäre `>=` eine unbelegte Behauptung. Der Pin wird erst geweitet,
wenn CI gegen mehr als eine Version läuft — das ist Sache des `integrator`.

Notausgang für lokale Experimente, ohne die Datei zu ändern:
`PRETIX_IGNORE_CONFLICTS=True` (wird in `PluginConfig.__init__` ausgewertet).

`SPEC.md` Abschnitt 1 ließ die Zielversion offen (`<HIER EINTRAGEN>`); sie ist
damit hier entschieden. `ENVIRONMENT.md` pinnt den Klon zusätzlich auf den Tag
`v2026.6.0`, Commit `fd565ecdb29c55a3e82dc15d94a848d193664caa`.

## 2. Python 3.12.6, deklariert als `requires-python = ">=3.11"`

Entwickelt und getestet auf 3.12.6 (`C:\Python312`, das venv unter
`../venv`). Die Untergrenze `>=3.11` ist von `../pretix/pyproject.toml`
übernommen — ein Plugin darf nicht strenger sein als sein Host. Django 5.2.16.

Kein Django-5.2-Feature wird eingesetzt, das in der Zielversion nicht schon von
pretix selbst benutzt wird (`SPEC.md` Abschnitt 1).

## 3. Paketierung: Cookiecutter-Layout, `pyproject.toml` + `setup.py`-Shim

Vorlage: `pretix/pretix-plugin-cookiecutter` (HEAD `9ef6054`), gegengeprüft an
`pretix/pretix-passbook` als real veröffentlichtem externem Plugin. Übernommen
wurden: `pyproject.toml`-Struktur, `setup.py` als reiner Shim, `MANIFEST.in`,
`Makefile`, `setup.cfg`, `pretixplugin.toml`, `.gitignore` und die
Entry-Point-Registrierung.

**Entry Point und `PretixPluginMeta`.** Verifiziert in `pretix/settings.py`:
die Plugin-Erkennung liest ausschließlich den *Modul*-Teil des Entry Points
(`metadata.entry_points(group='pretix.plugin')` → `entry_point.module`), der
Attributteil wird nie aufgelöst. `get_all_plugins()` in
`pretix/base/plugins.py` scannt danach die App-Configs nach einer inneren Klasse
`PretixPluginMeta`. Deshalb:

- `pretix_custom_reports/__init__.py` enthält nur `__version__` und bleibt
  importfrei — es ist zusätzlich die Quelle der dynamischen Paketversion.
- `PretixPluginMeta` liegt in `apps.py` in einer `PluginConfig`-Subklasse mit
  `default = True`, damit Django die App-Config automatisch findet.

**Kategorie `FORMAT`** ("Output and export formats"), wie das Core-Plugin
`pretix/plugins/reports` (`apps.py`). Ein Report-Generator ist ein Ausgabeformat,
kein Feature im Sinne der pretix-Kategorien.

**Keine Laufzeitabhängigkeiten.** `dependencies = []`. Das ist Absicht und
gleichzeitig eine harte Regel für alle folgenden Agents: alles, was gebraucht
wird, bringt pretix bereits mit (Django, DRF, `django-scopes`, `openpyxl` via
`ListExporter`, `jsonschema` …). Wer eine neue Abhängigkeit braucht, begründet
sie in einer **eigenen** ADR und trägt sie ein; still hinzufügen ist nicht
erlaubt. Grund: jede zusätzliche Abhängigkeit ist bei selbst gehosteten
pretix-Installationen ein Upgrade-Risiko und eine Angriffsfläche.

**Version 0.1.0** als Startpunkt. Erst der `integrator` vergibt 1.0.0.

**`[project.urls]` fehlt bewusst.** Es gibt noch kein veröffentlichtes
Repository; eine erfundene URL wäre schlechter als keine. Nachzutragen vom
`integrator` beim Release.

## 4. Lizenz: Apache-2.0

`SPEC.md` Abschnitt 1 ließ die Lizenz offen (`<HIER EINTRAGEN>`). Das
Cookiecutter bietet „Apache" oder „pretix Enterprise" an. Gewählt:
**Apache-2.0**, weil

- das Plugin ein eigenständiges Werk neben pretix ist und pretix selbst
  Apache-lizenzierte Beiträge in seinen AGPL-Kern aufnimmt — Apache-2.0 ist im
  Ökosystem die kompatibelste Wahl für externe Plugins,
- „pretix Enterprise" eine kommerzielle Lizenz der pretix GmbH ist und für ein
  Fremdplugin nicht in Frage kommt.

`LICENSE` enthält den Kurzhinweis in der Form, die das Cookiecutter erzeugt
(Volltext per URL referenziert). `MANIFEST.in` nimmt die Datei ins sdist auf,
`check-manifest` verlangt sie.

**Rechteinhaber und Autor: „JuKi Schömberg e.V."** — vom Auftraggeber
festgelegt. Eingetragen in `LICENSE` (Copyright), `pyproject.toml`
(`authors`/`maintainers`) und `PretixPluginMeta.author` in `apps.py`; letzteres
ist der Name, den pretix in der Plugin-Übersicht anzeigt. **Ohne
E-Mail-Adresse**, weil keine genannt wurde und eine erfundene Kontaktadresse in
Paketmetadaten schlechter ist als gar keine — vom `integrator` beim Release
nachzutragen, zusammen mit `[project.urls]` (Abschnitt 3).

Das ist eine Paketierungs-, keine Contract-Frage — daher hier entschieden und
nicht eskaliert. Widerspruch bitte über `handoff/requests/`.

## 5. Walking Skeleton: Verdrahtung, wie sie in 2026.6.0 tatsächlich ist

Vier versionsabhängige Eigenheiten, jede im Source geprüft. Sie sind der Grund,
warum hier abgeschrieben und nicht ausgedacht wurde:

**a) URLs hängen an der URL-Wurzel, nicht unter `/control/`.**
`pretix/multidomain/maindomain_urlconf.py` bindet `urlpatterns` eines Plugins
ohne Präfix ein. Control-Routen müssen den vollen Pfad ausschreiben, wie
`pretix/plugins/webcheckin/urls.py`:

```python
re_path(r'^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports/$', ...)
```

Der Namespace ist `plugins:` + `AppConfig.label`, hier also
`plugins:pretix_custom_reports`. Route-Name: `event.index` — mit `event.`-Präfix,
damit die Organizer-Route des `integrator` später `organizer.index` heißen kann,
ohne dass ein nichtssagendes `index` mehrdeutig wird.

**b) Permission-Key ist die neue Doppelpunkt-Form.**
`SPEC.md` Abschnitt 4 nennt noch die Legacy-Keys `can_view_orders` /
`can_change_event_settings`. In 2026.6.0 gilt
`pretix/helpers/permission_migration.py`:
`"can_view_orders" → ["event.orders:read"]`. Verwendet wird **`event.orders:read`**
— derselbe Key, mit dem pretix seinen eigenen Export-Menüpunkt gated
(`pretix/control/navigation.py`). Definiert als
`pretix_custom_reports.signals.VIEW_PERMISSION`, damit View und Navigation nicht
auseinanderlaufen können.

Wichtig für `persistence-dev`: Gültige Keys entstehen in
`pretix/base/permissions.py` als `{group}:{action}` aus den deklarierten
`actions`. `event.items:read` existiert **nicht** (die Gruppe `event.items`
deklariert nur `write`), `event.orders:read` schon. `assert_valid_event_permission`
wirft bei unbekannten Keys eine Exception.

**c) Navigation über `nav_event`.** `pretix/control/signals.py`, ein
`EventPluginSignal` — feuert also nur für Events, in denen das Plugin aktiv ist.
Der Receiver gibt bei fehlendem Recht `[]` zurück (Form abgeschrieben von
`pretix/plugins/webcheckin/signals.py`). Damit ist F1 aus `SPEC.md` auf
Event-Ebene erfüllt; die Organizer-Ebene kommt vom `integrator`.

**d) `navigation_links` sind `(label, urlname, kwargs)`.**
`EventPlugins.prepare_links` in `pretix/control/views/event.py` injiziert
`organizer` und `event` selbst — sie dürfen **nicht** mitgegeben werden. Ein
`NoReverseMatch` wird für Event-Level-Plugins weitergereicht und zerlegt die
Plugin-Seite; `tests/test_smoke.py::test_navigation_link_target_reverses` sichert
das ab.

## 6. Testkonventionen

- **Runner:** pytest + pytest-django, `DJANGO_SETTINGS_MODULE =
  pretix.testutils.settings` in `setup.cfg` — die Konvention für
  Out-of-tree-Plugins (Cookiecutter, passbook). pretix core benutzt
  `tests.settings`, das für uns nicht erreichbar ist.
- **Marker `performance`** ist in `setup.cfg` registriert, damit
  `test-engineer` ihn in Welle 3 ohne Warnung benutzen kann.
  `pytest -m "not performance"` ist der schnelle Lauf und das, was CI fährt.
- **`django-scopes` muss selbst behandelt werden.** `pretix/src/tests/conftest.py`
  umhüllt jede Non-Generator-Fixture über einen `pytest_fixture_setup`-Hook mit
  `scopes_disabled()`. Dieser Hook gilt **nur** innerhalb des pretix-Repos, nicht
  für uns. Unsere Fixtures öffnen deshalb explizit `scopes_disabled()`. Wer das
  vergisst, bekommt einen schwer lesbaren `ScopeError` statt eines Testfehlers.
- **Rechte kommen aus `Team`-Objekten, nicht aus `is_staff`.** In 2026.6.0:
  `all_event_permissions` (bool) plus `limit_event_permissions` als **JSONField
  in der Form `{key: True}`** — keine Liste (`pretix/base/models/organizer.py`,
  Kommentar dort: sonst fehlen Lookups unter SQLite).
- **Der Negativtest braucht ein Team, kein fehlendes Team.** Der Nutzer ohne
  Rechte hat `all_events=True` und `limit_event_permissions={"event.items:write":
  True}`. Nur so lädt die Event-Seite überhaupt und der Test beweist wirklich,
  dass der Menüpunkt *versteckt* wird. Ein Nutzer ganz ohne Team würde nur ein
  404 auf das Event belegen — eine andere Aussage.
- **Basis-Fixtures** in `tests/conftest.py`: `organizer`, `event`,
  `event_without_plugin`, `user_with_perms`, `user_without_perms`,
  `client_with_perms`, `client_without_perms`, `fixture_dir`. Ab Welle 1 gehört
  die Datei `test-engineer`.
- **Migrationen im Test:** `pretix.testutils.settings` setzt
  `MIGRATION_MODULES = DisableMigrations()`, **außer** `GITHUB_WORKFLOW` ist
  gesetzt. Lokal entstehen die Tabellen also über `--run-syncdb`, in CI laufen
  die echten Migrationen. Für `persistence-dev` heißt das: ein Migrationsfehler
  fällt möglicherweise erst in CI auf, nicht lokal.

## 7. Formatierung und Lint

**Nativ im pretix-Ökosystem sind nur `flake8` und `isort`.** Deren Konfiguration
in `setup.cfg` ist wörtlich aus dem Cookiecutter übernommen, inklusive der
Eigenheit `known_standard_library = typing`. Diese Zeile ersetzt die
Standardbibliotheks-Liste von isort statt sie zu erweitern, wodurch `datetime`,
`re` usw. als Third-Party gelten und in einem Block mit `django` und `pretix`
landen. Das sieht falsch aus, ist aber genau das Ergebnis, das man in
veröffentlichten pretix-Plugins vorfindet (`pretix_passbook/signals.py` beginnt
mit `from collections import OrderedDict` direkt gefolgt von den django- und
pretix-Importen). Bewusst **nicht** auf das `extra_standard_library` aus pretix
core geändert: das Ökosystem-Ergebnis abzuschreiben ist hier wertvoller als
formale Korrektheit, und ein späterer Wechsel würde jede Datei anfassen.

`black` ist keine pretix-Abhängigkeit, aber im Cookiecutter fest verankert:
`.install-hooks.sh` und der Style-Workflow fahren `black --check .`. Wir
übernehmen es (`line-length = 88`, passend zu `isort profile = black`) und haben
es ins venv nachinstalliert.

**`docformatter` verwendet das pretix-Ökosystem nicht** — weder pretix core noch
das Cookiecutter. Es ist trotzdem konfiguriert, weil der Bootstrap-Auftrag es
verlangt, aber mit `wrap-summaries = 0` und `wrap-descriptions = 0`. Grund: mit
aktivem Wrapping formatiert docformatter Prosa um und **zerstörte im ersten Lauf
die reST-Eigentümertabelle in `views/__init__.py`**. Ebenso ist
`pre-summary-newline` deaktiviert, weil es einzeilige Summaries künstlich auf
drei Zeilen aufbläht und damit gegen den pretix-Stil arbeitet. docformatter ist
deshalb **nicht** Teil des verbindlichen Gates
`flake8 . && isort -c . && black --check .` — nur ein zusätzlicher CI-Schritt.
Wer es lästig findet, kann es ersatzlos entfernen; es sichert nichts, was
`flake8` nicht schon prüft.

**Ausschlüsse (`scripts/**`, `.claude/**`).** Die Gate-Befehle laufen laut
`CLAUDE.md` vom Repo-Root aus und würden sonst `scripts/seed_demo.py` und
`scripts/verify/*.py` erfassen — Eigentum von `env-setup`, siehe
Ownership-Tabelle. Ohne Ausschluss hätte Gate 5 entweder fehlgeschlagen oder
fremde Dateien umformatiert; beides verstößt gegen `CLAUDE.md` Regel 9. Die
Ausschlüsse stehen in `setup.cfg` (flake8 `exclude`, isort `skip_glob`) und in
`pyproject.toml` (`[tool.black] extend-exclude`, `[tool.docformatter] exclude`).
Wer `scripts/` künftig mit prüfen will, muss das mit `env-setup` klären.

## 8. CI

Zwei Workflows, angelehnt an den Cookiecutter, mit drei bewussten Abweichungen:

1. **pretix exakt gepinnt** (`pip3 install "pretix==2026.6.0"`) statt
   `pip3 install pretix`. Sonst testet CI gegen eine andere Version als die, auf
   die `compatibility` zeigt — und der Lauf endet in `sys.exit(1)` statt in einem
   lesbaren Fehler.
2. **Action-Majors angehoben** (`checkout@v4`, `setup-python@v5`). Die
   Cookiecutter-Version nutzt `@v1`/`@v2`, die auf GitHub-Runnern
   Deprecation-Fehler erzeugen.
3. **Vier Linter in einem Job** statt vier Jobs. Die pretix-Installation
   dominiert die Laufzeit; vier Jobs installieren sie viermal.

Zusätzlich ein Job `migrations` (`python -m pretix makemigrations --check
--dry-run`, lokal verifiziert: „No changes detected"). Er ist heute trivial grün
und wird ab Welle 1 zum Schutz gegen ein vergessenes `makemigrations` — der
klassische Fehler bei parallel arbeitenden Agents.

`check-manifest` und `python setup.py sdist` laufen im `packaging`-Job. Beides
lokal geprüft (`check-manifest`: „lists of files in version control and sdist
match", nach temporärem `git add -A`, weil das Tool gegen die Versionskontrolle
vergleicht und in Welle 0a noch nichts committet ist).

## 9. Was hier bewusst nicht entschieden wurde

`models.py`, `forms.py` und `exporters.py` sind leer und tragen nur einen
Eigentümer-Kommentar. Es existiert **keine** Migration. Kein Feld, kein Filter,
kein Registry-Eintrag, kein Schema. Das ist keine Unvollständigkeit, sondern die
Bedingung dafür, dass Welle 0c die Contracts frei entwerfen kann: jede hier
vorweggenommene Struktur wäre eine ungeprüfte Vorentscheidung, an der sich vier
parallele Agents anschließend ausrichten müssten.

Das Datenmodell aus `SPEC.md` Abschnitt 5 ist ausdrücklich ein *Vorschlag*
(„validieren, gern verbessern") und darf nicht als beschlossen behandelt werden.

---

## Konsequenzen

**Positiv**

- Das Skelett ist nachweislich aktivierbar und der Menüpunkt nachweislich
  rechteabhängig (Belege in `handoff/status/bootstrap-dev.md`).
- Alle Paketverzeichnisse existieren samt `__init__.py`. Insbesondere
  `views/__init__.py` — hätte es gefehlt, hätten `persistence-dev`,
  `frontend-dev` und `portability-dev` es in Welle 1 gleichzeitig angelegt.
- Die vier Verdrahtungs-Eigenheiten aus Abschnitt 5 sind einmal richtig
  festgehalten und durch Smoke-Tests abgesichert, statt in jeder Welle neu
  erraten zu werden.

**Negativ / Preis**

- Der exakte pretix-Pin erzwingt bei jedem pretix-Upgrade eine bewusste
  Änderung an `apps.py`, `README.rst` und beiden Workflows.
- Die aus dem Cookiecutter übernommene isort-Eigenheit produziert
  Import-Blöcke, die außerhalb des pretix-Ökosystems falsch aussehen.
- `black` und `docformatter` sind Dev-Abhängigkeiten, die kein pretix-Setup
  mitbringt; wer nur `pip install -e .` macht, kann Gate 5 nicht fahren.
  (Bewusst nicht als `[project.optional-dependencies]` deklariert, um dem
  `integrator` die Release-Metadaten nicht vorzuschreiben.)
- `scripts/**` ist von Lint und Formatierung ausgenommen und wird dort nie
  geprüft.

**Bindend für spätere Wellen**

1. Keine neue Laufzeitabhängigkeit ohne eigene ADR (Abschnitt 3).
2. Feldzugriff nur über die Registry, Permissions nur über pretix-Mixins
   (`CLAUDE.md` Regeln 2–4) — hier nicht wiederholt, aber unverändert gültig.
3. `views/__init__.py`, `contracts/__init__.py` und die anderen Paket-`__init__`
   bleiben importfrei. Re-Exports dort machen sie zu gemeinsamen
   Schreibzielen — genau die Kollision, die sie verhindern sollen.
4. `event.orders:read` als Lese-Permission (Abschnitt 5b). Eine Änderung
   betrifft Navigation, View und Exporter gleichzeitig und braucht eine ADR.
