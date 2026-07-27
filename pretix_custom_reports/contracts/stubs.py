"""Working stand-ins for the registry and the query compiler.

Owner: contract-architect (wave 0c). Frozen -- see ``contracts/__init__.py``.

Why this exists
---------------

``query-dev`` needs a registry, ``frontend-dev`` needs preview rows and
``persistence-dev`` needs something to validate against -- and in wave 1 none
of the real implementations exist yet. These stubs satisfy
:class:`~.protocols.FieldRegistry` and :class:`~.protocols.QueryCompiler`
without Django, without a database and without an event.

What they do and do not do
--------------------------

They *do*: expose a realistic field set for both bases, resolve keys, enforce
the checks that need a registry (base support, mandatory/allowed aggregates,
allowed operators, sortability), and produce deterministic fake rows.

They *do not*: filter, sort or aggregate anything. Row values depend only on
the column and the row index. A test that asserts filtering works against
these stubs is testing nothing.

The field set is the same one the golden fixtures in
``tests/fixtures/definitions/`` use, so any fixture compiles against the stub
compiler. ``tests/fixtures/definitions/_index.json`` lists which of those keys
the real registry must provide.
"""

from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import datetime
from dataclasses import dataclass
from decimal import Decimal

from pretix_custom_reports.contracts.definition import (
    PREVIEW_ROW_LIMIT,
    Column,
    FieldUsage,
    ReportDefinition,
)
from pretix_custom_reports.contracts.errors import (
    CompilationError,
    FieldResolutionError,
)
from pretix_custom_reports.contracts.fields import (
    AGGREGATES_FOR_DATATYPE,
    DEFAULT_OPERATORS,
    Aggregate,
    Base,
    DataType,
    FieldContext,
    ReportField,
    ValueScope,
    meta_field_key,
    plugin_field_key,
    question_field_key,
)
from pretix_custom_reports.contracts.protocols import (
    DEFAULT_CHUNK_SIZE,
    CompiledColumn,
)

__all__ = [
    "STUB_META_PROPERTIES",
    "STUB_PLUGIN_APP_LABEL",
    "STUB_QUESTIONS",
    "StubCompiledReport",
    "StubFieldRegistry",
    "StubQuerySet",
    "StubQueryCompiler",
    "StubRow",
    "stub_compiler",
    "stub_registry",
]


# ---------------------------------------------------------------------------
# The field table
# ---------------------------------------------------------------------------

_ORDER_STATUS_CHOICES: Tuple[Tuple[str, str], ...] = (
    ("n", "pending"),
    ("p", "paid"),
    ("e", "expired"),
    ("c", "canceled"),
)


@dataclass(frozen=True)
class _Spec:
    """Compact description of one stub field, expanded per base below."""

    key: str
    label: str
    group: str
    datatype: DataType
    order_path: Optional[str]
    """ORM path when the report base is ``order`` (``None`` = not available)."""

    position_path: Optional[str]
    """ORM path when the report base is ``orderposition``."""

    sortable: bool = True
    aggregate_on_order: bool = False
    """True for one-to-many data that needs an aggregate on base ``order``."""

    choices: Optional[Tuple[Tuple[Any, Any], ...]] = None
    value_scope: ValueScope = ValueScope.GLOBAL
    aggregates: Optional[Tuple[Aggregate, ...]] = None


def _s(*args: Any, **kwargs: Any) -> _Spec:
    return _Spec(*args, **kwargs)


