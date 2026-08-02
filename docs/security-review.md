# Security-Review — Welle 3

Adversarialer Review des gesamten Plugins nach Stand Welle 2 (`ce765d5`).
Prüfgegenstand: `pretix_custom_reports/**`, alle Views, der Exporter, das
Portability-Paket, die Registry-Naht und der Query-Compiler.

Alle Befunde sind mit einem Test in `tests/test_security.py` belegt
(128 grün, 6 `xfail(strict=True)`). Ein Befund ohne Test steht ausschließlich im
Abschnitt „Unbestätigt" und ist dort als Vermutung gekennzeichnet.

> **Nachtrag vom 2026-08-02 — S-001 und S-002 sind behoben und verifiziert.**
> Der Orchestrator hat `persistence-dev` und `exporter-dev` vor dem Verdrahten
> von `urls.py`/`signals.py` reaktiviert (Empfehlung U-07). Beide Fixes wurden
> von mir gegengeprüft: die zwei Beweistests tragen kein `xfail` mehr, sind auf
> die Lücken erweitert, die die ursprüngliche Fassung nicht messen konnte, und
> schlagen bei künstlich entferntem Fix weiterhin genau am ursprünglichen Leck
> fehl. Details je Befund unten unter **Status**. Offen bleiben S-003 bis S-006.

**Kein kritischer Befund.** Es gibt in diesem Stand keinen Pfad, auf dem

* ein ORM-Pfad, ein Lookup oder ein Operator aus einer Definition, einer
  Importdatei oder einem Editor-Request in ein Queryset gelangt,
* ein Report Daten eines fremden Events oder eines fremden Organizers liefert,
* ein Endpunkt ohne Rechteprüfung antwortet.

Was gefunden wurde, sind sechs Befunde zwischen *mittel* und *niedrig*: zweimal
ein fehlendes Plugin-Gate, einmal ein nicht encodierbarer Unicode-Wert, der drei
Endpunkte auf 500 legt und persistierbar ist, einmal ein IntegrityError statt
eines Formularfehlers, einmal eine Query-Amplifikation und einmal eine
Validierungsstufe, die sich per POST abschalten lässt.

---

## Wie die Tests gebaut sind

`tests/test_security.py` enthält **nur Angriffe**. Ein Test, der ein
*bestehendes Problem* beweist, trägt `@pytest.mark.xfail(strict=True)` mit der
Nummer des Befunds im `reason`. Damit gilt:

* solange der Fehler existiert, ist die Suite grün und der Befund dokumentiert,
* sobald jemand ihn behebt, wird der Test `XPASS` und die Suite **rot** — die
  Behebung erzwingt also, den Marker zu entfernen und damit den Befund
  abzuschließen,
* `pytest --runxfail tests/test_security.py` zeigt die echten Fehlermeldungen
  (bei Abgabe acht, nach dem Schließen von S-001 und S-002 noch sechs); jede
  wurde gegengelesen, damit kein Test aus dem falschen Grund fehlschlägt.

Zu jedem Befund, bei dem ein Nachbarpfad *nicht* betroffen ist, gibt es eine
grüne **Kontrollgruppe**. Ohne die wäre ein `xfail` nicht von einem kaputten
Test zu unterscheiden.

Beim Schließen eines Befunds reicht „der Test ist grün" ausdrücklich **nicht**.
Der Marker fällt erst, wenn drei Dinge gelten:

1. Der Test läuft ohne `--runxfail` grün — also nicht nur „nicht mehr rot".
2. Der Test misst noch, was er behauptet. Genau daran wäre der Beweistest zu
   S-002 fast gescheitert: nach dem Fix hätte sein Aufbau einen `ExportError`
   erzeugt, bevor die eigentliche Zusicherung erreicht wird. Ein Test, der aus
   einem neuen Grund grün wird, ist kein Nachweis, sondern ein Deckmantel.
3. Mit künstlich entferntem Fix fällt er wieder — und zwar an derselben Stelle
   wie vorher. Geprüft über ein Wegwerf-Pytest-Plugin, das `dispatch` bzw.
   `_plugin_is_active` zur Laufzeit neutralisiert; **kein Produktivcode wird
   dafür angefasst**, auch nicht kurzzeitig.

---

## Befunde

### S-001 CRUD-Views laufen weiter, wenn das Plugin abgeschaltet ist
Schweregrad: mittel
Status: **behoben** (persistence-dev, verifiziert am 2026-08-02) — siehe unten
Betroffen: `pretix_custom_reports/views/crud.py:94` (`EventReportMixin` und alle fünf Views darunter)
Zuständig: persistence-dev
Reproduktion: `test_every_event_view_404s_when_the_plugin_is_off` (jetzt grün, ohne Marker), `test_no_crud_view_is_missing_the_plugin_gate`, `test_the_permission_check_runs_before_the_plugin_gate_not_after`, Nachbarpfade `test_the_endpoints_that_do_have_the_plugin_gate_really_404`

Auswirkung:
`views/api.py` hat einen `PluginActiveMixin`, `views/portability.py` hat ihn
(bewusst dupliziert), `views/templates.py` hat die Organizer-Variante,
`views/editor.py` erbt ihn. `views/crud.py` hat ihn nicht. Ergebnis: die Liste,
das Anlege-, Änderungs-, Duplizier- und Löschformular antworten in einem Event,
für das das Plugin abgeschaltet wurde, mit 200. Gemessen:
`GET /control/event/dummy/plain/customreports/reports/` → 200.

