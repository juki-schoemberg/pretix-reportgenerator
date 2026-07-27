# pretix-API-Notizen — verbindliche Referenz

**Erstellt von:** `pretix-researcher`, Welle 0b
**Gilt für:** pretix **2026.6.0** (Git-Tag `v2026.6.0`, Commit `fd565ecdb29c55a3e82dc15d94a848d193664caa`)

Diese Datei ist laut `CLAUDE.md` Regel 1 die **verbindliche Referenz** für alle
pretix-API-Fragen. Jede Aussage hier ist am installierten Source verifiziert.
Wenn eine Angabe hier von deiner Erinnerung abweicht: diese Datei gewinnt.
Wenn diese Datei von dem abweicht, was du im Source siehst: **Source gewinnt**,
dann bitte `handoff/requests/` schreiben.

## 0. Verifikation der Umgebung

```
$ pip show pretix
Name: pretix
Version: 2026.6.0
Location: D:\Projekte\juki\venv\Lib\site-packages
Editable project location: D:\Projekte\juki\pretix

$ python -c "import pretix; print(pretix.__file__); print(pretix.__version__)"
D:\Projekte\juki\pretix\src\pretix\__init__.py
2026.6.0

$ git -C D:\Projekte\juki\pretix log -1
fd565ecdb29c55a3e82dc15d94a848d193664caa  (HEAD, tag: v2026.6.0, origin/stable)
```

Es ist ein **Editable-Install des Klons** — der Source unter
`D:\Projekte\juki\pretix\src\pretix\` ist exakt der Code, der zur Laufzeit
ausgeführt wird. Alle Zeilenangaben in diesem Dokument beziehen sich auf diesen
Pfad; ich kürze ihn im Folgenden zu `pretix/`.

Zusätzlich relevante Paketversionen:

| Paket | Version | Fundstelle |
| --- | --- | --- |
| `django-scopes` | 2.0.0 | `pip show django-scopes` |
| `defusedcsv` | 3.0.0 | `pip show defusedcsv` |

Teile dieses Dokuments (Feldlisten von `Order`/`OrderPosition`) sind nicht nur
aus dem Source gelesen, sondern zusätzlich durch Introspektion des geladenen
Django-Modells erzeugt (`django.setup()` mit
`PRETIX_CONFIG_FILE=/d/Projekte/juki/pretix/src/pretix.cfg`). Das ist unten
jeweils markiert.

---

## 1. `pretix/base/exporter.py` — Exporter-Basisklassen

**Modul:** `pretix/base/exporter.py` (459 Zeilen gesamt)

### 1.1 `BaseExporter`

`pretix/base/exporter.py:58-199`

Konstruktor, **wörtlich** (Zeile 63):

```python
class BaseExporter:
    def __init__(self, event, organizer, permission_holder: PermissionHolder=None, progress_callback=lambda v: None):
```

Was der Konstruktor tut (Zeilen 72-87):

```python
        self.event = event
        self.organizer = organizer
        self.progress_callback = progress_callback
        self.is_multievent = isinstance(event, QuerySet)
        self.permission_holder = permission_holder
        if isinstance(event, QuerySet):
            self.events = event
            self.event = None
            e = self.events.first()
            self.timezone = e.timezone if e else ZoneInfo(settings.TIME_ZONE)
        else:
            self.events = Event.objects.filter(pk=event.pk)
            self.timezone = event.timezone

        if hasattr(self, 'organizer_required_permission'):
            raise TypeError("Deprecated attribute organizer_required_permission no longer supported.")
```

**Attribute / Properties** (alle als `@property` in der Basisklasse definiert,
in Subklassen üblicherweise als schlichtes Klassenattribut überschrieben):

| Name | Zeilen | Default | Bedeutung (Docstring gekürzt) |
| --- | --- | --- | --- |
| `verbose_name -> str` | 92-98 | `raise NotImplementedError()` | Menschenlesbarer Name |
| `description -> str` | 100-105 | `""` | Beschreibung |
| `category -> Optional[str]` | 107-112 | `None` | Kategoriename oder `None` |
| `featured -> bool` | 114-119 | `False` | Wird hervorgehoben |
| `repeatable_read -> bool` | 121-131 | `True` | Lauf in REPEATABLE-READ-Transaktion |
| `identifier -> str` | 133-140 | `raise NotImplementedError()` | "short and unique … only lowercase letters" |
| `export_form_fields -> dict` | 142-166 | `{}` | Dict `feldname -> Django-Formfeld` |

Wörtlich zu `identifier` (133-140):

```python
    @property
    def identifier(self) -> str:
        """
        A short and unique identifier for this exporter.
        This should only contain lowercase letters and in most
        cases will be the same as your package name.
        """
        raise NotImplementedError()  # NOQA
```

Wörtlich zu `repeatable_read` (121-131) — für uns wichtig, weil ein
Report-Export lange laufen kann:

```python
    @property
    def repeatable_read(self) -> bool:
        """
        If ``True``, this exporter will be run in a REPEATABLE READ transaction. ...
        We recommend to disable this for exporters that take very long to run ...
        Defaults to ``True`` for now, but default may change in future versions.
        """
        return True
```

**Methoden:**

```python
    def render(self, form_data: dict) -> Tuple[str, str, Optional[bytes]]:     # 168
    def available_for_user(self, user) -> bool:                                 # 185, default: return True
    @classmethod
    def get_required_event_permission(cls) -> Optional[str]:                    # 192-199, default: return 'event.orders:read'
```

`render` gibt `(filename, mimetype, content_bytes)` zurück. Aus dem Docstring
(168-182), wörtlich und für uns **zentral**:

> Note: If you use a ``ModelChoiceField`` (or a ``ModelMultipleChoiceField``), the
> ``form_data`` will not contain the model instance but only it's primary key (or
> a list of primary keys) for reasons of internal serialization when using background
> tasks.

Der Docstring erwähnt außerdem einen `output_file`-Parameter — **die Signatur von
`BaseExporter.render` hat ihn nicht**, nur `ListExporter.render` /
`MultiSheetListExporter.render` (siehe 1.3/1.4). Siehe Abschnitt 12 (Doku vs.
Code).

### 1.2 `OrganizerLevelExportMixin`

`pretix/base/exporter.py:202-215`

```python
class OrganizerLevelExportMixin:
    @classmethod
    def get_required_event_permission(cls):
        raise TypeError("required_event_permission may not be called on OrganizerLevelExportMixin")

    @classmethod
    def get_required_organizer_permission(cls) -> Optional[str]:
        raise NotImplementedError()
```

Für organizer-weite Exporte, die *nicht* über eine Event-Liste laufen (z. B.
Gift-Card-Transaktionen). Kernbeispiel:
`pretix/base/exporters/orderlist.py:1254-1263`.

**Für unser Plugin voraussichtlich nicht relevant** — unsere Reports laufen über
Bestellungen, also über die Event-Liste. Wenn wir organizer-weite
Report-Vorlagen exportieren wollen, dann trotzdem als normaler Multievent-Export
mit `get_required_event_permission() == 'event.orders:read'`, weil sonst die
Event-Filterung (`self.events`) nicht befüllt wird (`init_organizer_exporters`
setzt für `OrganizerLevelExportMixin` explizit `event=Event.objects.none()`,
`pretix/base/services/export.py:251-257`).

### 1.3 `ListExporter`

`pretix/base/exporter.py:218-336`

```python
class ListExporter(BaseExporter):
    ProgressSetTotal = namedtuple('ProgressSetTotal', 'total')

    @property
    def export_form_fields(self) -> dict:                     # 221-238
    @property
    def additional_form_fields(self) -> dict:                 # 240-242, default: return {}
    def iterate_list(self, form_data):                        # 244-245, raise NotImplementedError()
    def get_filename(self):                                   # 247-248, return 'export'
    def get_csv_encoding(self):                               # 250-251, return 'utf-8'
    def _render_csv(self, form_data, output_file=None, **kwargs):   # 253
    def prepare_xlsx_sheet(self, ws):                         # 294-295, pass
    def _render_xlsx(self, form_data, output_file=None):      # 297
    def render(self, form_data: dict, output_file=None) -> Tuple[str, str, bytes]:   # 328
```

`export_form_fields` ist in `ListExporter` **bereits implementiert** und darf in
Subklassen normalerweise *nicht* überschrieben werden — eigene Felder kommen über
`additional_form_fields` dazu (221-238):

```python
    @property
    def export_form_fields(self) -> dict:
        ff = OrderedDict(
            [
                ('_format',
                 forms.ChoiceField(
                     label=_('Export format'),
                     choices=(
                         ('xlsx', _('Excel (.xlsx)')),
                         ('default', _('CSV (with commas)')),
                         ('csv-excel', _('CSV (Excel-style)')),
                         ('semicolon', _('CSV (with semicolons)')),
                     ),
                 )),
            ]
        )
        ff.update(self.additional_form_fields)
        return ff
```

`render` dispatcht auf `_format` (328-336):

```python
    def render(self, form_data: dict, output_file=None) -> Tuple[str, str, bytes]:
        if form_data.get('_format') == 'xlsx':
            return self._render_xlsx(form_data, output_file=output_file)
        elif form_data.get('_format') == 'default':
            return self._render_csv(form_data, quoting=csv.QUOTE_NONNUMERIC, delimiter=',', output_file=output_file)
        elif form_data.get('_format') == 'csv-excel':
            return self._render_csv(form_data, dialect='excel', output_file=output_file)
        elif form_data.get('_format') == 'semicolon':
            return self._render_csv(form_data, dialect='excel', delimiter=';', output_file=output_file)
```

`iterate_list(form_data)` ist ein **Generator**. Er yieldet Listen (eine pro
Zeile). Ein `ProgressSetTotal`-Namedtuple im Stream setzt die Gesamtzahl für die
Fortschrittsanzeige und wird nicht ausgegeben (260-263, 279-282, 307-310).

**Minimalbeispiel aus dem Core** (`pretix/base/exporters/waitinglist.py:39-44`,
`91-128`, `167-187`, `190-197`, gekürzt):

```python
class WaitingListExporter(ListExporter):
    identifier = 'waitinglist'
    verbose_name = _('Waiting list')
    category = pgettext_lazy('export_category', 'Waiting list')
    description = _('Download a spread sheet with all your waiting list data.')
    repeatable_read = False

    def iterate_list(self, form_data):
        entries = WaitingListEntry.objects.filter(event__in=self.events)...
        headers = [_('Date'), _('Name'), ...]
        yield headers
        yield self.ProgressSetTotal(total=len(entries))
        for entry in entries:
            yield [ ... ]

    @property
    def additional_form_fields(self):
        return OrderedDict([('status', forms.ChoiceField(...))])

    def get_filename(self):
        if self.is_multievent:
            event = self.events.first()
            slug = event.organizer.slug if len(self.events) > 1 else event.slug
        else:
            slug = self.event.slug
        return '{}_waitinglist'.format(slug)


@receiver(register_data_exporters, dispatch_uid="exporter_waitinglist")
def register_waitinglist_exporter(sender, **kwargs):
    return WaitingListExporter


@receiver(register_multievent_data_exporters, dispatch_uid="multiexporter_waitinglist")
def register_multievent_i_waitinglist_exporter(sender, **kwargs):
    return WaitingListExporter
```

### 1.4 `MultiSheetListExporter`

`pretix/base/exporter.py:339-458`

```python
class MultiSheetListExporter(ListExporter):
    @property
    def sheets(self):                                         # 341-343, raise NotImplementedError()
    @property
    def export_form_fields(self) -> dict:                     # 345-366
    def iterate_list(self, form_data):                        # 368-369, pass
    def iterate_sheet(self, form_data, sheet):                # 371-375
    def _render_sheet_csv(self, form_data, sheet, output_file=None, **kwargs):   # 377
    def _render_xlsx(self, form_data, output_file=None):      # 416
    def render(self, form_data: dict, output_file=None) -> Tuple[str, str, bytes]:   # 447
```

`sheets` ist eine Liste von `(key, label)`-Tupeln. `iterate_sheet` dispatcht per
`getattr` auf `iterate_<key>` (371-375):

```python
    def iterate_sheet(self, form_data, sheet):
        if hasattr(self, 'iterate_' + sheet):
            yield from getattr(self, 'iterate_' + sheet)(form_data)
        else:
            raise NotImplementedError()  # noqa
```

Analog wird `prepare_xlsx_sheet_<key>` gesucht (421-422). Die `_format`-Choices
sind hier `xlsx` (alle Sheets kombiniert) sowie `"<sheet>:default"`,
`"<sheet>:excel"`, `"<sheet>:semicolon"` (345-366, Dispatch in 447-458).

Kernbeispiel: `pretix/base/exporters/orderlist.py:85-107` (`OrderListExporter`,
Sheets `orders` / `positions` / `fees`).

### Fallstricke (Exporter)

1. **`render` gibt bei unbekanntem `_format` implizit `None` zurück.**
   `ListExporter.render` hat keinen `else`-Zweig (328-336). Der aufrufende Task
   macht daraus `ExportError('Your export did not contain any data.')`
   (`pretix/base/services/export.py:106-109`) — eine irreführende Meldung. Wenn
   wir `_format` je selbst setzen (z. B. bei Scheduled Exports aus gespeicherten
   Daten), muss der Wert exakt einer der vier Strings sein.
2. **`return None` aus `render` bedeutet "leerer Export"** und wird zu
   `ExportError` bzw. bei Scheduled Exports zu `ExportEmptyError`
   (`services/export.py:371-374`) — Letzteres zählt als "soft" Fehler und
   erhöht `error_counter` **nicht**.
3. **`Decimal` wird nur im CSV-Pfad lokalisiert**, nicht im XLSX-Pfad:
   `localize(f) if isinstance(f, Decimal) else f` steht in `_render_csv`
   (264-267, 283-286) und `_render_sheet_csv` (388-391, 405-408), aber
   `_render_xlsx` schreibt Werte roh (311-313). XLSX bekommt also echte Zahlen,
   CSV lokalisierte Strings. Für Testvergleiche relevant.
4. **`export_form_fields` in einem `ListExporter` zu überschreiben killt die
   `_format`-Auswahl** — dann greift der `render`-Dispatch nicht mehr. Immer
   `additional_form_fields` benutzen.
5. `identifier` soll laut Docstring nur Kleinbuchstaben enthalten; erzwungen wird
   das nirgends. Es wird aber als `ScheduledExport.export_identifier`
   (`max_length=190`) und als Form-Prefix im Control-UI benutzt
   (`control/views/orders.py:2695-2699`), taucht also in HTML-Feldnamen der Form
   `"<identifier>-<feldname>"` auf. Bindestriche im Identifier sind damit
   unschön, aber nicht verboten.
6. Der Konstruktor **kollidiert mit dem Aufrufmuster im Management-Command**:
   `pretix/base/management/commands/export.py:108` ruft
   `ex = response(e, o, report_status)` auf — der dritte Positionsparameter ist
   laut Signatur aber `permission_holder`, nicht `progress_callback`. Sieht nach
   einem Bug im CLI aus. Wir übernehmen dieses Muster **nicht**; immer
   Keyword-Argumente.
7. `self.event` ist bei Multievent-Exporten **`None`** (Zeile 79). Wer
   `self.event.settings` schreibt, kracht im Organizer-Export. Immer über
   `self.events` gehen bzw. `self.is_multievent` prüfen.

---

## 2. CSV- und XLSX-Injection: was pretix bereits erledigt

**Antwort: Ja, `ListExporter` neutralisiert CSV-Injection — automatisch und ohne
Zutun der Subklasse.**

Fundstelle `pretix/base/exporter.py:42`:

```python
from defusedcsv import csv
```

Das ist ein Drop-in-Ersatz für das Stdlib-`csv`. Alle CSV-Pfade in
`exporter.py` (`csv.writer` in Zeilen 257, 276, 383, 402; `csv.QUOTE_NONNUMERIC`
in 332, 453) nutzen dieses Modul.

Die Neutralisierung selbst (`defusedcsv/csv.py:28-38`, Version 3.0.0, aus
`D:\Projekte\juki\venv\Lib\site-packages\defusedcsv\csv.py`):

```python
def _escape(payload):
    if payload is None:
        return payload
    if isinstance(payload, Number):
        return payload

    payload = str(payload)
    if payload and payload[0] in ('@', '+', '-', '=', '|', '%') and not re.match("^-?[0-9,\\.]+$", payload):
        payload = payload.replace("|", "\\|")
        payload = "'" + payload
    return payload
