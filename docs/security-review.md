# Security-Review — Welle 3

Adversarialer Review des gesamten Plugins nach Stand Welle 2 (`ce765d5`).
Prüfgegenstand: `pretix_custom_reports/**`, alle Views, der Exporter, das
Portability-Paket, die Registry-Naht und der Query-Compiler.

Alle Befunde sind mit einem Test in `tests/test_security.py` belegt
(Stand 2026-08-03, Ende: 166 grün, kein `xfail`). Ein Befund ohne Test steht
ausschließlich im Abschnitt „Unbestätigt" und ist dort als Vermutung
gekennzeichnet.

> **Nachtrag vom 2026-08-02 — S-001 und S-002 sind behoben und verifiziert.**
> Der Orchestrator hat `persistence-dev` und `exporter-dev` vor dem Verdrahten
> von `urls.py`/`signals.py` reaktiviert (Empfehlung U-07). Beide Fixes wurden
> von mir gegengeprüft: die zwei Beweistests tragen kein `xfail` mehr, sind auf
> die Lücken erweitert, die die ursprüngliche Fassung nicht messen konnte, und
> schlagen bei künstlich entferntem Fix weiterhin genau am ursprünglichen Leck
> fehl. Details je Befund unten unter **Status**. Offen bleiben S-003 bis S-006.

> **Nachtrag vom 2026-08-03 — S-003 bis S-006 sind behoben und verifiziert;
> ein neuer Befund S-007.**
> Sechs Agents haben nach Welle 4 die vier offenen Befunde bearbeitet. Alle vier
> Fixes halten: die sechs Beweistests tragen kein `xfail` mehr, sie messen nach
> dem Umbau mehr als vorher, und bei künstlich neutralisiertem Fix fällt jeder
> wieder genau am ursprünglichen Leck (Protokoll unten, Punkt 1–3).
> Zwei Abweichungen von meinen eigenen Empfehlungen — `_identifier_taken` statt
> des Managers bei S-004, `condition_signature()` statt `str(Q)` bei S-005 —
> habe ich im echten Code nachgeprüft; beide sind richtig, die *Begründung* der
> ersten stimmt nur zur Hälfte (siehe S-004).
> Der adversariale Nachlauf hat außerdem eine vierte Fundstelle von S-003
> gefunden, die keiner der beiden zuständigen Agents angefasst hat: das
> Änderungsformular. Sie steht als eigener Befund **S-007** unten.
>
> **Nachtrag am selben Tag:** `persistence-dev` hat S-007 noch am 2026-08-03
> behoben, ich habe gegengeprüft. Damit sind **alle sieben Befunde geschlossen**
> und `tests/test_security.py` enthält keinen `xfail` mehr — zum ersten Mal seit
> Welle 3.

**Kein kritischer Befund.** Es gibt in diesem Stand keinen Pfad, auf dem

* ein ORM-Pfad, ein Lookup oder ein Operator aus einer Definition, einer
  Importdatei oder einem Editor-Request in ein Queryset gelangt,
* ein Report Daten eines fremden Events oder eines fremden Organizers liefert,
* ein Endpunkt ohne Rechteprüfung antwortet.

Was gefunden wurde, sind sieben Befunde zwischen *mittel* und *niedrig*: zweimal
ein fehlendes Plugin-Gate, einmal ein nicht encodierbarer Unicode-Wert, der drei
Endpunkte auf 500 legt und persistierbar ist (S-003) und der nach dem Fix an
einer vierten Stelle stehen geblieben ist (S-007), einmal ein IntegrityError
statt eines Formularfehlers, einmal eine Query-Amplifikation und einmal eine
Validierungsstufe, die sich per POST abschalten lässt. Sechs davon sind zum
Stand 2026-08-03 behoben und gegengeprüft.

---

## Wie die Tests gebaut sind

`tests/test_security.py` enthält **nur Angriffe**. Ein Test, der ein
*bestehendes Problem* beweist, trägt `@pytest.mark.xfail(strict=True)` mit der
Nummer des Befunds im `reason`. Damit gilt:

* solange der Fehler existiert, ist die Suite grün und der Befund dokumentiert,
* sobald jemand ihn behebt, wird der Test `XPASS` und die Suite **rot** — die
  Behebung erzwingt also, den Marker zu entfernen und damit den Befund
  abzuschließen,
* `pytest --runxfail tests/test_security.py` zeigte die echten Fehlermeldungen
  (bei Abgabe acht, nach dem Schließen von S-001 und S-002 sechs, nach dem
  Schließen von S-003 bis S-006 zwei — die Parametrisierungen von S-007 —, seit
  dem Schließen von S-007 keine mehr); jede wurde gegengelesen, damit kein Test
  aus dem falschen Grund fehlschlägt. Der Schalter ist damit vorerst wirkungslos;
  er bleibt in dieser Anleitung, weil der nächste Befund ihn wieder braucht.

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
   wie vorher. Geprüft über ein Wegwerf-Pytest-Plugin, das `dispatch`,
   `_plugin_is_active`, `payload._walk`, `_ApiView.json`, das `json`-Modul zweier
   View-Module, `clean_identifier`, `join_leaf_to_attr` bzw. `coerce_user_choice`
   zur Laufzeit neutralisiert; **kein Produktivcode wird dafür angefasst**, auch
   nicht kurzzeitig. Das Plugin liegt außerhalb des Repos und wird über
   `PYTHONPATH` und `-p` geladen.

