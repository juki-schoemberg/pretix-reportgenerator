# Status — `integrator`, Welle 4 (Verdrahtung, i18n, Doku, Release)

**Datum:** 2026-08-02
**Ausgangsstand:** `f5959fb` („Welle 3 Nacharbeit: S-001 und S-002 …")
**Betriebsart:** Modus A, seriell, kein anderer Agent lief parallel.
**Nicht committet** — laut Auftrag committet der Orchestrator.

Alle Änderungen sind **gestaged** (`git add -A`), damit `git status` und
`git diff HEAD` den vollständigen Umfang zeigen. `git diff` ohne Argument zeigt
nichts, weil nichts unstaged ist.

---

## 1. Ergebnis in Zahlen

| Tor | Ergebnis |
| --- | --- |
| `pytest -m "not performance"` | **1 failed, 1012 passed, 8 xfailed** — der Fehlschlag liegt in fremdem Gebiet, siehe Abschnitt 6 |
| `pytest -m performance` | **8 passed** (106 s) |
| `black --check .` | sauber (77 Dateien) |
| `isort -c .` | sauber |
| `flake8 .` | sauber |
| `docformatter --check .` | sauber |
| `check-manifest .` | sauber |
| `python -m pretix makemigrations pretix_custom_reports --check --dry-run` | „No changes detected", Exit 0 |
| Testsuite mit echten Migrationen (`GITHUB_WORKFLOW=1`, CI-Äquivalent) | dasselbe Bild — die Migration läuft gegen eine leere DB |
| `pip install -e .` | erfolgreich, Entry Point registriert |
| `python -m build --sdist` | 129 Dateien, `locale/`, `templates/`, `static/`, `LICENSE`, `README.rst`, `CHANGELOG.rst` enthalten |
| `handoff/requests/` | leer (neun Dateien nach `handoff/requests/erledigt/` verschoben und markiert) |

Vorher: 1004 passed, 1 failed (`test_no_migration_created_yet`). Nachher:
1012 passed, 1 failed (ein anderer, fremder Test). Netto acht neue Tests, alle
in `tests/test_smoke.py` (meine Datei).

---

## 2. Was verdrahtet wurde

### `urls.py` — 20 Routen

`urls.py` definiert selbst genau **eine** Route (`event.index`) und verkettet
sechs Modulvariablen. Jede Route steht damit genau einmal im Repo, neben ihrer
View.

| Quelle | Routen |
| --- | --- |
| `views/crud.py::event_urlpatterns` (persistence-dev) | 5 |
| `views/editor.py::editor_urlpatterns` (frontend-dev) | 2 |
| `views/api.py::api_urlpatterns` (frontend-dev) | 3 |
| `views/portability.py::portability_event_urlpatterns` (portability-dev) | 2 |
| `views/templates.py::templates_event_urlpatterns` (portability-dev) | 2 |
| `views/templates.py::templates_organizer_urlpatterns` (portability-dev) | 5 |
| `urls.py` selbst | 1 (`event.index`) |

Die beiden `api.examples`/`api.example`-Zeilen aus der alten Fassung von
`frontend-dev`s Request wurden **nicht** übernommen (die Mock-Views existieren
nicht mehr, es hätte einen `ImportError` beim Serverstart gegeben).

Neuer Test `test_every_route_reverses_and_resolves_back_to_itself` prüft, dass
sich alle 20 reversen lassen und wieder auf sich selbst auflösen — die Zusage
„die Präfixe überschneiden sich nicht" ist damit geprüft und nicht geglaubt.
Dazu `test_no_route_name_and_no_url_pattern_is_used_twice` und
`test_every_route_list_of_every_agent_is_wired_up` (fängt ein verlorenes `+`).

### `signals.py` — sechs Empfänger plus die Log-Typen

| Was | Herkunft |
| --- | --- |
| `nav_event` (unverändert) | Welle 0a |
| `nav_organizer` → „Report templates" | portability-dev, Request Abschnitt 4 |
| `register_data_exporters.connect(...)` | exporter-dev |
| `register_multievent_data_exporters.connect(...)` | exporter-dev |
| `event_copy_data` → `copy_reports_to_event` | portability-dev, eigener Request |
| `import registry.cache` (Cache-Invalidierung) | registry-dev |
| `ReportLogEntryType` mit sieben Action-Types | persistence-dev (optionaler Vorschlag) |

Die Platzhalterkommentare (Zeilen 52-62 alt) sind ersetzt.

### `apps.py`, `__init__.py`

Unverändert. Beide brauchten nichts (`ready()` importiert `signals`, das reicht
für den Registry-Cache-Import; ADR 0002 Abschnitt 7 lässt `signals.py` oder
`apps.py` zu, ich habe `signals.py` gewählt, weil dort schon alle Empfänger
stehen).

### Fremde Templates: die drei Knöpfe aus `portability-dev`s Abschnitt 6

`templates/pretix_custom_reports/report_list.html` (persistence-dev) hat jetzt
„Import a report" und „Load a template" neben „Create a new report" — **an
beiden Stellen**, also auch im `empty-collection`-Zweig, weil man ohne einen
einzigen Report sonst nie importieren könnte. Dazu ein Export-Knopf je Zeile.

Der Export-Knopf steht bewusst **außerhalb** von `{% if can_change %}`:
`ReportExportView` verlangt nur `event.orders:read`, die anderen beiden
`event.settings.general:write`.

`editor.html` (frontend-dev) brauchte nichts — `frontend-dev` hat die drei
Links in Welle 2 selbst eingebaut (`portability.export/import/templates` im
JSON-Panel, `views/editor.py::portability_urls`).

---

## 3. Getroffene Entscheidungen (alle in `docs/adr/0006-verdrahtung.md`)

**a) `event.index` zeigt auf `ReportListView`, `event.reports` bleibt.**
Der Menüpunkt muss zu etwas Benutzbarem führen. `event.reports` zu streichen
hätte Änderungen in `views/crud.py`, `views/portability.py`,
`views/templates.py` und drei Testdateien bedeutet — zwei URLs auf dieselbe
Liste sind der kleinere Preis. `tests/test_smoke.py` prüft jetzt die
Report-Liste statt der Platzhalterseite.

