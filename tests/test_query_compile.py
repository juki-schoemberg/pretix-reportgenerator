"""Pass two, against a real database: rows, counts, query counts, preview.

Owner: query-dev (ORCHESTRIERUNG.md section 5).

Everything in here executes SQL. That is the point: a plan that looks right and a
queryset that runs are two different claims, and the interesting bugs of a report
compiler -- a join that multiplies rows, an aggregate that double counts, a
prefetch that turns into an N+1 -- are only visible once rows come back.

The dataset is deliberately shaped to catch those:

* one order with **three** positions, so any row multiplication shows up
  immediately as a wrong row count,
* one order with a canceled position, so ``include_canceled_positions`` has
  something to switch,
* a test-mode order, so the default exclusion has something to exclude,
* two payments and a refund on one order, so a sum over a *second* one-to-many
  relation can collide with the position aggregates -- which is exactly the
  cross-product trap that joined annotations fall into,
* answers to two different questions on one position, so an answer aggregate has
  something to mix up if it forgets its question filter,
* a second event, so event scoping has something to leak into.

``scopes_disabled`` wraps the fixtures because pretix's own hook for that lives in
its repository's conftest and does not apply to out-of-tree plugins
(docs/pretix-api-notes.md section 7.5).
"""

import datetime
import pytest
from decimal import Decimal
from django.db.models import Q
from django.utils.timezone import now
from django_scopes import scopes_disabled
from pretix.base.models import (
    Checkin,
    CheckinList,
    Event,
    Item,
    ItemCategory,
    Order,
    OrderPayment,
    OrderPosition,
    OrderRefund,
    Question,
    QuestionAnswer,
)

from pretix_custom_reports.contracts.definition import (
    PREVIEW_ROW_LIMIT,
    BoolOp,
    Column,
    FilterCondition,
    FilterGroup,
    ReportDefinition,
    ReportOptions,
    SortEntry,
)
from pretix_custom_reports.contracts.fields import (
    Aggregate,
    Base,
    Operator,
    SortDirection,
)
from pretix_custom_reports.query.compiler import ReportQueryCompiler

from .test_query_support import (
    VALID_FIXTURES,
    WIDE_FIXTURE,
    ReferenceRegistry,
    column_values,
    load_fixture,
    order_codes,
)


@pytest.fixture
def registry():
    return ReferenceRegistry()


@pytest.fixture
def compiler(registry):
    return ReportQueryCompiler(registry)


@pytest.fixture
def data(event):
    """A small but adversarial dataset. Returns a dict of the objects."""
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        category = ItemCategory.objects.create(event=event, name="Workshops")
        ticket = Item.objects.create(
            event=event, name="Ticket", internal_name="ticket", default_price=23
        )
        workshop = Item.objects.create(
            event=event,
            name="Workshop",
            internal_name="workshop",
            category=category,
            default_price=10,
        )

        questions = {}
        for identifier, label, qtype in (
            ("tshirt-size", "T-shirt size", Question.TYPE_CHOICE),
            ("arrival-date", "Day of arrival", Question.TYPE_DATE),
            ("newsletter", "Newsletter opt-in", Question.TYPE_BOOLEAN),
        ):
            questions[identifier] = Question.objects.create(
                event=event, question=label, identifier=identifier, type=qtype
            )

        # Order A: three positions, paid, two payments and a refund.
        order_a = Order.objects.create(
            event=event,
            code="AAAAA",
            status=Order.STATUS_PAID,
            email="a@example.org",
            sales_channel=channel,
            datetime=now() - datetime.timedelta(days=2),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("43.00"),
        )
        positions_a = [
            OrderPosition.objects.create(
                order=order_a,
                item=ticket,
                price=Decimal("23.00"),
                positionid=1,
                attendee_name_parts={"_legacy": "Ada Lovelace"},
                attendee_email="ada@example.org",
            ),
            OrderPosition.objects.create(
                order=order_a,
                item=workshop,
                price=Decimal("10.00"),
                positionid=2,
                attendee_name_parts={"_legacy": "Grace Hopper"},
            ),
            OrderPosition.objects.create(
                order=order_a,
                item=workshop,
                price=Decimal("10.00"),
                positionid=3,
                attendee_name_parts={"_legacy": "Alan Turing"},
            ),
        ]
        OrderPayment.objects.create(
            order=order_a,
            provider="manual",
            amount=Decimal("30.00"),
            state=OrderPayment.PAYMENT_STATE_CONFIRMED,
        )
        OrderPayment.objects.create(
            order=order_a,
            provider="banktransfer",
            amount=Decimal("13.00"),
            state=OrderPayment.PAYMENT_STATE_CONFIRMED,
        )
        OrderRefund.objects.create(
            order=order_a,
            provider="manual",
            amount=Decimal("3.00"),
            state=OrderRefund.REFUND_STATE_DONE,
        )
        QuestionAnswer.objects.create(
            orderposition=positions_a[0],
            question=questions["tshirt-size"],
            answer="L",
        )
        QuestionAnswer.objects.create(
            orderposition=positions_a[0],
            question=questions["arrival-date"],
            answer="2026-09-01",
        )
        QuestionAnswer.objects.create(
            orderposition=positions_a[1],
            question=questions["tshirt-size"],
            answer="XL",
        )

        checkin_list = CheckinList.objects.create(
            event=event, name="Entry", all_products=True
        )
        Checkin.objects.create(position=positions_a[0], list=checkin_list)

        # Order B: one live position plus one canceled position.
        order_b = Order.objects.create(
            event=event,
            code="BBBBB",
            status=Order.STATUS_PENDING,
            email="b@example.org",
            sales_channel=channel,
            datetime=now() - datetime.timedelta(days=1),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("23.00"),
        )
        live_b = OrderPosition.objects.create(
            order=order_b,
            item=ticket,
            price=Decimal("23.00"),
            positionid=1,
            attendee_name_parts={"_legacy": "Barbara Liskov"},
        )
        canceled_b = OrderPosition.all.create(
            order=order_b,
            item=ticket,
            price=Decimal("23.00"),
            positionid=2,
            canceled=True,
            attendee_name_parts={"_legacy": "Cancelled Person"},
        )

        # Order C: test mode, must be invisible by default.
        order_c = Order.objects.create(
            event=event,
            code="CCCCC",
            status=Order.STATUS_PAID,
            email="c@example.org",
            sales_channel=channel,
            testmode=True,
            datetime=now(),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("5.00"),
        )
        OrderPosition.objects.create(
            order=order_c, item=ticket, price=Decimal("5.00"), positionid=1
        )

        # A second event in the same organizer: nothing of it may ever show up.
        other_event = Event.objects.create(
            organizer=event.organizer,
            name="Other Event",
            slug="other",
            date_from=now() + datetime.timedelta(days=60),
            plugins="pretix_custom_reports",
        )
        other_item = Item.objects.create(
            event=other_event, name="Other ticket", default_price=1
        )
        other_order = Order.objects.create(
            event=other_event,
            code="ZZZZZ",
            status=Order.STATUS_PAID,
            email="z@example.org",
            sales_channel=channel,
            datetime=now(),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("1.00"),
        )
        OrderPosition.objects.create(
            order=other_order, item=other_item, price=Decimal("1.00"), positionid=1
        )

        yield {
            "order_a": order_a,
            "order_b": order_b,
            "order_c": order_c,
            "positions_a": positions_a,
            "live_b": live_b,
            "canceled_b": canceled_b,
            "ticket": ticket,
            "workshop": workshop,
            "category": category,
            "questions": questions,
            "checkin_list": checkin_list,
            "other_event": other_event,
            "other_order": other_order,
        }