```

angewandt in `_ProxyWriter.writerow` / `writerows` (`defusedcsv/csv.py:41-54`).

**XLSX ebenfalls abgesichert**, über `SafeWorkbook`
(`pretix/base/exporter.py:51-53` importiert aus
`pretix/helpers/safe_openpyxl.py`; benutzt in `_render_xlsx`, Zeilen 298 und
417). Was `safe_openpyxl` tut (`pretix/helpers/safe_openpyxl.py:39-50`,
Modul-Docstring wörtlich):

> - It makes sure strings starting with = are treated as text, not as a formula, as openpyxl will
>   otherwise assume, which can be used for remote code execution.
> - It removes characters considered invalid by Excel to avoid exporter crashes.

Umsetzung: `SafeCell` setzt `c.data_type = TYPE_STRING`, wenn openpyxl
`TYPE_FORMULA` erkannt hat (`safe_openpyxl.py:75-84`), und
`remove_invalid_excel_chars` filtert per Regex XML-illegale Zeichen (52-72).
`SafeWorkbook.create_sheet` liefert `SafeWriteOnlyWorksheet` bzw.
`SafeWorksheet` (141-162).

### Fallstricke (Injection)

1. **Der Schutz hängt daran, dass wir `ListExporter` benutzen.** Sobald wir
   eigenes `csv.writer` aus der Stdlib oder eigenes `openpyxl.Workbook`
   verwenden, ist er weg. Das ist der technische Grund hinter `CLAUDE.md`
   Regel 6.
2. `_escape` lässt `Number`-Instanzen unangetastet. Wenn wir `Decimal` per
   `localize()` schon in einen String verwandelt haben, greift die Prüfung — bei
   negativen Zahlen als String (`-5`) rettet uns die Ziffern-Regex
   `^-?[0-9,\.]+$`. Bei einem lokalisierten Wert mit Leerzeichen als
   Tausendertrennzeichen greift diese Regex allerdings **nicht**, das Feld
   bekommt dann ein führendes `'`. Kosmetisch, nicht sicherheitsrelevant.
3. Der Schutz gilt für **Zelleninhalte**, nicht für Dateinamen. `get_filename()`
   fließt in `CachedFile.filename` und in Mail-Attachment-Namen. Dort keine
   ungeprüften Nutzereingaben durchreichen.

---

## 3. `pretix/base/signals.py` — Signalsystem

### 3.1 Signalklassen

| Klasse | Zeilen | Sender-Typ | Empfänger-Filter |
| --- | --- | --- | --- |
| `PluginSignal(Generic[T], django.dispatch.Signal)` | 144-250 | `self.type` | `is_receiver_active` |
| `EventPluginSignal(PluginSignal[Event])` | 253-274 | `Event` | Plugin muss für das Event aktiv sein |
| `OrganizerPluginSignal(PluginSignal[Organizer])` | 277-311 | `Organizer` | Plugin muss für den Organizer aktiv sein |
| `GlobalSignal(django.dispatch.Signal)` | 314-360 | beliebig | keiner |
| `DeprecatedSignal(GlobalSignal)` | 363-371 | — | warnt bei `connect()` |

`EventPluginSignal` (253-259, wörtlich):

```python
class EventPluginSignal(PluginSignal[Event]):
    """
    This is an extension to Django's built-in signals which differs in a way that it sends
    out its events only to receivers which belong to plugins that are enabled for the given
    Event.
    """
    type = Event
```

`OrganizerPluginSignal` (277-290, wörtlich):

```python
class OrganizerPluginSignal(PluginSignal[Organizer]):
    """
    ... sends out its events only to receivers which belong to plugins that are enabled for the given
    Organizer.
    """
    type = Organizer

    def __init__(self, allow_legacy_plugins=False):
        self.allow_legacy_plugins = allow_legacy_plugins
        super().__init__()
```

**Der entscheidende Unterschied für uns**: `PluginSignal.send` prüft den
Sendertyp hart (155-156):

```python
        if sender and not isinstance(sender, self.type):
            raise ValueError(f"Sender needs to be of type {self.type}.")
```

und `connect()` prüft das **Plugin-Level** des empfangenden Apps
(`EventPluginSignal.connect`, 261-274; `OrganizerPluginSignal.connect`,
292-311). Ein reines Event-Level-Plugin (`level` nicht gesetzt →
`PLUGIN_LEVEL_EVENT`, `pretix/base/plugins.py:35-37`) darf sich an einen
`OrganizerPluginSignal` **nur** anschließen, wenn dieser mit
`allow_legacy_plugins=True` erzeugt wurde — dann kommt eine
`DeprecationWarning` (301-306). Sonst: `ImproperlyConfigured`.

Plugin-Level-Konstanten (`pretix/base/plugins.py:35-37`):

```python
PLUGIN_LEVEL_EVENT = 'event'
PLUGIN_LEVEL_ORGANIZER = 'organizer'
PLUGIN_LEVEL_EVENT_ORGANIZER_HYBRID = 'event_organizer'
```

Weitere Eigenschaften von `PluginSignal`:
- `send(sender, **named) -> List[Tuple[Callable, Any]]` (150)
- `send_chained(sender, chain_kwarg_name, **named)` (171) — Rückgabewert des
  einen Receivers wird Eingabe des nächsten
- `send_robust(sender, **named)` (193) — Exceptions landen als Wert in der Liste
- `asend` / `asend_robust` sind **nicht implementiert** (222-226,
  `raise NotImplementedError`), async Receiver sind verboten (231-233)
- Receiver werden deterministisch sortiert: Core-Module zuerst, danach
  `(__module__, __name__)` alphabetisch (242-249)

### 3.2 Exporter-Registrierung

`pretix/base/signals.py:649-663`, wörtlich:

```python
register_data_exporters = EventPluginSignal()
"""
This signal is sent out to get all known data exporters. Receivers should return a
subclass of pretix.base.exporter.BaseExporter

As with all event-plugin signals, the ``sender`` keyword argument will contain the event.
"""

register_multievent_data_exporters = OrganizerPluginSignal(allow_legacy_plugins=True)
"""
This signal is sent out to get all known data exporters, which support exporting data for
multiple events. Receivers should return a subclass of pretix.base.exporter.BaseExporter

The ``sender`` keyword argument will contain an organizer.
"""
```

Beachte: Der Receiver liefert die **Klasse**, nicht eine Instanz. Instanziiert
wird in `init_event_exporters` / `init_organizer_exporters` (siehe Abschnitt 5.2).

### 3.3 Event-Kopie: `event_copy_data`

`pretix/base/signals.py:900-916`, wörtlich:

```python
event_copy_data = EventPluginSignal()
"""
Arguments: "other", ``tax_map``, ``category_map``, ``item_map``, ``question_map``, ``variation_map``, ``checkin_list_map``, ``quota_map``

This signal is sent out when a new event is created as a clone of an existing event, i.e.
the settings from the older event are copied to the newer one. You can listen to this
signal to copy data or configuration stored within your plugin's models as well.

You don't need to copy data inside the general settings storage which is cloned automatically,
but you might need to modify that data.

The ``sender`` keyword argument will contain the event of the **new** event. The ``other``
keyword argument will contain the event to **copy from**. The keyword arguments
``tax_map``, ``category_map``, ``item_map``, ``question_map``, ``quota_map``, ``variation_map`` and
``checkin_list_map`` contain mappings from object IDs in the original event to objects
in the new event of the respective types.
"""
```

Gesendet in `Event.copy_data_from` (`pretix/base/models/event.py:886` Methode,
`1209-1213` der Send):

```python
        event_copy_data.send(
            sender=self, other=other,
            tax_map=tax_map, category_map=category_map, item_map=item_map, variation_map=variation_map,
            question_map=question_map, checkin_list_map=checkin_list_map, quota_map=quota_map,
        )
```

Aufrufer von `copy_data_from`: `control/views/main.py:343` (Event-Anlage im
Backend), `api/serializers/event.py:449` und `api/views/event.py:289` (API).

**Semantik von `question_map`** — hier ist eine Falle, die man aus dem
Docstring nicht sieht (`event.py:1090-1099`):

```python
        question_map = {}
        for q in Question.objects.filter(event=other).prefetch_related('items', 'options'):
            items = list(q.items.all())
            opts = list(q.options.all())
            question_map[q.pk] = q
            q.pk = None
            q._prefetched_objects_cache = {}
            q.event = self
            q.save(force_insert=True)
```

Das Objekt wird in die Map gelegt und **danach mutiert**. Der Key ist also die
**alte** PK, der Wert ist die **neue** `Question` (dasselbe Python-Objekt mit
neuer PK). `question_map[alte_pk].pk` ist die neue PK,
`question_map[alte_pk].identifier` ist unverändert.

Beispiel-Receiver aus dem Core (`pretix/plugins/badges/signals.py:88-112`,
gekürzt):

```python
@receiver(signal=event_copy_data, dispatch_uid="badges_copy_data")
def event_copy_data_receiver(sender, other, question_map, item_map, **kwargs):
    for bl in other.badge_layouts.all():
        bl = copy.copy(bl)
        bl.pk = None
        bl.event = sender
        ...
        newq = question_map.get(int(o['content'][9:]))
        ...
        bl.save()
```

Verwandtes Signal: `item_copy_data` (`signals.py:931-941`, Argumente `source`,
`target`) für das Duplizieren eines einzelnen Produkts.

### 3.4 `periodic_task`

`pretix/base/signals.py:943-949`:

```python
periodic_task = GlobalSignal()
"""
This is a regular django signal (no pretix event signal) that we send out every
time the periodic task cronjob runs. This interval is not sharply defined, it can
be everything between a minute and a day. The actions you perform should be
idempotent, ...
"""
```

Das ist der Hook, über den pretix seine Scheduled Exports startet (Abschnitt
5.4). Wir brauchen ihn **nicht** selbst (`CLAUDE.md` Regel 5: kein eigener
Scheduler).

### Fallstricke (Signale)

1. `register_multievent_data_exporters` ist ein `OrganizerPluginSignal`. Unser
   Plugin ist voraussichtlich Event-Level → wir hängen uns über die
   Legacy-Ausnahme (`allow_legacy_plugins=True`) dran und erzeugen dabei eine
   `DeprecationWarning`. Die pytest-Konfiguration von pretix filtert genau diese
   Warnung explizit heraus (`src/setup.cfg`, `filterwarnings`-Eintrag
   `ignore:.*This signal will soon be only available for plugins that declare to be organizer-level.*`).
   In unserer eigenen `pytest`-Konfiguration müssen wir das ggf. selbst tun,
   sonst schlägt der Test wegen `filterwarnings = error` fehl, falls wir das
   übernehmen.
2. Ein Receiver, der einen `EventPluginSignal` empfängt, muss in einem Modul
   liegen, das zu einer App mit `PretixPluginMeta` gehört (`connect`-Check,
   261-274). Receiver in Test-Hilfsmodulen ohne App-Zuordnung feuern nicht.
3. `send()` liefert `List[Tuple[receiver, response]]` — Core-Code baut daraus
   üblicherweise `sum((list(a[1]) for a in ...), [])`. Wer `None` zurückgibt,
   muss das im Aufrufer abgefangen sehen; `init_event_exporters` macht das
   (`if not response: continue`, `services/export.py:205-206`), die
   Navigation nicht (`navigation.py:345-348` würde bei `None` in `list()`
   krachen) — Navigations-Receiver müssen immer eine **Liste** zurückgeben,
   notfalls `[]`.

---

## 4. Navigation

### 4.1 Welche Signale existieren

Alle in `pretix/control/signals.py`:

| Signal | Zeilen | Typ | Zweck |
| --- | --- | --- | --- |
| `nav_event` | 58-81 | `EventPluginSignal` | Hauptnavigation im Event-Kontext |
| `nav_topbar` | 83-100 | `GlobalSignal` | obere Leiste |
| `nav_global` | 102-125 | `GlobalSignal` | Navigation ohne Event-Kontext |
| `nav_organizer` | 234-262 | `OrganizerPluginSignal(allow_legacy_plugins=True)` | Organizer-Detailseite |
| `nav_event_settings` | 294-310 | `EventPluginSignal` | Tabs auf der Event-Einstellungsseite |
| `organizer_edit_tabs` | 226-232 | `DeprecatedSignal` | **tot**, "no longer works" |

`nav_event` (58-81), wörtlich gekürzt:

> Receivers are expected to return a list of dictionaries. The dictionaries
> should contain at least the keys ``label`` and ``url``. You can also return
> a fontawesome icon name with the key ``icon`` … You should also return an ``active`` key with a boolean
> set to ``True``, when this item should be marked as active. …
> You can optionally create sub-items … Either you specify a key ``children`` on your top navigation item
> that contains a list of navigation items (as dictionaries), or you specify a ``parent``
> key with the ``url`` value of the designated parent item.

`nav_organizer` (234-262) bekommt zusätzlich die Keyword-Argumente `organizer`
und `request`; der Sender ist der `Organizer`.

### 4.2 Struktur des Rückgabewerts

Keys, die der Core tatsächlich auswertet:

| Key | Pflicht | Ausgewertet in |
| --- | --- | --- |
| `label` | ja | Template + Sortierung (`navigation.py:347`, `723`) |
| `url` | ja | Template + `merge_in`-Parent-Match (`navigation.py:731`) |
| `active` | faktisch ja | Template; Core setzt es überall explizit |
| `icon` | nein | Template (FontAwesome-Name ohne `fa-`-Präfix) |
| `children` | nein | Untermenü, Liste gleichartiger Dicts |
| `parent` | nein | `url`-Wert des Zielelternteils, siehe `merge_in` |

Zusammenführung (`pretix/control/navigation.py:728-740`), wörtlich:

```python
def merge_in(nav, newnav):
    for item in newnav:
        if 'parent' in item:
            parents = [n for n in nav if n['url'] == item['parent']]
            if parents:
                if 'children' not in parents[0]:
                    parents[0]['children'] = [
                        dict(parents[0])
                    ]
                    parents[0]['active'] = False
                parents[0]['children'].append(item)
                continue
        nav.append(item)
```

Aufruf für Events (`navigation.py:345-348`):

```python
    merge_in(nav, sorted(
        sum((list(a[1]) for a in nav_event.send(request.event, request=request)), []),
        key=lambda r: (1 if r.get('parent') else 0, r['label'])
    ))
```

Aufruf für Organizer (`navigation.py:720-724`) analog mit
`nav_organizer.send(request.organizer, request=request, organizer=request.organizer)`.

### 4.3 `active`-Logik im Core

Der Core leitet `active` aus `request.resolver_match` ab
(`navigation.py:31-33`, `url = request.resolver_match`), typischerweise über
`url.url_name` und `url.namespace`. Beispiele:

```python
'active': (url.url_name == 'event.index'),                      # navigation.py:42
'active': 'event.orders.export' in url.url_name,                # navigation.py:246
'active': url.url_name.startswith('event.settings.tax'),        # navigation.py:102
'active': 'organizer.team' in url.url_name and url.namespace == 'control',   # navigation.py:570
```

Plugins nutzen zusätzlich `resolve(request.path_info)` und vergleichen den
Namespace (`pretix/plugins/badges/signals.py:45`, `59`):

```python
    url = resolve(request.path_info)
    ...
        'active': url.namespace == 'plugins:badges',
```

**Wichtig:** Ein Top-Level-Eintrag mit `children` hat im Core immer
`'active': False`; die Markierung passiert auf Kind-Ebene
(`navigation.py:272`, `294`, `323`; und `merge_in` setzt den Parent explizit auf
`False`, wenn er zum Container wird, Zeile 737).

### 4.4 Permission-Keys in der Navigation

Der Core prüft **nicht** `request.user.has_event_permission(...)` in der
Navigation, sondern das vorberechnete Set `request.eventpermset` bzw.
`request.orgapermset`. Gesetzt in
`pretix/control/middleware.py:172-175` und `194-197`:

```python
            if request.user.has_active_staff_session(request.session.session_key):
                request.eventpermset = SuperuserPermissionSet()
            else:
                request.eventpermset = EventPermissionSet(request.user.get_event_permission_set(request.organizer, request.event))
```

Tatsächlich in `navigation.py` verwendete Keys (vollständig, Event-Ebene):

| Key | Zeilen |
| --- | --- |
| `event.settings.general:write` | 47, 57, 67, 95, 105, 115, 316 |
| `event.settings.payment:write` | 57 |
| `event.settings.tax:write` | 95 |
| `event.settings.invoicing:write` | 105 |
| `event.orders:read` | 214, 316 |
| `event.orders:write` | 257 |
| `event.vouchers:read` | 287 |