Bei einem *neuen* Befund gilt die Umkehrung: der `xfail` bekommt seine
Fundstelle erst dann, wenn der vorgeschlagene Einzeiler — zur Laufzeit
eingespielt, wieder ohne Produktivcode anzufassen — den Test auf `XPASS` hebt.
So bewiesen für S-007, und deshalb ließ sich der Fix in derselben Sitzung
schreiben, in der der Befund entstand.

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
Status: **behoben** (portability-dev + frontend-dev, verifiziert am 2026-08-03) — siehe unten
Betroffen: `pretix_custom_reports/portability/payload.py:204` (`load_json_object`), Folgestellen `pretix_custom_reports/views/api.py:353`, `pretix_custom_reports/views/portability.py:163`, `pretix_custom_reports/views/templates.py:282`
Zuständig: portability-dev (Gate), frontend-dev (`views/api.py`)
Reproduktion: `test_a_lone_surrogate_is_refused_by_the_payload_gate`, `test_the_validate_endpoint_survives_a_lone_surrogate`, `test_the_preview_endpoint_survives_a_lone_surrogate`, `test_the_export_view_survives_a_stored_lone_surrogate` (alle vier jetzt grün, ohne Marker), dazu neu `test_an_imported_file_can_no_longer_carry_a_lone_surrogate`, `test_the_template_export_survives_a_stored_lone_surrogate`, `test_the_payload_gate_still_accepts_text_outside_the_basic_plane`, `test_the_exported_file_of_a_poisoned_report_is_refused_on_the_way_back_in`; Grenzen: `test_the_csv_path_survives_a_stored_lone_surrogate`, `test_the_xlsx_path_survives_a_stored_lone_surrogate`, `test_the_editor_page_survives_a_stored_lone_surrogate`, `test_the_pretix_event_log_survives_a_stored_lone_surrogate` (alle grün)

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

**Status — behoben, verifiziert. Mit einem Rest, der als S-007 aufgenommen und
noch am selben Tag geschlossen wurde.**
`portability-dev` hat Punkt 1 umgesetzt: `payload._walk` (`payload.py:202-216`)
versucht auf jedem String ein `encode("utf-8")` und lehnt mit dem bestehenden
`REASON_NOT_UTF8` ab, dessen Docstring in `errors.py` entsprechend erweitert
wurde. `frontend-dev` hat Punkt 2 für `views/api.py:402` erledigt,
`portability-dev` für `views/portability.py:167` und `views/templates.py:285`.
Punkt 3 (`envelope._clean_text` auf Labels) wurde bewusst nicht gemacht — das
war auch nur „optional" und hätte an Contract-Nähe gekratzt.

Drei Dinge habe ich beim Gegenprüfen ergänzt oder korrigiert:

1. **Der Gate-Test prüft jetzt den Grund, nicht nur die Ablehnung.** Die alte
   Fassung war `pytest.raises((PayloadRejected, DefinitionValidationError))`.
   Das wäre auch von der Tiefen-, Knoten- oder Größenprüfung erfüllt worden — ein
   Fix an der falschen Stelle der Datei hätte den Test grün gemacht. Er verlangt
   jetzt `excinfo.value.reason == REASON_NOT_UTF8`, und das an vier Stellen:
   Label, tief verschachtelter Filterwert, Low-Surrogate `\udc00` und
   **Objektschlüssel**. Der Schlüsselfall hängt daran, dass `_walk` die Keys mit
   auf den Stack legt; eine Fassung, die nur Werte abläuft, hätte drei von vier
   bestanden.
2. **Die drei Endpunkt-Tests messen mehr als „200".** `assert status_code == 200`
   allein ist kein Nachweis: ein Fix, der das Zeichen *entfernt* oder ersetzt,
   erfüllt ihn genauso und schreibt dabei stillschweigend das Label des Nutzers
   um. Jeder der drei prüft jetzt dreiteilig — der Body ist reines ASCII
   (`.decode("ascii")`), er enthält die Escape-Sequenz `\ud800`, und
   `json.loads` gibt exakt den geposteten bzw. gespeicherten String zurück. Beim
   Vorschau-Endpunkt kommt die Zeilenzusicherung dazu, sonst wäre eine Antwort
   „200 mit leerem Ergebnis" ununterscheidbar von einem Erfolg.
3. **Die Gegenrichtung war ungeprüft und ist es wert.** `"\ud83d\ude00"` ist ein
   *Paar*, das `json.loads` zu einem Zeichen zusammenfaltet. Ein Gate, das die
   Escape-Sequenz textuell vor dem Parsen abgelehnt hätte, hätte damit jedes
   Emoji und jedes seltenere CJK-Zeichen in einem Label verboten. Die Prüfung
   sitzt an der richtigen Stelle (am geparsten String), und
   `test_the_payload_gate_still_accepts_text_outside_the_basic_plane` hält sie
   dort fest.

Gegenprobe wie bei S-001/S-002, in drei Läufen: mit neutralisiertem `_walk`
speichert der Import wieder (`assert 302 == 200`) und der Gate-Test meldet
„DID NOT RAISE"; mit auf `ensure_ascii=False` zurückgedrehtem `_ApiView.json`
sterben Validate und Vorschau wieder mit `UnicodeEncodeError`; mit
zurückgedrehtem `json`-Modul in `views/portability.py` und `views/templates.py`
ebenso beide Exportansichten. Jeweils exakt am ursprünglichen Leck.

**Vier Nachbarpfade neu abgedeckt** — drei davon halten, einer nicht:

