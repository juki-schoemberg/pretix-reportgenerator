# Agent-Prompt: pretix-Plugin „Custom Reports"

> **Nutzung:** Abschnitt 0–9 als initialen Prompt in Claude Code geben. Abschnitt 7 (Phasenplan) ist so geschnitten, dass jede Phase in einem eigenen Chat/Kontext bearbeitet werden kann. Abschnitt 10 als `CLAUDE.md` ins Repo legen.

---

## 0. Arbeitsweise (verbindlich)

Du baust ein pretix-Plugin. Halte dich strikt an diese Reihenfolge:

1. **Recherche zuerst.** Bevor du eine Zeile Code schreibst, lies die tatsächlich installierte pretix-Version im `site-packages` bzw. im Source-Checkout. Verlasse dich **nicht** auf dein Trainingswissen über pretix-APIs — die ändern sich pro Release. Konkret zu lesen:
   - `pretix/base/exporter.py` (`BaseExporter`, `ListExporter`, `MultiSheetListExporter`)
   - `pretix/base/signals.py` (`register_data_exporters`, `register_multievent_data_exporters`, `EventPluginSignal`)
   - `pretix/control/navigation.py` + Signal `nav_event` / `nav_organizer`
   - `pretix/base/models/orders.py` (`Order`, `OrderPosition`, `InvoiceAddress`, `QuestionAnswer`)
   - `pretix/base/models/exports.py` (Scheduled Exports) und der zugehörige periodische Task
   - `pretix/control/views/mixins.py` bzw. `pretix/control/permissions.py` (Permission-Mixins)
   - Ein bestehender Core-Exporter als Referenz, z. B. `pretix/plugins/reports/` und der OrderList-Exporter
2. **Design vor Code.** Liefere nach der Recherche ein Dokument `docs/architektur.md` mit Datenmodell (ER), Feld-Registry-Konzept, Sicherheitsmodell und offenen Fragen. **Warte auf mein Go**, bevor du implementierst.
3. **Phasenweise arbeiten** gemäß Abschnitt 7. Nach jeder Phase: lauffähiger Stand, Tests grün, Commit, kurzer Statusblock (siehe Abschnitt 9).
4. **Frage nach, statt zu raten.** Wenn eine pretix-API nicht eindeutig ist, lies den Source. Wenn eine fachliche Entscheidung offen ist, frag mich.
5. Kein Code-Dump ohne Kontext. Kleine, nachvollziehbare Commits mit aussagekräftigen Messages.

---

## 1. Kontext

- **Zielsystem:** selbst gehostetes pretix (Docker). Zielversion: `<HIER EINTRAGEN, z. B. 2025.7>`. Prüfe die tatsächliche Version und nenne sie im Architekturdokument.
- **Repo:** neues eigenständiges Plugin-Paket, Struktur analog zum offiziellen `pretix-plugin-cookiecutter`.
- **Paketname:** `pretix-custom-reports`, Python-Modul `pretix_custom_reports`.
- **Sprachen:** UI zweisprachig `de`/`en`, Quellstrings englisch, `de`-Katalog vollständig gepflegt.
- **Python/Django:** analog zur Zielversion von pretix (kein neuerer Django-Feature-Einsatz).
- **Lizenz:** `<HIER EINTRAGEN>`.

---

## 2. Ziel in einem Satz

Ein Plugin, mit dem Veranstalter ohne SQL-Kenntnisse per grafischem Editor eigene Auswertungen über Bestellungen und Bestellpositionen definieren, speichern, wiederverwenden, terminieren, exportieren/importieren und auf Organizer-Ebene als Vorlage bereitstellen können.

---

## 3. Fachliche Anforderungen

### F1 — Menüpunkt „Exports"
Eigener Navigationseintrag auf Event-Ebene und auf Organizer-Ebene, registriert über die pretix-Navigation-Signale. Nur sichtbar, wenn das Plugin im Event aktiv ist und der Nutzer die nötigen Rechte hat.

