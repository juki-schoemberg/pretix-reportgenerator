"""The compiler against the **real** field registry, not a double.

Owner: query-dev (ORCHESTRIERUNG.md section 5, ``tests/test_query*.py``).

Why this module exists
----------------------

Every other ``test_query_*`` module compiles against
``contracts.stubs.StubFieldRegistry`` or against the ``ReferenceRegistry`` in
``tests/test_query_support.py``. Both are ours. That is deliberate -- the
compiler was built while ``registry/`` was being written in parallel, and a
compiler test that fails because somebody added a field is a bad test -- but it
left a gap wide enough to hide a real bug:

``query/columns.py`` used to read the condition for an aggregated relation from
``field.extra["relation_filter"]``, a convention only *our own* double
implemented. The real registry expresses the same thing through
``registry.hints.aggregate_filter`` (see
``handoff/requests/registry-dev-an-query-dev-annotationen-und-aggregate.md``
section 2), which the compiler never called. Against the real registry an
``answer.<identifier>`` column on base ``order`` therefore lost its
``question=<pk>`` condition and aggregated the answers to **every** question of
the event into one cell.

That is not an exception, it is a wrong number in a spreadsheet -- and the double
kept the corresponding test green throughout. So: this module resolves its fields
through :func:`pretix_custom_reports.registry.library.field_registry` and asserts
on values, not on plan shape.

What it deliberately does not do
--------------------------------

It does not re-test the registry (``tests/test_registry.py`` owns that) and it
does not enumerate fields. It exercises the seam between the two packages:
``hints.aggregate_relation``, ``hints.aggregate_filter``, and the annotations of
the fields a report actually uses.
"""

import datetime
import pytest
from decimal import Decimal
from django.db.models import Q
from django.utils.timezone import now
from django_scopes import scopes_disabled
from pretix.base.models import (
    Item,
    Order,
    OrderPosition,
    Question,
    QuestionAnswer,
)

from pretix_custom_reports.contracts.definition import (
    Column,
    ReportDefinition,
    ReportOptions,
)
from pretix_custom_reports.contracts.errors import FieldContractError
from pretix_custom_reports.contracts.fields import (
    Aggregate,
    Base,
    DataType,
    Operator,
    ReportField,
)
from pretix_custom_reports.query import columns as columns_mod, relations
from pretix_custom_reports.query.compiler import ReportQueryCompiler
from pretix_custom_reports.registry import cache as registry_cache, hints
from pretix_custom_reports.registry.library import field_registry


@pytest.fixture
def registry():
    """The real registry, with a clean process-local field cache around it.

    ``field_registry()`` is a process-wide singleton and its cache is keyed by
    event primary key -- which repeats across tests. The test settings use the
    dummy cache backend, so the validity token never survives anyway, but clearing
    explicitly keeps this module independent of that detail.
    """
    registry_cache.clear_local_cache()
    yield field_registry()
    registry_cache.clear_local_cache()


@pytest.fixture
def compiler(registry):
    return ReportQueryCompiler(registry)