Organizer-Ebene: `organizer.settings.general:write` (510, 698),
`organizer.teams:write` (564), `organizer.giftcards:read|write` (574),
`organizer.outgoingmails:read` (710).

Plugin-Receiver prüfen selbst und geben `[]` zurück, wenn nichts erlaubt ist —
Muster aus `pretix/plugins/banktransfer/signals.py:41-46`:

```python
@receiver(nav_event, dispatch_uid="payment_banktransfer_nav")
def control_nav_import(sender, request=None, **kwargs):
    url = resolve(request.path_info)
    if not request.user.has_event_permission(request.organizer, request.event, 'event.orders:write', request=request):
        return []
    return [ ... ]
```

und das Organizer-Pendant (`banktransfer/signals.py:76-83`), das über
`get_events_with_permission(...).filter(organizer=...).exists()` prüft.

### 4.5 Wo hängt der Export-Menüpunkt?

`control:event.orders.export` liegt als Kind unter "Orders", wenn
`event.orders:read` vorhanden ist (`navigation.py:240-247`), und andernfalls als
eigener Top-Level-Punkt mit Icon `download` (`navigation.py:276-285`). Der
Organizer-Export analog bei `navigation.py:689-696`.

Wenn wir unseren Report-Builder als Kind des Export-Menüs einhängen wollen, ist
`parent` = `reverse('control:event.orders.export', kwargs={...})` der passende
Wert — genau so macht es der Core für "Data sync problems"
(`navigation.py:698-708`).

### Fallstricke (Navigation)

1. `request.eventpermset` existiert **nur im Control-Backend** und nur, wenn die
   URL `organizer` *und* `event` als kwargs hat
   (`control/middleware.py:149`). In unseren eigenen Views nicht blind darauf
   zugreifen, sondern `request.user.has_event_permission(...)`.
2. Ein Navigations-Receiver muss `[]` zurückgeben statt `None` (siehe 3.5
   Fallstrick 3).
3. `merge_in` matcht den Parent über **String-Gleichheit der URL**. Ein
   Trailing-Slash-Unterschied lässt den Eintrag stillschweigend auf oberster
   Ebene landen.
4. `nav_event` ist ein `EventPluginSignal` — der Menüpunkt erscheint nur, wenn
   das Plugin für dieses Event aktiviert ist. Das ist gewollt.
5. Die Tupelform `(label, urlname, kwargs)` für `navigation_links` ist eine
   **andere** API (Innerhalb-der-Seite-Navigation), nicht zu verwechseln mit den
   Dicts von `nav_event`. Beides existiert; `bootstrap-dev` hat die Tupelform in
   `docs/adr/0000-setup.md` dokumentiert.

---

## 5. Scheduled Exports

Das ist der Mechanismus, an den wir laut `CLAUDE.md` Regel 5 andocken.

### 5.1 Modelle

**Modul:** `pretix/base/models/exports.py` (138 Zeilen, komplett gelesen)

`AbstractScheduledExport(LoggedModel)` — Zeilen 37-114, `abstract = True`
(93-94). Feldliste **wörtlich**:

| Feld | Definition | Zeilen |
| --- | --- | --- |
| `id` | `models.BigAutoField(primary_key=True)` | 38 |
| `export_identifier` | `models.CharField(max_length=190, verbose_name=_("Export"))` | 40-43 |
| `export_form_data` | `models.JSONField(default=dict, encoder=DjangoJSONEncoder)` | 44-47 |
| `owner` | `models.ForeignKey("pretixbase.User", on_delete=models.PROTECT)` | 49-52 |
| `locale` | `models.CharField(verbose_name=_('Language'), max_length=250)` | 53-56 |
| `mail_additional_recipients` | `models.TextField(null=False, blank=True, validators=[multimail_validate])` | 58-62 |
| `mail_additional_recipients_cc` | dito | 63-67 |
| `mail_additional_recipients_bcc` | dito | 68-72 |
| `mail_subject` | `models.CharField(max_length=250)` | 73-76 |
| `mail_template` | `models.TextField()` | 77-79 |
| `schedule_rrule` | `models.TextField(null=True, blank=True, validators=[RRuleValidator(enforce_simple=True)])` | 81-83 |
| `schedule_rrule_time` | `models.TimeField(...)` | 84-87 |
| `schedule_next_run` | `models.DateTimeField(null=True, blank=True)` | 88 |
| `error_counter` | `models.IntegerField(default=0)` | 90 |
| `error_last_message` | `models.TextField(null=True, blank=True)` | 91 |

Methoden: `__str__` gibt `self.mail_subject` (96-97);
`compute_next_run()` (99-114) berechnet `schedule_next_run` aus
`schedule_rrule` + `schedule_rrule_time` in `self.tz`, inkl.
DST-Korrektur (`if not datetime_exists(...): += timedelta(hours=1)`).

Konkrete Modelle:

```python
class ScheduledEventExport(AbstractScheduledExport):                         # 117-124
    event = models.ForeignKey(
        "pretixbase.Event", on_delete=models.CASCADE, related_name="scheduled_exports"
    )

    @property
    def tz(self):
        return self.event.timezone


class ScheduledOrganizerExport(AbstractScheduledExport):                     # 127-137
    organizer = models.ForeignKey(
        "pretixbase.Organizer", on_delete=models.CASCADE, related_name="scheduled_exports"
    )
    timezone = models.CharField(max_length=100, default=settings.TIME_ZONE, verbose_name=_('Timezone'))

    @property
    def tz(self):
        return zoneinfo.ZoneInfo(self.timezone)
```

Zugriff also über `event.scheduled_exports` bzw. `organizer.scheduled_exports`.
Migrationen: `pretix/base/migrations/0228_scheduledeventexport_scheduledorganizerexport.py`.

**Es gibt kein `Meta.ordering`** auf diesen Modellen. Die Views sortieren
explizit: `.order_by('export_identifier', 'schedule_next_run')`
(`control/views/orders.py:2710`, `control/views/organizer.py:2046`).

### 5.2 Wie `export_identifier` und `export_form_data` befüllt werden

Im Control-Backend (`pretix/control/views/orders.py:2820-2827`), wörtlich:

```python
            elif self.exporter.form.is_valid() and self.rrule_form.is_valid() and self.schedule_form.is_valid():
                self.schedule_form.instance.export_identifier = self.exporter.identifier
                self.schedule_form.instance.export_form_data = self.exporter.form.cleaned_data
                self.schedule_form.instance.schedule_rrule = str(self.rrule_form.to_rrule())
                self.schedule_form.instance.error_counter = 0
                self.schedule_form.instance.error_last_message = None
                self.schedule_form.instance.compute_next_run()
                self.schedule_form.instance.save()
```

`export_form_data` ist also **`cleaned_data` des Exporter-Formulars**, JSON-serialisiert
mit `DjangoJSONEncoder`. Damit das JSON-fähig ist, wandelt `ExporterForm.clean`
Modellinstanzen in PKs um (`pretix/control/forms/orders.py:264-278`), wörtlich:

```python
class ExporterForm(forms.Form):
    def clean(self):
        data = super().clean()

        for k, v in data.items():
            if isinstance(v, models.Model):
                data[k] = v.pk
            elif isinstance(v, models.QuerySet):
                data[k] = [m.pk for m in v]

        if 'all_events' in self.fields and 'events' in self.fields:
            if not data.get('all_events') and not data.get('events'):
                raise ValidationError(_('Please select some events.'))

        return data
```

Beim Wiederanzeigen einer gespeicherten Konfiguration wird das umgekehrt per
`field.to_python(...)` versucht, Fehler werden geschluckt
(`control/views/orders.py:2675-2685`):

```python
                for k in initial:
                    if initial[k] and k in test_form.fields:
                        try:
                            initial[k] = test_form.fields[k].to_python(initial[k])
                        except Exception:
                            pass
```

Beim **Ausführen** wird `export_form_data` unverändert an `render()` gereicht
(`services/export.py:366-370`) — es findet **keine erneute Formularvalidierung**
statt. Der Exporter bekommt also rohes JSON aus der Datenbank.

Über die API läuft es über `ScheduledExportSerializer`
(`pretix/api/serializers/exporters.py:161-235`) mit
`export_form_data = ExportFormDataField()` (136-158) und
`export_identifier = serializers.ChoiceField(choices=[])`, dessen Choices aus
`self.context['exporters']` befüllt werden (169-171). Für Multievent-Exporte
werden `events` API-seitig als **Slugs**, in der DB als **PKs** geführt
(`JobRunSerializer.to_internal_value`, 110-116; `to_representation`, 75-80).

### 5.3 Multievent-Sonderfelder `all_events` / `events`

Diese beiden Felder gehören **nicht** zum Exporter, sondern werden von der
Organizer-Export-View nachträglich in `form.fields` injiziert
(`pretix/control/views/organizer.py:2000-2019`) — und zwar nur, wenn der
Exporter kein `OrganizerLevelExportMixin` ist. Beim Ausführen filtert der Task
danach (`services/export.py:439-444`):

```python
    event_qs = organizer.events.all()
    if schedule.export_form_data.get('events') is not None and not schedule.export_form_data.get('all_events'):
        if isinstance(schedule.export_form_data['events'][0], str):
            event_qs = event_qs.filter(slug__in=schedule.export_form_data.get('events'))
        else:
            event_qs = event_qs.filter(pk__in=schedule.export_form_data.get('events'))
```

(Der String-Zweig ist Kompatibilität für "legacy API-created schedules", siehe
Kommentar in `services/export.py:138`.)

### 5.4 Wer führt sie aus

**Periodischer Task** (`pretix/base/services/export.py:496-521`), wörtlich:

```python
@receiver(signal=periodic_task)
@scopes_disabled()
@transaction.atomic
def run_scheduled_exports(sender, **kwargs):
    qs = ScheduledEventExport.objects.filter(
        schedule_next_run__lt=now(),
        error_counter__lt=5,
    ).select_for_update(skip_locked=connection.features.has_select_for_update_skip_locked, of=OF_SELF).select_related('event')
    for s in qs:
        scheduled_event_export.apply_async(kwargs={
            'event': s.event_id,
            'schedule': s.pk,
        })
        s.compute_next_run()
        s.save(update_fields=['schedule_next_run'])
    qs = ScheduledOrganizerExport.objects.filter(...)   # analog für Organizer
```

Die eigentlichen Tasks:

```python
@app.task(base=OrganizerTask, bind=True, max_retries=5, default_retry_delay=120)
def scheduled_organizer_export(self, organizer: Organizer, schedule: int) -> None:      # 435-466

@app.task(base=EventTask, bind=True, max_retries=5, default_retry_delay=120)
def scheduled_event_export(self, event: Event, schedule: int) -> None:                  # 469-493
```

Beide holen `schedule` aus dem Kontext-Objekt (`event.scheduled_exports.get(pk=schedule)`,
Zeile 471) und rufen `_run_scheduled_export(...)` (322-432).

Manuelles Auslösen aus dem Backend: `RunScheduledExportView`
(`control/views/orders.py:2975-2995`) mit `queue='default'` statt der
Hintergrund-Queue.

### 5.5 Owner- und Permission-Anforderungen

- `owner` ist ein `FK("pretixbase.User", on_delete=models.PROTECT)`
  (`exports.py:49-52`). **Ein User mit Scheduled Exports kann nicht gelöscht
  werden**, ohne dass die Exports vorher weg sind.
- Beim Ausführen wird der Exporter **im Namen des Owners** initialisiert
  (`services/export.py:473-477`):
  ```python
      exporter = init_event_exporter(
          identifier=schedule.export_identifier,
          event=event,
          user=schedule.owner,
      )
      has_permission = schedule.owner.is_active
  ```
- `init_event_exporters` prüft je Exporter
  (`services/export.py:211-213`):
  ```python
          permission_name = response.get_required_event_permission()
          if not perm_holder.has_event_permission(event.organizer, event, permission_name, request) and not staff_session:
              continue
  ```
  Fällt der Exporter durch, liefert `init_event_exporter` `None`.
- Anlegen im Backend erfordert die Exporter-Permission, **explizit ohne
  Staff-Session-Bonus** (`control/views/orders.py:2921-2924`), wörtlich inkl.
  Begründungs-Kommentar:
  ```python
      def has_permission_to_create_scheduled(self):
          # Exports can only be created if the user has the correct permissions. We *ignore* staff sessions, because
          # the export is not *run* during a staff session and then would fail at the scheduled time.
          return self.request.user.has_event_permission(self.request.organizer, self.request.event, self.exporter.get_required_event_permission())
  ```
- Bearbeiten fremder Schedules: nur Owner, Staff-Session, oder
  `event.settings.general:write` **plus** die Exporter-Permission
  (`control/views/orders.py:2899-2919`, mit ausführlichem Kommentar zur
  verhinderten Privilege Escalation).
- Sichtbarkeit der Liste (`control/views/orders.py:2704-2710`):
  ohne `event.settings.general:write` sieht man **nur die eigenen**.
  Organizer-Pendant mit `organizer.settings.general:write`
  (`control/views/organizer.py:2040-2046`).

### 5.6 Was passiert, wenn ein referenziertes Objekt fehlt

Das ist der Teil, der uns am meisten betrifft (eine gespeicherte
Report-Definition kann gelöscht werden, während ein Schedule sie noch
referenziert). Die relevante Fehlerbehandlung steht in
`_run_scheduled_export` (`services/export.py:322-432`).

**Fall A — Exporter-Identifier existiert nicht mehr / Owner hat keine Rechte
mehr / Plugin deaktiviert:** `init_event_exporter` liefert `None`
(`services/export.py:191-195`), und dann (363-365):

```python
        try:
            if not exporter:
                raise ExportError("Export type not found or permission denied.")
```

→ `_handle_error(msg, soft=False)` (329-357):
- `context.log_action('pretix.event.export.schedule.failed', data={...'reason': msg, 'soft': False})`
- Mail "Export failed" an `schedule.owner.email`, **nur wenn** `owner.is_active`
- `schedule.error_counter += 1`, `error_last_message = msg`, gespeichert

Nach 5 Fehlern wird der Schedule vom periodischen Task **stillschweigend
ignoriert** (`error_counter__lt=5`, Zeile 502/513). Es gibt keine weitere
Benachrichtigung. Erfolgreicher Lauf setzt den Zähler zurück (398-400).

Verifiziert durch den Core-Test `tests/base/test_export.py:92-109`:

```python
def test_event_fail_invalid_config(event, user):
    s.export_identifier = " invalid "
    ...
    assert s.error_counter == 1
    assert djmail.outbox[0].subject == "Export failed"
    assert "Reason: Export type not found" in djmail.outbox[0].body
```

und `tests/base/test_export.py:112-131` (inaktiver Owner: `error_counter == 1`,
**keine** Mail) sowie `:134-157` (Owner ohne Permission: `error_counter == 1`,
Mail mit "Reason: Export type not found or permission denied.").

**Fall B — Ein in `export_form_data` referenziertes Objekt fehlt** (z. B.
gelöschte Report-Definition, gelöschte Frage). Das fängt pretix **nicht**
speziell ab. Der Exporter bekommt die tote PK und wirft typischerweise
`DoesNotExist` — so machen es auch die Core-Exporter, z. B.
`pretix/plugins/checkinlists/exporters.py:322`:

```python
        cl = self.event.checkin_lists.get(pk=form_data['list'])
```

Der Ablauf ist dann (`services/export.py:392-397`):

```python
        except Exception:
            logger.exception("Scheduled export failed.")
            try:
                retry_func()
            except MaxRetriesExceededError:
                _handle_error('Internal Error')
```

Also: **5 Celery-Retries à 120 s** (`max_retries=5, default_retry_delay=120`,
Zeilen 435/469), dann eine Mail mit dem nichtssagenden Text "Internal Error" und
`error_counter += 1`. Für den Nutzer ist das eine schlechte Erfahrung.

→ **Konsequenz für unser Plugin:** Unser Exporter sollte eine fehlende
Report-Definition **selbst** abfangen und eine `ExportError` mit klarer Meldung
werfen (`from pretix.base.services.export import ExportError`), statt
`DoesNotExist` durchzureichen. Dann gibt es sofort eine verständliche Mail und
keine 10 Minuten nutzlose Retries. Das gilt genauso für Report-Definitionen, die
auf inzwischen gelöschte Fragen/Produkte verweisen.

Weitere Abbruchgründe in `_run_scheduled_export`:
- `has_permission == False` → `_handle_error(gettext('Permission denied.'))`, Return (359-361)
- Ergebnis `None` → `ExportEmptyError` → `_handle_error(..., soft=True)`:
  Mail ja, `error_counter` **nein** (371-374, 388-389)