### F2 — Report-Builder (grafischer Editor)
- Reports werden per Klick zusammengestellt, kein Freitext-SQL, keine Freitext-ORM-Pfade.
- Linke Seite: Feldbibliothek, gruppiert nach Herkunft (Bestellung, Position, Rechnungsadresse, Produkt, Fragen, Check-in, Plugin-X …), mit Suchfeld.
- Rechte Seite: gewählte Spalten als sortierbare Liste (Drag & Drop), pro Spalte: Anzeigename überschreibbar, Formatierung (z. B. Datumsformat, Betrag mit/ohne Währung), Sichtbarkeit.
- **Live-Vorschau** mit begrenztem Datensatz (z. B. 20 Zeilen), serverseitig gerendert, mit Anzeige der geschätzten Gesamtzeilenzahl.

### F3 — Granularität (Designentscheidung, muss explizit umgesetzt werden)
Jeder Report hat eine **Basis**: `order` (eine Zeile je Bestellung) oder `orderposition` (eine Zeile je Position). Die verfügbare Feldmenge und die Filterlogik hängen davon ab. Bei Basis `order` müssen Positionsfelder entweder gesperrt oder als Aggregat (`count`, `sum`, `min`, `max`, `join(", ")`) verfügbar sein — schlage im Architekturdokument vor, welchen Weg du wählst, und begründe.

### F4 — Feldabdeckung
- Alle sinnvoll auswertbaren Felder von `Order` und `OrderPosition` inkl. der wichtigsten Relationen: `InvoiceAddress`, `Item`, `ItemVariation`, `SubEvent`, `Seat`, `OrderPayment`/`OrderRefund` (aggregiert), `Checkin` (aggregiert), `Voucher`, `Discount`, Meta-Properties.
- **Fragen/Antworten** (`Question`/`QuestionAnswer`) als dynamische Felder je Event.
- **Custom Fields anderer Plugins**: eigenes Signal, über das Fremdplugins Felder beisteuern können (siehe F5).
- Berechnete Standardfelder (z. B. offener Betrag, Zahlungsstatus-Klartext, Alter zum Veranstaltungsdatum) sind erlaubt, wenn sie als reguläre Registry-Felder mit Annotation implementiert sind.

### F5 — Erweiterbarkeit durch Fremdplugins
Definiere ein `EventPluginSignal` (z. B. `register_report_fields`), über das andere Plugins `ReportField`-Objekte zurückgeben. Dokumentiere dieses Signal in `docs/extending.md` mit lauffähigem Beispiel. Ein Feld muss mindestens deklarieren: stabiler Key, Label, Gruppe, Datentyp, erlaubte Filteroperatoren, sowie **entweder** einen ORM-Pfad **oder** eine Annotation **oder** einen Value-Getter.

### F6 — Filter je Feld
- Filter werden **pro Feld einzeln** definiert, mit typabhängigen Operatoren:
  - Text: ist, ist nicht, enthält, beginnt mit, ist leer, ist nicht leer, in Liste
  - Zahl/Betrag/Datum: =, ≠, <, ≤, >, ≥, zwischen, ist leer
  - Auswahl/FK: in, nicht in (mit Auswahl-Widget statt Freitext)
  - Boolean: ja/nein
- Datumsfilter brauchen **relative Optionen** (heute, letzte N Tage, laufender Monat, seit Event-Start), damit terminierte Reports sinnvoll wiederkehren. Das ist für F8 essenziell.
- Mehrere Filter kombinierbar mit UND/ODER; mindestens eine Gruppierungsebene (z. B. `UND` über Gruppen, `ODER` innerhalb einer Gruppe). Keine beliebig tiefe Verschachtelung in v1.

### F7 — Sortierung
Mehrstufige Sortierung: geordnete Liste aus (Feld, Richtung). Muss im Editor per Drag & Drop umsortierbar sein. Nur Felder aus der Registry, die als sortierbar markiert sind (nicht alles ist DB-sortierbar).

