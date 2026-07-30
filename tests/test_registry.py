# Owner: registry-dev (tests/test_registry*.py, ORCHESTRIERUNG.md section 5)
#
# The registry is the plugin's allow-list: a stored or imported definition only
# ever carries field keys, and this is the only place a key becomes an ORM path.
# The tests are therefore split into three groups, and the second one is the
# security-relevant one:
#
#   1. completeness  -- every key the golden fixtures use resolves
#   2. tightness     -- nothing else does, and nothing internal is exposed
#   3. correctness   -- the annotations actually run and return what they claim
"""Tests for the field registry: core fields, questions, meta, computed fields."""

import datetime
import pytest
from decimal import Decimal
from django_scopes import scope, scopes_disabled
from pretix.base.models import Order, OrderPosition, Question, QuestionAnswer

from pretix_custom_reports.contracts import (
    NS_PLUGIN,
    PROVIDER_CORE,
    RESERVED_NAMESPACES,
    Aggregate,
    Base,
    DataType,
    Operator,
    ReportField,
    find_unresolved_fields,
    validate_definition_json,
    validate_key,
)
from pretix_custom_reports.registry import annotations, cache as registry_cache
from pretix_custom_reports.registry.library import EventFieldRegistry, field_registry
from pretix_custom_reports.registry.questions import AGE_KEY_PREFIX
from tests import test_registry_support as support
from tests.test_registry_support import (
    answer,
    load_index,
    make_order,
    valid_fixture_paths,
)

# Fixtures live in the support module because three test modules need them and
# tests/conftest.py belongs to test-engineer. Re-bound by assignment rather than
# imported so that a test function parameter of the same name is not a
# "redefinition of an unused import" to pyflakes.
registry = support.registry
event_questions = support.event_questions
event_meta = support.event_meta

BASES = (Base.ORDER, Base.ORDERPOSITION)


# ---------------------------------------------------------------------------
# 1. Completeness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base", BASES)
def test_required_core_keys_resolve(registry, event, base):
    """Every ``required_field_keys.core`` entry from _index.json exists.

    ADR 0001 section 10 makes this binding: if one of these keys is missing the
    golden fixtures stop being a shared test basis and each agent silently gets
    their own.
    """
    with scopes_disabled():
        fields = registry.get_fields(event, base)
    missing = [
        key for key in load_index()["required_field_keys"]["core"] if key not in fields
    ]
    assert missing == []


@pytest.mark.parametrize("base", BASES)
def test_required_question_keys_resolve(registry, event, event_questions, base):
    """``answer.<identifier>`` exists for each promised question identifier."""
    index = load_index()["required_field_keys"]["questions"]
    with scopes_disabled():
        fields = registry.get_fields(event, base)
    missing = [
        f"answer.{identifier}"
        for identifier in index["identifiers"]
        if f"answer.{identifier}" not in fields
    ]
    assert missing == []


def test_required_meta_key_resolves(registry, event, event_meta):
    """``meta.event.campaign`` exists once the organizer defines the property."""
    with scopes_disabled():
        field = registry.resolve("meta.event.campaign", event, Base.ORDER)
    assert field is not None
    assert field.provider == PROVIDER_CORE


def test_question_datatypes_match_the_index(registry, event, event_questions):
    """The datatypes _index.json promises are the ones the registry publishes.

    ``arrival-date`` is a date and ``newsletter`` a boolean even though pretix
    stores every answer in a ``TextField``; the boolean is normalised in SQL, the
    date works because ISO strings sort correctly (see registry/questions.py).
    """
    with scopes_disabled():
        fields = registry.get_fields(event, Base.ORDERPOSITION)
    assert fields["answer.tshirt-size"].datatype is DataType.CHOICE
    assert fields["answer.arrival-date"].datatype is DataType.DATE
    assert fields["answer.newsletter"].datatype is DataType.BOOLEAN


@pytest.mark.parametrize("path", valid_fixture_paths(), ids=lambda p: p.name)
def test_valid_fixture_resolves_completely(
    registry, event, event_questions, event_meta, path
):
    """Every field reference of every valid golden fixture resolves.

    ``plugin_and_meta_fields.json`` is the exception that proves the rule: it
    references ``plugin.pretix_demo.demo_value``, which no plugin provides here,
    so exactly that one key is expected to be missing. The signal side of it is
    covered in test_registry_signal.py, where the example plugin is connected.
    """
    definition = validate_definition_json(path.read_text(encoding="utf-8"))
    with scopes_disabled():
        unresolved = find_unresolved_fields(definition, registry, event)
    unresolved_keys = sorted({reference.key for reference in unresolved})
    if path.name == "plugin_and_meta_fields.json":
        assert unresolved_keys == ["plugin.pretix_demo.demo_value"]
    else:
        assert unresolved_keys == []