- Ergebnisgröße > 20 MB → `ExportError('Your exported data exceeded the size limit for scheduled exports.')` (377-380)

Die erzeugte Datei ist ein `CachedFile(web_download=False)` mit
`expires = now() + 24h` (324-327) und wird per
`attach_cached_files=[file]` an die Mail gehängt (421).

### Fallstricke (Scheduled Exports)

1. **`export_form_data` ist nach dem Speichern Untrusted-ish Input.** Es wird
   beim Ausführen nicht revalidiert. Unser Exporter muss jeden Wert daraus als
   potenziell veraltet/fremd behandeln — insbesondere jede ID. `CLAUDE.md`
   Regel 2 gilt hier unmittelbar: ORM-Pfade kommen aus der Registry, aus
   `export_form_data` kommen ausschließlich IDs/Flags, die wir gegen das
   aktuelle Event prüfen.
2. **20-MB-Limit** für terminierte Exporte. Ein großer Report läuft interaktiv,
   aber scheitert terminiert. Das sollte in der UI erwähnt werden.
3. Die Größe wird über `len(data)` bestimmt (376) — das setzt voraus, dass
   `render()` Bytes zurückgibt, also **nicht** mit `output_file` gearbeitet wird.
   Bei Scheduled Exports wird `output_file` nie gesetzt.
4. `error_counter >= 5` deaktiviert den Schedule faktisch dauerhaft und
   unsichtbar. Beim Speichern über die UI wird er zurückgesetzt
   (`control/views/orders.py:2824-2825`).
5. `owner` ist `PROTECT`. Wenn wir eigene Modelle mit `owner`-Semantik bauen,
   ist das ein bewusst gewähltes Vorbild, aber es blockiert User-Löschung.
6. `locale` ist ein `CharField(max_length=250)` ohne `choices` auf Modellebene —
   die Choices kommen erst im Form (`control/forms/exports.py:47-51`) aus
   `event.settings.locales`.
7. Beim Ausführen gilt `with language(schedule.locale, context.settings.region), override(schedule.tz)`
   (323) — Sprache und Zeitzone weichen also potenziell von der Event-Einstellung ab.
   Zeitzonenabhängige Spalten in Reports müssen das aushalten.

---

## 6. Datenmodell

### 6.1 `Order`

**Modul:** `pretix/base/models/orders.py:138-1315`
Klasse: `class Order(LockModel, LoggedModel)` (138)

**Statuskonstanten** (195-205), wörtlich:

```python
    STATUS_PENDING = "n"
    STATUS_PAID = "p"
    STATUS_EXPIRED = "e"
    STATUS_CANCELED = "c"
    STATUS_REFUNDED = "c"  # deprecated
    STATUS_CHOICE = (
        (STATUS_PENDING, _("pending")),
        (STATUS_PAID, _("paid")),
        (STATUS_EXPIRED, _("expired")),
        (STATUS_CANCELED, _("canceled")),
    )
```

**Vollständige Feldliste** (Zeilen 207-330; die folgende Tabelle ist zusätzlich
per Django-Introspektion des geladenen Modells verifiziert):

| Feld | Typ | null | Relation → (related_name) |
| --- | --- | --- | --- |
| `id` | `BigAutoField` | nein | |
| `code` | `CharField(max_length=16, db_index=True)` | nein | |
| `status` | `CharField(max_length=3, choices=STATUS_CHOICE, db_index=True)` | nein | |
| `valid_if_pending` | `BooleanField(default=False)` | nein | |
| `testmode` | `BooleanField(default=False)` | nein | |
| `organizer` | `ForeignKey` | ja | `Organizer` (`orders`) — redundant, für Unique-Constraint |
| `event` | `ForeignKey(on_delete=CASCADE)` | nein | `Event` (`orders`) |
| `customer` | `ForeignKey(on_delete=SET_NULL)` | ja | `Customer` (`orders`) |
| `email` | `EmailField` | ja | |
| `phone` | `PhoneNumberField` | ja | |
| `locale` | `CharField(max_length=32)` | ja | |
| `secret` | `CharField(max_length=32, default=generate_secret)` | nein | |
| `internal_secret` | `CharField(max_length=32, default=generate_secret)` | ja | |
| `datetime` | `DateTimeField(db_index=False)` | nein | Bestellzeitpunkt |
| `cancellation_date` | `DateTimeField` | ja | |
| `expires` | `DateTimeField` | nein | |
| `total` | `DecimalField(decimal_places=2, max_digits=13)` | nein | |
| `comment` | `TextField(blank=True)` | nein | interner Kommentar |
| `custom_followup_at` | `DateField` | ja | |
| `checkin_attention` | `BooleanField(default=False)` | nein | |
| `checkin_text` | `TextField` | ja | |
| `expiry_reminder_sent` | `BooleanField(default=False)` | nein | |
| `download_reminder_sent` | `BooleanField(default=False)` | nein | |
| `meta_info` | `TextField` | ja | JSON **als Text** |
| `api_meta` | `JSONField(default=dict)` | nein | |
| `last_modified` | `DateTimeField(auto_now=True, db_index=False)` | nein | |
| `require_approval` | `BooleanField(default=False)` | nein | |
| `sales_channel` | `ForeignKey(on_delete=PROTECT)` | nein | `SalesChannel` (`order_set`) |
| `email_known_to_work` | `BooleanField(default=False)` | nein | |
| `invoice_dirty` | `BooleanField(default=False)` | nein | |
| `tax_rounding_mode` | `CharField(max_length=100, choices=ROUNDING_MODES, default="line")` | nein | |

**Reverse-Relationen auf `Order`** (Django-Introspektion; Kernmodelle):

| Accessor | Quelle |
| --- | --- |
| `all_positions` | `OrderPosition.order` |
| `all_fees` | `OrderFee.order` |
| `payments` | `OrderPayment.order` |
| `refunds` | `OrderRefund.order` |
| `transactions` | `Transaction.order` |
| `invoice_address` | `InvoiceAddress.order` (**OneToOne**) |
| `invoices` | `Invoice.order` |
| `outgoing_mails` | `OutgoingMail.order` |
| `gift_card_transactions` | `GiftCardTransaction.order` |
| `cancellation_requests` | `CancellationRequest.order` |
| `queued_sync_jobs`, `sync_results` | Datasync |
| `cachedcombinedticket_set` | `CachedCombinedTicket.order` |
| `banktransaction_set`, `referencedstripeobject_set`, `referencedpaypalobject_set` | Plugins (nur wenn aktiv installiert) |

**Manager / QuerySet** (332):

```python
    objects = ScopedManager(OrderQuerySet.as_manager().__class__, organizer='event__organizer')
```

`OrderQuerySet` (110-135) enthält genau eine Methode:
`get_with_secret_check(self, code, received_secret, tag, secret_length=64)`.

**Meta** (334-344), wörtlich:

```python
    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        ordering = ("-datetime", "-pk")
        indexes = [
            models.Index(fields=["datetime", "id"], name="pretixbase__datetim_66aff0_idx"),
            models.Index(fields=["last_modified", "id"], name="pretixbase__last_mo_4ebf8b_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["organizer", "code"], name="order_organizer_code_uniq"),
        ]
```

**`positions` vs. `all_positions`** — der zentrale Punkt für unsere Queries
(447-472), wörtlich:

```python
    @property
    def fees(self):
        """
        Related manager for all non-canceled fees. Use ``all_fees`` instead if you want
        canceled positions as well.
        """
        return self.all_fees(manager='objects')

    ...

    @property
    def positions(self):
        """
        Related manager for all non-canceled positions. Use ``all_positions`` instead if you want
        canceled positions as well.
        """
        return self.all_positions(manager='objects')
```

`all_positions` ist der **echte** `related_name` des FK
(`orders.py:2547-2552`), `positions` ist eine Python-Property, die denselben
Related-Manager mit dem gefilterten Default-Manager benutzt.

Wichtige berechnete Properties für Reports:

| Name | Zeilen | Anmerkung |
| --- | --- | --- |
| `count_positions` | 459-464 | `@cached_property @scopes_disabled()`, nutzt Annotation `pcnt`, falls vorhanden |
| `meta_info_data` | 474-481 | `@cached_property`, JSON-Parse von `meta_info` |
| `payment_refund_sum` | 483-493 | `@property @scopes_disabled()` |
| `pending_sum` | 495-508 | `@property @scopes_disabled()` |
| `annotate_overpayments(cls, qs, results=True, refunds=True, sums=False)` | 510-575 | `@classmethod`, liefert Annotationen `pending_sum_t`, `pending_sum_rc`, optional `payment_sum`, `refund_sum`, `computed_payment_refund_sum`, `has_external_refund`, `has_pending_refund`, `is_overpaid`, `is_pending_with_full_payment`, `is_underpaid` |
| `full_code` | 577-582 | `<event_slug>-<code>` |
| `tax_total`, `net_total` | 667-669, 671-673 | |
| `get_extended_status_display()` | 435-445 | Achtung: Doppelpflege mit Templates, siehe Kommentar Zeile 436 |

`annotate_overpayments` ist das Vorbild für teure Aggregat-Spalten: es baut
`Subquery(...)`-Annotationen statt N+1-Queries (512-530).

### 6.2 `OrderPosition`

**Modul:** `pretix/base/models/orders.py:2497-2975`
Klasse: `class OrderPosition(AbstractPosition)` (2497)

`AbstractPosition` (1479-1712, `abstract = True` in 1575-1576) liefert die
gemeinsamen Felder für `OrderPosition` und `CartPosition`.

**Vollständige Feldliste** (geerbt aus `AbstractPosition` 1508-1573 + eigene
2537-2600; per Django-Introspektion verifiziert, Reihenfolge wie im Modell):

| Feld | Typ | null | Relation → (related_name) | Herkunft |
| --- | --- | --- | --- | --- |
| `id` | `BigAutoField` | nein | | |
| `subevent` | `ForeignKey(on_delete=PROTECT)` | ja | `SubEvent` (`orderposition_set`) | Abstract |
| `item` | `ForeignKey(on_delete=PROTECT)` | nein | `Item` (`orderposition_set`) | Abstract |
| `variation` | `ForeignKey(on_delete=PROTECT)` | ja | `ItemVariation` (`orderposition_set`) | Abstract |
| `price` | `DecimalField(2, 13)` | nein | | Abstract |
| `price_includes_rounding_correction` | `DecimalField(13, 2, default=0.00)` | nein | | Abstract |
| `attendee_name_cached` | `CharField(max_length=255)` | ja | | Abstract |
| `attendee_name_parts` | `JSONField(default=dict)` | nein | | Abstract |
| `attendee_email` | `EmailField` | ja | | Abstract |
| `voucher` | `ForeignKey(on_delete=PROTECT)` | ja | `Voucher` (`orderposition_set`) | Abstract |
| `used_membership` | `ForeignKey(on_delete=PROTECT)` | ja | `Membership` (`orderposition_set`) | Abstract |
| `addon_to` | `ForeignKey('self', on_delete=PROTECT)` | ja | `OrderPosition` (**`addons`**) | Abstract |
| `meta_info` | `TextField` | ja | JSON als Text | Abstract |
| `seat` | `ForeignKey(on_delete=PROTECT)` | ja | `Seat` (`orderposition_set`) | Abstract |
| `is_bundled` | `BooleanField(default=False)` | nein | | Abstract |
| `discount` | `ForeignKey(on_delete=RESTRICT)` | ja | `Discount` (`orderposition_set`) | Abstract |
| `company` | `CharField(max_length=255)` | ja | | Abstract |
| `street` | `TextField` | ja | | Abstract |
| `zipcode` | `CharField(max_length=30)` | ja | | Abstract |
| `city` | `CharField(max_length=255)` | ja | | Abstract |
| `country` | `FastCountryField` | ja | | Abstract |
| `state` | `CharField(max_length=255)` | ja | | Abstract |
| `positionid` | `PositiveIntegerField(default=1)` | nein | | OP |
| `organizer` | `ForeignKey(on_delete=CASCADE)` | ja | `Organizer` (`order_positions`) | OP, redundant |
| `order` | `ForeignKey(on_delete=PROTECT)` | nein | `Order` (**`all_positions`**) | OP |
| `voucher_budget_use` | `DecimalField(13, 2)` | ja | | OP |
| `tax_rate` | `DecimalField(max_digits=7, decimal_places=2)` | nein | | OP |
| `tax_rule` | `ForeignKey(on_delete=PROTECT)` | ja | `TaxRule` (`orderposition_set`) | OP |
| `tax_code` | `CharField(max_length=190)` | ja | | OP |
| `tax_value` | `DecimalField(13, 2)` | nein | | OP |
| `tax_value_includes_rounding_correction` | `DecimalField(13, 2, default=0.00)` | nein | | OP |
| `secret` | `CharField(max_length=255, db_index=True)` | nein | | OP |
| `web_secret` | `CharField(max_length=32, default=generate_secret, db_index=True)` | nein | | OP |
| `pseudonymization_id` | `CharField(max_length=16, unique=True, db_index=True)` | nein | | OP |
| `canceled` | `BooleanField(default=False)` | nein | | OP |
| `blocked` | `JSONField` | ja | Liste von Strings oder `None`, **nie `[]`** | OP |
| `ignore_from_quota_while_blocked` | `BooleanField(default=False)` | nein | | OP |
| `valid_from` | `DateTimeField` | ja | | OP |
| `valid_until` | `DateTimeField` | ja | | OP |

Zu `blocked` sagt der Klassen-Docstring wörtlich (2522-2526):

> :param blocked: A list of reasons why this order position is blocked. … If the position is not blocked, the value must be ``None``, not an empty
> list.

**Reverse-Relationen auf `OrderPosition`** (Django-Introspektion):

| Accessor | Quelle |
| --- | --- |
| `answers` | `QuestionAnswer.orderposition` |
| `all_checkins` | `Checkin.position` |
| `addons` | `OrderPosition.addon_to` |
| `issued_gift_cards` | `GiftCard.issued_in` |
| `owned_gift_cards` | `GiftCard.owner_ticket` |
| `granted_memberships` | `Membership.granted_in` |
| `outgoing_mails` | `OutgoingMail.orderposition` |
| `print_logs` | `PrintLog.position` |
| `revoked_secrets`, `blocked_secrets` | Ticket-Secrets |
| `linked_media` | `ReusableMedium.linked_orderpositions` (**M2M**) |
| `cachedticket_set` | `CachedTicket.order_position` |
| `sync_results` | `OrderSyncResult.order_position` |

**Manager** (2602-2603), wörtlich:

```python
    all = ScopedManager(organizer='order__event__organizer')
    objects = ActivePositionManager()
```

mit (2290-2292):

```python
class ActivePositionManager(ScopedManager(organizer='order__event__organizer').__class__):
    def get_queryset(self):
        return super().get_queryset().filter(canceled=False)
```

**Der Default-Manager `OrderPosition.objects` blendet stornierte Positionen
also aus.** Aus dem Klassen-Docstring (2503-2504), wörtlich (inkl. Tippfehler
"fees"):

> The default ``OrderPosition.objects`` manager only contains fees that are not ``canceled``. If
> you want all objects, you need to use ``OrderPosition.all`` instead.

**Meta** (2623-2629), wörtlich:

```python
    class Meta:
        verbose_name = _("Order position")
        verbose_name_plural = _("Order positions")
        ordering = ("positionid", "id")
        constraints = [
            models.UniqueConstraint("organizer", "secret", name="orderposition_organizer_secret_uniq")
        ]
```

Wichtige Properties:

| Name | Zeilen | Anmerkung |
| --- | --- | --- |
| `event` | 2886-2888 | **Property**, kein Feld: `return self.order.event`. Für ORM-Lookups gibt es nur `order__event` |
| `checkins` | 2662-2668 | `self.all_checkins(manager='objects')` = nur erfolgreiche |
| `code` | 2969-2975 | `"<order_code>-<positionid>"` |
| `sort_key` | 2641-2643 | `(addon_to.positionid or positionid, addon_to_id or 0, positionid)` |
| `require_checkin_attention` | 2645-2649 | Order/Item/Variation kombiniert |
| `checkin_texts` | 2651-2660 | Liste aus Order-, Variations- und Item-Text |
| `net_price` | 1643-1645 (Abstract) | `price - tax_value` |
| `attendee_name` | 1670-1672 (Abstract) | via `build_name(attendee_name_parts, ...)` |
| `meta_info_data` | 1578-1583 (Abstract) | JSON-Parse |
| `quotas` | 1647-1651 (Abstract) | |
| `cache_answers(all=True)` | 1593-1641 (Abstract) | erzeugt `self.answ` (dict `question_id -> QuestionAnswer`) und `self.questions` |
| `assign_pseudonymization_id()` | 2872-2884 | `@scopes_disabled()` |

