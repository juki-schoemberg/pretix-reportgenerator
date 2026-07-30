# registry-dev → integrator: eine Import-Zeile in `signals.py`, plus `docs/extending.md`

**Welle:** 1 → 4 (bzw. beim ersten Merge)
**Quelle:** `docs/adr/0002-registry.md` Abschnitt 7 (Cache), Abschnitt 6 (Signal)
**Warum du:** `pretix_custom_reports/signals.py` und `docs/extending.md` gehören
dir (`ORCHESTRIERUNG.md` Abschnitt 5). Ich habe sie nicht angefasst.

---

## 1. Pflicht: `signals.py` muss `registry.cache` importieren

Die Registry hält einen Cache pro Event und invalidiert ihn über
`post_save`/`post_delete`-Empfänger auf `Question`, `Item`, `ItemCategory`,
`SubEvent`, `Discount` und `EventMetaProperty`. Diese Empfänger werden beim
**Import** von `pretix_custom_reports/registry/cache.py` verbunden.

Ohne diesen Import sind sie erst verbunden, sobald irgendjemand die Registry
zum ersten Mal berührt. Eine Frage, die vorher umbenannt wird, invalidiert dann
nichts — und ein Report exportiert eine Spalte, die es nicht mehr gibt. Das ist
der einzige Weg, auf dem der Cache falsche Daten liefern kann.

Kopierfertig, ans Ende der Imports von `pretix_custom_reports/signals.py`:

```python
# Connects the registry's cache invalidation receivers (post_save/post_delete on
# Question, Item, ItemCategory, SubEvent, Discount, EventMetaProperty). They are
# wired up on import; without this line they would only be connected once
# something happens to touch the registry, and a question renamed before that
# would not invalidate anything. See docs/adr/0002-registry.md section 7.
from pretix_custom_reports.registry import cache as registry_cache  # noqa: F401
```

`connect_invalidation_receivers()` benutzt `dispatch_uid`, ein zweiter Import ist
also harmlos (`tests/test_registry_cache.py::test_receivers_are_connected_once`).

**Alternative, falls du Imports in `signals.py` vermeiden willst:** derselbe
Import in `apps.py` in `AppConfig.ready()`. Beides gehört dir, mir ist die
Stelle gleich — nur passieren muss es.

---

## 2. Pflicht: nichts weiter zu verdrahten

