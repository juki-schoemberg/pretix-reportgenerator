# Owner: contract-architect (see ORCHESTRIERUNG.md section 5)
#
# Proves the two claims the rest of wave 1 relies on:
#   1. the contracts import and work without Django, a database or an event
#   2. every golden fixture behaves exactly as tests/fixtures/definitions/
#      _index.json and invalid/_expectations.json say it does
#
# No database, no fixtures from conftest.py -- these tests must stay fast
# enough that every agent runs them on every change.
"""Structural tests for the frozen contracts and the golden fixtures."""

import json
import pathlib
import pytest

from pretix_custom_reports.contracts import (
    SCHEMA_VERSION,
    Aggregate,
    Base,
    CompilationError,
    DataType,
    DefinitionValidationError,
    ErrorCode,
    FieldContractError,
    FieldResolutionError,
    FieldUsage,
    Operator,
    OperatorSpec,
    ReportDefinition,
    ReportField,
    ValueKind,
    find_unresolved_fields,
    plugin_field_key,
    question_field_key,
    validate_definition,
    validate_identifier,
    validate_key,
    validate_portable_document,
)
from pretix_custom_reports.contracts.fields import OPERATOR_SPECS
from pretix_custom_reports.contracts.stubs import (
    StubFieldRegistry,
    StubQueryCompiler,
    stub_compiler,
    stub_registry,
)

DEFINITIONS_DIR = pathlib.Path(__file__).parent / "fixtures" / "definitions"
INVALID_DIR = DEFINITIONS_DIR / "invalid"
PORTABLE_DIR = DEFINITIONS_DIR / "portable"


def _fixtures(directory):
    """Fixture files in *directory*; names starting with ``_`` are metadata."""
    return sorted(p for p in directory.glob("*.json") if not p.name.startswith("_"))


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


VALID_FILES = _fixtures(DEFINITIONS_DIR)
INVALID_FILES = _fixtures(INVALID_DIR)
PORTABLE_FILES = _fixtures(PORTABLE_DIR)
EXPECTATIONS = _load(INVALID_DIR / "_expectations.json")["fixtures"]
INDEX = _load(DEFINITIONS_DIR / "_index.json")


# ---------------------------------------------------------------------------
# The fixture set itself
# ---------------------------------------------------------------------------


def test_at_least_eight_valid_fixtures():
    assert len(VALID_FILES) >= 8


def test_every_invalid_fixture_has_an_expectation():
    assert {p.name for p in INVALID_FILES} == set(EXPECTATIONS)


def test_every_valid_fixture_is_listed_in_the_index():
    listed = set(INDEX["fixtures"])
    assert {p.name for p in VALID_FILES} <= listed


# ---------------------------------------------------------------------------
# Valid fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", VALID_FILES, ids=lambda p: p.name)
def test_valid_fixture_validates(path):
    definition = validate_definition(_load(path))
    assert definition.schema_version == SCHEMA_VERSION
    assert isinstance(definition.base, Base)
    assert definition.columns


@pytest.mark.parametrize("path", VALID_FILES, ids=lambda p: p.name)
def test_valid_fixture_round_trips(path):
    """``validate -> as_dict -> validate`` is a fixed point."""
    once = validate_definition(_load(path))
    twice = validate_definition(once.as_dict())
    assert once == twice
    assert json.loads(once.as_json()) == once.as_dict()


@pytest.mark.parametrize("path", VALID_FILES, ids=lambda p: p.name)
def test_valid_fixture_uses_no_orm_paths(path):
    """No key outside invalid/ may look like an ORM lookup."""
    definition = validate_definition(_load(path))
    for ref in definition.iter_field_references():
        assert "__" not in ref.key


@pytest.mark.parametrize("path", VALID_FILES, ids=lambda p: p.name)
def test_valid_fixture_compiles_against_the_stub(path):
    definition = validate_definition(_load(path))
    compiled = stub_compiler().compile(definition, event=None)

    visible = [c for c in definition.columns if not c.hidden]
    assert len(compiled.columns) == len(visible)
    assert compiled.headers() == [c.label for c in compiled.columns]
    assert all(header for header in compiled.headers())

    rows = list(compiled.iter_rows(limit=3))
    assert len(rows) == min(3, compiled.count())
    for row in rows:
        assert len(row) == len(compiled.columns)


@pytest.mark.parametrize("path", VALID_FILES, ids=lambda p: p.name)
def test_valid_fixture_resolves_completely(path):
    definition = validate_definition(_load(path))
    assert find_unresolved_fields(definition, stub_registry(), None) == ()


@pytest.mark.parametrize("path", PORTABLE_FILES, ids=lambda p: p.name)
def test_portable_fixture_validates(path):
    document = validate_portable_document(_load(path))
    assert document.name
    assert document.definition.columns
    assert document.schema_version == document.definition.schema_version