@pytest.mark.parametrize("path", valid_fixture_paths(), ids=lambda p: p.name)
def test_valid_fixture_usage_is_permitted(
    registry, event, event_questions, event_meta, path
):
    """The fixtures do not just resolve, they use the fields in a legal way.

    This is the check the query compiler performs in stage two: mandatory
    aggregates, permitted aggregates, permitted operators, sortability. Running
    it here means a golden fixture cannot pass resolution and then fail
    compilation for a registry reason.
    """
    from pretix_custom_reports.contracts import FieldUsage

    definition = validate_definition_json(path.read_text(encoding="utf-8"))
    problems = []
    with scopes_disabled():
        for reference in definition.iter_field_references():
            field = registry.resolve(reference.key, event, definition.base)
            if field is None:
                continue  # covered by the previous test
            if reference.usage is FieldUsage.COLUMN:
                if (
                    field.needs_aggregate_on(definition.base)
                    and reference.aggregate is None
                ):
                    problems.append(f"{reference.path}: needs an aggregate")
                if reference.aggregate is not None and not field.allows_aggregate(
                    reference.aggregate
                ):
                    problems.append(
                        f"{reference.path}: aggregate {reference.aggregate} not allowed"
                    )
            elif reference.usage is FieldUsage.FILTER:
                if reference.operator is not None and not field.allows_operator(
                    reference.operator
                ):
                    problems.append(
                        f"{reference.path}: operator {reference.operator} not allowed"
                    )
            elif reference.usage is FieldUsage.SORT and not field.sortable:
                problems.append(f"{reference.path}: not sortable")
    assert problems == []


# ---------------------------------------------------------------------------
# 2. Tightness
# ---------------------------------------------------------------------------


def test_key_from_invalid_fixture_does_not_resolve(registry, event, event_questions):
    """The keys in invalid/unknown_field_key.json stay unresolvable.

    Both of them are structurally impeccable, which is the point: only the
    registry can tell that ``order.does_not_exist`` is not a field and that
    ``answer.question-that-was-renamed`` no longer matches any
    ``Question.identifier``. This is the test the definition of done asks for.
    """
    definition = validate_definition_json(
        (
            valid_fixture_paths()[0].parent / "invalid" / "unknown_field_key.json"
        ).read_text(encoding="utf-8")
    )
    with scopes_disabled():
        unresolved = find_unresolved_fields(definition, registry, event)
        assert registry.resolve("order.does_not_exist", event, Base.ORDER) is None
        assert (
            registry.resolve("answer.question-that-was-renamed", event, Base.ORDER)
            is None
        )
    assert sorted({reference.key for reference in unresolved}) == [
        "answer.question-that-was-renamed",
        "order.does_not_exist",
    ]


@pytest.mark.parametrize(
    "key",
    [
        "order.event__organizer__slug",
        "order.event__settings__secret",
        "position.order__event__organizer__slug",
        "order.code__icontains",
    ],
)
def test_smuggled_orm_path_does_not_resolve(registry, event, key):
    """A key that looks like an ORM path resolves to nothing, and never raises.

    The double underscore ban (ADR 0001 section 2) already rejects these at the
    grammar level. The registry is the second, independent layer: even if the
    grammar check were ever bypassed, there is no such entry in the table.
    """
    with scopes_disabled():
        for base in BASES:
            assert registry.resolve(key, event, base) is None


@pytest.mark.parametrize(
    "key",
    [
        "order.secret",
        "order.internal_secret",
        "position.secret",
        "position.web_secret",
        "order.meta_info",
        "position.meta_info",
        "order.organizer",
        "position.organizer",
    ],
)
def test_internal_columns_are_not_exposed(registry, event, key):
    """Ticket secrets and internal columns have no field, on either base.

    The reason the core table is written by hand rather than derived from
    ``Model._meta``: automatic introspection would publish exactly these.
    """
    with scopes_disabled():
        for base in BASES:
            assert registry.resolve(key, event, base) is None


@pytest.mark.parametrize("base", BASES)
def test_no_field_key_contains_a_double_underscore(
    registry, event, event_questions, base
):
    """Not one published key looks like a multi-level lookup."""
    with scopes_disabled():
        keys = registry.keys(event, base)
    assert keys
    for key in keys:
        assert "__" not in key
        validate_key(key)