`register_report_fields` ist **unser eigenes** Signal
(`pretix_custom_reports/registry/signals.py`). Es braucht keinen Empfänger von
uns und keinen Eintrag in `signals.py`. Der Platzhalterkommentar in
`signals.py` Zeile 60-61 („wave 1 registry-dev own EventPluginSignal
register_report_fields — declared in contracts/") ist in einem Punkt ungenau:
`contracts/protocols.py` deklariert nur den **Namen**
(`REGISTER_FIELDS_SIGNAL_NAME`), nicht das Signalobjekt. Das Objekt liegt in
`registry/signals.py`; `registry.signals.SIGNAL_NAME` und die Contract-Konstante
sind per Test aneinander gebunden.

Der kanonische Import für Fremdplugins ist:

```python
from pretix_custom_reports.registry.signals import register_report_fields
```

---

## 3. `docs/extending.md`: lauffähiges Beispiel

`SPEC.md` F5 verlangt „Dokumentiere dieses Signal in `docs/extending.md` mit
lauffähigem Beispiel". Das Folgende ist getestet
(`tests/test_registry_signal.py`, dort als Beispielplugin mit echter Annotation
ausgeführt) und kann übernommen werden.

````markdown
# Felder aus anderen Plugins beisteuern

`pretix-custom-reports` sendet ein `EventPluginSignal`, über das andere Plugins
eigene Spalten in den Report-Builder einhängen können.

```python
from django.db.models import OuterRef, Subquery
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from pretix_custom_reports.contracts import (
    Base, DataType, Operator, ReportField, plugin_field_key,
)
from pretix_custom_reports.registry.signals import register_report_fields

APP_LABEL = "pretix_myplugin"       # dein Django-App-Label
ALIAS = "pcr_myplugin_zone"         # Alias deiner Annotation


def zone_annotation(ctx):
    """ctx.event ist das Event, ctx.base die Report-Basis."""
    return {
        ALIAS: Subquery(
            MyModel.objects.filter(
                event=ctx.event,
                order_id=OuterRef("pk" if ctx.base is Base.ORDER else "order_id"),
            ).values("zone")[:1]
        )
    }


@receiver(register_report_fields, dispatch_uid="myplugin_report_fields")
def report_fields(sender, base, **kwargs):
    return [
        ReportField(
            key=plugin_field_key(APP_LABEL, "zone"),      # plugin.pretix_myplugin.zone
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

## Regeln

| Regel | Warum |
| --- | --- |
| `sender` ist das `Event`, `base` die Basis als String (`"order"` / `"orderposition"`). Gib die Felder für **diese** Basis zurück. | Die Feldmenge unterscheidet sich pro Basis. |
| Der Key **muss** `plugin.<app_label>.<name>` sein — benutze `plugin_field_key()`. | Django-App-Labels sind pro Installation eindeutig, damit ist eine Kollision zwischen zwei Plugins nicht konstruierbar. |
| `provider` **muss** dasselbe App-Label sein. | Sonst könnte ein Plugin Felder unter dem Präfix eines anderen parken. |
| Gib eine Liste zurück. `None` ist erlaubt und heißt „nichts". | |
| Deklariere entweder `orm_path`, oder `annotation` **plus** `orm_path` (den Alias), oder nur `value_getter`. | Ein `value_getter`-Feld ist nur eine Anzeigespalte: es kann nicht gefiltert und nicht sortiert werden, und `filter_operators`/`sortable` darauf sind ein Fehler. |
| Alias-Namen: eigenes Präfix benutzen. | Der Kern benutzt `pcr_*`; pretix selbst `payment_sum`, `pending_sum_t`, `pcnt`. |
| Eine `annotation` darf **nicht** pro Zeile abfragen. `Subquery`/`Coalesce` benutzen. | Reports laufen gegen Events mit sechsstelligen Positionszahlen. |
| Ein `value_getter` darf die Datenbank **nicht** anfassen. Was er braucht, über `select_related`/`prefetch_related` deklarieren. | |

## Was abgelehnt wird

Die 15 Kern-Namespaces (`order`, `position`, `invoice_address`, `item`,
`variation`, `subevent`, `seat`, `voucher`, `discount`, `payment`, `refund`,
`checkin`, `answer`, `meta`, `computed`) sind gesperrt. Ein Feld darin wird
verworfen — der Kern gewinnt. Ebenso verworfen wird ein Feld, dessen `provider`
nicht zum App-Label im Key passt, dessen Key schon existiert, das die angefragte
Basis nicht deklariert, oder das kein `ReportField` ist. Wirft dein Empfänger
eine Exception, wird sie gefangen und der Report-Editor läuft weiter.

Nichts davon passiert still: jede Ablehnung geht als `WARNING` ins Log und
erscheint in `registry.diagnostics(event, base).skipped` mit einem stabilen
`reason`-Code.

Dein Plugin muss für das Event **aktiviert** sein, sonst feuert der Empfänger
nicht — das ist gewollt.
````

---

## 4. Neue Strings, die in den `de`-Katalog müssen

Alle in `pretix_custom_reports/registry/` mit `gettext_lazy` markiert, englisch.
Die Sammelstellen:

- `registry/groups.py` — 13 Gruppennamen (`GROUP_LABELS`)
- `registry/core.py` — Labels und `help_text` der Kernfelder
- `registry/computed.py` — 2 Feldlabels plus `help_text`
- `registry/questions.py` — `help_text`-Bausteine und
  `"Age at the event date: {question}"`
- `registry/meta.py` — ein `help_text`
- `registry/annotations.py` — `PAYMENT_STATE_CHOICES` (4 Werte)