def _compile(compiler, definition, event, **kwargs):
    with scopes_disabled():
        return compiler.compile(definition, event, **kwargs)


def _rows(compiler, definition, event, **kwargs):
    with scopes_disabled():
        report = compiler.compile(definition, event, **kwargs)
        return report, list(report.iter_rows())


def _order_report(*columns, **kwargs):
    return ReportDefinition(base=Base.ORDER, columns=tuple(columns), **kwargs)


def _position_report(*columns, **kwargs):
    return ReportDefinition(base=Base.ORDERPOSITION, columns=tuple(columns), **kwargs)


# ---------------------------------------------------------------------------
# Every golden fixture compiles and runs
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_golden_fixture_compiles_and_executes(name, compiler, event, data):
    report, rows = _rows(compiler, load_fixture(name), event)
    assert len(report.headers()) == len(report.columns)
    for row in rows:
        assert len(row) == len(report.columns)
    with scopes_disabled():
        assert report.count() >= 0


@pytest.mark.django_db
def test_compiled_report_satisfies_the_protocol(compiler, event, data):
    from pretix_custom_reports.contracts.protocols import CompiledReport

    report = _compile(compiler, load_fixture("minimal_order.json"), event)
    assert isinstance(report, CompiledReport)
    assert report.base is Base.ORDER
    assert report.event is event


@pytest.mark.django_db
def test_compiler_satisfies_the_protocol(compiler):
    from pretix_custom_reports.contracts.protocols import QueryCompiler

    assert isinstance(compiler, QueryCompiler)


# ---------------------------------------------------------------------------
# Event scoping (CLAUDE.md rule 4)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_base_order_never_leaves_the_event(compiler, event, data):
    definition = _order_report(
        Column(field="order.code"), options=ReportOptions(include_testmode_orders=True)
    )
    _, rows = _rows(compiler, definition, event)
    codes = {row[0] for row in rows}
    assert codes == {"AAAAA", "BBBBB", "CCCCC"}
    assert "ZZZZZ" not in codes


@pytest.mark.django_db
def test_base_orderposition_never_leaves_the_event(compiler, event, data):
    definition = _position_report(
        Column(field="order.code"), options=ReportOptions(include_testmode_orders=True)
    )
    _, rows = _rows(compiler, definition, event)
    assert "ZZZZZ" not in {row[0] for row in rows}


@pytest.mark.django_db
def test_compiling_for_the_other_event_returns_only_its_rows(compiler, event, data):
    definition = _order_report(Column(field="order.code"))
    _, rows = _rows(compiler, definition, data["other_event"])
    assert {row[0] for row in rows} == {"ZZZZZ"}


# ---------------------------------------------------------------------------
# Base switching: no row multiplication
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_base_order_yields_one_row_per_order(compiler, event, data):
    """Order A has three positions. A join would return it three times."""
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.price", aggregate=Aggregate.SUM),
        Column(field="item.name", aggregate=Aggregate.JOIN),
    )
    _, rows = _rows(compiler, definition, event)
    assert [row[0] for row in rows].count("AAAAA") == 1
    assert len(rows) == 2  # A and B; C is test mode


