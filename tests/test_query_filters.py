"""Filter compilation: one operator and datatype at a time, then combinations.

Owner: query-dev (ORCHESTRIERUNG.md section 5).

SPEC.md section 4 asks for "filter compilation per operator" as a minimum test.
This module walks the whole operator table rather than a sample, because the table
is closed and a missing entry is exactly the kind of gap that only shows up when a
user picks the one operator nobody tried.

Assertions are on the resulting ``Q`` object, not on SQL strings: the ``Q`` is what
the compiler is responsible for, and comparing generated SQL would tie the tests to
a database backend. The end-to-end behaviour ("does this filter actually select the
right rows?") is in ``tests/test_query_compile.py``, against a real database.
"""

import datetime
import pytest
from decimal import Decimal
from django.db.models import Q
from django_scopes import scopes_disabled
from zoneinfo import ZoneInfo

from pretix_custom_reports.contracts.definition import (
    BoolOp,
    Column,
    FilterCondition,
    FilterGroup,
    ReportDefinition,
    ReportOptions,
)
from pretix_custom_reports.contracts.errors import CompilationError
from pretix_custom_reports.contracts.fields import (
    OPERATOR_SPECS,
    Base,
    DataType,
    Operator,
    ReportField,
    ValueKind,
)
from pretix_custom_reports.query import relations
from pretix_custom_reports.query.filters import FilterContext, compile_condition
from pretix_custom_reports.query.values import coerce_value, emptiness_q

from .test_query_support import FakeEvent, ReferenceRegistry, load_fixture

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime.datetime(2026, 7, 30, 12, 0, tzinfo=BERLIN)


@pytest.fixture
def event():
    return FakeEvent()


def _ctx(base=Base.ORDER, event=None, include_canceled=False):
    return FilterContext(
        base=base,
        base_model=relations.base_model_for(base),
        event=event or FakeEvent(),
        include_canceled=include_canceled,
        now=NOW,
    )


def _q(key, operator, value=None, base=Base.ORDER, event=None, include_canceled=False):
    registry = ReferenceRegistry()
    fields = dict(registry.get_fields(event or FakeEvent(), base))
    condition = FilterCondition(field=key, operator=operator, value=value)
    with scopes_disabled():
        return compile_condition(condition, fields, _ctx(base, event, include_canceled))


# ---------------------------------------------------------------------------
# One operator at a time, on a single-valued path
# ---------------------------------------------------------------------------


def test_exact_on_a_string():
    assert _q("order.code", Operator.EXACT, "ABC12") == Q(code__exact="ABC12")


def test_contains_is_case_insensitive():
    """ "contains" is semantic; the compiler decides it means ``icontains``."""
    assert _q("order.email", Operator.CONTAINS, "example") == Q(
        email__icontains="example"
    )


def test_starts_and_ends_with_are_case_insensitive():
    assert _q("order.code", Operator.STARTS_WITH, "AB") == Q(code__istartswith="AB")
    assert _q("order.code", Operator.ENDS_WITH, "12") == Q(code__iendswith="12")


def test_in_takes_a_list():
    assert _q("order.status", Operator.IN, ["p", "n"]) == Q(status__in=["p", "n"])


def test_not_in_is_the_negation_of_in():
    assert _q("order.status", Operator.NOT_IN, ["p"]) == ~Q(status__in=["p"])


def test_not_exact_is_the_negation_of_exact():
    assert _q("order.code", Operator.NOT_EXACT, "ABC12") == ~Q(code__exact="ABC12")


def test_not_contains_is_the_negation_of_contains():
    assert _q("order.email", Operator.NOT_CONTAINS, "spam") == ~Q(
        email__icontains="spam"
    )


@pytest.mark.parametrize(
    "operator,suffix",
    [
        (Operator.LT, "lt"),
        (Operator.LTE, "lte"),
        (Operator.GT, "gt"),
        (Operator.GTE, "gte"),
    ],
)
def test_ordered_comparisons(operator, suffix):
    assert _q("order.total", operator, "10.00") == Q(
        **{f"total__{suffix}": Decimal("10.00")}
    )


