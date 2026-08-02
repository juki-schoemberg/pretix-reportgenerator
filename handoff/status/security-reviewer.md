# Status: security-reviewer — Welle 3

`tests/test_security.py` (2.3 kLOC, 87 Testfunktionen → **131 Testfälle: 123
grün, 8 `xfail(strict=True)`**), `docs/security-review.md`,
`handoff/blockers.md` (neu angelegt). Kein Produktivcode angefasst, kein Commit,
keine fremde Testdatei verändert.

## Ergebnis in einem Satz

**Kein kritischer Befund.** Die drei Eigenschaften, an denen das ganze Design
hängt — kein ORM-Vokabular aus untrusted Input, keine Daten über die
Event-Grenze, kein Endpunkt ohne Rechteprüfung — halten allen Angriffen stand,
die ich bauen konnte. Was ich gefunden habe, sind sechs Befunde zwischen
*mittel* und *niedrig*, davon zwei Varianten desselben Themas („Plugin aus
stoppt nichts") und einer, den ich für den unangenehmsten halte, weil er über
eine Importdatei einschleusbar ist.

| Nr. | Schwere | Zuständig | Kurz |
| --- | --- | --- | --- |
| S-001 | mittel | persistence-dev | `views/crud.py` hat als einziges View-Modul kein `PluginActiveMixin` |
| S-002 | mittel | exporter-dev | Organizer-Multi-Event-Export exportiert Events, für die das Plugin aus ist |
| S-003 | mittel | portability-dev, frontend-dev | ungepaarte Surrogate: 500 auf `api/validate/`, `api/preview/` und dem JSON-Export, persistierbar über den Import |
| S-004 | niedrig | persistence-dev | doppelter Identifier → `IntegrityError`/500 statt Formularfehler |
| S-005 | niedrig | query-dev | eine zusätzliche Query pro `join`-Spalte, bis zu ~200 pro Vorschau |
| S-006 | niedrig | portability-dev | `strategy=keep` ist per POST wählbar und überspringt `check_definition` |

Volltext mit Reproduktion und Empfehlung: `docs/security-review.md`.
Verweis für den Orchestrator: `handoff/blockers.md` (Eintrag „kein Blocker",
mit der Tabelle und der Empfehlung, S-001/S-002 **vor** dem Verdrahten von
`urls.py`/`signals.py` zu beheben — danach sind sie erreichbar).

## Methode: warum `xfail(strict=True)` und nicht ein roter Test

Die Rollenbeschreibung verlangt zu jedem Befund einen *fehlschlagenden* Test.
Ein dauerhaft roter Test hat aber eine schlechte Eigenschaft: nach zwei Wochen
weiß niemand mehr, welche der roten Tests Befunde und welche kaputt sind, und
die Suite verliert ihre Aussage. Deshalb trägt jeder Befundtest
`@pytest.mark.xfail(strict=True)` mit der Befundnummer im `reason`:

* solange der Fehler existiert, ist die Suite grün und der Befund dokumentiert,
* sobald jemand ihn behebt, wird der Test `XPASS` und die Suite **rot** — die
  Behebung erzwingt also aktiv, den Marker zu entfernen und den Befund
  abzuschließen. Ein stiller „ist wohl behoben" ist damit unmöglich.
* `pytest tests/test_security.py --runxfail` zeigt die acht echten
  Fehlermeldungen.

Ich habe **jede** der acht gegengelesen, damit kein Test aus dem falschen Grund
fehlschlägt. Beispiele aus dem `--runxfail`-Lauf:

```
S-001  assert 200 == 404   GET /control/event/dummy/plain/customreports/reports/
S-002  DID NOT RAISE ExportError  (+ der Warn-Log, der zeigt, dass nur das
       Plugin-lose Event geliefert hat)
S-003  UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800'
       django/http/response.py:324  bzw.  views/portability.py:165
S-004  IntegrityError: UNIQUE constraint failed: ...event_id, ...identifier
S-006  assert 1 == 0  (ein Report wurde geschrieben)
```

Zu **jedem** `xfail` gibt es eine grüne **Kontrollgruppe** auf dem
Nachbarpfad. Ohne die wäre ein `xfail` nicht von einem schlicht falsch
geschriebenen Test zu unterscheiden:

| Befund | Kontrollgruppe (grün) |
| --- | --- |
| S-001 | `test_the_endpoints_that_do_have_the_plugin_gate_really_404` — api, editor, import, templates geben 404 |
| S-002 | `test_the_event_level_exporter_is_invisible_when_the_plugin_is_off` — die Event-Ebene ist dicht |
| S-003 | `test_the_csv_path_survives_a_stored_lone_surrogate` — der CSV-Weg ist **nicht** betroffen (`errors="replace"` im Kern) |
| S-006 | `test_the_same_definition_is_refused_under_the_offered_strategies` — `abort` und `skip` lehnen dieselbe Datei ab |

## Die drei Befunde, die eine Begründung brauchen

### S-003 ist der einzige, der von außen kommt

Die anderen fünf braucht jemand mit Schreibrecht oder eine
Konfigurationsänderung. S-003 nicht: `"\ud800"` ist **syntaktisch gültiges
JSON**, `json.loads` liefert dafür einen Python-String, der sich nicht nach
UTF-8 encodieren lässt, und das Payload-Gate prüft Größe, Tiefe, Knotenzahl,
Stringlänge, Zahlenlänge, `NaN` und doppelte Member — aber nicht die
Encodierbarkeit.

Ein Administrator, der eine Reportdatei von außen einspielt (genau der Vorgang,
für den `payload.py` als Gate gebaut wurde), speichert damit eine Zeile, nach der
`POST api/validate/`, `POST api/preview/` und der JSON-Export dieses Reports
dauerhaft 500 antworten. Der Report bleibt im Editor **öffenbar** (dort greift
`escapejson_dumps` mit `ensure_ascii=True`) und als CSV exportierbar (der Kern
encodiert mit `errors="replace"`), ist also reparierbar — deshalb *mittel* und
nicht *hoch*. Beide Grenzen sind eigene grüne Tests, damit der Befund nicht
überzeichnet werden kann.

Die Behebung liegt bewusst **nicht** in den Contracts: drei Zeilen in
`payload._walk` plus dreimal `ensure_ascii=True` reichen. Kein
Contract-Änderungswunsch, kein Blocker.

### S-001 und S-002 sind ein Thema, nicht zwei

`views/api.py`, `views/editor.py`, `views/portability.py` und
`views/templates.py` haben je ein Plugin-Gate; `views/crud.py` und
`exporters.py` haben keins. Zusammengenommen heißt das: „Plugin für dieses Event
abschalten" stoppt weder das Anlegen noch das Ausführen von Reports. Für ein
Plugin, das Bestelldaten exportiert, ist die Deaktivierung die Notbremse, und
SPEC.md F1 verlangt sie ausdrücklich — `frontend-dev` hat die Ergänzung in
Welle 1 sogar empfohlen (Statusbericht, Entscheidung 4), sie ist bei
`persistence-dev` nicht angekommen.

S-002 ist der ernstere Teil davon, weil `register_multievent_data_exporters` ein
`OrganizerPluginSignal(allow_legacy_plugins=True)` ist und `self.events` nur
nach *Berechtigung* gefiltert kommt, nicht nach Plugin-Status. Gemessen: ein
Event `plain` ohne das Plugin, mit einem übrig gebliebenen Report und einer
Bestellung `OFFEV` — der Organizer-Export liefert `OFFEV` in der CSV aus, und
derselbe Weg läuft unter `ScheduledOrganizerExport` als wiederkehrende Mail.

### S-006 ist klein, aber es entwertet eine dokumentierte Zusicherung

`portability/resolution.py` beschreibt zwei Nachprüfungen vor dem Speichern, die
zweite ist `query.plan.check_definition` — „wäre der Importeur großzügiger als
der Compiler, speicherten wir Reports, die beim ersten terminierten Lauf
scheitern". Diese Prüfung wird unter der Strategie `keep` übersprungen, und
`keep` steht in `ResolutionStrategy.ALL`, das beide Views direkt mit
`request.POST.get("strategy")` füttern. Die Oberfläche bietet es nicht an, ein
`curl` schon.

Gemessen an der Wirkung ist das *niedrig* — keine Rechteausweitung, kein
Datenabfluss, und dieselbe Zeile ließe sich über das CRUD-Formular anlegen. Ich
melde es trotzdem, weil eine Sicherheitszusicherung, die ein Formularfeld
abschaltet, keine ist.

## Was gehalten hat (und wogegen)

Der ausführliche Abschnitt steht in `docs/security-review.md` („Geprüft und in
Ordnung"). Die sechs Angriffe, die ich am ehesten für erfolgreich gehalten
hätte und die es nicht waren:

1. **Eine handgebaute `contracts.ReportDefinition`**, die den JSON-Validator
   komplett umgeht, mit `field="order__event__organizer__name"`. Scheitert an
   der Registry-Allowlist, und — das ist der Teil, den ich messen wollte —
   dabei wird **keine Zeile** aus `Order`/`OrderPosition` gelesen
   (`CaptureQueriesContext`).
2. **Die Registry-Naht**, die `query-dev` als interessantesten Angriffspunkt
   benannt hat. Sie ist in Wirklichkeit die stärkste Stelle im Plugin: die
   Annotation-Closures in `registry/annotations.py` prüfen `ctx.event.pk` und
   werfen `FieldContractError`, wenn ein für Event A gebautes Feld mit einem
   Kontext für Event B benutzt wird. Direkt angegriffen, hält.
3. **`meta.references` als Datenkanal.** Ein Dokument mit Hinweisen auf Keys,
   die die Definition nicht benutzt, mit einem Label, das eine echte Frage des
   Zielevents trifft, und mit einem `{"key": 42}`-Eintrag. Ergebnis: jeder Key,
   den der Resolver ausgibt, steht in `registry.get_fields()` des Zielevents.
   Das ist der zentrale Test des Importpfades
   (`test_everything_the_resolver_outputs_comes_from_the_target_registry`) und
   genau die Behauptung, die `portability-dev` mir mitgegeben hat.
4. **Identifier-Hijacking über eine Importdatei.** Eine Datei, die
   `meta.identifier` eines bestehenden Reports trägt, würde bei naiver
   Implementierung den terminierten Export dieses Reports umleiten.
   `ensure_unique_identifier()` hängt ein Suffix an, der Originalreport bleibt
   unangetastet.
5. **Der lügende Upload.** `views/portability.py::_document_text` prüft
   `upload.size`, was aus dem Browser kommt. Ein `UploadedFile`, dessen `size`
   10 meldet und das 513 KiB liefert, wird trotzdem abgewiesen — die
   Leseobergrenze `read(MAX_PAYLOAD_BYTES + 1)` ist das echte Gate. Genau der
   Test, den `portability-dev` sich gewünscht hat.
6. **Die Event-Kopie über Organizergrenzen.** Der Fund, den `portability-dev`
   selbst gemeldet und behoben hat (`scopes_disabled()` + hartes `event=`).
   Gegengeprüft mit zwei Organizern und zwei Reports: es kommt genau der Report
   des Quellevents an.

Zwei Prüfungen, die ich als **strukturelle Wächter** angelegt habe, weil sie
Regressionen fangen, die kein Funktionstest fängt:

* `test_no_module_evaluates_code_or_builds_raw_sql` und
  `test_every_dynamic_lookup_keyword_comes_from_a_named_variable` laufen über
  den **Syntaxbaum des gesamten Pakets** (nicht nur `query/`, wie der
  vorhandene Test von `query-dev`).
* Drei Tests, die vorher als Textscan gescheitert wären und deshalb jetzt über
  den AST laufen: `csrf_exempt`, `scope()`/`scopes_disabled()` in
  `exporters.py`, `scopes_disabled()` im ganzen Paket. Alle drei Module
  *besprechen* diese Namen in ihren Docstrings — ein Textscan hätte den
  Docstring für den Verstoß gehalten. Das ist ein Muster, das ich für künftige
  Wächter empfehle.

## Zwei Dinge, die ich absichtlich **nicht** als Befund führe

1. **`resolution._normalise()` faltet über den vollen Unicode-Raum**
   (`str.lower()`/`str.isalnum()`). `answer.İD` faltet zu `id`. Das führt nie
   aus dem Zielevent heraus — das Ergebnis kommt aus dessen Registry — und der
   Auflösungsbericht zeigt es als `mapped`. Kein Versprechen verletzt, also
   „Unbestätigt" (U-02), kein Befund.
2. **Der doppelte Storno-Filter** von `query-dev` und die Tatsache, dass
   `resolve_definition` unter `abort` ein `document` **zurückgibt**, obwohl der
   Plan nicht `ok` ist. Beides sah bei der ersten Lesung nach einem Fehler aus,
   beides ist bewusst und dokumentiert. Ich habe stattdessen einen Test
   geschrieben, der die *Absicht* festnagelt
   (`test_a_core_key_is_never_matched_by_similarity` prüft
   `not outcome.ok and outcome.report.blocking`, nicht `document is None`) —
   das war der einzige Test von mir, der beim ersten Lauf aus einem Missver-
   ständnis heraus rot war.

## Aufbau von `tests/test_security.py`

Nach Angriffsfläche, nicht nach Modul:

| Abschnitt | Inhalt |
| --- | --- |
| 1 Registry-Umgehung | AST-Wächter, geschmuggeltes ORM-Vokabular, Lookup-Tabelle, handgebaute Definition, `separator` |
| 2 Event-/Mandantentrennung | fremdes Event, fremder Organizer, Vorlagen, Vorschau, Exporter-Identifier, Registry, Annotation-Closures |
| 3 Berechtigungen | 13 Event-Endpunkte × 3 feindliche Akteure, 5 Organizer-Endpunkte, Nur-Lese-Matrix, CSRF, Methoden |
| 4 Import | alle 17 `invalid/`-Fixtures, `schema_version`, Payload-Gate, Unicode-Tricks, lügender Upload, Bestätigungsschritt, Strategie |
| 5 Ausgabe | CSV-Injection in Zelle **und** Kopfzeile, Dateinamen, Vorschau-Escaping, Ausbruch aus dem JSON-Konfigblock |
| 6 Hintergrund | entzogene Rechte, django-scopes, `export_form_data` als untrusted, Event-Kopie |
| 7 Ressourcen | SQL-`LIMIT`, Limit-Klemmung, `join`-Amplifikation, 100 Filter = 1 Query |
| 8 zweite Runde | gefälschte Formularfelder, Methoden-Matrix, Identifier-Hijacking |
| 9 Resolver-Zusicherung | „alles kommt aus der Zielregistry" |
| 10 Messungen | Nullbyte, Feldbibliothek-Querykosten, JS-Rendering |
| 11 dritte Runde | `keep`-Strategie, Plugin-Gate auf Exporter-Ebene, Slug im Dateinamen |

Infrastruktur: eine modulweite Fixture hängt **alle** Routen der Wellen 1 und 2
in den echten Resolver (`urls.py` gehört dem `integrator`), inklusive der
Organizer-Vorlagenrouten, die `tests/test_editor_api.py` bewusst auslässt. Drei
feindliche Akteure als Fixtures: anonym, `user_without_perms` aus der
`conftest.py`, und ein **Voll-Admin eines zweiten Organizers**
(`rival_user`) — der Fall, den keine der bestehenden Testdateien hatte.

## Nicht erledigt (und warum)

* **PostgreSQL.** Die Testumgebung ist SQLite (`pretix.testutils.settings`), wie
  bei `registry-dev`, `query-dev` und `exporter-dev`. Betrifft konkret U-01
  (Nullbyte in `jsonb`) und die Ausgabetypen der Aggregat-Subqueries. Gehört zum
  PostgreSQL-Durchgang, den `test-engineer` ohnehin auf dem Plan hat.
* **Lasttests.** Kein Test mit sechsstelliger Zeilenzahl, kein 20-MB-Export,
  keine Zeitmessung des Payload-Gates unter Vollast (die Anregung von
  `portability-dev`). S-005 ist die Amplifikation, die ich *gemessen* habe;
  alles andere kostet nur Suite-Laufzeit. `test-engineer` hat den
  `performance`-Marker.
* **Kein Fuzzing.** Die Import-Angriffe sind handgeschrieben und zielgerichtet.
  Ein `hypothesis`-Lauf über `validate_definition` wäre die logische Ergänzung
  und hätte S-003 vermutlich auch gefunden; das ist eine Werkzeugentscheidung
  für das Repo und nicht meine.
* **Keine Prüfung der `de`-Übersetzungen** auf Formatstring-Injektionen — der
  Katalog existiert noch nicht (`integrator`, Welle 4).
* **Kein Review von `scripts/`** (`seed_demo.py`, `verify/*`). Sie laufen nicht
  im Request-Pfad; `flake8` schließt sie ohnehin aus.

## Anmerkung zum Zustand des Arbeitsbaums

Während meines Laufs sind `tests/factories.py`, `tests/test_integration.py` und
`tests/test_performance.py` neu erschienen und `tests/conftest.py` wurde
geändert — `test-engineer` arbeitet parallel in derselben Welle. Meine Zahlen
unten sind gegen den Stand am Ende meines Laufs gemessen; die Gesamtsumme wandert
also noch. Zwischenzeitlich lag eine `tests/test_scratch.py` mit einem
`assert False` im Baum, sie ist inzwischen weg.

## Tests

```
pytest tests/test_security.py -q          -> 123 passed, 8 xfailed  (~8 s)
pytest tests/test_security.py --runxfail  -> 8 failed  (die acht Befunde)
pytest tests/ -q                          -> 999 passed, 10 xfailed, 1 failed
```

Der eine Fehlschlag der Gesamtsuite ist fremd:
`tests/test_smoke.py::test_no_migration_created_yet` — das Welle-0-Gate, das
`persistence-dev` planmäßig gebrochen hat; es steht seit Welle 1 in drei
Statusberichten und der Ersatz liegt in dessen Handoff.

Mein Modul läuft isoliert **und** in der Gesamtsuite grün, dreimal in
wechselnder Reihenfolge (`pytest-randomly` ist aktiv); die Routen-Fixture nimmt
ihre Änderung an der URLconf im Teardown zurück, damit sie kein anderes Modul
beeinflusst.

Lint über die eigene Datei, alle drei sauber:

```
flake8 tests/test_security.py        -> rc 0
isort -c tests/test_security.py      -> rc 0
black --check tests/test_security.py -> 1 file unchanged
```

Kein `black .` / `isort .` über das Repo, kein `git commit`, keine Datei
außerhalb von `tests/test_security.py`, `docs/security-review.md`,
`handoff/blockers.md` und dieser Datei angefasst.

## Nächster Schritt

1. **`persistence-dev`:** S-001 (eine Zeile: `PluginActiveMixin` in
   `EventReportMixin`) und S-004 (`clean_identifier` gegen
   `for_event(...).by_identifier(...)`). Beide `xfail`-Marker danach entfernen,
   sonst wird die Suite rot — so ist es gedacht.
2. **`exporter-dev`:** S-002. Der Skip-Pfad in `_prepare()` existiert bereits;
   es fehlt ein `_EventProblem`, wenn `self.plugin_module not in
   event.get_plugins()`. Bitte **nicht** über `plugins__contains` filtern
   (Teilstringtreffer auf einem längeren Plugin-Namen).
3. **`portability-dev` + `frontend-dev`:** S-003. Drei Zeilen in
   `payload._walk` (`value.encode("utf-8")` versuchen) plus dreimal
   `ensure_ascii=True` in `views/api.py:353`, `views/portability.py:163`,
   `views/templates.py:282`. Dabei U-01 (Nullbyte) gleich mit erschlagen.
4. **`portability-dev`:** S-006, eine engere Coerce-Funktion für die Views.
5. **`query-dev`:** S-005, entweder eine Obergrenze für `join`-Spalten oder
   Prefetch-Dedup über `(lookup, Bedingung)` statt über `to_attr`.
6. **`test-engineer`:** die Suite gegen PostgreSQL fahren; U-01 und die
   Aggregat-Ausgabetypen sind dort die interessanten Stellen. Und bitte
   `tests/test_scratch.py` aufräumen.
7. **`integrator`:** S-001 und S-002 vor dem Verdrahten von `urls.py` und
   `signals.py` einplanen — solange die Routen und die beiden
   `connect()`-Zeilen fehlen, sind beide produktiv nicht erreichbar, danach
   sofort. Außerdem U-06: die Browser-Tests skippen auf einem CI ohne Browser
   **still**.


---

# Nachtrag 2026-08-02 — Verifikation der Fixes zu S-001 und S-002

Kein neuer Review-Durchgang, sondern ein gezielter Verifikationslauf auf
Anforderung des Orchestrators: `persistence-dev` und `exporter-dev` waren vor
dem Verdrahten von `urls.py`/`signals.py` reaktiviert worden, um die beiden
Plugin-Gate-Befunde zu schließen (meine eigene Empfehlung aus U-07).

## Was ich geprüft habe

**S-001 (`views/crud.py`).** Fix gelesen, nicht nur den Diff: neues
`PluginActiveMixin` (Zeile 98), `EventReportMixin` erbt davon, damit hängen alle
fünf Views daran. Bewusst dupliziert statt aus `views/api.py` importiert — die
Begründung (keine Modulabhängigkeit zwischen zwei Agentengebieten) trägt, das
Vorbild `views/portability.py` gab es schon.

Feindlich gegengelesen, drei Punkte:

1. `ReportDuplicateView` ist POST-only. Ein GET dagegen ist 405, ob das Gate
   existiert oder nicht — meine ursprüngliche Fassung des Tests hätte dort also
   **strukturell nicht fehlschlagen können**. `persistence-dev` hat genau das
   selbst gemeldet; der Hinweis war richtig. Der Test greift jetzt jede der fünf
   Ansichten mit der Methode an, die schreibt, und prüft danach die Tabelle:
   nichts angelegt, nichts umbenannt, nichts gelöscht.
2. Routen sind nicht die Klassenhierarchie. Eine sechste View, die morgen in
   `views/crud.py` entsteht und in meinem Testmodul nicht geroutet ist, würde
   den Requesttest nie erreichen. Deshalb zusätzlich
   `test_no_crud_view_is_missing_the_plugin_gate` über `crud.__all__`.
3. Fix zur Laufzeit entfernt (Wegwerf-Pytest-Plugin, Produktivcode unangetastet)
   → der Test fällt wieder mit `200 == 404` auf der Reportliste des
   abgeschalteten Events. Er misst also weiterhin das Leck, nicht sich selbst.

**Gefunden dabei:** die Begründung des Fixes ist falsch, das Ergebnis richtig.
`EventPermissionRequiredMixin` implementiert kein `dispatch`, sondern wickelt in
`as_view()` die fertige View in `event_permission_required(...)`
(`pretix/control/permissions.py:81-91`). Die Rechteprüfung läuft damit **vor**
dem Plugin-Gate, nicht danach; ein Nutzer ohne Schreibrecht bekommt 403, nicht
404. Kein Befund (die Reihenfolge ist die vorsichtigere), aber festgenagelt in
`test_the_permission_check_runs_before_the_plugin_gate_not_after`, damit die
Begründung nicht weitergereicht wird.

**S-002 (`exporters.py`).** `_plugin_is_active` benutzt
`self.plugin_module in event.get_plugins()` — die Prüfung, die pretix selbst vor
der Zustellung eines Event-Plugin-Signals macht, und ausdrücklich **nicht**
`plugins__contains`. Angewandt in `report_choices()` und ganz oben in
`_prepare()`, also vor dem Report-Lookup: ein abgeschaltetes Event bekommt seine
Reports gar nicht erst gelesen. Der `_EventProblem`-Pfad nennt den Grund, und der
Grund überlebt bis in den `ExportError`, den der Eigentümer eines terminierten
Exports per Mail liest.

`exporter-dev` hat gemeldet, dass mein Beweistest nach dem Fix nicht mehr das
Leck misst, sondern an einem `ExportError` stirbt. Nachgeprüft: stimmt, und es
ist der wichtigere der beiden Punkte dieses Laufs. Der alte Aufbau legte
`LEFTOVER` nur im abgeschalteten Event an; nach dem Fix kann kein Event mehr
etwas liefern, `render()` bricht ab, und `assert b"OFFEV" not in data` wird nie
erreicht. Der Test wäre grün geworden, **ohne je etwas nachgewiesen zu haben**.
Umgebaut nach Variante 1: beide Events halten denselben Identifier und je eine
Bestellung. Gegenprobe mit neutralisiertem Gate: die CSV enthält wieder
`"plain","Plain Event","OFFEV"`, der Test fällt an genau dieser Zeile.

Zweite Facette neu abgedeckt: `report_choices()`. Der Reportname eines
abgeschalteten Events ist selbst eine Auskunft, und eine Auswahl anzubieten, die
`_prepare()` gleich darauf verweigert, ist ein Fehlerpfad ohne Nutzen —
`test_the_organizer_export_form_never_offers_a_switched_off_events_report`.

## Regel, die ich daraus mitnehme

Ein `xfail`-Marker fällt erst, wenn drei Dinge gelten: der Test ist ohne
`--runxfail` grün, er misst noch dasselbe wie vorher, **und** mit künstlich
entferntem Fix fällt er wieder an derselben Stelle. Punkt zwei ist der, an dem
S-002 fast durchgerutscht wäre. Steht jetzt so in `docs/security-review.md`,
Abschnitt „Wie die Tests gebaut sind".

## Zahlen

* `pytest tests/test_security.py -q` → **128 passed, 6 xfailed** (vorher 123/8;
  zwei Marker entfernt, drei Tests dazu).
* `pytest tests -q -m "not performance"` → **1004 passed, 1 failed, 8 xfailed**.
  Der Fehlschlag ist `test_smoke.py::test_no_migration_created_yet`, das
  vorbestehende Welle-0-Gate — nicht meins und nicht durch diesen Lauf
  verursacht.
* `flake8`, `black --check`, `isort -c` über `tests/test_security.py`: grün.
* Geänderte Dateien: `tests/test_security.py`, `docs/security-review.md`,
  `handoff/blockers.md` (angehängt), diese Datei. Kein Produktivcode, kein
  Commit.

## Offen

S-003 (mittel), S-004, S-005, S-006 (niedrig) unverändert. Die sechs
verbleibenden `xfail(strict=True)` verteilen sich auf S-003 (vier), S-004 (einer)
und S-006 (einer). Nacharbeit an den `integrator`: das Plugin-Gate steht jetzt
dreimal wörtlich im Repo — Kandidat für ein gemeinsames `views/_mixins.py`.

---

# Nachtrag (Welle 5): `registered_exporter`-Fixture repariert

Reine Testinfrastruktur, kein neuer Befund. `docs/security-review.md` und
`handoff/blockers.md` bleiben unverandert.

## Was falsch war

`tests/test_security.py::registered_exporter` war ein nackter
connect/disconnect-Pfad. Seit `signals.py` die beiden Exporter-Receiver beim
Plugin-Import dauerhaft verbindet, ist so ein Paar in beiden Varianten falsch:

* Mit **produktiven** `dispatch_uid`s ist `connect()` ein stiller No-op
  (`django/dispatch/dispatcher.py:113-117`), das Teardown-`disconnect()` matcht
  aber allein uber `(dispatch_uid, sender_id)` und entfernt damit die
  produktive Registrierung -- sessionweit, fur jede Datei, die danach lauft
  (`dispatcher.py:138-153`). Das war der Fall in `tests/test_exporters.py`, den
  `exporter-dev` behoben hat.
* Meine Fixture hatte stattdessen **eigene** uids
  (`pretix_custom_reports_security_exporter` / `..._multiexporter`). Damit
  unterscheidet sich der Lookup-Key, `connect()` ist kein No-op, und dieselbe
  Funktion hangt waehrend jedes Tests **zweimal** am Signal.
  `init_event_exporters()` dedupliziert nicht
  (`pretix/base/services/export.py:198-225`), also lief jeder Test mit
  `registered_exporter` gegen eine Exporter-Liste, in der `customreports`
  doppelt steht. Kein Sicherheitsbefund -- aber die Tests haben nicht das
  geprueft, was in Produktion passiert, und ein Duplikat hatte eine echte
  Doppelregistrierung nie auffallen lassen.

## Was jetzt drinsteht

Muster von `exporter-dev` uebernommen, Helfer bewusst dupliziert statt aus
`tests/test_exporters.py` importiert (zwei Testmodule zweier Agents importieren
nicht voneinander):

* `connected_receiver(signal, dispatch_uid)` -- dereferenziert die
  `weakref.ref` aus `Signal.receivers`.
* `named_receivers(signal)` -- alle Receiver mit String-uid, fuer den Kanarien-
  vogel.
* `times_connected(signal, function)` -- zaehlt Registrierungen einer Funktion.
* `EXPORTER_UID` / `EXPORTER_MULTI_UID` zeigen jetzt auf die **produktiven**
  uids aus `signals.py`; `EXPORTER_WIRING` haelt die zwei Tripel.
* `registered_exporter` verbindet nur, was fehlt, prueft bei belegter uid per
  `assert`, dass wirklich unsere Funktion dranhaengt, trennt im Teardown nur
  Selbstverbundenes -- und stellt vor dem `yield` sicher, dass jeder Receiver
  **genau einmal** haengt.
* Neu am Dateiende:
  `test_this_module_hands_the_exporter_wiring_back_untouched` (Kanarienvogel,
  modulweiter Vorher/Nachher-Vergleich uber die neue modulweite Autouse-Fixture
  `exporter_wiring_before_this_module`). Er deckt die *bleibende* Haelfte ab;
  die transiente Doppelregistrierung faengt der `assert` in der Fixture.

## Zahlen

* `pytest tests/test_security.py -q` -> **129 passed, 6 xfailed** (vorher 128/6;
  +1 = der Kanarienvogel, keine Regression, dieselben sechs `xfail`).
* `pytest tests/test_security.py tests/test_exporters.py -q` -> 171 passed,
  6 xfailed. Umgekehrte Reihenfolge identisch.
  `pytest tests/test_integration.py tests/test_security.py -q` -> 164 passed,
  8 xfailed.
* `pytest -m "not performance" -q` -> **1019 passed, 8 deselected, 8 xfailed**,
  0 failed. (Das fruehere `test_no_migration_created_yet` faellt nicht mehr.)
* `flake8`, `black --check`, `isort -c` ueber `tests/test_security.py`: gruen.
* Geaendert: nur `tests/test_security.py` und diese Datei. Kein Produktivcode,
  kein Commit.

## Weitergegeben

`tests/test_integration.py::registered` hatte dieselbe Doppelregistrierung
(eigene uids `pretix_custom_reports_integration_*`); `integration-tester` hat
sie waehrend meines Laufs parallel auf dasselbe Muster umgestellt -- erledigt,
nur zur Kenntnis. Offen bleiben zwei veraltete Querverweise in fremdem Gebiet:
`tests/test_exporters.py:1488` nennt `tests/test_security.py` noch als Datei
mit dem alten connect/disconnect-Paar, und der Snapshot-Workaround in
`tests/test_smoke.py:38-53` begruendet sich mit genau diesem Defekt. Beides
stimmt ab jetzt nicht mehr; Nachziehen liegt bei `exporter-dev` bzw. dem
Eigentuemer von `tests/test_smoke.py`.
