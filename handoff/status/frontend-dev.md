# Status: frontend-dev — Welle 1

**Erledigt:**

- **`views/api.py`** — vier JSON-Endpunkte plus zwei Welle-1-Mock-Endpunkte, alle
  über `EventPermissionRequiredMixin` (`event.orders:read`) und den neuen
  `PluginActiveMixin` abgesichert, alle POST-Endpunkte CSRF-geschützt (kein
  `csrf_exempt` im Modul):
  - `GET api/fields/` — komplette Feldbibliothek für **beide** Basen in einem
    Request: pro Feld Label, Gruppe, Datentyp, Choices, `value_scope`,
    `provider` und je Basis `available` / `sortable` / `requires_aggregate` /
    erlaubte Operatoren / erlaubte Aggregate. Dazu alle Enums mit Labels
    (Operatoren mit `value_kind`, Aggregate, Datums-/Zahl-/Boolean-Formate,
    Sortierrichtungen, UND/ODER) und alle Grenzwerte aus `contracts`.
  - `POST api/validate/` — Strukturvalidierung, gibt die **kanonische** Form
    zurück (das ist die Serverseite des Roundtrips) plus Registry-Warnungen pro
    Dokumentpfad (`columns[3]`, `sorting[0]`, …).
  - `POST api/preview/` — Spalten, Zeilen als Anzeige-Strings, geschätzte
    Gesamtzahl, `truncated`-Flag und das serverseitig gerenderte
    Tabellenfragment. Limit hart auf `contracts.PREVIEW_ROW_LIMIT` geklemmt.
  - `GET api/examples/`, `GET api/examples/<slug>/` — die Golden Fixtures als
    Beispiel-Reports (Welle 1, siehe unten).
- **`views/editor.py`** — die Editor-Seite: erweitert
  `pretixcontrol/event/base.html`, liefert Konfiguration und Übersetzungen als
  `<script type="application/json">`, lädt zwei statische JS-Dateien und ein
  CSS. Routen (`editor.new`, `editor.edit`) liegen als `editor_urlpatterns` im
  Modul.
- **Templates** — `editor.html` (Bootstrap-3-Panels des Cores, keine eigene
  Grid-/Button-Schicht) und `preview_table.html`.
- **`static/pretix_custom_reports/js/report-editor-model.js`** — Zustand ↔
  Dokument, DOM-frei, unter node testbar. Enthält Laden, kanonisches
  Serialisieren, alle strukturellen Mutationen, `baseImpact()`/`applyBase()` und
  `localIssues()`.
- **`static/pretix_custom_reports/js/report-editor.js`** — die UI:
  - **Linke Spalte**: Feldbibliothek gruppiert (Reihenfolge aus dem Server),
    Suche über Label/Key/Gruppe/Hilfetext, Umschalter „auch Felder der anderen
    Basis zeigen", Badges für „braucht Aggregat", „eventspezifische Werte" und
    Fremdplugin. Drag & Drop über Sortable.js, dazu Klick-Buttons für
    Spalte/Filter/Sortierung (Drag allein ist nicht bedienbar per Tastatur).
  - **Rechte Spalte**: Spaltenliste per Drag & Drop **und** Hoch/Runter-Knöpfen,
    Anzeigename überschreibbar, Aggregat-Auswahl (nur erlaubte Werte, Pflicht
    ohne Leer-Option wenn die Basis es verlangt), Format-Auswahl passend zum
    Datentyp, Separator-Feld nur bei `join`, Sichtbarkeit umschaltbar, Entfernen.
  - **Filterbereich**: UND/ODER auf Wurzelebene, eine Gruppenebene, **ein Widget
    je Datentyp** — Details in `docs/adr/0005-editor.md` Abschnitt 5. Datumsfeld
    bekommt Datumsauswahl **und** die sechs relativen Operatoren, Choice-Feld
    eine Mehrfachauswahl, Boolean eine Ja/Nein-Auswahl, `between` zwei
    typisierte Felder, `relative_last_days` ein Tagesfeld mit Einheit, Listen
    ohne Choices einen Token-Editor. Freitext bleibt auf Text/E-Mail/URL/Telefon
    beschränkt.
  - **Sortierung**: geordnete Liste mit Stufennummer, umsortierbar per Drag &
    Drop und per Knopf, nur als `sortable` markierte Felder in der Auswahl,
    Limit `MAX_SORT_ENTRIES` sichtbar.
  - **Optionen**: beide Schalter plus Zeilenlimit; „abgesagte Positionen" wird
    auf Basis `order` deaktiviert und erklärt.
  - **Live-Vorschau**: entprellt (600 ms), abschaltbar, mit „X von ~Y Zeilen —
    Vorschau auf 20 Zeilen begrenzt". Läuft nicht los, solange der Editor selbst
    ein blockierendes Problem sieht.
  - **Basisumschalter**: zeigt **vor** dem Wechsel, welche Spalten, Filter und
    Sortierstufen wegfallen, welche ein Aggregat bekommen und welche ihres
    verlieren; erst danach „Trotzdem wechseln".
  - **JSON-Panel**: kanonisches JSON zum Kopieren, „JSON anwenden" zum Einfügen.
  - Fehler- und Warnungsanzeige getrennt: Serverfehler mit Dokumentpfad,
    Registry-Warnungen als Warnung, lokale Probleme als Hinweis mit Markierung
    der betroffenen Zeile.
- **`tests/test_editor_api.py`** — 90 Tests: Seite, Rechte (positiv/negativ/ohne
  Login/fremdes Event), CSRF (ohne Token 403, mit Token 200), erlaubte
  HTTP-Methoden, kaputte Request-Bodies, Feldbibliothek gegen
  `required_field_keys` aus `_index.json`, Vorschau-Limit, Formatierung,
  versteckte Spalten, Escaping, kaputtes Feld, Pfad-Traversal beim
  Beispiel-Endpunkt, Roundtrip aller zehn Fixtures über `api/validate/` **und**
  über die echte Modell-Datei im node-Subprozess.
- **`docs/adr/0005-editor.md`** — sechs Entscheidungen mit Begründung.
- **`handoff/requests/frontend-dev-an-integrator-urls.md`** — kopierfertige
  URL-Zeilen.

**Nicht erledigt (und warum):**

- **Speichern.** `views/crud.py` und die Routen dafür gehören
  `persistence-dev`/`integrator`. Das Formular ist fertig und postet genau die
  Felder, die `ReportDefinitionForm` erwartet (`name`, `description`, `base`,
  `definition` als JSON-String); der Knopf ist deaktiviert, solange
  `save_url` `None` ist. Das ist eine Zeile in Welle 2, in *meinem* Bereich.
- **Import/Export-Buttons im Editor** — `portability-dev`, Welle 2. Das
  JSON-Panel deckt Copy-Paste bis dahin ab.
- **Kein Browser-Klicktest.** Ich habe die gerenderte Seite, alle Endpunkte und
  die Modell-Datei automatisiert geprüft, aber niemand hat den Editor mit der
  Maus bedient. Drag & Drop, select2 und die Datumsfelder sind der Teil, der
  einen menschlichen Blick braucht — bitte in Welle 2 einplanen.
- **Choice-Werte in der Vorschau** stehen roh da (`n` statt `pending`), weil der
  Compiler den Rohwert rendert und die Vorschau nicht schöner sein darf als der
  Export. Offene Frage, siehe unten.

**Getroffene Entscheidungen:** alle in `docs/adr/0005-editor.md`. Die vier, die
andere unmittelbar betreffen:

1. **Zwei Austauschpunkte statt zweier Betriebsarten** (ADR 0005 Abschnitt 2):
   `get_registry()` und `get_compiler()` in `views/api.py`. Bereits verifiziert:
   mit der echten Registry (`registry.library.field_registry`) und dem echten
   Compiler (`query.compiler.ReportQueryCompiler`) antworten Feldbibliothek (83
   Felder) und Vorschau **ohne eine Änderung** an Template, CSS oder
   JavaScript. Welle 2 ist damit ein Sechs-Zeilen-Diff plus das Entfernen des
   Mock-Abschnitts.