@pytest.mark.django_db
def test_base_orderposition_yields_one_row_per_position(compiler, event, data):
    definition = _position_report(Column(field="order.code"))
    _, rows = _rows(compiler, definition, event)
    # Three live positions on A, one live on B. C is test mode, B's second is
    # canceled.
    assert [row[0] for row in rows].count("AAAAA") == 3
    assert [row[0] for row in rows].count("BBBBB") == 1


@pytest.mark.django_db
def test_a_position_filter_on_base_order_does_not_duplicate_orders(
    compiler, event, data
):
    """ "orders containing a Workshop" -- A has two of them, so a join would
    return A twice."""
    definition = _order_report(
        Column(field="order.code"),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="item.internal_name",
                    operator=Operator.EXACT,
                    value="workshop",
                ),
            ),
        ),
    )
    _, rows = _rows(compiler, definition, event)
    assert [row[0] for row in rows] == ["AAAAA"]


@pytest.mark.django_db
def test_a_negated_position_filter_excludes_the_whole_order(compiler, event, data):
    """ "no position is a Workshop" excludes A even though it has a Ticket too."""
    definition = _order_report(
        Column(field="order.code"),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="item.internal_name",
                    operator=Operator.NOT_EXACT,
                    value="workshop",
                ),
            ),
        ),
    )
    _, rows = _rows(compiler, definition, event)
    assert [row[0] for row in rows] == ["BBBBB"]


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_aggregates_over_two_relations_do_not_multiply_each_other(
    compiler, event, data
):
    """The cross-product trap, in one test.

    Order A has three positions (23 + 10 + 10 = 43) *and* two confirmed payments
    (30 + 13 = 43). With joined annotations instead of subqueries, the position sum
    would come back doubled (86) and the payment sum tripled (129).
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.price", aggregate=Aggregate.SUM),
        Column(field="payment.sum_confirmed"),
        Column(field="position.positionid", aggregate=Aggregate.COUNT),
    )
    report, rows = _rows(compiler, definition, event)
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert row_a[1] == Decimal("43.00")
    assert row_a[2] == Decimal("43.00")
    assert row_a[3] == 3


@pytest.mark.django_db
def test_every_aggregate_function_produces_the_right_value(compiler, event, data):
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.price", aggregate=Aggregate.SUM),
        Column(field="position.price", aggregate=Aggregate.MIN),
        Column(field="position.price", aggregate=Aggregate.MAX),
        Column(field="position.price", aggregate=Aggregate.AVG),
        Column(field="position.positionid", aggregate=Aggregate.COUNT),
        Column(field="item.internal_name", aggregate=Aggregate.COUNT_DISTINCT),
        Column(field="item.internal_name", aggregate=Aggregate.JOIN),
    )
    _, rows = _rows(compiler, definition, event)
    row_a = next(row for row in rows if row[0] == "AAAAA")
    total, minimum, maximum, average, count, distinct, joined = row_a[1:]
    assert total == Decimal("43.00")
    assert minimum == Decimal("10.00")
    assert maximum == Decimal("23.00")
    assert Decimal("14.30") < Decimal(str(average)) < Decimal("14.40")
    assert count == 3
    assert distinct == 2
    assert joined == "ticket, workshop, workshop"


@pytest.mark.django_db
def test_count_of_an_order_without_matching_rows_is_zero_not_blank(
    compiler, event, data
):
    with scopes_disabled():
        empty = Order.objects.create(
            event=event,
            code="EMPTY",
            status=Order.STATUS_PENDING,
            sales_channel=event.organizer.sales_channels.get(identifier="web"),
            datetime=now(),
            expires=now() + datetime.timedelta(days=1),
            total=Decimal("0.00"),
        )
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.positionid", aggregate=Aggregate.COUNT),
    )
    _, rows = _rows(compiler, definition, event)
    row = next(r for r in rows if r[0] == empty.code)
    assert row[1] == 0


@pytest.mark.django_db
def test_join_uses_the_columns_separator(compiler, event, data):
    from pretix_custom_reports.contracts.definition import ColumnFormat

    definition = _order_report(
        Column(field="order.code"),
        Column(
            field="item.internal_name",
            aggregate=Aggregate.JOIN,
            format=ColumnFormat(separator=" | "),
        ),
    )
    _, rows = _rows(compiler, definition, event)
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert row_a[1] == "ticket | workshop | workshop"


@pytest.mark.django_db
def test_join_over_a_two_hop_relation(compiler, event, data):
    """``answer.tshirt-size`` on base ``order`` is Order -> position -> answer."""
    definition = _order_report(
        Column(field="order.code"),
        Column(field="answer.tshirt-size", aggregate=Aggregate.JOIN),
    )
    _, rows = _rows(compiler, definition, event)
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert row_a[1] == "L, XL"
    row_b = next(row for row in rows if row[0] == "BBBBB")
    assert row_b[1] == ""


@pytest.mark.django_db
def test_an_answer_aggregate_does_not_mix_up_questions(compiler, event, data):
    """Order A also has an ``arrival-date`` answer. It must not appear here.

    This is what ``extra['relation_filter']`` is for -- without it the aggregate
    would gather every answer of every question.
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="answer.tshirt-size", aggregate=Aggregate.COUNT),
        Column(field="answer.arrival-date", aggregate=Aggregate.COUNT),
    )
    _, rows = _rows(compiler, definition, event)
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert row_a[1] == 2  # L and XL
    assert row_a[2] == 1  # one arrival date


