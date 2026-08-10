> **ARCHIVIERT — 2026-08-10, `integrator`.** Nicht an mich adressiert;
> beim Aufräumen von `handoff/requests/` gegen den Code geprüft und als
> umgesetzt befunden: `views/api.py:182` importiert `format_cell_value` aus
> `exporters.py` statt es zu duplizieren, der Docstring ab Zeile 159 nennt
> es ausdrücklich die *eine* Implementierung. T-001 steht in
> `handoff/blockers.md` als „behoben, verifiziert“. Falls `frontend-dev`
> anderer Meinung ist: Datei zurückschieben.

# exporter-dev → frontend-dev: der geteilte Zellen-Renderer (T-001)

Betrifft `views/api.py` (dein Gebiet), Vorlage steht in `exporters.py` (meins).
Kein Contract-Änderungswunsch, kein Blocker. Zum Abarbeiten in deiner
Folge-Runde.

## Worum es geht

T-001 aus `handoff/blockers.md`: `ColumnFormat.date_style`, `number_style` und
`boolean_style` wirkten in deiner Vorschau und nirgends sonst. Der Export wendet
sie jetzt an — und zwar mit **deiner** Implementierung, wörtlich übernommen, in
`exporters.py`. Damit gibt es die Logik zweimal. Der zweite Schritt gehört dir:
importieren statt duplizieren.

## Import und Signatur

```python
from pretix_custom_reports.exporters import format_cell_value
```

```python
def format_cell_value(
    value: Any,
    fmt: Optional[ColumnFormat],
    datatype: Optional[DataType] = None,
    event: Optional[Any] = None,
) -> str:
```

Argumentreihenfolge, Typen und Rückgabewert sind identisch zu deinem heutigen
`views/api.py::format_cell`. `datatype` und `event` haben zusätzlich
Vorgabewerte, damit ein Aufrufer ohne Event-Kontext die Funktion benutzen kann;
positionell ändert sich nichts.

## Beispielaufruf

Dein `PreviewView._rows` bleibt Zeichen für Zeichen stehen, nur der Name der
Funktion kommt aus einem anderen Modul:

```python
from pretix_custom_reports.exporters import format_cell_value

out.append(
    [
        format_cell_value(
            value,
            formats_by_index[index][0],
            formats_by_index[index][1],
            event,
        )
        for index, value in enumerate(row)
    ]
)
```

Ersatzlos entfallen können danach `format_cell`, `_format_temporal` und
`_format_number` in `views/api.py`. `_formats_by_index` bleibt deins — der
Exporter hat eine eigene Paarung (`CustomReportExporter._cell_formats`), weil er
zusätzlich entscheiden muss, ob überhaupt formatiert wird.

## Verhalten und Randfälle

| Eingabe | Ergebnis |
| --- | --- |
| `value is None` | `""` |
| `value` ist schon `str` | unverändert zurück |
| `fmt is None` oder `ColumnFormat()` | Standard je Datentyp, wie bisher |
| `fmt` ist irgendein Objekt ohne die Stil-Attribute | wie `None`; gelesen wird mit `getattr(fmt, "...", None)` |
| `event is None` | Zeitzone und Währung fallen auf eine schlichte Darstellung zurück, keine Exception |
| Stil passt nicht zum Werttyp (`date_only` auf `datetime.time`) | **wirft**, siehe unten |

Der letzte Fall ist der einzige, in dem du etwas entscheiden musst.
`format_cell_value` ist absichtlich streng und fängt nichts ab — sie ist der
Renderer, nicht die Fehlerbehandlung. Auf dem Exportweg liegt der `try/except`
eine Ebene höher (`format_export_cell`), weil dort eine Exception fünf
Celery-Retries und das Wort „Internal Error" bedeuten würde. In der Vorschau
wäre es eine 500 auf `api.preview`; heute ist das genauso, dein `format_cell`
wirft an derselben Stelle. Wenn du das ändern willst, dann bitte in `views/api.py`
und nicht in `format_cell_value`, sonst weicht die Vorschau wieder vom Export ab.

Erreichbar ist der Fall über eine importierte oder von Hand editierte Definition:
`validate_definition` prüft die Stile strukturell, aber nicht gegen den
Datentyp der Spalte.

## Was du **nicht** aufrufen solltest

`format_export_cell(value, fmt, datatype=None, event=None) -> Any` im selben
Modul gibt Rohwerte zurück, wo die Definition keinen Stil setzt (und bei
`NumberStyle.RAW`), damit XLSX echte Zahlen behält und Reports von vor T-001
byte-gleiche Dateien liefern. Für die Vorschau ist das falsch: du brauchst immer
einen String.

## Abnahme

`tests/test_exporters.py::test_the_preview_and_the_export_share_one_renderer`
vergleicht beide Implementierungen über 800 Kombinationen (zehn Werttypen, 14
Formate, vier Datentypen), Ergebnis und Exceptiontyp. Der Test erkennt beide
Endzustände: solange `views.api.format_cell` existiert und eine eigene Funktion
ist, wird verglichen; ist sie weg oder identisch mit `format_cell_value`, wird
er zum No-Op. Du musst ihn also nicht anfassen — er hört von selbst auf, etwas
zu behaupten, sobald es nichts mehr zu vergleichen gibt.

Dein `tests/test_editor_api.py::test_preview_applies_the_column_format` muss
nach dem Umbau unverändert grün bleiben; das ist die eigentliche Abnahme.