# ---------------------------------------------------------------------------
# Invalid fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", INVALID_FILES, ids=lambda p: p.name)
def test_invalid_fixture_fails_as_expected(path):
    expectation = EXPECTATIONS[path.name]
    data = _load(path)

    if expectation["stage"] == "structure":
        with pytest.raises(DefinitionValidationError) as excinfo:
            validate_definition(data)
        missing = set(expectation["codes"]) - set(excinfo.value.codes)
        assert not missing, (
            f"{path.name}: expected codes {sorted(missing)} not raised, "
            f"got {sorted(set(excinfo.value.codes))}"
        )
        return

    # stage == "registry": the structure is fine, the fields are not.
    definition = validate_definition(data)
    expected = {
        "FieldResolutionError": FieldResolutionError,
        "CompilationError": CompilationError,
    }[expectation["error"]]
    with pytest.raises(expected):
        stub_compiler().compile(definition, event=None)


def test_registry_stage_fixtures_pass_structural_validation():
    """The split between the two stages is load-bearing; assert it explicitly."""
    registry_stage = [
        name for name, exp in EXPECTATIONS.items() if exp["stage"] == "registry"
    ]
    assert registry_stage
    for name in registry_stage:
        validate_definition(_load(INVALID_DIR / name))


# ---------------------------------------------------------------------------
# Key grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "order.code",
        "position.attendee_name",
        "invoice_address.vat_id",
        "answer.tshirt-size",
        "answer.a.b.c",
        "answer.ABC123",
        "meta.event.campaign",
        "plugin.pretix_demo.demo_value",
    ],
)
def test_valid_keys(key):
    assert validate_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "code",
        "Order.code",
        "order.event__organizer__slug",
        "order..code",
        "order.",
        ".code",
        "order.code!",
        "order.code with space",
        "unknown_namespace.code",
        "plugin.demo",
        "order." + "x" * 300,
        None,
        42,
    ],
)
def test_invalid_keys(key):
    with pytest.raises(ValueError):
        validate_key(key)


def test_key_helpers():
    assert question_field_key("tshirt-size") == "answer.tshirt-size"
    assert plugin_field_key("my_plugin", "field") == "plugin.my_plugin.field"


def test_identifier_grammar():
    assert validate_identifier("attendee-list") == "attendee-list"
    for bad in ["", "with space", "with/slash", "x" * 200, None]:
        with pytest.raises(ValueError):
            validate_identifier(bad)


# ---------------------------------------------------------------------------
# ReportField invariants
# ---------------------------------------------------------------------------


def _field(**kwargs):
    defaults = dict(
        key="order.code",
        label="Order code",
        group="order",
        datatype=DataType.STRING,
        bases=(Base.ORDER,),
        orm_path="code",
    )
    defaults.update(kwargs)
    return ReportField(**defaults)


def test_field_coerces_strings_to_enums():
    field = _field(
        datatype="money", bases=["order", "orderposition"], filter_operators=["exact"]
    )
    assert field.datatype is DataType.MONEY
    assert field.bases == (Base.ORDER, Base.ORDERPOSITION)
    assert field.filter_operators == (Operator.EXACT,)


def test_field_rejects_unknown_enum_value():
    with pytest.raises(FieldContractError):
        _field(datatype="colour")


def test_field_requires_a_source():
    with pytest.raises(FieldContractError):
        _field(orm_path=None)


def test_field_annotation_requires_orm_path():
    with pytest.raises(FieldContractError):
        _field(orm_path=None, annotation=lambda ctx: {"x": 1})


def test_python_only_field_cannot_be_filtered_or_sorted():
    ok = _field(orm_path=None, value_getter=lambda row: "x")
    assert ok.filter_operators == ()
    with pytest.raises(FieldContractError):
        _field(orm_path=None, value_getter=lambda row: "x", sortable=True)
    with pytest.raises(FieldContractError):
        _field(
            orm_path=None,
            value_getter=lambda row: "x",
            filter_operators=(Operator.EXACT,),
        )


def test_field_rejects_orm_path_that_is_not_a_path():
    with pytest.raises(FieldContractError):
        _field(orm_path="code; DROP TABLE")


def test_requires_aggregate_needs_aggregates_and_a_matching_base():
    with pytest.raises(FieldContractError):
        _field(requires_aggregate_on=(Base.ORDER,))
    with pytest.raises(FieldContractError):
        _field(
            bases=(Base.ORDER,),
            requires_aggregate_on=(Base.ORDERPOSITION,),
            aggregates=(Aggregate.COUNT,),
        )


def test_plugin_namespace_and_provider_must_agree():
    with pytest.raises(FieldContractError):
        _field(key="plugin.demo.value")
    with pytest.raises(FieldContractError):
        _field(provider="some_plugin")
    assert _field(key="plugin.demo.value", provider="demo").provider == "demo"


# ---------------------------------------------------------------------------
# Operator table
# ---------------------------------------------------------------------------


def test_every_operator_has_a_spec():
    assert set(OPERATOR_SPECS) == set(Operator)
    for operator, spec in OPERATOR_SPECS.items():
        assert isinstance(spec, OperatorSpec)
        assert isinstance(spec.value_kind, ValueKind)
        assert spec.relative == operator.value.startswith("relative_")