| Pfad | Ergebnis |
| --- | --- |
| Vorlagen-Export (`views/templates.py:285`) | hält — der Befund nannte die Zeile, gemessen hatte sie niemand |
| XLSX-Export (`SafeWorkbook`/openpyxl) | hält — „CSV ist unauffällig" sagt darüber nichts, es ist ein völlig anderer Serialisierer, **und** es ist der unbeaufsichtigte Pfad |
| pretix' eigene Event-Log-Seite | hält — `ReportDefinition.log_data()` legt die **ganze** Definition in jeden `LogEntry`; pretix rendert bei unbekanntem Action-Type nur den Typ, nicht die Daten. Wäre das anders, hätte ein Label eine Kernseite des Events zerlegt |
| Änderungsformular (`forms.py:65`) | **hielt nicht** → S-007, inzwischen ebenfalls behoben |

Der XLSX- und der Log-Test sind Stolperdrähte, keine Befunde: sie kosten nichts
und stehen zwischen `log_data()` bzw. dem Exporter und der nächsten Person, die
dort etwas umstellt.

---

### S-004 Doppelter Identifier wird ein IntegrityError statt eines Formularfehlers
Schweregrad: niedrig
Status: **behoben** (persistence-dev, verifiziert am 2026-08-03) — siehe unten
Betroffen: `pretix_custom_reports/forms.py:103` (`fields` führt `identifier`, aber nicht `event`), `pretix_custom_reports/models.py:280` (`UniqueConstraint(["event", "identifier"])`)
Zuständig: persistence-dev
Reproduktion: `test_a_duplicate_identifier_is_a_form_error_not_a_500` (jetzt grün, ohne Marker, über beide Eigentümer parametrisiert), Kontrollgruppen `test_a_report_may_keep_its_own_identifier_when_it_is_changed`, `test_the_same_identifier_may_be_used_again_in_another_event`, `test_the_duplicate_check_survives_without_an_active_scope`

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

**Status — behoben, verifiziert. Der Fix ist besser als meine Empfehlung, die
Begründung dafür stimmt zur Hälfte.**
`persistence-dev` hat `clean_identifier` ergänzt (`forms.py:131-166`), aber
**nicht** über den Manager, sondern über `self.instance._identifier_taken(value)`
(`models.py:403-418`) — eine Methode, die es seit Welle 1 gibt und die
`_generate_identifier` schon benutzt. Sie läuft unter `scopes_disabled()` und
filtert hart auf `event_id` bzw. `organizer_id` plus `event__isnull=True`,
spiegelt also beide `UniqueConstraint`s genau, und schließt die eigene Zeile aus.
Das ist die bessere Variante, aus zwei Gründen: sie deckt den Vorlagenzweig
ohne zweiten Code-Pfad ab (der `event`/`organizer`-XOR steckt schon in der
Methode), und sie hängt nicht vom Scope ab.

Die *Begründung* im Statusbericht — die Empfehlung wäre „mit `ScopeError`
geplatzt" — habe ich im Code nachgeprüft und sie trägt nur teilweise. Richtig
ist: `ReportDefinition.objects` ist scope-gebunden (`ReportDefinitionManager`
bildet `ScopedManager` nach und liefert ohne `organizer`-Scope ein
`DisabledQuerySet`, `models.py:132-136`). Falsch ist der Schluss für den
Ansichtenpfad: `pretix/control/middleware.py:199` legt **jeden** Control-Request
in `scope(organizer=request.organizer)`, der Manager hätte dort also
funktioniert. Der `ScopeError` wäre nur außerhalb eines Requests gekommen — im
Direkttest des Formulars, in einem Task, in der Event-Kopie. Der Fix ist damit
richtig gewählt und aus dem falschen Grund verteidigt; das ist dieselbe Sorte
Begründungsfehler wie die MRO-Aussage bei S-001, und es ist derselbe Grund, ihn
hier festzuhalten: damit er nicht als Regel weitergereicht wird.
`test_the_duplicate_check_survives_without_an_active_scope` nagelt die
tatsächlich gültige, stärkere Eigenschaft fest — das Formular validiert ganz
ohne Scope und lehnt den Duplikat-Identifier trotzdem ab.

**Der Beweistest musste umgebaut werden.** Die Welle-3-Fassung prüfte `200` und
`b"identifier" in response.content`. Beides ist für **jedes** neu gerenderte
Formular wahr, weil „identifier" der Name eines Feldes auf der Seite ist — der
Test wäre nach dem Fix grün geworden, ohne die Prüfung je gemessen zu haben, und
wäre auch grün geblieben, wenn das Formular aus einem völlig anderen Grund
abgelehnt hätte. Er prüft jetzt: Statuscode 200, die Meldung des neuen
`clean_identifier` im Body, und **die Zeilenzahl vorher gleich nachher**.

**Drei Facetten neu abgedeckt:**

1. **Der Vorlagenzweig.** `ReportDefinitionForm` bedient Event-Report und
   Organizer-Vorlage aus einer Klasse, die zweite Bedingung ist
   `(organizer, identifier)`. Im Befund stand nur die Event-Hälfte; getestet war
   die andere nie. Der Test ist jetzt über beide Eigentümer parametrisiert, und
   in der Gegenprobe fällt er auch auf beiden — mit den zwei verschiedenen
   `UNIQUE constraint failed`-Meldungen.
2. **Die Prüfung darf nicht zu eng sein.** Ohne `.exclude(pk=...)` scheitert
   jedes Speichern eines bestehenden Reports am eigenen Identifier, und weil der
   Editor ihn als verstecktes Feld zurückpostet, wäre das der Normalfall, nicht
   der Randfall.
3. **Und nicht zu weit.** Die Eindeutigkeit ist `(event, identifier)`, und ein
   Identifier überlebt eine Event-Kopie absichtlich (ADR 0001 Abschnitt 5) —
   zwei Events mit demselben sind der *Normalzustand* nach einer Kopie. Ein
   global fragendes `clean_identifier` hätte Facette 1 bestanden und die
   Event-Kopie still zerlegt.

