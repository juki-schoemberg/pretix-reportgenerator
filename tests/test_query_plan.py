"""Pass one: resolution, registry-stage validation, plan shape.

Owner: query-dev (ORCHESTRIERUNG.md section 5).

No database. Two registries are exercised on purpose (see
``tests/test_query_support.py``): the contractual
:class:`~pretix_custom_reports.contracts.stubs.StubFieldRegistry` for everything
that is about *accepting* or *rejecting* a definition, and the reference registry
with real ORM paths wherever the plan's expressions have to be inspectable.

``scopes_disabled`` appears wherever a plan is actually built: building an
aggregate subquery touches ``OrderPosition.objects``, which is a ``ScopedManager``
and wants a scope even though nothing is executed. That is deliberate -- see the
note in ``query/plan.py``. ``check_definition`` needs no scope at all, which is
what the provenance tests rely on.
"""

import pytest
from django.db.models import F, Q
from django_scopes import scopes_disabled

from pretix_custom_reports.contracts.definition import (
    BoolOp,
    Column,
    FilterCondition,
    FilterGroup,
    ReportDefinition,
    ReportOptions,
    SortEntry,
    validate_definition,
)
from pretix_custom_reports.contracts.errors import (
    CompilationError,
    FieldContractError,
    FieldResolutionError,
)
from pretix_custom_reports.contracts.fields import (
    Aggregate,
    Base,
    DataType,
    Operator,
    ReportField,
    SortDirection,
)
from pretix_custom_reports.contracts.stubs import StubFieldRegistry, stub_registry
from pretix_custom_reports.query import relations
from pretix_custom_reports.query.compiler import ReportQueryCompiler
from pretix_custom_reports.query.plan import build_plan, check_definition
from pretix_custom_reports.query.relations import (
    aggregate_expression,
    condition_signature,
)
from pretix_custom_reports.registry.annotations import MoneyField

from .test_query_support import (
    VALID_FIXTURES,
    FakeEvent,
    ReferenceRegistry,
    expectations,
    load_fixture,
    load_raw,
    required_field_keys,
)


@pytest.fixture
def event():
    return FakeEvent()


@pytest.fixture
def reference():
    return ReferenceRegistry()


def _plan(definition, event, registry, **kwargs):
    with scopes_disabled():
        return build_plan(definition, event, registry, **kwargs)


# ---------------------------------------------------------------------------
# Golden fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_golden_fixture_is_accepted_by_the_stub_registry(name, event):
    """Every golden fixture passes the registry-stage checks.

    Against the *stub* registry, because that is the contract's own stand-in and
    therefore the honest answer to "would the shipped field set accept this?".
    Only the checks -- the stub's annotations return ``{alias: None}`` by design
    and cannot produce SQL.
    """
    fields = check_definition(load_fixture(name), event, stub_registry())
    assert fields


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_golden_fixture_plans_against_real_orm_paths(name, event, reference):
    plan = _plan(load_fixture(name), event, reference)
    assert plan.columns
    assert plan.headers() == [c.label for c in plan.columns]
    assert plan.ordering, "a plan always ends with the primary key tiebreaker"


def test_portable_envelope_definition_plans(event, reference):
    from pretix_custom_reports.contracts.definition import validate_portable_document

    envelope = validate_portable_document(load_raw("portable/report_export.json"))
    plan = _plan(envelope.definition, event, reference)
    assert [c.key for c in plan.columns] == [
        "order.code",
        "position.attendee_name",
        "item.name",
        "answer.tshirt-size",
    ]


def test_reference_registry_covers_every_required_key(event, reference):
    """Guards the fixture, not the compiler.

    ``_index.json`` lists what the real registry must provide (ADR 0001 section
    10). If the reference registry drifts from that list, the compiler tests stop
    testing what the other agents are building against.
    """
    required = set(required_field_keys()["core"])
    for base in (Base.ORDER, Base.ORDERPOSITION):
        available = set(reference.get_fields(event, base))
        assert not required - available, sorted(required - available)