def test_value_kinds_are_enforced():
    def condition(operator, value):
        return {
            "schema_version": SCHEMA_VERSION,
            "base": "order",
            "columns": [{"field": "order.code"}],
            "filters": {
                "op": "and",
                "children": [
                    {"field": "order.datetime", "operator": operator, "value": value}
                ],
            },
        }

    validate_definition(condition("relative_last_days", 7))
    for bad in ["7", 7.5, 0, True, None, [7]]:
        with pytest.raises(DefinitionValidationError):
            validate_definition(condition("relative_last_days", bad))

    validate_definition(condition("between", ["a", "b"]))
    for bad in ["a", ["a"], ["a", "b", "c"], None]:
        with pytest.raises(DefinitionValidationError):
            validate_definition(condition("between", bad))


def test_no_value_operators_reject_a_value():
    with pytest.raises(DefinitionValidationError) as excinfo:
        validate_definition(
            {
                "schema_version": SCHEMA_VERSION,
                "base": "order",
                "columns": [{"field": "order.code"}],
                "filters": {
                    "op": "and",
                    "children": [
                        {"field": "order.code", "operator": "is_empty", "value": "x"}
                    ],
                },
            }
        )
    assert ErrorCode.VALUE_SHAPE_MISMATCH in excinfo.value.codes


# ---------------------------------------------------------------------------
# Validator behaviour
# ---------------------------------------------------------------------------


def test_all_issues_are_collected_not_just_the_first():
    with pytest.raises(DefinitionValidationError) as excinfo:
        validate_definition(
            {
                "schema_version": 99,
                "base": "nope",
                "columns": [{"field": "bad key"}, {"field": "order.code", "x": 1}],
            }
        )
    assert len(excinfo.value.issues) >= 4
    assert all(issue.path for issue in excinfo.value.issues if issue.path != "")


def test_empty_root_filter_group_is_normalised_to_none():
    definition = validate_definition(
        {
            "schema_version": SCHEMA_VERSION,
            "base": "order",
            "columns": [{"field": "order.code"}],
            "filters": {"op": "and", "children": []},
        }
    )
    assert definition.filters is None


def test_field_references_cover_columns_filters_and_sorting():
    definition = validate_definition(_load(DEFINITIONS_DIR / "filters_and_or.json"))
    usages = {ref.usage for ref in definition.iter_field_references()}
    assert usages == {FieldUsage.COLUMN, FieldUsage.FILTER, FieldUsage.SORT}
    assert "order.code" in definition.field_keys()


def test_definition_json_string_entry_point():
    from pretix_custom_reports.contracts import validate_definition_json

    text = (DEFINITIONS_DIR / "minimal_order.json").read_text(encoding="utf-8")
    assert isinstance(validate_definition_json(text), ReportDefinition)
    with pytest.raises(DefinitionValidationError) as excinfo:
        validate_definition_json("{not json")
    assert ErrorCode.NOT_JSON in excinfo.value.codes


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def test_stub_registry_provides_every_required_key():
    required = INDEX["required_field_keys"]
    for base in (Base.ORDER, Base.ORDERPOSITION):
        fields = stub_registry().get_fields(None, base)
        for key in required["core"]:
            assert key in fields, f"{key} missing on base {base}"
        for identifier in required["questions"]["identifiers"]:
            assert question_field_key(identifier) in fields
        for name in required["meta_properties"]["event"]:
            assert f"meta.event.{name}" in fields
        for key in required["plugin"]["keys"]:
            assert key in fields


def test_stub_registry_matches_the_protocol():
    from pretix_custom_reports.contracts import FieldRegistry, QueryCompiler

    assert isinstance(stub_registry(), FieldRegistry)
    assert isinstance(stub_compiler(), QueryCompiler)


def test_stub_registry_resolve_returns_none_for_unknown_keys():
    assert stub_registry().resolve("order.nope", None, Base.ORDER) is None


def test_stub_compiler_honours_row_limit_and_hidden_columns():
    definition = validate_definition(_load(DEFINITIONS_DIR / "wide_order.json"))
    compiled = stub_compiler().compile(definition, event=None)
    assert all(c.key != "order.comment" for c in compiled.columns)

    limited = StubQueryCompiler(stub_registry(), rows=50).compile(
        validate_definition(_load(DEFINITIONS_DIR / "options_full.json"))
    )
    assert limited.count() == 50  # row_limit of 5000 does not shrink it

    small = StubQueryCompiler(stub_registry(), rows=3).compile(
        validate_definition(_load(DEFINITIONS_DIR / "minimal_order.json"))
    )
    assert small.count() == 3
    assert len(list(small.iter_rows())) == 3


def test_stub_registry_can_drop_questions():
    registry = StubFieldRegistry(questions=(), include_plugin_field=False)
    assert registry.resolve("answer.tshirt-size", None, Base.ORDERPOSITION) is None
    definition = validate_definition(
        _load(DEFINITIONS_DIR / "orderposition_questions.json")
    )
    unresolved = find_unresolved_fields(definition, registry, None)
    assert {ref.key for ref in unresolved} == {
        "answer.tshirt-size",
        "answer.arrival-date",
        "answer.newsletter",
    }