# Order-level data. Same value for every position of an order, so it is
# directly usable on both bases.
_ORDER_SPECS: Tuple[_Spec, ...] = (
    _s("order.code", "Order code", "order", DataType.STRING, "code", "order__code"),
    _s(
        "order.status",
        "Order status",
        "order",
        DataType.CHOICE,
        "status",
        "order__status",
        choices=_ORDER_STATUS_CHOICES,
    ),
    _s(
        "order.datetime",
        "Order date",
        "order",
        DataType.DATETIME,
        "datetime",
        "order__datetime",
    ),
    _s(
        "order.expires",
        "Expiry date",
        "order",
        DataType.DATETIME,
        "expires",
        "order__expires",
    ),
    _s(
        "order.cancellation_date",
        "Cancellation date",
        "order",
        DataType.DATETIME,
        "cancellation_date",
        "order__cancellation_date",
    ),
    _s("order.email", "E-mail", "order", DataType.EMAIL, "email", "order__email"),
    _s("order.phone", "Phone", "order", DataType.PHONE, "phone", "order__phone"),
    _s("order.total", "Order total", "order", DataType.MONEY, "total", "order__total"),
    _s("order.locale", "Language", "order", DataType.CHOICE, "locale", "order__locale"),
    _s(
        "order.testmode",
        "Test mode",
        "order",
        DataType.BOOLEAN,
        "testmode",
        "order__testmode",
    ),
    _s(
        "order.comment",
        "Internal comment",
        "order",
        DataType.TEXT,
        "comment",
        "order__comment",
    ),
    _s(
        "order.sales_channel",
        "Sales channel",
        "order",
        DataType.CHOICE,
        "sales_channel__identifier",
        "order__sales_channel__identifier",
    ),
    _s(
        "order.require_approval",
        "Approval required",
        "order",
        DataType.BOOLEAN,
        "require_approval",
        "order__require_approval",
    ),
    _s(
        "order.checkin_attention",
        "Check-in attention",
        "order",
        DataType.BOOLEAN,
        "checkin_attention",
        "order__checkin_attention",
    ),
    _s(
        "order.position_count",
        "Number of positions",
        "order",
        DataType.INTEGER,
        "pcnt",
        "order__pcnt",
    ),
    _s(
        "order.pending_sum",
        "Outstanding amount",
        "order",
        DataType.MONEY,
        "pending_sum_t",
        "order__pending_sum_t",
    ),
)

_INVOICE_ADDRESS_SPECS: Tuple[_Spec, ...] = tuple(
    _s(
        f"invoice_address.{name}",
        label,
        "invoice_address",
        datatype,
        f"invoice_address__{path}",
        f"order__invoice_address__{path}",
    )
    for name, label, datatype, path in (
        ("name", "Invoice name", DataType.STRING, "name_cached"),
        ("company", "Company", DataType.STRING, "company"),
        ("street", "Street", DataType.TEXT, "street"),
        ("zipcode", "ZIP code", DataType.STRING, "zipcode"),
        ("city", "City", DataType.STRING, "city"),
        ("country", "Country", DataType.COUNTRY, "country"),
        ("state", "State", DataType.STRING, "state"),
        ("vat_id", "VAT ID", DataType.STRING, "vat_id"),
        ("is_business", "Business customer", DataType.BOOLEAN, "is_business"),
        (
            "internal_reference",
            "Internal reference",
            DataType.STRING,
            "internal_reference",
        ),
    )
)

# Position-level data: one row per position on base ``orderposition``, many
# per order on base ``order`` -- hence aggregate_on_order.
_POSITION_SPECS: Tuple[_Spec, ...] = (
    _s(
        "position.positionid",
        "Position number",
        "position",
        DataType.INTEGER,
        "all_positions__positionid",
        "positionid",
        aggregate_on_order=True,
    ),
    _s(
        "position.price",
        "Position price",
        "position",
        DataType.MONEY,
        "all_positions__price",
        "price",
        aggregate_on_order=True,
    ),
    _s(
        "position.tax_rate",
        "Tax rate",
        "position",
        DataType.DECIMAL,
        "all_positions__tax_rate",
        "tax_rate",
        aggregate_on_order=True,
    ),
    _s(
        "position.tax_value",
        "Tax amount",
        "position",
        DataType.MONEY,
        "all_positions__tax_value",
        "tax_value",
        aggregate_on_order=True,
    ),
    _s(
        "position.attendee_name",
        "Attendee name",
        "position",
        DataType.STRING,
        "all_positions__attendee_name_cached",
        "attendee_name_cached",
        aggregate_on_order=True,
    ),
    _s(
        "position.attendee_email",
        "Attendee e-mail",
        "position",
        DataType.EMAIL,
        "all_positions__attendee_email",
        "attendee_email",
        aggregate_on_order=True,
    ),
    _s(
        "position.company",
        "Attendee company",
        "position",
        DataType.STRING,
        "all_positions__company",
        "company",
        aggregate_on_order=True,
    ),
    _s(
        "position.city",
        "Attendee city",
        "position",
        DataType.STRING,
        "all_positions__city",
        "city",
        aggregate_on_order=True,
    ),
    _s(
        "position.country",
        "Attendee country",
        "position",
        DataType.COUNTRY,
        "all_positions__country",
        "country",
        aggregate_on_order=True,
    ),
    _s(
        "position.canceled",
        "Position canceled",
        "position",
        DataType.BOOLEAN,
        "all_positions__canceled",
        "canceled",
        aggregate_on_order=True,
    ),
    _s(
        "position.pseudonymization_id",
        "Attendee ID",
        "position",
        DataType.STRING,
        "all_positions__pseudonymization_id",
        "pseudonymization_id",
        aggregate_on_order=True,
    ),
)