@pytest.mark.parametrize("base", BASES)
def test_core_fields_never_claim_a_plugin_provider(registry, event, base):
    """Reserved namespace implies ``provider == "core"`` and vice versa."""
    with scopes_disabled():
        fields = registry.get_fields(event, base)
    for key, field in fields.items():
        namespace = key.split(".", 1)[0]
        if namespace in RESERVED_NAMESPACES:
            assert field.provider == PROVIDER_CORE, key
        else:
            assert namespace == NS_PLUGIN, key
            assert field.provider != PROVIDER_CORE, key


@pytest.mark.parametrize("base", BASES)
def test_declaration_families_are_not_mixed(
    registry, event, event_questions, event_meta, base
):
    """Each field declares one thing: a path, a path plus annotation, or Python.

    The rule from the brief ("orm_path OR annotation OR value_getter, never
    several without a documented reason"). The one documented combination is
    ``annotation`` together with ``orm_path``, which the contract *requires*
    because the path names the alias the annotation produces.
    """
    with scopes_disabled():
        fields = registry.get_fields(event, base)
    for key, field in fields.items():
        if field.value_getter is not None:
            assert field.orm_path is None, key
            assert field.annotation is None, key
        if field.annotation is not None:
            assert field.orm_path is not None, key


@pytest.mark.parametrize("base", BASES)
def test_only_database_backed_fields_are_sortable_or_filterable(
    registry, event, event_questions, event_meta, base
):
    """No filter and no sort on anything that is computed in Python."""
    with scopes_disabled():
        fields = registry.get_fields(event, base)
    for key, field in fields.items():
        if field.orm_path is None:
            assert not field.sortable, key
            assert field.filter_operators == (), key
            assert field.aggregates == (), key


def test_position_fields_need_an_aggregate_on_base_order(
    registry, event, event_questions
):
    """Position-level data on an order report is aggregated and not sortable.

    ADR 0001 section 7: aggregated instead of blocked, and sorting by an
    aggregate is out of scope for v1.
    """
    with scopes_disabled():
        order_fields = registry.get_fields(event, Base.ORDER)
        position_fields = registry.get_fields(event, Base.ORDERPOSITION)

    for key in (
        "position.price",
        "position.positionid",
        "item.name",
        "item.category",
        "variation.value",
        "subevent.name",
        "seat.zone_name",
        "voucher.code",
        "answer.tshirt-size",
    ):
        field = order_fields[key]
        assert field.needs_aggregate_on(Base.ORDER), key
        assert field.aggregates, key
        assert not field.sortable, key
        # ... and the very same key is a plain, sortable column per position.
        assert not position_fields[key].needs_aggregate_on(Base.ORDERPOSITION), key
        assert position_fields[key].sortable, key


def test_order_level_fields_need_no_aggregate_on_base_order(
    registry, event, event_meta
):
    """Order, invoice address, payment, check-in and meta data stay direct."""
    with scopes_disabled():
        fields = registry.get_fields(event, Base.ORDER)
    for key in (
        "order.code",
        "order.pending_sum",
        "order.position_count",
        "invoice_address.city",
        "payment.sum_confirmed",
        "refund.sum_done",
        "checkin.count",
        "checkin.first_datetime",
        "meta.event.campaign",
    ):
        assert not fields[key].needs_aggregate_on(Base.ORDER), key


@pytest.mark.parametrize("base", BASES)
def test_payment_providers_is_display_only(registry, event, base):
    """``payment.providers`` is not sortable on either base.

    _index.json lists it under ``not_sortable`` for base ``orderposition``. It is
    display-only here on both bases because the obvious SQL expression,
    ``StringAgg``, is PostgreSQL-only and pretix also runs on SQLite.
    """
    with scopes_disabled():
        field = registry.get_fields(event, base)["payment.providers"]
    assert not field.sortable
    assert field.filter_operators == ()
    assert field.value_getter is not None
    assert field.prefetch_related


def test_registry_refuses_to_build_without_an_event(registry):
    """No event, no field table -- there is no event-independent field list."""
    with pytest.raises(ValueError):
        registry.get_fields(None, Base.ORDER)
    with pytest.raises(ValueError):
        registry.resolve("order.code", None, Base.ORDER)


def test_registry_refuses_an_unsaved_event(registry, organizer):
    """An event without a primary key cannot be scoped to, so it is refused."""
    from pretix.base.models import Event

    unsaved = Event(organizer=organizer, name="Unsaved", slug="unsaved")
    with pytest.raises(ValueError):
        registry.get_fields(unsaved, Base.ORDER)


def test_resolve_tolerates_garbage(registry, event):
    """A malformed key returns ``None`` instead of raising.

    Keys come from untrusted documents. Making every caller guard against an
    exception would just move the crash somewhere less convenient.
    """
    with scopes_disabled():
        for key in ("", "nonsense", None, 42, "order.", ".code", "a b", "x" * 500):
            assert registry.resolve(key, event, Base.ORDER) is None


