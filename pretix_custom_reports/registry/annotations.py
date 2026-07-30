"""Database expressions behind the annotated fields.

Owner: registry-dev.

Every function here returns an ``annotation`` callable in the shape the contract
asks for: ``FieldContext -> {alias: expression}``. Three rules hold throughout:

**Aliases are prefixed ``pcr_``.** ``pretix`` annotates ``pending_sum_t``,
``payment_sum``, ``pcnt`` and friends itself (``Order.annotate_overpayments``,
docs/pretix-api-notes.md section 6.1). Prefixing keeps us from colliding with a
queryset that already went through one of pretix' own helpers, and it makes the
alias recognisable in ``str(qs.query)`` during debugging.

**The same value always uses the same alias.** ``order.pending_sum`` and
``computed.payment_state`` both need the outstanding amount, and both emit it
under ``pcr_pending_sum`` with an identical expression. The compiler therefore
merges every used field's mapping into **one** dict and calls ``annotate()``
once; duplicate aliases collapse instead of raising
``ValueError: The annotation 'x' conflicts ...``.

**Order inside the mapping matters.** Later entries may reference earlier
aliases (``pcr_payment_state`` compares against ``pcr_pending_sum``), which
Django resolves because ``annotate(**mapping)`` adds them in dict order. Do not
sort the mapping.

No aggregate over a joined relation is used: everything is a correlated
``Subquery``, following ``Order.annotate_overpayments``
(``pretix/base/models/orders.py:510-575``). That is what keeps a report with
several money columns from multiplying rows, and it is why ``SPEC.md`` section 4
(six-digit position counts) stays achievable.
"""

from typing import Any, Callable, Mapping, Optional, Tuple

from decimal import Decimal
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    DateField,
    DecimalField,
    F,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import (
    Cast,
    Coalesce,
    ExtractDay,
    ExtractMonth,
    ExtractYear,
)
from django.utils.translation import gettext_lazy as _
from pretix.base.models import (
    Checkin,
    Order,
    OrderPayment,
    OrderPosition,
    OrderRefund,
    Question,
    QuestionAnswer,
)

from pretix_custom_reports.contracts import Base, FieldContext, FieldContractError

__all__ = [
    "ALIAS_ANSWER_PREFIX",
    "ALIAS_CHECKIN_COUNT",
    "ALIAS_CHECKIN_FIRST",
    "ALIAS_CHECKIN_LAST",
    "ALIAS_META_PREFIX",
    "ALIAS_NET_PRICE",
    "ALIAS_PAYMENT_LAST",
    "ALIAS_PAYMENT_STATE",
    "ALIAS_PAYMENT_SUM",
    "ALIAS_PENDING_SUM",
    "ALIAS_POSITION_COUNT",
    "ALIAS_PREFIX",
    "ALIAS_REFUND_SUM",
    "ALIAS_STATUS_LABEL",
    "COUNTED_PAYMENT_STATES",
    "COUNTED_REFUND_STATES",
    "ISO_DATE_REGEX",
    "PAYMENT_STATE_CHOICES",
    "age_at_event_annotation",
    "alias_for",
    "answer_annotation",
    "checkin_count_annotation",
    "checkin_first_annotation",
    "checkin_last_annotation",
    "meta_annotation",
    "net_price_annotation",
    "payment_last_annotation",
    "payment_state_annotation",
    "payment_sum_annotation",
    "pending_sum_annotation",
    "position_count_annotation",
    "refund_sum_annotation",
    "status_label_annotation",
]


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

#: Prefix of every annotation alias this plugin adds.
ALIAS_PREFIX = "pcr_"

ALIAS_PAYMENT_SUM = ALIAS_PREFIX + "payment_sum"
ALIAS_REFUND_SUM = ALIAS_PREFIX + "refund_sum"
ALIAS_PENDING_SUM = ALIAS_PREFIX + "pending_sum"
ALIAS_POSITION_COUNT = ALIAS_PREFIX + "position_count"
ALIAS_CHECKIN_COUNT = ALIAS_PREFIX + "checkin_count"
ALIAS_CHECKIN_FIRST = ALIAS_PREFIX + "checkin_first"
ALIAS_CHECKIN_LAST = ALIAS_PREFIX + "checkin_last"
ALIAS_STATUS_LABEL = ALIAS_PREFIX + "status_label"
ALIAS_PAYMENT_STATE = ALIAS_PREFIX + "payment_state"
ALIAS_PAYMENT_LAST = ALIAS_PREFIX + "payment_last"
ALIAS_NET_PRICE = ALIAS_PREFIX + "net_price"
ALIAS_ANSWER_PREFIX = ALIAS_PREFIX + "answer_"
ALIAS_META_PREFIX = ALIAS_PREFIX + "meta_event_"


