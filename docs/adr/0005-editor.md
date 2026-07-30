# ADR 0005 — Aufbau des Report-Editors

Status: akzeptiert (Welle 1)
Autor: `frontend-dev`
Betrifft: `views/editor.py`, `views/api.py`, `static/**`, `templates/**/editor*.html`,
`templates/**/preview_table.html`

> Nummernlücke: 0003 und 0004 sind für `query-dev` und `persistence-dev`
> reserviert, die in derselben Welle parallel laufen. Eine Lücke ist billiger als
> eine Kollision.

---

## 1. Kontext

Der Editor muss vollständig klickbar sein, bevor Registry und Query-Compiler
existieren (ORCHESTRIERUNG.md Abschnitt 4: `frontend-dev` ist der langsamste
Agent und startet deshalb in Welle 1 gegen die Golden Fixtures). Gleichzeitig
darf die UI in Welle 2 beim Umschalten auf echte Daten **nicht** umgebaut werden
müssen — sonst war der Contract falsch.

Randbedingungen aus SPEC.md Abschnitt 4 und CLAUDE.md: pretix-Control-Stack,
kein CDN, keine Build-Chain, kein SPA-Framework, alle Endpunkte CSRF-geschützt
und permissionsgeprüft, Vorschau nie ohne Limit, keine ORM-Pfade oder Operatoren
aus dem Browser.

## 2. Entscheidung 1 — genau zwei Austauschpunkte, nicht zwei Betriebsarten

`views/api.py` enthält zwei Funktionen, `get_registry()` und `get_compiler()`,
die in Welle 1 die eingefrorenen Stubs aus `contracts/stubs.py` liefern. Der
restliche Code kennt ausschließlich die Protokolle
`FieldRegistry`/`QueryCompiler`/`CompiledReport`.

