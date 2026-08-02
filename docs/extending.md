# Felder aus anderen Plugins beisteuern

`pretix-custom-reports` sendet ein `EventPluginSignal`, über das andere Plugins
eigene Spalten in den Report-Builder einhängen können (`SPEC.md` F5,
`docs/adr/0002-registry.md` Abschnitt 6).

Der kanonische Import ist:

```python
from pretix_custom_reports.registry.signals import register_report_fields
```

Das Signalobjekt liegt bewusst in `registry/signals.py` und nicht in
`pretix_custom_reports/signals.py`: dort liegen unsere eigenen *Empfänger*, und
diese Datei ändert sich mit unserer Verdrahtung. Der Importpfad für
Fremdplugins tut das nicht. `contracts/protocols.py` deklariert nur den
**Namen** (`REGISTER_FIELDS_SIGNAL_NAME`), nicht das Objekt; beide sind per
Test aneinander gebunden (`tests/test_registry_signal.py`).

---

## 1. Ein vollständiges Beispielplugin

Der folgende Code ist die um Erklärungen ergänzte Fassung des Beispielplugins
aus `tests/test_registry_signal.py`. Dort läuft es mit echter Annotation gegen
eine echte Datenbank, wird auf beiden Report-Basen veröffentlicht, gefiltert
und sortiert.

```python
# myplugin/reports.py
from django.db.models import OuterRef, Subquery
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from pretix_custom_reports.contracts import (
    Base,
    DataType,
    FieldContext,
    Operator,
    ReportField,
    plugin_field_key,
)
from pretix_custom_reports.registry.signals import register_report_fields

from .models import Zone

APP_LABEL = "pretix_myplugin"      # dein Django-App-Label
ALIAS = "pcr_myplugin_zone"        # Alias deiner Annotation, eigenes Präfix


def zone_annotation(ctx: FieldContext):
    """``{alias: expression}`` -- genau wie ein Kernfeld.

    ``ctx.event`` ist die Veranstaltung, ``ctx.base`` die Report-Basis. Über
    den Kontext kommt ein Plugin an alles, was es für eine
    veranstaltungsspezifische Subquery braucht, ohne dass die Registry etwas
    darüber wissen muss.
    """
    return {
        ALIAS: Subquery(
            Zone.objects.filter(
                event=ctx.event,
                order_id=OuterRef("pk" if ctx.base is Base.ORDER else "order_id"),
            ).values("name")[:1]
        )
    }


@receiver(register_report_fields, dispatch_uid="myplugin_report_fields")
def report_fields(sender, base, **kwargs):
    """``sender`` ist das Event, ``base`` die angefragte Basis als String."""
    return [
        ReportField(
            key=plugin_field_key(APP_LABEL, "zone"),   # plugin.pretix_myplugin.zone
            label=_("Zone"),
            group=_("My plugin"),
            datatype=DataType.STRING,
            bases=(Base.coerce(base),),
            orm_path=ALIAS,
            annotation=zone_annotation,
            filter_operators=(Operator.EXACT, Operator.CONTAINS),
            sortable=True,
            provider=APP_LABEL,
        )
    ]
```

Der Empfänger wird wie jeder pretix-Empfänger beim Import verbunden, also aus
der `ready()`-Methode deiner `AppConfig` heraus:

```python
class PluginApp(PluginConfig):
    name = "pretix_myplugin"

    def ready(self):
        from . import reports  # noqa
```

Dein Plugin muss für die Veranstaltung **aktiviert** sein, sonst feuert der
Empfänger nicht. Das ist gewollt: `register_report_fields` ist ein
`EventPluginSignal`, ein abgeschaltetes Plugin darf keine Spalten beisteuern.

---

## 2. Regeln

| Regel | Warum |
| --- | --- |
| `sender` ist das `Event`, `base` die Basis als String (`"order"` / `"orderposition"`). Gib die Felder für **diese** Basis zurück. | Die Feldmenge unterscheidet sich pro Basis. |
| Der Key **muss** `plugin.<app_label>.<name>` sein — benutze `plugin_field_key()`. | Django-App-Labels sind pro Installation eindeutig, damit ist eine Kollision zwischen zwei Plugins nicht konstruierbar. |
| `provider` **muss** dasselbe App-Label sein. | Sonst könnte ein Plugin Felder unter dem Präfix eines anderen parken und dieses Präfix übernehmen, sobald das andere Plugin installiert wird. |
| Gib eine Liste zurück. `None` ist erlaubt und heißt „nichts". | |
| Deklariere entweder `orm_path`, oder `annotation` **plus** `orm_path` (den Alias), oder nur `value_getter`. | Ein `value_getter`-Feld ist nur eine Anzeigespalte: es kann nicht gefiltert und nicht sortiert werden, und `filter_operators`/`sortable` darauf sind ein Vertragsfehler, keine stille Abwertung. |
| Alias-Namen: eigenes Präfix benutzen. | Der Kern benutzt `pcr_*`; pretix selbst `payment_sum`, `pending_sum_t`, `pcnt`. |
| Eine `annotation` darf **nicht** pro Zeile abfragen. `Subquery`/`Coalesce` benutzen. | Reports laufen gegen Veranstaltungen mit sechsstelligen Positionszahlen. |
| Ein `value_getter` darf die Datenbank **nicht** anfassen. Was er braucht, über `select_related`/`prefetch_related` deklarieren. | Sonst entsteht ein N+1 über die gesamte Exportdatei. |

