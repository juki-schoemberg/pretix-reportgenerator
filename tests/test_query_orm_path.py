"""Where an ORM path comes from, and where it provably cannot come from.

Owner: query-dev (ORCHESTRIERUNG.md section 5).

CLAUDE.md rule 2 and SPEC.md section 4 say the same thing twice: ORM paths,
lookups and operators come from the registry, never from stored or imported JSON.
A definition is untrusted input.

That is easy to *claim*. These tests try to make it checkable, from three angles:

1. **Positive provenance.** Compile one definition against two registries that
   disagree about a field's ``orm_path``, and show the generated SQL follows the
   registry. If the path came from the definition, the two would be identical.
2. **Nothing can be smuggled in.** The grammar of a field key forbids ``__``, a
   column has no ``orm_path`` member, and an unknown key is rejected -- each with a
   test, including hand-built definitions that bypass the JSON validator entirely.
3. **Nothing reaches the ORM before the check.** The registry-stage check runs
   before any queryset exists, so a rejected definition costs zero queries. That is
   asserted rather than argued.
"""

import pathlib
import pytest
from django.core.exceptions import FieldError
from django.db.models import F
from django_scopes import scopes_disabled

from pretix_custom_reports.contracts.definition import (
    BoolOp,
    Column,
    FilterCondition,
    FilterGroup,
    ReportDefinition,
    SortEntry,
    validate_definition,
)
from pretix_custom_reports.contracts.errors import (
    CompilationError,
    DefinitionValidationError,
    FieldResolutionError,
)
from pretix_custom_reports.contracts.fields import (
    Aggregate,
    Base,
    DataType,
    Operator,
    ReportField,
    validate_key,
)
from pretix_custom_reports.query import relations
from pretix_custom_reports.query.compiler import ReportQueryCompiler
from pretix_custom_reports.query.plan import check_definition

from .test_query_support import FakeEvent, ReferenceRegistry, load_raw

#: A definition that looks completely ordinary. Everything technical about it has
#: to come from whichever registry is handed in.
PLAIN = ReportDefinition(
    base=Base.ORDER,
    columns=(Column(field="order.code"),),
    filters=FilterGroup(
        op=BoolOp.AND,
        children=(
            FilterCondition(field="order.code", operator=Operator.EXACT, value="ABC12"),
        ),
    ),
    sorting=(SortEntry(field="order.code"),),
)


def _field(orm_path: str) -> ReportField:
    return ReportField(
        key="order.code",
        label="Order code",
        group="order",
        datatype=DataType.STRING,
        bases=(Base.ORDER,),
        orm_path=orm_path,
        filter_operators=(Operator.EXACT,),
        sortable=True,
    )


def _sql_for(orm_path: str, event) -> str:
    """Compile :data:`PLAIN` with ``order.code`` bound to *orm_path*."""
    registry = ReferenceRegistry(overrides={"order.code": _field(orm_path)})
    with scopes_disabled():
        report = ReportQueryCompiler(registry).compile(PLAIN, event)
        return str(report.queryset.query)


# ---------------------------------------------------------------------------
# 1. The path demonstrably follows the registry
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_registry_decides_which_column_is_read(event):
    """One definition, two registries, two different SQL statements.

    The definition says ``order.code`` both times. If the ORM path came from the
    definition -- or were derived from the key -- these would be the same query.
    """
    using_code = _sql_for("code", event)
    using_email = _sql_for("email", event)

    assert '"code"' in using_code
    assert '"email"' in using_email
    assert using_code != using_email


@pytest.mark.django_db
def test_the_registry_decides_what_is_filtered_and_sorted(event):
    sql = _sql_for("email", event)
    # Plain names, not expressions, inside the slices: black and flake8 disagree
    # about the spacing of ``sql[a() : b()]`` (E203) and both have to pass.
    start, end = sql.index("WHERE"), sql.index("ORDER BY")
    where = sql[start:end]
    assert '"email" = ' in where
    assert '"code" = ' not in where
    assert '"email"' in sql[end:]


@pytest.mark.django_db
def test_the_field_key_itself_never_becomes_a_lookup(event):
    """``order.code`` is a registry key, not ``order__code`` and not a column."""
    sql = _sql_for("email", event)
    assert "order.code" not in sql
    assert "order_code" not in sql


@pytest.mark.django_db
def test_a_relation_path_from_the_registry_is_honoured_verbatim(event):
    registry = ReferenceRegistry(
        overrides={"order.code": _field("invoice_address__company")}
    )
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="order.code"),)
    )
    with scopes_disabled():
        plan = ReportQueryCompiler(registry).plan(definition, event)
    assert plan.select_related == ("invoice_address",)


# ---------------------------------------------------------------------------
# 2. Nothing can be smuggled in
# ---------------------------------------------------------------------------