def alias_for(prefix: str, name: str) -> str:
    """A safe annotation alias built from *prefix* and a user-controlled *name*.

    ``Question.identifier`` and ``EventMetaProperty.name`` may contain dots and
    dashes, neither of which is valid in a Python identifier, and a dot would
    additionally be read as a lookup separator. Everything outside
    ``[A-Za-z0-9]`` becomes an underscore.

    Two different identifiers can collapse onto the same alias (``a-b`` and
    ``a.b``). That is harmless: both would then also collapse to the same field
    key, and :func:`~pretix_custom_reports.contracts.validate_key` plus the
    ``unique_together`` on ``(event, identifier)`` make the *keys* distinct, so
    the registry rejects the second field before it can shadow the first. The
    de-duplication happens in :mod:`registry.questions`.
    """
    safe = "".join(char if char.isalnum() and char.isascii() else "_" for char in name)
    return f"{prefix}{safe}"


# ---------------------------------------------------------------------------
# State sets -- these must match pretix exactly or our numbers differ from the
# ones the backend shows (docs/pretix-api-notes.md section 6.9).
# ---------------------------------------------------------------------------

#: Payment states that count as "money received"
#: (``Order.pending_sum``, ``pretix/base/models/orders.py:495-508``).
COUNTED_PAYMENT_STATES: Tuple[str, ...] = (
    OrderPayment.PAYMENT_STATE_CONFIRMED,
    OrderPayment.PAYMENT_STATE_REFUNDED,
)

#: Refund states that count as "money given back" (same source).
COUNTED_REFUND_STATES: Tuple[str, ...] = (
    OrderRefund.REFUND_STATE_DONE,
    OrderRefund.REFUND_STATE_TRANSIT,
    OrderRefund.REFUND_STATE_CREATED,
)

#: Values of ``computed.payment_state``. Machine-readable codes with a
#: translated label, so a stored filter value stays portable (ValueScope.GLOBAL)
#: while the editor and the export can show words.
PAYMENT_STATE_CHOICES: Tuple[Tuple[str, Any], ...] = (
    ("unpaid", _("not paid")),
    ("partially_paid", _("partially paid")),
    ("paid", _("paid in full")),
    ("overpaid", _("overpaid")),
)

_ZERO = Decimal("0.00")

#: Guard in front of every ``Cast(answer, DateField)``. Character classes rather
#: than ``\d`` so the pattern means the same thing to PostgreSQL and SQLite.
ISO_DATE_REGEX = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"


def _money() -> DecimalField:
    return DecimalField(max_digits=13, decimal_places=2)


def _check(ctx: FieldContext, base: Base, event_pk: Optional[int] = None) -> None:
    """Guard against a field being used with the wrong base or the wrong event.

    Cheap, but it closes the one way a cached ``ReportField`` could read data
    from another event: a closure built for event A being handed a context for
    event B.
    """
    if ctx.base is not base:
        raise FieldContractError(
            f"This field was built for base {base} but used with {ctx.base}."
        )
    if event_pk is not None:
        if ctx.event is None or ctx.event.pk != event_pk:
            raise FieldContractError(
                "This field was built for a different event; registry entries are "
                "never valid across events."
            )


def _order_ref(base: Base) -> OuterRef:
    """``OuterRef`` pointing at the order primary key of the current row."""
    return OuterRef("pk") if base is Base.ORDER else OuterRef("order_id")


def _order_path(base: Base, name: str) -> str:
    """ORM path to a field of ``Order``, relative to the base model."""
    return name if base is Base.ORDER else f"order__{name}"


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def _payment_sum_expression(base: Base) -> Subquery:
    queryset = (
        OrderPayment.objects.filter(
            state__in=COUNTED_PAYMENT_STATES,
            order=_order_ref(base),
        )
        .order_by()
        .values("order")
        .annotate(total=Sum("amount"))
        .values("total")
    )
    return Subquery(queryset, output_field=_money())


