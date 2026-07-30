"""The hand-curated core field table.

Owner: registry-dev.

**Every field in here is a decision somebody made on purpose.** Nothing is
derived from ``Model._meta``. That is not conservatism, it is the security
boundary: this table is the allow-list that a stored or imported definition is
resolved against (CLAUDE.md rule 2, ADR 0001 section 2). Introspecting the models
would publish ``Order.secret``, ``Order.internal_secret``,
``OrderPosition.secret``, ``web_secret``, ``nonce`` and every relation path
reachable from them into a surface whose input is untrusted JSON. A single
missing exclusion would then be a data leak instead of a missing feature.

The consequences of doing it by hand, both of them accepted:

* a new pretix release does not automatically make new columns available,
* a renamed pretix field breaks exactly one entry here, loudly, in a test --
  rather than silently breaking every saved report.

Layout
------

Most fields are one :class:`_Spec` row: a key, a label, a datatype and one ORM
path per report base. The two paths differ because the base model differs --
``code`` on ``Order`` is ``order__code`` seen from an ``OrderPosition``.

``aggregate_on_order`` marks the one-to-many data. On base ``order`` those
fields get the ``all_positions__...`` path, ``requires_aggregate_on=(ORDER,)``
and ``sortable=False`` (ADR 0001 section 7b). ``all_positions`` and not
``positions``, because only the former is a real ``related_name``; the canceled
filter is handed to the compiler through :mod:`registry.hints`.

Fields that need an expression or Python are built explicitly further down, via
:func:`_annotated` and :func:`_python`.

Deliberately absent
-------------------

``secret``, ``internal_secret``, ``web_secret``, ``nonce``,
``pseudonymization_id`` of the *order*, ``meta_info`` (JSON in a ``TextField``,
so not queryable anyway -- docs/pretix-api-notes.md section 6.2 pitfall 3),
``organizer`` on ``Order``/``OrderPosition`` (redundant nullable FKs, pitfall 4),
and every ``*_includes_rounding_correction`` column (pitfall 8: they express
pre-rounding values and would silently disagree with the totals).
``OrderPosition.pseudonymization_id`` *is* offered -- it is the attendee ID
printed on the ticket and designed to be shared.
"""

from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from dataclasses import dataclass, field as dataclass_field
from django.utils.translation import gettext_lazy as _

from pretix_custom_reports.contracts import (
    AGGREGATES_FOR_DATATYPE,
    DEFAULT_OPERATORS,
    GROUP_CHECKIN,
    GROUP_INVOICE_ADDRESS,
    GROUP_ITEM,
    GROUP_ORDER,
    GROUP_PAYMENT,
    GROUP_POSITION,
    GROUP_SEAT,
    GROUP_SUBEVENT,
    GROUP_VOUCHER,
    Aggregate,
    Base,
    DataType,
    FieldContext,
    Operator,
    ReportField,
    ValueScope,
)
from pretix_custom_reports.registry import annotations, choices
from pretix_custom_reports.registry.groups import GROUP_DISCOUNT
from pretix_custom_reports.registry.hints import (
    EXTRA_AGGREGATE_RELATION,
    EXTRA_CANCELED_FLAG,
    EXTRA_PAYMENT_STATES,
    EXTRA_REFUND_STATES,
)

__all__ = [
    "POSITION_RELATION",
    "core_field_keys",
    "core_fields",
]

#: The ``related_name`` from ``Order`` to its positions. ``all_positions``
#: contains canceled positions; ``Order.positions`` is a Python property over
#: the filtered manager and therefore not usable as an ORM path
#: (docs/pretix-api-notes.md section 6.2, pitfall 1).
POSITION_RELATION = "all_positions"

_CANCELED_FLAG = f"{POSITION_RELATION}__canceled"

#: No aggregate at all. Used where an aggregate would be meaningless rather than
#: merely unusual, e.g. joining check-in counts.
_NO_AGGREGATES: Tuple[Aggregate, ...] = ()