def test_the_smuggled_orm_path_fixture_is_rejected_structurally():
    """``invalid/smuggled_orm_path.json``: the attack this design is built against.

    A double underscore in a field key and an extra ``orm_path`` member on a
    column. Both are refused before a registry is consulted, let alone a database.
    """
    with pytest.raises(DefinitionValidationError) as excinfo:
        validate_definition(load_raw("invalid/smuggled_orm_path.json"))
    codes = set(excinfo.value.codes)
    assert "invalid_field_key" in codes
    assert "unknown_key" in codes


def test_a_column_has_no_place_to_put_an_orm_path():
    """Structural, not behavioural: the dataclass has no such member."""
    assert not hasattr(Column(field="order.code"), "orm_path")
    assert "orm_path" not in set(Column.__dataclass_fields__)


@pytest.mark.parametrize(
    "key",
    [
        "order.event__organizer__slug",
        "order.code__icontains",
        "position.order__event__settings",
        "answer.a__b",
    ],
)
def test_a_key_containing_a_lookup_separator_is_not_a_valid_key(key):
    """The first of two independent layers (ADR 0001 section 2)."""
    with pytest.raises(ValueError) as excinfo:
        validate_key(key)
    assert "double underscore" in str(excinfo.value)


@pytest.mark.parametrize(
    "key",
    [
        "order.event__organizer__slug",
        "order.secret",
        "order.internal_secret",
        "invoice_address.vat_id_validated",
        "position.web_secret",
    ],
)
def test_a_hand_built_definition_with_an_unknown_key_is_refused(key):
    """The second layer, reached by bypassing the JSON validator completely.

    ``ReportDefinition`` is a plain dataclass, so a caller *can* construct one with
    a key the grammar would have rejected -- an importer with a bug, a future
    refactoring, a test. The registry allow-list catches it either way.
    """
    definition = ReportDefinition(base=Base.ORDER, columns=(Column(field=key),))
    with pytest.raises(FieldResolutionError) as excinfo:
        check_definition(definition, FakeEvent(), ReferenceRegistry())
    assert key in excinfo.value.keys


def test_an_unknown_key_in_a_filter_is_refused_too():
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="order.code"),),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="order.event__organizer__slug",
                    operator=Operator.EXACT,
                    value="x",
                ),
            ),
        ),
    )
    with pytest.raises(FieldResolutionError):
        check_definition(definition, FakeEvent(), ReferenceRegistry())


def test_an_unknown_key_in_sorting_is_refused_too():
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="order.code"),),
        sorting=(SortEntry(field="order.does_not_exist"),),
    )
    with pytest.raises(FieldResolutionError):
        check_definition(definition, FakeEvent(), ReferenceRegistry())


def test_a_key_that_happens_to_name_a_model_field_is_still_refused():
    """Resolution is an allow-list, not a guess.

    ``Order.secret`` exists on the model. The compiler must not care: if the
    registry does not offer it, there is no such report field. This is the
    difference between a hand-curated field list and ``Model._meta`` introspection
    (ADR 0001 section 2, rejected alternatives).
    """
    from pretix.base.models import Order

    assert Order._meta.get_field("secret")
    definition = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="order.secret"),)
    )
    with pytest.raises(FieldResolutionError):
        check_definition(definition, FakeEvent(), ReferenceRegistry())


def test_an_operator_the_field_does_not_offer_cannot_be_forced():
    """Operators are an allow-list per field, not a global set.

    Also covers the registry's documented narrowings: ``answer.*`` on base
    ``order`` and ``computed.order_status_label`` ship ``filter_operators=()``, and
    a filter on them has to be a CompilationError like any other.
    """
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(Column(field="order.code"),),
        filters=FilterGroup(
            op=BoolOp.AND,
            children=(
                FilterCondition(
                    field="order.total", operator=Operator.CONTAINS, value="1"
                ),
            ),
        ),
    )
    with pytest.raises(CompilationError):
        check_definition(definition, FakeEvent(), ReferenceRegistry())


def test_a_field_with_no_filter_operators_at_all_cannot_be_filtered():
    locked = ReportField(
        key="order.code",
        label="Order code",
        group="order",
        datatype=DataType.STRING,
        bases=(Base.ORDER,),
        orm_path="code",
        filter_operators=(),
    )
    registry = ReferenceRegistry(overrides={"order.code": locked})
    with pytest.raises(CompilationError) as excinfo:
        check_definition(PLAIN, FakeEvent(), registry)
    assert "not allowed for" in str(excinfo.value)


def test_no_module_in_the_query_package_uses_eval_or_raw_sql():
    """Blunt but cheap. SPEC.md section 4 forbids all of these by name."""
    import pretix_custom_reports.query as package

    forbidden = ("eval(", "exec(", ".raw(", "RawSQL", ".extra(")
    root = pathlib.Path(package.__file__).parent
    checked = 0
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{path.name} contains {needle!r}"
        checked += 1
    assert checked >= 8