---

## 3. Was abgelehnt wird

Die 15 Kern-Namespaces sind gesperrt (`contracts.RESERVED_NAMESPACES`):

```
order  position  invoice_address  item     variation  subevent  seat
voucher  discount  payment  refund  checkin  answer  meta  computed
```

Ein Feld darin wird verworfen — der Kern gewinnt. Die Prüfungen laufen in
dieser Reihenfolge, jede mit einem stabilen `reason`-Code:

| Fall | `reason` |
| --- | --- |
| Key in einem der 15 Kern-Namespaces | `reserved_namespace` |
| `provider` passt nicht zum App-Label im Key | `wrong_provider` |
| Feld deklariert die angefragte Basis nicht | `unsupported_base` |
| Key existiert schon (Kern oder früheres Plugin) | `duplicate_key` |
| Rückgabe ist kein `ReportField` | `not_a_field` |
| Empfänger wirft eine Exception | `receiver_failed` |

Zwischen zwei Plugins gewinnt das erste — und „erste" ist reproduzierbar, weil
pretix Empfänger nach `(is_core, __module__, __name__)` sortiert
(`pretix/base/signals.py`).

Das Signal wird mit `send_robust` verschickt: ein fehlerhaftes Fremdplugin
nimmt den Report-Editor nicht mit.

**Nichts davon passiert still.** Jede Ablehnung geht als `WARNING` ins Log und
erscheint in den Diagnosedaten:

```python
from pretix_custom_reports.registry.library import field_registry

diag = field_registry().diagnostics(event, "orderposition")
diag.field_count      # veröffentlichte Felder
diag.providers        # ("core", "pretix_myplugin", ...)
diag.skipped          # verworfene Einträge mit reason-Code
```

---

## 4. Cache

Die Registry hält einen Cache pro Veranstaltung und Basis und invalidiert ihn
über `post_save`/`post_delete` auf `Question`, `Item`, `ItemCategory`,
`SubEvent`, `Discount` und `EventMetaProperty`
(`docs/adr/0002-registry.md` Abschnitt 7).

Für die Felder eines Fremdplugins heißt das: Sie werden zusammen mit den
Kernfeldern gecacht, aber **ihre eigenen Datenänderungen invalidieren nichts**.
Solange dein Empfänger für ein Event immer dieselben Felder liefert — das ist
der Normalfall — ist das ohne Folgen. Wenn deine *Feldliste* von deinen eigenen
Modellen abhängt, invalidiere beim Ändern selbst:

```python
from pretix_custom_reports.registry.cache import (
    invalidate_event, invalidate_organizer,
)

invalidate_event(event.pk)              # eine Veranstaltung
invalidate_organizer(organizer.pk)      # alle Veranstaltungen eines Veranstalters
```

Ein Wechsel der aktiven Plugins invalidiert ohnehin: `cache_token()` enthält
`event.plugins`.

---

## 5. Grenzen, die bewusst so sind

* **Kein Zugriff auf ORM-Pfade aus gespeichertem JSON.** Deine `orm_path`-Angabe
  ist Code, kein Datenwert. Sie kommt aus deinem `ReportField`, nie aus einer
  Definition oder einer importierten Datei (`CLAUDE.md` Regel 2).
* **Keine Registry ohne Event.** `field_registry()` verlangt ein gespeichertes
  Event; die Registry öffnet selbst **keinen** django-scopes-Scope
  (`docs/adr/0002-registry.md` Abschnitt 8). Im Control-Panel ist einer offen,
  in einem Management-Command oder Celery-Task musst du ihn selbst öffnen.
* **Portabilität.** Ein Report, der dein Feld benutzt, bleibt beim Export in eine
  Datei und beim Kopieren einer Veranstaltung erhalten. Ist dein Plugin im Ziel
  nicht installiert, löst der Key dort nicht auf: Der Editor zeigt eine Warnung,
  der Export scheitert mit einer verständlichen Meldung, und die Spalte
  verschwindet **nicht** stillschweigend aus der gespeicherten Definition.
