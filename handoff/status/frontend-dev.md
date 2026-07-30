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