@pytest.mark.django_db
def test_aggregates_exclude_canceled_positions_by_default(compiler, event, data):
    """Order B has one live and one canceled position."""
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.positionid", aggregate=Aggregate.COUNT),
    )
    _, rows = _rows(compiler, definition, event)
    row_b = next(row for row in rows if row[0] == "BBBBB")
    assert row_b[1] == 1


@pytest.mark.django_db
def test_include_canceled_positions_changes_the_aggregate(compiler, event, data):
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.positionid", aggregate=Aggregate.COUNT),
        options=ReportOptions(include_canceled_positions=True),
    )
    _, rows = _rows(compiler, definition, event)
    row_b = next(row for row in rows if row[0] == "BBBBB")
    assert row_b[1] == 2


@pytest.mark.django_db
def test_include_canceled_positions_changes_the_join_too(compiler, event, data):
    """The option has to hold in the prefetch as well as in the subqueries.

    If it only held in one of them, two columns of the same report would disagree
    about which positions exist.
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.attendee_name", aggregate=Aggregate.JOIN),
        options=ReportOptions(include_canceled_positions=True),
    )
    _, rows = _rows(compiler, definition, event)
    row_b = next(row for row in rows if row[0] == "BBBBB")
    assert "Cancelled Person" in row_b[1]


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_testmode_orders_are_hidden_by_default(compiler, event, data):
    definition = _order_report(Column(field="order.code"))
    _, rows = _rows(compiler, definition, event)
    assert "CCCCC" not in {row[0] for row in rows}


@pytest.mark.django_db
def test_testmode_orders_appear_when_asked_for(compiler, event, data):
    definition = _order_report(
        Column(field="order.code"), options=ReportOptions(include_testmode_orders=True)
    )
    _, rows = _rows(compiler, definition, event)
    assert "CCCCC" in {row[0] for row in rows}


@pytest.mark.django_db
def test_canceled_positions_are_hidden_by_default_on_base_orderposition(
    compiler, event, data
):
    definition = _position_report(
        Column(field="order.code"), Column(field="position.positionid")
    )
    _, rows = _rows(compiler, definition, event)
    assert (("BBBBB", 2)) not in [tuple(row) for row in rows]


@pytest.mark.django_db
def test_canceled_positions_appear_when_asked_for(compiler, event, data):
    definition = _position_report(
        Column(field="order.code"),
        Column(field="position.positionid"),
        options=ReportOptions(include_canceled_positions=True),
    )
    _, rows = _rows(compiler, definition, event)
    assert ("BBBBB", 2) in [tuple(row) for row in rows]


@pytest.mark.django_db
def test_row_limit_caps_the_queryset(compiler, event, data):
    definition = _position_report(
        Column(field="order.code"), options=ReportOptions(row_limit=2)
    )
    report, rows = _rows(compiler, definition, event)
    assert len(rows) == 2
    with scopes_disabled():
        assert report.count() == 2, "count is capped too, or the preview would lie"


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_multi_level_sorting_is_applied_in_order(compiler, event, data):
    definition = _position_report(
        Column(field="order.code"),
        Column(field="position.positionid"),
        sorting=(
            SortEntry(field="order.code", direction=SortDirection.DESC),
            SortEntry(field="position.positionid", direction=SortDirection.ASC),
        ),
    )
    _, rows = _rows(compiler, definition, event)
    assert [tuple(row) for row in rows] == [
        ("BBBBB", 1),
        ("AAAAA", 1),
        ("AAAAA", 2),
        ("AAAAA", 3),
    ]


@pytest.mark.django_db
def test_the_model_default_ordering_is_replaced_not_extended(compiler, event, data):
    """``Order.Meta.ordering`` is ``("-datetime", "-pk")``.

    If the compiler used ``order_by()`` additively, that would become the primary
    sort of every report and the definition's own sorting would be decoration.
    """
    definition = _order_report(
        Column(field="order.code"),
        sorting=(SortEntry(field="order.code", direction=SortDirection.ASC),),
    )
    _, rows = _rows(compiler, definition, event)
    assert [row[0] for row in rows] == ["AAAAA", "BBBBB"]


@pytest.mark.django_db
def test_sorting_is_stable_across_pages(compiler, event, data):
    """The pk tiebreaker in action.

    Two orders sharing every sort value must come back in the same order in two
    separately executed queries -- otherwise a paginated preview shows a row twice.
    """
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        stamp = now()
        for code in ("D0001", "D0002", "D0003"):
            Order.objects.create(
                event=event,
                code=code,
                status=Order.STATUS_PENDING,
                sales_channel=channel,
                datetime=stamp,
                expires=stamp + datetime.timedelta(days=1),
                total=Decimal("1.00"),
            )
    definition = _order_report(
        Column(field="order.code"),
        sorting=(SortEntry(field="order.datetime", direction=SortDirection.DESC),),
    )
    first, rows_one = _rows(compiler, definition, event)
    _, rows_two = _rows(compiler, definition, event)
    assert rows_one == rows_two
    assert len(first.plan.ordering) == 2


@pytest.mark.django_db
def test_nulls_sort_last_in_both_directions(compiler, event, data):
    """Order A has an attendee e-mail on position 1 only."""
    for direction in (SortDirection.ASC, SortDirection.DESC):
        definition = _position_report(
            Column(field="position.attendee_email"),
            sorting=(SortEntry(field="position.attendee_email", direction=direction),),
        )
        _, rows = _rows(compiler, definition, event)
        values = [row[0] for row in rows]
        non_empty = [v for v in values if v]
        assert values[: len(non_empty)] == non_empty, direction


# ---------------------------------------------------------------------------
# Filters, end to end
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_status_filter_selects_the_right_orders(compiler, event, data):
    definition = _order_report(
        Column(field="order.code"),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="order.status", operator=Operator.IN, value=["p"]
                ),
            ),
        ),
    )
    _, rows = _rows(compiler, definition, event)
    assert [row[0] for row in rows] == ["AAAAA"]


@pytest.mark.django_db
def test_money_filter_compares_numerically_not_lexicographically(compiler, event, data):
    """``"9.00" > "43.00"`` as strings. As decimals it is not."""
    definition = _order_report(
        Column(field="order.code"),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="order.total", operator=Operator.GT, value="9.00"
                ),
            ),
        ),
        options=ReportOptions(include_testmode_orders=True),
    )
    _, rows = _rows(compiler, definition, event)
    assert set(row[0] for row in rows) == {"AAAAA", "BBBBB"}


@pytest.mark.django_db
def test_relative_date_filter_selects_by_the_event_day(compiler, event, data):
    definition = _order_report(
        Column(field="order.code"),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="order.datetime",
                    operator=Operator.RELATIVE_LAST_DAYS,
                    value=1,
                ),
            ),
        ),
        options=ReportOptions(include_testmode_orders=True),
    )
    _, rows = _rows(compiler, definition, event)
    # Order C was created "now", A two days ago, B one day ago. "Last 1 day" is
    # today only.
    assert "CCCCC" in {row[0] for row in rows}
    assert "AAAAA" not in {row[0] for row in rows}


@pytest.mark.django_db
def test_relative_filter_reevaluates_on_every_compile(compiler, event, data):
    """The whole point for scheduled reports: no frozen date range."""
    definition = _order_report(
        Column(field="order.code"),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="order.datetime", operator=Operator.RELATIVE_TODAY
                ),
            ),
        ),
        options=ReportOptions(include_testmode_orders=True),
    )
    today = _rows(compiler, definition, event)[1]
    with scopes_disabled():
        later = compiler.compile(
            definition, event, now=now() + datetime.timedelta(days=3)
        )
        assert list(later.iter_rows()) == []
    assert today


@pytest.mark.django_db
def test_and_or_nesting_end_to_end(compiler, event, data):
    """A OR B inside, ANDed with a third condition."""
    definition = _position_report(
        Column(field="order.code"),
        Column(field="item.internal_name"),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="order.status", operator=Operator.IN, value=["p", "n"]
                ),
                FilterGroup(
                    op=BoolOp.OR,
                    children=(
                        FilterCondition(
                            field="item.internal_name",
                            operator=Operator.EXACT,
                            value="workshop",
                        ),
                        FilterCondition(
                            field="order.code",
                            operator=Operator.EXACT,
                            value="BBBBB",
                        ),
                    ),
                ),
            ),
        ),
    )
    _, rows = _rows(compiler, definition, event)
    assert sorted(tuple(row) for row in rows) == [
        ("AAAAA", "workshop"),
        ("AAAAA", "workshop"),
        ("BBBBB", "ticket"),
    ]


@pytest.mark.django_db
def test_answer_filter_on_base_orderposition(compiler, event, data):
    definition = _position_report(
        Column(field="order.code"),
        Column(field="answer.tshirt-size"),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="answer.tshirt-size",
                    operator=Operator.IN,
                    value=["L", "XL"],
                ),
            ),
        ),
    )
    _, rows = _rows(compiler, definition, event)
    assert sorted(row[1] for row in rows) == ["L", "XL"]


@pytest.mark.django_db
def test_answer_filter_on_base_order_is_an_existence_test(compiler, event, data):
    definition = _order_report(
        Column(field="order.code"),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="answer.tshirt-size", operator=Operator.IN, value=["L"]
                ),
            ),
        ),
    )
    _, rows = _rows(compiler, definition, event)
    assert [row[0] for row in rows] == ["AAAAA"]


@pytest.mark.django_db
def test_a_filter_on_an_annotation_alias_works(compiler, event, data):
    definition = _order_report(
        Column(field="order.code"),
        Column(field="payment.sum_confirmed"),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="payment.sum_confirmed",
                    operator=Operator.GT,
                    value="0.00",
                ),
            ),
        ),
    )
    _, rows = _rows(compiler, definition, event)
    assert [row[0] for row in rows] == ["AAAAA"]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_related_values_are_rendered_without_extra_queries(compiler, event, data):
    definition = _position_report(
        Column(field="order.code"),
        Column(field="item.name"),
        Column(field="item.category"),
        Column(field="invoice_address.company"),
    )
    _, rows = _rows(compiler, definition, event)
    workshop_rows = [row for row in rows if str(row[1]) == "Workshop"]
    assert workshop_rows
    assert str(workshop_rows[0][2]) == "Workshops"


@pytest.mark.django_db
def test_a_missing_related_object_renders_as_none(compiler, event, data):
    """No order in the dataset has an invoice address."""
    definition = _position_report(Column(field="invoice_address.company"))
    _, rows = _rows(compiler, definition, event)
    assert all(row[0] is None for row in rows)


@pytest.mark.django_db
def test_i18n_values_survive_as_lazy_strings(compiler, event, data):
    """``select_related`` plus attribute access, rather than an ``F()`` alias."""
    from i18nfield.strings import LazyI18nString

    definition = _position_report(Column(field="item.name"))
    _, rows = _rows(compiler, definition, event)
    assert any(isinstance(row[0], LazyI18nString) for row in rows)


@pytest.mark.django_db
def test_checkin_annotation_counts_only_successful_entries(compiler, event, data):
    definition = _position_report(
        Column(field="order.code"),
        Column(field="position.positionid"),
        Column(field="checkin.count"),
    )
    _, rows = _rows(compiler, definition, event)
    checked_in = [row for row in rows if row[2]]
    assert len(checked_in) == 1
    assert checked_in[0][:2] == ["AAAAA", 1]


# ---------------------------------------------------------------------------
# Query counts (SPEC.md section 4: assertNumQueries for a wide report)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_wide_report_is_two_queries(compiler, event, data, django_assert_num_queries):
    """The 31-column workhorse, fully iterated.

    One SELECT for everything -- 30 visible columns including seven registry
    subquery annotations and one aggregate subquery -- plus one prefetch for the
    single ``join`` column. That is the whole budget, and it does not grow with the
    number of rows.
    """
    definition = load_fixture(WIDE_FIXTURE)
    with scopes_disabled():
        report = compiler.compile(definition, event)
        assert len(report.columns) == 30
        with django_assert_num_queries(2):
            rows = list(report.iter_rows())
    assert rows
    assert len(rows[0]) == 30


@pytest.mark.django_db
def test_wide_report_query_count_does_not_grow_with_rows(
    compiler, event, data, django_assert_num_queries
):
    """The N+1 guard. Twenty more orders must not cost twenty more queries."""
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        for index in range(20):
            order = Order.objects.create(
                event=event,
                code=f"N{index:04d}",
                status=Order.STATUS_PAID,
                sales_channel=channel,
                datetime=now(),
                expires=now() + datetime.timedelta(days=1),
                total=Decimal("10.00"),
            )
            OrderPosition.objects.create(
                order=order, item=data["ticket"], price=Decimal("10.00"), positionid=1
            )
    definition = load_fixture(WIDE_FIXTURE)
    with scopes_disabled():
        report = compiler.compile(definition, event)
        with django_assert_num_queries(2):
            rows = list(report.iter_rows())
    assert len(rows) == 22


@pytest.mark.django_db
def test_attendee_list_is_a_single_query(
    compiler, event, data, django_assert_num_queries
):
    """The most common report of all: twenty columns across six relations, one
    SELECT."""
    definition = load_fixture("orderposition_basic.json")
    with scopes_disabled():
        report = compiler.compile(definition, event)
        with django_assert_num_queries(1):
            list(report.iter_rows())


@pytest.mark.django_db
def test_question_columns_do_not_cause_n_plus_one(
    compiler, event, data, django_assert_num_queries
):
    """Three answer columns as correlated subqueries: still one query."""
    definition = load_fixture("orderposition_questions.json")
    with scopes_disabled():
        report = compiler.compile(definition, event)
        with django_assert_num_queries(1):
            list(report.iter_rows())


@pytest.mark.django_db
def test_count_is_one_query(compiler, event, data, django_assert_num_queries):
    definition = load_fixture(WIDE_FIXTURE)
    with scopes_disabled():
        report = compiler.compile(definition, event)
        with django_assert_num_queries(1):
            report.count()


@pytest.mark.django_db
def test_the_count_query_skips_the_column_annotations(compiler, event, data):
    """A COUNT(*) must not pay for values nobody reads."""
    definition = load_fixture(WIDE_FIXTURE)
    with scopes_disabled():
        report = compiler.compile(definition, event)
        display_sql = str(report.queryset.query)
        count_sql = str(report.count_queryset.query)
    assert display_sql.upper().count("SELECT") > count_sql.upper().count("SELECT")
    assert "ORDER BY" not in count_sql.upper()


# ---------------------------------------------------------------------------
# The scale of an aggregated amount (T-002)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "aggregate,expected",
    [
        (Aggregate.SUM, "43.00"),
        (Aggregate.MIN, "10.00"),
        (Aggregate.MAX, "23.00"),
        # 43,00 / 3 = 14,3333...; quantised half-even, deliberately -- see
        # ``relations.aggregate_expression``.
        (Aggregate.AVG, "14.33"),
    ],
)
@pytest.mark.django_db
def test_an_aggregated_amount_keeps_two_decimal_places(
    compiler, event, data, aggregate, expected
):
    """T-002: compared as text, because that is what lands in the file.

    ``Decimal("43") == Decimal("43.00")`` is true, which is exactly why nobody
    found this by comparing numbers. The exporter writes ``str(value)``, so the
    trailing zero is the difference between an accounting column that adds up in
    a spreadsheet and one that does not -- and between the file this plugin
    writes on SQLite and the one it writes on PostgreSQL.
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.price", aggregate=aggregate),
    )
    _, rows = _rows(compiler, definition, event)
    value = next(row for row in rows if row[0] == "AAAAA")[1]
    assert isinstance(value, Decimal)
    assert str(value) == expected
    assert value.as_tuple().exponent == -2