def test_get_fields_returns_a_copy(registry, event):
    """A caller cannot poison the cache by mutating what it got."""
    with scopes_disabled():
        first = registry.get_fields(event, Base.ORDER)
        first["order.code"] = "not a field"
        second = registry.get_fields(event, Base.ORDER)
    assert isinstance(second["order.code"], ReportField)


def test_registry_works_inside_an_organizer_scope(registry, event, event_questions):
    """The production path: an active scope, not ``scopes_disabled()``.

    That is what the control middleware, the API middleware and every
    ``EventTask``-based Celery task set up (docs/pretix-api-notes.md section 7).
    The registry deliberately never disables scopes itself -- switching off the
    tenant separation in the component that decides which data a report may name
    would be the wrong place for a convenience. Every other test here uses
    ``scopes_disabled()`` for brevity, so this one is the proof that the real
    thing works.
    """
    with scope(organizer=event.organizer):
        fields = registry.get_fields(event, Base.ORDERPOSITION)
        assert "order.code" in fields
        assert "answer.tshirt-size" in fields
        field = fields["answer.tshirt-size"]
        options = list(field.choices(registry.context(event, Base.ORDERPOSITION)))
        assert options
        mapping = field.annotation(registry.context(event, Base.ORDERPOSITION))
        assert (
            OrderPosition.all.filter(order__event=event).annotate(**mapping).count()
            == 0
        )


def test_registry_raises_without_any_scope(registry, event):
    """Without a scope the underlying managers refuse, and we do not paper over it.

    ``django_scopes`` raises ``ScopeError`` as soon as a scoped queryset is
    filtered. Catching it here and falling back to ``scopes_disabled()`` would
    turn a missing scope into a silent cross-tenant query.
    """
    from django_scopes.exceptions import ScopeError

    registry_cache.clear_local_cache()
    with pytest.raises(ScopeError):
        registry.get_fields(event, Base.ORDER)


# ---------------------------------------------------------------------------
# Questions: identifiers, fallback, choices
# ---------------------------------------------------------------------------


def test_question_with_double_underscore_is_skipped_and_reported(registry, event):
    """An identifier containing ``__`` cannot become a key, and says so.

    The fallback is deliberately *not* to mangle the identifier: a rewritten key
    would be unstable between events and could collide with a real identifier.
    The question is skipped and the diagnostics name it, so the debug view can
    tell the user which question to rename (ADR 0001 section 2).
    """
    from pretix_custom_reports.registry.diagnostics import (
        REASON_INVALID_KEY,
        SOURCE_QUESTION,
    )

    with scopes_disabled():
        Question.objects.create(
            event=event,
            question="Broken",
            identifier="broken__identifier",
            type=Question.TYPE_STRING,
        )
        Question.objects.create(
            event=event,
            question="Fine",
            identifier="fine-identifier",
            type=Question.TYPE_STRING,
        )
        fields = registry.get_fields(event, Base.ORDERPOSITION)
        diagnostics = registry.diagnostics(event, Base.ORDERPOSITION)

    assert "answer.fine-identifier" in fields
    assert "answer.broken__identifier" not in fields
    skipped = diagnostics.by_source(SOURCE_QUESTION)
    assert [entry.key for entry in skipped] == ["answer.broken__identifier"]
    assert skipped[0].reason == REASON_INVALID_KEY
    assert "double underscore" in skipped[0].detail


def test_renaming_a_question_moves_its_key(registry, event, event_questions):
    """The old key stops resolving, the new one starts. Both are normal states.

    ``Question.identifier`` is editable in the backend and through the API
    (docs/pretix-api-notes.md section 6.4), so a saved report losing a column is
    an expected event, not a bug -- which is why ``resolve`` returns ``None``
    rather than raising.
    """
    with scopes_disabled():
        assert registry.resolve("answer.tshirt-size", event, Base.ORDERPOSITION)
        question = event_questions["tshirt-size"]
        question.identifier = "shirt-size"
        question.save(update_fields=["identifier"])
        registry_cache.clear_local_cache()

        assert registry.resolve("answer.tshirt-size", event, Base.ORDERPOSITION) is None
        assert registry.resolve("answer.shirt-size", event, Base.ORDERPOSITION)


def test_answer_keys_resolve_case_insensitively(registry, event, event_questions):
    """``answer.TSHIRT-SIZE`` finds the same field as ``answer.tshirt-size``.

    pretix checks the uniqueness of ``Question.identifier`` case-insensitively
    (``Question._clean_identifier``), so two questions differing only in
    capitalisation cannot exist and the lookup is unambiguous. ADR 0001
    section 3.2 asks for it.
    """
    with scopes_disabled():
        field = registry.resolve("answer.TShirt-Size", event, Base.ORDERPOSITION)
    assert field is not None
    assert field.key == "answer.tshirt-size"