def _refund_sum_expression(base: Base) -> Subquery:
    queryset = (
        OrderRefund.objects.filter(
            state__in=COUNTED_REFUND_STATES,
            order=_order_ref(base),
        )
        .order_by()
        .values("order")
        .annotate(total=Sum("amount"))
        .values("total")
    )
    return Subquery(queryset, output_field=_money())


def _pending_sum_expression(base: Base) -> Any:
    """Outstanding amount, mirroring ``Order.pending_sum``.

    A canceled order owes nothing, which is why ``total`` goes through a
    ``Case`` instead of being used directly
    (``pretix/base/models/orders.py:495-508``).
    """
    total = Case(
        When(
            Q(**{_order_path(base, "status"): Order.STATUS_CANCELED}),
            then=Value(_ZERO, output_field=_money()),
        ),
        default=F(_order_path(base, "total")),
        output_field=_money(),
    )
    return (
        total
        - Coalesce(_payment_sum_expression(base), Value(_ZERO, output_field=_money()))
        + Coalesce(_refund_sum_expression(base), Value(_ZERO, output_field=_money()))
    )


def payment_sum_annotation(base: Base) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Sum of all payments in a state pretix counts as received."""

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        return {
            ALIAS_PAYMENT_SUM: Coalesce(
                _payment_sum_expression(base), Value(_ZERO, output_field=_money())
            )
        }

    return build


def refund_sum_annotation(base: Base) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Sum of all refunds in a state pretix counts as paid back."""

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        return {
            ALIAS_REFUND_SUM: Coalesce(
                _refund_sum_expression(base), Value(_ZERO, output_field=_money())
            )
        }

    return build


def pending_sum_annotation(base: Base) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Outstanding amount of the order."""

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        return {ALIAS_PENDING_SUM: _pending_sum_expression(base)}

    return build


def payment_state_annotation(base: Base) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Payment status in words: unpaid / partially paid / paid / overpaid.

    Emits three aliases because the ``Case`` compares against the other two.
    Both of them are the very same expressions ``order.pending_sum`` and
    ``payment.sum_confirmed`` use, so a report containing all three columns
    still annotates each value once.
    """

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        return {
            ALIAS_PAYMENT_SUM: Coalesce(
                _payment_sum_expression(base), Value(_ZERO, output_field=_money())
            ),
            ALIAS_PENDING_SUM: _pending_sum_expression(base),
            ALIAS_PAYMENT_STATE: Case(
                When(
                    Q(**{f"{ALIAS_PENDING_SUM}__lt": _ZERO}),
                    then=Value("overpaid"),
                ),
                When(
                    Q(**{ALIAS_PENDING_SUM: _ZERO}),
                    then=Value("paid"),
                ),
                When(
                    Q(**{f"{ALIAS_PAYMENT_SUM}__gt": _ZERO}),
                    then=Value("partially_paid"),
                ),
                default=Value("unpaid"),
                output_field=CharField(),
            ),
        }

    return build


def payment_last_annotation(base: Base) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Date of the most recent payment pretix counts as received."""

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        queryset = (
            OrderPayment.objects.filter(
                state__in=COUNTED_PAYMENT_STATES,
                order=_order_ref(base),
            )
            .order_by()
            .values("order")
            .annotate(moment=Max("payment_date"))
            .values("moment")
        )
        return {ALIAS_PAYMENT_LAST: Subquery(queryset)}

    return build


def net_price_annotation(base: Base) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Net price of a position, mirroring ``AbstractPosition.net_price``.

    ``price - tax_value``. The ``*_includes_rounding_correction`` columns are
    deliberately ignored: they express the value *before* rounding and would
    make a column disagree with the order total
    (docs/pretix-api-notes.md section 6.2, pitfall 8).
    """

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        return {ALIAS_NET_PRICE: F("price") - F("tax_value")}

    return build