_PRODUCT_SPECS: Tuple[_Spec, ...] = (
    _s(
        "item.name",
        "Product",
        "item",
        DataType.I18N,
        "all_positions__item__name",
        "item__name",
        aggregate_on_order=True,
    ),
    _s(
        "item.internal_name",
        "Product (internal name)",
        "item",
        DataType.STRING,
        "all_positions__item__internal_name",
        "item__internal_name",
        aggregate_on_order=True,
    ),
    _s(
        "item.category",
        "Product category",
        "item",
        DataType.I18N,
        "all_positions__item__category__name",
        "item__category__name",
        aggregate_on_order=True,
    ),
    _s(
        "item.admission",
        "Admission product",
        "item",
        DataType.BOOLEAN,
        "all_positions__item__admission",
        "item__admission",
        aggregate_on_order=True,
    ),
    _s(
        "variation.value",
        "Product variation",
        "item",
        DataType.I18N,
        "all_positions__variation__value",
        "variation__value",
        aggregate_on_order=True,
    ),
    _s(
        "subevent.name",
        "Date",
        "subevent",
        DataType.I18N,
        "all_positions__subevent__name",
        "subevent__name",
        aggregate_on_order=True,
    ),
    _s(
        "subevent.date_from",
        "Date start",
        "subevent",
        DataType.DATETIME,
        "all_positions__subevent__date_from",
        "subevent__date_from",
        aggregate_on_order=True,
    ),
    _s(
        "seat.zone_name",
        "Seat zone",
        "seat",
        DataType.STRING,
        "all_positions__seat__zone_name",
        "seat__zone_name",
        aggregate_on_order=True,
    ),
    _s(
        "seat.row_name",
        "Seat row",
        "seat",
        DataType.STRING,
        "all_positions__seat__row_name",
        "seat__row_name",
        aggregate_on_order=True,
    ),
    _s(
        "seat.seat_number",
        "Seat number",
        "seat",
        DataType.STRING,
        "all_positions__seat__seat_number",
        "seat__seat_number",
        aggregate_on_order=True,
    ),
    _s(
        "voucher.code",
        "Voucher code",
        "voucher",
        DataType.STRING,
        "all_positions__voucher__code",
        "voucher__code",
        aggregate_on_order=True,
    ),
    _s(
        "voucher.tag",
        "Voucher tag",
        "voucher",
        DataType.STRING,
        "all_positions__voucher__tag",
        "voucher__tag",
        aggregate_on_order=True,
    ),
)

# Pre-aggregated annotations. Available on both bases as a single value.
_AGGREGATE_SPECS: Tuple[_Spec, ...] = (
    _s(
        "payment.sum_confirmed",
        "Amount paid",
        "payment",
        DataType.MONEY,
        "payment_sum",
        "order__payment_sum",
    ),
    _s(
        "refund.sum_done",
        "Amount refunded",
        "payment",
        DataType.MONEY,
        "refund_sum",
        "order__refund_sum",
    ),
    _s(
        "payment.providers",
        "Payment providers",
        "payment",
        DataType.LIST,
        "payment_providers",
        "order__payment_providers",
        sortable=False,
    ),
    _s(
        "checkin.count",
        "Check-ins",
        "checkin",
        DataType.INTEGER,
        "checkin_count",
        "checkin_count",
    ),
    _s(
        "checkin.first_datetime",
        "First check-in",
        "checkin",
        DataType.DATETIME,
        "checkin_first",
        "checkin_first",
    ),
    _s(
        "checkin.last_datetime",
        "Last check-in",
        "checkin",
        DataType.DATETIME,
        "checkin_last",
        "checkin_last",
    ),
)

_ALL_SPECS: Tuple[_Spec, ...] = (
    _ORDER_SPECS
    + _INVOICE_ADDRESS_SPECS
    + _POSITION_SPECS
    + _PRODUCT_SPECS
    + _AGGREGATE_SPECS
)