# ---------------------------------------------------------------------------
# invalid/ fixtures: the registry stage
# ---------------------------------------------------------------------------

_ERRORS = {
    "FieldResolutionError": FieldResolutionError,
    "CompilationError": CompilationError,
}


def _registry_stage_fixtures():
    return [
        (name, meta["error"])
        for name, meta in expectations().items()
        if meta["stage"] == "registry"
    ]


@pytest.mark.parametrize("name,error_name", _registry_stage_fixtures())
@pytest.mark.parametrize("registry_kind", ["stub", "reference"])
def test_registry_stage_fixture_raises_the_expected_error(
    name, error_name, registry_kind, event
):
    """The four fixtures that are structurally fine but must not compile.

    Both registries have to agree: this is the split ADR 0001 section 4 is built
    around, and it only holds if it does not depend on which registry is in play.
    """
    definition = validate_definition(load_raw(f"invalid/{name}"))
    registry = stub_registry() if registry_kind == "stub" else ReferenceRegistry()
    with pytest.raises(_ERRORS[error_name]):
        check_definition(definition, event, registry)


def test_structural_stage_fixtures_never_reach_the_registry():
    """The other thirteen must fail before a registry is even consulted."""
    from pretix_custom_reports.contracts.errors import DefinitionValidationError

    structural = [
        name for name, meta in expectations().items() if meta["stage"] == "structure"
    ]
    assert structural
    for name in structural:
        with pytest.raises(DefinitionValidationError):
            validate_definition(load_raw(f"invalid/{name}"))


def test_field_resolution_error_lists_every_missing_key(event):
    """An importer shows the user the whole list, not the first entry."""
    definition = validate_definition(load_raw("invalid/unknown_field_key.json"))
    with pytest.raises(FieldResolutionError) as excinfo:
        check_definition(definition, event, stub_registry())
    assert excinfo.value.keys == (
        "answer.question-that-was-renamed",
        "order.does_not_exist",
    )
    assert excinfo.value.base is Base.ORDER


def test_compilation_error_collects_every_problem(event):
    """Same reasoning for the second stage: report all of it at once."""
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(
            Column(field="position.price"),  # missing aggregate
            Column(field="order.code", aggregate=Aggregate.SUM),  # nothing to sum
        ),
        sorting=(SortEntry(field="position.price"),),  # not sortable
    )
    with pytest.raises(CompilationError) as excinfo:
        check_definition(definition, event, stub_registry())
    message = str(excinfo.value)
    assert "columns[0]" in message
    assert "columns[1]" in message
    assert "sorting[0]" in message


# ---------------------------------------------------------------------------
# Base switching (SPEC.md F3, ADR 0001 section 7)
# ---------------------------------------------------------------------------


def test_position_field_needs_an_aggregate_on_base_order(event):
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="position.price"),)
    )
    with pytest.raises(CompilationError) as excinfo:
        check_definition(definition, event, stub_registry())
    assert "aggregate" in str(excinfo.value)


def test_the_same_position_field_needs_no_aggregate_on_base_orderposition(
    event, reference
):
    definition = ReportDefinition(
        base=Base.ORDERPOSITION, columns=(Column(field="position.price"),)
    )
    plan = _plan(definition, event, reference)
    assert plan.columns[0].aggregate is None
    assert not plan.annotations, "a plain column on its own base needs no annotation"


def test_a_position_filter_needs_no_aggregate_on_base_order(event, reference):
    """ADR 0001 section 7a: "orders containing product X" is an EXISTS."""
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="order.code"),),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="item.name", operator=Operator.CONTAINS, value="Ticket"
                ),
            ),
        ),
    )
    plan = _plan(definition, event, reference)
    assert "EXISTS" in str(plan.filter_q).upper() or "Exists" in repr(plan.filter_q)


def test_aggregate_the_field_does_not_offer_is_rejected(event, reference):
    """On its own base a position field offers no aggregates at all."""
    definition = ReportDefinition(
        base=Base.ORDERPOSITION,
        columns=(Column(field="position.price", aggregate=Aggregate.SUM),),
    )
    with pytest.raises(CompilationError) as excinfo:
        check_definition(definition, event, reference)
    assert "does not support aggregate" in str(excinfo.value)