Gegenprobe: mit zur Laufzeit auf eine reine Durchreiche gesetztem
`clean_identifier` (Produktivcode unangetastet) endet der POST wieder in
`django.db.utils.IntegrityError: UNIQUE constraint failed: ...event_id,
...identifier` — dem Fehler, der wörtlich im Befund steht — und der Formulartest
fällt an derselben Zeile.

---

### S-005 Eine Vorschau mit vielen `join`-Spalten kostet eine Query pro Spalte
Schweregrad: niedrig
Status: **behoben** (query-dev, verifiziert am 2026-08-03) — siehe unten
Betroffen: `pretix_custom_reports/query/plan.py:400` (`_dedupe_prefetches`), `pretix_custom_reports/query/relations.py:563` (`to_attr=leaf_to_attr`)
Zuständig: query-dev
Reproduktion: `test_a_report_full_of_join_columns_costs_what_one_column_costs` (bis 2026-08-03 `test_a_report_full_of_join_columns_costs_one_query_per_column`, siehe Status), Nachbarpfade `test_join_columns_that_want_different_rows_are_still_kept_apart`, `test_the_residual_cost_of_join_columns_is_bounded_by_distinct_conditions`, `test_the_condition_signature_refuses_to_merge_what_it_cannot_read`

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

**Status — behoben, verifiziert. Zwei Abweichungen von meiner Formulierung,
beide zum Besseren.**
`query-dev` hat die zweite, richtige Variante gewählt:
`relations.join_leaf_to_attr()` (`relations.py:687-724`) leitet den `to_attr` aus
der *Identität des Blatt-Querysets* ab — Leaf-Modell, Lookup, Canceled-Regel,
innerer `select_related`, Bedingungssignatur — statt aus dem Spaltenindex, und
`plan._dedupe_prefetches` greift damit endlich. Gemessen: 1, 2, 20 und 200
(`MAX_COLUMNS`) identische `join`-Spalten kosten **dieselben zwei Queries**.

Die beiden Abweichungen habe ich einzeln nachgeprüft, weil sie meine eigene
Empfehlung korrigieren:

1. **`condition_signature()` statt `str(Q)`.** Meine Fassung stand da mit „dem
   üblichen Vorbehalt"; `query-dev` hat den Vorbehalt ernst genommen. `str(Q)`
   rendert eine Modellinstanz über deren `__str__`, zwei `Question`-Zeilen mit
   gleichem Label sähen also gleich aus — und ein daraufhin verschmolzener
   Prefetch legt die Antworten der einen Frage unter die Überschrift der
   anderen. Falsche Ausgabe, kein Fehler, kein Logeintrag. Signiert werden
   deshalb nur Skalare und Listen/Mengen davon, alles andere ergibt `None` und
   fällt auf den alten, spaltenweisen Namen zurück. Das ist die richtige
   Fehlerrichtung: offen scheitern kostet eine Query, geschlossen scheitern
   kostet Korrektheit. `test_the_condition_signature_refuses_to_merge_what_it_cannot_read`
   nagelt genau diese Richtung fest, damit eine spätere „Optimierung" die
   Signatur nicht still auf Modellinstanzen ausweitet.
2. **Der innere `select_related` gehört zur Identität.** Ohne ihn wäre die
   Ersparnis erkauft: `item.name` braucht `select_related("item")` auf den
   vorgeladenen Positionen, `position.attendee_name` nicht, und ein für den
   zweiten Fall gebauter gemeinsamer Prefetch macht aus dem ersten ein N+1. Das
   ist eine Verschärfung gegenüber „gleicher Lookup und gleiche Bedingung" und
   sachlich richtig.

Eine mögliche Kollision der Signatur habe ich durchgespielt und **nicht**
gefunden: die Grammatik ist `(CONNECTOR:key=repr,...)`, `repr` eines Strings ist
ein einzelnes Literal in Anführungszeichen, ein Lookup-Key enthält kein `=`, und
ein verschachtelter Knoten beginnt mit `(` oder `NOT(`, womit kein Wert-`repr`
beginnen kann. Zwei verschiedene Bedingungen können also nicht denselben Text
erzeugen. Die Restunsicherheit steht unter U-09.