Verworfen: eine Auto-Erkennung („nimm die echte Registry, falls importierbar").
Sie wäre in Welle 1 bequem, macht aber unsichtbar, gegen was ein Fehlerbild
gerade läuft. Ebenfalls verworfen: ein Settings-Schalter — das wäre eine
Konfigurationsoption für einen Zustand, der nach Welle 2 nicht mehr existiert.

Nachgewiesen am Ende von Welle 1: mit den zwei Zeilen

```python
def get_registry():
    from ..registry.library import field_registry
    return field_registry()

def get_compiler():
    from ..query.compiler import ReportQueryCompiler
    return ReportQueryCompiler(field_registry())
```

antworten Feldbibliothek (83 statt 60 Felder) und Vorschau gegen die echte
Registry und den echten Compiler, ohne eine Änderung an Template, CSS oder
JavaScript.

## 3. Entscheidung 2 — die Zustand-↔-JSON-Abbildung liegt in *einer* Datei, und die wird getestet

`static/.../js/report-editor-model.js` ist die einzige Stelle, die weiß, wie der
Editor-Zustand auf das eingefrorene Dokument abgebildet wird. Sie enthält keinen
DOM-Zugriff, kein jQuery und ist als UMD-Modul auch unter node ladbar.
`tests/test_editor_api.py` führt **genau diese Datei** in einem
node-Subprozess über alle zehn Golden Fixtures aus und vergleicht das Ergebnis
mit `ReportDefinition.as_dict()` — inklusive Schlüsselreihenfolge.

Begründung: der Roundtrip („Fixture laden, bearbeiten, identisches JSON
ausgeben") passiert im Browser. Ihn in Python nachzubauen hätte eine
Zweitimplementierung getestet, nicht den Editor. Der node-Test ist mit
`skipif` abgesichert und braucht kein npm, kein Paket und keine Konfiguration.

Zwei Regeln, die daraus folgen und die der Test absichert:

* Die Operator-→-`value_kind`-Tabelle wird **nicht** in JavaScript gespiegelt.
  Sie kommt aus `GET api/fields/`, also aus `contracts.OPERATOR_SPECS`. Ein Test
  prüft, dass kein Operator-String in der Modelldatei vorkommt.
* Unvollständige Zeilen (Filter ohne Feld/Operator) werden beim Serialisieren
  **verworfen**, nicht als kaputtes JSON verschickt. Die Vorschau bleibt damit
  während des Tippens benutzbar; gemeldet werden sie über `localIssues()`.

## 4. Entscheidung 3 — Vorschau serverseitig gerendert, als HTML *und* als Zeilen

`POST api/preview/` liefert `columns`, `rows` (Anzeige-Strings) **und** `html`
(dasselbe durch `preview_table.html` gerendert). Der Browser setzt das Fragment
ein und baut keine Tabelle.

Begründung: SPEC.md F2 verlangt „serverseitig gerendert". Wichtiger ist der
Sicherheitsaspekt — die Vorschau zeigt echte Bestelldaten, und Djangos
Autoescaping ist die eine Stelle, an der das Escaping garantiert richtig
passiert. Ein Test schickt `<script>` durch eine Zelle und prüft, dass im HTML
nur `&lt;script&gt;` landet. `rows` bleibt trotzdem im Payload: Tests und
spätere Konsumenten brauchen die Werte maschinenlesbar, und beide Darstellungen
entstehen aus derselben Datenstruktur, können also nicht auseinanderlaufen.

Das Limit ist `contracts.PREVIEW_ROW_LIMIT`. Der Client darf weniger anfordern,
nie mehr; die Zahl wird geklemmt, nicht validiert-und-abgelehnt, und `iter_rows`
bekommt sie zusätzlich als zweite Bremse.

**Formatierung ist Vorschau-lokal.** `format_cell()` in `views/api.py` setzt
`ColumnFormat` für die Anzeige um (Datumsstil, Zahlenstil, Boolean-Stil).
Werte, die schon als `str` ankommen, werden unverändert durchgereicht — die
Funktion kann also nicht doppelt formatieren, wenn Compiler oder Exporter das
später selbst tun. Für CSV/XLSX bleibt `ListExporter` zuständig (CLAUDE.md
Regel 6).

## 5. Entscheidung 4 — ein Widget je Datentyp, Freitext ist die Ausnahme

Aus `datatype` und `value_kind` des gewählten Operators folgt das Widget:

| Datentyp / Operator | Widget |
|---|---|
| `value_kind == none` | kein Feld, nur ein Hinweis |
| `value_kind == day_count` | Zahlenfeld 1…`MAX_DAY_COUNT` mit Einheit |
| `choice`/`country`/`multichoice`, Server liefert `choices`, Liste | Mehrfachauswahl (select2 ab 8 Optionen) |
| dieselben, Einzelwert | Auswahlliste |
| `boolean` | Ja/Nein-Auswahl, erzeugt echte JSON-Booleans |
| `date` / `datetime` / `time` | `input type=date` / `datetime-local` / `time` **plus** die relativen Operatoren in der Operatorauswahl |
| `integer` / `decimal` / `money` | Zahlenfeld mit passendem `step` |
| `between` | zwei typisierte Felder |
| Liste ohne `choices` | Token-Editor (hinzufügen/entfernen), kein Komma-Freitext |
| `string`/`text`/`email`/`url`/`phone` | typisiertes Textfeld — der legitime Freitextfall |

Nachgeprüft, dass die Werte passen, die dabei entstehen: `query/values.py` liest
Datumswerte mit `date.fromisoformat`/`datetime.fromisoformat`, also genau das
Format, das die HTML-Datumsfelder liefern (`2026-03-01`,
`2026-03-01T14:30`, `14:30`). Choice-Werte behalten ihren JSON-Typ: die Auswahl
gibt Strings zurück, `castChoice()` bildet sie auf den vom Server gelieferten
Wert zurück, damit ein `QuestionOption`-Primärschlüssel eine Zahl bleibt.

## 6. Entscheidung 5 — der Basiswechsel zeigt zuerst den Preis

`baseImpact(state, base)` berechnet ohne Seiteneffekt, was ein Wechsel kostet:
zu entfernende Spalten, Filter und Sortierstufen, Spalten die ein Aggregat
bekommen, Spalten die ihres verlieren. Erst nach Bestätigung läuft
`applyBase()`. Ist der Plan leer, wird ohne Rückfrage gewechselt.

Verworfen: den Wechsel sperren, solange betroffene Felder gewählt sind. Das
macht die Basis faktisch unveränderlich und provoziert „neu anfangen".

## 7. Entscheidung 6 — `PluginActiveMixin`

pretix hängt `require_plugin` nur an die **Presale**-URLs eines Plugins
(`multidomain/plugin_handler.py`); Control-Panel-URLs eines Plugins sind auch für
ein Event erreichbar, in dem das Plugin abgeschaltet ist. SPEC.md F1 will das
Gegenteil. Der Mixin liefert 404, wenn das Modul nicht in
`event.get_plugins()` steht, und liegt in `views/api.py`, damit
`views/editor.py` ihn mitbenutzen kann. Empfehlung an den `integrator`:
`views/placeholder.py` und die CRUD-Views genauso absichern.

## 8. Konsequenzen

* Kein npm, kein Bundler, keine neue Abhängigkeit. jQuery, Bootstrap 3,
  Sortable.js, select2 und Font Awesome liefert `pretixcontrol/base.html`
  selbst gehostet mit; select2 ist optional (`if ($.fn.select2)`).
* Kein Inline-JavaScript. Die CSP des Control-Panels hat kein
  `unsafe-inline` für `script-src`; Konfiguration und Übersetzungen kommen über
  einen `<script type="application/json">`-Block.
* Übersetzbare Strings stehen in Python und in den Templates, nie in `.js`.
  `makemessages` braucht deshalb keinen `djangojs`-Lauf.
* Die Feldbibliothek ist **ein** Request für **beide** Basen (~50 KB mit der
  Stub-Registry, mehr mit echten Länderlisten). Das ist der Preis dafür, den
  Basiswechsel ohne Roundtrip erklären zu können.
* Die Golden Fixtures sind in Welle 1 über `api/examples/` ladbar. Der Abschnitt
  ist in einer installierten Kopie inert, weil `tests/` nicht paketiert wird,
  und wird in Welle 2 gelöscht.