def test_aggregate_on_a_single_valued_field_is_rejected(event):
    """Aggregating something that is already one value per row is nonsense.

    Needs a registry that *permits* the aggregate while the path is single-valued
    -- otherwise the "not offered" check above fires first. A registry could
    plausibly get this wrong by copying the aggregate list across both bases.
    """
    permissive = ReportField(
        key="position.price",
        label="Position price",
        group="position",
        datatype=DataType.MONEY,
        bases=(Base.ORDERPOSITION,),
        orm_path="price",
        aggregates=(Aggregate.SUM,),  # the mistake: nothing to sum on this base
    )
    registry = ReferenceRegistry(overrides={"position.price": permissive})
    definition = ReportDefinition(
        base=Base.ORDERPOSITION,
        columns=(Column(field="position.price", aggregate=Aggregate.SUM),),
    )
    with pytest.raises(CompilationError) as excinfo:
        check_definition(definition, event, registry)
    assert "nothing to aggregate" in str(excinfo.value)


def test_unsupported_aggregate_is_rejected(event):
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="position.attendee_name", aggregate=Aggregate.SUM),),
    )
    with pytest.raises(CompilationError) as excinfo:
        check_definition(definition, event, stub_registry())
    assert "does not support aggregate" in str(excinfo.value)


def test_every_aggregate_of_the_fixture_produces_a_column(event, reference):
    """``order_with_aggregates.json`` exercises all seven aggregates."""
    plan = _plan(load_fixture("order_with_aggregates.json"), event, reference)
    used = {c.aggregate for c in plan.columns if c.aggregate is not None}
    assert used == {
        Aggregate.COUNT,
        Aggregate.COUNT_DISTINCT,
        Aggregate.SUM,
        Aggregate.MIN,
        Aggregate.MAX,
        Aggregate.AVG,
        Aggregate.JOIN,
    }


def test_same_field_with_four_aggregates_gets_four_distinct_annotations(
    event, reference
):
    """The double-counting trap: four sums must not share one alias."""
    plan = _plan(load_fixture("order_with_aggregates.json"), event, reference)
    price_columns = [c for c in plan.columns if c.key == "position.price"]
    assert len(price_columns) == 4
    assert len({c.aggregate for c in price_columns}) == 4


# ---------------------------------------------------------------------------
# Sorting (SPEC.md F7)
# ---------------------------------------------------------------------------


def test_sorting_keeps_the_declared_order_and_directions(event, reference):
    plan = _plan(load_fixture("multi_level_sorting.json"), event, reference)
    # Five declared stages plus the primary key tiebreaker.
    assert len(plan.ordering) == 6
    descending = [
        expression.descending for expression in plan.ordering  # type: ignore[attr-defined]
    ]
    assert descending == [False, True, False, False, False, False]


def test_sorting_always_ends_with_the_primary_key(event, reference):
    """Without a stable tiebreaker, pagination shows rows twice or not at all."""
    for name in VALID_FIXTURES:
        plan = _plan(load_fixture(name), event, reference)
        last = plan.ordering[-1]
        assert last.expression == F("pk"), name
        assert last.descending is False, name


def test_sorting_puts_nulls_last_in_both_directions(event, reference):
    """Backends disagree by default; leaving it to them makes output unstable."""
    plan = _plan(load_fixture("multi_level_sorting.json"), event, reference)
    for expression in plan.ordering[:-1]:
        assert expression.nulls_last is True
        assert expression.nulls_first is None


def test_sorting_by_a_non_sortable_field_is_rejected(event):
    definition = ReportDefinition(
        base=Base.ORDERPOSITION,
        columns=(Column(field="order.code"),),
        sorting=(SortEntry(field="payment.providers"),),
    )
    with pytest.raises(CompilationError) as excinfo:
        check_definition(definition, event, stub_registry())
    assert "not sortable" in str(excinfo.value)