def test_case_insensitivity_does_not_leak_into_other_namespaces(registry, event):
    """Only answer keys are case-insensitive; core keys are exact."""
    with scopes_disabled():
        assert registry.resolve("ORDER.CODE", event, Base.ORDER) is None
        assert registry.resolve("order.CODE", event, Base.ORDER) is None


def test_choice_question_offers_its_options_lazily(registry, event, event_questions):
    """A choice question's options come back as ``choices``, evaluated on demand.

    The values are the option labels, because that is what pretix stores in
    ``QuestionAnswer.answer`` (``to_string``, orders.py:1440-1447). So a filter
    value survives an export/import by name -- hence ``ValueScope.EVENT``.
    """
    from pretix_custom_reports.contracts import ValueScope

    with scopes_disabled():
        field = registry.resolve("answer.tshirt-size", event, Base.ORDERPOSITION)
        assert field.choices is not None
        assert field.value_scope is ValueScope.EVENT
        options = list(field.choices(registry.context(event, Base.ORDERPOSITION)))
    assert options == [("S", "S"), ("M", "M"), ("L", "L"), ("XL", "XL")]
    assert Operator.IN in field.filter_operators


def test_choices_never_answer_for_a_different_event(
    registry, event, event_without_plugin, event_questions
):
    """A ``choices`` callable handed the wrong event returns nothing.

    Cheap insurance against the one way a cached field could read another
    event's data.
    """
    with scopes_disabled():
        field = registry.resolve("answer.tshirt-size", event, Base.ORDERPOSITION)
        wrong = registry.context(event_without_plugin, Base.ORDERPOSITION)
        assert list(field.choices(wrong)) == []


def test_annotation_refuses_a_context_for_another_event(
    registry, event, event_without_plugin, event_questions
):
    """Same guard on the annotation side, where it would be a data leak."""
    from pretix_custom_reports.contracts import FieldContractError

    with scopes_disabled():
        field = registry.resolve("answer.newsletter", event, Base.ORDERPOSITION)
        wrong = registry.context(event_without_plugin, Base.ORDERPOSITION)
        with pytest.raises(FieldContractError):
            field.annotation(wrong)


def test_annotation_refuses_a_context_for_another_base(registry, event):
    """And against being used on the base it was not built for."""
    from pretix_custom_reports.contracts import FieldContext, FieldContractError

    with scopes_disabled():
        field = registry.resolve("order.pending_sum", event, Base.ORDER)
        with pytest.raises(FieldContractError):
            field.annotation(FieldContext(event=event, base=Base.ORDERPOSITION))


def test_multiple_choice_question_uses_list_operators(registry, event):
    """Multiple choice answers are labels joined by ", ", so ``contains``.

    ``in`` would compare the whole joined string against one option and quietly
    match nothing.
    """
    with scopes_disabled():
        Question.objects.create(
            event=event,
            question="Workshops",
            identifier="workshops",
            type=Question.TYPE_CHOICE_MULTIPLE,
        )
        field = registry.resolve("answer.workshops", event, Base.ORDERPOSITION)
    assert field.datatype is DataType.MULTICHOICE
    assert Operator.CONTAINS in field.filter_operators
    assert Operator.IN not in field.filter_operators


def test_number_and_datetime_questions_are_text(registry, event):
    """Two mappings that look wrong on purpose, both documented in the module.

    A numeric answer is stored as text; ordering it numerically would need a
    cast, and one malformed row would then fail the whole report on PostgreSQL.
    """
    with scopes_disabled():
        Question.objects.create(
            event=event,
            question="How many",
            identifier="count",
            type=Question.TYPE_NUMBER,
        )
        Question.objects.create(
            event=event, question="When", identifier="when", type=Question.TYPE_DATETIME
        )
        fields = registry.get_fields(event, Base.ORDERPOSITION)
    assert fields["answer.count"].datatype is DataType.STRING
    assert fields["answer.when"].datatype is DataType.STRING
    assert Operator.BETWEEN not in fields["answer.count"].filter_operators


def test_answers_cannot_be_filtered_on_base_order(registry, event, event_questions):
    """Deliberate narrowing, with a reason.

    "Order has a position that answered X" and "all positions answered X" are
    different questions and the editor cannot express which is meant. A filter
    that silently picks one is worse than no filter; build the report on base
    ``orderposition`` instead.
    """
    with scopes_disabled():
        field = registry.get_fields(event, Base.ORDER)["answer.tshirt-size"]
    assert field.filter_operators == ()
    assert set(field.aggregates) == {
        Aggregate.JOIN,
        Aggregate.COUNT,
        Aggregate.COUNT_DISTINCT,
    }