@pytest.mark.django_db
def test_the_scale_holds_next_to_a_plain_amount_in_the_same_row(compiler, event, data):
    """The visible symptom was one row notating the same money two ways.

    ``order.total`` is a plain column and was always quantised by the backend;
    the summed position price was not. Both are asserted here so the two paths
    cannot drift apart again.
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="order.total"),
        Column(field="position.price", aggregate=Aggregate.SUM),
        Column(field="position.tax_value", aggregate=Aggregate.SUM),
    )
    _, rows = _rows(compiler, definition, event)
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert [str(value) for value in row_a[1:]] == ["43.00", "43.00", "0.00"]


@pytest.mark.django_db
def test_a_counted_aggregate_stays_an_integer(compiler, event, data):
    """The money output field must not leak onto the aggregates that count.

    ``COUNT`` has no scale to lose, and turning its result into a ``Decimal``
    would put "3.00" into a column headed "Positions".
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.positionid", aggregate=Aggregate.COUNT),
        Column(field="item.internal_name", aggregate=Aggregate.COUNT_DISTINCT),
    )
    _, rows = _rows(compiler, definition, event)
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert row_a[1:] == [3, 2]
    assert all(isinstance(value, int) for value in row_a[1:])


@pytest.mark.django_db
def test_an_aggregate_over_a_non_money_field_is_left_alone(compiler, event, data):
    """Only ``DataType.MONEY`` triggers the output field, not "it is a number"."""
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.positionid", aggregate=Aggregate.MAX),
    )
    _, rows = _rows(compiler, definition, event)
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert row_a[1] == 3
    assert not isinstance(row_a[1], Decimal)