def test_sorting_by_an_aggregate_is_rejected_even_if_the_registry_slips(event):
    """ADR 0001 section 7b, checked independently of the registry's own flag.

    A registry that wrongly marked a one-to-many field sortable would otherwise
    produce a join that multiplies rows -- silently, and only for orders with more
    than one position.
    """
    sloppy = ReportField(
        key="position.price",
        label="Position price",
        group="position",
        datatype=DataType.MONEY,
        bases=(Base.ORDER,),
        orm_path="all_positions__price",
        sortable=True,  # the mistake
        aggregates=(Aggregate.SUM,),
        requires_aggregate_on=(Base.ORDER,),
    )
    registry = ReferenceRegistry(overrides={"position.price": sloppy})
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="order.code"),),
        sorting=(SortEntry(field="position.price", direction=SortDirection.DESC),),
    )
    with pytest.raises(CompilationError) as excinfo:
        check_definition(definition, event, registry)
    assert "aggregat" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Columns and query optimisation
# ---------------------------------------------------------------------------


def test_hidden_columns_are_dropped(event, reference):
    """ADR 0001 section 9: the exporter must not have to filter."""
    definition = load_fixture("wide_order.json")
    assert any(column.hidden for column in definition.columns)
    plan = _plan(definition, event, reference)
    assert len(plan.columns) == len(definition.columns) - 1
    assert "order.comment" not in [c.key for c in plan.columns]


def test_a_report_whose_every_column_is_hidden_is_rejected(event, reference):
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="order.code", hidden=True),)
    )
    with pytest.raises(CompilationError) as excinfo:
        _plan(definition, event, reference)
    assert "hidden" in str(excinfo.value)


def test_select_related_follows_only_the_chosen_columns(event, reference):
    """Optimisation is driven by the columns actually selected (SPEC.md section 4)."""
    definition = ReportDefinition(
        base=Base.ORDERPOSITION,
        columns=(Column(field="order.code"), Column(field="item.name")),
    )
    plan = _plan(definition, event, reference)
    assert set(plan.select_related) == {"order", "item"}

    wider = definition.replace(
        columns=definition.columns + (Column(field="invoice_address.company"),)
    )
    plan = _plan(wider, event, reference)
    assert set(plan.select_related) == {"order", "item", "order__invoice_address"}


def test_no_select_related_for_annotation_backed_columns(event, reference):
    """An annotation alias is not a relation; joining for it would be waste."""
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="payment.sum_confirmed"),)
    )
    plan = _plan(definition, event, reference)
    assert plan.select_related == ()
    assert "pcr_payment_sum" in plan.annotations


def test_registry_annotations_are_only_added_for_used_fields(event, reference):
    """Annotating everything the registry knows would be a 60-subquery SELECT."""
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="order.code"),)
    )
    plan = _plan(definition, event, reference)
    assert plan.annotations == {}


def test_annotations_needed_by_a_filter_are_added(event, reference):
    """A filter on an annotation alias needs that annotation in the queryset."""
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="order.code"),),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="payment.sum_confirmed", operator=Operator.GT, value="0"
                ),
            ),
        ),
    )
    plan = _plan(definition, event, reference)
    assert "pcr_payment_sum" in plan.annotations
    assert "pcr_payment_sum" in plan.filter_annotations


def test_column_annotations_stay_out_of_the_count_query(event, reference):
    """The count query pays for filters only -- see query/report.py."""
    plan = _plan(load_fixture("wide_order.json"), event, reference)
    assert plan.annotations
    assert plan.filter_annotations == {}


def test_join_columns_share_one_prefetch_per_intermediate_level(event, reference):
    """Three joins over ``all_positions`` must not become three parent queries."""
    plan = _plan(load_fixture("order_with_aggregates.json"), event, reference)
    lookups = [p.prefetch_through for p in plan.prefetch_related]
    assert lookups.count("all_positions") == 3, lookups
    to_attrs = [p.to_attr for p in plan.prefetch_related]
    # Two leaf joins over all_positions itself get their own to_attr; the
    # unfiltered intermediate level for the answers chain is shared.
    assert to_attrs.count(None) == 1