@dataclass(frozen=True)
class _Spec:
    """One plain ORM-path field, expanded once per report base."""

    key: str
    label: Any
    group: str
    datatype: DataType

    order_path: Optional[str] = None
    """Path relative to ``Order``. ``None`` = not offered on base ``order``."""

    position_path: Optional[str] = None
    """Path relative to ``OrderPosition``. ``None`` = not offered there."""

    aggregate_on_order: bool = False
    """One-to-many for an order: needs an aggregate on base ``order``."""

    sortable: bool = True
    operators: Optional[Tuple[Operator, ...]] = None
    aggregates: Optional[Tuple[Aggregate, ...]] = None
    choices: Optional[Callable[[FieldContext], Sequence[Tuple[Any, Any]]]] = None
    value_scope: ValueScope = ValueScope.GLOBAL
    order_select_related: Tuple[str, ...] = ()
    position_select_related: Tuple[str, ...] = ()
    help_text: Any = None
    extra: Mapping[str, Any] = dataclass_field(default_factory=dict)


def _s(*args: Any, **kwargs: Any) -> _Spec:
    return _Spec(*args, **kwargs)


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

_TEXT_ONLY: Tuple[Operator, ...] = (
    Operator.CONTAINS,
    Operator.NOT_CONTAINS,
    Operator.IS_EMPTY,
    Operator.IS_NOT_EMPTY,
)

_ORDER_SPECS: Tuple[_Spec, ...] = (
    _s(
        "order.code",
        _("Order code"),
        GROUP_ORDER,
        DataType.STRING,
        "code",
        "order__code",
    ),
    _s(
        "order.status",
        _("Order status"),
        GROUP_ORDER,
        DataType.CHOICE,
        "status",
        "order__status",
        choices=choices.order_status_choices,
        help_text=_("n = pending, p = paid, e = expired, c = canceled."),
    ),
    _s(
        "order.datetime",
        _("Order date"),
        GROUP_ORDER,
        DataType.DATETIME,
        "datetime",
        "order__datetime",
    ),
    _s(
        "order.expires",
        _("Payment deadline"),
        GROUP_ORDER,
        DataType.DATETIME,
        "expires",
        "order__expires",
    ),
    _s(
        "order.cancellation_date",
        _("Cancellation date"),
        GROUP_ORDER,
        DataType.DATETIME,
        "cancellation_date",
        "order__cancellation_date",
    ),
    _s(
        "order.last_modified",
        _("Last modified"),
        GROUP_ORDER,
        DataType.DATETIME,
        "last_modified",
        "order__last_modified",
    ),
    _s(
        "order.custom_followup_at",
        _("Follow-up date"),
        GROUP_ORDER,
        DataType.DATE,
        "custom_followup_at",
        "order__custom_followup_at",
    ),
    _s(
        "order.email", _("E-mail"), GROUP_ORDER, DataType.EMAIL, "email", "order__email"
    ),
    _s("order.phone", _("Phone"), GROUP_ORDER, DataType.PHONE, "phone", "order__phone"),
    _s(
        "order.locale",
        _("Language"),
        GROUP_ORDER,
        DataType.CHOICE,
        "locale",
        "order__locale",
        choices=choices.locale_choices,
    ),
    _s(
        "order.sales_channel",
        _("Sales channel"),
        GROUP_ORDER,
        DataType.CHOICE,
        "sales_channel__identifier",
        "order__sales_channel__identifier",
        choices=choices.sales_channel_choices,
        order_select_related=("sales_channel",),
        position_select_related=("order__sales_channel",),
    ),
    _s(
        "order.total",
        _("Order total"),
        GROUP_ORDER,
        DataType.MONEY,
        "total",
        "order__total",
    ),
    _s(
        "order.testmode",
        _("Test mode"),
        GROUP_ORDER,
        DataType.BOOLEAN,
        "testmode",
        "order__testmode",
    ),
    _s(
        "order.require_approval",
        _("Approval required"),
        GROUP_ORDER,
        DataType.BOOLEAN,
        "require_approval",
        "order__require_approval",
    ),
    _s(
        "order.valid_if_pending",
        _("Valid although unpaid"),
        GROUP_ORDER,
        DataType.BOOLEAN,
        "valid_if_pending",
        "order__valid_if_pending",
    ),
    _s(
        "order.checkin_attention",
        _("Requires attention at check-in"),
        GROUP_ORDER,
        DataType.BOOLEAN,
        "checkin_attention",
        "order__checkin_attention",
    ),
    _s(
        "order.checkin_text",
        _("Check-in note"),
        GROUP_ORDER,
        DataType.TEXT,
        "checkin_text",
        "order__checkin_text",
        operators=_TEXT_ONLY,
    ),
    _s(
        "order.comment",
        _("Internal comment"),
        GROUP_ORDER,
        DataType.TEXT,
        "comment",
        "order__comment",
        operators=_TEXT_ONLY,
    ),
)