**b) Die `identifier`-vs-`report=<pk>`-Abweichung bleibt.**
`frontend-dev` hatte angeboten, `editor.edit` auf den PK umzustellen. Ich habe
abgelehnt: Die Editor-URL landet in Lesezeichen und soll eine Event-Kopie
überleben, dafür ist der Identifier gebaut (ADR 0001 Abschnitt 5.1). Die
CRUD-URLs sind Formularziele innerhalb einer Sitzung, dort ist der PK richtig.
**Für `frontend-dev` heißt das: nichts zu tun.**

**c) Der `nav_organizer`-Empfänger bekam eine Zeile mehr als vorgeschlagen.**
```python
if not organizer.events.filter(plugins__contains=PLUGIN_MODULE).exists():
    return []
```
Weil wir über den Legacy-Pfad an einem `OrganizerPluginSignal` hängen, läuft der
Empfänger für **jeden** Organizer — und `OrganizerPluginActiveMixin` antwortet
dort mit 404. Ein Menüpunkt, der garantiert ins Leere führt, ist schlechter als
keiner. Die Zeile stellt dieselbe Frage wie die View, sie entscheidet nichts
Neues.

**d) Log-Typen: `get_object_link_info` selbst gebaut.**
Der Vorschlag von `persistence-dev` war ungeprüft; im Source geprüft ergab sich:
`EventLogEntryType` liegt in `pretix.base.logentrytypes` (nicht
`…logentrytype_registry`), und die geerbte `get_object_link_info` reverst mit
`logentry.event.slug`. Eine Organizer-Vorlage hat `event=None` — genau der
Fallstrick, auf den `persistence-dev` hingewiesen hat. Überschrieben: Vorlagen
verlinken auf `organizer.templates.edit`, Event-Reports auf
`event.reports.edit`. Neuer Test
`test_the_log_object_link_survives_an_organizer_template`.
Kein Shredder-Mixin — `shred_pii` wird in 2026.6.0 nirgends aufgerufen, und
pretix' eigener `CoreEventLogEntryType` deklariert auch keines.