### F8 — Terminierung
- **Baue keinen eigenen Scheduler.** pretix bietet Scheduled Exports auf Event- und Organizer-Ebene, die auf registrierten Exportern aufsetzen.
- Registriere daher einen Exporter (`register_data_exporters` und `register_multievent_data_exporters`), dessen `export_form_fields` als zentrales Feld die Auswahl eines gespeicherten Reports enthält, plus Ausgabeformat.
- Verifiziere im Source, wie `form_data` persistiert wird, und stelle sicher, dass die gespeicherte Referenz (Report-ID) stabil bleibt bzw. bei gelöschtem Report sauber fehlschlägt statt einen Task-Crash zu erzeugen.
- Prüfe, ob die Scheduled-Export-UI zusätzliche Anforderungen an den Exporter stellt (Owner-Permissions, Multi-Event-Fähigkeit) und dokumentiere das im README.
- Erbe für die Ausgabeformate von `ListExporter`, damit CSV/XLSX/ODS etc. ohne Eigenbau abfallen.

### F9 — Import/Export von Report-Definitionen
- Export als JSON-Datei mit `schema_version`, Metadaten (Name, Beschreibung, Basis, erstellt von/am, pretix-Version) und der vollständigen Definition.
- Import mit **Validierung gegen die Registry**: unbekannte Feld-Keys führen zu einer Warnung mit Auswahl (überspringen / abbrechen), niemals zu stillem Verschlucken und niemals zu einem ungeprüften ORM-Pfad aus der Datei.
- **Portabilität:** Referenzen auf eventspezifische Objekte dürfen nicht über Primärschlüssel laufen. Nutze stabile Identifier, wo pretix sie anbietet (z. B. `Question.identifier`), sonst Name-Matching mit expliziter Auflösungsanzeige beim Import. Beschreibe die Auflösungsstrategie im Architekturdokument.
- Import per Datei-Upload und zusätzlich per Copy-Paste eines JSON-Blocks.

### F10 — Organizer-Vorlagen
- Auf Organizer-Ebene können Reports als **Vorlage** gepflegt werden.
- In einem Event: „Vorlage laden" erzeugt eine **Kopie** im Event, die frei angepasst werden kann. Kein Live-Link zur Vorlage in v1 (aber Datenmodell so bauen, dass eine `source_template`-Referenz für spätere Update-Hinweise mitgeführt wird).
- Beim Laden müssen eventspezifische Felder (Fragen, Produkte) aufgelöst werden — gleiche Mechanik wie beim Import (F9), also einmal implementieren und zweimal nutzen.

---

## 4. Technische Leitplanken (nicht verhandelbar)

**Sicherheit**
- **Allowlist-Prinzip:** Ein Report darf ausschließlich Feld-Keys referenzieren, die zur Laufzeit in der Registry existieren. ORM-Pfade, Lookups und Operatoren kommen **immer** aus der Registry, **nie** aus gespeichertem oder importiertem JSON. Ein importiertes JSON ist Untrusted Input.
- Kein `eval`, kein dynamisches `Q()`-Bauen aus Rohstrings, kein Raw SQL aus Nutzereingaben.
- Alle Querysets sind hart auf das aktuelle Event bzw. die berechtigten Events des Organizers eingeschränkt. Beachte `django-scopes` — Queries außerhalb eines aktiven Scopes müssen bewusst behandelt werden, insbesondere in Celery-Tasks.
- CSV-Injection: Formeln in Zellinhalten neutralisieren (prüfe, ob `ListExporter` das bereits tut, bevor du es doppelt machst).
- Permissions: Lesen/Ausführen an `can_view_orders`, Anlegen/Ändern/Löschen an `can_change_event_settings` (bzw. Organizer-Äquivalent). Verwende die pretix-Mixins, nicht eigene Prüfungen.

**Performance**
- Reports laufen gegen Events mit sechsstelligen Positionszahlen. Baue Querysets mit gezieltem `select_related`/`prefetch_related` auf Basis der tatsächlich gewählten Spalten.
- Antworten auf Fragen: kein N+1. Löse per Prefetch oder Subquery/Aggregat.
- Ausgabe streamend bzw. gechunkt (`iterator()` mit sinnvoller `chunk_size`).
- Die Vorschau darf niemals den vollen Datensatz laden.
- Prüfe die generierten Queries in Tests (`assertNumQueries` oder vergleichbar) für mindestens ein breites Beispielreport.