# ---------------------------------------------------------------------------
# Invoice address
# ---------------------------------------------------------------------------

_INVOICE_ADDRESS_FIELDS: Tuple[Tuple[str, Any, DataType, str], ...] = (
    ("name", _("Invoice name"), DataType.STRING, "name_cached"),
    ("company", _("Company"), DataType.STRING, "company"),
    ("street", _("Street"), DataType.TEXT, "street"),
    ("zipcode", _("ZIP code"), DataType.STRING, "zipcode"),
    ("city", _("City"), DataType.STRING, "city"),
    ("country", _("Country"), DataType.COUNTRY, "country"),
    ("state", _("State"), DataType.STRING, "state"),
    ("vat_id", _("VAT ID"), DataType.STRING, "vat_id"),
    ("is_business", _("Business customer"), DataType.BOOLEAN, "is_business"),
    (
        "internal_reference",
        _("Internal reference"),
        DataType.STRING,
        "internal_reference",
    ),
    ("beneficiary", _("Beneficiary"), DataType.TEXT, "beneficiary"),
    ("custom_field", _("Custom address field"), DataType.STRING, "custom_field"),
)

_INVOICE_ADDRESS_SPECS: Tuple[_Spec, ...] = tuple(
    _s(
        f"invoice_address.{name}",
        label,
        GROUP_INVOICE_ADDRESS,
        datatype,
        f"invoice_address__{path}",
        f"order__invoice_address__{path}",
        choices=choices.country_choices if datatype is DataType.COUNTRY else None,
        order_select_related=("invoice_address",),
        position_select_related=("order__invoice_address",),
    )
    for name, label, datatype, path in _INVOICE_ADDRESS_FIELDS
)

# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------

_POSITION_FIELDS: Tuple[Tuple[str, Any, DataType, str], ...] = (
    ("positionid", _("Position number"), DataType.INTEGER, "positionid"),
    ("price", _("Price (gross)"), DataType.MONEY, "price"),
    ("tax_rate", _("Tax rate"), DataType.DECIMAL, "tax_rate"),
    ("tax_value", _("Tax amount"), DataType.MONEY, "tax_value"),
    ("tax_code", _("Tax code"), DataType.STRING, "tax_code"),
    ("attendee_name", _("Attendee name"), DataType.STRING, "attendee_name_cached"),
    ("attendee_email", _("Attendee e-mail"), DataType.EMAIL, "attendee_email"),
    ("company", _("Attendee company"), DataType.STRING, "company"),
    ("street", _("Attendee street"), DataType.TEXT, "street"),
    ("zipcode", _("Attendee ZIP code"), DataType.STRING, "zipcode"),
    ("city", _("Attendee city"), DataType.STRING, "city"),
    ("country", _("Attendee country"), DataType.COUNTRY, "country"),
    ("state", _("Attendee state"), DataType.STRING, "state"),
    ("canceled", _("Position canceled"), DataType.BOOLEAN, "canceled"),
    ("is_bundled", _("Part of a bundle"), DataType.BOOLEAN, "is_bundled"),
    (
        "pseudonymization_id",
        _("Attendee ID (pseudonymised)"),
        DataType.STRING,
        "pseudonymization_id",
    ),
    ("valid_from", _("Ticket valid from"), DataType.DATETIME, "valid_from"),
    ("valid_until", _("Ticket valid until"), DataType.DATETIME, "valid_until"),
    (
        "addon_to",
        _("Add-on to position number"),
        DataType.INTEGER,
        "addon_to__positionid",
    ),
)