def test_identical_join_columns_collapse_into_one_prefetch(event, reference):
    """S-005: the ``to_attr`` names the *rows wanted*, not the column asking.

    Twenty ``join`` columns over one relation with one condition want one list of
    positions. Before the fix each got ``pcr_c<index>`` as its ``to_attr``, which
    made ``_dedupe_prefetches`` structurally unable to merge anything.
    """
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="order.code"),)
        + tuple(
            Column(field="position.attendee_name", aggregate=Aggregate.JOIN)
            for _ in range(20)
        ),
    )
    plan = _plan(definition, event, reference)
    assert len(plan.prefetch_related) == 1
    assert plan.prefetch_related[0].to_attr.startswith("pcr_j")


def test_the_join_prefetch_name_does_not_depend_on_the_column_position(
    event, reference
):
    """The same column in two reports gets the same prefetch name.

    A name minted from the column index is unique by construction and therefore
    useless as a de-duplication key -- that was the whole of S-005. Pinned here so
    that a future refactoring cannot quietly go back to indexing.
    """
    join = Column(field="position.attendee_name", aggregate=Aggregate.JOIN)
    first = _plan(
        ReportDefinition(base=Base.ORDER, columns=(join, Column(field="order.code"))),
        event,
        reference,
    )
    later = _plan(
        ReportDefinition(
            base=Base.ORDER,
            columns=(
                Column(field="order.code"),
                Column(field="order.email"),
                Column(field="order.total"),
                join,
            ),
        ),
        event,
        reference,
    )
    assert first.prefetch_related[0].to_attr == later.prefetch_related[0].to_attr


def test_join_columns_with_different_conditions_stay_separate(event, reference):
    """Two questions, two prefetches -- merging them would swap the answers."""
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(
            Column(field="answer.tshirt-size", aggregate=Aggregate.JOIN),
            Column(field="answer.arrival-date", aggregate=Aggregate.JOIN),
        ),
    )
    plan = _plan(definition, event, reference)
    to_attrs = [p.to_attr for p in plan.prefetch_related]
    # One shared intermediate level over ``all_positions`` plus one leaf each.
    assert to_attrs.count(None) == 1
    leaves = [attr for attr in to_attrs if attr is not None]
    assert len(leaves) == 2 and len(set(leaves)) == 2


def test_join_columns_that_need_different_select_related_stay_separate(
    event, reference
):
    """Sharing here would trade one query for one item lookup per position."""
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(
            Column(field="item.name", aggregate=Aggregate.JOIN),
            Column(field="position.attendee_name", aggregate=Aggregate.JOIN),
        ),
    )
    plan = _plan(definition, event, reference)
    assert len({p.to_attr for p in plan.prefetch_related}) == 2


def test_join_columns_reading_different_fields_of_one_prefetch_share_it(
    event, reference
):
    """Same rows, same ``select_related``: the tail is walked in Python."""
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(
            Column(field="position.attendee_name", aggregate=Aggregate.JOIN),
            Column(field="position.attendee_email", aggregate=Aggregate.JOIN),
        ),
    )
    plan = _plan(definition, event, reference)
    assert len(plan.prefetch_related) == 1


# ---------------------------------------------------------------------------
# The output field of a money aggregate (T-002)
# ---------------------------------------------------------------------------


def _declared_output_field(expression):
    """The ``output_field`` the expression was *given*, or ``None``.

    Read out of the instance dict rather than through ``expression.output_field``
    on purpose: the property resolves the field from the source expressions, and
    a bare ``Sum("price")`` cannot do that outside a query. What is interesting
    here is exactly the difference between "was told" and "will work it out".
    """
    return expression.__dict__.get("output_field")