Das ist kein Datenabfluss — die Rechteprüfung greift unverändert — aber im
pretix-Modell ist „Plugin aus" die Notbremse, und SPEC.md F1 verlangt
ausdrücklich das Gegenteil. Praktisch heißt es: ein Veranstalter, der nach einem
Vorfall das Plugin für ein Event deaktiviert, kann weiter Reports anlegen und
ändern, und die Änderung wird beim Wiedereinschalten wirksam. `frontend-dev` hat
genau diese Ergänzung in Welle 1 empfohlen (Statusbericht, Punkt 4 der
Entscheidungen); sie ist bei `persistence-dev` nicht angekommen.

Empfehlung:
`PluginActiveMixin` aus `views/api.py` in `EventReportMixin` mischen — eine
Zeile. Sauberer wäre, das Mixin einmal zu besitzen (es steht heute zweimal
wörtlich im Repo) und aus beiden Modulen zu importieren; das ist aber ein
Eigentumswechsel und gehört an den `integrator`.

**Status — behoben, verifiziert.**
`persistence-dev` hat in `views/crud.py:98` ein eigenes `PluginActiveMixin`
ergänzt (404, wenn `"pretix_custom_reports" not in event.get_plugins()`) und
`EventReportMixin` davon erben lassen. Bewusst dupliziert statt aus
`views/api.py` importiert, nach dem Vorbild von `views/portability.py`; damit
steht das Mixin nun **dreimal** wörtlich im Repo (siehe Nacharbeit unten).

Gegengeprüft habe ich drei Dinge, die die ursprüngliche Fassung meines Tests
nicht abdeckte:

1. **Alle fünf Views, nicht vier.** `ReportDuplicateView` ist POST-only; ein GET
   dagegen ist 405, ob das Gate existiert oder nicht — der alte Test hätte dort
   also gar nicht fehlschlagen können. Der Test greift jetzt jede Ansicht mit
   der Methode an, die tatsächlich schreibt (`POST` auf add, edit, duplicate,
   delete) und prüft anschließend die Tabelle: nichts angelegt, nichts
   umbenannt, nichts gelöscht.
2. **Die Klassenhierarchie, nicht nur die Routen.** `test_no_crud_view_is_missing_the_plugin_gate`
   läuft über `crud.__all__`, filtert auf Django-Views und verlangt für jede
   das Mixin. Eine sechste Ansicht, die morgen dazukommt und in diesem Testmodul
   nicht geroutet ist, fällt damit trotzdem auf.
3. **Der Fix hält aus dem richtigen Grund.** Mit zur Laufzeit entferntem Gate
   (`PluginActiveMixin.dispatch` auf ein reines `super()` gesetzt, Produktivcode
   unangetastet) antwortet `GET /control/event/dummy/plain/customreports/reports/`
   wieder mit **200** und der Test fällt genau dort — nicht an einer Nebenwirkung.

**Korrektur an der Begründung des Fixes.**
`handoff/status/persistence-dev.md` („Nacharbeit vor Welle 4") begründet den Fix
damit, die MRO stelle das Plugin-Gate **vor** die Rechteprüfung, es gebe deshalb
„404 statt 403". Die MRO-Aussage stimmt (nachgewiesen, das Gate ist in allen
fünf Klassen links von `EventPermissionRequiredMixin`), die Schlussfolgerung
nicht: `EventPermissionRequiredMixin` implementiert **kein** `dispatch`. Es
überschreibt `as_view()` und wickelt die fertige View in
`event_permission_required(...)` ein (`pretix/control/permissions.py:81-91`).
Der Rechte-Decorator sitzt damit *außerhalb* der gesamten Dispatch-Kette und
läuft **vor** jedem Mixin. Gemessen: ein Nutzer ohne
`event.settings.general:write` bekommt auf
`.../plain/customreports/reports/add/` **403**, nicht 404.

Das ist kein Befund — beide Tore weisen ab, und diese Reihenfolge ist die
vorsichtigere: wer die Seite ohnehin nicht sehen darf, erfährt am Statuscode
nichts über den Plugin-Zustand. Festgehalten ist es trotzdem, in
`test_the_permission_check_runs_before_the_plugin_gate_not_after`, damit die
falsche Begründung nicht bei der nächsten View wiederverwendet wird und damit
ein späteres Verschieben der Rechteprüfung in `dispatch()` sichtbar wird statt
still zu passieren. Gilt wortgleich für `views/api.py`, `views/portability.py`
und `views/templates.py`, die dieselbe Mixin-Kombination benutzen.

**Nacharbeit (nicht sicherheitsrelevant, an den `integrator`).**
Das Gate steht jetzt dreimal wörtlich im Repo (`api.py`, `portability.py`,
`crud.py`) plus einmal als Organizer-Variante in `templates.py` und einmal als
`_plugin_is_active` im Exporter. Ein gemeinsames `views/_mixins.py` in Welle 4
wäre der richtige Ort; bis dahin gilt: wer eines ändert, ändert alle.

---