**Mein eigener Test war rot und ist umgedreht.** Er hieß
`test_a_report_full_of_join_columns_costs_one_query_per_column` und verlangte
`many - few >= 15`, maß nach dem Fix aber `(2, 2)` — er war der Beweis des
*Defekts*, kein `xfail`, und musste deshalb ohnehin von mir angefasst werden. Er
heißt jetzt `test_a_report_full_of_join_columns_costs_what_one_column_costs`,
misst gegen `count_for(1)` und geht bis `MAX_COLUMNS` hinauf — also bis zu der
Zahl, die der Strukturvalidator wirklich zulässt und die der Befund zitiert hat
(„~200 Round Trips"). Der alte Name steht im Testdocstring, damit die Historie
auffindbar bleibt.

Gegenprobe: mit `join_leaf_to_attr` zur Laufzeit auf `None` gezwungen (also mit
dem alten Rückfall auf den spaltenweisen Namen) misst der Test wieder
`assert 3 == 2` — eine zusätzliche Query pro zusätzlicher Spalte, exakt die
Verstärkung des Befunds.

**Zwei Facetten neu abgedeckt.**
`test_join_columns_that_want_different_rows_are_still_kept_apart` greift die
andere Seite an: zwei `join`-Spalten über `answer.<identifier>` kreuzen dieselbe
Relation und unterscheiden sich nur in der Blattbedingung. Verschmölzen sie,
wäre das ein *Datenleck zwischen zwei Spalten desselben Reports*. Gemessen:
vier Queries (Zeilen, gemeinsame Zwischenebene, zwei Blätter) und jede Spalte
liefert ihren eigenen Wert. `query-dev` hat denselben Sachverhalt aus eigener
Sicht in `tests/test_query_compile.py`; doppelt ist hier richtig, weil die
Konsequenz eine Sicherheitsaussage ist und keine Performancezahl.

**Restrisiko, benannt statt verschwiegen.** Die Verstärkung ist für *identische*
Spalten weg, nicht im Prinzip: zehn `join`-Spalten über zehn verschiedene Fragen
sind zehn verschiedene Prefetches und kosten zehn Queries
(`test_the_residual_cost_of_join_columns_is_bounded_by_distinct_conditions`,
grün, misst `distinct + 2`). Was sich geändert hat, ist der Eintrittspreis: es
braucht jetzt N verschiedene Fragen im Event — angelegt mit
`event.can_change_items`, nicht mit dem `event.orders:read`, das die Vorschau
verlangt — wo vorher ein einziges Feld, 200-mal wiederholt, genügte. Damit ist
U-03 (kein Rate-Limit) entschärft, nicht erledigt.

---

### S-006 Die Importansicht lässt sich per POST auf die Event-Kopie-Strategie stellen
Schweregrad: niedrig
Status: **behoben** (portability-dev, verifiziert am 2026-08-03) — siehe unten
Betroffen: `pretix_custom_reports/views/portability.py:237`, `pretix_custom_reports/views/templates.py:367`, `pretix_custom_reports/portability/resolution.py:769`
Zuständig: portability-dev
Reproduktion: `test_the_import_view_cannot_be_talked_into_the_event_copy_strategy` (jetzt grün, ohne Marker), neu `test_the_template_apply_view_cannot_be_talked_into_the_event_copy_strategy`, `test_no_posted_value_whatsoever_yields_the_event_copy_strategy`, `test_no_view_hands_a_request_value_to_the_wide_coercion`; Kontrollgruppen `test_the_same_definition_is_refused_under_the_offered_strategies`, `test_the_event_copy_can_still_ask_for_keep`

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

**Status — behoben, verifiziert.**
`portability-dev` hat die Empfehlung wörtlich umgesetzt:
`ResolutionStrategy.coerce_user_choice` (`resolution.py:132-142`) prüft gegen
die neue Konstante `USER_CHOICES = ("abort", "skip")` und fällt auf `ABORT`
zurück; `coerce()` bleibt unverändert und behält alle drei, mit einem Docstring,
der sagt, wer welche benutzen darf. Beide Views sind umgestellt
(`views/portability.py:245`, `views/templates.py:372`), `eventcopy.py:130` ruft
weiter `KEEP` über `coerce()`.

**Der Beweistest musste geschärft werden.** Die Welle-3-Fassung prüfte
`status_code in (200, 302)` und „nichts gespeichert". „Nichts gespeichert" trägt
den Befund, aber allein hielte es auch, wenn der Import aus einem ganz anderen
Grund gescheitert wäre. Der Test liest die *effektiv verwendete* Strategie jetzt
über `response.context["plan"].strategy` zurück und verlangt `ABORT` — die
Ansicht muss zurückgefallen sein, nicht bloß abgelehnt haben. Die Kontrollgruppe
prüft spiegelbildlich, dass `abort` und `skip` als das ankommen, was gepostet
wurde; sonst wäre ein `coerce_user_choice`, das *immer* `ABORT` liefert, von
einem richtigen nicht zu unterscheiden.

**Drei Facetten neu abgedeckt:**

1. **Die zweite Ansicht.** `views/templates.py` liest dasselbe POST-Feld für
   „Organizer-Vorlage in dieses Event laden" — dieselbe Klasse von Tor (eine
   Definition, die von außerhalb dieses Events kommt), im Befund nur als
   Fundstelle genannt, nie gemessen.
   `test_the_template_apply_view_cannot_be_talked_into_the_event_copy_strategy`
   holt das nach, und in der Gegenprobe fällt sie genauso wie die Importansicht.
2. **Die Coerce-Funktion selbst, über die Formen, die ein POST-Feld annehmen
   kann.** `request.POST.get()` liefert `str` oder `None`, eine `QueryDict` lässt
   sich zu einer Liste überreden — der Typenzoo ist klein und ist jetzt
   vollständig aufgezählt. `" keep"`, `"keep "` und `"keep\x00"` stehen bewusst
   drin: „erst trimmen, dann vergleichen" ist der naheliegende nächste Refactor
   und würde das Loch wieder öffnen.
3. **Die Regel statt der zwei Stellen.** Eine dritte Ansicht, die morgen
   `strategy` aus einem Request liest und `coerce` aufruft, würde den Befund
   wieder öffnen, ohne einen der Tests oben rot zu machen.
   `test_no_view_hands_a_request_value_to_the_wide_coercion` verbietet deshalb
   die *Form*: über den Syntaxbaum jedes Moduls in `views/` darf kein
   `…coerce(...)`-Aufruf ein Argument haben, in dem `request` vorkommt.

Gegenprobe: mit `coerce_user_choice = coerce` zur Laufzeit antworten beide
Ansichten wieder mit `302` (der Report ist gespeichert, es wird auf ihn
weitergeleitet) und die Coerce-Parametrisierung meldet
`assert 'keep' == 'abort'`.

