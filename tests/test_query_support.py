"""Shared helpers for the ``tests/test_query_*.py`` modules. Not a test module.

Owner: query-dev (ORCHESTRIERUNG.md section 5, ``tests/test_query*.py``).

It lives under this name because that is the file pattern query-dev owns; pytest
collects it, finds no tests, and moves on.

What is in here and why
-----------------------

Two registries, for two different jobs.

``StubFieldRegistry`` from ``contracts/stubs.py`` is the contractual stand-in and
is used wherever a test only needs *a* registry: resolution, validation, plan
shape. Its ORM paths are deliberately fictional in places (``pcnt``,
``order_answer_tshirt_size``) and its annotations return ``{alias: None}``, which
is fine -- its own docstring says it does not filter, sort or aggregate anything.

:class:`ReferenceRegistry` is the second registry: the same keys as the stub, the
same base rules, but with **real** ORM paths and real annotations, so a definition
can be compiled all the way down to SQL and executed. It covers exactly
``required_field_keys`` from ``tests/fixtures/definitions/_index.json``, which is
the contract's own list of what the real registry has to provide (ADR 0001
section 10).

It is *not* a second implementation competing with ``registry/``. It is the
fixture that lets the compiler be tested against a database while ``registry-dev``
builds the real thing in parallel, and it doubles as a worked example of what the
compiler expects from a registry.

Aggregate hints
---------------

Fields that are one-to-many on base ``order`` declare the same ``extra`` keys as
the real registry (:mod:`pretix_custom_reports.registry.hints`): the relation
they multiply over and the path of the canceled flag inside it. That is not
decoration -- a double that agreed with the compiler's *own* conventions instead
of the registry's is how the missing question filter of
``hints.aggregate_filter`` stayed invisible through a whole test suite.

The one thing this double cannot mirror is
``hints.EXTRA_AGGREGATE_QUESTION_PK``: it is built without a database and has no
question primary keys, so its answer fields narrow themselves by
``question__identifier`` through
:data:`~pretix_custom_reports.query.columns.EXTRA_RELATION_FILTER`, the escape
hatch a third-party registry would use. The primary-key path of the real
registry is covered by ``tests/test_query_registry.py``, which compiles against
``registry.library.field_registry`` itself.
"""

from typing import Any, Dict, Mapping, Optional, Tuple, Union