`OrderFee` (2295-2494) hat dieselbe Manager-Struktur
(`all = ScopedManager(...)`, `objects = ActivePositionManager()`, Zeilen
2381-2382), FK `order` mit `related_name='all_fees'` (2348-2353) und die
Fee-Typen `payment`, `shipping`, `service`, `cancellation`, `insurance`,
`late`, `other`, `giftcard` (2322-2339). `OrderFee` hat **`value`** statt
`price`, mit Kompatibilitäts-Property `price` (2478-2494).

### Fallstricke (`Order` / `OrderPosition`)

1. **`positions` ≠ `all_positions`.** Für Reports über "was wurde verkauft" ist
   `positions` (ohne stornierte) fast immer richtig; für Buchhaltungsreports
   `all_positions`. Das muss in der Registry pro Feld/Report explizit
   entschieden und dokumentiert sein. Bei einem Queryset-Start über
   `OrderPosition.objects` sind stornierte bereits raus; über
   `OrderPosition.all` nicht.
2. **`OrderPosition.event` ist eine Property, kein Feld.** In `filter()` /
   `values()` / `order_by()` geht nur `order__event`. Ein Registry-Feld darf
   niemals `event` als ORM-Pfad auf `OrderPosition` erzeugen.
3. `meta_info` ist ein `TextField` mit JSON-Inhalt (nicht `JSONField`) — sowohl
   auf `Order` als auch auf `OrderPosition`. **Keine JSON-Lookups im ORM
   möglich.** `Order.api_meta` ist dagegen ein echtes `JSONField`.
4. `Order.organizer` und `OrderPosition.organizer` sind `null=True` redundante
   FKs, die nur wegen Unique-Constraints existieren (Kommentar in 222-223 bzw.
   2539-2540). Für Filter **nicht** benutzen — `event__organizer` bzw.
   `order__event__organizer` ist der verlässliche Pfad.
5. `Order.STATUS_REFUNDED` ist `"c"` und damit identisch mit
   `STATUS_CANCELED` — als deprecated markiert (199). Nicht in UI-Choices
   aufnehmen.
6. `Meta.ordering = ("-datetime", "-pk")` auf `Order` bzw.
   `("positionid", "id")` auf `OrderPosition` wird bei jedem Queryset ohne
   explizites `order_by()` angewendet. Bei Aggregationen (`values().annotate()`)
   sorgt Djangos Default-Ordering für **falsche Gruppierungen** — ein
   `.order_by()` (leer) ist bei jeder Aggregation Pflicht. Der Core macht das
   z. B. in `annotate_overpayments` (`orders.py:515`, `520`:
   `.order_by().values('order').annotate(...)`).
7. `save()` auf `OrderPosition`/`OrderFee` mit deferred fields
   (`.only()`/`.defer()`) wirft absichtlich einen Fehler
   (`orders.py:2865-2868`), weil die Transaction-Buchhaltung sonst inkonsistent
   wird. Reports lesen nur — aber bei Tests, die Positionen mit `.only()` laden
   und dann speichern, knallt es.
8. Die drei Vollständigkeits-Fallen bei Preisen: `price` ist brutto,
   `net_price`/`tax_value` separat, und die
   `*_includes_rounding_correction`-Felder plus `RoundingCorrectionMixin`
   (1464-1476) liefern die Werte "vor Rundung". Für Summenspalten muss die
   Registry festlegen, welche Variante gemeint ist.

### 6.3 `InvoiceAddress`

**Modul:** `pretix/base/models/orders.py:3360-3538`

Felder (3361-3394): `last_modified`, `order`
(`OneToOneField(Order, null=True, blank=True, related_name='invoice_address', on_delete=CASCADE)`),
`customer` (`FK(Customer, related_name='invoice_addresses')`), `is_business`,
`company`, `name_cached`, `name_parts` (`JSONField(default=dict)`), `street`,
`zipcode`, `city`, `country_old`, `country` (`FastCountryField(countries=CachedCountries)`),
`state`, `vat_id`, `vat_id_validated`, `custom_field`, `internal_reference`,
`beneficiary`, `transmission_type` (`default="email"`), `transmission_info` (`JSONField`).

Manager (3396-3397):

```python
    objects = ScopedManager(organizer='order__event__organizer')
    profiles = ScopedManager(organizer='customer__organizer')
```

Kein `Meta` mit `ordering`. Properties: `is_empty` (3450-3455), `name`
(3473ff.), `state_name`, `state_for_address`, `describe()` (3435-3448).

**Fallstricke:** `order.invoice_address` wirft
`InvoiceAddress.DoesNotExist`, wenn keine existiert — der Core fängt das
konsequent mit `try/except` ab (z. B. `orders.py:2434-2437`). In Querysets ist
der Pfad `order__invoice_address__...` bzw. von der Position aus
`order__invoice_address__...`; ein `select_related('invoice_address')` ist bei
OneToOne mit `null=True` erlaubt und liefert dann `None` statt Exception —
**aber nur beim Attributzugriff auf ein select_related-Objekt**; das übliche
Muster im Core ist trotzdem `try/except`.

### 6.4 `Question`, `QuestionOption`, `QuestionAnswer`

**`Question`** — `pretix/base/models/items.py:1606-1956`

Typkonstanten (1645-1673), wörtlich:

```python
    TYPE_NUMBER = "N"
    TYPE_STRING = "S"
    TYPE_TEXT = "T"
    TYPE_BOOLEAN = "B"
    TYPE_CHOICE = "C"
    TYPE_CHOICE_MULTIPLE = "M"
    TYPE_FILE = "F"
    TYPE_DATE = "D"
    TYPE_TIME = "H"
    TYPE_DATETIME = "W"
    TYPE_COUNTRYCODE = "CC"
    TYPE_PHONENUMBER = "TEL"
```

plus `UNLOCALIZED_TYPES = [TYPE_DATE, TYPE_TIME, TYPE_DATETIME]`,
`ASK_DURING_CHECKIN_UNSUPPORTED = []`,
`SHOW_DURING_CHECKIN_UNSUPPORTED = [TYPE_FILE]` (1671-1673).

Felder (1675-1771): `event` (FK, `related_name="questions"`), `question`
(`I18nTextField`), `identifier`, `help_text` (`I18nTextField`), `type`,
`required`, `items` (`M2M(Item, related_name='questions')`), `position`,
`ask_during_checkin`, `show_during_checkin`, `hidden`, `print_on_invoice`,
`dependency_question` (`FK('Question', on_delete=SET_NULL, related_name='dependent_questions')`),
`dependency_values` (`MultiStringField(default=[])`),
`valid_number_min/max`, `valid_date_min/max`, `valid_datetime_min/max`,
`valid_string_length_max`, `valid_file_portrait`.

Manager + Meta (1773-1779), wörtlich:

```python
    objects = ScopedManager(organizer='event__organizer')

    class Meta:
        verbose_name = _("Question")
        verbose_name_plural = _("Questions")
        ordering = ('position', 'id')
        unique_together = (('event', 'identifier'),)
```

**`Question.identifier` und seine Stabilitätsgarantien** — das ist die Grundlage
für unsere Portabilität, deshalb im Detail.

Definition (1683-1694), wörtlich:

```python
    identifier = models.CharField(
        max_length=190,
        verbose_name=_("Internal identifier"),
        help_text=_('You can enter any value here to make it easier to match the data with other sources. If you do '
                    'not input one, we will generate one automatically.'),
        validators=[
            RegexValidator(
                regex=r"^[a-zA-Z0-9.\-_]+$",
                message=_("The identifier may only contain letters, numbers, dots, dashes, and underscores."),
            ),
        ],
    )
```

Autogenerierung in `save()` (1800-1812), wörtlich:

```python
    def save(self, *args, **kwargs):
        if not self.identifier:
            charset = list('ABCDEFGHJKLMNPQRSTUVWXYZ3789')
            while True:
                code = get_random_string(length=8, allowed_chars=charset)
                if not Question.objects.filter(event=self.event, identifier=code).exists():
                    self.identifier = code
                    break
```

Eindeutigkeitsprüfung (1789-1798), **case-insensitive**:

```python
    def clean_identifier(self, code):
        Question._clean_identifier(self.event, code, self)

    @staticmethod
    def _clean_identifier(event, code, instance=None):
        qs = Question.objects.filter(event=event, identifier__iexact=code)
        if instance and instance.pk:
            qs = qs.exclude(pk=instance.pk)
        if qs.exists():
            raise ValidationError(_('This identifier is already used for a different question.'))
```

**Was garantiert ist:**
- Eindeutig **pro Event** (`unique_together (event, identifier)`, DB-Ebene).
- Nie leer: wird beim ersten `save()` automatisch erzeugt.
- Zeichensatz per Validator auf `[a-zA-Z0-9.\-_]` beschränkt (nur Form-/
  Serializer-Ebene, nicht DB-Ebene).
- **Beim Event-Kopieren bleibt der Identifier erhalten**: `copy_data_from`
  setzt nur `q.pk = None` und `q.event = self`, ohne `identifier` anzufassen
  (`pretix/base/models/event.py:1090-1099`). Das ist die Eigenschaft, auf der
  unsere Vorlagen-Portabilität aufsetzt.

**Was NICHT garantiert ist:**
- **Unveränderlichkeit.** `identifier` steht in `QuestionForm.Meta.fields`
  (`pretix/control/forms/item.py:234`) und wird für bestehende Instanzen
  **nicht** deaktiviert (`__init__`, 154-168, setzt nur `required = False`).
  Ein Nutzer kann den Identifier einer bestehenden Frage jederzeit im Backend
  ändern. Gleiches über die API: `QuestionSerializer` führt `identifier` in
  `fields` und validiert nur die Eindeutigkeit
  (`pretix/api/serializers/item.py:542-555`).
- Eindeutigkeit **über Events hinweg** — zwei Events können denselben
  Identifier für semantisch verschiedene Fragen benutzen.

→ **Konsequenz:** Import/Export von Report-Definitionen über
`Question.identifier` ist der bestmögliche Weg (besser als PKs), aber wir müssen
mit "Identifier nicht gefunden" als **regulärem, erwartbarem Zustand** umgehen,
nicht als Fehlerfall. Ein Report muss auch dann noch ladbar/anzeigbar sein.

**`QuestionOption`** (`items.py:1958-2000`): `question`
(`FK(related_name='options')`), `identifier`, `answer` (`I18nCharField`),
`position`; `Meta.ordering = ('position', 'id')`. Auch hier gibt es
`clean_identifier` (Nutzung in `api/serializers/item.py:518-520`).

**`QuestionAnswer`** — `pretix/base/models/orders.py:1317-1461`, wörtlich
(1332-1355):

```python
    orderposition = models.ForeignKey(
        'OrderPosition', null=True, blank=True,
        related_name='answers', on_delete=models.CASCADE
    )
    cartposition = models.ForeignKey(
        'CartPosition', null=True, blank=True,
        related_name='answers', on_delete=models.CASCADE
    )
    question = models.ForeignKey(
        Question, related_name='answers', on_delete=models.CASCADE
    )
    options = models.ManyToManyField(
        QuestionOption, related_name='answers', blank=True
    )
    answer = models.TextField()
    file = models.FileField(
        null=True, blank=True, upload_to=answerfile_name,
        max_length=255
    )

    objects = ScopedManager(organizer='question__event__organizer')

    class Meta:
        unique_together = [['orderposition', 'question'], ['cartposition', 'question']]
```

Darstellung: `to_string(use_cached=True)` (1402-1449) — Boolean wird zu
"Yes"/"No", Datum/Zeit lokalisiert, Choice-Fragen entweder aus dem gecachten
`answer`-Text (schnell) oder aus `options` (aktuell + übersetzt).
`__str__` ruft `to_string(use_cached=True)` (1396-1397).

**Fallstricke (Questions):**
1. `QuestionAnswer.answer` ist **immer** ein `TextField`, egal welcher
   Fragetyp. Numerische oder Datumsfilter auf Antworten sind ohne Cast
   unzuverlässig; Sortierung ist lexikografisch. Wenn die Registry
   Antwort-Felder anbietet, muss der Typ aus `Question.type` abgeleitet und
   die Einschränkung dokumentiert werden.
2. `unique_together` erlaubt genau **eine Antwort pro (Position, Frage)** — ein
   `LEFT JOIN` über `answers` mit Filter auf `question` multipliziert die
   Zeilen also nicht, solange man auf genau eine Frage filtert. Bei mehreren
   Fragen in einem Join sehr wohl.
3. `to_string(use_cached=False)` macht **eine Extra-Query pro Antwort**
   (`self.options.all()`, 1447). In einem Report über 10.000 Positionen ist das
   fatal — entweder `use_cached=True` oder `prefetch_related('options')`.
4. `QuestionAnswer.objects` ist über `question__event__organizer` gescopet, also
   über einen **anderen** Pfad als `OrderPosition` — bei `scopes_disabled()`
   irrelevant, im Scope aber ein zusätzlicher Join.

### 6.5 `Item`, `ItemVariation`

**`Item`** — `pretix/base/models/items.py:360-1113`, `class Item(LoggedModel)`.

Manager (467):

```python
    objects = ItemQuerySetManager()
```

mit (346-357):

```python
class ItemQuerySet(models.QuerySet):
    def filter_available(self, channel='web', voucher=None, allow_addons=False, allow_cross_sell=False):
        return filter_available(self, channel, voucher, allow_addons, allow_cross_sell)


class ItemQuerySetManager(ScopedManager(organizer='event__organizer').__class__):
    def __init__(self):
        super().__init__()
        self._queryset_class = ItemQuerySet

    def filter_available(self, channel='web', voucher=None, allow_addons=False, allow_cross_sell=False):
        return filter_available(self.get_queryset(), channel, voucher, allow_addons, allow_cross_sell)
```

Meta (798-801), wörtlich:

```python
    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")
        ordering = ("category__position", "category", "position", "pk")
```

Felder (469-793) — für Reports besonders relevant: `event`
(`related_name="items"`), `category` (`FK(ItemCategory)`), `name`
(`I18nCharField`), `internal_name` (`CharField`), `active`, `description`
(`I18nTextField`), `default_price` (`DecimalField`), `free_price`,
`free_price_suggestion`, `tax_rule` (`FK(TaxRule)`), `admission`,
`personalized`, `generate_tickets`, `allow_waitinglist`, `show_quota_left`,
`position`, `picture`, `available_from`/`available_until` (+ `_mode`),
`hidden_if_available` (`FK(Quota)`), `hidden_if_item_available` (`FK(Item)`),
`require_voucher`, `require_approval`, `hide_without_voucher`,
`require_bundling`, `allow_cancel`, `min_per_order`, `max_per_order`,
`checkin_attention`, `checkin_text`, `original_price`, `all_sales_channels`,
`limit_sales_channels` (M2M), `issue_giftcard`, `require_membership`,
`require_membership_types` (M2M), `require_membership_hidden`,
`grant_membership_type`, `grant_membership_duration_*`, `validity_mode`,
`validity_fixed_from`/`_until`, `validity_dynamic_*`, `media_policy`,
`media_type`.

Wichtig: `__str__` liefert `str(self.internal_name or self.name)` (803-804).
Für Report-Spalten "Produktname" muss entschieden werden, ob der interne oder
der öffentliche Name gemeint ist.

**`ItemVariation`** — `items.py:1126-1441`, `class ItemVariation(models.Model)`.

Felder (1151-1263): `item` (`FK`), `value` (`I18nCharField`), `active`,
`description` (`I18nTextField`), `position`, `default_price`, `original_price`,
`free_price_suggestion`, `require_approval`, `require_membership`,
`require_membership_types`, `require_membership_hidden`, `available_from`,
`available_from_mode`, `available_until`, `available_until_mode`,
`all_sales_channels`, `limit_sales_channels`, `hide_without_voucher`,
`checkin_attention`, `checkin_text`.

Manager + Meta (1264-1269):

```python
    objects = ScopedManager(organizer='item__event__organizer')

    class Meta:
        ...
        ordering = ("position", "id")
```

### 6.6 `SubEvent`

**Modul:** `pretix/base/models/event.py:1545-1786`,
`class SubEvent(EventMixin, LoggedModel)`.