#: Question identifiers the stub pretends the event has. Mirrors the question
#: keys used by the golden fixtures.
STUB_QUESTIONS: Tuple[Tuple[str, str, DataType], ...] = (
    ("tshirt-size", "T-shirt size", DataType.CHOICE),
    ("arrival-date", "Day of arrival", DataType.DATE),
    ("newsletter", "Newsletter opt-in", DataType.BOOLEAN),
)

#: Event meta properties the stub pretends the organizer has.
STUB_META_PROPERTIES: Tuple[Tuple[str, str], ...] = (("campaign", "Campaign"),)

#: App label the stub uses for the "field from another plugin" example.
STUB_PLUGIN_APP_LABEL = "pretix_demo"


def _build_field(spec: _Spec, base: Base) -> Optional[ReportField]:
    path = spec.order_path if base is Base.ORDER else spec.position_path
    if path is None:
        return None

    needs_aggregate = base is Base.ORDER and spec.aggregate_on_order
    aggregates: Tuple[Aggregate, ...] = ()
    if spec.aggregates is not None:
        aggregates = spec.aggregates
    elif spec.aggregate_on_order:
        aggregates = AGGREGATES_FOR_DATATYPE.get(spec.datatype, (Aggregate.COUNT,))

    return ReportField(
        key=spec.key,
        label=spec.label,
        group=spec.group,
        datatype=spec.datatype,
        bases=(base,),
        orm_path=path,
        filter_operators=DEFAULT_OPERATORS.get(spec.datatype, ()),
        # Sorting by an aggregated value is out of scope for v1
        # (docs/adr/0001-contracts.md section 7).
        sortable=spec.sortable and not needs_aggregate,
        choices=(lambda ctx, ch=spec.choices: list(ch)) if spec.choices else None,
        aggregates=aggregates,
        requires_aggregate_on=(Base.ORDER,) if needs_aggregate else (),
        value_scope=spec.value_scope,
    )


def _build_question_field(
    identifier: str, label: str, datatype: DataType, base: Base
) -> ReportField:
    path = "answer_" + identifier.replace("-", "_").replace(".", "_")
    if base is Base.ORDER:
        path = "order_" + path
    return ReportField(
        key=question_field_key(identifier),
        label=label,
        group="answers",
        datatype=datatype,
        bases=(base,),
        orm_path=path,
        annotation=lambda ctx, alias=path: {alias: None},
        filter_operators=DEFAULT_OPERATORS.get(datatype, ()),
        sortable=base is Base.ORDERPOSITION,
        aggregates=(Aggregate.JOIN, Aggregate.COUNT) if base is Base.ORDER else (),
        requires_aggregate_on=(Base.ORDER,) if base is Base.ORDER else (),
    )


def _build_meta_field(name: str, label: str, base: Base) -> ReportField:
    alias = f"meta_event_{name}"
    return ReportField(
        key=meta_field_key("event", name),
        label=label,
        group="meta",
        datatype=DataType.STRING,
        bases=(base,),
        orm_path=alias,
        annotation=lambda ctx, a=alias: {a: None},
        filter_operators=DEFAULT_OPERATORS[DataType.STRING],
        sortable=True,
    )


def _build_plugin_field(base: Base) -> ReportField:
    return ReportField(
        key=plugin_field_key(STUB_PLUGIN_APP_LABEL, "demo_value"),
        label="Demo value (other plugin)",
        group="Demo plugin",
        datatype=DataType.STRING,
        bases=(base,),
        orm_path="demo_value",
        annotation=lambda ctx: {"demo_value": None},
        filter_operators=DEFAULT_OPERATORS[DataType.STRING],
        sortable=True,
        provider=STUB_PLUGIN_APP_LABEL,
    )


# ---------------------------------------------------------------------------
# Registry stub
# ---------------------------------------------------------------------------