def test_age_field_exists_for_every_date_question(registry, event, event_questions):
    """``computed.age.<identifier>`` accompanies each date question."""
    with scopes_disabled():
        fields = registry.get_fields(event, Base.ORDERPOSITION)
        order_fields = registry.get_fields(event, Base.ORDER)
    key = f"{AGE_KEY_PREFIX}arrival-date"
    assert key in fields
    assert fields[key].datatype is DataType.INTEGER
    assert fields[key].sortable
    # Not offered on base order: it would need an aggregate over positions, and
    # "average age per order" is not a question anybody asks.
    assert key not in order_fields
    assert f"{AGE_KEY_PREFIX}tshirt-size" not in fields


# ---------------------------------------------------------------------------
# Meta properties
# ---------------------------------------------------------------------------


def test_meta_property_with_double_underscore_is_skipped(registry, event):
    """``^[a-zA-Z0-9_]+$`` allows ``a__b``; our key grammar does not."""
    from pretix_custom_reports.registry.diagnostics import (
        REASON_INVALID_KEY,
        SOURCE_META,
    )
    from tests.test_registry_support import make_meta_property

    make_meta_property(event.organizer, name="two__words")
    make_meta_property(event.organizer, name="fine")
    registry_cache.clear_local_cache()

    with scopes_disabled():
        fields = registry.get_fields(event, Base.ORDER)
        diagnostics = registry.diagnostics(event, Base.ORDER)
    assert "meta.event.fine" in fields
    assert "meta.event.two__words" not in fields
    skipped = diagnostics.by_source(SOURCE_META)
    assert [entry.reason for entry in skipped] == [REASON_INVALID_KEY]


def test_meta_field_is_a_constant_per_event(registry, event, event_meta):
    """The value is a literal in the SQL, so display and filter agree.

    Filtering through ``meta_values__property__name`` would miss every event
    that relies on the organizer-wide default
    (docs/pretix-api-notes.md section 6.7).
    """
    from django.db.models import Value

    with scopes_disabled():
        field = registry.resolve("meta.event.campaign", event, Base.ORDER)
        mapping = field.annotation(registry.context(event, Base.ORDER))
    alias = field.orm_path
    assert list(mapping) == [alias]
    assert isinstance(mapping[alias], Value)
    assert mapping[alias].value == "summer"


# ---------------------------------------------------------------------------
# Annotation hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base", BASES)
def test_every_annotation_declares_its_own_alias(
    registry, event, event_questions, event_meta, base
):
    """``orm_path`` is one of the aliases the annotation produces.

    The contract demands it (``ReportField.__post_init__``) but cannot check it:
    only calling the callable can.
    """
    with scopes_disabled():
        fields = registry.get_fields(event, base)
        context = registry.context(event, base)
        for key, field in fields.items():
            if field.annotation is None:
                continue
            mapping = field.annotation(context)
            assert field.orm_path in mapping, key


@pytest.mark.parametrize("base", BASES)
def test_every_annotation_alias_is_namespaced(
    registry, event, event_questions, event_meta, base
):
    """All aliases start with ``pcr_``, so we cannot collide with pretix.

    ``Order.annotate_overpayments`` uses ``payment_sum``, ``refund_sum`` and
    ``pending_sum_t``; ``Order.count_positions`` reads ``pcnt``. A queryset that
    already went through one of those must stay annotatable.
    """
    with scopes_disabled():
        fields = registry.get_fields(event, base)
        context = registry.context(event, base)
        for key, field in fields.items():
            if field.annotation is None:
                continue
            for alias in field.annotation(context):
                assert alias.startswith(annotations.ALIAS_PREFIX), f"{key}: {alias}"


@pytest.mark.parametrize("base", BASES)
def test_annotations_of_all_fields_merge_into_one_mapping(
    registry, event, event_questions, event_meta, base
):
    """Two fields sharing an alias produce the *same* expression, not a clash.

    ``order.pending_sum`` and ``computed.payment_state`` both need the
    outstanding amount. Because they emit it under the same alias, merging every
    used field's mapping into one dict and calling ``annotate()`` once works --
    which is exactly what the query compiler has to do.
    """
    merged = {}
    with scopes_disabled():
        fields = registry.get_fields(event, base)
        context = registry.context(event, base)
        for field in fields.values():
            if field.annotation is None:
                continue
            for alias, expression in field.annotation(context).items():
                merged[alias] = expression
    assert annotations.ALIAS_PENDING_SUM in merged
    assert annotations.ALIAS_PAYMENT_SUM in merged
    # The dependency has to come before the field that compares against it.
    aliases = list(merged)
    assert aliases.index(annotations.ALIAS_PENDING_SUM) < aliases.index(
        annotations.ALIAS_PAYMENT_STATE
    )