@pytest.mark.parametrize(
    "aggregate", [Aggregate.SUM, Aggregate.MIN, Aggregate.MAX, Aggregate.AVG]
)
def test_a_money_aggregate_carries_the_money_output_field(aggregate):
    """Without it the scale is lost in the converter, not in the SQL (T-002)."""
    output = _declared_output_field(
        aggregate_expression(aggregate, "price", DataType.MONEY)
    )
    assert isinstance(output, MoneyField)
    assert (output.max_digits, output.decimal_places) == (13, 2)


@pytest.mark.parametrize("aggregate", [Aggregate.COUNT, Aggregate.COUNT_DISTINCT])
def test_a_counting_aggregate_gets_no_money_output_field(aggregate):
    """A cardinality has no scale, and "3.00 positions" is not an improvement."""
    assert aggregate not in relations.MONEY_AGGREGATES
    expression = aggregate_expression(aggregate, "price", DataType.MONEY)
    assert _declared_output_field(expression) is None


@pytest.mark.parametrize("datatype", [None, DataType.INTEGER, DataType.DECIMAL])
def test_a_non_money_aggregate_is_left_as_it_was(datatype):
    """The registry's declaration decides, not "it happens to be numeric"."""
    expression = aggregate_expression(Aggregate.SUM, "price", datatype)
    assert _declared_output_field(expression) is None


def test_each_money_aggregate_gets_its_own_output_field_instance():
    """A ``Field`` picks up state once it is attached; sharing one is a trap."""
    first = aggregate_expression(Aggregate.SUM, "price", DataType.MONEY)
    second = aggregate_expression(Aggregate.SUM, "price", DataType.MONEY)
    assert _declared_output_field(first) is not _declared_output_field(second)


def test_an_unknown_aggregate_is_a_compilation_error():
    """``JOIN`` never becomes SQL: it is a prefetch plus a Python join."""
    with pytest.raises(CompilationError):
        aggregate_expression(Aggregate.JOIN, "price", DataType.MONEY)


# ---------------------------------------------------------------------------
# The condition signature the de-duplication rests on
# ---------------------------------------------------------------------------


def test_two_separately_built_equal_conditions_sign_equally():
    """The property the whole de-duplication needs.

    ``Q`` has no canonical form and no useful ``__hash__``; two aggregate filters
    for the same field are built twice by the same code and have to come out
    equal.
    """
    left = Q(canceled=False) & Q(question=17)
    right = Q(canceled=False) & Q(question=17)
    assert left is not right
    assert condition_signature(left) == condition_signature(right)


@pytest.mark.parametrize(
    "other",
    [
        Q(canceled=False) & Q(question=18),
        Q(canceled=True) & Q(question=17),
        Q(canceled=False) | Q(question=17),
        ~(Q(canceled=False) & Q(question=17)),
        Q(canceled=False),
        Q(canceled="False") & Q(question=17),
        Q(canceled=False) & Q(question="17"),
    ],
)
def test_a_different_condition_signs_differently(other):
    """Including the ones that only *look* the same.

    ``False`` and ``"False"`` are one character apart in a ``str(Q)`` and select
    different rows. The signature has to tell them apart, or two columns get
    merged that address different data.
    """
    reference = Q(canceled=False) & Q(question=17)
    assert condition_signature(reference) != condition_signature(other)


def test_no_condition_is_itself_a_condition():
    """No condition at all must not collide with an empty one."""
    assert condition_signature(None) is not None
    assert condition_signature(None) != condition_signature(Q())


def test_a_condition_holding_an_object_gets_no_signature(event):
    """Refusing to sign is how the fallback is triggered.

    Two model instances can share a ``str()`` and address different rows, so a
    condition containing one is treated as unrepresentable and its columns are
    never merged. ``FakeEvent`` stands in for any such object -- what matters is
    that it is not a scalar.
    """
    assert condition_signature(Q(item=event)) is None
    assert condition_signature(Q(canceled=False) & Q(item=event)) is None
    assert condition_signature(Q(item__in=[1, event])) is None