### S-002 Der Organizer-Export ignoriert das Plugin-Gate
Schweregrad: mittel
Status: **behoben** (exporter-dev, verifiziert am 2026-08-02) — siehe unten
Betroffen: `pretix_custom_reports/exporters.py:318` (`report_choices`), `:371` (`iterate_list`), `:709` (`register_multievent_report_exporter`)
Zuständig: exporter-dev
Reproduktion: `test_an_organizer_export_skips_events_with_the_plugin_switched_off` (jetzt grün, ohne Marker), `test_the_organizer_export_form_never_offers_a_switched_off_events_report`, Nachbarpfad `test_the_event_level_exporter_is_invisible_when_the_plugin_is_off`

Auswirkung:
Auf Event-Ebene ist alles in Ordnung: `register_data_exporters` ist ein
`EventPluginSignal` und feuert für ein Event ohne das Plugin gar nicht erst
(Kontrollgruppe, grün).

Auf Organizer-Ebene ist `register_multievent_data_exporters` ein
`OrganizerPluginSignal(allow_legacy_plugins=True)`. Der Exporter ist damit für
**jeden** Organizer aktiv — das steht so im Docstring und ist als harmlos
eingeschätzt. Ist es nicht ganz: `self.events` kommt aus
`init_organizer_exporters` und ist **nur nach Berechtigung** gefiltert, nicht
danach, ob das Plugin dort läuft. `report_choices()` und `_prepare()` gehen
beide über `self.events`, also liefert ein Event, für das das Plugin abgeschaltet
wurde, weiterhin Bestelldaten in die Exportdatei — und, weil derselbe Weg unter
`ScheduledOrganizerExport` läuft, in eine wiederkehrende Mail.

Gemessen: Event `plain` (Plugin aus) mit einem übrig gebliebenen Report
`LEFTOVER` und einer Bestellung `OFFEV`; ein Organizer-Export über beide Events
liefert `OFFEV` in der CSV aus.

Zusammen mit S-001 ergibt das: „Plugin ausschalten" stoppt in diesem Stand
weder das Anlegen noch das Ausführen von Reports.

Empfehlung:
In `report_choices` und in `iterate_list` auf die Events einschränken, die das
Plugin führen. Nicht über `plugins__contains` (Teilstringtreffer auf einem
längeren Plugin-Namen), sondern über die Prüfung, die pretix selbst benutzt:
`self.plugin_module in event.get_plugins()` je Event in `_prepare()`, mit einer
`_EventProblem`-Meldung, die den Grund nennt. Der Skip-Pfad existiert bereits und
ist die richtige Stelle.