2. **Die Zustand-↔-JSON-Abbildung liegt in `report-editor-model.js`** und wird
   im node-Subprozess über alle Fixtures getestet, inklusive
   Schlüsselreihenfolge. Die Operator-Tabelle wird nicht nach JavaScript
   gespiegelt; ein Test verbietet es.
3. **Vorschau serverseitig gerendert** (HTML-Fragment + Zeilen aus derselben
   Struktur), Limit geklemmt statt validiert, Formatierung Vorschau-lokal und
   idempotent (Strings werden durchgereicht).
4. **`PluginActiveMixin`**: Control-URLs eines Plugins sind bei pretix auch in
   Events erreichbar, in denen das Plugin aus ist — `require_plugin` hängt nur an
   den Presale-URLs (`multidomain/plugin_handler.py`). SPEC.md F1 will das
   Gegenteil, deshalb 404. Empfehlung an `integrator`: `views/placeholder.py`
   ebenso, und `persistence-dev` für die CRUD-Views.

**Contract-Abweichungen:** KEINE. `contracts/` und
`tests/fixtures/definitions/` sind unangetastet; kein Eintrag in
`handoff/blockers.md` nötig. Drei Contract-Details, die ich bewusst *benutzt*
statt umgangen habe:

- `PREVIEW_ROW_LIMIT` ist die einzige Quelle des Vorschau-Limits, auch im
  Browser (kommt über `limits.preview_rows`).
- `iter_field_references()` ist die Naht für die Registry-Warnungen — dieselbe
  Prüfliste wie im Compiler, nur mit Pfad statt Ausnahme, damit der Editor das
  richtige Widget markieren kann.
- Ein leerer Wurzel-Filter wird beim Serialisieren zu „kein `filters`-Schlüssel",
  eine leere Untergruppe verworfen. Genau das macht `validate_definition` beim
  Normalisieren auch, deshalb ist der Roundtrip stabil.

**Offene Anforderungen an andere:**

- `handoff/requests/frontend-dev-an-integrator-urls.md` — sieben Routen (zwei
  Zeilen `urls.py`), Hinweis auf die zwei Mock-Routen die in Welle 2 wieder
  rausgehen, i18n-Hinweis (kein `djangojs`-Lauf nötig), und eine bewusst
  markierte Abweichung: meine Editor-Route benutzt den stabilen `identifier`,
  die CRUD-Routen von `persistence-dev` den Primärschlüssel. Wenn das
  einheitlich sein soll, bitte ansagen — Kosten: zwei Zeilen bei mir.
- An `query-dev` und `exporter-dev`, nicht blockierend, kein eigenes
  Request-File weil es eine Frage und keine Änderung ist: **Werden Choice-Werte
  im Export als Rohwert (`n`) oder als Label (`pending`) ausgegeben?** Heute
  Rohwert, und die Vorschau zeigt es genauso, damit sie nicht lügt. Wenn das
  Label gewünscht ist, gehört die Abbildung in *eine* Stelle (Renderer im
  Compiler oder im Exporter), nicht in die Vorschau. Gleiches gilt für
  `ColumnFormat.separator` bei `join`, den heute der Compiler anwendet.
- An `integrator` (Welle 4): `tests/test_smoke.py::test_no_migration_created_yet`
  ist rot, weil `persistence-dev` `0001_initial.py` angelegt hat. Ersatz steht in
  `handoff/requests/persistence-dev-an-integrator-urls.md` Abschnitt 2. Nicht
  meine Datei, nicht mein Fehler, aber es ist der einzige rote Test im Repo.

**Tests:** 90 passed, 0 failed (`pytest tests/test_editor_api.py -q`, 5 s, davon
4 node-Tests). Gesamtes Repo: **411 passed, 1 failed** — der eine Fehler ist
`test_smoke.py::test_no_migration_created_yet` und gehört nicht zu meinem
Bereich (siehe oben). `flake8`, `isort -c` und `black --check` sauber über
`views/api.py`, `views/editor.py` und `tests/test_editor_api.py`; kein
repo-weiter Formatierlauf, kein `git commit`.

Definition of Done geprüft:

| Kriterium | Nachweis |
|---|---|
| Editor lädt | `test_editor_page_loads`, dazu manuell gegen die Testdatenbank gerendert (50 KB HTML, Status 200) |
| jede Golden Fixture lässt sich laden | `test_editor_page_opens_every_golden_fixture` (10×), `test_example_endpoint_serves_the_fixture_verbatim` (10×) |
| bearbeiten und identisch wieder ausgeben | `test_js_model_round_trips_every_golden_fixture` (10×, echte JS-Datei unter node, Spalte hinzufügen → verschieben → zurück → entfernen, danach Vergleich mit `as_dict()` **und** `as_json()`), `test_validate_round_trips_every_fixture` (10×) |
| Vorschau funktioniert | `test_preview_runs_for_every_fixture` (10×), `test_preview_never_exceeds_the_row_limit`, `test_preview_applies_the_column_format` |
| Statusbericht | diese Datei |

**Nächster Schritt (Welle 2, in dieser Reihenfolge):**

1. `get_registry()`/`get_compiler()` auf Registry und Compiler umstellen, Testlauf
   ohne UI-Änderung erwarten. Neu abzudecken: Felder, die es für ein konkretes
   Event nicht gibt (Fragen, Meta) — der Warn-Pfad steht schon, braucht aber
   Tests mit echten `Question`-Objekten (Fixtures von `test-engineer`).
2. Mock-Abschnitt in `views/api.py` und die beiden `api/examples/`-Routen
   entfernen, `load_definition()` auf `ReportDefinition` umstellen,
   `ctx["save_url"]` setzen.
3. Import/Export- und „Vorlage laden"-Knöpfe an die Views von `portability-dev`
   hängen.
4. Einen Menschen den Editor mit der Maus bedienen lassen: Drag & Drop zwischen
   Bibliothek und Spaltenliste, select2 in den Filterzeilen, Datumsfelder,
   Basiswechsel mit vielen betroffenen Feldern.

---

# Status: frontend-dev — Welle 2

Alle vier Schritte aus dem Plan oben erledigt. Schritte 1, 2 und 4 im ersten
Durchgang, Schritt 3 (Import/Export- und Vorlagen-Knöpfe) nachgereicht, sobald
`portability-dev` fertig war.

## Schritt 1 — Registry und Compiler sind echt

`views/api.py`, die zwei Austauschpunkte aus ADR 0005 Abschnitt 2:

```python
def get_registry():
    from ..registry.library import field_registry
    return field_registry()

def get_compiler():
    from ..query.compiler import ReportQueryCompiler
    return ReportQueryCompiler(get_registry())
```

Beide bleiben Funktionen (der Import bleibt lazy, Tests behalten eine Naht). Die
Vorschau kompiliert jetzt mit `compile(definition, event, preview=True)` — das
ist kein Kosmetikflag: **ohne** es steht das `LIMIT` nicht in der SQL, und die
Datenbank materialisiert das volle Ergebnis, bevor Python 20 Zeilen abschneidet
(`query/report.py::build_report`). Belegt durch
`test_preview_slices_in_sql_not_in_python`, der das erzeugte SQL liest.

