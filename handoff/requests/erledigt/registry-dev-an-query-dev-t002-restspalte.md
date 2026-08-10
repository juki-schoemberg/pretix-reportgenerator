> **ARCHIVIERT — 2026-08-10, `integrator`.** Nicht an mich adressiert;
> beim Aufräumen von `handoff/requests/` gegen den Code geprüft und als
> umgesetzt befunden: `query/relations.py:188` (`money_output_field()`),
> angewandt in `aggregate_expression` (Zeile 242). T-002 steht in
> `handoff/blockers.md` als „behoben, verifiziert“.
>
> **Nicht** erledigt ist T-004 — dieselbe Fehlerklasse für
> `DataType.DECIMAL`, mit scharfem `xfail`-Reproduzierer. Der Punkt bleibt
> in `handoff/blockers.md` offen und gehört weiterhin `query-dev`; er war
> nie Teil dieser Anforderung.

# registry-dev an query-dev: T-002 Restspalte `SUM(<Geldfeld>)`

Status: offen. Blockiert nichts, aber der T-002-xfail in `tests/test_integration.py`
bleibt bis dahin rot.

## Was ich erledigt habe

Alle Geld-Annotationen in `registry/annotations.py` liefern jetzt garantiert zwei
Nachkommastellen, backend-unabhängig. Mechanismus: `annotations.MoneyField`, eine
`DecimalField`-Unterklasse mit `from_db_value`, die auf `decimal_places`
quantisiert. Sie hängt als `output_field` an jedem Geldausdruck.

Betroffen und behoben: `order.pending_sum`, `payment.sum_confirmed`,
`refund.sum_done`, `position.net_price` sowie die Aliase, die
`computed.payment_state` mit emittiert.

**Wichtig für dich:** `Cast(expr, DecimalField(max_digits=13, decimal_places=2))`
löst das Problem *nicht*. Ich habe es gemessen. `CAST(x AS decimal)` ergibt in
SQLite NUMERIC-Affinität, die die nachlaufende Null genauso wegwirft, und
Python-seitig ist ein `Cast` ein `Func` und kein `Col`, greift also weiterhin in
den Zweig von `get_decimalfield_converter`, der nicht quantisiert. Dasselbe gilt
für `Round(x, 2)`. Die Skala geht im *Converter* verloren, nicht im SQL — deshalb
muss die Korrektur am `output_field` hängen.

## Was noch offen ist, in deinem Gebiet

Der vom Nutzer gewählte Aggregat-Modus. In `query/relations.py`:

```python
AGGREGATE_FUNCTIONS = {
    ...
    Aggregate.SUM: Sum,
    Aggregate.MIN: Min,
    Aggregate.MAX: Max,
    Aggregate.AVG: Avg,
}
```

`Sum("price")` über `position.price` liefert weiterhin `Decimal("23.5")`, weil das
`output_field` aus dem Modellfeld abgeleitet wird — ein gewöhnliches
`DecimalField`, typrichtig, skalenverlustig. Nachweisbar am T-002-Reproduzierer:

```
AssertionError: ['23.50', '20.50', '23.5'] == ['23.50', '20.50', '23.50']
                                    ^^^^ ("position.price", "sum")
```

Spalten eins und zwei stimmen jetzt.

## Vorschlag

Für Felder mit `datatype is DataType.MONEY` und einem Aggregat aus
`{SUM, MIN, MAX, AVG}` das `output_field` explizit setzen:

```python
from pretix_custom_reports.registry.annotations import MoneyField
...
Sum(expr, output_field=MoneyField(max_digits=13, decimal_places=2))
```

`MoneyField`, `MONEY_MAX_DIGITS` und `MONEY_DECIMAL_PLACES` sind in
`registry/annotations.__all__` exportiert, genau dafür. Die Konstanten sind gegen
den installierten pretix-Source geprüft (`Order.total`, `OrderPayment.amount`,
`OrderRefund.amount`, `AbstractPosition.price`, `OrderPosition.tax_value` — alle
`max_digits=13, decimal_places=2`); `tests/test_registry_money.py::
test_the_money_scale_matches_the_pretix_model_fields` hält das nach.

`COUNT`/`COUNT_DISTINCT` brauchen nichts, die haben kein Skalenproblem.

`AVG` ist gesondert zu bedenken: ein Durchschnitt von Geld ist fachlich kein
Geldbetrag mit zwei Stellen. Wenn du dort auf zwei Stellen quantisierst, ist das
eine Rundungsentscheidung und keine Formatkorrektur mehr — deine Wahl, aber sie
gehört dokumentiert.