def status_label_annotation(base: Base) -> Callable[[FieldContext], Mapping[str, Any]]:
    """``Order.status`` as the word pretix shows, not the single letter.

    ``STATUS_REFUNDED`` is deliberately absent: it is the same ``"c"`` as
    ``STATUS_CANCELED`` and marked deprecated
    (docs/pretix-api-notes.md section 6.1, pitfall 5).
    """

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        status_path = _order_path(base, "status")
        return {
            ALIAS_STATUS_LABEL: Case(
                *[
                    When(Q(**{status_path: code}), then=Value(str(label)))
                    for code, label in Order.STATUS_CHOICE
                ],
                default=Value(""),
                output_field=CharField(),
            )
        }

    return build


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def position_count_annotation(
    base: Base,
) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Number of non-canceled positions of the order.

    ``OrderPosition.objects`` is the ``ActivePositionManager``, so canceled
    positions are already out -- the same set ``Order.count_positions`` counts
    (docs/pretix-api-notes.md section 6.2).
    """

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        queryset = (
            OrderPosition.objects.filter(order=_order_ref(base))
            .order_by()
            .values("order")
            .annotate(number=Count("pk"))
            .values("number")
        )
        return {
            ALIAS_POSITION_COUNT: Coalesce(
                Subquery(queryset, output_field=IntegerField()),
                Value(0, output_field=IntegerField()),
            )
        }

    return build


def _checkin_queryset(base: Base, event: Any) -> Any:
    """Successful entry check-ins of the current row, hard-scoped to *event*.

    Three filters that are all easy to forget (docs/pretix-api-notes.md
    section 6.10):

    * ``Checkin.objects`` is the ``SuccessfulCheckinManager`` -- failed scans
      are already excluded, ``Checkin.all`` would include them,
    * ``type=TYPE_ENTRY`` -- an exit scan is a check-in row too,
    * ``list__event=event`` -- a check-in belongs to a list, and the list to an
      event; without this the subquery would not be event-scoped at all.
    """
    if base is Base.ORDER:
        row_filter = {"position__order": OuterRef("pk")}
        group_by = "position__order"
    else:
        row_filter = {"position": OuterRef("pk")}
        group_by = "position"
    return (
        Checkin.objects.filter(
            type=Checkin.TYPE_ENTRY,
            list__event=event,
            **row_filter,
        )
        .order_by()
        .values(group_by)
    )


def checkin_count_annotation(base: Base) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Number of successful entry check-ins."""

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        queryset = (
            _checkin_queryset(base, ctx.event)
            .annotate(number=Count("pk"))
            .values("number")
        )
        return {
            ALIAS_CHECKIN_COUNT: Coalesce(
                Subquery(queryset, output_field=IntegerField()),
                Value(0, output_field=IntegerField()),
            )
        }

    return build


def checkin_first_annotation(base: Base) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Timestamp of the earliest successful entry check-in."""

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        queryset = (
            _checkin_queryset(base, ctx.event)
            .annotate(moment=Min("datetime"))
            .values("moment")
        )
        return {ALIAS_CHECKIN_FIRST: Subquery(queryset)}

    return build


def checkin_last_annotation(base: Base) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Timestamp of the latest successful entry check-in."""

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base)
        queryset = (
            _checkin_queryset(base, ctx.event)
            .annotate(moment=Max("datetime"))
            .values("moment")
        )
        return {ALIAS_CHECKIN_LAST: Subquery(queryset)}

    return build


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


def _answer_subquery(question_pk: int, event_pk: int) -> Any:
    """The one answer this position gave to this question.

    ``unique_together [['orderposition', 'question']]`` guarantees there is at
    most one (docs/pretix-api-notes.md section 6.4, pitfall 2), so the
    ``Subquery`` needs no slicing for correctness -- ``[:1]`` is there to make
    that explicit to the database planner.

    ``question__event_id`` is redundant next to ``question=question_pk`` and
    present on purpose: it makes the event restriction visible in the SQL even
    if the primary key ever came from somewhere less trustworthy than
    ``event.questions``.
    """
    return QuestionAnswer.objects.filter(
        orderposition=OuterRef("pk"),
        question=question_pk,
        question__event_id=event_pk,
    ).values("answer")[:1]


