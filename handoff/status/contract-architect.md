# Status: contract-architect — Welle 0c

**Erledigt:**

- `pretix_custom_reports/contracts/` vollständig, fünf Module, reine
  Standardbibliothek (kein Django-, kein pretix-Import zur Laufzeit):
  - `errors.py` — Ausnahmehierarchie unter `ContractError`
    (`DefinitionValidationError`, `FieldContractError`, `FieldResolutionError`,
    `ReportNotFoundError`, `CompilationError`)
  - `fields.py` — `ReportField` (eingefrorene Dataclass mit Invarianten),
    Enums `Base`, `DataType`, `Operator`, `Aggregate`, `SortDirection`,
    `ValueKind`, `ValueScope`, Key-Grammatik + Helfer, geschlossene
    Operator-Tabelle, Default-Operatoren und -Aggregate je Datentyp
  - `definition.py` — `SCHEMA_VERSION = 1`, Dokument-Dataclasses
    (`Column`, `ColumnFormat`, `FilterCondition`, `FilterGroup`, `SortEntry`,
    `ReportOptions`, `ReportDefinition`, `PortableReport`), strenger
    Strukturvalidator mit stabilen `ErrorCode`s, der **alle** Fehler sammelt,
    Grenzwerte, `iter_field_references()` als Naht zur Registry-Stufe
  - `protocols.py` — `FieldRegistry`, `QueryCompiler`, `CompiledReport`,
    `CompiledColumn`, `find_unresolved_fields()` und die geteilten Konstanten
    (Log-Action-Types, `EXPORT_FORM_REPORT_KEY`, `DEFAULT_CHUNK_SIZE`,
    `REGISTER_FIELDS_SIGNAL_NAME`)
  - `stubs.py` — `StubFieldRegistry` (60 Felder je Basis, inkl. Fragen, Meta,
    Fremdplugin-Feld) und `StubQueryCompiler` mit deterministischen Zeilen;
    führt die Registry-Stufen-Prüfungen aus, filtert und sortiert aber nicht
- `contracts/__init__.py` re-exportiert 112 Namen; `from
  pretix_custom_reports.contracts import *` läuft ohne Settings und ohne DB
- **Golden Fixtures** in `tests/fixtures/definitions/`: 10 gültige Definitionen
  (minimal, breit/31 Spalten, Basis `order` mit Aggregaten, Basis
  `orderposition`, Fragen-Felder, relative Datumsfilter, UND/ODER-Kombination,
  fünfstufige Sortierung, Plugin-/Meta-Felder, alle Optionen), 1 Export-Hülle in
  `portable/`, 17 bösartige Beispiele in `invalid/` samt maschinenlesbarem
  `_expectations.json`; `_index.json` beschreibt jede Fixture und listet die
  48 Feld-Keys, die die echte Registry bereitstellen muss
- `docs/adr/0001-contracts.md`
- `tests/test_contracts.py` (116 Tests, keine DB, 0,3 s)
- Zwei Anforderungen an fremdes Gebiet unter `handoff/requests/`

**Nicht erledigt (und warum):**

- Nichts aus dem Auftrag offen. Bewusst *nicht* gemacht: Registry-Inhalt,
  Queryset-Bau, Datumsauflösung, Datenmodell. Das ist Welle 1 und würde die
  Entscheidungen der vier Agents vorwegnehmen (`docs/adr/0001-contracts.md`
  Abschnitt 12).

**Getroffene Entscheidungen:** alle in `docs/adr/0001-contracts.md`. Die sechs,
die andere Agents unmittelbar binden:

1. **Key-Schema** `<namespace>.<rest>`, getrennt am ersten Punkt, Zeichensatz
   wie `Question.identifier`, **doppelter Unterstrich überall verboten**;
   Fremdplugins ausschließlich `plugin.<app_label>.<name>` (ADR 2).
2. **Portabilität**: Keys enthalten nie PKs; Fragen über `Question.identifier`.
   „Nicht auflösbar" ist beim Laden/Importieren ein regulärer Zustand, beim
   Ausführen ein harter Fehler. `ValueScope` markiert Felder, deren *Werte*
   beim Import übersetzt werden müssen (ADR 3).