@pytest.fixture
def data(event):
    """Two orders, two questions, and one canceled position that answered both.

    The canceled position is the point: with ``include_canceled_positions=False``
    its answers and its price have to disappear from every aggregate, and with
    ``True`` they all have to reappear -- consistently across a ``join``, a
    ``count`` and a ``sum``, which travel three different code paths.
    """
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        ticket = Item.objects.create(
            event=event, name="Ticket", internal_name="ticket", default_price=23
        )
        tshirt = Question.objects.create(
            event=event,
            question="T-shirt size",
            identifier="tshirt-size",
            type=Question.TYPE_CHOICE,
            position=0,
        )
        arrival = Question.objects.create(
            event=event,
            question="Day of arrival",
            identifier="arrival-date",
            type=Question.TYPE_DATE,
            position=1,
        )

        order_a = Order.objects.create(
            event=event,
            code="AAAAA",
            status=Order.STATUS_PAID,
            email="a@example.org",
            sales_channel=channel,
            datetime=now() - datetime.timedelta(days=2),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("33.00"),
        )
        first = OrderPosition.objects.create(
            order=order_a, item=ticket, price=Decimal("23.00"), positionid=1
        )
        second = OrderPosition.objects.create(
            order=order_a, item=ticket, price=Decimal("10.00"), positionid=2
        )
        canceled = OrderPosition.all.create(
            order=order_a,
            item=ticket,
            price=Decimal("10.00"),
            positionid=3,
            canceled=True,
        )
        # Order of creation is the order of the primary keys, which is the order a
        # ``join`` column renders in.
        QuestionAnswer.objects.create(orderposition=first, question=tshirt, answer="L")
        QuestionAnswer.objects.create(
            orderposition=first, question=arrival, answer="2026-09-01"
        )
        QuestionAnswer.objects.create(
            orderposition=second, question=tshirt, answer="XL"
        )
        QuestionAnswer.objects.create(
            orderposition=canceled, question=tshirt, answer="S"
        )

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
        OrderPosition.objects.create(
            order=order_b, item=ticket, price=Decimal("23.00"), positionid=1
        )

        registry_cache.clear_local_cache()
        yield {
            "ticket": ticket,
            "tshirt": tshirt,
            "arrival": arrival,
            "order_a": order_a,
            "order_b": order_b,
        }


def _run(compiler, definition, event, **kwargs):
    with scopes_disabled():
        report = compiler.compile(definition, event, **kwargs)
        return report, list(report.iter_rows())


def _row(rows, code="AAAAA"):
    return next(row for row in rows if row[0] == code)


def _order_report(*cols, **kwargs):
    return ReportDefinition(base=Base.ORDER, columns=tuple(cols), **kwargs)


# ---------------------------------------------------------------------------
# The bug this module was written for
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_two_answer_columns_do_not_mix_up_their_questions(compiler, event, data):
    """The regression test for the missing ``hints.aggregate_filter`` call.

    Order A holds three answers on its live positions: two T-shirt sizes and one
    arrival date. Without the question condition every one of these four cells
    would report all three answers -- ``count`` 3 and a joined cell containing a
    date next to two shirt sizes. Nothing would raise.
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="answer.tshirt-size", aggregate=Aggregate.JOIN),
        Column(field="answer.tshirt-size", aggregate=Aggregate.COUNT),
        Column(field="answer.arrival-date", aggregate=Aggregate.JOIN),
        Column(field="answer.arrival-date", aggregate=Aggregate.COUNT),
    )
    _, rows = _run(compiler, definition, event)
    assert _row(rows)[1:] == ["L, XL", 2, "2026-09-01", 1]


@pytest.mark.django_db
def test_the_question_condition_is_in_the_sql_not_in_python(compiler, event, data):
    """Both answer columns must carry their *own* question into the database.

    Filtering in Python would still produce the right cells here and would fall
    over on the first six-digit event.
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="answer.tshirt-size", aggregate=Aggregate.COUNT),
        Column(field="answer.arrival-date", aggregate=Aggregate.COUNT),
    )
    with scopes_disabled():
        sql = str(compiler.compile(definition, event).queryset.query)
    assert sql.count("question_id") >= 2
    assert str(data["tshirt"].pk) in sql
    assert str(data["arrival"].pk) in sql


@pytest.mark.django_db
def test_the_registry_hint_is_rebased_onto_the_leaf_model(registry, event, data):
    """The translation itself, in isolation.

    ``hints.aggregate_filter`` answers from the base model's point of view because
    its documented use is ``Sum(path, filter=...)``. This compiler puts the
    condition inside a subquery over ``QuestionAnswer``, where the same two rules
    are two different lookups.
    """
    with scopes_disabled():
        field = registry.resolve("answer.tshirt-size", event, Base.ORDER)
    assert hints.aggregate_relation(field) == "all_positions__answers"
    assert hints.aggregate_filter(field, include_canceled_positions=False) == (
        Q(all_positions__canceled=False)
        & Q(all_positions__answers__question=data["tshirt"].pk)
    )

    chain = relations.relation_chain(Order, field.orm_path)
    assert columns_mod.relation_filter(field, chain, include_canceled=False) == (
        Q(orderposition__canceled=False) & Q(question=data["tshirt"].pk)
    )
    # With canceled positions included, only the question rule survives.
    assert columns_mod.relation_filter(field, chain, include_canceled=True) == Q(
        question=data["tshirt"].pk
    )


