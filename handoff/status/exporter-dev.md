# Status: exporter-dev

Zwei Läufe, chronologisch: [Welle 2](#welle-2) (Aufbau) und darüber der
Nacharbeits-Lauf zu Befund S-002 aus dem Security-Review.

---

# Nacharbeit S-002 — das Plugin-Gate im Organizer-Export

Gezielter Lauf vor Welle 4. Geändert: `pretix_custom_reports/exporters.py` und
`tests/test_exporters.py` (33 → 40 Tests). `tests/test_security.py` nicht
angefasst, `signals.py` nicht angefasst, kein Commit, keine Migration.

## Was der Befund war

`register_multievent_data_exporters` ist ein
`OrganizerPluginSignal(allow_legacy_plugins=True)`; ein Plugin auf Event-Ebene
gilt dort für **jeden** Organizer als aktiv (pretix `base/signals.py:100-113`).
`self.events` kommt aus `init_organizer_exporters` und ist ausschließlich nach
**Berechtigung** gefiltert (`services/export.py:266-287`) — nicht danach, ob das
Plugin im jeweiligen Event läuft. Damit lieferte ein Event, für das jemand das
Plugin abgeschaltet hatte, weiterhin Bestelldaten in die Organizer-Exportdatei,
und über `ScheduledOrganizerExport` in eine wiederkehrende Mail, die niemand
mehr anschaut.

Auf Event-Ebene bestand das Problem nie: `register_data_exporters` ist ein
`EventPluginSignal` und feuert für ein Event ohne Plugin gar nicht, und ein
`ScheduledEventExport` läuft dort in pretix' eigenes
„Export type not found or permission denied." (`services/export.py:365-366`).

## Was jetzt drin steht

**1. Ein Gate, an der Stelle, die pretix selbst benutzt.**

```python
plugin_module = "pretix_custom_reports"          # == apps.PluginApp.name

def _plugin_is_active(self, event) -> bool:
    return self.plugin_module in event.get_plugins()
```

Verifiziert im Source, nicht aus dem Gedächtnis: `is_app_active` vergleicht für
ein Event-Plugin genau `app.name in sender.get_plugins()`
(`pretix/base/signals.py:100-103`), und `Event.get_plugins()` splittet das
kommaseparierte Feld (`base/models/event.py:794-800`). `BaseExporter` hat **kein**
`plugin_module` und keine Plugin-Information — das Attribut ist unseres, mit
demselben Wert und derselben Prüfung wie die `PluginActiveMixin` der
View-Module.

Ausdrücklich **kein** `plugins__contains`: das Feld ist ein einziger String, ein
Teilstringtreffer würde auch ein `pretix_custom_reports_extra` als „wir" zählen.
Beide Richtungen sind getestet (zu eng / zu weit), siehe unten.

**2. `report_choices()`** baut die Auswahl nur noch aus Events mit aktivem
Plugin. Eine Auswahl anzubieten, die `_prepare` anschließend ablehnt, wäre eine
eigene Fehlerquelle.

**3. `_prepare()`** prüft das Gate als **erstes**, vor dem Report-Lookup, und
wirft das bestehende `_EventProblem` mit

```
The report "codes" cannot be exported for event second: the plugin is not
enabled for this event.
```

Damit nimmt der Fall exakt denselben Weg wie ein gelöschter Report: Voreinstellung
überspringen mit `WARNING` im Log, unter `on_unavailable=fail` ein `ExportError`,
und wenn **kein** Event liefern kann, steht der Grund in der Mail an den
Zeitplan-Eigentümer. Kein stiller Skip.

Die Reihenfolge ist Absicht und getestet: ein abgeschaltetes Event soll auch
dann „Plugin ist aus" melden, wenn der Report dort noch existiert — sonst liest
der Empfänger eine Diagnose, die zu einer anderen Reparatur führt. Nebeneffekt:
in einem abgeschalteten Event werden nicht einmal mehr die Reports gelesen.

**4. Docstrings korrigiert.** Der Docstring von
`register_multievent_report_exporter` nannte die organizerweite Aktivierung
bisher „harmless and deliberate". Das war der eigentliche Denkfehler und steht
jetzt richtig da, mit Verweis auf das Gate; der Modul-Docstring hat einen
entsprechenden Absatz unter „Permissions".

**Neuer übersetzbarer String** (englisch, `gettext`, nicht lazy — er entsteht
zur Laufzeit unter `language(schedule.locale)`), für den `de`-Katalog des
`integrator`:

```
The report "{identifier}" cannot be exported for event {event}: the plugin is
not enabled for this event.
```

## Tests

`pytest tests/test_exporters.py -q` → **40 passed** (vorher 33, alle 33 weiter
grün, insbesondere die Multi-Event-Läufe über zwei Events). Neuer Abschnitt
„3b. The plugin gate (security review S-002)":

| Test | Was er festhält |
| --- | --- |
| `…_with_the_plugin_switched_off_contributes_no_rows` | der Befund selbst: Report und Bestellungen in **beiden** Events, Plugin in einem aus → `ZZZZZ` fehlt, `AAAAA` ist da |
| `…_skipped_through_the_documented_mechanism` | `fail`-Politik nennt Event **und** Grund; alle Events aus → `ExportError`, keine leere Datei |
| `…_gate_comes_before_the_report_lookup` | Report existiert im abgeschalteten Event → trotzdem „plugin is not enabled" |
| `…_choices_hide_reports_of_events_with_the_plugin_switched_off` | vorher/nachher an derselben Auswahl |
| `…_gate_is_not_a_substring_match` | `plugins="pretix_custom_reports_extra"` zählt **nicht** als aktiv |
| `…_neighbouring_plugin_…_does_not_disable_us` | Gegenprobe: `"pretix.plugins.banktransfer,pretix_custom_reports"` zählt sehr wohl |
| `…_scheduled_organizer_export_stops_sending_a_deactivated_event` | derselbe Lauf über `ScheduledOrganizerExport`, geprüft wird der **Mailanhang** |

Gesamtsuite `pytest tests/ -q -m "not performance"` → **999 passed, 2 failed,
9 xfailed**. Beide Fehlschläge sind fremd und unverändert:
`test_smoke.py::test_no_migration_created_yet` (Welle-0-Gate) und
`test_security.py::test_every_event_view_404s_when_the_plugin_is_off`
(**XPASS(strict)** — S-001 ist in `views/crud.py` offenbar bereits behoben, der
xfail-Marker dort ist jetzt zu entfernen; gehört dem `security-reviewer`).

Lint über die eigenen zwei Dateien: `flake8` rc 0, `isort -c` rc 0,
`black --check` unchanged. Kein `black .` / `isort .` über das Repo.

## ⚠ Der xfail-Test zu S-002 wird **nicht** XPASS — bitte lesen

`tests/test_security.py::test_an_organizer_export_skips_events_with_the_plugin_switched_off`
bleibt nach dem Fix **XFAIL**, nicht XPASS. Nicht weil das Leck offen wäre, es
ist zu — sondern weil der Test es nicht messen kann:

Der Test benutzt nur die Fixtures `event` (Plugin an, **kein** Report, **keine**
Bestellung) und `event_without_plugin` (Plugin aus, Report `LEFTOVER`, Bestellung
`OFFEV`). Nach dem Fix kann **kein** Event `LEFTOVER` liefern:

- `dummy`: Report existiert dort nicht,
- `plain`: Plugin aus.

Also greift der bewusste Endpunkt „lieber laut scheitern als leer liefern" und
`render()` wirft

```
ExportError: This report could not be run for any of the selected events.
The report "LEFTOVER" does not exist in event dummy. …
The report "LEFTOVER" cannot be exported for event plain: the plugin is not
enabled for this event.
```

Der Test packt den Rückgabewert von `render()` aber unbedingt aus
(`_name, _mime, data = …`), erreicht sein `assert b"OFFEV" not in data` also nie.
Ergebnis:

```
pytest tests/test_security.py -q -k plugin_switched_off --runxfail   ->  1 failed
```

— mit der ExportError oben statt mit `OFFEV` in der Datei. Ohne `--runxfail`
bleibt die Suite grün (der Test „scheitert" weiter, nur aus dem richtigen Grund),
`strict=True` schlägt also **nicht** an.

Das ist keine Design-Frage, sondern eine Lücke im Testaufbau: mit *null*
lieferfähigen Events kann es keine Datei geben, in der `OFFEV` fehlen könnte.
Ich habe `test_security.py` nicht angefasst (fremdes Gebiet). Zwei Wege, beide
eine Minute Arbeit — für den `security-reviewer`:

1. **Absicht beibehalten** (empfohlen, entspricht dem gemessenen Szenario): im
   Test zusätzlich einen Report `LEFTOVER` **und** eine Bestellung in `event`
   anlegen. Dann liefert `dummy` Zeilen, die Datei entsteht, und
   `assert b"OFFEV" not in data` misst genau das Leck. Genau so liegt der Fall
   als `test_an_event_with_the_plugin_switched_off_contributes_no_rows` in
   `tests/test_exporters.py`.
2. **Oder** die Erwartung umdrehen:
   `with pytest.raises(ExportError) as e: found[0].render(...)` plus
   `assert "plugin is not enabled" in str(e.value)`.

In beiden Fällen kann der `xfail`-Marker weg.

## Nicht geändert

- `register_multievent_report_exporter` gibt weiter für jeden Organizer die
  Klasse zurück. Das Gate pro Event ist die Stelle, die der Review empfiehlt,
  und sie ist die belastbarere: eine Prüfung im Empfänger („hat der Organizer
  irgendein Event mit dem Plugin?") müsste beim Bauen **jeder** Exportseite über
  alle Events laufen und würde trotzdem nichts verhindern, was das Gate nicht
  schon verhindert. Ein Organizer ohne aktives Plugin sieht den Eintrag, findet
  dort eine leere Report-Auswahl und bekommt bei einem erzwungenen Lauf einen
  `ExportError`.
- Kein Eingriff in `iterate_list` über `_prepare` hinaus. Der Skip-Mechanismus
  war schon da; der neue Fall benutzt ihn, statt einen zweiten zu bauen.

---

# Welle 2

`pretix_custom_reports/exporters.py` (eine Klasse, zwei Empfänger-Funktionen)
und `tests/test_exporters.py` (33 Tests). Contracts unangetastet, `signals.py`
nicht angefasst, kein Commit, keine fremde Datei geschrieben.

> **Hinweis zur Sitzung.** Der Lauf wurde einmal vom Spend-Limit unterbrochen,
> nachdem Code und Tests standen und grün waren, aber bevor dieser Bericht
> geschrieben war. Nachgezogen wurden anschließend nur noch dieser Bericht und
> die drei Mutationsproben unten; am Code hat sich seitdem nichts geändert.

## Erledigt

**1. `CustomReportExporter(ListExporter)`, `identifier = "customreports"`.**

Kein eigener Scheduler, keine eigene Query-Logik, keine eigene Serialisierung.
Das ist nicht Bequemlichkeit, sondern die drei Regeln 2, 5 und 6 aus
`CLAUDE.md` an einer Stelle: Zeilen kommen aus `ReportQueryCompiler`, Bytes aus
`ListExporter`, Terminierung aus `run_scheduled_exports`. Das Modul ist deshalb
im Kern eine Übersetzungsschicht — und der größte Teil des Codes behandelt
Fehler, nicht Erfolg.

**2. Formularfelder** (über `additional_form_fields`, nie `export_form_fields`
— sonst verschwindet `_format` und der `render`-Dispatch greift nicht mehr,
`docs/pretix-api-notes.md` Abschnitt 1 Fallstrick 4).

| Feld | Typ | Zweck |
| --- | --- | --- |
| `report` (`contracts.EXPORT_FORM_REPORT_KEY`) | `ChoiceField`, Choices als **Callable** | Auswahl des gespeicherten Reports über seinen `identifier` |
| `include_canceled_positions` | `ChoiceField` `""`/`yes`/`no` | Laufzeit-Überschreibung |
| `include_testmode_orders` | `ChoiceField` `""`/`yes`/`no` | Laufzeit-Überschreibung |
| `row_limit` | `IntegerField`, optional, `1..MAX_ROW_LIMIT` | Laufzeit-Überschreibung, Stellschraube gegen die 20-MB-Grenze |
| `on_unavailable` | `ChoiceField` `skip`/`fail`, **nur Multi-Event** | Verhalten, wenn ein Event den Report nicht liefern kann |

Drei Entscheidungen dahinter, die eine Begründung brauchen:

- **`ChoiceField`, kein `ModelChoiceField`.** `ExporterForm.clean` macht aus
  jeder Modellinstanz einen PK (`pretix/control/forms/orders.py:264-278`). Ein
  PK zeigt in genau ein Event und wäre in einem Multi-Event-Export nicht
  auflösbar — deshalb schreibt ADR 0001 Abschnitt 5.1 den stabilen `identifier`
  vor, und deshalb darf das Feld kein Modellfeld sein.
- **Choices als Callable.** `export_form_fields` wird für **jeden** Exporter
  gelesen, den die Exportseite aufbaut, auch für die, die niemand aufklappt. Ein
  ausgewertetes Choice-Tupel wäre eine Datenbankabfrage pro Seitenaufruf, die in
  99 % der Fälle niemand sieht.
- **Tri-State als Strings statt `NullBooleanField`.** Die Werte müssen unverändert
  durch `export_form_data`-JSON und über `Field.to_python` wieder zurück
  (`control/views/orders.py:2675-2685`), und `""` ist ein eindeutiges „nicht
  überschreiben", das ein Boolean nicht ausdrücken kann.

**3. Event- und Multi-Event-Registrierung.**

`register_report_exporter` / `register_multievent_report_exporter` sind einfache
Funktionen in `exporters.py` und geben die **Klasse** zurück, nicht eine Instanz.
Die zwei `connect()`-Zeilen für `signals.py` liegen kopierfertig in
`handoff/requests/exporter-dev-an-integrator-signals.md`.

Pro Event wird **einmal** kompiliert — ein `CompiledReport` gehört zu genau
einem Event (`handoff/status/query-dev.md`, „Nächster Schritt" 1). Kompiliert
wird für **alle** Events, bevor die erste Zeile fließt: die Kopfzeile muss
zuerst raus, und ein Fehler, der den Export abbrechen wird, soll ihn abbrechen,
bevor eine halbe Datei entstanden ist. Kompilieren baut Querysets, es führt
keine aus, das kostet also nichts.

Im Multi-Event-Fall bekommt die Ausgabe zwei führende Spalten `Event slug` /
`Event name`. Im Event-Fall **nicht** — dort hat der Nutzer die Spalten selbst
definiert und bekommt genau die.

**4. Multi-Event: ein Event, das den Report nicht liefern kann.**

Drei verschiedene Ursachen, ein Mechanismus (`_EventProblem`, modulintern):

| Ursache | Beispiel |
| --- | --- |
| Report existiert dort nicht | Report nur in Event A angelegt, Export läuft über A und B |
| Feld nicht auflösbar | `answer.tshirt-size` gibt es in B nicht (`FieldResolutionError`) |
| Report existiert, hat aber eine andere Spaltenzahl | derselbe Identifier, zwei verschiedene Reports — Identifier sind nur **pro Event** eindeutig |

Voreinstellung ist **überspringen**, protokolliert als `WARNING`, und der Rest
des Exports läuft. Scheitert **jedes** Event, gibt es einen `ExportError` mit
allen gesammelten Gründen — bewusst kein leeres Ergebnis: das wäre ein
`ExportEmptyError`, den pretix als *soft* Fehler behandelt (Mail ja,
`error_counter` nein, `services/export.py:371-374, 388-389`). Ein Zeitplan auf
einen gelöschten Report würde dann auf ewig „keine Daten" mailen, ohne die
Ursache je zu nennen.

Der dritte Fall ist der unangenehmste und deshalb der Grund für das
`on_unavailable`-Feld: ein ganzes Event stillschweigend aus einem Report zu
werfen, gegen den jemand abrechnet, ist eine eigene Art von falschem Ergebnis.
Wer das nicht will, stellt „Fail the whole export" ein. Auf Event-Ebene gibt es
die Wahl nicht — dort bleibt nach dem Überspringen nichts übrig, „skip" wäre
dort schlicht gelogen.

**5. Anbindung an Scheduled Exports.**

Nichts Eigenes gebaut. Was zu tun war, war ausschließlich: unter einem
`export_identifier` auffindbar sein und `export_form_data` als **nicht
revalidierte** Datenbankdaten behandeln (`services/export.py:366-370`). Also
wird jeder Wert daraus geprüft, bevor er benutzt wird:

- `report` → `contracts.validate_identifier` (kein String, leer, zu lang,
  falsches Zeichen → `ExportError`). Damit kann aus einem handgeschriebenen
  `export_form_data` kein Dict, keine Liste und kein Fremdformat in ein
  `.get(identifier=…)` wandern.
- `row_limit` → Typ **und** Bereich. `bool` wird explizit abgelehnt, weil `True`
  in Python ein `int` ist und ein JSON-`true` sonst zu „ein Zeile" würde.
- `include_*` → nur `""`/`yes`/`no`.
- `_format` → siehe Punkt 7.

**6. Gelöschter Report: eine Mail statt zehn Minuten Retries.**

Der Kern des Auftrags. Ohne Zutun passiert Folgendes: `DoesNotExist` läuft in
den generischen Handler (`services/export.py:392-397`), Celery versucht es
**fünf Mal im Abstand von 120 s**, danach kommt eine Mail mit dem Wort
„Internal Error", und nach fünf solchen Läufen verschwindet der Zeitplan
kommentarlos aus der periodischen Abfrage (`error_counter__lt=5`, Zeile 502).

Stattdessen: `ObjectDoesNotExist` wird an der Stelle gefangen, an der
nachgeschlagen wird, und wird zu

```
The report "codes" does not exist in event dummy. It was probably deleted or
renamed after this export was configured.
```

Sofort, ein Versuch, `error_counter == 1`, und derselbe Text steht im
Event-Log (`pretix.event.export.schedule.failed`) und in
`schedule.error_last_message`.

Analog fängt **ein** `except contracts.ContractError` die vier anderen
Fehlerarten ab (`FieldResolutionError`, `CompilationError`,
`DefinitionValidationError`, `FieldContractError`) — genau dafür existiert die
Hierarchie (ADR 0001 Abschnitt 5.2).

**7. `_format` wird selbst geprüft.**

`ListExporter.render` hat keinen `else`-Zweig (`exporter.py:328-336`): ein
unbekanntes Format liefert `None`, und der aufrufende Task macht daraus „Your
export did not contain any data." Bei einem terminierten Export, dessen
Formulardaten nie wieder validiert werden, ist das eine **falsche Diagnose** —
der Empfänger sucht nach fehlenden Bestellungen statt nach einer kaputten
Konfiguration. `render()` prüft den Wert deshalb vorher und nennt ihn beim
Namen. Der Test hält beide Verhalten nebeneinander fest
(`ListExporter.render(ex, …) is None` vs. unser `ExportError`).

**8. CSV-/XLSX-Injection: geprüft, nicht verdoppelt.**

`ListExporter` importiert `defusedcsv` statt `csv` (`exporter.py:42`) und
schreibt XLSX über `SafeWorkbook` (`exporter.py:51-53, 298, 417`). Beides deckt
Zelleninhalte vollständig ab. **Nichts nachgerüstet** — eine zweite
Escaping-Schicht würde zwei Apostrophe vor ehrliche Daten setzen. Stattdessen
zwei Tests mit `=1+cmd|" /C calc"!A0` in `order.comment`:

- CSV: der Rohwert steht **nicht** in der Datei, `'=1+cmd` schon.
- XLSX: der Wert steht verbatim in der Zelle, aber `cell.data_type == "s"`.

Der Test ist der Ersatz für den Code, den es nicht gibt: er schlägt fehl, sobald
jemand einen eigenen `csv.writer` einbaut.

Was der Schutz **nicht** abdeckt, ist der Dateiname (api-notes Abschnitt 2,
Fallstrick 3). `get_filename()` filtert deshalb selbst
(`[^A-Za-z0-9_-]` → `-`, auf 60 Zeichen gekürzt).

**9. `report.log_executed(...)` hat jetzt einen Aufrufer.**

Pro Event ein Eintrag mit `row_count`, `format`, `exporter`, `multievent`.
`permission_holder` wird auf `log_action`s `user`/`auth` aufgeteilt, inklusive
Auspacken von `UserWithStaffSession` — und mit einem `else`, das **nichts**
annimmt, weil das `export`-Management-Command dort versehentlich einen
Fortschritts-Callback hineinreicht
(`base/management/commands/export.py:108`, Signaturfehler im CLI).

Ein Fehlschlag beim Loggen bricht den Export nicht ab (`log_action` stellt auch
Notification- und Webhook-Tasks ein; ein Broker-Ausfall würde sonst jeden Export
zum Fehlschlag machen), wird aber mit `logger.exception` laut protokolliert.

## Getroffene Entscheidungen

Keine neue ADR. Alles bewegt sich innerhalb von ADR 0001 Abschnitt 5 und den
api-notes; die Begründungen stehen im Moduldocstring von `exporters.py`
(Abschnitte „Injection", „Permissions", „django-scopes", „Stored form data is
untrusted", „Failure handling"). Vier Punkte, die ich explizit **entschieden**
und nicht bloß umgesetzt habe:

1. **Überspringen als Voreinstellung im Multi-Event-Fall, mit sichtbarem
   Schalter.** Die Alternativen sind beide schlecht: hart scheitern macht einen
   Organizer-Export unbenutzbar, sobald ein einziges Event abweicht; still
   überspringen liefert stillschweigend falsche Summen. Deshalb eine
   Voreinstellung, die den Normalfall bedient, plus ein Feld, das die andere
   Lesart benennt.
2. **Spaltenzahl-Vergleich zwischen Events.** Zwei Events können unter demselben
   Identifier verschiedene Reports haben. Ohne den Vergleich entstünde eine
   Datei, in der ab Zeile *n* die Spalten verrutschen — die Fehlerart, die
   niemandem auffällt. Zählt als „Event kann den Report nicht liefern".
3. **Zwei Zusatzspalten nur im Multi-Event-Modus.** Der Kern hängt `Event slug`
   und `Event name` immer an (`orderlist.py:283`). Bei einem **selbst
   definierten** Report ist eine Spalte, die der Nutzer nicht angelegt hat,
   aber eine Überraschung. Im Multi-Event-Fall ist sie unverzichtbar, sonst sind
   Zeilen aus vier Events nicht unterscheidbar.
4. **`repeatable_read = False`.** Ein Report kann sechsstellige
   Positionszahlen selektieren; die Basisklasse rät selbst davon ab, lange
   Exporte in einer REPEATABLE-READ-Transaktion laufen zu lassen
   (`exporter.py:121-131`), und der Kern macht es beim `WaitingListExporter`
   genauso.

## Contract-Abweichungen

**KEINE.** `pretix_custom_reports/contracts/` ist unangetastet. Benutzt werden
`EXPORT_FORM_REPORT_KEY`, `DEFAULT_CHUNK_SIZE`, `MAX_ROW_LIMIT`,
`LOG_ACTION_EXECUTED` (indirekt über `log_executed`), `ContractError`,
`validate_identifier` und `ReportDefinition` (Dokument) — alle wie deklariert.

## Offene Anforderungen an andere

`handoff/requests/exporter-dev-an-integrator-signals.md` (an `integrator`):

1. **Pflicht:** zwei `connect()`-Zeilen in `signals.py`. Ohne sie ist der
   Exporter produktiv **nicht sichtbar** und kein Zeitplan anlegbar. Die Tests
   verbinden dieselben zwei Funktionen selbst, sind also grün, ohne dass es
   verdrahtet ist — das ist der Grund, warum diese Zeile hier so deutlich steht.
2. Erwartete `DeprecationWarning` beim Verbinden des
   `OrganizerPluginSignal(allow_legacy_plugins=True)`, mitsamt dem Filter, den
   pretix selbst setzt, falls die CI je `filterwarnings = error` bekommt.
3. Vollständige Liste der neuen englischen Strings für den `de`-Katalog,
   getrennt nach „UI" (`gettext_lazy`) und „Fehlermail" (`gettext`, bewusst
   nicht lazy, weil sie unter `language(schedule.locale)` gebildet werden,
   `services/export.py:323`).
4. Explizit: keine URL, keine Migration, kein `periodic_task`-Empfänger, keine
   neue Log-Action nötig.

Keine Anforderung an `query-dev`, `registry-dev` oder `persistence-dev`. Die
Schnittstellen aus Welle 1 haben unverändert gepasst — `iter_rows()` /
`headers()` und `for_event(...).by_identifier(...)` sind genau das, was der
Exporter braucht.

**Eine Entscheidung, die ich bewusst nicht getroffen habe:** ob das Plugin
`level = PLUGIN_LEVEL_EVENT_ORGANIZER_HYBRID` deklarieren sollte. Das würde die
`DeprecationWarning` beseitigen, ist aber eine Aussage über den Charakter des
Plugins (mit Folgen für `nav_organizer` und die Organizer-Vorlagen von
`portability-dev`), nicht über den Exporter. Steht als Hinweis im Request.

## Tests

```
pytest tests/test_exporters.py -q   ->  33 passed
```

| Abschnitt | Tests | Inhalt |
| --- | --- | --- |
| Sichtbarkeit | 7 | `init_event_exporters` / `init_organizer_exporters`, Permission-Filter, `_format`-Erhalt, Report-Choices (Event, Multi-Event, Permission-Grenze) |
| Ein Report läuft | 7 | CSV, XLSX, Log-Eintrag, Überschreibungen, Bereichsprüfung, unbekanntes Format, kaputte Report-Referenz |
| Multi-Event | 7 | alle Events, fehlender Report, unauflösbares Feld, `fail`-Politik, „kein Event kann liefern", abweichende Spaltenzahl, kein Skip auf Event-Ebene |
| Scheduled Exports | 5 | Lauf, **gelöschter Report**, Event-Log, Organizer-Zeitplan über zwei Events, inaktiver Owner |
| Relative Daten | 2 | direkt und über den Scheduler, je zwei eingefrorene Zeitpunkte |
| Injection | 2 | CSV (`defusedcsv`), XLSX (`SafeWorkbook`) |
| Struktur | 3 | keine Query-Logik im Modul, Empfänger geben die Klasse zurück, Identifier-Form |

Explizit die aus der Definition of Done verlangten Punkte:

- **Exporter erscheint in beiden Oberflächen** —
  `test_exporter_appears_in_the_event_export_ui` und
  `…_organizer_export_ui` rufen genau die Funktionen auf, aus denen die
  Exportseiten ihre Liste bauen (`ExportMixin.exporters`,
  `control/views/orders.py:2653-2664`). **Nicht** im Browser geprüft: dafür
  müssten die Empfänger in `signals.py` stehen, und das ist fremdes Gebiet
  (siehe Request). Das ist die einzige Lücke zwischen Test und laufendem Server,
  und sie ist genau zwei Zeilen breit.
- **Ein Report läuft durch** — `test_a_report_runs_end_to_end_as_csv` prüft
  Kopfzeile, Inhalt und dass Testmodus-Bestellungen per Voreinstellung draußen
  bleiben.
- **Terminierter Export anlegbar und ausführbar** —
  `test_a_scheduled_export_can_be_created_and_runs` legt einen
  `ScheduledEventExport` an und ruft `run_scheduled_exports(None)`, den echten
  `periodic_task`-Empfänger. Mit `CELERY_TASK_ALWAYS_EAGER` läuft der versendete
  Task inline, also gehen `EventTask` (setzt den django-scopes-Scope),
  `init_event_exporter` (prüft die Rechte des Owners) und
  `_run_scheduled_export` (mailt die Datei) wirklich durch. Geprüft wird der
  **Anhang**, nicht nur der Rückgabewert.
- **Fehlerfall gelöschter Report getestet** —
  `test_a_scheduled_export_whose_report_was_deleted_explains_itself` und
  `test_the_deleted_report_error_reaches_the_event_log`.

### Mutationsproben (die Tests behaupten nicht nur, sie messen)

Ein grüner Test beweist nichts, wenn er auch grün bliebe, ohne dass der Code da
ist. Für die beiden Punkte, an denen der Auftrag ausdrücklich einen Nachweis
verlangt, deshalb gegengeprüft: Änderung eingespielt, Test laufen lassen,
Änderung zurückgenommen.

| Mutation | Erwartung | Ergebnis |
| --- | --- | --- |
| `except ObjectDoesNotExist` → `except ZeroDivisionError` — der gelöschte Report läuft in den generischen Handler, also in fünf Retries und „Internal Error" | beide Deleted-Tests fallen | **2 failed** |
| `compile(definition, event)` → `compile(…, now=<fest>)` — relative Fenster zur Speicher- statt zur Laufzeit | beide Relative-Tests fallen | **2 failed** |

Für den Injection-Schutz gibt es keine sinnvolle Mutation in **meinem** Code,
weil dort kein Code steht: der Test ist eine Aussage über `ListExporter`, und
genau das ist sein Zweck.

### Struktureller Test statt Versprechen

`test_the_exporter_contains_no_query_logic_of_its_own` liest den eigenen
Quelltext und lehnt `eval(`, `exec(`, `.raw(`, `RawSQL`, `.extra(`, `Q(`,
`OrderPosition` und `filter(order__` ab. Das ist die prüfbare Fassung von
„Ausführung ausschließlich über den Query-Compiler": wer hier eine Abkürzung
nimmt, holt einen Teil der Allow-List aus der Registry heraus — der einzigen
Stelle, an der sie reviewt wird.

### Lint

```
flake8      pretix_custom_reports/exporters.py tests/test_exporters.py  -> rc 0
isort -c    (dieselben Dateien)                                         -> rc 0
black --check (dieselben Dateien)                        -> 2 files unchanged
```

Kein `black .` / `isort .` über das Repo.

### Gesamtsuite

`pytest tests/ -q` → **647 passed, 52 failed**. Keiner der Fehlschläge kommt aus
meinen Dateien:

- 51 × `tests/test_editor_api.py` — `NoReverseMatch` auf
  `plugins:pretix_custom_reports:api.*` und Folgefehler. `urls.py` gehört dem
  `integrator`, `frontend-dev` arbeitet in dieser Welle parallel daran.
- 1 × `tests/test_smoke.py::test_no_migration_created_yet` — das Welle-0-Gate,
  das `persistence-dev` in Welle 1 planmäßig gebrochen hat; steht seit dort in
  zwei Statusberichten.

`tests/test_exporters.py` läuft isoliert **und** in der Gesamtsuite grün; die
`registered`-Fixture trennt die Signalverbindung im Teardown wieder, damit sie
keine anderen Module beeinflusst.

## Nicht erledigt (und warum)

- **ODS gibt es nicht.** Meine Rollenbeschreibung nennt „CSV/XLSX/ODS".
  `ListExporter` in pretix 2026.6.0 kennt genau vier Formate: `xlsx` und drei
  CSV-Dialekte (`exporter.py:221-238`). ODS gäbe es nur mit einem
  handgeschriebenen Serialisierer, und das verbietet `CLAUDE.md` Regel 6 —
  aus gutem Grund, denn daran hängt der Injection-Schutz. Die Konstante
  `EXPORT_FORMATS` trägt die Begründung im Kommentar. Wenn ODS gebraucht wird,
  ist das ein Feature-Request an pretix, keine Aufgabe dieses Plugins.
- **Keine Browser-Prüfung.** Siehe oben: die Registrierung liegt in
  `signals.py`. Sobald der `integrator` die zwei Zeilen gesetzt hat, ist der
  Menüpunkt „Exports" → „Custom report" der erste Klick, der zu machen ist.
- **XLSX-Bytes werden im Test über `output_file` erzeugt.** `_render_xlsx` ohne
  `output_file` schreibt in eine `tempfile.NamedTemporaryFile` und öffnet sie
  über ihren Namen erneut (`exporter.py:322-326`); unter Windows geht das nicht
  und wirft `PermissionError`. Das ist eine Plattformgrenze von pretix, nicht
  von uns, und produktiv unter Linux irrelevant. Der `output_file`-Zweig führt
  denselben `SafeWorkbook`-Code aus, der Test bleibt also aussagekräftig. Steht
  als Docstring an `render_xlsx()` im Testmodul. **Für `test-engineer` in Welle
  3:** unter Linux zusätzlich den Byte-Pfad durchlaufen lassen.
- **`row_limit` wirkt im Multi-Event-Export pro Event, nicht insgesamt.** Das
  folgt daraus, dass `CompiledReport` pro Event gekappt wird. Eine
  Gesamtdeckelung müsste über die Events hinweg mitzählen und mittendrin
  abbrechen — dann wäre unklar, welches Event abgeschnitten wurde. Bewusst so
  gelassen, aber im `help_text` nicht erwähnt: **Kandidat für eine Ergänzung
  durch den `integrator` beim i18n-Durchgang.**
- **20-MB-Grenze nicht getestet.** Ein Testfall dafür müsste 20 MB Daten
  erzeugen; der Nutzen steht in keinem Verhältnis. Der `row_limit`-Schalter ist
  die Antwort darauf und ist getestet.
- **PostgreSQL nicht verifiziert.** Wie bei `query-dev` und `registry-dev`: die
  Testumgebung ist SQLite. Für den Exporter selbst ist das unkritisch (er baut
  kein SQL), für die Reports darunter nicht.

## Nächster Schritt

1. **`integrator`:** die zwei `connect()`-Zeilen aus
   `handoff/requests/exporter-dev-an-integrator-signals.md`. Das ist die einzige
   Blockade zwischen „Tests grün" und „im Browser benutzbar".
2. **`portability-dev` (parallel in dieser Welle):** `duplicate()` behält den
   Identifier, und genau darauf verlässt sich der Multi-Event-Export. Wenn eine
   Event-Kopie oder eine Vorlagen-Instanziierung den Identifier je ändert, ohne
   dass es eine Kollision gab, fällt ein terminierter Organizer-Export
   stillschweigend auf weniger Events zurück (er überspringt sie ja). Wert, das
   im Kopf zu behalten.
3. **`security-reviewer` (Welle 3):** die interessanteste Angriffsfläche ist
   `export_form_data`. Es wird beim Ausführen **nicht** revalidiert, kann über
   die API geschrieben werden und überlebt Monate. `_read_identifier`,
   `_read_overrides` und `render` sind die drei Türen; ein manipulierter
   Identifier darf nie zu einem organizer-übergreifenden Zugriff führen (der
   Lookup ist immer `for_event(event)`, nie global — das wäre der Test, den ich
   an eurer Stelle schreiben würde).
4. **`test-engineer` (Welle 3):** einen Report mit sechsstelliger Zeilenzahl
   durch `iterate_list` schicken und den Speicherverbrauch messen. Die
   Streaming-Kette (`iterator(chunk_size=1000)` → Generator → `csv.writer`) ist
   durchgehend faul, aber das ist bisher nur konstruiert, nicht gemessen.
   Außerdem: XLSX-Byte-Pfad unter Linux (siehe oben).

---

## Nachtrag (nach Welle 4): Fixture `registered` reparte die Signal-Verdrahtung

Vom `integrator` gemeldeter Nebenbefund, bestätigt und behoben. Nur
`tests/test_exporters.py` geändert, kein Produktivcode.

**Der Fehler.** Seit `signals.py` die beiden Receiver beim Plugin-Import
verbindet, gab es die `dispatch_uid`s `pretix_custom_reports_exporter` und
`pretix_custom_reports_multiexporter` schon, bevor ein Test lief. Die alte
Fixture verband sie erneut und trennte sie im Teardown. Beides ist im
Django-Source nachgelesen (`django/dispatch/dispatcher.py`, Django 5.2.16):

* `connect()` überspringt einen Receiver, dessen `(dispatch_uid, sender_id)`
  schon vorhanden ist (Zeile 113-117) — der `connect()` der Fixture war also
  wirkungslos.
* `disconnect(dispatch_uid=...)` matcht **allein** über diesen Schlüssel, das
  `receiver`-Argument wird ignoriert (Zeile 138-153).

Der letzte Teardown des Moduls entfernte damit die *produktive* Verbindung, für
den Rest der Pytest-Session. `signals.py` läuft einmal und verbindet sich nicht
neu. pretix' `EventPluginSignal`/`OrganizerPluginSignal` überschreiben nur
`connect`, nicht `disconnect` (`pretix/base/signals.py:261-311`) — für
Plugin-Signale gilt das also unverändert.

**Die Lösung.** Weg 2 aus dem Auftrag: idempotent verbinden, nur das eigene
Zutun zurücknehmen. Weg 1 (eigene `dispatch_uid` mit `-test`-Suffix) wäre hier
falsch gewesen: `init_event_exporters` erzeugt **pro Signal-Response eine
Exporter-Instanz** (`services/export.py:203-225`), zwei Verbindungen hätten den
Exporter in jeder Liste doppelt erscheinen lassen — eine stille
Verhaltensänderung in allen 40 bestehenden Tests.

Die Fixture prüft jetzt pro `(Signal, Receiver, dispatch_uid)`, ob die uid schon
belegt ist. Falls ja: nichts tun, aber per `assert` sicherstellen, dass die uid
wirklich an *unsere* Funktion gebunden ist — sonst hätte der wirkungslose
`connect()` das ganze Modul stillschweigend gegen einen fremden Receiver laufen
lassen. Falls nein: verbinden und nur diese Verbindung im Teardown wieder lösen.

**Zwei neue Tests** (42 statt 40 im Modul):

* `test_signals_py_connects_both_receivers_at_plugin_import` — liest das
  Modul-Snapshot `WIRING_AT_IMPORT`, das zur *Importzeit* der Testdatei
  entsteht, also vor jedem Test und jeder Fixture. Bewusst **ohne** die
  `registered`-Fixture, die eine fehlende Verbindung ja selbst herstellen und
  den Fehler damit verdecken würde. Das ist der Test, der die `dispatch_uid`s
  als Schnittstelle festnagelt.
* `test_this_module_hands_the_signal_wiring_back_untouched` — steht bewusst als
  letzter im Modul (pytest führt innerhalb einer Datei in Definitionsreihenfolge
  aus) und vergleicht gegen den Zustand, den eine modulweite Autouse-Fixture
  beim Start dieses Moduls aufgenommen hat. Absichtlich nicht gegen
  `WIRING_AT_IMPORT`: diese Datei haftet dafür, nichts zu verändern, nicht
  dafür, dass die Session heil bei ihr ankommt. Der `has_listeners()`-Teil läuft
  nur, wenn wir die produktive Verdrahtung tatsächlich vorgefunden haben.

Gegenprobe: mit dem alten, unbedingten `disconnect()` im Teardown wieder
eingesetzt schlägt der Kanarienvogel fehl (`assert after ==
wiring_before_this_module`), danach wieder entfernt.

**Offen, nicht mein Gebiet.** Dieselbe Konstruktion steht in
`tests/test_integration.py:133-146` (`test-engineer`) und
`tests/test_security.py:306-319` (`security-reviewer`) — gleiche
`dispatch_uid`s, gleiches unbedingtes `disconnect` im Teardown, gleicher latenter
Fehler. Beide Dateien laufen alphabetisch **nach** `test_exporters.py`, ihr Leck
wirkt also auf alles danach. Der Fix ist wörtlich der aus dieser Datei
übertragbar (`connected_receiver()` plus die Schleife in `registered`). Solange
das offen ist, bleibt auch der Snapshot-Workaround in `tests/test_smoke.py:38-53`
nötig; dessen Kommentar verweist noch auf diese Fixture hier und kann nach dem
Nachziehen der beiden anderen Dateien entschärft werden (`integrator`).

**Geprüft.** `pytest tests/test_exporters.py -q` → 42 passed.
`pytest tests/test_exporters.py tests/test_integration.py -q` und dieselbe
Kombination in umgekehrter Reihenfolge → je 75 passed, 2 xfailed; die umgekehrte
Reihenfolge ist der interessante Fall, weil dort das noch offene Leck aus
`test_integration.py` die produktive Verbindung vor uns entfernt und unsere
Fixture sie sauber selbst herstellt und wieder abräumt.
`pytest tests/test_exporters.py tests/test_smoke.py -q` → 64 passed.
Gesamtsuite `pytest -m "not performance" -q` → 1016 passed, 8 deselected,
8 xfailed. `flake8`/`isort -c`/`black --check` auf der Datei sauber.