**Kontrollgruppe für die andere Richtung.** `test_the_event_copy_can_still_ask_for_keep`
hält fest, dass `coerce("keep")` weiterhin `KEEP` ergibt und `KEEP` nicht in
`USER_CHOICES` steht. Die Trennung verläuft zwischen den beiden Funktionen, nicht
zwischen den drei Strategien — eine Event-Kopie, die Spalten verlöre statt sie
unaufgelöst mitzunehmen, wäre ein anderer und schlimmerer Fehler.

---

### S-007 Das Änderungsformular ist die vierte Fundstelle von S-003 — und die einzige gebliebene
Schweregrad: niedrig
Status: gefunden am 2026-08-03 beim Gegenprüfen von S-003, **behoben** (persistence-dev, verifiziert am selben Tag) — siehe unten
Betroffen: `pretix_custom_reports/forms.py:65` (`PrettyJSONFormField.prepare_value`)
Zuständig: persistence-dev
Reproduktion: `test_the_change_form_survives_a_stored_lone_surrogate` (jetzt grün, ohne Marker, über beide Eigentümer parametrisiert); Nachbarpfade `test_the_change_form_is_the_only_way_a_surrogate_still_gets_stored`, `test_a_poisoned_report_is_still_repairable_through_the_editor`, `test_the_editor_page_survives_a_stored_lone_surrogate` (alle grün)

Auswirkung:
Die Behebung von S-003 hat drei Leser auf `ensure_ascii=True` umgestellt —
`views/api.py:402`, `views/portability.py:167`, `views/templates.py:285`. Einen
vierten nicht. Das Änderungsformular rendert die gespeicherte Definition über
`PrettyJSONFormField.prepare_value` in seine Textarea, und diese Methode ruft
weiter `json.dumps(value, indent=2, ensure_ascii=False, cls=self.encoder)`. Der
entstehende `str` stirbt in `django/http/response.py:324` mit demselben
`UnicodeEncodeError` wie die anderen drei vorher. Gemessen an beiden Stellen,
weil `ReportDefinitionForm` Event-Report und Organizer-Vorlage aus einer Klasse
bedient:

| Endpunkt | Ergebnis |
| --- | --- |
| `GET .../reports/<pk>/edit/` | `UnicodeEncodeError` → 500 |
| `GET .../customreports/templates/<pk>/edit/` | dasselbe |

Zwei Dinge halten den Schweregrad auf *niedrig*, und beide sind gemessen:

1. **Der Weg hinein ist nur noch der Selbstschaden.** Das Payload-Gate hat den
   Import dichtgemacht; wer heute ein Surrogat in die Datenbank bekommen will,
   muss es selbst durch das JSON-Textfeld desselben Formulars posten
   (`test_the_change_form_is_the_only_way_a_surrogate_still_gets_stored` — grün,
   dokumentiert einen akzeptierten Schreibvorgang). Das braucht
   `event.settings.general:write`. Bestandsdaten aus der Zeit vor dem Gate sind
   der andere, seltenere Fall.
2. **Der Report bleibt reparierbar.** Die grafische Oberfläche rendert
   (`escapejson_dumps` ist `json.dumps` mit dem voreingestellten
   `ensure_ascii=True`, `pretix/base/templatetags/escapejson.py:44`) und speichert
   per POST, der `prepare_value` nie erreicht.
   `test_a_poisoned_report_is_still_repairable_through_the_editor` fährt den
   ganzen Weg: Editor öffnen, sauber speichern, Änderungsformular wieder
   aufrufbar. Wird dieser Test je rot, ist S-007 nicht mehr *niedrig* — dann
   wäre ein Report speicherbar und über keine Oberfläche mehr zu entfernen.