def test_between_becomes_two_inclusive_bounds():
    assert _q("order.total", Operator.BETWEEN, ["5", "10"]) == Q(
        total__gte=Decimal("5"), total__lte=Decimal("10")
    )


def test_is_empty_on_text_covers_null_and_empty_string():
    """pretix mixes both: ``email`` is nullable, ``comment`` is ``blank=True``."""
    assert _q("order.email", Operator.IS_EMPTY) == (
        Q(email__isnull=True) | Q(email__exact="")
    )


def test_is_empty_on_a_datetime_is_null_only():
    """``__exact=''`` on a date column is a database error, not a wider match."""
    assert _q("order.cancellation_date", Operator.IS_EMPTY) == Q(
        cancellation_date__isnull=True
    )


def test_is_not_empty_is_the_negation():
    assert _q("order.email", Operator.IS_NOT_EMPTY) == ~(
        Q(email__isnull=True) | Q(email__exact="")
    )


# ---------------------------------------------------------------------------
# Value coercion per datatype
# ---------------------------------------------------------------------------


def test_money_strings_become_decimals():
    """JSON has no decimal type, so a money filter arrives as a string."""
    q = _q("order.total", Operator.GTE, "10.00")
    assert q.children[0][1] == Decimal("10.00")
    assert isinstance(q.children[0][1], Decimal)


def test_money_floats_go_through_str_to_avoid_binary_noise():
    q = _q("order.total", Operator.GTE, 0.1)
    assert q.children[0][1] == Decimal("0.1")


def test_booleans_are_real_booleans():
    q = _q("order.testmode", Operator.EXACT, True)
    assert q.children[0][1] is True


@pytest.mark.parametrize("value,expected", [("true", True), ("no", False), (1, True)])
def test_boolean_strings_and_flags_are_accepted(value, expected):
    field = ReferenceRegistry().resolve("order.testmode", FakeEvent(), Base.ORDER)
    assert coerce_value(field, value) is expected


def test_datetime_literal_without_offset_is_read_in_the_event_timezone():
    """A stored literal means the organizer's local time, not the server's."""
    q = _q("order.datetime", Operator.GTE, "2026-07-30T08:00:00")
    value = q.children[0][1]
    assert value == datetime.datetime(2026, 7, 30, 8, 0, tzinfo=BERLIN)
    assert value.utcoffset() == datetime.timedelta(hours=2)


def test_datetime_literal_with_offset_keeps_it():
    q = _q("order.datetime", Operator.GTE, "2026-07-30T08:00:00+00:00")
    assert q.children[0][1].utcoffset() == datetime.timedelta(0)


def test_bare_date_in_a_datetime_filter_is_accepted():
    q = _q("order.datetime", Operator.GTE, "2026-07-30")
    assert q.children[0][1] == datetime.datetime(2026, 7, 30, 0, 0, tzinfo=BERLIN)


def test_unparseable_money_value_is_a_compilation_error():
    with pytest.raises(CompilationError) as excinfo:
        _q("order.total", Operator.GTE, "ten euros")
    assert "order.total" in str(excinfo.value)


def test_unparseable_date_value_is_a_compilation_error():
    with pytest.raises(CompilationError):
        _q("order.datetime", Operator.GTE, "not-a-date")


def test_boolean_against_a_text_field_is_rejected():
    """``True`` would silently become the string "True" and match nothing."""
    with pytest.raises(CompilationError):
        _q("order.code", Operator.EXACT, True)


def test_emptiness_q_is_datatype_driven():
    assert emptiness_q("x", DataType.STRING) == (Q(x__isnull=True) | Q(x__exact=""))
    assert emptiness_q("x", DataType.MONEY) == Q(x__isnull=True)


