> **ARCHIVIERT — 2026-08-02, `integrator` (Welle 4).** Nicht an mich
> adressiert; beim Aufräumen von `handoff/requests/` gegen den Code geprüft und
> als umgesetzt befunden (`models.py`: `identifier` mit `IDENTIFIER_RE` und beiden `UniqueConstraint`s `pcr_uniq_identifier_event`/`_orga`). Falls der ursprüngliche
> Empfänger anderer Meinung ist: Datei zurückschieben.

# contract-architect → persistence-dev: stabiler `identifier` am Report-Modell

**Welle:** 0c → 1
**Quelle:** `docs/adr/0001-contracts.md` Abschnitt 5.1 (dort steht die Begründung)
**Grund für dieses Dokument:** Die Entscheidung betrifft dein Modell und deine
Migration. Sie steht in der ADR, aber du sollst sie nicht darin suchen müssen.

## Was gebraucht wird

Zusätzlich zu dem Feldvorschlag aus `SPEC.md` Abschnitt 5 braucht
`ReportDefinition` ein Feld `identifier`, modelliert nach `Question.identifier`
(`pretix/base/models/items.py:1683-1694`, `1789-1812`):

```python
from pretix_custom_reports.contracts import (
    IDENTIFIER_MAX_LENGTH,   # 190
    IDENTIFIER_RE,           # ^[a-zA-Z0-9.\-_]+$
    SCHEMA_VERSION,
    validate_identifier,
)
```

- `CharField(max_length=IDENTIFIER_MAX_LENGTH)` mit `RegexValidator(IDENTIFIER_RE)`
- eindeutig **pro Event**: `UniqueConstraint(fields=["event", "identifier"])`
- eindeutig **pro Organizer** für Vorlagen (`event IS NULL`):
  `UniqueConstraint(fields=["organizer", "identifier"], condition=Q(event__isnull=True))`
- wird in `save()` erzeugt, wenn leer — pretix' Vorbild ist ein 8-stelliger Code
  aus `ABCDEFGHJKLMNPQRSTUVWXYZ3789` mit Kollisionsschleife
- bleibt bei Event-Kopie und beim Instanziieren einer Organizer-Vorlage
  **erhalten**; nur bei Kollision im Ziel ein Suffix anhängen

Der Exporter referenziert Reports über diesen Wert, nicht über den PK
(`contracts.EXPORT_FORM_REPORT_KEY == "report"`).

## Zwei weitere Punkte, die dein Modell betreffen

1. **`schema_version` steht im `definition`-JSON selbst** (der Validator
   verlangt es). Wenn du zusätzlich eine Modellspalte willst, ist das eine
   denormalisierte Kopie für indizierte Abfragen; maßgeblich bleibt das JSON.
   `contracts.SCHEMA_VERSION` ist der aktuelle Wert.

2. **Validieren beim Speichern: nur Struktur.** In `forms.py` / `models.py`:

   ```python
   from pretix_custom_reports.contracts import (
       DefinitionValidationError, validate_definition,
   )

   try:
       validate_definition(self.cleaned_data["definition"])
   except DefinitionValidationError as e:
       raise ValidationError([i.message for i in e.issues])
   ```

   **Nicht** gegen die Registry prüfen (`SPEC.md` F9, ADR 0001 Abschnitt 4).
   Eine gespeicherte Definition darf einen Key enthalten, der heute nicht
   auflösbar ist (umbenannte Frage) und morgen wieder. Registry-Prüfung findet
   beim Import, im Editor und beim Ausführen statt — nicht beim Speichern.

## Log-Action-Types

Konstanten stehen in `contracts/protocols.py` und sind über
`from pretix_custom_reports.contracts import LOG_ACTION_ADDED, ...` erreichbar:
`pretix_custom_reports.report.added` / `.changed` / `.deleted` / `.executed` /
`.exported` / `.imported` / `.template_applied`.

Achtung: `log_action` maskiert `data`-Schlüssel, die `password`, `secret` oder
`api_key` als **Teilstring** enthalten, und mutiert das Dict dabei in place
(`pretix/base/models/base.py:153-163`). Keine solchen Schlüssel im Payload.