3. **Strukturvalidierung und Feldauflösung sind getrennt** — der Validator
   kennt keine Felder. Vier `invalid/`-Fixtures sind deshalb strukturell gültig
   und scheitern erst in Stufe zwei; `_expectations.json` markiert das mit
   `stage` (ADR 4).
4. **F3**: Positionsfelder bei Basis `order` sind **aggregiert** verfügbar
   (`count`, `count_distinct`, `sum`, `min`, `max`, `avg`, `join`) über ein
   Attribut an der Spalte, nicht über synthetische Keys. Filter brauchen kein
   Aggregat, Sortieren nach Aggregaten ist in v1 nicht möglich (ADR 7).
5. **Report-Referenz in `export_form_data`: stabiler `identifier`, kein PK.**
   Das ist der Punkt, den `pretix-researcher` offengelassen hatte; Begründung
   Multi-Event und lesbare Fehlermails (ADR 5, Anforderung an
   `persistence-dev` liegt unter `handoff/requests/`).
6. **Log-Action-Types**: Präfix `pretix_custom_reports.` — die pretix-Doku
   (`doc/development/implementation/logging.rst`) empfiehlt wörtlich den
   Paketnamen; `pretix.plugins.*` ist der Namensraum des Kerns (ADR 8).

**Contract-Abweichungen:** Eine, bewusst und dokumentiert.
`docs/adr/0000-setup.md` Abschnitt 9 Punkt 3 verlangt importfreie Paket-
`__init__`. `contracts/__init__.py` re-exportiert trotzdem. Begründung in
`docs/adr/0001-contracts.md` Abschnitt 6: die dortige Begründung (gemeinsames
Schreibziel mehrerer Agents) trifft auf ein Paket mit genau einem Eigentümer,
das anschließend eingefroren wird, nicht zu — und die Definition of Done dieser
Welle verlangt `from pretix_custom_reports.contracts import *`. Alle anderen
Paket-`__init__` sind unangetastet.

**Offene Anforderungen an andere:**

- `handoff/requests/contract-architect-an-persistence-dev-report-identifier.md`
  — `identifier`-Feld, Unique-Constraints, Strukturvalidierung beim Speichern
  (und *nur* Struktur), Log-Action-Konstanten
- `handoff/requests/contract-architect-an-exporter-dev-exporterror.md`
  — `ContractError` → `ExportError`, eventgebundenes Nachschlagen, `_format`,
  20-MB-Grenze

Keine Einträge in `handoff/blockers.md`: nichts blieb unklar oder ungedeckt.

**Tests:** 130 passed, 0 failed (`pytest -q`; davon 116 aus
`tests/test_contracts.py`). `flake8`, `isort -c`, `black --check` sauber über
`pretix_custom_reports/contracts/` und `tests/test_contracts.py`; kein
repo-weiter Formatierlauf. Definition of Done geprüft:
`python -c "from pretix_custom_reports.contracts import *"` läuft, alle gültigen
Fixtures validieren und kompilieren gegen den Stub-Compiler, alle
`invalid/`-Fixtures scheitern in der in `_expectations.json` genannten Stufe.

**Nächster Schritt:** Freigabe durch den Nutzer. Empfohlener Blick vor dem
Einfrieren, weil jede spätere Änderung vier Agents trifft:

1. ADR 0001 Abschnitt 7 (F3 — Aggregate statt Sperre)
2. ADR 0001 Abschnitt 5.1 (`identifier` statt PK; kostet `persistence-dev` Arbeit)
3. `tests/fixtures/definitions/_index.json` → `required_field_keys`: das ist die
   Feldliste, die `registry-dev` verbindlich bauen muss
4. ADR 0001 Abschnitt 2, Absatz „Warum das `__`-Verbot" (bewusst in Kauf
   genommener Randfall)

Danach `handoff/contracts-freigegeben.md` setzen und Welle 1 starten.
