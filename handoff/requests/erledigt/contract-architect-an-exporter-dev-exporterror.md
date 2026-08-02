> **ARCHIVIERT — 2026-08-02, `integrator` (Welle 4).** Nicht an mich
> adressiert; beim Aufräumen von `handoff/requests/` gegen den Code geprüft und
> als umgesetzt befunden (`exporters.py` übersetzt an 14 Stellen in `ExportError`). Falls der ursprüngliche
> Empfänger anderer Meinung ist: Datei zurückschieben.

# contract-architect → exporter-dev: tote Report-Referenz muss `ExportError` werden

**Welle:** 0c → 2
**Quelle:** `docs/pretix-api-notes.md` Abschnitt 5.6 Fall B,
`docs/adr/0001-contracts.md` Abschnitt 5

## Das Problem

`export_form_data` wird beim Ausführen eines terminierten Exports **nicht
revalidiert** (`pretix/base/services/export.py:366-370`). Fehlt das
referenzierte Objekt — gelöschte Report-Definition, gelöschte Frage —, fängt
pretix das **nicht** ab. Der Ablauf ist dann
(`services/export.py:392-397`): fünf Celery-Retries à 120 Sekunden, danach eine
Fehlermail mit dem Text „Internal Error". Zehn Minuten Rechenzeit für eine
nichtssagende Meldung.

## Was zu tun ist

Alles, was aus einer gespeicherten Definition kommen kann, in `iterate_list`
bzw. beim Laden abfangen und in `ExportError` übersetzen:

```python
from pretix.base.services.export import ExportError

from pretix_custom_reports.contracts import (
    EXPORT_FORM_REPORT_KEY,   # "report"
    ContractError,
    ReportNotFoundError,
)

identifier = form_data.get(EXPORT_FORM_REPORT_KEY)
try:
    # immer eventgebunden nachschlagen, nie global -- sonst ist ein
    # manipuliertes export_form_data ein organizer-uebergreifendes Leck
    report = event.report_definitions.get(identifier=identifier)
except ObjectDoesNotExist:
    raise ExportError(
        f"The report '{identifier}' does not exist in event {event.slug}."
    )

try:
    compiled = compiler.compile(validate_definition(report.definition), event)
except ContractError as e:
    raise ExportError(str(e))
```

`ContractError` ist die gemeinsame Basisklasse von
`DefinitionValidationError`, `FieldResolutionError`, `CompilationError` und
`ReportNotFoundError` (`contracts/errors.py`). Ein `except ContractError`
genügt; die Meldungen sind so formuliert, dass sie in einer Fehlermail
brauchbar sind.

## Drei weitere Punkte aus den API-Notizen, die den Exporter betreffen

1. **`_format` muss exakt einer von vier Strings sein** (`xlsx`, `default`,
   `csv-excel`, `semicolon`). `ListExporter.render` hat keinen `else`-Zweig; ein
   anderer Wert liefert `None` und wird zu „Your export did not contain any
   data." (`docs/pretix-api-notes.md` Abschnitt 1, Fallstrick 1).
2. **`export_form_fields` in einem `ListExporter` nicht überschreiben** —
   `additional_form_fields` benutzen, sonst ist die `_format`-Auswahl weg
   (ebenda, Fallstrick 4).
3. **20-MB-Grenze bei terminierten Exporten** (`services/export.py:377-380`).
   `contracts.MAX_ROW_LIMIT` und `ReportOptions.row_limit` sind die Stellschraube,
   die dem Nutzer dafür zur Verfügung steht.

## Konstanten, die du benutzen sollst statt sie neu zu erfinden

`from pretix_custom_reports.contracts import ...`

| Konstante | Wert | Zweck |
| --- | --- | --- |
| `EXPORT_FORM_REPORT_KEY` | `"report"` | Schlüssel in `export_form_data` |
| `DEFAULT_CHUNK_SIZE` | `1000` | `iterator(chunk_size=...)` |
| `LOG_ACTION_EXECUTED` | `pretix_custom_reports.report.executed` | `log_action` |
| `PREVIEW_ROW_LIMIT` | `20` | nur Vorschau, nicht Export |

`CompiledReport.columns` enthält bereits **nur sichtbare** Spalten; versteckte
sind beim Kompilieren entfernt. `headers()` und `iter_rows()` passen direkt auf
`iterate_list`.