def test_a_list_of_scalars_still_signs():
    """``in`` lookups are the common case and must not lose the saving."""
    assert condition_signature(Q(status__in=["p", "n"])) is not None
    assert condition_signature(Q(status__in=["p", "n"])) == condition_signature(
        Q(status__in=["p", "n"])
    )
    assert condition_signature(Q(status__in=["p", "n"])) != condition_signature(
        Q(status__in=["n", "p"])
    )
    assert condition_signature(Q(status__in={"p", "n"})) == condition_signature(
        Q(status__in={"n", "p"})
    )


def test_a_registry_alias_may_not_look_like_a_compiler_alias(event):
    """Otherwise one column could silently show another column's value."""
    collides = ReportField(
        key="order.code",
        label="Order code",
        group="order",
        datatype=DataType.STRING,
        bases=(Base.ORDER,),
        orm_path="pcr_c0",
        annotation=lambda ctx: {"pcr_c0": F("code")},
    )
    registry = ReferenceRegistry(overrides={"order.code": collides})
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="order.code"),)
    )
    with pytest.raises(FieldContractError) as excinfo:
        _plan(definition, event, registry)
    assert "reserved" in str(excinfo.value)


def test_a_stub_annotation_fails_loudly_rather_than_silently(event):
    """The stub cannot build SQL, and the error has to say so."""
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="meta.event.campaign"),)
    )
    with pytest.raises(FieldContractError) as excinfo:
        _plan(definition, event, stub_registry())
    assert "stubs.py" in str(excinfo.value)


def test_label_override_wins_over_the_field_label(event, reference):
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="order.code", label="Booking"),)
    )
    plan = _plan(definition, event, reference)
    assert plan.headers() == ["Booking"]


def test_options_are_carried_into_the_plan(event, reference):
    plan = _plan(load_fixture("options_full.json"), event, reference)
    assert plan.include_canceled_positions is True
    assert plan.include_testmode_orders is True
    assert plan.row_limit == 5000


def test_testmode_orders_are_excluded_unless_asked_for(event, reference):
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="order.code"),)
    )
    plan = _plan(definition, event, reference)
    assert plan.filter_q == Q(testmode=False)

    allowed = definition.replace(options=ReportOptions(include_testmode_orders=True))
    plan = _plan(allowed, event, reference)
    assert plan.filter_q is None


def test_testmode_exclusion_uses_the_right_path_per_base(event, reference):
    definition = ReportDefinition(
        base=Base.ORDERPOSITION, columns=(Column(field="order.code"),)
    )
    plan = _plan(definition, event, reference)
    assert plan.filter_q == Q(order__testmode=False)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_a_key_resolvable_only_via_resolve_is_accepted(event):
    """``resolve`` gets a second chance after ``get_fields``.

    A registry may legitimately resolve a deprecated alias it does not list.
    """

    class Narrow(StubFieldRegistry):
        def get_fields(self, event, base):
            fields = dict(super().get_fields(event, base))
            self._hidden = fields.pop("order.code")
            return fields

        def resolve(self, key, event, base):
            fields = self.get_fields(event, base)
            if key in fields:
                return fields[key]
            return self._hidden if key == "order.code" else None

    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="order.code"),)
    )
    assert "order.code" in check_definition(definition, event, Narrow())


def test_a_field_that_does_not_support_the_base_is_rejected(event):
    wrong_base = ReportField(
        key="order.code",
        label="Order code",
        group="order",
        datatype=DataType.STRING,
        bases=(Base.ORDERPOSITION,),
        orm_path="code",
    )
    registry = ReferenceRegistry(overrides={"order.code": wrong_base})
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="order.code"),)
    )
    with pytest.raises(CompilationError) as excinfo:
        check_definition(definition, event, registry)
    assert "not available on base" in str(excinfo.value)


def test_compiler_plan_and_build_plan_agree(event, reference):
    definition = load_fixture("minimal_order.json")
    with scopes_disabled():
        direct = build_plan(definition, event, reference)
        via_class = ReportQueryCompiler(reference).plan(definition, event)
    assert [c.key for c in direct.columns] == [c.key for c in via_class.columns]