Felder (1570-1632): `event` (`FK(Event, related_name="subevents", on_delete=PROTECT)`),
`active`, `is_public`, `name` (`I18nCharField`), `date_from`, `date_to`,
`date_admission`, `presale_end`, `presale_start`, `location` (`I18nTextField`),
`geo_lat`, `geo_lon`, `frontpage_text` (`I18nTextField`), `seating_plan`
(`FK(SeatingPlan, on_delete=PROTECT)`), `comment`, `last_modified`.

Manager + Meta (1635-1640):

```python
    objects = ScopedManager(organizer='event__organizer')

    class Meta:
        ...
        ordering = ("date_from", "name")
```

`meta_data` (1724-1731), wörtlich:

```python
    @property
    def meta_data(self):
        data = self.event.meta_data
        if hasattr(self, 'meta_values_cached'):
            data.update({v.property.name: v.value for v in self.meta_values_cached})
        else:
            data.update({v.property.name: v.value for v in self.meta_values.select_related('property').all()})
        return data
```

### 6.7 Meta-Properties (Event, SubEvent, Item, ItemVariation)

| Modell | Modul:Zeilen | Zweck |
| --- | --- | --- |
| `EventMetaProperty` | `event.py:1794-1862` | Definition auf Organizer-Ebene (`organizer`, `name`, `default`, `protected`, `required`, `choices` als `JSONField`), `Meta.ordering = ("position", "name",)` |
| `EventMetaValue` | `event.py:1864-1895` | `event` + `property` + `value`, `unique_together ('event','property')` |
| `SubEventMetaValue` | `event.py:1897-1926` | `subevent` + `property` + `value`, `unique_together ('subevent','property')` |
| `ItemMetaProperty` | `items.py:2230-2268` | pro Event (`event`, `name`, `default`, `required`, `allowed_values`), `Meta.ordering = ("name",)` |
| `ItemMetaValue` | `items.py:2271-2288` | `item` + `property` + `value`, `unique_together ('item','property')` |
| `ItemVariationMetaValue` | `items.py:2290-2307` | `variation` + `property` + `value` |

Zugriff über `Event.meta_data` (`event.py:1365-1373`), wörtlich:

```python
    @property
    def meta_data(self):
        data = {p.name: p.default for p in self.organizer.meta_properties.all()}
        if hasattr(self, 'meta_values_cached'):
            data.update({v.property.name: v.value for v in self.meta_values_cached})
        else:
            data.update({v.property.name: v.value for v in self.meta_values.select_related('property').all()})

        return OrderedDict((k, v) for k, v in sorted(data.items(), key=lambda k: k[0]))
```

Property-Namen sind per Validator auf `^[a-zA-Z0-9_]+$` beschränkt
(`event.py:1808-1813`, `items.py:2244-2253`).

**Fallstricke (Meta):** `meta_data` ist eine Python-Property mit Defaults aus
den Property-Definitionen — **kein** ORM-Feld. Filtern geht nur über
`meta_values__property__name=...` + `meta_values__value=...`, und dabei fehlen
die Defaults für Events, die keinen expliziten Wert gesetzt haben. Ein
Registry-Feld "Event-Metadatum X" muss diese Asymmetrie zwischen Anzeige und
Filter explizit behandeln.

### 6.8 `Seat`

**Modul:** `pretix/base/models/seating.py:169-256`

```python
class Seat(models.Model):
    event = models.ForeignKey(Event, related_name='seats', on_delete=models.CASCADE)
    subevent = models.ForeignKey(SubEvent, null=True, blank=True, related_name='seats', on_delete=models.CASCADE)
    zone_name = models.CharField(max_length=190, blank=True, default="")
    row_name = models.CharField(max_length=190, blank=True, default="")
    row_label = models.CharField(max_length=190, null=True)
    seat_number = models.CharField(max_length=190, blank=True, default="")
    seat_label = models.CharField(max_length=190, null=True)
    seat_guid = models.CharField(max_length=190, db_index=True)
    product = models.ForeignKey('Item', null=True, blank=True, related_name='seats', on_delete=models.SET_NULL)
    blocked = models.BooleanField(default=False)
    sorting_rank = models.BigIntegerField(default=0)
    x = models.FloatField(null=True)
    y = models.FloatField(null=True)

    class Meta:
        ordering = ['sorting_rank', 'seat_guid']
```

`name` ist eine Property, die `__str__` zurückgibt (191-193); `__str__`
(195-212) baut den Anzeigenamen aus `zone_name`, `row_label`/`row_name`,
`seat_label`/`seat_number` zusammen und fällt auf `seat_guid` zurück.

**Fallstrick:** `Seat` hat **keinen `ScopedManager`** — `seating.py` importiert
`django_scopes` nicht (verifiziert per Grep über `base/models/seating.py`).
`Seat.objects` ist also ungescopet. Wir müssen bei jedem Zugriff selbst auf
`event=` filtern. Ebenso: Für Report-Spalten ist der Anzeigename nur in Python
verfügbar (`__str__`), nicht als DB-Ausdruck — sortierbar ist nur
`sorting_rank`/`seat_guid`/`row_name`/`seat_number` einzeln.

### 6.9 `OrderPayment`, `OrderRefund`

**`OrderPayment`** — `pretix/base/models/orders.py:1715-2116`

Zustände (1745-1759), wörtlich:

```python
    PAYMENT_STATE_CREATED = 'created'
    PAYMENT_STATE_PENDING = 'pending'
    PAYMENT_STATE_CONFIRMED = 'confirmed'
    PAYMENT_STATE_FAILED = 'failed'
    PAYMENT_STATE_CANCELED = 'canceled'
    PAYMENT_STATE_REFUNDED = 'refunded'
```

Felder (1760-1796): `local_id` (`PositiveIntegerField`, pro Order fortlaufend),
`state`, `amount` (`DecimalField(2,13)`), `order`
(`FK(related_name='payments', on_delete=PROTECT)`), `created` (`auto_now_add`),
`payment_date`, `provider` (`CharField(max_length=255)`), `info` (`TextField`,
JSON als Text), `fee` (`FK(OrderFee, related_name='payments', on_delete=SET_NULL)`),
`migrated`, `process_initiated`.

```python
    objects = ScopedManager(organizer='order__event__organizer')     # 1798

    class Meta:
        ordering = ('local_id',)                                     # 1800-1801
```

`info_data` ist eine Property mit JSON-Parse (1806-1812); `__str__` gibt
`self.full_id`.

**`OrderRefund`** — `orders.py:2118-2287`

Zustände und Quellen (2145-2173), wörtlich gekürzt:

```python
    REFUND_STATE_EXTERNAL = 'external'
    REFUND_STATE_TRANSIT = 'transit'
    REFUND_STATE_DONE = 'done'
    REFUND_STATE_CANCELED = 'canceled'
    REFUND_STATE_CREATED = 'created'
    REFUND_STATE_FAILED = 'failed'

    REFUND_SOURCE_BUYER = 'buyer'
    REFUND_SOURCE_ADMIN = 'admin'
    REFUND_SOURCE_EXTERNAL = 'external'
```

Felder (2175-2217): `local_id`, `state`, `source`, `amount`, `order`
(`related_name='refunds'`), `payment` (`FK(OrderPayment, related_name='refunds', on_delete=PROTECT)`),
`created`, `execution_date`, `provider`, `comment`, `info`.

```python
    objects = ScopedManager(organizer='order__event__organizer')     # 2219

    class Meta:
        ordering = ('local_id',)                                     # 2221-2222
```

**Fallstricke:** Für Zahlungssummen zählen laut Core nur bestimmte Zustände —
Zahlungen: `confirmed` und `refunded`; Rückerstattungen: `done`, `transit`,
`created` (`orders.py:486-492`). Wer in einem Report "bezahlt" oder "erstattet"
summiert, muss exakt diese Mengen verwenden, sonst weichen die Zahlen von der
pretix-Oberfläche ab. `info` ist wieder JSON-im-TextField.

### 6.10 `Checkin`, `CheckinList`

**Modul:** `pretix/base/models/checkin.py`

`CheckinList` (52-321): `event` (`related_name='checkin_lists'`), `name`,
`all_products`, `limit_products` (M2M `Item`), `subevent`,
`ignore_in_statistics`, `consider_tickets_used`, `include_pending`,
`addon_match`, `gates` (M2M), `allow_entry_after_exit`,
`allow_multiple_entries`, `exit_all_at`, `rules` (`JSONField(default=dict)`).

```python
    objects = ScopedManager(organizer='event__organizer')            # 104

    class Meta:
        ordering = ('subevent__date_from', 'name', 'pk')             # 106-107
```

`Checkin` (329-477, Dateiende). Typen und Fehlergründe (333-375): `TYPE_ENTRY = 'entry'`,
`TYPE_EXIT = 'exit'`; `REASON_*` u. a. `canceled`, `invalid`, `unpaid`,
`product`, `rules`, `revoked`, `incomplete`, `already_redeemed`, `ambiguous`,
`medium_invalid`, `medium_exists`, `error`, `blocked`, `unapproved`,
`invalid_time`, `annulled`, `already_exchanged`.

Felder (377-450): `successful` (`BooleanField(default=True)`), `error_reason`,
`error_explanation`, `position`
(`FK(OrderPosition, related_name='all_checkins', on_delete=CASCADE, null=True)`),
`raw_barcode`, `raw_source_type`, `raw_item`, `raw_variation`, `raw_subevent`,
`datetime` (`default=now`), `created` (`auto_now_add`), `list`
(`FK(CheckinList, related_name='checkins', on_delete=PROTECT)`), `type`,
`nonce`, `force_sent`, `forced`, `device`, `gate`, `auto_checked_in`.

Manager + Meta (452-456), wörtlich:

```python
    all = ScopedManager(organizer='list__event__organizer')
    objects = SuccessfulCheckinManager()

    class Meta:
        ordering = (('-datetime'),)
```

mit (324-327):

```python
class SuccessfulCheckinManager(ScopedManager(organizer='list__event__organizer').__class__):
    def get_queryset(self):
        return super().get_queryset().filter(successful=True)
```

**Fallstricke:** Dieselbe `objects`/`all`-Falle wie bei `OrderPosition`, nur
mit anderem Kriterium: `Checkin.objects` enthält **nur erfolgreiche** Scans.
Fehlversuche brauchen `Checkin.all`. Ebenso ist
`OrderPosition.checkins` (Property, 2662-2668) gefiltert, während
`OrderPosition.all_checkins` (der echte `related_name`) alles enthält. In
ORM-Lookups von der Position aus (`all_checkins__...`) ist **kein** Filter
aktiv — dort muss `successful=True` explizit mit in die Bedingung. Außerdem:
Ein Checkin kann `position=None` haben (Raw-Scans, 391-396) und `type` kann
`exit` sein — "Anzahl Check-ins" ist also selten einfach `count()`.

### 6.11 `Voucher`

**Modul:** `pretix/base/models/vouchers.py:126-...`

Felder (177-310): `created`, `event` (FK), `subevent` (FK), `code`,
`max_usages`, `redeemed`, `min_usages`, `budget`, `valid_until`, `block_quota`,
`allow_ignore_quota`, `price_mode`, `value`, `item` (FK), `variation` (FK),
`quota` (FK), `seat` (FK), `tag`, `comment`, `show_hidden_items`,
`all_addons_included`, `all_bundles_included`.

```python
    objects = ScopedManager(organizer='event__organizer')            # 312

    class Meta:
        ...
        ordering = ('code', )                                        # 314-318
```

Nützliche Methoden für Reports: `is_active()` (565), `calculate_price(...)`
(576), `distinct_orders()` (602), `annotate_budget_used(cls, qs)`
(`@classmethod`, 626), `budget_used()` (637), `min_usages_remaining` (622).

### 6.12 `Transaction` (nur zur Einordnung)

`orders.py:2978-3156`, `Meta.ordering = 'datetime', 'pk'` (3102-3103). Das ist
pretix' buchhalterisches Append-only-Journal über Positions-/Fee-Änderungen. Für
Finanzreports ist das die korrektere Quelle als `OrderPosition`; für "was ist
aktuell gebucht" ist `OrderPosition` richtig. Der Core exportiert es separat als
`TransactionListExporter` (`base/exporters/orderlist.py:879-1057`).

---

## 7. `django-scopes`

**Version:** 2.0.0

### 7.1 Wo pretix Scopes setzt

| Kontext | Fundstelle | Code |
| --- | --- | --- |
| Control-Backend | `pretix/control/middleware.py:199` | `with scope(organizer=getattr(request, 'organizer', None)):` |
| Control, Event-Lookup vor Scope | `control/middleware.py:162-166` | `with scope(organizer=None): request.event = Event.objects.filter(...)` |
| Presale | `pretix/presale/middleware.py:67` | `with scope(organizer=getattr(request, 'organizer', None)), ...` |
| API | `pretix/api/middleware.py:140` | `with scope(organizer=getattr(request, 'organizer', None)):` |
| Multidomain | `pretix/multidomain/middlewares.py:118, 136` | `with scopes_disabled():` für Domain-Auflösung |
| Webhooks | `pretix/api/webhooks.py:584-587, 676-678, 690` | Mischung aus `scopes_disabled()` und `scope(organizer=...)` |
| Management-Command Export | `base/management/commands/export.py:65` | `with scope(organizer=o):` |

### 7.2 Verhalten in Celery-Tasks

**Das ist die für uns wichtigste Stelle.** `pretix/base/services/tasks.py`:

`EventTask` (87-109), wörtlich:

```python
class EventTask(app.Task):
    def __call__(self, *args, **kwargs):
        if 'event_id' in kwargs:
            event_id = kwargs.get('event_id')
            with scopes_disabled():
                event = Event.objects.select_related('organizer').get(pk=event_id)
            del kwargs['event_id']
            kwargs['event'] = event
        elif 'event' in kwargs:
            event_id = kwargs.get('event')
            with scopes_disabled():
                event = Event.objects.select_related('organizer').get(pk=event_id)
            kwargs['event'] = event
        else:
            args = list(args)
            event_id = args[0]
            with scopes_disabled():
                event = Event.objects.select_related('organizer').get(pk=event_id)
            args[0] = event

        with scope(organizer=event.organizer):
            ret = super().__call__(*args, **kwargs)
        return ret
```

`OrganizerTask` (112-134) und `OrganizerUserTask` (137-151) machen dasselbe mit
`Organizer`. Kombinationen: `ProfiledEventTask` (154-155),
`ProfiledOrganizerUserTask` (158-159), `TransactionAwareTask` (162-175),
`TransactionAwareProfiledEventTask` (178-187).

**Konsequenz:** Ein Task mit `base=EventTask` bekommt die Event-ID als erstes
Positionsargument oder als kwarg `event`/`event_id` und läuft **automatisch im
richtigen Scope**. Innerhalb eines solchen Tasks ist `scopes_disabled()` weder
nötig noch erwünscht. Genau so laufen `scheduled_event_export`
(`base=EventTask`) und `scheduled_organizer_export` (`base=OrganizerTask`)
(`services/export.py:435`, `469`).

Der periodische Auslöser dagegen läuft ohne Organizer-Kontext und ist deshalb
explizit `@scopes_disabled()` dekoriert (`services/export.py:496-499`).

### 7.3 Wo `scopes_disabled()` nötig ist

1. In Code, der **vor** der Organizer-Auflösung läuft (Middleware, Domain-Routing).
2. In `periodic_task`-Receivern und anderen globalen Tasks — Vorbild
   `run_scheduled_exports` (`services/export.py:496-499`).
3. Bei globalen Uniqueness-Prüfungen über alle Organizer hinweg — Vorbild
   `OrderPosition.assign_pseudonymization_id` (`orders.py:2872-2884`), das
   `@scopes_disabled()` als Dekorator **und** `with scopes_disabled():` innen
   benutzt.
4. In eigenen Properties, die auf Related Manager zugreifen und aus einem
   scope-losen Kontext gerufen werden können — Vorbild `Order.count_positions`,
   `Order.payment_refund_sum`, `Order.pending_sum`
   (`orders.py:459-460`, `483-484`, `495-496`), jeweils
   `@property` + `@scopes_disabled()`.
5. `User.get_events_with_permission` / `get_events_with_any_permission` sind
   selbst `@scopes_disabled()` (`base/models/auth.py:573`, `591`) — wir dürfen
   deren Rückgabe-Queryset also in beliebigem Scope weiterverwenden, aber ein
   `.filter(organizer=...)` bleibt trotzdem Pflicht, wenn wir nur einen
   Organizer wollen (so macht es `init_organizer_exporters`,
   `services/export.py:274-285`).

### 7.4 Welche Modelle gescopet sind (Übersicht)