**Status — behoben, verifiziert.**
`exporter-dev` hat die Empfehlung eins zu eins umgesetzt:
`CustomReportExporter._plugin_is_active` (`exporters.py:263`) prüft
`self.plugin_module in event.get_plugins()` — die Prüfung, die pretix selbst vor
der Zustellung eines Event-Plugin-Signals macht (`pretix/base/signals.py:100-103`),
nicht `plugins__contains`. Angewandt wird sie an beiden Stellen:
`report_choices()` filtert `self.events`, und `_prepare()` prüft **vor** dem
Report-Lookup und wirft ein `_EventProblem` mit eigener Meldung („the plugin is
not enabled for this event"). Damit läuft ein abgeschaltetes Event über denselben
Skip-/Fail-Pfad wie ein gelöschter Report, und der Grund überlebt bis in den
`ExportError`, den der Eigentümer eines terminierten Exports per Mail bekommt.

**Der Beweistest musste umgebaut werden — und das war nötig, nicht kosmetisch.**
Die Wellen-3-Fassung legte `LEFTOVER` **nur** im abgeschalteten Event an. Nach
dem Fix kann damit kein Event mehr etwas liefern, `render()` stirbt an
„could not be run for any of the selected events", und die Zusicherung
`assert b"OFFEV" not in data` wird nie erreicht. Der Test wäre also grün
geworden, ohne das Leck je gemessen zu haben — genau die Sorte Test, die eine
Behebung vortäuscht. Jetzt halten **beide** Events denselben Identifier
`LEFTOVER` und je eine Bestellung; die Datei enthält Zeilen, und das Einzige,
was `OFFEV` heraushalten kann, ist das Gate. Zusätzlich geprüft: `AAAAA` und
`dummy` sind weiterhin drin — ein deaktiviertes Event darf einen Organizer nicht
die übrigen kosten.

Gegenprobe wie bei S-001: mit zur Laufzeit auf `True` gezwungenem
`_plugin_is_active` (Produktivcode unangetastet) liefert die CSV wieder
`"plain","Plain Event","OFFEV"` und der Test fällt genau an dieser Zeile.

**Zweite Facette, neu abgedeckt.** `report_choices()` ist nicht nur eine
Bequemlichkeit: der *Name* eines Reports aus einem abgeschalteten Event ist
selbst eine Auskunft, die die Notbremse stoppen sollte, und eine Auswahl
anzubieten, die `_prepare()` anschließend verweigert, ist ein Fehlerpfad ohne
Nutzen. `test_the_organizer_export_form_never_offers_a_switched_off_events_report`
prüft beides: `OFFONLY` (nur im abgeschalteten Event) fehlt in den Choices,
`LIVEONE` ist da, und kein Label verrät den Namen.

`exporter-dev` hat denselben Sachverhalt aus eigener Sicht in
`tests/test_exporters.py::test_an_event_with_the_plugin_switched_off_contributes_no_rows`
und zwei Nachbartests. Doppelt und aus zwei Richtungen ist hier richtig: mein
Test greift über `init_organizer_exporters` und `render()` an, also über den
Weg, den `ScheduledOrganizerExport` nimmt.

---

### S-003 Ungepaarte Surrogate legen drei Endpunkte auf 500 und sind persistierbar
Schweregrad: mittel
Betroffen: `pretix_custom_reports/portability/payload.py:204` (`load_json_object`), Folgestellen `pretix_custom_reports/views/api.py:353`, `pretix_custom_reports/views/portability.py:163`, `pretix_custom_reports/views/templates.py:282`
Zuständig: portability-dev (Gate), frontend-dev (`views/api.py`)
Reproduktion: `test_a_lone_surrogate_is_refused_by_the_payload_gate`, `test_the_validate_endpoint_survives_a_lone_surrogate`, `test_the_preview_endpoint_survives_a_lone_surrogate`, `test_the_export_view_survives_a_stored_lone_surrogate` (alle xfail); Grenze: `test_the_csv_path_survives_a_stored_lone_surrogate` (grün)

Auswirkung:
`"\ud800"` ist **syntaktisch gültiges JSON**. `json.loads` liefert dafür einen
Python-String mit einem einzelnen High-Surrogate, und der lässt sich nicht nach
UTF-8 encodieren. Das Payload-Gate prüft Größe, Tiefe, Knotenzahl, Stringlänge,
Zahlenlänge, `NaN`, doppelte Member — aber nicht die Encodierbarkeit. Der
Strukturvalidator prüft nur Längen. Der Wert landet also unverändert in einem
`Column.label` oder einem Filterwert und von dort in die Datenbank
(`JSONField` schreibt mit `ensure_ascii=True`, das geht durch).

Danach:

| Endpunkt | Ergebnis |
| --- | --- |
| `POST api/validate/` | `UnicodeEncodeError` in `django/http/response.py:324` → 500 |
| `POST api/preview/` | dasselbe (die Spaltenüberschrift wird zurückgegeben) → 500 |
| `GET .../reports/<pk>/export/` | `UnicodeEncodeError` in `views/portability.py:165` → 500 |
| Vorlagen-Export | dieselbe Zeile in `views/templates.py:282` |
| CSV-/XLSX-Export | **unauffällig** — `ListExporter` encodiert mit `errors="replace"` |
| Editor-Seite | **unauffällig** — `escapejson_dumps` benutzt `ensure_ascii=True` |

Die letzten beiden Zeilen sind wichtig, damit der Befund nicht überzeichnet
wird: der Report bleibt exportierbar und reparierbar. Was kaputt geht, sind
genau die drei Stellen, die mit `ensure_ascii=False` serialisieren.

Zwei Wege hinein, beide realistisch:

1. eigenes Zutun über das JSON-Textfeld des CRUD-Formulars oder das JSON-Panel
   des Editors — Selbstschaden,
2. **eine Importdatei**. Der Import ist der Weg, auf dem ein Administrator ein
   Dokument von außen einspielt, und `payload.py` ist ausdrücklich als das Gate
   gebaut, das solche Dokumente abfängt. Eine Datei mit
   `"label": "\ud800"` wird heute akzeptiert, gespeichert, und anschließend
   antwortet die Vorschau des importierten Reports mit 500.

Empfehlung:
1. In `payload.load_json_object`, direkt neben der bestehenden Stringprüfung in
   `_walk`: `value.encode("utf-8")` versuchen und mit einem neuen
   `REASON_NOT_UTF8`-Code ablehnen. Das ist die Stelle, an der der Rest des
   Pfades sich darauf verlässt, „nur JSON-Primitive" bekommen zu haben.
2. `views/api.py:353` auf `ensure_ascii=True` umstellen (der Editor liest die
   Antwort mit `JSON.parse`, `\uXXXX` ist dort gleichwertig), ebenso
   `views/portability.py:163` und `views/templates.py:282`.
3. Optional `envelope._clean_text` — das die Kontrollzeichen bereits filtert —
   auch auf Labels und Filterwerte anwenden. Das wäre allerdings eine Änderung
   an einer Contract-nahen Stelle; Punkt 1 und 2 reichen.

Nicht empfohlen: eine Prüfung in `contracts.validate_definition`. Die Contracts
sind eingefroren, und die Behebung ist ohne sie möglich.

---

### S-004 Doppelter Identifier wird ein IntegrityError statt eines Formularfehlers
Schweregrad: niedrig
Betroffen: `pretix_custom_reports/forms.py:103` (`fields` führt `identifier`, aber nicht `event`), `pretix_custom_reports/models.py:280` (`UniqueConstraint(["event", "identifier"])`)
Zuständig: persistence-dev
Reproduktion: `test_a_duplicate_identifier_is_a_form_error_not_a_500` (xfail)

Auswirkung:
`ReportDefinitionForm` bietet `identifier` als Eingabefeld an. Die
Eindeutigkeitsbedingung ist `(event, identifier)`; `event` ist kein Formularfeld
und landet deshalb in Djangos `_get_validation_exclusions`, womit
`validate_unique` die Prüfung überspringt. Ein zweiter Report mit demselben
Identifier läuft in

```
django.db.utils.IntegrityError: UNIQUE constraint failed:
  pretix_custom_reports_reportdefinition.event_id, ...identifier
```

also in einen 500, mitten in `@transaction.atomic`. Jeder Nutzer mit
`event.settings.general:write` kann das auslösen, versehentlich oder absichtlich;
Auswirkung ist eine Fehlerseite und ein Eintrag im Error-Tracking, kein
Datenverlust.

Der Editor ist besonders exponiert, weil er den Identifier laut
`handoff/status/frontend-dev.md` als verstecktes Feld zurückpostet — beim
Duplizieren eines Browser-Tabs entsteht die Kollision also ohne Zutun.

Empfehlung:
In `ReportDefinitionForm` ein `clean_identifier` ergänzen, das gegen
`ReportDefinition.objects.for_event(self.instance.event).by_identifier(value)
.exclude(pk=self.instance.pk)` prüft (für Vorlagen analog
`templates_for_organizer`). Alternativ `_get_validation_exclusions` überschreiben
und `event`/`organizer` daraus entfernen.

---

### S-005 Eine Vorschau mit vielen `join`-Spalten kostet eine Query pro Spalte
Schweregrad: niedrig
Betroffen: `pretix_custom_reports/query/plan.py:400` (`_dedupe_prefetches`), `pretix_custom_reports/query/relations.py:563` (`to_attr=leaf_to_attr`)
Zuständig: query-dev
Reproduktion: `test_a_report_full_of_join_columns_costs_one_query_per_column` (grün, misst)

Auswirkung:
`join`-Spalten werden absichtlich über `Prefetch` mit einem **pro Spalte
eindeutigen** `to_attr` gelöst, damit drei `join`-Spalten über dieselbe Relation
mit drei verschiedenen Bedingungen nebeneinander stehen können. Der Preis ist,
dass die Dedup-Regel `(lookup, to_attr)` nie greift: 20 identische
`join`-Spalten kosten gemessen ≥ 15 zusätzliche Queries, 200 (`MAX_COLUMNS`)
entsprechend ~200.

`POST api/preview/` ist nur entprellt, nicht rate-limitiert, und ein Angreifer
braucht `event.orders:read`. Das ist keine Verstärkung, mit der man eine
Instanz umwirft, aber es ist der einzige Ort im Plugin, an dem die Kosten eines
Requests linear mit einer vom Nutzer wählbaren Zahl wachsen — und der Rest des
Compilers hat für genau diese Eigenschaft Query-Zahl-Tests.

Empfehlung:
Entweder eine eigene, kleinere Obergrenze für `join`-Spalten (z. B. 20, mit
`CompilationError`), oder die Dedup-Regel auf „gleicher Lookup **und** gleiche
Bedingung" heben und den `to_attr` daraus ableiten, statt aus dem Spaltenindex.
Die zweite Variante ist die richtige, aber sie braucht einen stabilen
Vergleich zweier `Q`-Objekte — machbar über `str(Q)`, mit dem üblichen
Vorbehalt.

---

### S-006 Die Importansicht lässt sich per POST auf die Event-Kopie-Strategie stellen
Schweregrad: niedrig
Betroffen: `pretix_custom_reports/views/portability.py:237`, `pretix_custom_reports/views/templates.py:367`, `pretix_custom_reports/portability/resolution.py:769`
Zuständig: portability-dev
Reproduktion: `test_the_import_view_cannot_be_talked_into_the_event_copy_strategy` (xfail), Kontrollgruppe `test_the_same_definition_is_refused_under_the_offered_strategies`

Auswirkung:
`ResolutionStrategy.coerce()` akzeptiert alle drei Strategien, und beide Views
reichen `request.POST.get("strategy")` unverändert hinein. Die Oberfläche bietet
nur `abort` und `skip` an — `keep` ist laut Docstring die Strategie der
Event-Kopie, „wo niemand vor einer Bestätigungsseite steht".

Unter `keep` überspringt `resolve_definition` den zweiten der beiden
dokumentierten Nachprüfungsschritte, `query.plan.check_definition`. Gemessen: die
Definition „Spalte `position.price` auf Basis `order`" (auflösbar, aber ohne
Aggregat nicht kompilierbar) wird unter `abort` und unter `skip` abgelehnt und
unter `keep` **gespeichert**.