_POSITION_SPECS: Tuple[_Spec, ...] = tuple(
    _s(
        f"position.{name}",
        label,
        GROUP_POSITION,
        datatype,
        f"{POSITION_RELATION}__{path}",
        path,
        aggregate_on_order=True,
        choices=choices.country_choices if datatype is DataType.COUNTRY else None,
    )
    for name, label, datatype, path in _POSITION_FIELDS
)

# ---------------------------------------------------------------------------
# Product, variation, date, seat, voucher, discount
# ---------------------------------------------------------------------------

_PRODUCT_FIELDS: Tuple[Tuple[str, Any, str, DataType, str, Any, ValueScope], ...] = (
    (
        "item.name",
        _("Product"),
        GROUP_ITEM,
        DataType.I18N,
        "item__name",
        choices.item_name_choices,
        ValueScope.EVENT,
    ),
    (
        "item.internal_name",
        _("Product (internal name)"),
        GROUP_ITEM,
        DataType.STRING,
        "item__internal_name",
        choices.item_internal_name_choices,
        ValueScope.EVENT,
    ),
    (
        "item.category",
        _("Product category"),
        GROUP_ITEM,
        DataType.I18N,
        "item__category__name",
        choices.category_choices,
        ValueScope.EVENT,
    ),
    (
        "item.admission",
        _("Admission product"),
        GROUP_ITEM,
        DataType.BOOLEAN,
        "item__admission",
        None,
        ValueScope.GLOBAL,
    ),
    (
        "item.default_price",
        _("Product default price"),
        GROUP_ITEM,
        DataType.MONEY,
        "item__default_price",
        None,
        ValueScope.GLOBAL,
    ),
    (
        "variation.value",
        _("Product variation"),
        GROUP_ITEM,
        DataType.I18N,
        "variation__value",
        choices.variation_choices,
        ValueScope.EVENT,
    ),
    (
        "subevent.name",
        _("Date"),
        GROUP_SUBEVENT,
        DataType.I18N,
        "subevent__name",
        choices.subevent_name_choices,
        ValueScope.EVENT,
    ),
    (
        "subevent.date_from",
        _("Date start"),
        GROUP_SUBEVENT,
        DataType.DATETIME,
        "subevent__date_from",
        None,
        ValueScope.GLOBAL,
    ),
    (
        "subevent.date_to",
        _("Date end"),
        GROUP_SUBEVENT,
        DataType.DATETIME,
        "subevent__date_to",
        None,
        ValueScope.GLOBAL,
    ),
    (
        "subevent.date_admission",
        _("Date admission"),
        GROUP_SUBEVENT,
        DataType.DATETIME,
        "subevent__date_admission",
        None,
        ValueScope.GLOBAL,
    ),
    (
        "subevent.location",
        _("Date location"),
        GROUP_SUBEVENT,
        DataType.I18N,
        "subevent__location",
        None,
        ValueScope.GLOBAL,
    ),
    (
        "seat.zone_name",
        _("Seat zone"),
        GROUP_SEAT,
        DataType.STRING,
        "seat__zone_name",
        choices.seat_zone_choices,
        ValueScope.EVENT,
    ),
    (
        "seat.row_name",
        _("Seat row"),
        GROUP_SEAT,
        DataType.STRING,
        "seat__row_name",
        None,
        ValueScope.EVENT,
    ),
    (
        "seat.seat_number",
        _("Seat number"),
        GROUP_SEAT,
        DataType.STRING,
        "seat__seat_number",
        None,
        ValueScope.EVENT,
    ),
    (
        "seat.seat_guid",
        _("Seat ID"),
        GROUP_SEAT,
        DataType.STRING,
        "seat__seat_guid",
        None,
        ValueScope.EVENT,
    ),
    (
        "voucher.code",
        _("Voucher code"),
        GROUP_VOUCHER,
        DataType.STRING,
        "voucher__code",
        None,
        ValueScope.EVENT,
    ),
    (
        "voucher.tag",
        _("Voucher tag"),
        GROUP_VOUCHER,
        DataType.STRING,
        "voucher__tag",
        choices.voucher_tag_choices,
        ValueScope.EVENT,
    ),
    (
        "voucher.comment",
        _("Voucher comment"),
        GROUP_VOUCHER,
        DataType.TEXT,
        "voucher__comment",
        None,
        ValueScope.GLOBAL,
    ),
    (
        "discount.internal_name",
        _("Discount"),
        GROUP_DISCOUNT,
        DataType.STRING,
        "discount__internal_name",
        choices.discount_choices,
        ValueScope.EVENT,
    ),
)

