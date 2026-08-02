> **ERLEDIGT — 2026-08-02, `integrator` (Welle 4).** Vollständig übernommen.
> Was daraus wohin ging und welche Entscheidungen dabei getroffen wurden, steht
> in `handoff/status/integrator.md`.

# portability-dev → integrator: `event_copy_data`-Empfänger

**Welle:** 2
**Betrifft:** `pretix_custom_reports/signals.py` (nur diese eine Datei)
**Logik:** liegt fertig in `pretix_custom_reports/portability/eventcopy.py`
(`copy_reports_to_event`), getestet in `tests/test_portability.py` Abschnitt 8.
Der Empfänger unten ruft sie nur auf — bitte keine Logik in `signals.py`
nachbauen.

---

## 1. Kopierfertig

```python
from django.dispatch import receiver
from pretix.base.signals import event_copy_data


@receiver(event_copy_data, dispatch_uid="pretix_custom_reports_copy_data")
def copy_reports(sender, other, question_map=None, **kwargs):
    """Take the saved reports along when an event is copied (SPEC.md F10).

    ``sender`` is the NEW event, ``other`` the one being copied from
    (docs/pretix-api-notes.md section 3.3).
    """
    from .portability.eventcopy import copy_reports_to_event

    copy_reports_to_event(sender, other, question_map=question_map)
```

Das ist alles. `**kwargs` ist Pflicht: das Signal schickt sieben `*_map`-
Argumente (`tax_map`, `category_map`, `item_map`, `variation_map`,
`question_map`, `checkin_list_map`, `quota_map`), und ein Empfänger ohne
`**kwargs` würde mit `TypeError` sterben, sobald pretix eines ergänzt.

## 2. Warum die Signatur so aussieht

`docs/pretix-api-notes.md` Abschnitt 3.3, wörtlich aus
`pretix/base/signals.py:900-916` und `pretix/base/models/event.py:1209-1213`:

- `sender` = **neues** Event, `other` = Quelle. Beides wird an
  `copy_reports_to_event(target_event, source_event, ...)` in genau dieser
  Reihenfolge weitergereicht.
- `question_map` bildet **alte PK → neues `Question`-Objekt** ab, und die
  neue Frage hat denselben `identifier`. Genau deshalb muss unsere Kopie
  nichts über Primärschlüssel übersetzen: unsere Feld-Keys sind
  `answer.<identifier>`. Die Map geht trotzdem mit in den Log-Eintrag
  (Anzahl der mitgewanderten Fragen), damit im Zweifel nachvollziehbar ist,
  wie viele Fragen die Kopie hatte.
- `event_copy_data` ist ein `EventPluginSignal`, feuert also nur, wenn unser
  Plugin für das **neue** Event aktiv ist. Das ist es: `copy_data_from` setzt
  `self.plugins = other.plugins` und speichert **vor** dem `send`
  (`event.py:900-906`). Für ein Quellevent ohne unser Plugin gibt es nichts zu
  kopieren.

## 3. Was der Empfänger garantiert (und was nicht)

- **Kein Report geht verloren.** Ein Report, dessen Feld-Keys im neuen Event
  nicht auflösen, wird trotzdem kopiert — unverändert. Ein nicht auflösbarer
  Key ist laut `models.py` und SPEC.md F9 ein legaler Speicherzustand; der
  Editor zeigt ihn als Warnung, der Exporter scheitert sauber. Spalten still
  wegzuwerfen wäre bei einer Event-Kopie die schlechtere Wahl, und den Report
  ganz auszulassen die schlechteste.
- **Auflösbare Umbenennungen werden übersetzt.** Wenn im Zielevent eine Frage
  anders geschrieben ist (`tshirt-size` vs. `tshirt_size`), wandert der Key
  mit — über dieselbe Auflösungsschicht wie beim Datei-Import
  (`ResolutionStrategy.KEEP`).
- **Der Auflösungsbericht landet im Log**, als Feld `copied_from_event` im
  `LOG_ACTION_ADDED`-Eintrag der Kopie. Kein neuer Action-Type nötig.
- **Ein einzelner kaputter Report bricht die Event-Kopie nicht ab.**
  Ausnahmen pro Report werden gesammelt (`CopyResult.failed`), nicht
  weitergeworfen — eine halb kopierte Veranstaltung wäre der teurere Fehler.
- **Nicht kopiert werden Organizer-Vorlagen.** Sie hängen am Organizer, nicht
  am Event; bei einer Kopie innerhalb desselben Organizers sind sie ohnehin
  schon da.

## 4. Der Fallstrick, den der Code schon berücksichtigt

pretix kann ein Event **in einen anderen Organizer** kopieren
(`Event.copy_data_from`, Variable `is_cross_organizer`). Der aktive
django-scopes-Scope ist dabei der **Ziel**organizer. Ein Zugriff über
`other.custom_reports.all()` würde deshalb stillschweigend **null** Reports
liefern — der Nutzer fände ein Event ohne Reports und nirgends eine
Fehlermeldung. `copy_reports_to_event` liest die Quellreports darum unter
`scopes_disabled()` mit hartem `event=source_event`-Filter (dieselbe Bauweise
wie `ReportDefinition._identifier_taken` in `models.py`). Test:
`test_an_event_copy_across_organizers_still_finds_the_reports`.

Falls der Empfänger irgendwann in einen Celery-Task wandert: dort ist gar kein
Scope aktiv, dann braucht der **Registry**-Zugriff einen
(`registry/library.py` öffnet bewusst keinen). Heute läuft alles synchron im
Control-Request, also innerhalb des Middleware-Scopes.

## 5. Prüfen nach dem Verdrahten

```bash
pytest tests/test_portability.py -q -k event_copy
```

Diese vier Tests decken die Logik ohne das Signal ab. Ein Test, der das Signal
selbst auslöst (`Event.copy_data_from`), gehört nach dem Verdrahten in
`tests/test_integration.py` (test-engineer, Welle 3) — er braucht ein
vollständiges Quellevent mit Produkten, Quotas und Fragen, und das ist
Integrationstest-Gebiet:

```python
with scopes_disabled():
    new_event.copy_data_from(old_event)
assert new_event.custom_reports.count() == old_event.custom_reports.count()
```