Keine Rechteausweitung — dieselbe Zeile ließe sich über das CRUD-Formular
anlegen, das ohnehin nur strukturell validiert — und kein Datenabfluss. Der
Punkt ist, dass `portability/resolution.py` diese Prüfung als
Sicherheitseigenschaft des Importpfades beschreibt („Wäre der Importeur
großzügiger als der Compiler, speicherten wir Reports, die beim ersten
terminierten Lauf scheitern"), und ein POST-Feld sie abschaltet.

Empfehlung:
Eine zweite, engere Coerce-Funktion für die Views:
`ResolutionStrategy.coerce_user_choice(value)` mit `ABORT`/`SKIP` und Fallback
`ABORT`. `KEEP` bleibt programmatisch erreichbar für `eventcopy.py`.

---

## Geprüft und in Ordnung

Damit der nächste Reviewer weiß, was schon abgeklopft ist — und damit die
Abwesenheit von Befunden nicht mit Abwesenheit von Prüfung verwechselt wird.

### Registry-Umgehung (Prüfschwerpunkt 1)

* Kein `eval`, `exec`, `compile`, `__import__`, `.raw()`, `.extra()`, `RawSQL`
  im **gesamten** Paket, über den Syntaxbaum geprüft
  (`test_no_module_evaluates_code_or_builds_raw_sql`).
* Kein f-String, der aus Definitionsdaten einen Lookup baut
  (`test_every_dynamic_lookup_keyword_comes_from_a_named_variable`).
* Die Lookup-Suffix-Tabelle in `query/filters.py` ist geschlossen und besteht
  nur aus Literalen (`test_the_lookup_suffix_table_is_closed_and_contains_no_user_input`).
* Fünf Varianten, ORM-Vokabular in eine Spalte zu schmuggeln (`orm_path`,
  `order__code`, `order.code__icontains`, `lookup`, `annotation`) scheitern
  strukturell; sieben Django-Lookups als `operator` ebenso.
* Eine **handgebaute** `contracts.ReportDefinition` mit
  `field="order__event__organizer__name"`, die den JSON-Validator komplett
  umgeht, scheitert an der Registry-Allowlist, und es wird dabei keine Zeile aus
  `Order`/`OrderPosition` gelesen (`CaptureQueriesContext`).
* `ColumnFormat.separator` ist ein `str.join`-Argument: `"'; DROP"` taucht in der
  erzeugten SQL nicht auf.
* Die von `query-dev` beschriebene Registry-Naht (`registry/hints.py`) hält: die
  Annotation-Closures prüfen `ctx.event.pk` und werfen `FieldContractError`,
  wenn ein für Event A gebautes Feld mit einem Kontext für Event B benutzt wird
  (`test_an_annotation_built_for_one_event_refuses_a_foreign_context`).

### Event- und Mandantentrennung (Prüfschwerpunkt 2)

* Report eines fremden Events über Editor, CRUD-Edit und Export-View: 404.
* Organizer-Vorlage über die Event-Routen: 404.
* Vorlage eines fremden Organizers: `TemplateAccessDenied`, und
  `available_templates()` listet sie gar nicht erst.
* Die Vorschau eines Events zeigt nur dessen Bestellungen, auch wenn ein zweites
  Event desselben Organizers Bestellungen hat.
* Der Exporter löst einen Report-Identifier **nie** global auf: ein Identifier,
  den nur ein fremdes Event kennt, ergibt einen `ExportError`, keine Zeile.
* `init_organizer_exporters` übergibt nur Events des eigenen Organizers.
* Die Registry eines Events veröffentlicht keine Frage eines anderen Events.
* Die Event-Kopie über Organizergrenzen (`scopes_disabled()` + hartes `event=`)
  liest wirklich nur das Quellevent — der Fund, den `portability-dev` selbst
  gemeldet hat, ist dicht.

### Berechtigungen (Prüfschwerpunkt 3)

Dreizehn Event-Endpunkte × drei feindliche Akteure, jeder einzeln:

| Akteur | Ergebnis |
| --- | --- |
| nicht angemeldet | 302 auf `/login` (oder 403) |
| Nutzer desselben Organizers ohne `event.orders:read` | 403 / 404 |
| Voll-Admin eines **fremden** Organizers | 403 / 404 |

Dazu: fünf Organizer-Vorlagen-Endpunkte gegen dieselben Akteure; der
Nur-Lese-Nutzer bekommt Feldbibliothek, Editor, Liste, Vorschau und Export und
wird bei Anlegen, Ändern, Duplizieren, Löschen, Import und „Vorlage laden"
abgewiesen. Die **Auflösungsvorschau des Imports** (Schritt 1, der bereits gegen
echte Daten auflöst) hängt korrekt am Schreibrecht.

Kein Modul benutzt `csrf_exempt` (über den Syntaxbaum geprüft — ein Textscan
würde am Docstring von `views/api.py` scheitern, der das Wort erwähnt). Alle
POST-Endpunkte, JSON wie Formular, geben ohne Token 403. `api.fields` lehnt
POST ab, `api.validate`/`api.preview` lehnen GET ab, Duplizieren ist POST-only.

Owner-Fälschung: weder `event`/`organizer` im POST-Body noch ein
`instance-event`-Feld ändern, wem ein Report gehört; die
XOR-Check-Constraint hält auch gegen `bulk_create`.

### Import (Prüfschwerpunkt 4)

* Alle 17 `invalid/`-Fixtures werden abgelehnt, danach ist die Tabelle leer.
* `schema_version` 2, 999, 0, −1, `1.0`, `true`, `"1"` — alle abgelehnt.
* Doppelte Member, `NaN`, `Infinity`, `1e999`, Top-Level-Array, Top-Level-String,
  nicht-UTF-8, leer — alle abgelehnt.
* Tiefe > 20 wird **vor** `json.loads` abgelehnt; ein String voller `[` ist kein
  Bypass und wird korrekt nicht mitgezählt.
* Knotenzahl > 20 000, String > 10 000 Zeichen, Datei > 512 KiB — abgelehnt.
* Eine Uploaddatei, die über ihre `size` **lügt**, wird trotzdem gekappt
  (die Leseobergrenze ist das echte Gate, nicht `upload.size`).
* Unicode-Tricks in Feldschlüsseln (Nullbyte, Bidi-Override, Homoglyph,
  BOM, Fullwidth, Zero-Width-Space) werden entweder strukturell abgelehnt oder
  lösen nicht auf — keiner wird stillschweigend zu `order.code`.
* Ein Kern-Key wird **nie** über Ähnlichkeit gematcht (`order.c-o-d-e` bleibt
  `missing`).
* Schritt 2 des Imports re-parst die Originalbytes: ein zusätzlich mitgesendetes,
  feindliches Dokument hat keine Wirkung.
* `meta.references` kann keinen Key erfinden — Hinweise für Keys, die die
  Definition nicht benutzt, werden verworfen, und **alles**, was der Resolver
  ausgibt, steht in der Registry des Zielevents (der zentrale Test:
  `test_everything_the_resolver_outputs_comes_from_the_target_registry`).
* Eine Importdatei kann den Identifier eines bestehenden Reports **nicht**
  übernehmen — das wäre die Umleitung eines terminierten Exports.
* Das Portability-Paket importiert kein `pickle`/`marshal`/`yaml`/`shelve`/
  `subprocess`/`os` (über den Syntaxbaum).

### Ausgabe (Prüfschwerpunkt 5)

* CSV-Injection ist durch `defusedcsv` neutralisiert — geprüft **in der Zelle
  und in der Kopfzeile**, denn die Spaltenüberschrift ist freier Nutzertext.
  (`exporter-dev` hatte nur die Zelle geprüft.)
* Fünf feindliche Reportnamen (`../../etc/passwd`, `"; rm -rf /`, CRLF, 500
  Zeichen, Unicode) ergeben einen ASCII-Dateinamen ohne `/`, `\`, `..`, `"`,
  CR/LF, und der `Content-Disposition`-Header bleibt einzeilig.
* Der Dateiname des Exporters bleibt auch bei einem gepunkteten Event-Slug ein
  reiner Name (die Annahme über pretix' Slug-Regex ist jetzt festgenagelt).
* Die Vorschau escapet Bestelldaten **und** eine feindliche Spaltenüberschrift.
* Ein gespeichertes `</script><script>alert(1)</script>` als Spaltentitel bricht
  nicht aus dem JSON-Konfigblock des Editors aus (`escapejson_dumps`).
* Das Editor-JavaScript rendert Servertexte über `textContent`; der
  `html:`-Ausweg des `el()`-Helfers wird nirgends benutzt (festgenagelt).

### Hintergrundausführung (Prüfschwerpunkt 6)

* Ein terminierter Export, dessen Eigentümer die Rechte verloren hat, erzeugt
  **keine** Datei: `init_event_exporter` findet den Exporter nicht mehr,
  `error_counter` steigt auf 1, keine Mail hat einen Anhang.
* Ein terminierter Export läuft mit aktivem django-scopes-Scope — der Beweis ist
  indirekt, aber strikt: der Report wird über einen `ScopedManager` gelesen, ein
  Lauf ohne Scope wäre ein `ScopeError`.
* `exporters.py` ruft weder `scope()` noch `scopes_disabled()` (über den
  Syntaxbaum; ein Textscan scheitert hier am Docstring).
* `scopes_disabled()` wird im ganzen Paket nur in `models.py` und
  `eventcopy.py` aufgerufen, beide mit hartem Filter daneben.
* Sieben feindliche `export_form_data`-Varianten (Dict statt String, Liste,
  Pfad als `_format`, `true`/`-1`/`"1; DROP"` als `row_limit`, `"maybe"` als
  Tri-State) ergeben je einen `ExportError` — nie eine ungeprüfte Verwendung.

### Ressourcen (Prüfschwerpunkt 7)

* Die Vorschau schneidet in SQL (`LIMIT` im erzeugten Statement), nicht in
  Python.
* Acht Varianten des `limit`-Parameters (0, −1, 10⁹, `"20"`, `3.5`, `true`,
  `null`, `[20]`) enden immer in `1..PREVIEW_ROW_LIMIT`.
* 100 Filterbedingungen kompilieren zu **einer** Query.
* Mehr als `MAX_COLUMNS` Spalten und ein `row_limit` über `MAX_ROW_LIMIT` werden
  strukturell abgelehnt.
* `GET api/fields/` bleibt auch mit fünf zusätzlichen Choice-Fragen unter 60
  Queries.

---

## Unbestätigt

Vermutungen ohne belastbaren Test. Keine davon ist ein Befund; sie stehen hier,
damit sie nicht zweimal gefunden werden müssen.

**U-01 — Nullbyte in einem Label/Filterwert unter PostgreSQL.**
`contracts.validate_definition` akzeptiert `"a\u0000b"` als Spaltentitel
(nachgewiesen: `test_a_null_byte_in_a_label_is_accepted_by_the_structural_validator`).
SQLite speichert das. PostgreSQL `jsonb` **kann** `\u0000` in einem String nicht
darstellen und wirft beim Schreiben. Das wäre derselbe Persistenzweg wie S-003,
nur mit einem anderen Zeichen und einem `DataError` statt eines 500 beim Lesen.
Nicht beweisbar, solange die Testumgebung SQLite ist. Wer S-003 behebt, sollte
das Nullbyte in derselben Prüfung mit erschlagen.

**U-02 — Unicode-Faltung im Resolver.**
`resolution._normalise()` faltet über `str.lower()` und `str.isalnum()`, also
über den vollen Unicode-Raum. `answer.İD` (U+0130) faltet zu `id` und könnte
damit eine Frage `id` des Zielevents treffen. Das führt **nie** aus dem
Zielevent heraus — das Ergebnis kommt aus dessen Registry — und der
Auflösungsbericht zeigt es als `mapped` an. Kein Sicherheitsversprechen
verletzt, deshalb kein Test und kein Befund; erwähnenswert, falls das Matching
je stiller wird.

**U-03 — Kein Rate-Limit auf `api/preview/` und `api/validate/`.**
Beide führen echte Queries aus, beide sind authentifiziert. In pretix gibt es
für Control-Panel-Endpunkte auch sonst keins; eine eigene Drossel wäre eine
Abweichung vom Kern. Nur in Kombination mit S-005 relevant.

**U-04 — `MAX_ROW_LIMIT = 1 000 000` im interaktiven Export.**
Die Streamingkette ist durchgehend faul, aber eine Million Zeilen × 200 Spalten
ist trotzdem eine lange Transaktion und eine große Datei. `exporter-dev` hat den
Speicherverbrauch bewusst nicht gemessen; `test-engineer` hat es auf dem Plan.
Kein eigener Test von mir, weil er nur die Laufzeit der Suite kostet.

**U-05 — PostgreSQL insgesamt nicht verifiziert.**
Gilt wie bei `registry-dev`, `query-dev` und `exporter-dev`. Für den Review
konkret betroffen: U-01, `nulls_last`, und die Frage, ob eine der
Aggregat-Subqueries dort einen anderen Ausgabetyp liefert.

**U-06 — Die Browser-Tests skippen ohne Browser.**
`tests/test_editor_api.py` überspringt seine sechs Playwright-Tests still, wenn
kein Edge/Chrome vorhanden ist. Auf einem CI ohne Browser fällt damit die
Abdeckung von Drag & Drop, select2 und der Verlassen-Nachfrage weg, **ohne dass
es auffällt**. Das ist keine Schwachstelle, aber es ist eine Prüfung, die man zu
haben glaubt. Empfehlung an den `integrator`: entweder `playwright install` ins
CI-Image oder `--strict-markers` plus ein bewusstes `xfail`.

**U-07 — Alles hängt an `urls.py` und `signals.py`.**
Beide sind noch nicht verdrahtet (`integrator`, Welle 4). Sämtliche
View- und Exporter-Befunde betreffen deshalb Code, der produktiv derzeit nicht
erreichbar ist. Das ändert die *Dringlichkeit*, nicht den Befund — und es heißt,
dass S-001 und S-002 vor dem Verdrahten am billigsten zu beheben sind.
*Erledigt:* genau das ist am 2026-08-02 passiert, beide sind vor der Verdrahtung
behoben. Für S-003 bis S-006 gilt der Absatz unverändert weiter.

---

## Zusammenfassung nach Zuständigkeit

Stand 2026-08-02. „behoben" heißt: Fix gelesen, Beweistest ohne `xfail` grün,
und bei künstlich entferntem Fix fällt derselbe Test wieder am ursprünglichen
Leck.

| Befund | Schweregrad | Zuständig | Status |
| --- | --- | --- | --- |
| S-001 CRUD-Views ohne Plugin-Gate | mittel | persistence-dev | **behoben** (2026-08-02) |
| S-002 Organizer-Export ohne Plugin-Gate | mittel | exporter-dev | **behoben** (2026-08-02) |
| S-003 Ungepaarte Surrogate | mittel | portability-dev (Gate), frontend-dev (`views/api.py:353`) | offen |
| S-004 Doppelter Identifier → IntegrityError | niedrig | persistence-dev | offen |
| S-005 Query-Amplifikation bei `join`-Spalten | niedrig | query-dev | offen |
| S-006 `keep`-Strategie per POST erreichbar | niedrig | portability-dev | offen |

| Agent | Offene Befunde |
| --- | --- |
| portability-dev | S-003 (mittel, gemeinsam mit frontend-dev), S-006 (niedrig) |
| frontend-dev | S-003, Teil 2 (`views/api.py:353`) |
| persistence-dev | S-004 (niedrig) — S-001 geschlossen |
| query-dev | S-005 (niedrig) |
| exporter-dev | keine — S-002 geschlossen |
| contract-architect | keine — die Contracts haben gehalten |
| registry-dev | keine — die Event-Bindung der Annotationen ist die stärkste Naht im Plugin |

## Ausführen

```
pytest tests/test_security.py -q          # 128 passed, 6 xfailed
pytest tests/test_security.py --runxfail  # zeigt die sechs echten Fehler
```

Die sechs verbleibenden `xfail(strict=True)` sind vier zu S-003, einer zu S-004
und einer zu S-006. Wer einen davon behebt, macht die Suite rot (`XPASS`) — das
ist Absicht und erzwingt, den Marker zu entfernen und den Befund zu schließen,
so wie es hier für S-001 und S-002 geschehen ist.