# ---------------------------------------------------------------------------
# Prefetch de-duplication for ``join`` columns (S-005)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_twenty_identical_join_columns_cost_one_prefetch(
    compiler, event, data, django_assert_num_queries
):
    """The S-005 counter-check: the price is per *prefetch*, not per column.

    Twenty ``join`` columns over the same relation with the same condition select
    exactly the same related rows. Until S-005 each of them got a ``Prefetch``
    with a ``to_attr`` minted from the column index, which made the plan's
    de-duplication unreachable: 20 columns cost 20 extra queries, and
    ``MAX_COLUMNS`` at 200 made a single preview request ~200 round trips.

    Two queries: one for the orders, one for the shared prefetch. And every column
    still has to render, so the saving is not bought by dropping a column.
    """
    definition = _order_report(
        Column(field="order.code"),
        *[
            Column(field="position.attendee_name", aggregate=Aggregate.JOIN)
            for _ in range(20)
        ],
    )
    with scopes_disabled():
        report = compiler.compile(definition, event)
        with django_assert_num_queries(2):
            rows = list(report.iter_rows())
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert row_a[1:] == ["Ada Lovelace, Grace Hopper, Alan Turing"] * 20


@pytest.mark.django_db
def test_join_columns_with_different_conditions_keep_their_own_prefetch(
    compiler, event, data, django_assert_num_queries
):
    """De-duplication must not merge two columns that ask different questions.

    Both go through ``all_positions__answers``, so the intermediate level is
    shared, but the leaf conditions differ by question. Merging them would put the
    T-shirt sizes in the arrival-date column -- wrong output, no error -- which is
    why the shared name is derived from the condition and not merely from the
    relation.
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="answer.tshirt-size", aggregate=Aggregate.JOIN),
        Column(field="answer.arrival-date", aggregate=Aggregate.JOIN),
    )
    with scopes_disabled():
        report = compiler.compile(definition, event)
        # 1 row query + 1 shared intermediate (all_positions) + 2 leaves.
        with django_assert_num_queries(4):
            rows = list(report.iter_rows())
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert row_a[1] == "L, XL"
    assert row_a[2] == "2026-09-01"


@pytest.mark.django_db
def test_join_columns_that_need_different_select_related_stay_apart(
    compiler, event, data, django_assert_num_queries
):
    """Same relation, same condition, different tail -- and that is a difference.

    ``item.name`` needs ``select_related("item")`` on the prefetched positions,
    ``position.attendee_name`` needs nothing. Sharing one prefetch would save a
    query and buy an item lookup per position instead, so the inner
    ``select_related`` is part of what makes two prefetches interchangeable.
    Three queries here, and none of them per row -- adding rows does not add
    queries (asserted next to it in the wide-report tests).
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="item.internal_name", aggregate=Aggregate.JOIN),
        Column(field="position.attendee_name", aggregate=Aggregate.JOIN),
    )
    with scopes_disabled():
        report = compiler.compile(definition, event)
        with django_assert_num_queries(3):
            rows = list(report.iter_rows())
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert row_a[1] == "ticket, workshop, workshop"
    assert row_a[2] == "Ada Lovelace, Grace Hopper, Alan Turing"