@pytest.mark.parametrize("base", BASES)
def test_aggregate_hints_are_json_safe(registry, event, event_questions, base):
    """``extra`` must survive ``json.dumps``: the editor API serialises fields.

    That is why the ``Q`` object for an aggregate's ``filter=`` lives in
    :mod:`registry.hints` as a function and not in ``extra``.
    """
    import json

    with scopes_disabled():
        fields = registry.get_fields(event, base)
    for key, field in fields.items():
        json.dumps(dict(field.extra))


def test_aggregate_filter_excludes_canceled_positions(registry, event, event_questions):
    """The hint turns into ``filter=Q(all_positions__canceled=False)``."""
    from django.db.models import Q

    from pretix_custom_reports.registry import hints

    with scopes_disabled():
        price = registry.get_fields(event, Base.ORDER)["position.price"]
        answer_field = registry.get_fields(event, Base.ORDER)["answer.tshirt-size"]

    assert hints.aggregate_relation(price) == "all_positions"
    assert hints.aggregate_filter(price) == Q(all_positions__canceled=False)
    assert hints.aggregate_filter(price, include_canceled_positions=True) is None

    # An answer column additionally has to be restricted to its own question,
    # otherwise it would aggregate the answers to every question of the event.
    condition = hints.aggregate_filter(answer_field)
    assert condition is not None
    rendered = str(condition)
    assert "all_positions__canceled" in rendered
    assert "all_positions__answers__question" in rendered


def test_plain_fields_carry_no_aggregate_hints(registry, event):
    """Order-level fields have nothing for the compiler to filter."""
    from pretix_custom_reports.registry import hints

    with scopes_disabled():
        field = registry.get_fields(event, Base.ORDER)["order.code"]
    assert hints.aggregate_relation(field) is None
    assert hints.aggregate_filter(field) is None


# ---------------------------------------------------------------------------
# 3. Correctness: the expressions actually run
# ---------------------------------------------------------------------------


def _merged_annotations(registry, event, base, keys):
    context = registry.context(event, base)
    fields = registry.get_fields(event, base)
    merged = {}
    for key in keys:
        annotation = fields[key].annotation
        if annotation is None:
            continue
        merged.update(annotation(context))
    return merged, fields


@pytest.mark.parametrize("base", BASES)
def test_all_annotations_execute(registry, event, event_questions, event_meta, base):
    """Build one queryset with *every* annotated field and evaluate it.

    A registry full of expressions that only look right is worthless; this is the
    test that would have caught a wrong lookup path, a missing ``output_field``
    or an alias referenced before it exists.
    """
    with scopes_disabled():
        order = make_order(event)
        position = order.positions.first()
        answer(position, event_questions["arrival-date"], "1990-05-03")
        answer(position, event_questions["newsletter"], "True")
        answer(position, event_questions["tshirt-size"], "L")
        registry_cache.clear_local_cache()

        fields = registry.get_fields(event, base)
        merged, _ = _merged_annotations(registry, event, base, list(fields))
        if base is Base.ORDER:
            queryset = Order.objects.filter(event=event)
        else:
            queryset = OrderPosition.all.filter(order__event=event)
        rows = list(queryset.annotate(**merged))

    assert len(rows) == 1
    row = rows[0]
    assert getattr(row, annotations.ALIAS_PAYMENT_SUM) == Decimal("23.00")
    assert getattr(row, annotations.ALIAS_REFUND_SUM) == Decimal("0.00")
    assert getattr(row, annotations.ALIAS_PENDING_SUM) == Decimal("0.00")
    assert getattr(row, annotations.ALIAS_POSITION_COUNT) == 1
    assert getattr(row, annotations.ALIAS_PAYMENT_STATE) == "paid"
    assert getattr(row, annotations.ALIAS_STATUS_LABEL)
    assert getattr(row, annotations.ALIAS_CHECKIN_COUNT) == 0
    assert getattr(row, annotations.ALIAS_CHECKIN_FIRST) is None