# ---------------------------------------------------------------------------
# Operator permission comes from the field, never from the definition
# ---------------------------------------------------------------------------


def test_operator_not_offered_by_the_field_is_rejected():
    """``invalid/field_type_conflict.json``: ``contains`` on a money field."""
    with pytest.raises(CompilationError) as excinfo:
        _q("order.total", Operator.CONTAINS, "abc")
    assert "not allowed for" in str(excinfo.value)


def test_a_narrowed_operator_set_is_honoured():
    """A registry may drop an operator per field; the compiler must follow."""
    narrowed = ReportField(
        key="order.code",
        label="Order code",
        group="order",
        datatype=DataType.STRING,
        bases=(Base.ORDER,),
        orm_path="code",
        filter_operators=(Operator.EXACT,),
    )
    registry = ReferenceRegistry(overrides={"order.code": narrowed})
    fields = dict(registry.get_fields(FakeEvent(), Base.ORDER))
    with pytest.raises(CompilationError):
        compile_condition(
            FilterCondition(field="order.code", operator=Operator.CONTAINS, value="A"),
            fields,
            _ctx(),
        )


def test_a_python_only_field_cannot_be_filtered():
    computed = ReportField(
        key="order.code",
        label="Computed",
        group="order",
        datatype=DataType.STRING,
        bases=(Base.ORDER,),
        value_getter=lambda row: "x",
    )
    registry = ReferenceRegistry(overrides={"order.code": computed})
    fields = dict(registry.get_fields(FakeEvent(), Base.ORDER))
    with pytest.raises(CompilationError) as excinfo:
        compile_condition(
            FilterCondition(field="order.code", operator=Operator.EXACT, value="A"),
            fields,
            _ctx(),
        )
    assert "cannot be filtered" in str(excinfo.value)


def test_every_operator_the_registry_offers_can_be_compiled():
    """No operator may be advertised and then fail to compile.

    Walks every field of both bases against every operator it declares. A gap here
    means the editor offers a filter that explodes when the report runs.
    """
    registry = ReferenceRegistry()
    event = FakeEvent()
    samples = {
        ValueKind.NONE: None,
        ValueKind.SCALAR: None,  # filled per datatype below
        ValueKind.LIST: None,
        ValueKind.RANGE: None,
        ValueKind.DAY_COUNT: 7,
    }
    scalar_for = {
        DataType.INTEGER: 1,
        DataType.DECIMAL: "1.5",
        DataType.MONEY: "10.00",
        DataType.BOOLEAN: True,
        DataType.DATE: "2026-07-30",
        DataType.DATETIME: "2026-07-30T10:00:00",
        DataType.TIME: "10:00",
    }
    checked = 0
    with scopes_disabled():
        for base in (Base.ORDER, Base.ORDERPOSITION):
            fields = dict(registry.get_fields(event, base))
            ctx = _ctx(base, event)
            for key, field in fields.items():
                for operator in field.filter_operators:
                    kind = OPERATOR_SPECS[operator].value_kind
                    scalar = scalar_for.get(field.datatype, "x")
                    if kind is ValueKind.SCALAR:
                        value = scalar
                    elif kind is ValueKind.LIST:
                        value = [scalar]
                    elif kind is ValueKind.RANGE:
                        value = [scalar, scalar]
                    else:
                        value = samples[kind]
                    result = compile_condition(
                        FilterCondition(field=key, operator=operator, value=value),
                        fields,
                        ctx,
                        path=f"{base}:{key}:{operator}",
                    )
                    assert result is not None
                    checked += 1
    assert checked > 300, checked


# ---------------------------------------------------------------------------
# Relative date filters
# ---------------------------------------------------------------------------


def test_relative_last_days_becomes_a_half_open_window():
    q = _q("order.datetime", Operator.RELATIVE_LAST_DAYS, 30)
    assert q == Q(datetime__gte=datetime.datetime(2026, 7, 1, tzinfo=BERLIN)) & Q(
        datetime__lt=datetime.datetime(2026, 7, 31, tzinfo=BERLIN)
    )