# ---------------------------------------------------------------------------
# 3. The registry stage runs before anything is built
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_check_definition_builds_no_queryset(django_assert_num_queries):
    """It needs no database, no event object and no django-scopes scope.

    All three matter: a scope error would prove it had reached a manager, a query
    would prove it had reached the database.
    """
    with django_assert_num_queries(0):
        fields = check_definition(PLAIN, FakeEvent(), ReferenceRegistry())
    assert "order.code" in fields


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["unknown_field_key.json", "field_type_conflict.json"])
def test_a_rejected_definition_costs_zero_queries(name, django_assert_num_queries):
    definition = validate_definition(load_raw(f"invalid/{name}"))
    with django_assert_num_queries(0):
        with pytest.raises((FieldResolutionError, CompilationError)):
            check_definition(definition, FakeEvent(), ReferenceRegistry())


@pytest.mark.django_db
def test_relation_introspection_runs_without_a_database(django_assert_num_queries):
    """``_meta`` walking is introspection, not access."""
    with django_assert_num_queries(0):
        chain = relations.relation_chain(
            relations.base_model_for(Base.ORDER), "all_positions__answers__answer"
        )
    assert [hop.accessor for hop in chain.hops] == ["all_positions", "answers"]
    assert chain.correlation_path == "orderposition__order"
    assert chain.remainder == "answer"


@pytest.mark.django_db
def test_an_unresolvable_path_from_the_registry_fails_loudly(event):
    """A registry bug must not degrade into a silently empty column.

    If the registry hands over a path no model knows, the report has to break. It
    is a programming error, and swallowing it would turn a broken field into an
    invisible one -- the one failure nobody notices, because a column full of
    blanks looks like missing data.

    *Where* it breaks depends on the strategy the compiler picked for that path,
    and there is deliberately no up-front path check that would make it uniform:
    ``contracts/stubs.py`` declares fictional paths (``pcnt``, ``payment_sum``,
    ``checkin_count``) with no annotation behind them, so a compiler that rejected
    unresolvable paths at plan time would reject the frozen contract's own stub
    registry. Instead every strategy is loud on its own, which is what the three
    cases below pin down. Pass one stays happy throughout: it only checks what the
    registry *declares*.
    """
    from pretix.base.models import Order

    broken = ReferenceRegistry(overrides={"order.code": _field("no_such_column")})
    column_only = ReportDefinition(
        base=Base.ORDER, columns=(Column(field="order.code"),)
    )
    with scopes_disabled():
        check_definition(column_only, event, broken)

        # 1. Filtered or sorted: Django resolves lookups when they are added, so
        #    the queryset cannot even be built. PLAIN does both.
        with pytest.raises(FieldError):
            _sql_for("no_such_column", event)

        # 2. A multi-segment column becomes an F() annotation, which Django
        #    resolves just as eagerly -- so this one does not even need a filter.
        deep = ReferenceRegistry(
            overrides={"order.code": _field("invoice_address__no_such_column")}
        )
        with pytest.raises(FieldError):
            ReportQueryCompiler(deep).compile(column_only, event)

        # 3. A single-segment column is read off the row object, so the SQL is
        #    valid and the break happens in the renderer. It has to be an
        #    exception and not ``None``; see ``_attribute_renderer`` in
        #    query/columns.py, which catches missing *relations* only.
        report = ReportQueryCompiler(broken).compile(column_only, event)
        str(report.queryset.query)
        with pytest.raises(AttributeError):
            report.columns[0].render(Order())


@pytest.mark.django_db
def test_the_event_filter_is_present_in_every_compiled_queryset(event):
    """CLAUDE.md rule 4, asserted rather than assumed -- for both bases and for
    the count query, which is easy to forget because nobody looks at its rows."""
    for base in (Base.ORDER, Base.ORDERPOSITION):
        definition = ReportDefinition(base=base, columns=(Column(field="order.code"),))
        with scopes_disabled():
            report = ReportQueryCompiler(ReferenceRegistry()).compile(definition, event)
            sql = str(report.queryset.query)
            count_sql = str(report.count_queryset.query)
        assert "event_id" in sql, base
        assert "event_id" in count_sql, base


@pytest.mark.django_db
def test_compiler_generated_aliases_cannot_be_confused_with_lookups(event):
    """A generated alias never contains ``__``, so it cannot be read as a path."""
    definition = ReportDefinition(
        base=Base.ORDER,
        columns=(
            Column(field="order.code"),
            Column(field="position.price", aggregate=Aggregate.SUM),
        ),
    )
    with scopes_disabled():
        plan = ReportQueryCompiler(ReferenceRegistry()).plan(definition, event)
    assert [alias for alias in plan.annotations if alias.startswith("pcr_c")]
    for alias in plan.annotations:
        assert "__" not in alias


@pytest.mark.django_db
def test_a_sort_expression_is_an_f_object_not_a_built_string(event):
    """No ``"-" + path`` string concatenation anywhere in the ordering."""
    with scopes_disabled():
        plan = ReportQueryCompiler(ReferenceRegistry()).plan(PLAIN, event)
    assert plan.ordering
    for expression in plan.ordering:
        assert isinstance(expression.expression, F)