def test_boolean_answer_is_a_real_boolean(registry, event, event_questions):
    """pretix stores ``"True"``/``"False"`` as text; we hand out a boolean.

    Without this the golden fixture
    ``{"field": "answer.newsletter", "operator": "exact", "value": true}`` could
    never match anything.
    """
    with scopes_disabled():
        order = make_order(event)
        position = order.positions.first()
        answer(position, event_questions["newsletter"], "True")
        registry_cache.clear_local_cache()

        field = registry.resolve("answer.newsletter", event, Base.ORDERPOSITION)
        mapping = field.annotation(registry.context(event, Base.ORDERPOSITION))
        queryset = OrderPosition.all.filter(order__event=event).annotate(**mapping)
        assert getattr(queryset.get(), field.orm_path) is True
        assert queryset.filter(**{field.orm_path: True}).count() == 1
        assert queryset.filter(**{field.orm_path: False}).count() == 0


def test_answer_text_is_filterable_and_sortable(registry, event, event_questions):
    """A date answer compares correctly because ISO strings sort correctly."""
    with scopes_disabled():
        order = make_order(event)
        position = order.positions.first()
        answer(position, event_questions["arrival-date"], "2026-07-01")
        registry_cache.clear_local_cache()

        field = registry.resolve("answer.arrival-date", event, Base.ORDERPOSITION)
        mapping = field.annotation(registry.context(event, Base.ORDERPOSITION))
        queryset = OrderPosition.all.filter(order__event=event).annotate(**mapping)
        alias = field.orm_path
        assert (
            queryset.filter(**{f"{alias}__gte": datetime.date(2026, 1, 1)}).count() == 1
        )
        assert (
            queryset.filter(**{f"{alias}__gte": datetime.date(2027, 1, 1)}).count() == 0
        )
        assert list(queryset.order_by(alias))


def test_age_at_event_date_is_computed_in_the_database(
    registry, event, event_questions
):
    """Age in full years against the event start, and the birthday edge case."""
    with scopes_disabled():
        event.date_from = datetime.datetime(
            2026, 7, 1, 10, 0, tzinfo=datetime.timezone.utc
        )
        event.save(update_fields=["date_from"])
        registry_cache.clear_local_cache()

        order = make_order(event)
        position = order.positions.first()
        # Birthday already passed in 2026 -> 36. One day later -> still 35.
        answer(position, event_questions["arrival-date"], "1990-05-03")

        field = registry.resolve(
            f"{AGE_KEY_PREFIX}arrival-date", event, Base.ORDERPOSITION
        )
        mapping = field.annotation(registry.context(event, Base.ORDERPOSITION))
        queryset = OrderPosition.all.filter(order__event=event).annotate(**mapping)
        assert getattr(queryset.get(), field.orm_path) == 36

        QuestionAnswer.objects.filter(orderposition=position).update(
            answer="1990-12-31"
        )
        assert getattr(queryset.get(), field.orm_path) == 35


def test_age_is_null_without_an_answer(registry, event, event_questions):
    """No answer, no age -- and no database error either."""
    with scopes_disabled():
        order = make_order(event)
        field = registry.resolve(
            f"{AGE_KEY_PREFIX}arrival-date", event, Base.ORDERPOSITION
        )
        mapping = field.annotation(registry.context(event, Base.ORDERPOSITION))
        queryset = OrderPosition.all.filter(order__event=event).annotate(**mapping)
        assert getattr(queryset.get(), field.orm_path) is None
        assert order  # keep the order referenced


def test_payment_state_reflects_partial_payments(registry, event):
    """``computed.payment_state`` distinguishes the four cases in SQL."""
    from pretix.base.models import OrderPayment

    with scopes_disabled():
        order = make_order(event)
        field = registry.resolve("computed.payment_state", event, Base.ORDER)
        mapping = field.annotation(registry.context(event, Base.ORDER))

        def state():
            queryset = Order.objects.filter(event=event).annotate(**mapping)
            return getattr(queryset.get(), field.orm_path)

        assert state() == "paid"

        OrderPayment.objects.filter(order=order).update(amount=Decimal("10.00"))
        assert state() == "partially_paid"

        OrderPayment.objects.filter(order=order).update(amount=Decimal("0.00"))
        assert state() == "unpaid"

        OrderPayment.objects.filter(order=order).update(amount=Decimal("30.00"))
        assert state() == "overpaid"


def test_payment_providers_getter_needs_no_extra_query(registry, event):
    """The value getter reads the prefetch, it does not hit the database."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with scopes_disabled():
        make_order(event)
        field = registry.resolve("payment.providers", event, Base.ORDER)
        queryset = Order.objects.filter(event=event).prefetch_related(
            *field.prefetch_related
        )
        rows = list(queryset)
        with CaptureQueriesContext(connection) as captured:
            assert field.value_getter(rows[0]) == "banktransfer"
        assert len(captured) == 0


def test_field_registry_singleton_is_stable():
    """``field_registry()`` hands out one instance."""
    assert field_registry() is field_registry()
    assert isinstance(field_registry(), EventFieldRegistry)