| Modell | Scope-Pfad | Fundstelle |
| --- | --- | --- |
| `Event` | `organizer` | `event.py:699` |
| `SubEvent` | `event__organizer` | `event.py:1635` |
| `Order` | `event__organizer` | `orders.py:332` |
| `OrderPosition` (`all`) | `order__event__organizer` | `orders.py:2602` |
| `OrderFee` (`all`) | `order__event__organizer` | `orders.py:2381` |
| `OrderPayment` | `order__event__organizer` | `orders.py:1798` |
| `OrderRefund` | `order__event__organizer` | `orders.py:2219` |
| `QuestionAnswer` | `question__event__organizer` | `orders.py:1352` |
| `InvoiceAddress` (`objects`) | `order__event__organizer` | `orders.py:3396` |
| `InvoiceAddress` (`profiles`) | `customer__organizer` | `orders.py:3397` |
| `Item` | `event__organizer` | `items.py:351`, `467` |
| `ItemVariation` | `item__event__organizer` | `items.py:1264` |
| `Question` | `event__organizer` | `items.py:1773` |
| `Voucher` | `event__organizer` | `vouchers.py:312` |
| `CheckinList` | `event__organizer` | `checkin.py:104` |
| `Checkin` (`all`) | `list__event__organizer` | `checkin.py:452` |
| **`Seat`** | **keiner** | `seating.py` importiert `django_scopes` nicht |

### 7.5 Tests

Der Automatismus aus `src/tests/conftest.py:67-77`, wörtlich:

```python
@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    """
    This hack automatically disables django-scopes for all fixtures which are not yield fixtures.
    This saves us a *lot* of decorcators…
    """
    if inspect.isgeneratorfunction(fixturedef.func):
        yield
    else:
        with scopes_disabled():
            yield
```

**gilt nur innerhalb des pretix-Repos**, weil er in dessen `conftest.py` steht.
Für unser Out-of-tree-Plugin müssen wir entweder denselben Hook in unsere eigene
`conftest.py` kopieren oder in jeder Fixture explizit `scopes_disabled()` /
`scope(organizer=...)` verwenden. Das hat `bootstrap-dev` bereits festgestellt
(`docs/adr/0000-setup.md`); hier ist die Fundstelle dazu.

Beachte: Fixtures, die selbst `yield` benutzen (Generator-Fixtures), sind vom
Automatismus **ausgenommen** — typisches Muster im Core ist dann
`with scope(organizer=o): yield event` (`tests/base/test_export.py:36-46`).

### Fallstricke (Scopes)

1. Ein `ScopedManager`-Queryset ohne aktiven Scope wirft
   `ScopeError` beim Auswerten, nicht beim Bauen. Fehler tauchen also erst dort
   auf, wo iteriert wird — gerne mitten im Exporter.
2. `scopes_disabled()` schaltet die Mandantentrennung **komplett** aus. Es ist
   kein Ersatz für ein `filter(event=...)`; `CLAUDE.md` Regel 4 gilt zusätzlich.
   Faustregel für uns: `scopes_disabled()` nur dort, wo pretix es selbst tut,
   und das Queryset immer trotzdem hart auf Event/erlaubte Events einschränken.
3. `Seat` ist ungescopet — dort trägt der Scope keine Sicherheit bei, das
   `event=`-Filter ist die einzige Absicherung.

---

## 8. Permissions

### 8.1 Permission-Strings (2026.6.0)

Die gültigen Permission-Strings entstehen aus `PermissionGroup`-Objekten
(`pretix/base/permissions.py:62-67`) in der Form `"<group.name>:<action>"`
(`get_all_event_permissions`, 94-102).

**Event-Ebene** (`permissions.py:183-257`), vollständig:

| Gruppe | `actions` | Daraus gültige Strings |
| --- | --- | --- |
| `event.settings.general` | `["write"]` | `event.settings.general:write` |
| `event.settings.payment` | `["write"]` | `event.settings.payment:write` |
| `event.settings.tax` | `["write"]` | `event.settings.tax:write` |
| `event.settings.invoicing` | `["write"]` | `event.settings.invoicing:write` |
| `event.subevents` | `["write"]` | `event.subevents:write` |
| `event.items` | `["write"]` | `event.items:write` |
| `event.orders` | `["read", "write", "checkin"]` | `event.orders:read`, `event.orders:write`, `event.orders:checkin` |
| `event.vouchers` | `["read", "write"]` | `event.vouchers:read`, `event.vouchers:write` |
| `event` | `["cancel"]` | `event:cancel` |

**Damit bestätigt: `event.items:read` existiert nicht.** Die Gruppe
`event.items` hat nur `actions=["write"]` (`permissions.py:219-225`). Die
Options-Liste `OPTS_ALL_READ` (162-165) modelliert "View" als *leeres*
Action-Tupel — Lesen ist also implizit durch jeglichen Event-Zugang gegeben und
nicht als eigener String abfragbar.

**Organizer-Ebene** (`permissions.py:260-334`), vollständig:
`organizer.events:create`, `organizer.settings.general:write`,
`organizer.teams:write`, `organizer.giftcards:read|write`,
`organizer.customers:read|write`, `organizer.reusablemedia:read|write`,
`organizer.devices:read|write`, `organizer.seatingplans:write`,
`organizer.outgoingmails:read`.

**Legacy-Mapping** (`pretix/helpers/permission_migration.py:56-72`), wörtlich:

```python
OLD_TO_NEW_EVENT_COMPAT = {
    "can_change_event_settings": ["event.settings.general:write"],
    "can_change_items": ["event.items:write"],
    "can_view_orders": ["event.orders:read"],
    "can_change_orders": ["event.orders:write"],
    "can_checkin_orders": ["event.orders:checkin"],
    "can_view_vouchers": ["event.vouchers:read"],
    "can_change_vouchers": ["event.vouchers:write"],
}
OLD_TO_NEW_ORGANIZER_COMPAT = {
    "can_create_events": ["organizer.events:create"],
    "can_change_organizer_settings": ["organizer.settings.general:write"],
    "can_change_teams": ["organizer.teams:write"],
    "can_manage_gift_cards": ["organizer.giftcards:read", "organizer.giftcards:write"],
    "can_manage_customers": ["organizer.customers:read", "organizer.customers:write"],
    "can_manage_reusable_media": ["organizer.reusablemedia:read", "organizer.reusablemedia:write"],
}
```

Die Legacy-Keys sind noch **gültige Eingaben** (`get_all_event_permissions`
nimmt sie in die erlaubte Menge auf, `permissions.py:96-102`), aber wir
verwenden ausschließlich die Doppelpunkt-Form.

Validierung: `assert_valid_event_permission(permission, allow_legacy=True, allow_tuple=True)`
(`permissions.py:117-135`) wirft **Exception + Warning** bei unbekanntem String.
Das passiert beim `as_view()` des Mixins, also zur URLconf-Ladezeit — ein Typo
legt die ganze Installation lahm, nicht nur die eine View.

`AnyPermissionOf` (`permissions.py:157-159`) erlaubt Listen von Alternativen:

```python
class AnyPermissionOf(list):
    def __init__(self, *items):
        super().__init__(items)
```

`has_event_permission` behandelt Listen/Tupel als ODER
(`base/models/auth.py:547-548`).

### 8.2 Permission-Mixins in `pretix/control/`

**Modul:** `pretix/control/permissions.py` — exakte Klassennamen und Signaturen:

```python
def event_permission_required(permission):                        # 54-79
class EventPermissionRequiredMixin:                               # 82-92
    permission = None  # None means "any permission"
    @classmethod
    def as_view(cls, **initkwargs):
        view = super(EventPermissionRequiredMixin, cls).as_view(**initkwargs)
        return event_permission_required(cls.permission)(view)

def organizer_permission_required(permission):                    # 95-118
class OrganizerPermissionRequiredMixin:                           # 121-131
    permission = None  # None means "any permission"

def administrator_permission_required():                          # 134-150
def staff_member_required():                                      # 153-167
class AdministratorPermissionRequiredMixin:                       # 170-178
class StaffMemberRequiredMixin:                                   # 181-189
```

Verwendung (aus `control/views/orders.py:2789-2790`):

```python
class ExportView(EventPermissionRequiredMixin, ExportMixin, ListView):
    permission = None
```

`permission = None` heißt "irgendein Zugriff auf das Event genügt" — die
Feinprüfung passiert dann pro Exporter in `init_event_exporters`.

Es gibt **kein** `UserPermissionRequiredMixin` und kein
`EventPermissionRequiredMixin` mit Objekt-Level-Prüfung. Weiteres Mixin:
`EventBasedFormMixin` (`pretix/control/views/__init__.py:32`) — reicht das Event
an Formulare durch, keine Permission-Logik.

`event_permission_required` enthält eine Legacy-Sonderbehandlung
(`permissions.py:59-61`):

```python
    if permission == 'can_change_settings':
        # Legacy support
        permission = 'event.settings.general:write'
```

und `organizer_permission_required` mappt
`'event.settings.general:write'`/`'can_change_settings'`/`'can_change_event_settings'`
auf `'organizer.settings.general:write'` (100-102).

### 8.3 Permission-Abfrage-API

`pretix/base/models/auth.py`, Protokoll (71-76):

```python
class PermissionHolder(Protocol):
    def has_event_permission(self, organizer, event, perm_name=None, request=None, session_key=None) -> bool:
        ...

    def has_organizer_permission(self, organizer, perm_name=None, request=None):
        ...
```

`User`-Implementierung:

```python
    def get_event_permission_set(self, organizer, event) -> set:                                   # 498-511
    def get_organizer_permission_set(self, organizer) -> set:                                      # 513-525
    def has_event_permission(self, organizer, event, perm_name=None, request=None, session_key=None) -> bool:   # 527-551
    def has_organizer_permission(self, organizer, perm_name=None, request=None):                   # 553-571
    @scopes_disabled()
    def get_events_with_any_permission(self, request=None):                                        # 573-589
    @scopes_disabled()
    def get_events_with_permission(self, permission, request=None):                                # 591-613
```

Aus dem Docstring von `has_event_permission` (528-539), wörtlich:

> Either ``request`` or ``session_key`` are required to detect staff sessions properly.
> :param perm_name: The permission, e.g. ``event.orders:read``

Dieselben Methoden existieren auf `Device` und `TeamAPIToken`
(`base/models/organizer.py:544-620`) sowie auf `Team` in der Ein-Argument-Form
`has_event_permission(self, perm_name)` (`organizer.py:460-470`).

### Fallstricke (Permissions)

1. **Ein ungültiger Permission-String ist ein harter Fehler zur Importzeit**
   (`assert_valid_event_permission`, 131-135). Deshalb: die Strings in diesem
   Dokument sind maßgeblich, nicht Vermutungen.
2. `has_event_permission(...)` ohne `request`/`session_key` erkennt **keine
   Staff-Session** (Assert in 541, Prüfung in 542). Das ist an manchen Stellen
   Absicht (siehe `has_permission_to_create_scheduled`,
   `control/views/orders.py:2921-2924`).
3. Für Produkte/Fragen gibt es nur `event.items:write`. Ein Report-Builder, der
   Produktnamen anzeigt, darf das **nicht** als Voraussetzung fordern —
   `event.orders:read` ist der richtige Gate-Key, denn genau den verlangt auch
   `BaseExporter.get_required_event_permission()`
   (`base/exporter.py:192-199`).
4. `request.eventpermset` ist ein `EventPermissionSet` bzw.
   `SuperuserPermissionSet` (`base/models/auth.py:220-225`) — bei
   Staff-Session enthält `in`-Prüfung **immer** `True`. Nicht als Liste
   iterieren.

---

## 9. `log_action`

**Modul:** `pretix/base/models/base.py`

Signatur (103), wörtlich:

```python
class LoggingMixin:

    def log_action(self, action, data=None, user=None, api_token=None, auth=None, save=True):
        """
        Create a LogEntry object that is related to this object.
        See the LogEntry documentation for details.

        :param action: The namespaced action code
        :param data: Any JSON-serializable object
        :param user: The user performing the action (optional)
        """
```

Verhalten (121-178):
- Event/Organizer werden automatisch aus dem Objekt abgeleitet: `Organizer` →
  `organizer_id`; `Event` → `event` + `organizer_id`; sonst `self.event` bzw.
  `self.organizer_id` bzw. `self.issuer_id` (123-134).
- Nicht authentifizierte User werden zu `None` (136-137).
- `auth` wird nach Typ auf `oauth_application` / `api_token` / `device`
  gemappt (139-149).
- **`data` muss ein `dict` sein** — alles andere wirft
  `TypeError("You should only supply dictionaries as log data.")` (162-163).
- **Automatische Maskierung:** Keys, die `password`, `secret` oder `api_key`
  enthalten, werden zu `"********"` (153-159), wörtlich:
  ```python
        if isinstance(data, dict):
            sensitivekeys = ['password', 'secret', 'api_key']

            for sensitivekey in sensitivekeys:
                for k, v in data.items():
                    if (sensitivekey in k) and v:
                        data[k] = "********"
  ```
  Achtung: Das mutiert das übergebene Dict **in place**.
- Serialisierung mit `json.dumps(data, cls=CustomJSONEncoder, sort_keys=True)` (161).
- `save=False` liefert das ungespeicherte `LogEntry` zurück, für
  `LogEntry.bulk_create_and_postprocess(...)` (Vorbild:
  `orders.py:367-379`).
- Bei `save=True` werden Notifications und Webhooks angestoßen (167-176).

`LoggedModel` (181-230) erbt von `models.Model` und `LoggingMixin` und bietet
zusätzlich `all_logentries()`, `top_logentries()`, `all_logentries_link`.

### Konventionen für Action-Types

Aus den Core-Beispielen:

| Action-Type | Fundstelle |
| --- | --- |
| `pretix.event.order.deleted` | `orders.py:373` |
| `pretix.object.cloned` | `event.py:1099`, `1139` |
| `pretix.event.export.schedule.added` / `.changed` | `control/views/orders.py:2838` |
| `pretix.event.export.schedule.deleted` | `control/views/orders.py:2969` |
| `pretix.event.export.schedule.failed` | `services/export.py:331` |
| `pretix.event.export.schedule.executed` | `services/export.py:424` |
| `pretix.event.order.email.sent` | `orders.py:2891` (Default-Parameter) |

Muster: `pretix.<scope>.<objekt>.<verb im Perfekt>`, alles klein, punktgetrennt.
Für Plugins ist die Konvention `plugins.<pluginname>.<objekt>.<verb>` —
Beispiel aus dem Core-Plugin banktransfer/badges vorhanden; für unser Plugin
schlage ich `pretix_custom_reports.<objekt>.<verb>` bzw.
`plugins.custom_reports.*` vor. **Die endgültige Festlegung gehört in einen
Contract, nicht in diese Notizen.**

**Registry für die Anzeige:** Seit einiger Zeit sollen Log-Typen über
`pretix/base/logentrytype_registry.py` registriert werden statt über die
Signale `logentry_display` / `logentry_object_link`. Die beiden Signale sind
explizit als deprecated markiert (`base/signals.py:879-893`), wörtlich:

> **DEPRECTATION:** Please do not use this signal for new LogEntry types. Use the log_entry_types
> registry instead, as described in https://docs.pretix.eu/en/latest/development/implementation/logging.html

Die Registry (`logentrytype_registry.py:58-105`) verlangt Instanzen von
`LogEntryType` (108-151) und bietet den Decorator `new_from_dict(data)`
(72-96), Beispiel aus dem Docstring wörtlich:

```python
            @log_entry_types.new_from_dict({
                'pretix.event.item.added': _('The product has been created.'),
                'pretix.event.item.changed': _('The product has been changed.'),
                # ...
            })
            class CoreItemLogEntryType(ItemLogEntryType):
                # ...
```

`LogEntryTypeRegistry.register` lehnt Klassen aus `pretix.base.` ab
(67-68) — man registriert immer eine eigene Subklasse.

### Fallstricke (`log_action`)

1. `data` wird **in place** maskiert. Wer dasselbe Dict danach noch verwendet,
   bekommt `"********"` zurück. Immer eine Kopie übergeben, wenn das Dict
   weiterlebt.
2. Der Key-Match ist ein Substring-Match: ein Feld `secret_santa_name` würde
   maskiert. Bei Report-Definitionen also keine Feldnamen mit `secret`,
   `password` oder `api_key` — sonst sind unsere Logs unbrauchbar.
3. `log_action` mit `save=True` triggert Notification- und Webhook-Tasks. In
   einer Schleife über viele Objekte deshalb `save=False` +
   `LogEntry.bulk_create_and_postprocess`.