Warum es trotzdem ein eigener Befund ist und keine Fußnote: das
Änderungsformular ist der Reparaturpfad, den die Einschätzung von S-003
ausdrücklich als mildernden Umstand geführt hat („der Report bleibt exportierbar
und reparierbar"). Genau dieser Pfad ist der einzige, der nicht mitgezogen wurde.
Und es ist ein Beispiel für die Sorte Fix, die man für vollständig hält, weil
alle *genannten* Zeilen angefasst sind: der Befund hat drei Fundstellen
aufgezählt, `forms.py` stand nicht darunter, also hat niemand gesucht.

Empfehlung:
`ensure_ascii=True` in `PrettyJSONFormField.prepare_value`. Ein Zeichen mehr im
Quelltext; die Textarea zeigt dann `\u00fc` statt `ü`, was für ein Feld, in dem
ohnehin roher JSON steht, vertretbar ist — und `\ud800` statt eines Absturzes.
Nachgewiesen: mit genau dieser Änderung zur Laufzeit geht der `xfail`-Test in
beiden Parametrisierungen auf `XPASS(strict)`.

Wer eleganter will: die Definition weiterhin mit `ensure_ascii=False` rendern und
nur bei `UnicodeEncodeError` auf `True` zurückfallen. Das erhält die Lesbarkeit
für die 99,99 % der Reports, die kein Surrogat enthalten. Meine Empfehlung ist
trotzdem die einfache Variante — zwei Rendering-Pfade für ein Textfeld sind mehr
Fläche, als die Lesbarkeit wert ist.

Nicht empfohlen: das Surrogat beim Schreiben abzuweisen (also `clean_definition`
oder `contracts.validate_definition` zu erweitern). Die Contracts sind
eingefroren, und ein Schreib-Gate hilft den bereits gespeicherten Zeilen nicht.
Falls jemand es *zusätzlich* tut, wird
`test_the_change_form_is_the_only_way_a_surrogate_still_gets_stored` rot; dann
ist zu entscheiden, ob für dieses Formular die Quarantäne von S-003 („nichts
speichern, was sich nicht encodieren lässt") oder die Toleranz von S-007
(„rendern, was gespeichert ist") die Regel ist. Beide vertragen sich heute nur
deshalb, weil die zweite bedingungslos gilt.

**Status — behoben, verifiziert.**
`persistence-dev` hat die einfache Variante genommen: `ensure_ascii=True` in
`PrettyJSONFormField.prepare_value` (`forms.py:75`), plus einen Kommentar, der
sagt, warum das keine Kosmetik ist und nicht „für hübschere Umlaute"
zurückgedreht werden darf. Die von mir als Alternative genannte Rückfalllösung
(erst `False`, bei `UnicodeEncodeError` auf `True`) wurde richtigerweise nicht
gebaut — zwei Rendering-Pfade für ein Textfeld sind mehr Fläche, als die
Lesbarkeit wert ist.

**Der Beweistest ist ausgebaut worden, nach demselben Maßstab wie die drei
Endpunkte von S-003.** Die Fassung, mit der ich den Befund gemeldet habe, prüfte
`status_code == 200`. Das ist beim Schließen zu wenig, und zwar hier noch
deutlicher als bei den drei Endpunkten: das Änderungsformular **schreibt
zurück, was es anzeigt**. Ein „Fix", der das Zeichen beim Rendern verwirft,
hätte den Test erfüllt und beim nächsten Speichern die Definition des Nutzers
still umgeschrieben — aus einem 500 wäre lautloser Datenverlust geworden. Der
Test schneidet die Textarea deshalb jetzt aus der Seite, macht das
HTML-Escaping rückgängig und verlangt, dass der geparste Inhalt zeichengenau
dem entspricht, was in der Datenbank steht. Dazu, wie bei den anderen dreien,
die Escape-Sequenz `\ud800` im Body.

Gegenprobe: mit `prepare_value` zur Laufzeit auf `ensure_ascii=False`
zurückgedreht (Produktivcode unangetastet) fallen beide Parametrisierungen
wieder mit `UnicodeEncodeError` in `django/http/response.py:324` — an denselben
Byte-Positionen wie vor dem Fix (33438 für den Event-Report, 27064 für die
Vorlage). Genau das ursprüngliche Leck, nicht eine Nebenwirkung.

**Zwei Nachbartests wurden nachgezogen**, weil sie den Befund als *offen*
beschrieben: `test_the_editor_page_survives_a_stored_lone_surrogate` (die
Editor-Seite war die Referenz, gegen die der Fix gemessen wurde — beide rendern
jetzt gleich) und `test_a_poisoned_report_is_still_repairable_through_the_editor`
(trug das Argument für den Schweregrad; das ist verbraucht, der Weg
Editor → Speichern → erneut öffnen bleibt aber der einzige durchgehende in
diesem Modul, und seine letzte Zusicherung ist genau die, die S-007 gefunden
hätte).

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
* *Ergänzt 2026-08-03:* ein nicht encodierbares Label (S-003) bringt weder den
  XLSX-Export (`SafeWorkbook`/openpyxl — der **unbeaufsichtigte** Pfad) noch
  pretix' eigene Event-Log-Seite um, obwohl `ReportDefinition.log_data()` die
  ganze Definition in jeden `LogEntry` legt. Beide sind als Stolperdraht
  festgehalten.

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
* *Ergänzt 2026-08-03:* 1, 2, 20 und 200 identische `join`-Spalten kosten
  dieselbe Zahl Queries (S-005 behoben); verschiedene Bedingungen kosten
  weiterhin je eine, gemessen und als Restrisiko benannt.

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
*Nachtrag 2026-08-03:* das ist **nicht** passiert. Die neue Prüfung in
`payload._walk` versucht `value.encode("utf-8")`, und `"\x00".encode("utf-8")`
gelingt — das Nullbyte kommt also weiterhin durch das Gate. Der Vorbehalt gilt
unverändert weiter, jetzt nur ohne die Aussicht, nebenbei miterledigt zu werden.

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
*Nachtrag 2026-08-03:* mit dem S-005-Fix ist der Multiplikator weg, den ein
Angreifer ohne Vorbereitung erreichen konnte. Was bleibt, ist eine Query pro
*verschiedener* `join`-Bedingung; die Kombination ist damit entschärft, nicht
erledigt.

**U-04 — `MAX_ROW_LIMIT = 1 000 000` im interaktiven Export.**
Die Streamingkette ist durchgehend faul, aber eine Million Zeilen × 200 Spalten
ist trotzdem eine lange Transaktion und eine große Datei. `exporter-dev` hat den
Speicherverbrauch bewusst nicht gemessen; `test-engineer` hat es auf dem Plan.
Kein eigener Test von mir, weil er nur die Laufzeit der Suite kostet.

**U-05 — PostgreSQL insgesamt nicht verifiziert.**
Gilt wie bei `registry-dev`, `query-dev` und `exporter-dev`. Für den Review
konkret betroffen: U-01, `nulls_last`, und die Frage, ob eine der
Aggregat-Subqueries dort einen anderen Ausgabetyp liefert.

**U-08 — Wettlauf zwischen zwei gleichzeitigen Speichervorgängen (S-004).**
`clean_identifier` prüft und `save()` schreibt; zwischen beiden liegt ein
Zeitfenster. Zwei parallele Requests mit demselben Identifier können also
weiterhin in dem `IntegrityError` enden, den S-004 beschreibt. Das ist ein
klassisches TOCTOU und mit einer Formularprüfung grundsätzlich nicht zu
schließen — die Datenbankbedingung ist die einzige Instanz, die es kann. Kein
Test, weil ein verlässlicher Wettlauf in dieser Suite (SQLite, ein Prozess) nicht
zu bauen ist. Wer es sauber will, fängt den `IntegrityError` in `form_valid` und
übersetzt ihn in denselben Formularfehler; die Wahrscheinlichkeit rechtfertigt
den Aufwand aus meiner Sicht nicht.

**U-09 — `condition_signature()` und Werte, deren `repr` nicht eindeutig ist.**
Die Signatur benutzt `repr` für `datetime`, `Decimal`, `UUID` und Freunde.
`repr(datetime)` enthält die `tzinfo` über deren eigenes `repr`, und zwei
verschiedene `tzinfo`-Objekte können sich denselben Text teilen; ein `float`
`nan` signiert wie jedes andere `nan`. Beides würde zwei Prefetches verschmelzen,
die es nicht sollten. Nicht erreichbar aus dem heutigen Code — die Bedingungen
kommen aus `registry/hints.py` und sind per Contract JSON-sichere Primitive, in
der Praxis Strings und Integer — und deshalb kein Befund und kein Test. Steht
hier, falls `hints.py` je Datumsbedingungen erzeugt.

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
behoben.
*Erledigt, zweiter Teil:* `urls.py` und `signals.py` sind seit Welle 4 (`d7c6842`)
verdrahtet, und S-003 bis S-007 sind am 2026-08-03 im verdrahteten Stand behoben
worden. Damit ist U-07 abgeschlossen — und der Absatz gilt für keinen Befund mehr:
alles, was hier steht, betraf am Ende Code, der produktiv erreichbar ist. Die
Einschränkung „ändert die Dringlichkeit, nicht den Befund" hat sich als die
richtige Lesart erwiesen; abgearbeitet wurden am Ende alle sieben.

---

## Zusammenfassung nach Zuständigkeit

Stand 2026-08-03, Ende des Tages. „behoben" heißt: Fix gelesen, Beweistest ohne
`xfail` grün, Test misst nach dem Umbau noch das, was er behauptet, und bei
künstlich neutralisiertem Fix fällt derselbe Test wieder am ursprünglichen Leck.

| Befund | Schweregrad | Zuständig | Status |
| --- | --- | --- | --- |
| S-001 CRUD-Views ohne Plugin-Gate | mittel | persistence-dev | **behoben** (2026-08-02) |
| S-002 Organizer-Export ohne Plugin-Gate | mittel | exporter-dev | **behoben** (2026-08-02) |
| S-003 Ungepaarte Surrogate | mittel | portability-dev (Gate), frontend-dev (`views/api.py`) | **behoben** (2026-08-03) |
| S-004 Doppelter Identifier → IntegrityError | niedrig | persistence-dev | **behoben** (2026-08-03) |
| S-005 Query-Amplifikation bei `join`-Spalten | niedrig | query-dev | **behoben** (2026-08-03) |
| S-006 `keep`-Strategie per POST erreichbar | niedrig | portability-dev | **behoben** (2026-08-03) |
| S-007 Änderungsformular rendert mit `ensure_ascii=False` | niedrig | persistence-dev | **behoben** (2026-08-03) |

| Agent | Offene Befunde |
| --- | --- |
| persistence-dev | keine — S-001, S-004 und S-007 geschlossen |
| portability-dev | keine — S-003 (Gate) und S-006 geschlossen |
| frontend-dev | keine — S-003, Teil 2 geschlossen |
| query-dev | keine — S-005 geschlossen, mit benanntem Restrisiko |
| exporter-dev | keine — S-002 geschlossen |
| contract-architect | keine — die Contracts haben gehalten, durch beide Nachbesserungsrunden |
| registry-dev | keine — die Event-Bindung der Annotationen ist die stärkste Naht im Plugin |

**Alle sieben Befunde sind geschlossen.** Was von mir bleibt, sind die neun
Punkte unter „Unbestätigt" — Vermutungen, keine Befunde, jede einzeln
begründet, warum sie ohne Test dasteht. Vier davon (U-01, U-04, U-05, U-09)
warten auf eine Umgebung oder einen Code, den es hier nicht gibt; die übrigen
sind bewusste Entscheidungen.

Ein Satz zur Selbsteinschätzung, weil eine Liste ohne offene Punkte leicht wie
ein Freibrief aussieht: geprüft ist, was in „Geprüft und in Ordnung" steht, auf
SQLite, gegen pretix v2026.6.0. Nicht geprüft sind PostgreSQL (U-05), das
Verhalten unter Last (U-04) und alles, was nach diesem Commit dazukommt.

## Ausführen

```
pytest tests/test_security.py -q          # 166 passed, 0 xfailed
```

Zum ersten Mal seit Welle 3 trägt kein Test in diesem Modul einen
`xfail(strict=True)`. Das ist der Zustand, den das Verfahren anstrebt, und
gleichzeitig der, in dem es am wenigsten aussagt: ein grünes Modul beweist nur,
dass die *gefundenen* Angriffe abgewehrt werden. Wer den nächsten Befund
einträgt, findet die Regeln oben unter „Wie die Tests gebaut sind" — inklusive
der Umkehrung für neue Funde, mit der S-007 belegt und noch am selben Tag
geschlossen wurde.

Volle Suite zum Stand dieses Nachtrags:

```
pytest -m "not performance" -q            # 1171 passed, 10 deselected, 2 xfailed
```

Die verbleibenden zwei `xfail` gehören nicht hierher: es sind T-004 und T-005 in
`tests/test_integration.py` (`test-engineer`). Die Zahl der grünen Tests wandert,
solange andere Agents parallel schreiben; entscheidend ist, dass aus
`tests/test_security.py` kein `xfail` und kein `failed` mehr darunter ist.