**Frontend**
- Halte dich an den pretix-Control-Stack (Bootstrap-Templates, statische Assets über die pretix-Pipeline). Kein eigenes SPA-Framework mit Build-Chain, wenn es sich vermeiden lässt.
- Der Editor darf JS-lastig sein, muss aber ohne CDN auskommen (self-hosted Assets) und einen serverseitigen Fallback für das reine Ausführen gespeicherter Reports haben.
- Vorschau und Feldauswahl über JSON-Endpunkte des Plugins, CSRF-geschützt.

**Datenhaltung**
- Eigene Models mit Migrationen. Die Report-Definition selbst als versioniertes JSON-Feld (`definition`, `schema_version`), damit Weiterentwicklung ohne Schema-Migration pro Feature möglich ist — die **Struktur** dieses JSON aber über Serializer/Dataclasses hart validieren.
- Event-Kopie: registriere dich auf das Signal für Event-Kopien, damit Reports beim Kopieren eines Events mitwandern und eventspezifische Referenzen übersetzt werden.
- Logging: nutze das pretix-Logging (`log_action`) für Anlegen/Ändern/Löschen/Ausführen.

**Qualität**
- Tests mit pytest und den pretix-Test-Fixtures. Mindestens: Registry-Auflösung, Filter-Kompilierung je Operator, Sortierung, Permissions (auch negativ), Import mit unbekanntem Feld, Import mit manipuliertem ORM-Pfad, Export-Roundtrip, Exporter-Registrierung, Organizer-Vorlage → Event-Kopie.
- `flake8`/`isort`/`black` nach pretix-Konventionen, Konfiguration wie im pretix-Core.
- README mit Installation, Screenshots-Platzhaltern, Kompatibilitätsmatrix; `docs/extending.md` für das Fremdplugin-Signal.

---

## 5. Vorgeschlagenes Datenmodell (validieren, gern verbessern)

```
ReportDefinition
  id, name, description
  event        FK Event   NULL   # gesetzt = Event-Report
  organizer    FK Organizer NULL # gesetzt + event NULL = Vorlage
  base         choice(order|orderposition)
  definition   JSONField        # columns, filters, sorting, options
  schema_version int
  source_template FK self NULL
  created_by / created_at / updated_at
  constraint: genau eines von (event, organizer) gesetzt
```

`definition`-Struktur (Entwurf, im Architekturdokument finalisieren):

```json
{
  "columns": [
    {"field": "order.code", "label": null, "format": null},
    {"field": "answer.tshirt-size", "label": "Größe", "format": null}
  ],
  "filters": {
    "op": "and",
    "children": [
      {"field": "order.status", "operator": "in", "value": ["p", "n"]},
      {"field": "order.datetime", "operator": "relative_last_days", "value": 7}
    ]
  },
  "sorting": [
    {"field": "order.datetime", "direction": "desc"}
  ],
  "options": {"include_cancelled": false, "row_limit": null}
}
```

---

## 6. Feld-Registry (Kern der Architektur)

Baue eine zentrale Registry, die pro Event zur Laufzeit aufgebaut und gecacht wird. Ein Feld ist mindestens:

```python
@dataclass(frozen=True)
class ReportField:
    key: str                 # stabil & portabel, z.B. "order.code", "answer.<identifier>"
    label: LazyI18nString
    group: str               # UI-Gruppierung
    datatype: str            # str|int|decimal|money|date|datetime|bool|choice|i18n
    bases: tuple             # auf welchen Report-Basen nutzbar
    orm_path: str | None     # für filter/sort/values
    annotation: Callable | None
    value_getter: Callable | None
    filter_operators: tuple
    sortable: bool
    choices: Callable | None # lazy, eventabhängig
```

Quellen der Registry, in dieser Reihenfolge:
1. Statisch deklarierte Core-Felder (handgepflegt, **nicht** automatisch über `Model._meta` alles freigeben — bewusste Auswahl, sonst leakst du interne Felder).
2. Dynamische Event-Felder (Fragen, Meta-Properties, ggf. Produkt-Varianten für Choice-Werte).
3. Felder aus dem Fremdplugin-Signal.