**e) `de`-Katalog in der Sie-Form.**
pretix pflegt `de` (Sie) und `de_Informal` (Du) getrennt — im Source geprüft.
Wir liefern `de`, also Sie. Eine Du-Form dort stünde in derselben Oberfläche
neben pretix' Sie-Form. (Der Auftrag sagte „Du-Form dort, wo es zum pretix-Ton
passt" — der pretix-Ton im `de`-Katalog **ist** die Sie-Form.) Ein
`de_Informal`-Katalog ist jederzeit nachrüstbar.

**f) `README.rst` bleibt `.rst`, kein `README.md`.**
`ORCHESTRIERUNG.md` §5 nennt „README.md", die Datei heißt seit Welle 0a
`README.rst`, `pyproject.toml` verweist darauf, ADR 0000 nennt sie so, und das
gesamte pretix-Plugin-Ökosystem benutzt `.rst`. Ich habe die vorhandene Datei
ausgebaut statt eine zweite anzulegen. Wenn du Markdown willst: `git mv` plus
eine Zeile in `pyproject.toml`, sonst nichts.

---

## 4. Übersetzungen

**442 Strings, 100 % übersetzt, keine fuzzy, keine leeren.**
`pretix_custom_reports/locale/de/LC_MESSAGES/django.po`.

Extraktion ohne `gettext`: Die Umgebung hat weder `xgettext` noch `msgfmt`
(`ENVIRONMENT.md` Stolperstein 3), `makemessages` ist damit nicht lauffähig.
Stattdessen Djangos eigenes `django.utils.translation.template.templatize()`
(derselbe Vorverarbeitungsschritt, den `makemessages` vor `xgettext` fährt) für
die Templates, `babel.messages.extract.extract_python` für den Python-Code, und
`polib` zum Schreiben. Erfasst: `_`, `gettext`, `gettext_lazy`, `gettext_noop`,
`ngettext`, `ngettext_lazy`, `pgettext`, `pgettext_lazy`, `npgettext`, plus
`{% trans %}` und `{% blocktrans %}` aus 14 Templates. Kein `-d djangojs` —
in den `.js`-Dateien steht bewusst kein übersetzbarer String
(`frontend-dev`s Hinweis, ADR 0005 Abschnitt 8).

Geprüft:

* Platzhalter maschinell gegengeprüft (`%(name)s`, `%s`, `{format}`) — 0
  Abweichungen zwischen `msgid` und `msgstr`, inklusive der drei Pluralformen.
* Katalog mit `polib` nach `.mo` kompiliert und zur Laufzeit gegen Django
  geladen: `translation.override("de")` liefert „Auswertungs-Editor",
  „Bestellnummer", „Auswertungs-Vorlagen"; `pgettext("export_category", …)` und
  `ngettext(…, 3)` funktionieren.
* Terminologie an pretix' eigenen `de`-Katalog angeglichen: Bestellnummer,
  Bestelldatum, Gesamtbetrag, Verkaufskanal, Rechnungsadresse, Firmenkunde,
  Leistungsempfänger, Zutrittsprodukt, Variante, Name Teilnehmer*in, **Termin**
  für `SubEvent`, Gutscheincode, Sitzplatz.