class StubFieldRegistry:
    """In-memory :class:`~.protocols.FieldRegistry`. No database, no event.

    ``event`` is accepted and ignored; pass ``None`` in tests that have none.
    """

    def __init__(
        self,
        questions: Sequence[Tuple[str, str, DataType]] = STUB_QUESTIONS,
        meta_properties: Sequence[Tuple[str, str]] = STUB_META_PROPERTIES,
        include_plugin_field: bool = True,
    ) -> None:
        self._cache: Dict[Base, Dict[str, ReportField]] = {}
        self._questions = tuple(questions)
        self._meta_properties = tuple(meta_properties)
        self._include_plugin_field = include_plugin_field

    def _build(self, base: Base) -> Dict[str, ReportField]:
        fields: Dict[str, ReportField] = {}
        for spec in _ALL_SPECS:
            built = _build_field(spec, base)
            if built is not None:
                fields[built.key] = built
        for identifier, label, datatype in self._questions:
            built = _build_question_field(identifier, label, datatype, base)
            fields[built.key] = built
        for name, label in self._meta_properties:
            built = _build_meta_field(name, label, base)
            fields[built.key] = built
        if self._include_plugin_field:
            built = _build_plugin_field(base)
            fields[built.key] = built
        return fields

    # -- FieldRegistry protocol -------------------------------------------

    def get_fields(
        self, event: Any, base: Union[Base, str]
    ) -> Mapping[str, ReportField]:
        """All stub fields for *base*. Insertion order is stable."""
        coerced = Base.coerce(base)
        if coerced not in self._cache:
            self._cache[coerced] = self._build(coerced)
        return dict(self._cache[coerced])

    def resolve(
        self, key: str, event: Any, base: Union[Base, str]
    ) -> Optional[ReportField]:
        """Single lookup, ``None`` if the key is unknown for this base."""
        return self.get_fields(event, base).get(key)

    # -- convenience -------------------------------------------------------

    def context(self, event: Any, base: Union[Base, str]) -> FieldContext:
        """Build a :class:`~.fields.FieldContext` for callables."""
        return FieldContext(event=event, base=Base.coerce(base))


_DEFAULT_REGISTRY: Optional[StubFieldRegistry] = None


def stub_registry() -> StubFieldRegistry:
    """Process-wide default :class:`StubFieldRegistry`."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = StubFieldRegistry()
    return _DEFAULT_REGISTRY


# ---------------------------------------------------------------------------
# Fake rows
# ---------------------------------------------------------------------------

_EPOCH = datetime.datetime(2026, 3, 1, 9, 0, 0)
_COUNTRIES = ("DE", "AT", "CH", "NL")
_CHANNELS = ("web", "resellers", "api")


class StubRow(Dict[str, Any]):
    """A fake result row. ``dict`` with attribute access for convenience."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as e:  # pragma: no cover - defensive
            raise AttributeError(item) from e


def _fake_value(field: ReportField, index: int, aggregate: Optional[Aggregate]) -> Any:
    """Deterministic placeholder value for a column in row *index*."""
    if aggregate in (Aggregate.COUNT, Aggregate.COUNT_DISTINCT):
        return index % 4 + 1
    if aggregate is Aggregate.JOIN:
        return ", ".join(f"{field.key.split('.')[-1]} {index}-{n}" for n in (1, 2))

    datatype = field.datatype
    if datatype is DataType.INTEGER:
        return index + 1
    if datatype in (DataType.DECIMAL, DataType.MONEY):
        return Decimal("19.00") + Decimal(index)
    if datatype is DataType.BOOLEAN:
        return index % 2 == 0
    if datatype is DataType.DATETIME:
        return _EPOCH + datetime.timedelta(hours=index)
    if datatype is DataType.DATE:
        return (_EPOCH + datetime.timedelta(days=index)).date()
    if datatype is DataType.TIME:
        return (_EPOCH + datetime.timedelta(minutes=index)).time()
    if datatype is DataType.EMAIL:
        return f"attendee{index}@example.org"
    if datatype is DataType.PHONE:
        return f"+4970000000{index % 10}"
    if datatype is DataType.COUNTRY:
        return _COUNTRIES[index % len(_COUNTRIES)]
    if datatype is DataType.LIST:
        return ", ".join(_CHANNELS[: index % len(_CHANNELS) + 1])
    if datatype is DataType.CHOICE:
        if field.choices is not None:
            options = list(field.choices(FieldContext(event=None, base=field.bases[0])))
            if options:
                return options[index % len(options)][0]
        return f"choice-{index % 3}"
    if datatype is DataType.I18N:
        return f"{field.label} {index % 3 + 1}"
    return f"{field.key}-{index}"