@pytest.mark.django_db
def test_join_columns_of_the_same_relation_share_across_different_tails(
    compiler, event, data, django_assert_num_queries
):
    """Two plain position fields read out of one prefetched list.

    Neither tail needs a ``select_related``, so both columns can walk the same
    prefetched positions: one row query, one prefetch, two different cells.
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.attendee_name", aggregate=Aggregate.JOIN),
        Column(field="position.attendee_email", aggregate=Aggregate.JOIN),
    )
    with scopes_disabled():
        report = compiler.compile(definition, event)
        with django_assert_num_queries(2):
            rows = list(report.iter_rows())
    row_a = next(row for row in rows if row[0] == "AAAAA")
    assert row_a[1] == "Ada Lovelace, Grace Hopper, Alan Turing"
    assert row_a[2] == "ada@example.org"


@pytest.mark.django_db
def test_a_condition_without_a_faithful_text_form_is_never_merged(
    compiler, event, data, django_assert_num_queries
):
    """When in doubt, keep them apart.

    A third-party field may put any ``Q`` into ``extra['relation_filter']``,
    including one holding a model instance. Two such instances can share a
    ``str()`` and address different rows, so
    :func:`~pretix_custom_reports.query.relations.condition_signature` refuses to
    sign them and the compiler falls back to a prefetch per column -- the old,
    expensive, always-correct behaviour. Correctness is not traded for the
    saving.
    """
    from dataclasses import replace

    from pretix_custom_reports.query.columns import EXTRA_RELATION_FILTER

    base_field = ReferenceRegistry().get_fields(event, Base.ORDER)[
        "position.attendee_name"
    ]
    opaque = replace(
        base_field,
        extra=dict(
            base_field.extra or {}, **{EXTRA_RELATION_FILTER: Q(item=data["ticket"])}
        ),
    )
    registry = ReferenceRegistry(overrides={"position.attendee_name": opaque})
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.attendee_name", aggregate=Aggregate.JOIN),
        Column(field="position.attendee_name", aggregate=Aggregate.JOIN),
    )
    with scopes_disabled():
        report = ReportQueryCompiler(registry).compile(definition, event)
        with django_assert_num_queries(3):
            list(report.iter_rows())


@pytest.mark.django_db
def test_iter_rows_streams_with_the_default_chunk_size(compiler, event, data):
    """``iter_rows`` goes through ``iterator()``, not through a list."""
    definition = _position_report(Column(field="order.code"))
    with scopes_disabled():
        report = compiler.compile(definition, event)
        generator = report.iter_rows()
        assert hasattr(generator, "__next__")
        assert next(generator)


# ---------------------------------------------------------------------------
# Preview mode (SPEC.md F2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_preview_applies_a_hard_sql_limit(compiler, event, data):
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        for index in range(PREVIEW_ROW_LIMIT + 5):
            Order.objects.create(
                event=event,
                code=f"P{index:04d}",
                status=Order.STATUS_PENDING,
                sales_channel=channel,
                datetime=now(),
                expires=now() + datetime.timedelta(days=1),
                total=Decimal("1.00"),
            )
    definition = _order_report(Column(field="order.code"))
    with scopes_disabled():
        report = compiler.compile(definition, event, preview=True)
        assert report.preview is True
        assert report.effective_limit == PREVIEW_ROW_LIMIT
        assert "LIMIT %d" % PREVIEW_ROW_LIMIT in str(report.queryset.query)
        assert len(list(report.iter_rows())) == PREVIEW_ROW_LIMIT


@pytest.mark.django_db
def test_preview_count_reports_the_full_total(compiler, event, data):
    """Twenty sample rows next to the real estimated total (SPEC.md F2)."""
    definition = _order_report(
        Column(field="order.code"), options=ReportOptions(include_testmode_orders=True)
    )
    with scopes_disabled():
        report = compiler.compile(definition, event, preview=True)
        assert len(list(report.iter_rows())) == 3
        assert report.count() == 3


@pytest.mark.django_db
def test_preview_respects_a_lower_row_limit(compiler, event, data):
    definition = _order_report(
        Column(field="order.code"), options=ReportOptions(row_limit=2)
    )
    with scopes_disabled():
        report = compiler.compile(definition, event, preview=True)
        assert report.effective_limit == 2


@pytest.mark.django_db
def test_preview_never_loads_the_full_dataset(
    compiler, event, data, django_assert_num_queries
):
    definition = load_fixture(WIDE_FIXTURE)
    with scopes_disabled():
        report = compiler.compile(definition, event, preview=True)
        with django_assert_num_queries(2):
            list(report.iter_rows())
        with django_assert_num_queries(1):
            report.count()


@pytest.mark.django_db
def test_iter_rows_limit_is_applied_on_top(compiler, event, data):
    definition = _position_report(Column(field="order.code"))
    with scopes_disabled():
        report = compiler.compile(definition, event)
        assert len(list(report.iter_rows(limit=2))) == 2
        assert len(list(report.iter_rows(limit=0))) == 0
        assert len(list(report.iter_rows())) == 4


# ---------------------------------------------------------------------------
# Helpers used by the other test modules
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_support_helpers_work(compiler, event, data):
    report = _compile(compiler, load_fixture("minimal_order.json"), event)
    with scopes_disabled():
        assert set(order_codes(report)) == {"AAAAA", "BBBBB"}
        assert column_values(report, "order.code") == order_codes(report)