import datetime
import json
import pathlib
from django.db.models import (
    Count,
    DateTimeField,
    DecimalField,
    F,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from pretix.base.models import (
    Checkin,
    Order,
    OrderPayment,
    OrderPosition,
    OrderRefund,
    Question,
    QuestionAnswer,
)

from pretix_custom_reports.contracts.definition import (
    ReportDefinition,
    validate_definition,
)
from pretix_custom_reports.contracts.fields import (
    AGGREGATES_FOR_DATATYPE,
    DEFAULT_OPERATORS,
    Aggregate,
    Base,
    DataType,
    FieldContext,
    Operator,
    ReportField,
    ValueScope,
    meta_field_key,
    plugin_field_key,
    question_field_key,
)
from pretix_custom_reports.query.columns import EXTRA_RELATION_FILTER
from pretix_custom_reports.registry import hints

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "definitions"

#: Golden fixtures that must compile. Order fixed so failures are reproducible.
VALID_FIXTURES: Tuple[str, ...] = (
    "minimal_order.json",
    "wide_order.json",
    "order_with_aggregates.json",
    "orderposition_basic.json",
    "orderposition_questions.json",
    "relative_date_filters.json",
    "filters_and_or.json",
    "multi_level_sorting.json",
    "plugin_and_meta_fields.json",
    "options_full.json",
)

#: The 31-column workhorse used for the query-count test.
WIDE_FIXTURE = "wide_order.json"


def load_fixture(name: str) -> ReportDefinition:
    """Load and structurally validate a golden fixture."""
    path = FIXTURE_DIR / name
    return validate_definition(json.loads(path.read_text(encoding="utf-8")))


def load_raw(name: str) -> Any:
    """Load a fixture without validating it (for the ``invalid/`` cases)."""
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def expectations() -> Dict[str, Dict[str, Any]]:
    """``invalid/_expectations.json``, fixtures section only."""
    raw = load_raw("invalid/_expectations.json")
    return raw["fixtures"]


def required_field_keys() -> Dict[str, Any]:
    """``required_field_keys`` from ``_index.json``."""
    return load_raw("_index.json")["required_field_keys"]


# ---------------------------------------------------------------------------
# A fake event, for tests that need a timezone but no database
# ---------------------------------------------------------------------------


class FakeEvent:
    """Just enough of an ``Event`` for date resolution and plan building.

    The compiler only ever asks an event for ``timezone`` and ``date_from``; a
    real ``Event`` needs a database and an organizer, and pass one needs neither.
    """

    def __init__(
        self,
        timezone: Union[str, datetime.tzinfo] = "Europe/Berlin",
        date_from: Optional[datetime.datetime] = None,
    ) -> None:
        if isinstance(timezone, str):
            from zoneinfo import ZoneInfo

            timezone = ZoneInfo(timezone)
        self.timezone = timezone
        self.date_from = date_from or datetime.datetime(
            2026, 9, 1, 10, 0, 0, tzinfo=timezone
        )
        self.slug = "fake"

    def __repr__(self) -> str:
        return f"<FakeEvent tz={self.timezone}>"


# ---------------------------------------------------------------------------
# Reference registry with real ORM paths
# ---------------------------------------------------------------------------

_ORDER_STATUS_CHOICES = (
    ("n", "pending"),
    ("p", "paid"),
    ("e", "expired"),
    ("c", "canceled"),
)


def _spec(
    key: str,
    label: str,
    group: str,
    datatype: DataType,
    order_path: Optional[str],
    position_path: Optional[str],
    sortable: bool = True,
    aggregate_on_order: bool = False,
    choices: Optional[Tuple[Tuple[Any, Any], ...]] = None,
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "group": group,
        "datatype": datatype,
        "order_path": order_path,
        "position_path": position_path,
        "sortable": sortable,
        "aggregate_on_order": aggregate_on_order,
        "choices": choices,
    }


#: Order-level data. One value per order, so usable directly on both bases.
_ORDER_SPECS = (
    _spec("order.code", "Order code", "order", DataType.STRING, "code", "order__code"),
    _spec(
        "order.status",
        "Order status",
        "order",
        DataType.CHOICE,
        "status",
        "order__status",
        choices=_ORDER_STATUS_CHOICES,
    ),
    _spec(
        "order.datetime",
        "Order date",
        "order",
        DataType.DATETIME,
        "datetime",
        "order__datetime",
    ),
    _spec(
        "order.expires",
        "Expiry date",
        "order",
        DataType.DATETIME,
        "expires",
        "order__expires",
    ),
    _spec(
        "order.cancellation_date",
        "Cancellation date",
        "order",
        DataType.DATETIME,
        "cancellation_date",
        "order__cancellation_date",
    ),
    _spec("order.email", "E-mail", "order", DataType.EMAIL, "email", "order__email"),
    _spec("order.phone", "Phone", "order", DataType.PHONE, "phone", "order__phone"),
    _spec(
        "order.total", "Order total", "order", DataType.MONEY, "total", "order__total"
    ),
    _spec(
        "order.locale",
        "Language",
        "order",
        DataType.CHOICE,
        "locale",
        "order__locale",
    ),
    _spec(
        "order.testmode",
        "Test mode",
        "order",
        DataType.BOOLEAN,
        "testmode",
        "order__testmode",
    ),
    _spec(
        "order.comment",
        "Internal comment",
        "order",
        DataType.TEXT,
        "comment",
        "order__comment",
    ),
    _spec(
        "order.sales_channel",
        "Sales channel",
        "order",
        DataType.CHOICE,
        "sales_channel__identifier",
        "order__sales_channel__identifier",
    ),
    _spec(
        "order.require_approval",
        "Approval required",
        "order",
        DataType.BOOLEAN,
        "require_approval",
        "order__require_approval",
    ),
    _spec(
        "order.checkin_attention",
        "Check-in attention",
        "order",
        DataType.BOOLEAN,
        "checkin_attention",
        "order__checkin_attention",
    ),
)

_INVOICE_ADDRESS_SPECS = tuple(
    _spec(
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

#: Position-level data: one row per position on base ``orderposition``, many per
#: order on base ``order``. ``all_positions`` is the real ``related_name``
#: (docs/pretix-api-notes.md section 6.1); the canceled filter is applied by the
#: compiler, not baked into the path.
_POSITION_SPECS = (
    _spec(
        "position.positionid",
        "Position number",
        "position",
        DataType.INTEGER,
        "all_positions__positionid",
        "positionid",
        aggregate_on_order=True,
    ),
    _spec(
        "position.price",
        "Position price",
        "position",
        DataType.MONEY,
        "all_positions__price",
        "price",
        aggregate_on_order=True,
    ),
    _spec(
        "position.tax_rate",
        "Tax rate",
        "position",
        DataType.DECIMAL,
        "all_positions__tax_rate",
        "tax_rate",
        aggregate_on_order=True,
    ),
    _spec(
        "position.tax_value",
        "Tax amount",
        "position",
        DataType.MONEY,
        "all_positions__tax_value",
        "tax_value",
        aggregate_on_order=True,
    ),
    _spec(
        "position.attendee_name",
        "Attendee name",
        "position",
        DataType.STRING,
        "all_positions__attendee_name_cached",
        "attendee_name_cached",
        aggregate_on_order=True,
    ),
    _spec(
        "position.attendee_email",
        "Attendee e-mail",
        "position",
        DataType.EMAIL,
        "all_positions__attendee_email",
        "attendee_email",
        aggregate_on_order=True,
    ),
    _spec(
        "position.company",
        "Attendee company",
        "position",
        DataType.STRING,
        "all_positions__company",
        "company",
        aggregate_on_order=True,
    ),
    _spec(
        "position.city",
        "Attendee city",
        "position",
        DataType.STRING,
        "all_positions__city",
        "city",
        aggregate_on_order=True,
    ),
    _spec(
        "position.country",
        "Attendee country",
        "position",
        DataType.COUNTRY,
        "all_positions__country",
        "country",
        aggregate_on_order=True,
    ),
    _spec(
        "position.canceled",
        "Position canceled",
        "position",
        DataType.BOOLEAN,
        "all_positions__canceled",
        "canceled",
        aggregate_on_order=True,
    ),
    _spec(
        "position.pseudonymization_id",
        "Attendee ID",
        "position",
        DataType.STRING,
        "all_positions__pseudonymization_id",
        "pseudonymization_id",
        aggregate_on_order=True,
    ),
)

_PRODUCT_SPECS = (
    _spec(
        "item.name",
        "Product",
        "item",
        DataType.I18N,
        "all_positions__item__name",
        "item__name",
        aggregate_on_order=True,
    ),
    _spec(
        "item.internal_name",
        "Product (internal name)",
        "item",
        DataType.STRING,
        "all_positions__item__internal_name",
        "item__internal_name",
        aggregate_on_order=True,
    ),
    _spec(
        "item.category",
        "Product category",
        "item",
        DataType.I18N,
        "all_positions__item__category__name",
        "item__category__name",
        aggregate_on_order=True,
    ),
    _spec(
        "item.admission",
        "Admission product",
        "item",
        DataType.BOOLEAN,
        "all_positions__item__admission",
        "item__admission",
        aggregate_on_order=True,
    ),
    _spec(
        "variation.value",
        "Product variation",
        "item",
        DataType.I18N,
        "all_positions__variation__value",
        "variation__value",
        aggregate_on_order=True,
    ),
    _spec(
        "subevent.name",
        "Date",
        "subevent",
        DataType.I18N,
        "all_positions__subevent__name",
        "subevent__name",
        aggregate_on_order=True,
    ),
    _spec(
        "subevent.date_from",
        "Date start",
        "subevent",
        DataType.DATETIME,
        "all_positions__subevent__date_from",
        "subevent__date_from",
        aggregate_on_order=True,
    ),
    _spec(
        "seat.zone_name",
        "Seat zone",
        "seat",
        DataType.STRING,
        "all_positions__seat__zone_name",
        "seat__zone_name",
        aggregate_on_order=True,
    ),
    _spec(
        "seat.row_name",
        "Seat row",
        "seat",
        DataType.STRING,
        "all_positions__seat__row_name",
        "seat__row_name",
        aggregate_on_order=True,
    ),
    _spec(
        "seat.seat_number",
        "Seat number",
        "seat",
        DataType.STRING,
        "all_positions__seat__seat_number",
        "seat__seat_number",
        aggregate_on_order=True,
    ),
    _spec(
        "voucher.code",
        "Voucher code",
        "voucher",
        DataType.STRING,
        "all_positions__voucher__code",
        "voucher__code",
        aggregate_on_order=True,
    ),
    _spec(
        "voucher.tag",
        "Voucher tag",
        "voucher",
        DataType.STRING,
        "all_positions__voucher__tag",
        "voucher__tag",
        aggregate_on_order=True,
    ),
)

_ALL_SPECS = _ORDER_SPECS + _INVOICE_ADDRESS_SPECS + _POSITION_SPECS + _PRODUCT_SPECS

#: Questions the reference event is expected to have, mirroring ``_index.json``.
REFERENCE_QUESTIONS: Tuple[Tuple[str, str, DataType], ...] = (
    ("tshirt-size", "T-shirt size", DataType.CHOICE),
    ("arrival-date", "Day of arrival", DataType.DATE),
    ("newsletter", "Newsletter opt-in", DataType.BOOLEAN),
)

REFERENCE_META_PROPERTIES: Tuple[Tuple[str, str], ...] = (("campaign", "Campaign"),)

REFERENCE_PLUGIN_APP_LABEL = "pretix_demo"


def _order_prefix(base: Base) -> str:
    """Lookup prefix from a row of *base* to its ``Order``."""
    return "" if base is Base.ORDER else "order__"


def _money() -> DecimalField:
    return DecimalField(max_digits=13, decimal_places=2)


def _payment_sum_annotation(base: Base):
    """``Sum`` of confirmed payments, as a correlated subquery.

    Modelled on ``Order.annotate_overpayments``
    (pretix/base/models/orders.py:510-575), which is the reference the ADR points
    at for expensive aggregate columns.
    """
    outer = "pk" if base is Base.ORDER else "order_id"

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        inner = (
            OrderPayment.objects.filter(
                order=OuterRef(outer), state=OrderPayment.PAYMENT_STATE_CONFIRMED
            )
            .order_by()
            .values("order")
            .annotate(s=Sum("amount"))
            .values("s")
        )
        return {
            "pcr_payment_sum": Coalesce(
                Subquery(inner, output_field=_money()),
                Value(0, output_field=_money()),
                output_field=_money(),
            )
        }

    return build


def _refund_sum_annotation(base: Base):
    outer = "pk" if base is Base.ORDER else "order_id"

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        inner = (
            OrderRefund.objects.filter(
                order=OuterRef(outer), state=OrderRefund.REFUND_STATE_DONE
            )
            .order_by()
            .values("order")
            .annotate(s=Sum("amount"))
            .values("s")
        )
        return {
            "pcr_refund_sum": Coalesce(
                Subquery(inner, output_field=_money()),
                Value(0, output_field=_money()),
                output_field=_money(),
            )
        }

    return build


def _position_count_annotation(base: Base):
    outer = "pk" if base is Base.ORDER else "order_id"

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        inner = (
            OrderPosition.objects.filter(order=OuterRef(outer))
            .order_by()
            .values("order")
            .annotate(c=Count("pk"))
            .values("c")
        )
        return {"pcr_position_count": Coalesce(Subquery(inner), 0)}

    return build


def _pending_sum_annotation(base: Base):
    """``total - confirmed payments + done refunds``, all in SQL.

    Mirrors ``Order.pending_sum`` (pretix/base/models/orders.py:495-508) without
    its per-object queries.
    """
    prefix = _order_prefix(base)
    payment = _payment_sum_annotation(base)
    refund = _refund_sum_annotation(base)

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        annotations = dict(payment(ctx))
        annotations.update(refund(ctx))
        annotations["pcr_pending_sum"] = (
            F(f"{prefix}total") - F("pcr_payment_sum") + F("pcr_refund_sum")
        )
        return annotations

    return build


def _checkin_annotations(base: Base):
    """Successful entry check-ins only.

    ``Checkin.objects`` filters ``successful=True``, but that filter does **not**
    travel along a relation lookup (docs/pretix-api-notes.md section 6.10), so the
    condition is spelled out. ``type=entry`` is spelled out for the same reason:
    an exit scan is a check-in row too.
    """
    if base is Base.ORDER:
        correlation = {"position__order": OuterRef("pk")}
        group_by = "position__order"
    else:
        correlation = {"position": OuterRef("pk")}
        group_by = "position"

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        base_qs = Checkin.all.filter(
            successful=True, type=Checkin.TYPE_ENTRY, **correlation
        ).order_by()
        counted = base_qs.values(group_by).annotate(c=Count("pk")).values("c")
        first = base_qs.values(group_by).annotate(d=Min("datetime")).values("d")
        last = base_qs.values(group_by).annotate(d=Max("datetime")).values("d")
        return {
            "pcr_checkin_count": Coalesce(Subquery(counted), 0),
            "pcr_checkin_first": Subquery(first, output_field=DateTimeField()),
            "pcr_checkin_last": Subquery(last, output_field=DateTimeField()),
        }

    return build


def _answer_annotation(identifier: str, alias: str, base: Base):
    """The answer to one question, as a correlated subquery.

    ``unique_together (orderposition, question)`` means there is at most one row
    per position and question (docs/pretix-api-notes.md section 6.4), so on base
    ``orderposition`` this is a plain scalar subquery. On base ``order`` the field
    is one-to-many and the compiler aggregates it -- see
    :func:`_answer_field` for the ``extra`` it gets instead.
    """

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        inner = QuestionAnswer.objects.filter(
            orderposition=OuterRef("pk"), question__identifier=identifier
        ).order_by()
        if getattr(ctx.event, "pk", None) is not None:
            # Question identifiers are unique per event, and the subquery is
            # already correlated to a position of this event, so this narrowing is
            # belt and braces. Skipped for the FakeEvent used by the plan-shape
            # tests, which has no primary key.
            inner = inner.filter(question__event=ctx.event)
        return {alias: Subquery(inner.values("answer")[:1])}

    return build


def _answer_alias(identifier: str) -> str:
    safe = identifier.replace("-", "_").replace(".", "_")
    return f"pcr_answer_{safe}"


def _answer_field(
    identifier: str, label: str, datatype: DataType, base: Base
) -> ReportField:
    if base is Base.ORDERPOSITION:
        return ReportField(
            key=question_field_key(identifier),
            label=label,
            group="answers",
            datatype=datatype,
            bases=(base,),
            orm_path=_answer_alias(identifier),
            annotation=_answer_annotation(identifier, _answer_alias(identifier), base),
            filter_operators=DEFAULT_OPERATORS.get(datatype, ()),
            sortable=True,
        )
    # Base ``order``: many answers per order. No annotation -- the field is handed
    # to the compiler as a plain relation path plus the question condition, which
    # is what lets ``count``, ``count_distinct``, ``join`` and the ``EXISTS``
    # filters all work off one declaration. The path crosses two reverse foreign
    # keys (Order -> OrderPosition -> QuestionAnswer); the compiler's relation
    # chain handles that.
    return ReportField(
        key=question_field_key(identifier),
        label=label,
        group="answers",
        datatype=datatype,
        bases=(base,),
        orm_path="all_positions__answers__answer",
        filter_operators=DEFAULT_OPERATORS.get(datatype, ()),
        sortable=False,
        aggregates=(Aggregate.JOIN, Aggregate.COUNT, Aggregate.COUNT_DISTINCT),
        requires_aggregate_on=(Base.ORDER,),
        extra={
            hints.EXTRA_AGGREGATE_RELATION: "all_positions__answers",
            hints.EXTRA_CANCELED_FLAG: "all_positions__canceled",
            # No EXTRA_AGGREGATE_QUESTION_PK -- see the module docstring.
            EXTRA_RELATION_FILTER: Q(question__identifier=identifier),
        },
    )


def _meta_field(name: str, label: str, base: Base) -> ReportField:
    """An event meta property.

    Event meta values are the same for every row of an event-scoped report, so
    they are a constant rather than a join: one ``Value()`` instead of a
    ``meta_values`` subquery per row.
    """
    alias = f"pcr_meta_event_{name}"

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        value = None
        event = ctx.event
        if event is not None:
            try:
                value = event.meta_data.get(name)
            except Exception:
                value = None
        return {alias: Value(value or "")}

    return ReportField(
        key=meta_field_key("event", name),
        label=label,
        group="meta",
        datatype=DataType.STRING,
        bases=(base,),
        orm_path=alias,
        annotation=build,
        filter_operators=DEFAULT_OPERATORS[DataType.STRING],
        sortable=True,
    )


def _plugin_field(base: Base) -> ReportField:
    """Stands in for a field contributed by another plugin (SPEC.md F5)."""
    alias = "pcr_demo_value"
    prefix = _order_prefix(base)

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        return {alias: F(f"{prefix}code")}

    return ReportField(
        key=plugin_field_key(REFERENCE_PLUGIN_APP_LABEL, "demo_value"),
        label="Demo value (other plugin)",
        group="Demo plugin",
        datatype=DataType.STRING,
        bases=(base,),
        orm_path=alias,
        annotation=build,
        filter_operators=DEFAULT_OPERATORS[DataType.STRING],
        sortable=True,
        provider=REFERENCE_PLUGIN_APP_LABEL,
    )


def _annotated_field(
    key: str,
    label: str,
    group: str,
    datatype: DataType,
    alias: str,
    annotation: Any,
    base: Base,
    sortable: bool = True,
    filter_operators: Optional[Tuple[Operator, ...]] = None,
) -> ReportField:
    return ReportField(
        key=key,
        label=label,
        group=group,
        datatype=datatype,
        bases=(base,),
        orm_path=alias,
        annotation=annotation,
        filter_operators=(
            filter_operators
            if filter_operators is not None
            else DEFAULT_OPERATORS.get(datatype, ())
        ),
        sortable=sortable,
    )


def _build_plain_field(spec: Dict[str, Any], base: Base) -> Optional[ReportField]:
    path = spec["order_path"] if base is Base.ORDER else spec["position_path"]
    if path is None:
        return None

    needs_aggregate = base is Base.ORDER and spec["aggregate_on_order"]
    aggregates: Tuple[Aggregate, ...] = ()
    if spec["aggregate_on_order"]:
        aggregates = AGGREGATES_FOR_DATATYPE.get(spec["datatype"], (Aggregate.COUNT,))

    choices = spec["choices"]
    extra: Dict[str, Any] = {}
    if needs_aggregate:
        # Exactly what registry/core.py puts on these fields. The compiler reads
        # it through ``hints.aggregate_filter``.
        extra[hints.EXTRA_AGGREGATE_RELATION] = "all_positions"
        extra[hints.EXTRA_CANCELED_FLAG] = "all_positions__canceled"
    return ReportField(
        key=spec["key"],
        label=spec["label"],
        group=spec["group"],
        datatype=spec["datatype"],
        bases=(base,),
        orm_path=path,
        filter_operators=DEFAULT_OPERATORS.get(spec["datatype"], ()),
        # Sorting by an aggregated value is out of scope for v1
        # (docs/adr/0001-contracts.md section 7b).
        sortable=spec["sortable"] and not needs_aggregate,
        choices=(lambda ctx, ch=choices: list(ch)) if choices else None,
        aggregates=aggregates if needs_aggregate else (),
        requires_aggregate_on=(Base.ORDER,) if needs_aggregate else (),
        value_scope=ValueScope.GLOBAL,
        extra=extra or None,
    )


class ReferenceRegistry:
    """A :class:`~pretix_custom_reports.contracts.protocols.FieldRegistry` with
    real ORM paths, so compiled reports actually run.

    ``questions`` defaults to :data:`REFERENCE_QUESTIONS`; pass a shorter list to
    simulate a renamed identifier.
    """

    def __init__(
        self,
        questions: Tuple[Tuple[str, str, DataType], ...] = REFERENCE_QUESTIONS,
        meta_properties: Tuple[Tuple[str, str], ...] = REFERENCE_META_PROPERTIES,
        include_plugin_field: bool = True,
        overrides: Optional[Dict[str, ReportField]] = None,
    ) -> None:
        self._questions = tuple(questions)
        self._meta_properties = tuple(meta_properties)
        self._include_plugin_field = include_plugin_field
        self._overrides = dict(overrides or {})
        self._cache: Dict[Base, Dict[str, ReportField]] = {}

    def _build(self, base: Base) -> Dict[str, ReportField]:
        fields: Dict[str, ReportField] = {}
        for spec in _ALL_SPECS:
            built = _build_plain_field(spec, base)
            if built is not None:
                fields[built.key] = built

        fields["order.position_count"] = _annotated_field(
            "order.position_count",
            "Number of positions",
            "order",
            DataType.INTEGER,
            "pcr_position_count",
            _position_count_annotation(base),
            base,
        )
        fields["order.pending_sum"] = _annotated_field(
            "order.pending_sum",
            "Outstanding amount",
            "order",
            DataType.MONEY,
            "pcr_pending_sum",
            _pending_sum_annotation(base),
            base,
        )
        fields["payment.sum_confirmed"] = _annotated_field(
            "payment.sum_confirmed",
            "Amount paid",
            "payment",
            DataType.MONEY,
            "pcr_payment_sum",
            _payment_sum_annotation(base),
            base,
        )
        fields["refund.sum_done"] = _annotated_field(
            "refund.sum_done",
            "Amount refunded",
            "payment",
            DataType.MONEY,
            "pcr_refund_sum",
            _refund_sum_annotation(base),
            base,
        )
        fields["payment.providers"] = ReportField(
            key="payment.providers",
            label="Payment providers",
            group="payment",
            datatype=DataType.LIST,
            bases=(base,),
            orm_path="pcr_payment_providers",
            annotation=_payment_providers_annotation(base),
            filter_operators=(
                Operator.CONTAINS,
                Operator.NOT_CONTAINS,
                Operator.IS_EMPTY,
                Operator.IS_NOT_EMPTY,
            ),
            sortable=False,
        )
        checkins = _checkin_annotations(base)
        for key, label, alias, datatype in (
            ("checkin.count", "Check-ins", "pcr_checkin_count", DataType.INTEGER),
            (
                "checkin.first_datetime",
                "First check-in",
                "pcr_checkin_first",
                DataType.DATETIME,
            ),
            (
                "checkin.last_datetime",
                "Last check-in",
                "pcr_checkin_last",
                DataType.DATETIME,
            ),
        ):
            fields[key] = _annotated_field(
                key, label, "checkin", datatype, alias, checkins, base
            )

        for identifier, label, datatype in self._questions:
            built = _answer_field(identifier, label, datatype, base)
            fields[built.key] = built
        for name, label in self._meta_properties:
            built = _meta_field(name, label, base)
            fields[built.key] = built
        if self._include_plugin_field:
            built = _plugin_field(base)
            fields[built.key] = built

        fields.update(self._overrides)
        return fields

    # -- FieldRegistry protocol -------------------------------------------

    def get_fields(
        self, event: Any, base: Union[Base, str]
    ) -> Mapping[str, ReportField]:
        coerced = Base.coerce(base)
        if coerced not in self._cache:
            self._cache[coerced] = self._build(coerced)
        return dict(self._cache[coerced])

    def resolve(
        self, key: str, event: Any, base: Union[Base, str]
    ) -> Optional[ReportField]:
        return self.get_fields(event, base).get(key)


def _payment_providers_annotation(base: Base):
    """Distinct payment providers of an order, as one text value.

    A ``LIST`` datatype column that is not sortable -- the fixture ``_index.json``
    names ``payment.providers`` as the one field that must not be sortable on base
    ``orderposition``, so the reference registry has to reproduce that.
    """
    outer = "pk" if base is Base.ORDER else "order_id"

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        inner = (
            OrderPayment.objects.filter(order=OuterRef(outer))
            .order_by()
            .values("order")
            .annotate(c=Count("provider", distinct=True))
            .values("c")
        )
        # Count rather than a string aggregation: there is no portable string
        # aggregate in Django 5.2, and this module only has to exercise the
        # LIST/not-sortable combination, not render it prettily.
        return {"pcr_payment_providers": Coalesce(Subquery(inner), 0)}

    return build


def reference_questions_for(event: Any) -> Tuple[Question, ...]:
    """Create the three reference questions on *event*. Requires a database."""
    type_map = {
        DataType.CHOICE: Question.TYPE_CHOICE,
        DataType.DATE: Question.TYPE_DATE,
        DataType.BOOLEAN: Question.TYPE_BOOLEAN,
    }
    created = []
    for position, (identifier, label, datatype) in enumerate(REFERENCE_QUESTIONS):
        created.append(
            Question.objects.create(
                event=event,
                question=label,
                identifier=identifier,
                type=type_map[datatype],
                position=position,
            )
        )
    return tuple(created)


def order_codes(report: Any) -> Tuple[str, ...]:
    """The ``order.code`` column of every row, for readable assertions."""
    index = [c.key for c in report.columns].index("order.code")
    return tuple(row[index] for row in report.iter_rows())


def column_values(report: Any, key: str) -> Tuple[Any, ...]:
    """Every value of the column with registry key *key*."""
    index = [c.key for c in report.columns].index(key)
    return tuple(row[index] for row in report.iter_rows())


def sql_of(report: Any) -> str:
    """The compiled SQL of a report's queryset, for path-provenance assertions."""
    return str(report.queryset.query)


def all_order_objects() -> Any:
    """``Order`` -- re-exported so tests do not import pretix models directly."""
    return Order