Die Behauptung aus Welle 1 („Sechs-Zeilen-Diff, keine Änderung an Template, CSS
oder JavaScript") hat **fast** gehalten. Eine Stelle musste doch nach:

**Ein Feld-Key, zwei `ReportField`-Objekte.** Die echte Registry baut je Basis
ein eigenes Objekt. Bei einer Choice-Frage heißt das: auf `orderposition` trägt
sie ihre Optionen und `value_scope=event`, auf `order` ist sie ein Aggregat
**ohne** Optionen und mit `value_scope=global` (`registry/questions.py`). Die
Feldbibliothek nahm „das erste Objekt, das ich finde", und das ist `order`.
Ergebnis: eine Choice-Frage hätte im Filterbereich ein **Freitextfeld**
bekommen statt einer Mehrfachauswahl — genau das Gegenteil von SPEC.md F6. Der
Stub aus Welle 0c konnte das nicht zeigen, er hat pro Key genau ein Objekt.

Behoben in `FieldLibraryView` (drei kleine Helfer, im Code begründet):

- `_sample()` beschreibt ein Feld aus der Variante, die **ohne Aggregat** auf
  ihrer Basis steht — das ist die mit Label, Hilfetext und Optionen.
- `_choices_across_bases()` nimmt die Optionen von der Variante, die welche hat.
  Welche Werte ein Feld annehmen kann, hängt nicht von der Report-Basis ab.
- `_value_scope()` antwortet `event`, sobald **eine** Variante das sagt. Die
  strengere Antwort muss gewinnen, sonst importiert man später stillschweigend
  Verweise auf die Objekte eines fremden Events.

Test: `test_choice_question_offers_its_options`. Kein Contract berührt, keine
Zeile JavaScript geändert.

## Schritt 2 — Mocks raus, echtes Speichern dran

- **Mock-Abschnitt gelöscht** (`MockDefinitionListView`, `MockDefinitionView`,
  `mock_available()`, Fixture-Loader) samt der beiden `api/examples/`-Routen.
  `handoff/requests/frontend-dev-an-integrator-urls.md` ist entsprechend
  korrigiert — die zwei Zeilen dürfen **nicht** nach `urls.py`, sie erzeugten
  dort einen `ImportError` beim Serverstart. Wächter:
  `test_no_stub_is_left_in_the_editor_views` prüft über den AST, dass keine der
  beiden View-Dateien `contracts.stubs` importiert.
- **`load_definition()`** liest aus `event.custom_reports.by_identifier(...)`.
  Damit sind ein Report eines fremden Events und eine Organizer-Vorlage
  strukturell unerreichbar (beide 404, je ein Test), nicht durch einen Filter,
  den jemand vergessen kann. Die Definition wird beim Öffnen **nicht** erneut
  validiert: ein alter Report, dessen Struktur nicht mehr durchgeht, muss sich
  öffnen lassen, sonst kann ihn niemand reparieren. Was daran falsch ist, sagt
  `api/validate/`.
- **`ctx["save_url"]`** zeigt auf `event.reports.add` bzw. `event.reports.edit`
  (PK-basiert, `views/crud.py`). Zwei Fälle geben `None` und lassen den Knopf
  deaktiviert: Nutzer ohne `event.settings.general:write`, und eine `urls.py`
  ohne die CRUD-Routen (`NoReverseMatch` wird gefangen — der Editor muss auch
  bei unvollständiger `urls.py` benutzbar bleiben, nur eben ohne Speichern).

**Dabei gefunden, und das wäre teuer geworden:** `ReportDefinitionForm` führt
`identifier` als optionales Feld. Wird es nicht mitgepostet, ist es nach dem
Cleanen leer, und `ReportDefinition.save()` erzeugt dann einen **neuen**
Identifier. Der Editor hätte also bei jedem Speichern den stabilen Identifier
gewechselt — und damit jeden Scheduled Export gebrochen, der den Report darüber
referenziert (ADR 0001 Abschnitt 5). Der Editor postet ihn jetzt als verstecktes
Feld zurück. `test_editor_posts_the_stable_identifier_back` prüft beides: mit
Feld bleibt er, **ohne** Feld wechselt er wirklich (Gegenprobe, damit die
Zusicherung nicht aus Versehen grün ist). Für alle, die dasselbe Formular
benutzen (`portability-dev`, Vorlagen, Import), steht der Hinweis im Request an
den `integrator`.

**Zur Identifier-vs-PK-Frage** (mein offener Punkt aus Welle 1): unverändert.
Die Editor-Route benutzt weiter den stabilen `identifier`, die CRUD-Routen den
Primärschlüssel; beide sind jetzt sauber miteinander verlinkt. Der Wechsel
bleibt für mich zwei Zeilen, ich mache ihn nicht stillschweigend.

## Schritt 3 — Import, Export und Vorlagen im Editor

Drei Links im JSON-Panel, genau dort, wo `portability-dev` sie in Abschnitt 6
seines Requests vorgeschlagen hat:

| Knopf | Route | sichtbar wenn |
|---|---|---|
| „Export as a file" | `event.reports.export` (`report=<pk>`) | Report ist gespeichert; braucht nur `event.orders:read` |
| „Import from a file" | `event.reports.import` | `event.settings.general:write` |
| „Load a template" | `event.reports.templates` | `event.settings.general:write` |

Drei Entscheidungen dahinter, alle im Code begründet:

1. **Export nur für einen gespeicherten Report.** Die URL wird aus dem
   Primärschlüssel gebaut, den ein ungespeicherter Entwurf nicht hat. Statt
   eines toten Knopfes steht dort „Save this report to be able to export it as
   a file."
2. **Export und Import sind unterschiedlich berechtigt.** `ReportExportView`
   hängt an `event.orders:read` wie der Editor selbst, `ReportImportView` und
   `TemplatePickView` an `event.settings.general:write`. Ein Nur-Lese-Nutzer
   sieht deshalb den Export-Knopf und **keinen** Import-Knopf — ein Link in ein
   403 wäre schlechter als kein Link.
   Test: `test_read_only_user_may_export_but_not_import`, der beide Richtungen
   prüft (Knopf da/weg **und** View 200/403).
3. **Alle drei verlassen den Editor, keiner kennt den ungespeicherten Stand.**
   Import und Vorlage ersetzen ihn, und die Exportdatei wird aus der
   Datenbankzeile gebaut — wer gerade zehn Spalten hinzugefügt hat, bekäme
   stillschweigend die alte Fassung als Datei. `report-editor.js` merkt sich
   deshalb beim Laden die kanonische JSON-Fassung (`savedJson`), vergleicht bei
   jedem Klick auf `a[data-pcr-leave]` und fragt nur dann nach — mit **zwei**
   Texten: „Änderungen gehen verloren" für Import/Vorlage, „die Datei enthält
   die gespeicherte Fassung" für den Export. Nach dem Absenden des
   Speichern-Formulars wird `savedJson` nachgezogen, damit die Frage nicht
   ausgerechnet beim Speichern kommt.
   Browser-Test: `test_browser_asks_before_leaving_with_unsaved_changes` — ohne
   Änderung folgt der Link **ohne** Dialog, nach einer Änderung kommt genau ein
   Dialog, „Abbrechen" bleibt auf der Seite und die Änderung ist noch da, und
   der Export-Dialog trägt den anderen Text.

Wie bei `save_url` läuft jeder `reverse()` über `url_or_none()` mit
`try/except NoReverseMatch`. Die Routen kommen aus drei verschiedenen
Handoff-Requests und landen nicht in einem Commit; eine halb verdrahtete
`urls.py` darf einen Knopf kosten, nicht die Seite.
`test_editor_survives_missing_portability_routes` sperrt genau das ab.

`views/portability.py` und `views/templates.py` blieben unangetastet — ich
importiere nur ihre URL-Namenskonstanten (`URL_NAME_EXPORT`, `URL_NAME_IMPORT`,
`URL_NAME_EVENT_PICK`), damit ein umbenannter Name hier einen ImportError gibt
und keinen stillen toten Knopf.

## Schritt 4 — Der Editor in einem echten Browser

Kein einmaliger Klicktest, sondern sechs automatisierte Browser-Tests am Ende von
`tests/test_editor_api.py`. Werkzeug: **playwright** (war im venv, nichts
nachinstalliert) auf dem **Edge, der auf der Maschine liegt**
(`chromium.launch(channel="msedge")`, also auch kein Browser-Download). Server
ist `live_server` aus pytest-django mit denselben eingehängten Routen wie die
übrigen Tests; kein Dev-Server, den jemand starten muss. Ohne Browser
**skippen** die Tests, sie fallen nicht um.

Der Browser hat drei Dinge gefunden, die kein Python-Test finden konnte:

1. **select2 hat jede Auswahl verschluckt. Der schwerste Fund.**
   select2 meldet eine Auswahl mit jQuerys `.trigger("change")`, und jQuery ruft
   nur eigene Handler auf — ein mit `addEventListener` registrierter Listener
   sieht davon **nichts**. Der Editor benutzt durchgehend `addEventListener`.
   Konkret: die Feldauswahl in jeder Filterzeile und in jeder Sortierstufe hat
   mehr als acht Optionen, wird also immer von select2 übernommen. Wer dort ein
   Feld auswählte, sah es ausgewählt — im Report kam es **nie** an. Gemessen:
   `select.value` korrekt, `change`-Events: 0.
   Behoben in `enhanceSelect()`: eine Brücke von `select2:select`/`unselect`/
   `clear` auf ein natives `change`-Event (bewusst nicht von `change` selbst,
   das wäre eine Endlosschleife). Danach: 1 Event, die Auswahl kommt an.
   Test: `test_browser_select2_enhances_the_field_chooser`.

2. **In die leere Spaltenliste konnte man nichts fallen lassen.** Das Drop-Ziel
   war ein leeres `<tbody>` — null Pixel hoch, also nicht treffbar. Drag & Drop
   war genau dann unmöglich, wenn es der einzige Weg hinein ist.
   Behoben: `renderColumns()` legt bei leerer Liste eine Platzhalterzeile an
   (`.pcr-drop-hint`, gestrichelter Rahmen, ~56 px), Sortable bekommt
   `filter: ".pcr-drop-hint"`, damit sie Ziel und nie Ware ist. Neuer Text
   `drop_here` in `js_strings()`.
   Test: `test_browser_drag_and_drop_adds_a_column` (zwei Felder nacheinander,
   das zweite landet **neben** dem ersten, nicht statt seiner).

3. **Die Aktionsknöpfe an den Feldern sind bis zum Hover unsichtbar**
   (`visibility: hidden`, `:hover`/`:focus-within`). Das ist so gewollt und
   funktioniert auch per Tastatur, ist aber der Grund, warum ein Test ohne
   `hover()` ewig wartet — steht als Kommentar am Helfer, damit es niemand für
   einen Fehler hält.

Abgedeckt sind damit die vier verlangten Punkte: Drag & Drop zwischen Bibliothek
und Spaltenliste, ein select2-Feld, Datumsfelder (`datetime-local` plus die
sechs relativen Operatoren; Umschalten auf `relative_last_days` gibt ein
Tagesfeld) und ein Basiswechsel mit vielen betroffenen Feldern
(`orderposition_basic` → `order`: erst die Liste dessen, was wegfällt, dann
„Trotzdem wechseln"). Dazu die Live-Vorschau mit echten Zeilen aus der
Testdatenbank. Jeder Test prüft zusätzlich, dass **keine** JS-Exception
aufgetreten ist (`page.on("pageerror")`).

Eine Eigenheit der Testumgebung, kein Produktfehler: `pretix.testutils.settings`
schaltet die django-compressor-Precompiler ab, das SCSS des Control-Panels wird
also als SCSS ausgeliefert und vom Browser ignoriert. Ohne Bootstrap-Grid
stapeln sich die beiden Editor-Spalten, und das Drop-Ziel rutscht aus einem
720-px-Viewport heraus — ein Ziel außerhalb des Viewports bekommt **nie** ein
`dragover`. Deshalb hat der Browser-Kontext ein Viewport von 1400×2400. Das
steht so im Code, weil es sonst beim nächsten Mal wieder eine Stunde kostet.

## Nicht erledigt (und warum)

- **`event.reports.templates.apply` verlinke ich bewusst nicht.** Der Editor
  führt auf die Auswahlseite (`event.reports.templates`); welche Vorlage
  angewendet wird, entscheidet die Seite von `portability-dev`, die auch die
  Namensauflösung anzeigt. Ein Direktlink aus dem Editor würde diese Ansicht
  überspringen.
- **Choice-Werte in der Vorschau** stehen weiter roh da (`n` statt `pending`).
  Unverändert die offene Frage aus Welle 1 an `query-dev`/`exporter-dev`: wenn
  Labels gewünscht sind, gehört die Abbildung in **eine** Stelle (Compiler oder
  Exporter), nicht in die Vorschau — die darf nicht schöner sein als der Export.
- **PostgreSQL** nicht gegengeprüft (SQLite-Testumgebung), gleiche Einschränkung
  wie bei `registry-dev` und `query-dev`.

## Contract-Abweichungen

**KEINE.** `contracts/` und `tests/fixtures/definitions/` sind unangetastet,
kein Eintrag in `handoff/blockers.md` nötig. Der Contract hat in Welle 2
gehalten: für den Tausch von Registry und Compiler musste die UI nicht angefasst
werden. Die eine Server-Änderung (ein Key, zwei Feldvarianten) ist keine
Contract-Lücke, sondern eine Eigenschaft der echten Registry, die der Stub nicht
hatte.

## Tests

`pytest tests/test_editor_api.py -q` → **112 passed, 0 failed** (~26 s, davon
6 Browser- und 4 node-Tests), auch mit zufälliger Reihenfolge.

Neu gegenüber Welle 1, jeweils erst mit echter Registry möglich:

| Was | Test |
|---|---|
| Feldbibliothek kommt aus `EventFieldRegistry`, > 80 Felder | `test_field_library_is_served_from_the_real_registry` |
| Choice-Frage liefert ihre `QuestionOption`s | `test_choice_question_offers_its_options` |
| Frage eines anderen Events taucht **nicht** auf | `test_question_fields_are_event_specific` |
| Frage umbenannt → Key wandert, alter Report warnt am richtigen Pfad | `test_renaming_a_question_moves_its_key` |
| `meta.event.campaign` existiert nur mit `EventMetaProperty` | `test_meta_property_field_only_exists_when_the_organizer_defines_it` |
| Event ohne die Fragen: öffnen geht mit Warnungen pro Pfad, ausführen nennt alle fehlenden Keys | `test_a_report_using_fields_this_event_does_not_have_is_reported_not_hidden`, `test_preview_of_a_report_with_missing_fields_names_all_of_them` |
| Vorschau filtert, sortiert und formatiert wirklich | `test_preview_applies_the_filters`, `..._the_sorting`, `..._the_column_format` |
| Vorschau schneidet in SQL, nicht in Python | `test_preview_slices_in_sql_not_in_python` |
| Vorschau zeigt nur Daten dieses Events | `test_preview_shows_only_this_events_orders` |
| Voller Roundtrip Editor-Formular → CreateView → DB → Editor, alle 10 Fixtures | `test_full_round_trip_editor_form_to_database_and_back` |
| Identifier überlebt das Speichern (mit Gegenprobe) | `test_editor_posts_the_stable_identifier_back` |
| Nur-Lese-Nutzer: Editor ja, Speichern nein | `test_read_only_user_gets_the_editor_without_a_save_target` |

Aus Schritt 3:

| Was | Test |
|---|---|
| Gespeicherter Report zeigt alle drei Knöpfe, richtig markiert | `test_stored_report_offers_export_import_and_templates` |
| Neuer Report: kein Export, dafür der Hinweis „erst speichern" | `test_a_new_report_cannot_be_exported_yet` |
| Nur-Lese-Nutzer: Export ja, Import/Vorlagen nein — Knopf **und** View | `test_read_only_user_may_export_but_not_import` |
| Fehlende Portability-Routen kosten einen Knopf, nicht die Seite | `test_editor_survives_missing_portability_routes` |
| Der Export-Link liefert wirklich eine importierbare Datei | `test_export_link_serves_the_stored_definition` (gegen `validate_portable_document`) |
| Nachfrage nur bei ungespeicherten Änderungen, mit zwei Texten | `test_browser_asks_before_leaving_with_unsaved_changes` |

Die Testdaten baut das Modul selbst: drei Fragen, eine Meta-Property, ein
Beispiel-Plugin über den `__mocked_app`-Haken von pretix und vier Bestellungen
mit acht Positionen — genau das Event, das
`tests/fixtures/definitions/_index.json` verspricht.

Gesamtsuite `pytest tests/ -q`: **837 passed, 1 failed**. Der eine Fehlschlag ist
weiterhin `tests/test_smoke.py::test_no_migration_created_yet` (Welle-0-Gate,
gehört dem `integrator`, Ersatz liegt im Handoff von `persistence-dev`).

`flake8`, `isort -c` und `black --check` über `views/api.py`, `views/editor.py`
und `tests/test_editor_api.py`: grün. `node --check` über beide JS-Dateien:
grün. Kein repo-weiter Formatierlauf, kein `git commit`.

## Nächster Schritt

1. **integrator (Welle 4):** fünf Editor-Routen aus
   `handoff/requests/frontend-dev-an-integrator-urls.md` (nicht sieben, die zwei
   Beispiel-Routen sind weg). Der Editor verlinkt jetzt außerdem auf drei
   Routen von `persistence-dev`/`portability-dev` — fehlen sie in `urls.py`,
   fehlen nur die jeweiligen Knöpfe, die Seite läuft. Vollständig ist der Editor
   erst mit `event.reports.add`, `event.reports.edit`, `event.reports.export`,
   `event.reports.import` und `event.reports.templates`.
2. **integrator (Welle 4), Übersetzungen:** in `views/editor.py` sind drei neue
   `js_strings`-Einträge dazugekommen (`drop_here`, `leave_unsaved`,
   `leave_unsaved_export`), in `editor.html` die Beschriftungen und Hilfetexte
   der drei Knöpfe. Weiterhin kein `djangojs`-Lauf nötig.
3. **test-engineer / security-reviewer (Welle 3):** die Browser-Tests laufen nur
   mit Edge oder Chrome auf der Maschine, sonst skippen sie — auf einem CI ohne
   Browser fällt die Abdeckung von Drag & Drop, select2 und der
   Verlassen-Nachfrage also **still** weg. Wer sie dort haben will, braucht
   `playwright install chromium` im CI-Image; das ist eine
   Umgebungsentscheidung und deshalb nicht von mir getroffen.

---

# Nachtrag: Nacharbeitsrunde S-003 und T-001

Zwei kleine, unabhängige Änderungen, beide ausschließlich in
`pretix_custom_reports/views/api.py` und `tests/test_editor_api.py`. Keine
Template-, JS- oder CSS-Zeile angefasst — die UI ist von beidem nicht betroffen.

## S-003: die JSON-Antworten sind jetzt reines ASCII

`_ApiView.json` serialisierte mit `ensure_ascii=False`. Ein ungepaartes
Surrogat (`"\ud800"`) ist syntaktisch gültiges JSON, also nimmt `json.loads` es
an und liefert einen Python-String, der sich nicht nach UTF-8 encodieren lässt.
Django baut den Response-Body als `str` und encodiert ihn erst in
`django/http/response.py` — dort schlug es mit `UnicodeEncodeError` fehl.
Ergebnis war eine 500 auf `api/validate/` **und** `api/preview/` für genau die
Reports, die man am dringendsten öffnen müsste: die kaputten. Ein solcher Report
war im Editor nicht mehr reparierbar, weil der Editor sich beim Laden über
`api/validate/` vergewissert.

Umgestellt auf `ensure_ascii=True`. Im Browser ändert sich nichts: der Editor
liest jede Antwort mit `JSON.parse`, und dort sind `\uXXXX` und das Rohzeichen
derselbe String. Nicht-ASCII-Labels (Umlaute, Emoji in einem Anzeigenamen)
reisen ab jetzt escaped und kommen unverändert an — das ist im neuen Test
`test_validate_survives_a_lone_surrogate_in_a_label` von beiden Seiten
festgehalten: Body ist `.decode("ascii")`-fähig, und der Wert überlebt den
Roundtrip.

Der Kommentar an der Stelle nennt den Grund, damit niemand `ensure_ascii=False`
als "schönere Antwort" zurückdreht.

Drei neue Tests in `tests/test_editor_api.py`:

| Test | Was er festhält |
| --- | --- |
| `test_validate_survives_a_lone_surrogate_in_a_label` | 200 statt 500, Body ASCII, Label unverändert zurück |
| `test_preview_survives_a_lone_surrogate_in_a_label` | dasselbe für die Vorschau, plus: es kommen weiterhin Zeilen |
| `test_every_editor_endpoint_answers_in_pure_ascii` | die Regel statt des Symptoms — `api/fields/`, `api/validate/`, `api/preview/` und eine Fehler-Envelope, die die Eingabe zitiert |

Der dritte ist der eigentlich wertvolle: ein Label ist nur der kürzeste Weg
hinein, und der Test hält jeden künftigen Endpunkt, der Nutzertext
zurückspiegelt, mit fest.

Nicht mein Teil und von `portability-dev` erledigt: das Gate in
`payload.load_json_object`, das solche Dokumente beim Import gar nicht mehr
hereinlässt. Meine Hälfte bleibt auch danach nötig — für Werte, die vor dem Gate
entstanden sind, und für jeden Pfad, der nicht durch das Gate läuft (das
JSON-Panel des Editors, das CRUD-Textfeld).

## T-001: die Vorschau rendert mit dem Renderer des Exporters

Die Formatierung von `ColumnFormat` gab es zweimal: `format_cell()`,
`_format_temporal()` und `_format_number()` in `views/api.py` (Vorschau) und
seit `exporter-dev`s Runde wortgleich als `format_cell_value()` in
`exporters.py` (Export). Zwei Implementierungen, die genau so lange gleich
bleiben, bis jemand eine davon anfasst. Meine drei Funktionen sind ersatzlos
raus; die Vorschau ruft jetzt `exporters.format_cell_value` auf.

**Wie ich die Verhaltensgleichheit geprüft habe**, bevor ich gelöscht habe —
nicht auf Zusage, weil `views/api.py` mein Gebiet bleibt:

1. **Zeilenweiser Vergleich der Quelltexte.** Rumpf von
   `views/api.py::format_cell` gegen `exporters.py::format_cell_value` und beide
   Hilfsfunktionen gegen ihre Gegenstücke, Zeile für Zeile über `==` auf den
   Zeilenlisten — beides `True`. Es war wirklich derselbe Code, nicht nur
   derselbe Zweck. Der einzige Unterschied sind die Vorgabewerte
   `datatype=None, event=None` in der Signatur; positionell ändert sich dadurch
   nichts, und die Vorschau übergibt ohnehin beide.
2. **Der Aufruf blieb Zeichen für Zeichen stehen.** In `PreviewView._rows` ist
   nur der Name ausgetauscht, die vier Argumente in derselben Reihenfolge.
   `_formats_by_index` bleibt meins — der Exporter hat seine eigene Paarung,
   weil er zusätzlich entscheidet, *ob* überhaupt formatiert wird.
3. **Der Verhaltenstest lief unverändert weiter.**
   `test_preview_applies_the_column_format` prüft sieben Spalten quer über alle
   drei Stilarten (raw/currency, yes_no/one_zero, iso/date_only) am fertigen
   Response und hat nach dem Umbau ohne eine Änderung bestanden. Das ist die
   Abnahme; kein Test in meiner Datei hing an der internen Implementierung.

Die Vorschau ruft `format_cell_value` und **nicht** `format_export_cell`:
letzteres gibt unformatierte Werte nativ typisiert zurück, was für XLSX richtig
und für eine JSON-Antwort falsch ist — eine Vorschauzelle muss immer ein String
sein.

Zur Strenge des Renderers: `format_cell_value` fängt nichts ab, ein Stil, der
nicht zum Datentyp der Spalte passt (`date_only` auf einer Uhrzeitspalte, über
eine importierte oder handeditierte Definition erreichbar), wirft. Das war bei
meinem `format_cell` genauso, ich habe daran nichts geändert. In der Vorschau
landet das nicht als 500: `PreviewView.post` fängt um `_rows()` herum jede
Exception und antwortet mit `stage: "execute"` und 400. Auf dem Exportweg fängt
`format_export_cell` selbst, weil eine Exception dort fünf Celery-Retries
bedeutet. Beide Seiten sind abgesichert, ohne dass der Renderer selbst weich
wird.

### Der Import ist bewusst lazy

`exporters.py` importiert auf Modulebene `models`, `query.compiler` und
`registry.library`. Ein `from ..exporters import format_cell_value` ganz oben in
`views/api.py` hätte all das zur URLconf-Importzeit hereingezogen — genau die
Eigenschaft, für die `get_registry()` und `get_compiler()` seit Welle 1
Funktionen und keine Modulimporte sind (Modul-Docstring, ADR 0005 Abschnitt 2).
Deshalb dritte Naht statt Import:

```python
def get_cell_renderer():
    from ..exporters import format_cell_value
    return format_cell_value
```

Steht direkt bei den beiden anderen, gleiche Begründung, gleicher Nutzen für
Tests (an einer Stelle austauschbar). Die Abschnittsüberschrift heißt jetzt
"The three seams", `__all__` ist ergänzt.

Ein Anti-Drift-Test in meiner Datei,
`test_the_preview_renders_cells_with_the_exporter_s_function`, prüft
`api.get_cell_renderer() is exporters.format_cell_value` und dass
`format_cell`, `_format_temporal` und `_format_number` in `views/api.py` nicht
wieder auftauchen. Nötig, weil `exporter-dev`s Paritätstest
(`test_the_preview_and_the_export_share_one_renderer`) sich ausdrücklich
abschaltet, sobald es nichts mehr zu vergleichen gibt — ab jetzt hält meiner den
Zustand.

Aufgeräumt: `datetime`, `decimal` und `django.utils.formats`/`timezone` waren
nur noch für die gelöschten Funktionen importiert und sind raus.

## Testergebnis

`pytest tests/test_editor_api.py`: **116 passed**, keine Änderung an einem
bestehenden Test nötig.

`pytest -m "not performance"`: **1120 passed, 9 failed**. Alle neun sind
Fehlschläge auf Markern anderer Agenten, die auf ihre Entmarkierungsrunde
warten — kein echter Regress:

* `test_security.py::test_the_validate_endpoint_survives_a_lone_surrogate` und
  `::test_the_preview_endpoint_survives_a_lone_surrogate` — `XPASS(strict)`,
  **durch meine Änderung** grün geworden, das ist der Zweck. Entmarkierung durch
  `security-reviewer`.
* `test_security.py::test_the_export_view_survives_a_stored_lone_surrogate`,
  `::test_a_lone_surrogate_is_refused_by_the_payload_gate`,
  `::test_a_duplicate_identifier_is_a_form_error_not_a_500`,
  `::test_the_import_view_cannot_be_talked_into_the_event_copy_strategy` —
  `XPASS(strict)`; S-003-Rest, S-004, S-006, alle von anderen behoben.
* `test_integration.py::test_finding_a_column_format_chosen_in_the_editor_reaches_the_export`
  (T-001) und
  `::test_finding_an_aggregated_money_column_keeps_its_two_decimal_places` —
  `XPASS(strict)`; `exporter-dev` bzw. `query-dev`, `test-engineer` entmarkiert.
* `test_security.py::test_a_report_full_of_join_columns_costs_one_query_per_column`
  — kein `xfail`, sondern ein Charakterisierungstest, den `query-dev`s
  S-005-Behebung überholt hat: er erwartet, dass die Query-Zahl mit der
  Spaltenzahl wächst, gemessen wird jetzt `(2, 2)`. Gehört
  `security-reviewer`/`query-dev`, nicht mir, und hängt nicht an meiner
  Änderung.

`flake8`, `isort -c`, `black --check` über `views/api.py` und
`tests/test_editor_api.py`: grün. Kein repo-weiter Lauf, kein `git commit`.

## Eine Kleinigkeit für den Orchestrator

`docs/adr/0005-editor.md:96` behauptet noch "**Formatierung ist
Vorschau-lokal.** `format_cell()` in `views/api.py` …". Das stimmt seit T-001
nicht mehr. `docs/` ist nicht mein Gebiet, deshalb nur der Hinweis.

---

# Nachtrag 2: Blindtext auf der Editor-Seite

Vom Nutzer an der Dev-Instanz gefunden, vom Orchestrator isoliert: ein
Template-Kommentar stand als sichtbarer Text auf der Editor-Seite.

## Ursache

Djangos kurze Kommentarform `{# ... #}` ist **nicht mehrzeilig**. Sie wird von
`django.template.base.tag_re` gelext, und deren Kommentar-Alternative matcht
nicht über einen Zeilenumbruch hinweg. Ein Kommentar, dessen Öffner und
Schließer auf verschiedenen Zeilen stehen, ist deshalb überhaupt kein
Kommentar — er ist Zeichendaten und landet wortwörtlich im gerenderten HTML.

Gegen die Django-Version dieser venv nachgeprüft, statt aus dem Gedächtnis
(CLAUDE.md Regel 1):

```
django 5.2.16
single: 'ab'                    Template('a{# hi #}b')
multi : 'a{#\n hi \n#}b'        Template('a{#\n hi \n#}b')      <- kein Kommentar
comment tag: 'ab'               Template('a{% comment %}\n hi \n{% endcomment %}b')
```

`{% comment %}` hat die Einschränkung nicht: es ist ein Block-Tag, über das der
Parser mit `skip_past` hinwegläuft, Zeilenumbrüche inklusive.

## Was ich geändert habe

Vier Stellen, alle in meinen zwei Templates:

| Datei | Stelle | Was |
| --- | --- | --- |
| `editor.html` | Kopf, "The editor shell. Bootstrap 3 markup …" | auf `{% comment %}` umgestellt |
| `editor.html` | vor den versteckten Inputs, "What the CRUD form of persistence-dev expects …" | **gelöscht** (der vom Nutzer gemeldete Blindtext) |
| `editor.html` | Portability-Knöpfe, "File import/export and templates live in …" | auf `{% comment %}` umgestellt |
| `preview_table.html` | Kopf, "The live preview table, rendered on the server …" | auf `{% comment %}` umgestellt |

In den beiden Kopfkommentaren steht jetzt zusätzlich, **warum** es ein
Comment-Tag ist. Ohne die Begründung baut der nächste, der dort etwas ergänzt,
denselben Fehler wieder ein, weil die kurze Form überall sonst in der Datei
korrekt funktioniert — sie ist ja einzeilig.

Selbst gegengelesen statt nur der Liste vertraut: ein kleines Skript hat beide
Dateien zeilenweise nach jedem `{#` ohne `#}` auf derselben Zeile und nach jedem
verwaisten `#}` durchsucht. Ergebnis nach der Änderung: null. Die vier
verbliebenen `{# … #}` in `editor.html` und das eine in `preview_table.html`
sind einzeilige Abschnittsmarker und Owner-Zeilen und damit korrekt.

### Zum gelöschten Kommentar

Der Inhalt war nicht wertlos, deshalb hier, damit er nicht verloren geht: das
CRUD-Formular von `persistence-dev` erwartet `name`, `description`,
`identifier`, `base` und `definition` (JSON-String, den `forms.JSONField`
parst). `definition` und `base` hält der Editor bei jeder Änderung nach;
`identifier` wird unverändert zurückgepostet, **weil ein leerer das Modell einen
neuen erzeugen lässt und damit jeden Scheduled Export bricht, der auf diesen
Report zeigt**. Das ist der einzige nicht offensichtliche Teil der drei
versteckten Inputs. Steht damit hier und in
`handoff/requests/frontend-dev-an-integrator-urls.md`, nicht mehr im Template.

## Verifikation

Nicht per Grep über die Templatedatei, sondern am gerenderten Response über
`django.test.Client` mit echtem Login (`client_with_perms` meldet sich in
`conftest.py` mit E-Mail und Passwort an, kein `force_login`-Kurzschluss). Ein
Grep über die Datei würde weiter grün bleiben, sobald die Shell einmal ein
Fragment von woanders einbindet.

Drei neue Tests in `tests/test_editor_api.py`:

* `test_the_editor_page_renders_no_raw_django_comment` — `editor.new`: Status
  200 (damit der Test nicht auf einem Login-Redirect grün wird), kein `{#`, kein
  `#}`, und zusätzlich alle drei Kommentarabsätze namentlich.
* `test_the_stored_editor_page_renders_no_raw_django_comment` — dieselbe Vorlage
  über `editor.edit` mit einem gespeicherten Report.
* `test_the_preview_html_renders_no_raw_django_comment` — das `html`-Element der
  Antwort von `POST api/preview/`, also `preview_table.html`.

Die groben `"{#" not in content`-Zusicherungen sind Absicht: sie fangen die
ganze Fehlerklasse und nicht nur die vier bekannten Absätze.

## Testergebnis

`pytest tests/test_editor_api.py`: **119 passed** (116 + 3 neue).

`pytest -m "not performance"`: **1183 passed, 2 xfailed, 0 failed**. Die neun
`XPASS(strict)`-Fehlschläge aus meinem vorigen Nachtrag sind weg —
`security-reviewer` und `test-engineer` haben ihre Marker inzwischen entfernt.
Die Suite ist damit vollständig grün.

`flake8`, `isort -c`, `black --check` über `tests/test_editor_api.py`: grün.
Templates sind nicht Teil der Python-Linter. Kein repo-weiter Lauf, kein
`git commit`.

---

# Nachtrag 3: Layout aufgeräumt (A) und grafischer Editor für Vorlagen (B)

**Stand:** 2026-08-10. Zwei unabhängige Aufträge, beide erledigt. Kein Commit
(Modus A). Keine Fremddatei angefasst.

## A — Layout des Editors

Nutzer-Feedback war: alles da, aber gedrängt und die Reihenfolge ergibt keinen
Sinn. Umgesetzt sind vier Blöcke mit je einer Überschrift, einer Einzeiler-
Erläuterung und einem `<hr class="pcr-section-divider">` dazwischen:

| Block | `id` | Inhalt |
|---|---|---|
| 1 | `pcr-section-basics` | Name, Beschreibung, **Report base** (aus der linken Spalte hierher gezogen) |
| 2 | `pcr-section-content` | Field library (links) · Columns + **Filters** (rechts) |
| 3 | `pcr-section-arrangement` | Sorting (`col-md-8`) · Options (`col-md-4`) |
| 4 | `pcr-section-result` | Live preview · Definition as JSON |

**Kein einziges bestehendes `id` hat sich geändert**, nur die Position im
Dokument und die umgebenden Wrapper. `report-editor.js` ist unverändert — das war
die Bedingung und sie hält: die Playwright-Tests (echtes Drag & Drop, select2,
Basiswechsel, Live-Vorschau) laufen ohne Anpassung durch.

### Warum die Filters in Block 2 und nicht in Block 3

Wie vorgegeben, mit derselben Begründung, die ich auch gewählt hätte: eine
Bedingung entsteht durch Ziehen eines Feldes aus der Bibliothek links, genau wie
eine Spalte. Spalten und Filter beantworten zusammen die *eine* Frage „was landet
im Report" — Filter über die Zeilen, Spalten über die Spalten. Sortierung und
Optionen ordnen dieses Ergebnis nur noch um. Kein Abweichungsbedarf.

### Zweiter Speichern-Knopf

`#pcr-save` bleibt oben in Block 1 (nicht ersetzt), zusätzlich steht
`#pcr-save-bottom` unter Block 4. Der untere Knopf liegt **außerhalb** von
`#pcr-form` und postet über das HTML5-Attribut `form="pcr-form"` — die Seite hat
weiter genau ein Formular, `report-editor.js` weiter genau einen
`submit`-Handler (und der ist der, der das versteckte `definition`-Feld füllt,
also darf es keinen zweiten geben).

Zur ausdrücklichen Frage: **`#pcr-save` wird von `report-editor.js` nicht
angesprochen** — geprüft per Grep über `js/`, `py`, `html`, `css`; die einzigen
Treffer waren das Template selbst und zwei Zusicherungen in
`tests/test_editor_api.py`. Es war also kein Mechanismus mitzugeben. Trotzdem
tragen jetzt **beide** Knöpfe die gemeinsame Klasse `pcr-save`, damit ein
künftiges „alle Speichern-Knöpfe" nicht wieder wissen muss, dass es zwei sind;
die Einzel-IDs bleiben, weil die bestehenden Tests auf `id="pcr-save"` prüfen.
Das `disabled` bei fehlendem `save_url` bekommen beide
(`test_both_save_buttons_are_disabled_together`).

### Dateien

* `templates/pretix_custom_reports/editor.html` — nur umsortiert und umhüllt,
  plus die Template-Modus-Zweige aus Auftrag B.
* `static/pretix_custom_reports/css/report-editor.css` — neuer Abschnitt „page
  structure" (`.pcr-section`, `.pcr-section-title`, `.pcr-section-divider`,
  `.pcr-save-bar`), und `.pcr-library-list` von `30em` auf `34em`: die linke
  Spalte ist ohne das Base-Panel kürzer als die rechte.

Live nachgesehen, nicht nur getestet: `GET
/control/event/demo/demo-event/customreports/editor/` auf dem laufenden Server
liefert 200, drei Trenner und die vierzehn IDs in der geplanten Reihenfolge.

## B — Grafischer Editor für Veranstalter-Vorlagen

`TemplateEditorView` in `views/editor.py`, gleiche Shell, gleiches JavaScript,
gleiche JSON-Endpunkte. **`views/api.py`, `registry/**` und `views/templates.py`
sind unverändert** — wie angekündigt, und ich habe es gegen den installierten
Code geprüft, nicht geglaubt (Regel 1):

* `forms.ReportDefinitionForm.Meta.fields` ist wirklich
  `("name", "description", "identifier", "base", "definition")`, `definition` ist
  ein `JSONField` (`PrettyJSONFormField`), das den JSON-**String** des versteckten
  Inputs parst, und der Eigentümer (`event`/`organizer`) kommt über
  `__init__(organizer=…)` aus der View, nicht aus dem Body. Die Annahme stimmt.
* Der Beweis ist kein Kommentar, sondern ein Test:
  `test_template_editor_post_round_trip`, parametrisiert über **jede** Golden
  Fixture — Editor-Formular → `TemplateCreateView` → Datenbank → Editor-Seite,
  Definition unverändert, danach noch ein Update über `TemplateUpdateView` mit
  demselben `identifier`.

### Aufbau

`EditorShellMixin` trägt jetzt alles Gemeinsame (Kontext, `js_strings`, URL-Bau);
`ReportEditorView` und `TemplateEditorView` unterscheiden sich in genau vier
Punkten: Gate, `url_kwargs()`, `api_url()` und `get_report()`. Kein zweites
Template für die Shell — `editor.html` bekommt `is_template` und
`editor_base_template` und entscheidet daran über Titel, Referenz-Event-Hinweis,
Vorschau-Warnung und Portabilitäts-Knöpfe.

Der Mixin war nötig, weil `ReportEditorView` `EventPermissionRequiredMixin` erbt
und dessen `as_view()` den View in `event_permission_required` wickelt — eine
Unterklasse davon wäre auf Organizer-Ebene an einem fehlenden `request.event`
zerbrochen.

**Fallstrick, der mich einmal erwischt hat:** meine erste Fassung nannte den
Kontext-Hook `extra_context()`. Das ist der Name eines *Attributs* von Djangos
`ContextMixin` (`django/views/generic/base.py`), das `get_context_data()` mit
`kwargs.update(self.extra_context)` verarbeitet, sobald es nicht `None` ist — bei
einer Methode also `TypeError: 'method' object is not iterable`, und zwar auf
**jeder** Editor-Seite, nicht nur der neuen. Heißt jetzt `shell_extra_context()`.

### Basis-Template

`editor.html` beginnt mit `{% extends editor_base_template|default:"…" %}`.
Verifiziert in pretix 2026.6.0: `pretixcontrol/event/base.html` setzt nur
`title`, `pretixcontrol/organizers/base.html` umschließt zusätzlich einen
`inner`-Block — beide lassen `content` frei, und den überschreiben wir. Der
Rest von `pretixcontrol/base.html` fragt `request.event` überall defensiv ab
(`{% if request.event and … %}`), also läuft die Seite ohne Event.

### Referenz-Event

`?reference_event=<slug>`. Zulässig sind Events des Organizers mit
`plugins__contains="pretix_custom_reports"` **und** `event.orders:read` für diesen
Nutzer — beides notwendig, weil sonst die API-Endpunkte 404 respektive 403
antworten und der Editor als leere Seite mit rotem Kasten enden würde.

* **genau eins** → automatisch gewählt, kein Zwischenschritt, und dann auch kein
  „anderes Event wählen"-Link (der führte zurück auf dieselbe Seite).
* **mehrere** → `editor_choose_event.html`, ein `<select>` mit `method="get"`.
  Kein CSRF-Token, weil ein GET-Formular nichts ändert.
* **ungültig / nicht berechtigt / anderer Organizer** → dieselbe Auswahlseite mit
  einer `messages.error`, Status 200. Die drei Fälle werden bewusst *nicht*
  unterschieden: für den Nutzer bedeuten sie dasselbe, und sie
  auseinanderzuhalten würde verraten, welche Slugs existieren.
* **keins** → dieselbe Seite, aber mit Erklärung statt Auswahl. Das trifft den
  Organizer-Admin ohne Bestellrecht; ohne diesen Zweig hätte er eine Seite
  bekommen, deren jede Anfrage 403 antwortet.

`api.fields`, `api.preview` und `api.validate` werden mit
`{organizer, event: reference_event.slug}` reversed, alles andere nur mit
`{organizer}`.

### Sicherheit

* Gate: `OrganizerPluginActiveMixin` + `OrganizerPermissionRequiredMixin` mit
  `ORGANIZER_CHANGE_PERMISSION` — dieselbe einzelne Berechtigung, an der
  `TemplateListView` schon hängt, also kein getrennter Lesemodus und
  `may_change()` ist genau diese Prüfung.
* Die **Vorschau bleibt event-gegattet**. `views/api.py` ist unangetastet, also
  gilt weiter `event.orders:read` plus das harte Event-Scoping des Querysets, und
  das Limit kommt weiter vom Server.
  `test_template_editor_preview_stays_gated_on_the_reference_event` prüft beide
  Richtungen mit zwei verschiedenen Nutzern.
  Nebenbefund: der abgewiesene Nutzer bekommt **404, nicht 403** — pretix'
  `ControlMiddleware` wirft `Http404`, bevor irgendeine View läuft, wenn der
  Nutzer im Event gar keine Berechtigung hat. Der Test erlaubt beides, damit ein
  künftiger pretix-Wechsel nicht wie ein Loch in unserem Gate aussieht.
* Der Editor schickt weiter ausschließlich Feld-Keys; am JSON-Vertrag hat sich
  nichts geändert (`js_strings()` hat **keinen** neuen Eintrag).
* `ReportDefinition.objects.templates_for_organizer(organizer).get(pk=…)` ist
  Mandantengrenze und XOR-Filter in einem. Ein Event-Report ist über diese Route
  strukturell nicht erreichbar, nicht per Filter, den man vergessen kann — Test
  `test_template_editor_404s_for_a_report_that_is_not_a_template`.

### Routen

`template_editor_urlpatterns` (eigene Liste), Namen und Routen exakt wie
festgelegt:

```
^control/organizer/(?P<organizer>[^/]+)/customreports/templates/editor/$
    -> organizer.templates.editor.new
^control/organizer/(?P<organizer>[^/]+)/customreports/templates/editor/(?P<template>\d+)/$
    -> organizer.templates.editor.edit
```

Kopierfertige Anforderung:
`handoff/requests/frontend-dev-an-integrator-template-editor-urls.md`.
`urls.py` habe ich **nicht** angefasst.

## Parallelarbeit: alles passt zusammen

Anders als erwartet gab es **keinen** `NoReverseMatch`. `portability-dev` hat
`template_list.html` parallel schon auf `organizer.templates.editor.new` /
`.edit` mit `template=<pk>` umgestellt und `TemplateApplyView` auf `editor.edit`
— exakt die Namen und Kwargs aus dem Contract. Meine Testsuite hängt die Routen
selbst in die URLconf (`editor_routes`-Fixture, jetzt inklusive
`template_editor_urlpatterns` und `templates_organizer_urlpatterns`), reverst also
den echten Namespace und läuft unverändert weiter, sobald der Integrator die eine
Zeile ergänzt.

**Ein Hinweis für den Orchestrator, weil es die Reihenfolge betrifft:**
`template_list.html` reverst die zwei neuen Namen mit `{% url %}` — also *nicht*
defensiv. Solange `template_editor_urlpatterns` nicht in `urls.py` steht, wirft
die Vorlagenliste `NoReverseMatch`. Die zwei Änderungen (portability-devs Template
und integrators `urls.py`) müssen zusammen landen; einzeln ist die Vorlagenliste
kaputt. Mein eigener Teil ist davon nicht betroffen, `views/editor.py` reverst
alles über `url_or_none()`.

## Tests

`tests/test_editor_api.py`, neu (119 → 152 Tests):

*Layout (A)*

* `test_editor_page_orders_its_four_blocks` — vierzehn IDs in der geplanten
  Dokumentreihenfolge.
* `test_editor_page_separates_the_four_blocks` — drei Trenner, vier
  Überschriften, und die CSS-Regeln dazu existieren wirklich.
* `test_the_report_base_sits_in_the_first_block`
* `test_editor_repeats_the_save_button_at_the_end_of_the_page` — ein Formular,
  zwei Knöpfe, der untere außerhalb und per `form="pcr-form"` hinein.
* `test_both_save_buttons_are_disabled_together`

*Vorlagen-Editor (B)*

* `test_template_editor_points_at_the_reference_events_endpoints` (a)
* `test_template_editor_asks_which_event_when_there_are_several` (b)
* `test_template_editor_handles_an_unusable_reference_event` (c), parametrisiert
  über unbekannten Slug, Event ohne Plugin und einen kodierten Slug
* `test_template_editor_post_round_trip` (d), parametrisiert über jede Golden
  Fixture
* `test_template_editor_offers_export_only` (e)
* plus: Auto-Auswahl bei genau einem Event, Weg zurück zur Auswahl inkl.
  `data-pcr-leave`, fremder Organizer, kein nutzbares Event, fehlende
  Organizer-Berechtigung (403), Login-Redirect, gespeicherte Vorlage öffnen,
  404 für Nicht-Vorlage und für unbekannte PK, ungespeicherte Vorlage nicht
  exportierbar, Kommentar-Blindtext auf beiden neuen Pfaden, Vorschau-Gate.

**Ein Fixture-Fallstrick zum Weitersagen:** `client_organizer_only` baut sich
einen **eigenen** `Client()`. pytest-djangos `client` ist *ein* Objekt pro Test —
ein zweites Login darauf ersetzt das erste, und ein Test mit zwei Nutzern
(Vorschau-Gate) prüft dann zweimal denselben. Das hat mich einen falsch-roten
Test gekostet.

## Testergebnis

`pytest tests/test_editor_api.py`: **152 passed**, davon 10 Browser- und
Node-Tests (Playwright läuft hier, wird nicht übersprungen).

`pytest -m "not performance"` über das ganze Repo: **1224 passed, 2 xfailed, 0
failed** — inklusive `tests/test_org_templates.py` und `tests/test_portability.py`
mit den parallelen Änderungen von `portability-dev`.

`flake8`, `isort -c`, `black --check` über `views/editor.py` und
`tests/test_editor_api.py`: grün. Kein repo-weiter Formatierlauf, kein
`git commit`.

## Offen / bewusst nicht gemacht

* **Keine Übersetzung.** Alle neuen Strings englisch; der `de`-Katalog ist Sache
  des `integrator`. Was neu ist und wo, steht in Abschnitt 5 der Handoff-Datei.
* **Kein Menüeintrag** für den Vorlagen-Editor. `signals.py` ist fremdes Gebiet,
  und der Einstieg über die Vorlagenliste genügt.
* **Referenz-Event wird nicht gespeichert.** Bewusst: es ist eine Sicht auf die
  Vorlage, keine Eigenschaft von ihr. Eine Vorlage mit gemerktem Event wäre eine
  halbe Event-Bindung und würde die XOR-Zusicherung des Modells verwässern. Wenn
  Nutzer es doch vermissen, gehört es in eine Nutzereinstellung und nicht in
  `ReportDefinition`.
* **Der Vorlagen-Editor ist nicht live geprüft**, weil seine Routen noch nicht in
  `urls.py` stehen (integrators Datei). Abgedeckt ist er über die Testsuite, die
  die echten Routen, den echten Namespace und die echte Middleware-Kette
  verwendet.