4. Ein nicht registrierter Action-Type erzeugt keinen Fehler, sondern nur eine
   leere Anzeige im Log-Viewer.

---

## 10. Plugin-URLs (Kurzbestätigung)

Verifiziert für die Aussagen aus `docs/adr/0000-setup.md`:

`pretix/multidomain/maindomain_urlconf.py:55-80`, wörtlich gekürzt:

```python
raw_plugin_patterns = []
for app in apps.get_app_configs():
    if hasattr(app, 'PretixPluginMeta'):
        if importlib.util.find_spec(app.name + '.urls'):
            urlmod = importlib.import_module(app.name + '.urls')
            single_plugin_patterns = []
            if hasattr(urlmod, 'urlpatterns'):
                single_plugin_patterns += urlmod.urlpatterns
            if hasattr(urlmod, 'event_patterns'):
                patterns = plugin_event_urls(urlmod.event_patterns, plugin=app.name)
                single_plugin_patterns.append(re_path(r'^(?P<organizer>[^/]+)/(?P<event>[^/]+)/',
                                                      include(patterns)))
            if hasattr(urlmod, 'organizer_patterns'):
                patterns = plugin_event_urls(urlmod.organizer_patterns, plugin=app.name)
                single_plugin_patterns.append(re_path(r'^(?P<organizer>[^/]+)/',
                                                      include(patterns)))
            raw_plugin_patterns.append(
                re_path(r'', include((single_plugin_patterns, app.label)))
            )

plugin_patterns = [
    re_path(r'', include((raw_plugin_patterns, 'plugins')))
]
```

Also:
- `urlpatterns` in `<plugin>/urls.py` hängen an der **URL-Wurzel**. Control-Views
  müssen das Präfix `control/event/<organizer>/<event>/` selbst schreiben.
  Vorbild `pretix/plugins/badges/urls.py:32-45`:
  ```python
  urlpatterns = [
      re_path(r'^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/badges/$',
              LayoutListView.as_view(), name='index'),
      ...
  ]
  ```
- `event_patterns` / `organizer_patterns` bekommen automatisch das
  **Presale**-Präfix (ohne `control/`).
- Namespace ist `plugins:<app.label>`, daher `reverse('plugins:badges:index', kwargs={...})`
  (`plugins/badges/signals.py:55`).

---

## 11. Test-Fixtures und Konventionen der pretix-Testsuite

**Ort:** `D:\Projekte\juki\pretix\src\tests\`

Struktur: `tests/api`, `tests/base`, `tests/control`, `tests/presale`,
`tests/plugins`, `tests/multidomain`, `tests/helpers`, `tests/e2e`,
`tests/concurrency_tests`, plus die Dummy-Plugins `tests/testdummy`,
`tests/testdummyhidden`, `tests/testdummyhybrid`, `tests/testdummyorga`,
`tests/testdummyorgarestricted`, `tests/testdummyrestricted`.

### Konfiguration

`src/setup.cfg`, Abschnitt `[tool:pytest]`:

```
DJANGO_SETTINGS_MODULE = tests.settings
addopts = -rw
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
filterwarnings =
    error
    ...
```

`filterwarnings = error` bedeutet: **jede nicht explizit ausgenommene Warnung
lässt Tests fehlschlagen.** Die Ausnahmeliste enthält u. a. den
Organizer-Signal-DeprecationWarning-Eintrag (siehe 3.5 Fallstrick 1).

`src/setup.cfg` enthält außerdem die Lint-Konfiguration, die für uns relevant
ist, wenn wir dieselben Regeln übernehmen:
`flake8: max-line-length = 160, max-complexity = 11, ignore = N802,W503,E402,C901,E722,W504,E252,N812,N806,N818,E741`;
`isort: known_first_party = pretix, multi_line_output = 5, line_length = 79, combine_as_imports = true, include_trailing_comma = true`.

### Fixture-Konventionen

Der `scopes_disabled()`-Automatismus in `tests/conftest.py:67-77` (siehe 7.5).
Weitere autouse-Fixtures: `reset_locale` (80-83, aktiviert `en`),
`set_lock_namespaces` (126-133). Optional: `fakeredis_client` (85-123),
`class_monkeypatch` (136-138).

**Typisches Fixture-Muster mit Scope** (`tests/base/test_export.py:36-58`),
wörtlich:

```python
@pytest.fixture(scope='function')
def event():
    o = Organizer.objects.create(name='Dummy', slug='dummy')
    event = Event.objects.create(
        organizer=o, name='Dummy', slug='dummy',
        date_from=datetime(2023, 1, 19, 2, 30, 0, tzinfo=timezone.utc),
        plugins='pretix.plugins.banktransfer'
    )
    o.settings.timezone = "Europe/Berlin"
    with scope(organizer=o):
        yield event


@pytest.fixture
def team(event):
    return event.organizer.teams.create(all_events=True, all_event_permissions=True)


@pytest.fixture
def user(team):
    user = User.objects.create_user('dummy@dummy.dummy', 'dummy')
    team.members.add(user)
    return user
```

**Typisches Control-Test-Muster** (`tests/control/test_export.py:17-51`),
wörtlich gekürzt:

```python
@pytest.fixture
def env():
    o = Organizer.objects.create(name="Dummy", slug="dummy")
    event = Event.objects.create(
        organizer=o, name="Dummy", slug="dummy",
        date_from=now(), plugins="pretix.plugins.banktransfer,pretix.plugins.stripe,tests.testdummy"
    )
    user = User.objects.create_user("dummy@dummy.dummy", "dummy")
    t = Team.objects.create(organizer=o, all_event_permissions=True)
    t.members.add(user)
    t.limit_events.add(event)
    Item.objects.create(event=event, name="Early-bird ticket", category=None, default_price=23,
                        admission=True, personalized=True)
    return event, user, t


@pytest.mark.django_db(transaction=True)
def test_event_export(client, env):
    client.login(email="dummy@dummy.dummy", password="dummy")
    response = client.get("/control/event/dummy/dummy/orders/export/?identifier=itemdata")
    assert b"Export format" in response.content
    response = client.post("/control/event/dummy/dummy/orders/export/do", {
        "exporter": "itemdata",
        "itemdata-_format": "default",
        "ajax": "1"
    })
    d = json.loads(response.content)
    assert d["ready"]
    assert d["success"]
```

Beobachtungen, die für unsere Tests wichtig sind:
- **Formfelder sind mit dem Exporter-Identifier geprefixt**: `"itemdata-_format"`.
- Der Export-Post geht an `/control/event/<org>/<event>/orders/export/do` mit
  `exporter=<identifier>` und `ajax=1`; die Antwort ist JSON mit
  `ready`/`success`/`redirect`.
- `@pytest.mark.django_db(transaction=True)` ist nötig, sobald Celery-Tasks
  (auch eager) mit `transaction.on_commit` beteiligt sind.
- `freeze_time` (freezegun) wird für alles Zeitabhängige benutzt
  (`tests/base/test_export.py:62`, `76`, `91`, …).
- `djmail.outbox` (`from django.core import mail as djmail`) für Mailprüfungen.
- Hilfsfunktion `from tests.base import extract_form_fields`
  (`tests/control/test_export.py:9`) — parst ein Formular aus HTML, damit man
  nur einzelne Felder überschreiben muss.
- Plugins werden pro Event über den `plugins`-String aktiviert
  (kommagetrennte App-Namen).

### Fallstricke (Tests)

1. Unsere Testsuite liegt außerhalb von `src/tests/`, damit gilt **weder**
   `tests/conftest.py` noch `src/setup.cfg` automatisch. Beides muss bewusst
   übernommen oder bewusst weggelassen werden.
2. `tests.settings` ist pretix' Test-Settings-Modul. Für unser Plugin brauchen
   wir ein eigenes, das unser Plugin in `INSTALLED_APPS` hat — das gehört zum
   Aufgabenbereich, den `bootstrap-dev`/`integrator` abdecken.
3. `filterwarnings = error` ist eine gute Idee, kostet aber Pflege (siehe
   `OrganizerPluginSignal`-Deprecation).

---

## 12. Doku vs. Code — festgestellte Abweichungen

| Thema | Doku | Code | Es gilt |
| --- | --- | --- | --- |
| `ListExporter` / `MultiSheetListExporter` | In `doc/development/api/exporter.rst` **gar nicht dokumentiert** — die Datei behandelt nur `BaseExporter` (Zeilen 58-98) und `OrganizerLevelExportMixin` (100-102) | `pretix/base/exporter.py:218-458` | Code. Dieses Dokument ist die Referenz für `ListExporter`. |
| `BaseExporter.render(output_file=...)` | Docstring in `exporter.py:174-177` beschreibt einen `output_file`-Parameter | Die Signatur `def render(self, form_data: dict)` (168) hat ihn **nicht**; nur `ListExporter.render` (328) und `MultiSheetListExporter.render` (447) haben ihn | Code. Wer `output_file` unterstützen will, muss ihn selbst in die Signatur aufnehmen. `base/management/commands/export.py:113-117` fängt den daraus resultierenden `TypeError` explizit ab. |
| `BaseExporter.__init__`-Docstring | Nennt Parameter `user`, `token`, `device` (65-70) | Signatur hat `permission_holder` (63) | Code. Der Docstring ist veraltet. |
| `event_copy_data`-Docstring | "mappings from object IDs in the original event to objects in the new event" | Stimmt, aber die Objekte in `question_map` sind **dieselben Python-Instanzen**, nachträglich mutiert (`event.py:1090-1099`) | Code — Detail siehe 3.3. |
| `logentry_display` / `logentry_object_link` | In `base/signals.py:879-893` selbst als deprecated markiert, Verweis auf docs.pretix.eu | Registry in `base/logentrytype_registry.py` | Registry benutzen. |
| `OrderPosition`-Docstring | "The default ``OrderPosition.objects`` manager only contains **fees** that are not canceled" (2503) | Es geht um Positionen, nicht Fees — Copy-Paste-Fehler aus `OrderFee` | Gemeint sind Positionen. |

---

## 13. Zusammenfassung: was das für unser Plugin heißt

Nur Ableitungen aus dem Obigen, keine Architekturentscheidung — die trifft der
`contract-architect`.

1. **Exporter:** `ListExporter` erben, `identifier`, `verbose_name`, `category`,
   `description`, ggf. `featured` als Klassenattribute; `additional_form_fields`
   (nicht `export_form_fields`) für die Report-Auswahl; `iterate_list` als
   Generator mit `yield headers` + `yield self.ProgressSetTotal(total=...)`;
   `get_filename()` überschreiben; `repeatable_read = False` erwägen, da
   Reports lange laufen können. Registrierung über `register_data_exporters`
   **und** `register_multievent_data_exporters`.
2. **Scheduled Exports:** nichts eigenes bauen. Die Report-ID landet über ein
   normales Formfeld in `export_form_data`. Weil das nicht revalidiert wird und
   die Definition zwischenzeitlich gelöscht sein kann, muss der Exporter beim
   Laden eine klare `ExportError` werfen statt `DoesNotExist`.
3. **Scopes:** Unser Exporter läuft in `scheduled_event_export`
   (`base=EventTask`) bereits im richtigen Organizer-Scope. Kein
   `scopes_disabled()` dort. In eigenen Fixtures dagegen zwingend explizit.
4. **Permissions:** `event.orders:read` ist der Gate-Key
   (`BaseExporter.get_required_event_permission()`-Default). `event.items:read`
   existiert nicht.
5. **Portabilität:** `Question.identifier` ist pro Event eindeutig und
   überlebt Event-Kopien, ist aber jederzeit vom Nutzer änderbar. "Nicht
   auflösbar" ist ein regulärer Zustand.
6. **Sicherheit der Ausgabe:** CSV-Injection und Excel-Formel-Injection sind
   durch `defusedcsv` und `SafeWorkbook` bereits abgedeckt — **solange** wir
   `ListExporter` benutzen.
7. **Query-Bau:** `positions` vs. `all_positions`, `Checkin.objects` vs.
   `Checkin.all`, `OrderPosition.event` als Property, `meta_info` als
   TextField, Default-`Meta.ordering` bei Aggregationen — das sind die vier
   Stolperstellen, die die Registry sauber kapseln muss.

---

## 14. Unklar geblieben

Ehrliche Liste dessen, was ich **nicht** eindeutig aus dem Source klären konnte.
Nichts davon ist geraten; wo ich eine Vermutung habe, ist sie als solche
markiert.

1. **Wie unser Plugin einen Report-Parameter in `export_form_data` am
   robustesten ablegt.** Ich habe verifiziert, *dass* `cleaned_data` gespeichert
   wird und Modelle zu PKs werden (`ExporterForm.clean`), und *dass* beim
   Ausführen nicht revalidiert wird. Ob ein `ModelChoiceField` (PK) oder ein
   `CharField` mit einem stabilen Slug der Report-Definition besser ist, ist
   eine Designentscheidung, keine API-Frage. Der Core benutzt durchgängig PKs
   (z. B. `checkinlists/exporters.py:322`). → Entscheidung liegt beim
   `contract-architect`.

2. **Ob `ScheduledEventExport` beim Löschen des referenzierten Objekts
   irgendwo aufgeräumt wird.** Ich habe keinen Signal-Handler oder
   Cascade-Mechanismus gefunden, der Schedules anfasst, wenn ein in
   `export_form_data` referenziertes Objekt verschwindet. Grep über
   `ScheduledEventExport`/`ScheduledOrganizerExport` findet nur die in
   Abschnitt 5 genannten Stellen. **Vermutung (ungeprüft): es gibt keinen.**
   Das Verhalten ist dann exakt das in 5.6 Fall B beschriebene.

3. **Ob es eine offizielle Konvention für Plugin-Action-Types gibt.** Ich finde
   im Core nur `pretix.*`-Präfixe und in `logentrytype_registry.py` keine
   Namensvorschrift. Die Doku unter
   `https://docs.pretix.eu/en/latest/development/implementation/logging.html`
   habe ich nicht abgerufen (kein Netzzugriff im Rahmen dieser Recherche), sie
   könnte eine Konvention nennen. → Vor der Festlegung im Contract bitte
   nachschlagen.

4. **Exakte Zeilenzahlen einzelner `Item`-Felder.** Ich habe die Feldnamen und
   den Klassenbereich (`items.py:360-1113`) verifiziert und die Feldliste per
   Offset-Grep erzeugt, aber für jedes einzelne der ~60 `Item`-Felder eine
   Zeilennummer anzugeben wäre fehleranfällig. Die Zeilennummern in Abschnitt
   6.5 sind Bereichsangaben, keine Einzelnachweise. Für `Order` und
   `OrderPosition` (die uns wirklich betreffen) ist die Liste dagegen per
   Django-Introspektion vollständig verifiziert.

5. **Verhalten von `MultiSheetListExporter` in Scheduled Exports.** Der
   `_format`-Wert `"<sheet>:default"` wird beim Speichern normal übernommen; ich
   habe aber keinen Test im Core gefunden, der einen Multi-Sheet-Exporter
   terminiert ausführt. Die UI setzt eine `multisheet_warning`, wenn mehr als
   ein Sheet existiert (`control/views/orders.py:2700`) — was diese Warnung im
   Template genau sagt, habe ich nicht nachgelesen. Vermutlich der Hinweis, dass
   CSV nur ein Sheet enthält.

6. **Ob `OrganizerLevelExportMixin` für organizer-weite Report-Vorlagen taugt.**
   Ich habe die Mechanik verstanden (Abschnitt 1.2), aber nicht durchgetestet,
   ob ein solcher Exporter überhaupt sinnvoll auf Bestelldaten zugreifen kann —
   `self.events` ist dort per Konstruktion leer
   (`services/export.py:252-253`). Meine Einschätzung: für Bestelldaten
   ungeeignet. Nicht empirisch bestätigt.

7. **`Event`-Feldliste.** Ich habe `Event` nur punktuell gelesen
   (`event.py:557-1543`, Meta bei 701-704 mit
   `ordering = ("date_from", "name", "slug")`, `objects = ScopedManager(organizer='organizer')`
   bei 699, `meta_data` bei 1365-1373). Eine vollständige Feldtabelle für
   `Event` war nicht Teil des Auftrags; falls die Registry Event-Felder anbieten
   soll, muss das nachgezogen werden.

8. **Genaues Verhalten von `filter_available` auf `ItemQuerySet`.** Die
   Implementierung liegt in der Modulfunktion `filter_available`
   (`items.py:311-343`), die ich nur überflogen habe. Für Reports über
   *verkaufte* Positionen ist sie irrelevant; für Reports über
   *verfügbare Produkte* müsste man sie genau lesen.