class StubQuerySet:
    """Minimal queryset-ish object: iterable, sliceable, countable.

    Enough for a preview and for ``len()``-style assertions; deliberately not
    enough to be mistaken for a real ``QuerySet``.
    """

    def __init__(self, rows: Sequence[StubRow]) -> None:
        self._rows = list(rows)

    def __iter__(self) -> Iterator[StubRow]:
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, item: Any) -> Any:
        return self._rows[item]

    def count(self) -> int:
        """Number of rows."""
        return len(self._rows)

    def iterator(self, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[StubRow]:
        """Mirrors ``QuerySet.iterator``; *chunk_size* is ignored."""
        return iter(self._rows)


# ---------------------------------------------------------------------------
# Compiler stub
# ---------------------------------------------------------------------------


@dataclass
class StubCompiledReport:
    """A :class:`~.protocols.CompiledReport` over generated rows."""

    definition: ReportDefinition
    base: Base
    event: Any
    columns: Tuple[CompiledColumn, ...]
    queryset: StubQuerySet

    def headers(self) -> List[str]:
        """Header row."""
        return [column.label for column in self.columns]

    def iter_rows(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        limit: Optional[int] = None,
    ) -> Iterator[List[Any]]:
        """Yield rendered rows. Filtering and sorting are *not* applied."""
        for index, row in enumerate(self.queryset.iterator(chunk_size=chunk_size)):
            if limit is not None and index >= limit:
                return
            yield [column.render(row) for column in self.columns]

    def count(self) -> int:
        """Number of rows this stub produces."""
        return self.queryset.count()


class StubQueryCompiler:
    """In-memory :class:`~.protocols.QueryCompiler`.

    Performs the registry-stage checks a real compiler must perform -- so error
    handling can be built and tested against it -- and then fabricates rows.
    """

    def __init__(
        self,
        registry: Optional[StubFieldRegistry] = None,
        rows: int = PREVIEW_ROW_LIMIT,
    ) -> None:
        self.registry = registry or stub_registry()
        self.rows = rows

    def compile(
        self, definition: ReportDefinition, event: Any = None
    ) -> StubCompiledReport:
        """Resolve, check and build a :class:`StubCompiledReport`.

        :raises FieldResolutionError: unknown key in columns, filters or sorting.
        :raises CompilationError: known key used in a way the field forbids.
        """
        base = definition.base
        fields = self.registry.get_fields(event, base)

        missing = [
            ref.key
            for ref in definition.iter_field_references()
            if ref.key not in fields
        ]
        if missing:
            raise FieldResolutionError(sorted(set(missing)), base=base)

        problems: List[str] = []
        for ref in definition.iter_field_references():
            field = fields[ref.key]
            if not field.supports_base(base):
                problems.append(
                    f"{ref.path}: {ref.key} is not available on base {base}."
                )
            if ref.usage is FieldUsage.COLUMN:
                if field.needs_aggregate_on(base) and ref.aggregate is None:
                    problems.append(
                        f"{ref.path}: {ref.key} needs an aggregate on base {base}."
                    )
                if ref.aggregate is not None and not field.allows_aggregate(
                    ref.aggregate
                ):
                    problems.append(
                        f"{ref.path}: {ref.key} does not support aggregate "
                        f"{ref.aggregate}."
                    )
            elif ref.usage is FieldUsage.FILTER:
                if ref.operator is not None and not field.allows_operator(ref.operator):
                    problems.append(
                        f"{ref.path}: operator {ref.operator} is not allowed for "
                        f"{ref.key}."
                    )
            elif ref.usage is FieldUsage.SORT and not field.sortable:
                problems.append(f"{ref.path}: {ref.key} is not sortable.")
        if problems:
            raise CompilationError(" ".join(problems))

        columns = tuple(
            self._compile_column(column, fields[column.field])
            for column in definition.columns
            if not column.hidden
        )

        count = self.rows
        if definition.options.row_limit is not None:
            count = min(count, definition.options.row_limit)
        rows = [
            StubRow(
                {
                    column.key: _fake_value(fields[column.key], index, column.aggregate)
                    for column in columns
                }
            )
            for index in range(count)
        ]
        return StubCompiledReport(
            definition=definition,
            base=base,
            event=event,
            columns=columns,
            queryset=StubQuerySet(rows),
        )

    @staticmethod
    def _compile_column(column: Column, field: ReportField) -> CompiledColumn:
        key = column.field
        return CompiledColumn(
            key=key,
            label=column.label or str(field.label),
            datatype=field.datatype,
            render=lambda row, k=key: row.get(k),
            aggregate=column.aggregate,
            field=field,
        )


def stub_compiler() -> StubQueryCompiler:
    """A :class:`StubQueryCompiler` bound to :func:`stub_registry`."""
    return StubQueryCompiler(stub_registry())