_PRODUCT_SPECS: Tuple[_Spec, ...] = tuple(
    _s(
        key,
        label,
        group,
        datatype,
        f"{POSITION_RELATION}__{path}",
        path,
        aggregate_on_order=True,
        choices=choices_factory,
        value_scope=value_scope,
        position_select_related=(path.rsplit("__", 1)[0],),
    )
    for key, label, group, datatype, path, choices_factory, value_scope in _PRODUCT_FIELDS
)


_PATH_SPECS: Tuple[_Spec, ...] = (
    _ORDER_SPECS + _INVOICE_ADDRESS_SPECS + _POSITION_SPECS + _PRODUCT_SPECS
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_path_field(spec: _Spec, base: Base) -> Optional[ReportField]:
    path = spec.order_path if base is Base.ORDER else spec.position_path
    if path is None:
        return None

    needs_aggregate = base is Base.ORDER and spec.aggregate_on_order

    if spec.aggregates is not None:
        aggregates = spec.aggregates
    elif needs_aggregate:
        aggregates = AGGREGATES_FOR_DATATYPE.get(spec.datatype, (Aggregate.COUNT,))
    else:
        aggregates = _NO_AGGREGATES

    operators = (
        spec.operators
        if spec.operators is not None
        else DEFAULT_OPERATORS.get(spec.datatype, ())
    )

    extra: Dict[str, Any] = dict(spec.extra)
    if needs_aggregate:
        extra[EXTRA_AGGREGATE_RELATION] = POSITION_RELATION
        extra[EXTRA_CANCELED_FLAG] = _CANCELED_FLAG

    select_related = (
        spec.order_select_related
        if base is Base.ORDER
        else spec.position_select_related
    )

    return ReportField(
        key=spec.key,
        label=spec.label,
        group=spec.group,
        datatype=spec.datatype,
        bases=(base,),
        orm_path=path,
        filter_operators=operators,
        # Sorting by an aggregated value is out of scope for v1
        # (ADR 0001 section 7b).
        sortable=spec.sortable and not needs_aggregate,
        choices=spec.choices,
        aggregates=aggregates,
        requires_aggregate_on=(Base.ORDER,) if needs_aggregate else (),
        select_related=() if needs_aggregate else select_related,
        value_scope=spec.value_scope,
        help_text=spec.help_text,
        extra=extra,
    )


def _annotated(
    *,
    key: str,
    label: Any,
    group: str,
    datatype: DataType,
    base: Base,
    alias: str,
    annotation: Callable[[FieldContext], Mapping[str, Any]],
    sortable: bool = True,
    operators: Optional[Tuple[Operator, ...]] = None,
    field_choices: Optional[Callable[[FieldContext], Sequence[Tuple[Any, Any]]]] = None,
    help_text: Any = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> ReportField:
    """A field whose value comes from an annotation.

    ``orm_path`` is the annotation's alias, which is what makes the field
    filterable and sortable just like a column: the compiler puts the alias into
    ``filter()`` and ``order_by()`` after ``annotate()``. That is the one
    documented case where ``orm_path`` and ``annotation`` appear together, and
    the contract requires it (``ReportField.__post_init__``).
    """
    return ReportField(
        key=key,
        label=label,
        group=group,
        datatype=datatype,
        bases=(base,),
        orm_path=alias,
        annotation=annotation,
        filter_operators=(
            operators if operators is not None else DEFAULT_OPERATORS.get(datatype, ())
        ),
        sortable=sortable,
        choices=field_choices,
        help_text=help_text,
        extra=dict(extra or {}),
    )


def _python(
    *,
    key: str,
    label: Any,
    group: str,
    datatype: DataType,
    base: Base,
    value_getter: Callable[[Any], Any],
    select_related: Tuple[str, ...] = (),
    prefetch_related: Tuple[str, ...] = (),
    help_text: Any = None,
) -> ReportField:
    """A display-only field computed in Python.

    No ``filter_operators``, no ``sortable``, no ``aggregates`` -- the contract
    rejects all three for a field without an ``orm_path``, and rightly so: a
    filter that quietly does nothing is worse than a missing filter.
    """
    return ReportField(
        key=key,
        label=label,
        group=group,
        datatype=datatype,
        bases=(base,),
        value_getter=value_getter,
        sortable=False,
        select_related=select_related,
        prefetch_related=prefetch_related,
        help_text=help_text,
    )


# ---------------------------------------------------------------------------
# Value getters
# ---------------------------------------------------------------------------


def _order_of(row: Any, base: Base) -> Any:
    return row if base is Base.ORDER else row.order


def _full_code(base: Base) -> Callable[[Any], Any]:
    def getter(row: Any) -> Any:
        order = _order_of(row, base)
        return order.full_code if order is not None else None

    return getter


def _position_code(row: Any) -> Any:
    return row.code


def _seat_name(row: Any) -> Any:
    return str(row.seat) if row.seat_id else None


def _payment_providers(base: Base) -> Callable[[Any], Any]:
    """Distinct payment providers of the order, alphabetically, comma separated.

    Python rather than SQL because the obvious expression, ``StringAgg``, is
    PostgreSQL-only, and pretix also runs on SQLite. The price is that the field
    is display-only; the compiler must ``prefetch_related`` the payments, which
    is why the field declares that.
    """

    def getter(row: Any) -> Any:
        order = _order_of(row, base)
        if order is None:
            return None
        providers = sorted({payment.provider for payment in order.payments.all()})
        return ", ".join(providers)

    return getter


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _computed_core_fields(base: Base) -> Tuple[ReportField, ...]:
    """Core fields that need an expression or Python, in field-library order."""
    on_order = base is Base.ORDER
    fields = [
        _annotated(
            key="order.pending_sum",
            label=_("Outstanding amount"),
            group=GROUP_ORDER,
            datatype=DataType.MONEY,
            base=base,
            alias=annotations.ALIAS_PENDING_SUM,
            annotation=annotations.pending_sum_annotation(base),
            help_text=_(
                "Order total minus confirmed payments plus refunds, calculated the "
                "same way pretix calculates it. A canceled order owes nothing."
            ),
        ),
        _annotated(
            key="order.position_count",
            label=_("Number of positions"),
            group=GROUP_ORDER,
            datatype=DataType.INTEGER,
            base=base,
            alias=annotations.ALIAS_POSITION_COUNT,
            annotation=annotations.position_count_annotation(base),
            help_text=_("Canceled positions are not counted."),
        ),
        _python(
            key="order.full_code",
            label=_("Order code with event slug"),
            group=GROUP_ORDER,
            datatype=DataType.STRING,
            base=base,
            value_getter=_full_code(base),
            select_related=() if on_order else ("order",),
            help_text=_("Unique across all events of the organizer."),
        ),
        _annotated(
            key="payment.sum_confirmed",
            label=_("Amount paid"),
            group=GROUP_PAYMENT,
            datatype=DataType.MONEY,
            base=base,
            alias=annotations.ALIAS_PAYMENT_SUM,
            annotation=annotations.payment_sum_annotation(base),
            help_text=_(
                "Sum of all payments pretix counts as received, i.e. in state "
                "confirmed or refunded."
            ),
            extra={EXTRA_PAYMENT_STATES: list(annotations.COUNTED_PAYMENT_STATES)},
        ),
        _annotated(
            key="refund.sum_done",
            label=_("Amount refunded"),
            group=GROUP_PAYMENT,
            datatype=DataType.MONEY,
            base=base,
            alias=annotations.ALIAS_REFUND_SUM,
            annotation=annotations.refund_sum_annotation(base),
            help_text=_(
                "Sum of all refunds pretix counts, i.e. in state done, in transit "
                "or created."
            ),
            extra={EXTRA_REFUND_STATES: list(annotations.COUNTED_REFUND_STATES)},
        ),
        _annotated(
            key="payment.last_datetime",
            label=_("Last payment"),
            group=GROUP_PAYMENT,
            datatype=DataType.DATETIME,
            base=base,
            alias=annotations.ALIAS_PAYMENT_LAST,
            annotation=annotations.payment_last_annotation(base),
        ),
        _python(
            key="payment.providers",
            label=_("Payment providers"),
            group=GROUP_PAYMENT,
            datatype=DataType.LIST,
            base=base,
            value_getter=_payment_providers(base),
            select_related=() if on_order else ("order",),
            prefetch_related=("payments",) if on_order else ("order__payments",),
        ),
        _annotated(
            key="checkin.count",
            label=_("Number of check-ins"),
            group=GROUP_CHECKIN,
            datatype=DataType.INTEGER,
            base=base,
            alias=annotations.ALIAS_CHECKIN_COUNT,
            annotation=annotations.checkin_count_annotation(base),
            help_text=_(
                "Successful entry scans in this event only. Exit scans and failed "
                "scans are not counted."
            ),
        ),
        _annotated(
            key="checkin.first_datetime",
            label=_("First check-in"),
            group=GROUP_CHECKIN,
            datatype=DataType.DATETIME,
            base=base,
            alias=annotations.ALIAS_CHECKIN_FIRST,
            annotation=annotations.checkin_first_annotation(base),
        ),
        _annotated(
            key="checkin.last_datetime",
            label=_("Last check-in"),
            group=GROUP_CHECKIN,
            datatype=DataType.DATETIME,
            base=base,
            alias=annotations.ALIAS_CHECKIN_LAST,
            annotation=annotations.checkin_last_annotation(base),
        ),
    ]

    if not on_order:
        fields.extend(
            [
                _python(
                    key="position.code",
                    label=_("Position code"),
                    group=GROUP_POSITION,
                    datatype=DataType.STRING,
                    base=base,
                    value_getter=_position_code,
                    select_related=("order",),
                    help_text=_("Order code and position number, e.g. ABCDE-1."),
                ),
                _annotated(
                    key="position.net_price",
                    label=_("Price (net)"),
                    group=GROUP_POSITION,
                    datatype=DataType.MONEY,
                    base=base,
                    alias=annotations.ALIAS_NET_PRICE,
                    annotation=annotations.net_price_annotation(base),
                    help_text=_("Gross price minus tax amount."),
                ),
                _python(
                    key="seat.name",
                    label=_("Seat"),
                    group=GROUP_SEAT,
                    datatype=DataType.STRING,
                    base=base,
                    value_getter=_seat_name,
                    select_related=("seat",),
                    help_text=_(
                        "Full seat name as pretix displays it. Only available as a "
                        "column: it is assembled in Python, so it cannot be "
                        "filtered or sorted. Use seat zone, row and number for that."
                    ),
                ),
            ]
        )
    return tuple(fields)


def core_fields(base: Base) -> Dict[str, ReportField]:
    """Every core field for *base*, keyed by field key, in field-library order.

    Event independent by construction: everything event specific lives behind a
    lazy ``choices`` or ``annotation`` callable that receives the event through
    its :class:`~pretix_custom_reports.contracts.FieldContext`. That is what
    lets this table be built once per process instead of once per event.
    """
    coerced = Base.coerce(base)
    fields: Dict[str, ReportField] = {}
    for spec in _PATH_SPECS:
        built = _build_path_field(spec, coerced)
        if built is not None:
            fields[built.key] = built
    for built in _computed_core_fields(coerced):
        fields[built.key] = built
    return fields


def core_field_keys(base: Base) -> Tuple[str, ...]:
    """Keys of every core field for *base*. Cheap; does not build the fields."""
    return tuple(core_fields(base).keys())