Konfliktbehandlung bei doppelten Keys: definiert und getestet (Vorschlag: Core gewinnt, Plugin-Felder erhalten Namespace-Präfix).

---

## 7. Phasenplan

Arbeite eine Phase nach der anderen ab. Jede Phase endet mit: lauffähiger Stand, grüne Tests, Commit, Statusblock.

| Phase | Inhalt | Ergebnis |
|---|---|---|
| **P0** | Recherche im pretix-Source, `docs/architektur.md`, offene Fragen | Freigabe durch mich |
| **P1** | Plugin-Skelett: Paketstruktur, `PretixPluginMeta`, Nav-Signale, leere Views, i18n-Setup, CI-Konfig | Plugin aktivierbar, Menüpunkt sichtbar |
| **P2** | Feld-Registry (Core-Felder + Fragen), Unit-Tests, Debug-View zum Anzeigen aller Felder | Registry belastbar |
| **P3** | Query-Builder: Definition → Queryset (Spalten, Filter, Sortierung), inkl. Sicherheitstests | Reports serverseitig ausführbar |
| **P4** | Models + CRUD-Views ohne fancy UI (einfache Formulare), Permissions, `log_action` | Reports speicherbar/ausführbar |
| **P5** | Editor-UI: Feldbibliothek, Drag & Drop, Filter-Widgets, Live-Vorschau | Kernfeature nutzbar |
| **P6** | Exporter-Registrierung (Event + Multi-Event) auf Basis `ListExporter`, Anbindung an Scheduled Exports, relative Datumsfilter | Terminierung funktioniert |
| **P7** | Import/Export der Definitionen inkl. Auflösungsstrategie und Validierung | F9 erfüllt |
| **P8** | Organizer-Vorlagen + „Vorlage laden" im Event, Event-Copy-Signal | F10 erfüllt |
| **P9** | Fremdplugin-Signal dokumentieren, Performance-Tuning, Lasttest mit synthetischen Daten, README/Docs, Release-Vorbereitung | v1.0 |

---

## 8. Explizit **nicht** in v1

- Freitext-SQL oder Freitext-ORM-Pfade in der UI
- Diagramme/Charts, Pivot-Tabellen
- Beliebig tief verschachtelte Filterlogik
- Cross-Event-Reports im Editor (nur Multi-Event-Exporter über pretix-Bordmittel)
- Eigene E-Mail-Versandlogik für Reports (macht pretix' Scheduled Export)
- Eigene Scheduler-/Cron-Implementierung

---

## 9. Statusblock nach jeder Phase

```
## Status Phase <N>
Erledigt: ...
Getroffene Entscheidungen: ... (mit Begründung)
Abweichungen vom Prompt: ... (mit Begründung)
Offene Fragen an dich: ...
Nächster Schritt: ...
Dateien geändert: ...
Tests: <n> passed
```

---

## 10. Vorlage für `CLAUDE.md` im Repo

```markdown
# pretix-custom-reports

Plugin für konfigurierbare Reports über Bestellungen/Positionen in pretix.

## Grundregeln
- pretix-APIs immer im installierten Source verifizieren, nie aus dem Gedächtnis.
- Feld-Zugriff ausschließlich über die Registry (`registry.py`). Nie ORM-Pfade
  aus gespeichertem oder importiertem JSON verwenden.
- Alle Querysets auf das aktuelle Event/den Organizer einschränken; django-scopes beachten.
- Ausgabeformate über `ListExporter`, keine eigene CSV/XLSX-Erzeugung.
- Kein eigener Scheduler — Terminierung läuft über pretix Scheduled Exports.
- Neue Strings immer englisch + `de`-Katalog aktualisieren.

## Befehle
- Tests: `pytest`
- Lint: `flake8 . && isort -c . && black --check .`
- Übersetzungen: `python setup.py build`  # bzw. Projektstandard

## Architektur
Siehe `docs/architektur.md`. Erweiterung durch Fremdplugins: `docs/extending.md`.
```