* Umlaute und Anführungszeichen: UTF-8, deutsche Anführungszeichen („…").

Die `.mo` ist gitignored (so vorgesehen: `pretix-plugin-build` kompiliert sie
beim Paketbau). Sie liegt trotzdem lokal im Arbeitsverzeichnis, damit man die
deutsche Oberfläche im Dev-Server sehen kann.

**Ein Übersetzungsdefekt, den der Katalog nicht lösen kann** — siehe offener
Punkt O-6.

---

## 5. Doku, Release, CI

* **`README.rst`** neu geschrieben: Screenshot-Platzhalter (drei
  `figure`-Direktiven auf `docs/img/`, Verzeichnis angelegt),
  Feature-Übersicht, Installation inklusive Rechtetabelle,
  Kompatibilitätsmatrix (pretix/Python/Django/DB/Formate/Abhängigkeiten),
  ein eigener Abschnitt **Scheduled exports**, **Known limitations**,
  Dev-Setup, Doku-Wegweiser. RST-Tabellen und Underlines maschinell auf
  Spaltenbreite geprüft (`twine`/`docutils` sind im venv nicht installiert,
  die Prüfung läuft in CI).
* **`docs/extending.md`** neu: `register_report_fields` mit vollständigem,
  lauffähigem Beispielplugin (die kommentierte Fassung des Beispiels aus
  `tests/test_registry_signal.py`, das dort mit echter Annotation gegen eine
  echte DB läuft), Regeltabelle, Ablehnungsgründe mit `reason`-Codes,
  Cache-Invalidierung (API im Source verifiziert:
  `invalidate_event`/`invalidate_organizer`, **nicht** `invalidate`), Grenzen.
* **`docs/adr/0006-verdrahtung.md`** neu: die acht Entscheidungen dieser Welle.
  Nummer 0006, weil 0003/0004 in Welle 1 reserviert wurden und leer blieben —
  die Lücke bleibt bewusst offen.
* **`CHANGELOG.rst`** neu, Eintrag `0.1.0 (unreleased)`. In `MANIFEST.in`
  aufgenommen, sonst wäre `check-manifest` rot geworden.
* **`CLAUDE.md`** Befehlsliste: `python -m pretix makemigrations
  pretix_custom_reports --check --dry-run`, mit Begründung (App-Label,
  `--dry-run`, nie `django-admin`).
* **`.github/workflows/tests.yml`**: derselbe Befehl im `migrations`-Job.
* **`Makefile`**: Kommentar mit dem `polib`-Ersatzweg für Umgebungen ohne
  `gettext`.

**Release-Check.** `pip install -e .` läuft, Entry Point ist registriert,
`python -m build --sdist` baut ein vollständiges Archiv, `check-manifest` ist
sauber, die Migration läuft gegen eine leere DB. Version bleibt `0.1.0`;
`PretixPluginMeta.version`, `__init__.__version__` und `pyproject` sind über
`dynamic = ["version"]` an *eine* Quelle gebunden, ein Test sichert das.
**Nicht** erledigt, weil es Angaben braucht, die ich nicht erfinden darf: siehe
O-8.

---

## 6. Der eine rote Test — fremdes Gebiet, nicht repariert

```
FAILED tests/test_integration.py::test_an_event_copy_carries_its_reports_and_runs_them_in_the_copy
AssertionError: assert 'sizes-2' == 'sizes'
tests/test_integration.py:516
```

**Fundstelle:** `tests/test_integration.py`, Zeilen 485-533, Eigentümer
`test-engineer`.

**Ursache:** Der Test kopiert die Reports zweimal. Zeile 509
(`copy_event.copy_data_from(world.event)`) löst seit dieser Welle den Empfänger
`signals.copy_reports` aus; Zeile 511 ruft `copy_reports_to_event(...)` danach
noch einmal direkt auf. Der zweite Lauf findet `sizes` belegt und vergibt
`sizes-2`.

**Nachgewiesen:** mit zur Laufzeit abgehängtem Empfänger (Wegwerf-pytest-Plugin,
kein Produktivcode angefasst) läuft derselbe Test grün. Der Test war also
korrekt, solange das Signal nicht verdrahtet war — sein eigener Docstring sagt
das („``event_copy_data`` equivalent"), und `portability-dev` hat genau diesen
Umbau in seinem Signal-Request Abschnitt 5 angekündigt.

**Warum ich ihn nicht repariere:** fremdes Gebiet, und die Entscheidung, *was*
der Test künftig prüfen soll (nur noch das Signal? Signal **und** direkte
Funktion getrennt?), gehört `test-engineer`. Die naheliegende Fassung wäre,
Zeile 511 zu streichen und stattdessen `copy_event.custom_reports.get(...)` zu
lesen — dann prüft der Test die Verdrahtung mit.

Auch in `handoff/blockers.md` eingetragen.

---

## 7. Offene Punkte für dich

| Nr. | Wer | Was |
| --- | --- | --- |
| **O-1** | test-engineer | Der rote Test aus Abschnitt 6. **Das einzige, was zwischen dir und einer grünen Suite steht.** |
| **O-2** | exporter-dev | `tests/test_exporters.py::registered` (Zeilen 71-96) trennt im Teardown die **Produktiv**verbindung der beiden Exporter-`dispatch_uid`s. Ab da läuft die restliche Session ohne registrierten Exporter. Fällt heute nicht auf, weil jeder betroffene Test die Fixture selbst benutzt. Meine neuen Tests umgehen das über einen Snapshot zur Collection-Zeit — das ist ein Pflaster, kein Fix. |
| **O-3** | bootstrap-dev / frontend-dev | `views/placeholder.py` und `templates/…/placeholder.html` sind seit dieser Welle toter Code. Beide Docstrings sagen selbst, dass sie dann gelöscht werden können. Ich habe sie nicht angefasst (fremdes Dateigebiet). |
| **O-4** | du + security-reviewer | Das Plugin-Gate steht dreimal wörtlich im Repo (`views/api.py`, `views/portability.py`, `views/crud.py`), plus die Organizer-Variante und `_plugin_is_active` im Exporter. `security-reviewer` schlägt `views/_mixins.py` beim `integrator` vor. Das ist ein **Ownership-Wechsel** (vier fremde Dateien importieren dann aus meiner) — deine Entscheidung, nicht meine. |
| **O-5** | du | `PLUGIN_LEVEL_EVENT_ORGANIZER_HYBRID` in `PretixPluginMeta`? Das würde die zwei erwarteten `DeprecationWarning`s beseitigen und die Organizer-Sicht sauber machen, ist aber eine Entscheidung über den Charakter des Plugins mit Folgen für Aktivierung, Navigation, Exporter-Sichtbarkeit und die Organizer-Vorlagen. `exporter-dev` hat sie ausdrücklich nicht getroffen, ich auch nicht. Gehört in eine ADR. |
| **O-6** | registry-dev **oder** frontend-dev | **Übersetzungskollision.** `"Date"` ist gleichzeitig das Label des Datentyps `DATE` (`views/api.py:266`) und das Label der Termin-Gruppe und des Feldes `subevent.name` (`registry/groups.py:70`, `registry/core.py:439`). Deutsch braucht „Datum" für das eine und „Termin" für das andere; ein Katalog kann pro `msgid` nur eine Übersetzung haben. Aktuell steht überall „Datum". Fix: eine der beiden Stellen auf `pgettext_lazy("subevent", "Date")` umstellen — genau das tut pretix selbst für `Date {val}`. Zwei Zeilen, aber in fremdem Gebiet. |
| **O-7** | du | **ADR-Status.** `docs/adr/0001-contracts.md` steht auf „vorgeschlagen (wird mit `handoff/contracts-freigegeben.md` akzeptiert)" — die Freigabedatei existiert seit dem 2026-07-30, der Status ist also veraltet. `0002-registry.md` steht auf „vorgeschlagen" ohne Freigabevermerk. `0003` und `0004` wurden für `query-dev`/`persistence-dev` reserviert und nie geschrieben; deren Entscheidungen (Filterlogik, Datenmodell) stehen damit nur in Code und Statusberichten. `ORCHESTRIERUNG.md` §5 verbietet mir, fremde ADRs zu ändern — deshalb gemeldet statt korrigiert. **Inhaltliche Widersprüche zwischen den ADRs habe ich keine gefunden.** |
| **O-8** | du | **Release-Metadaten fehlen und ich darf sie nicht erfinden:** `pyproject.toml` hat keine `[project.urls]` (das Repo hat kein Git-Remote) und keine Kontakt-E-Mail bei `authors`/`maintainers` — der Kommentar dort sagt seit Welle 0a „to be completed by the integrator before release". Außerdem: `Development Status :: 2 - Pre-Alpha` in den Classifiern — für ein funktional vollständiges 0.1.0 wäre `3 - Alpha` ehrlicher, aber das ist eine Release-Entscheidung. |
| **O-9** | du | **PostgreSQL-Gegenprobe** — unverändert offen, Umgebungsentscheidung. `test-engineer`s Vorschlag: Container starten, `pretix.cfg` auf `backend=postgresql`, dann `tests/test_registry.py`, `tests/test_query_compile.py`, `tests/test_query_registry.py`, `tests/test_integration.py`. Vier Risikostellen in `handoff/blockers.md`. Steht als bekannte Einschränkung im README. |
| **O-10** | die jeweiligen Eigentümer | **S-003 bis S-006** (Security-Review) und **T-001 bis T-003** (test-engineer) sind unverändert offen. Auftragsgemäß **nicht** von mir gefixt — T-001 braucht laut `test-engineer` eine Architekturentscheidung, nicht nur einen Patch. Alle sieben stehen jetzt als „Known limitations" im README, damit sie nicht unbemerkt in ein Release rutschen. |
| **O-11** | du | Screenshots. `docs/img/` ist angelegt, die drei `figure`-Direktiven im README zeigen auf `screenshot-editor.png`, `screenshot-report-list.png`, `screenshot-scheduled-export.png`. Solange die Dateien fehlen, rendert GitHub ein kaputtes Bild. |

---

## 8. Was ich bewusst *nicht* getan habe

* **Kein `git commit`** — laut Auftrag.
* **Keine fremde Logik repariert.** Der rote Test, die Exporter-Fixture, die
  Platzhalter-Dateien, die `"Date"`-Kollision, S-003…S-006, T-001…T-003: alle
  gemeldet, keiner angefasst.
* **Kein Feature nachgebaut.** Was kein Agent geliefert hat, steht als
  Einschränkung im README, nicht als neuer Code im Repo.
* **Keine fremde ADR geändert** (`ORCHESTRIERUNG.md` §5) — stattdessen eine
  eigene geschrieben und die Statusfrage eskaliert.
* **Keine Contracts angefasst.**
* **Kein `pip install` von Werkzeugen** in das gemeinsame venv. Die
  Katalogerzeugung läuft mit dem, was schon da war (`babel`, `polib`).

---

## 9. Geänderte und neue Dateien

**Geändert:** `pretix_custom_reports/urls.py`, `pretix_custom_reports/signals.py`,
`pretix_custom_reports/templates/pretix_custom_reports/report_list.html`
(fremd, nur Links), `tests/test_smoke.py`, `README.rst`, `CLAUDE.md`,
`MANIFEST.in`, `Makefile`, `.github/workflows/tests.yml`,
`handoff/blockers.md`.

**Neu:** `pretix_custom_reports/locale/de/LC_MESSAGES/django.po`,
`docs/extending.md`, `docs/adr/0006-verdrahtung.md`, `CHANGELOG.rst`,
`docs/img/.gitkeep`, `handoff/status/integrator.md`.

**Verschoben:** neun Dateien aus `handoff/requests/` nach
`handoff/requests/erledigt/`, jede mit einer Kopfzeile „ERLEDIGT" bzw.
„ARCHIVIERT" versehen. Die drei nicht an mich adressierten wurden vor dem
Archivieren gegen den Code geprüft (Fundstelle steht jeweils in der Kopfzeile).

**Nicht im Git (gitignored, aber lokal vorhanden):**
`pretix_custom_reports/locale/de/LC_MESSAGES/django.mo`.