def test_relative_since_event_start_has_no_upper_bound():
    event = FakeEvent(date_from=datetime.datetime(2026, 6, 1, 10, 0, tzinfo=BERLIN))
    q = _q("order.datetime", Operator.RELATIVE_SINCE_EVENT_START, event=event)
    assert q == Q(datetime__gte=datetime.datetime(2026, 6, 1, 10, 0, tzinfo=BERLIN))


def test_relative_on_a_date_typed_answer_uses_dates():
    """``answer.arrival-date`` is a DATE field on base ``orderposition``."""
    q = _q(
        "answer.arrival-date",
        Operator.RELATIVE_TODAY,
        base=Base.ORDERPOSITION,
    )
    assert q == Q(pcr_answer_arrival_date__gte=datetime.date(2026, 7, 30)) & Q(
        pcr_answer_arrival_date__lte=datetime.date(2026, 7, 30)
    )


def test_relative_operator_on_a_non_date_field_is_rejected():
    loose = ReportField(
        key="order.code",
        label="Order code",
        group="order",
        datatype=DataType.STRING,
        bases=(Base.ORDER,),
        orm_path="code",
        filter_operators=(Operator.RELATIVE_TODAY,),  # registry bug
    )
    registry = ReferenceRegistry(overrides={"order.code": loose})
    fields = dict(registry.get_fields(FakeEvent(), Base.ORDER))
    with pytest.raises(CompilationError) as excinfo:
        compile_condition(
            FilterCondition(field="order.code", operator=Operator.RELATIVE_TODAY),
            fields,
            _ctx(),
        )
    assert "date or datetime" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Multi-valued paths on base ``order``: EXISTS, not a join
# ---------------------------------------------------------------------------


def _sql(q, base=Base.ORDER):
    model = relations.base_model_for(base)
    with scopes_disabled():
        return str(model.objects.filter(q).query)


def _is_exists(q):
    """True if *q* wraps a single ``Exists`` expression."""
    from django.db.models import Exists

    return len(q.children) == 1 and isinstance(q.children[0], Exists)


def test_a_position_filter_on_base_order_is_an_exists():
    """A join would return the order once per matching position."""
    q = _q("item.name", Operator.CONTAINS, "Ticket")
    assert _is_exists(q)
    assert q.negated is False
    assert "EXISTS" in _sql(q).upper()


def test_a_negated_position_filter_is_a_not_exists():
    """ "no position is a Ticket", not "some position is not a Ticket".

    See the module docstring of query/filters.py: the other reading makes
    ``not_in`` useless on any order with more than one product.
    """
    q = _q("item.name", Operator.NOT_CONTAINS, "Ticket")
    assert _is_exists(q)
    assert q.negated is True
    # Django writes this as ``NOT (EXISTS(...))``; the exact spelling is its
    # business, the negation is ours.
    sql = _sql(q).upper()
    assert "NOT (EXISTS" in sql
    # The positive condition is what sits inside the EXISTS.
    assert "LIKE %TICKET%" in sql


def test_is_not_empty_on_a_position_field_stays_positive():
    """The documented exception: a presence test reads as "at least one"."""
    q = _q("position.attendee_email", Operator.IS_NOT_EMPTY)
    assert _is_exists(q)
    assert q.negated is False, "presence tests are not inverted"


def test_an_answer_filter_on_base_order_crosses_two_relations():
    """Order -> OrderPosition -> QuestionAnswer, correlated in one EXISTS."""
    sql = _sql(_q("answer.tshirt-size", Operator.IN, ["L", "XL"]))
    assert "EXISTS" in sql.upper()
    assert "questionanswer" in sql.lower()
    # The relation_filter must be in there, or the filter would match any
    # question's answer.
    assert "identifier" in sql.lower()