def answer_annotation(
    question_pk: int,
    event_pk: int,
    question_type: str,
    alias: str,
) -> Callable[[FieldContext], Mapping[str, Any]]:
    """The answer to one question, for base ``orderposition``.

    ``QuestionAnswer.answer`` is a ``TextField`` for every question type. Only
    booleans are normalised, because pretix stores them as the literal strings
    ``"True"`` / ``"False"`` (``QuestionAnswer.to_string``,
    ``pretix/base/models/orders.py:1402-1449``) and a stored filter value of
    ``true`` would otherwise never match. Everything else stays text; see
    :mod:`registry.questions` for what that means per question type.
    """

    def build_boolean(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, Base.ORDERPOSITION, event_pk=event_pk)
        raw_alias = f"{alias}_raw"
        return {
            raw_alias: Subquery(
                _answer_subquery(question_pk, event_pk), output_field=CharField()
            ),
            alias: Case(
                When(Q(**{raw_alias: "True"}), then=Value(True)),
                When(Q(**{raw_alias: "False"}), then=Value(False)),
                default=Value(None, output_field=BooleanField(null=True)),
                output_field=BooleanField(null=True),
            ),
        }

    def build_plain(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, Base.ORDERPOSITION, event_pk=event_pk)
        return {
            alias: Subquery(
                _answer_subquery(question_pk, event_pk), output_field=CharField()
            )
        }

    if question_type == Question.TYPE_BOOLEAN:
        return build_boolean
    return build_plain


def age_at_event_annotation(
    question_pk: int,
    event_pk: int,
    alias: str,
    reference_date: Any,
) -> Callable[[FieldContext], Mapping[str, Any]]:
    """Full years between a date answer and *reference_date*.

    *reference_date* is a plain ``datetime.date`` resolved when the field is
    built, from ``Event.date_from`` in the event's own timezone. It is a constant
    in the SQL, which is what makes the calculation a handful of cheap integer
    operations instead of per-row Python.

    Three aliases: the answer cast to a date, a ``MMDD`` sort key, and the age
    itself. The middle one exists because "has the birthday already happened
    this year" is a comparison of month and day, and expressing that inside a
    single ``Case`` without an intermediate alias is unreadable.

    The cast is the risky part: on PostgreSQL ``CAST(text AS date)`` raises for
    the whole query if a single row is malformed, and ``QuestionAnswer.answer``
    is a plain ``TextField``. The subquery therefore only lets values through
    that look like an ISO date (:data:`ISO_DATE_REGEX`), which also takes care of
    empty answers. A syntactically well-formed but impossible date
    (``2026-02-30``) would still fail; pretix validates date answers in the order
    form, the backend and the API, so that requires somebody writing raw rows.
    The failure mode is a database error, never a wrong number -- see
    docs/adr/0002-registry.md.
    """
    alias_date = f"{alias}_date"
    alias_key = f"{alias}_key"

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, Base.ORDERPOSITION, event_pk=event_pk)
        answers = (
            QuestionAnswer.objects.filter(
                orderposition=OuterRef("pk"),
                question=question_pk,
                question__event_id=event_pk,
            )
            .filter(answer__regex=ISO_DATE_REGEX)
            .values("answer")[:1]
        )
        reference_key = reference_date.month * 100 + reference_date.day
        return {
            alias_date: Cast(
                Subquery(answers, output_field=CharField()),
                output_field=DateField(),
            ),
            alias_key: ExtractMonth(alias_date) * Value(100) + ExtractDay(alias_date),
            alias: Case(
                When(
                    Q(**{f"{alias_date}__isnull": True}),
                    then=Value(None, output_field=IntegerField(null=True)),
                ),
                When(
                    Q(**{f"{alias_key}__gt": reference_key}),
                    then=Value(reference_date.year)
                    - ExtractYear(alias_date)
                    - Value(1),
                ),
                default=Value(reference_date.year) - ExtractYear(alias_date),
                output_field=IntegerField(),
            ),
        }

    return build


# ---------------------------------------------------------------------------
# Meta properties
# ---------------------------------------------------------------------------


def meta_annotation(
    property_name: str,
    event_pk: int,
    base: Base,
    alias: str,
) -> Callable[[FieldContext], Mapping[str, Any]]:
    """An event meta property as a constant.

    ``Event.meta_data`` is a Python property that layers the organizer-wide
    default under the event's own value; filtering through
    ``meta_values__property__name`` would silently miss every event that relies
    on the default (docs/pretix-api-notes.md section 6.7). Because a compiled
    report belongs to exactly one event (ADR 0001 section 9), the value is a
    constant for the whole query -- so display and filter cannot disagree, and
    the cost is one small query per compile instead of a join per row.

    A multi-event export compiles once per event and therefore gets each event's
    own value.
    """

    def build(ctx: FieldContext) -> Mapping[str, Any]:
        _check(ctx, base, event_pk=event_pk)
        value = ctx.event.meta_data.get(property_name)
        return {
            alias: Value(
                None if value in (None, "") else str(value),
                output_field=CharField(),
            )
        }

    return build