# ---------------------------------------------------------------------------
# include_canceled_positions, against the real hints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "include_canceled,expected",
    [
        (False, ["L, XL", 2, Decimal("33.00")]),
        (True, ["L, XL, S", 3, Decimal("43.00")]),
    ],
)
def test_include_canceled_positions_switches_every_aggregate(
    compiler, event, data, include_canceled, expected
):
    """One option, three code paths: a prefetch, a count and a sum.

    The canceled position of order A costs 10.00 and answered the T-shirt
    question with ``S``. All three columns have to agree about whether it exists,
    and all three take their condition from ``hints.aggregate_filter``.
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="answer.tshirt-size", aggregate=Aggregate.JOIN),
        Column(field="answer.tshirt-size", aggregate=Aggregate.COUNT),
        Column(field="position.price", aggregate=Aggregate.SUM),
        options=ReportOptions(include_canceled_positions=include_canceled),
    )
    _, rows = _run(compiler, definition, event)
    assert _row(rows)[1:] == expected


@pytest.mark.django_db
def test_the_canceled_rule_reaches_a_two_hop_relation(compiler, event, data):
    """The canceled flag sits one hop *above* the rows being aggregated.

    ``answer.*`` aggregates ``QuestionAnswer`` rows, and ``canceled`` belongs to
    the position they hang off. A condition that only ever looked at the leaf
    would silently count the canceled position's answer.
    """
    definition = _order_report(
        Column(field="order.code"),
        Column(field="answer.tshirt-size", aggregate=Aggregate.COUNT_DISTINCT),
    )
    _, rows = _run(compiler, definition, event)
    assert _row(rows)[1] == 2, "S belongs to the canceled position"


# ---------------------------------------------------------------------------
# The ordinary things, once, with real fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_order_report_still_yields_one_row_per_order(compiler, event, data):
    """Four aggregates over two relations, no cross product, no duplicate rows."""
    definition = _order_report(
        Column(field="order.code"),
        Column(field="position.price", aggregate=Aggregate.SUM),
        Column(field="position.positionid", aggregate=Aggregate.COUNT),
        Column(field="answer.tshirt-size", aggregate=Aggregate.COUNT),
        Column(field="payment.sum_confirmed"),
    )
    _, rows = _run(compiler, definition, event)
    assert [row[0] for row in rows] == ["AAAAA", "BBBBB"] or [
        row[0] for row in rows
    ] == ["BBBBB", "AAAAA"]
    assert len(rows) == 2
    assert _row(rows)[1:4] == [Decimal("33.00"), 2, 2]


@pytest.mark.django_db
def test_a_position_report_with_answers_and_computed_fields_runs(compiler, event, data):
    """Base ``orderposition``: answers are annotations, not aggregates.

    Also the merge case from
    ``handoff/requests/registry-dev-an-query-dev-annotationen-und-aggregate.md``
    section 1: ``order.pending_sum``, ``payment.sum_confirmed`` and
    ``computed.payment_state`` share annotation aliases and depend on each other's
    order in one ``annotate()`` call.
    """
    definition = ReportDefinition(
        base=Base.ORDERPOSITION,
        columns=(
            Column(field="order.code"),
            Column(field="position.positionid"),
            Column(field="answer.tshirt-size"),
            Column(field="item.name"),
            Column(field="order.pending_sum"),
            Column(field="payment.sum_confirmed"),
            Column(field="computed.payment_state"),
        ),
    )
    _, rows = _run(compiler, definition, event)
    assert len(rows) == 3  # two live positions on A, one on B
    answers = {row[0] + str(row[1]): row[2] for row in rows}
    assert answers["AAAAA1"] == "L"
    assert answers["AAAAA2"] == "XL"
    assert answers["BBBBB1"] is None


@pytest.mark.django_db
def test_a_real_registry_report_does_not_grow_queries_with_rows(
    compiler, event, data, django_assert_num_queries
):
    """The N+1 guard for the real registry.

    Four queries for ten columns and seventeen orders, and the number does not
    depend on the number of rows:

    1. the row itself, with both aggregate subqueries and both registry
       subquery annotations (``payment.sum_confirmed``, ``checkin.count``) inline,
    2. ``all_positions`` with the ``to_attr`` of the ``item.name`` join,
    3. ``all_positions`` again, without a ``to_attr``, as the parent level the
       nested answer prefetch needs,
    4. ``all_positions__answers`` with the ``to_attr`` of the answer join.

    2 and 3 do not collapse: the leaf level of a ``join`` carries a per-column
    ``to_attr`` precisely so that two columns over the same relation cannot
    interfere, and Django refuses one lookup with two different querysets. The
    intermediate levels do collapse, which is what keeps this at four rather than
    one per column.

    Compiling is outside the block on purpose: building the field table queries
    the event's questions and the organizer's meta properties.
    """
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        for index in range(15):
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

    definition = _order_report(
        Column(field="order.code"),
        Column(field="order.status"),
        Column(field="order.total"),
        Column(field="payment.sum_confirmed"),
        Column(field="checkin.count"),
        Column(field="position.price", aggregate=Aggregate.SUM),
        Column(field="position.positionid", aggregate=Aggregate.COUNT),
        Column(field="item.name", aggregate=Aggregate.JOIN),
        Column(field="answer.tshirt-size", aggregate=Aggregate.JOIN),
        Column(field="answer.tshirt-size", aggregate=Aggregate.COUNT),
    )
    with scopes_disabled():
        report = compiler.compile(definition, event)
        with django_assert_num_queries(4):
            rows = list(report.iter_rows())
    assert len(rows) == 17
    assert _row(rows)[8:] == ["L, XL", 2]


@pytest.mark.django_db
def test_the_preview_of_a_real_registry_report_is_capped(compiler, event, data):
    definition = _order_report(
        Column(field="order.code"),
        Column(field="answer.tshirt-size", aggregate=Aggregate.JOIN),
    )
    with scopes_disabled():
        report = compiler.compile(definition, event, preview=True)
        assert "LIMIT" in str(report.queryset.query)
        assert report.count() == 2


# ---------------------------------------------------------------------------
# The guard against a registry that contradicts itself
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_declared_relation_that_the_path_does_not_cross_is_rejected(event):
    """``hints.aggregate_relation`` and ``orm_path`` have to describe one relation.

    Not reachable from a definition -- it needs a malformed ``ReportField`` -- so
    it is a ``FieldContractError``, the class reserved for "the registry itself is
    wrong". Without the check the compiler would quietly aggregate over a
    different relation than the one whose condition it applies.
    """
    field = ReportField(
        key="position.price",
        label="Position price",
        group="position",
        datatype=DataType.MONEY,
        bases=(Base.ORDER,),
        orm_path="total",
        filter_operators=(Operator.GT,),
        aggregates=(Aggregate.SUM,),
        extra={hints.EXTRA_AGGREGATE_RELATION: "all_positions"},
    )
    with pytest.raises(FieldContractError) as excinfo:
        columns_mod.relation_source(field, Order)
    assert "all_positions" in str(excinfo.value)


@pytest.mark.django_db
def test_a_condition_outside_the_chain_is_rejected(event):
    """A ``Q`` that does not run through the declared relation cannot be rebased.

    Dropping it would be the dangerous alternative: an aggregate silently missing
    its condition is the exact shape of the bug this module exists for.
    """
    chain = relations.relation_chain(Order, "all_positions__answers__answer")
    with pytest.raises(FieldContractError) as excinfo:
        relations.rebase_condition(chain, Q(invoices__number=1), "answer.x")
    assert "invoices__number" in str(excinfo.value)