def test_the_exists_subquery_excludes_canceled_positions_by_default():
    sql = _sql(_q("item.name", Operator.CONTAINS, "Ticket"))
    assert "canceled" in sql.lower()


def test_include_canceled_positions_drops_that_condition():
    sql = _sql(_q("item.name", Operator.CONTAINS, "T", include_canceled=True))
    assert "canceled" not in sql.lower()


def test_a_position_filter_on_base_orderposition_is_a_plain_lookup():
    """On its own base the field is single-valued -- no subquery needed."""
    q = _q("item.name", Operator.CONTAINS, "Ticket", base=Base.ORDERPOSITION)
    assert q == Q(item__name__icontains="Ticket")


# ---------------------------------------------------------------------------
# Groups: exactly one level of AND/OR
# ---------------------------------------------------------------------------


def _compile_definition_filters(definition, base=Base.ORDER, event=None):
    from pretix_custom_reports.query.filters import compile_filters

    registry = ReferenceRegistry()
    event = event or FakeEvent()
    fields = dict(registry.get_fields(event, base))
    with scopes_disabled():
        return compile_filters(definition.filters, fields, _ctx(base, event))


def test_root_group_joins_children_with_its_operator():
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="order.code"),),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="order.status", operator=Operator.EXACT, value="p"
                ),
                FilterCondition(
                    field="order.email", operator=Operator.CONTAINS, value="a"
                ),
            ),
        ),
    )
    assert _compile_definition_filters(definition) == (
        Q(status__exact="p") & Q(email__icontains="a")
    )


def test_or_inside_a_group_and_and_across_groups():
    """The exact nesting SPEC.md F6 allows, and no more."""
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="order.code"),),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="order.status", operator=Operator.IN, value=["p"]
                ),
                FilterGroup(
                    op=BoolOp.OR,
                    children=(
                        FilterCondition(
                            field="order.email",
                            operator=Operator.CONTAINS,
                            value="a",
                        ),
                        FilterCondition(
                            field="order.email",
                            operator=Operator.CONTAINS,
                            value="b",
                        ),
                    ),
                ),
            ),
        ),
    )
    expected = Q(status__in=["p"]) & (Q(email__icontains="a") | Q(email__icontains="b"))
    assert _compile_definition_filters(definition) == expected


def test_an_or_root_with_and_free_children():
    definition = load_fixture("options_full.json")
    q = _compile_definition_filters(definition, base=Base.ORDERPOSITION)
    assert q.connector == "OR"
    assert len(q.children) == 2


def test_no_filters_compiles_to_none():
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="order.code"),),
        options=ReportOptions(include_testmode_orders=True),
    )
    assert _compile_definition_filters(definition) is None


def test_the_and_or_fixture_produces_the_expected_shape():
    """Two OR groups plus three bare conditions, ANDed together.

    ``negated`` has to be part of the test: ``is_not_empty`` also produces an
    OR-connected node (null or empty string) and would otherwise be counted as a
    third group.
    """
    definition = load_fixture("filters_and_or.json")
    q = _compile_definition_filters(definition, base=Base.ORDERPOSITION)
    assert q.connector == "AND"
    or_groups = [
        child
        for child in q.children
        if isinstance(child, Q) and child.connector == "OR" and not child.negated
    ]
    assert len(or_groups) == 2
    assert len(q.children) == 5


def test_the_relative_date_fixture_produces_bounded_windows():
    definition = load_fixture("relative_date_filters.json")
    event = FakeEvent(date_from=datetime.datetime(2026, 6, 1, tzinfo=BERLIN))
    registry = ReferenceRegistry()
    fields = dict(registry.get_fields(event, Base.ORDER))
    from pretix_custom_reports.query.filters import compile_filters

    with scopes_disabled():
        q = compile_filters(definition.filters, fields, _ctx(Base.ORDER, event))
    text = str(q)
    # Every bound is a concrete value, never a symbolic "today".
    assert "2026" in text
    assert "today" not in text.lower()
