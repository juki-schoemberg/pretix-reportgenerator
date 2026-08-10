# Owner from wave 2 on: portability-dev (see ORCHESTRIERUNG.md section 5)
"""Import, export and the resolution layer -- including the attacks.

Structure of this module:

1. the payload gate: everything that is refused before anybody interprets it
2. the file format: what an export looks like and what an import accepts
3. the round trip demanded by the definition of done
4. every file in ``tests/fixtures/definitions/invalid/``, one test each
5. the resolution layer: found, mapped, missing, ambiguous, values
6. the importer: nothing is written before the decision, one row after it
7. the views, through the real URL resolver and the real permission decorators
8. the event copy

Routing note, same as ``tests/test_permissions.py``: ``urls.py`` belongs to the
integrator and is wired up in wave 4. The module-scoped ``plugin_urls`` fixture
attaches the routes of ``views/crud.py``, ``views/portability.py`` and
``views/templates.py`` for the duration of the module so the view tests run
through the real resolver, the real control middleware and the real permission
decorators. The copy-ready lines are in
handoff/requests/portability-dev-an-integrator-urls.md.
"""

from typing import Any, Dict, List

import importlib
import json
import pathlib
import pytest
import sys
from django.urls import clear_url_caches, reverse
from django_scopes import scope, scopes_disabled
from pretix.base.models import Item, ItemCategory, LogEntry, Question, QuestionOption

from pretix_custom_reports import contracts
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.portability import payload as payload_mod
from pretix_custom_reports.portability.envelope import (
    build_export_document,
    export_filename,
    parse_document,
)
from pretix_custom_reports.portability.errors import (
    ImportRejected,
    PayloadRejected,
)
from pretix_custom_reports.portability.eventcopy import copy_reports_to_event
from pretix_custom_reports.portability.importer import commit_import, plan_import
from pretix_custom_reports.portability.payload import (
    MAX_DEPTH,
    MAX_NODES,
    MAX_PAYLOAD_BYTES,
    MAX_STRING_CHARS,
    load_json_object,
)
from pretix_custom_reports.portability.references import Reference
from pretix_custom_reports.portability.resolution import (
    STATUS_FOUND,
    STATUS_MAPPED,
    STATUS_MISSING,
    ResolutionStrategy,
    resolve_definition,
)
from pretix_custom_reports.registry import cache as registry_cache
from pretix_custom_reports.registry.library import EventFieldRegistry

from .conftest import PASSWORD

URLCONF = "pretix.multidomain.maindomain_urlconf"
FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "definitions"


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


def _reload_urlconf():
    if URLCONF in sys.modules:
        importlib.reload(sys.modules[URLCONF])
    else:  # pragma: no cover - only if nothing has resolved a URL yet
        importlib.import_module(URLCONF)
    clear_url_caches()


def _template_editor_urlpatterns():
    """frontend-dev's organizer-level editor routes, if they exist yet.

    ``template_list.html`` links "create" and "edit" to
    ``organizer.templates.editor.new`` / ``.edit`` instead of to the JSON forms
    of ``views/templates.py``, so rendering that page needs those two routes to
    reverse. They live in ``views/editor.py`` in their own list, waiting for the
    integrator to add them to ``urls.py``.

    Attached here for the same reason every other route in this function is:
    ``urls.py`` belongs to the integrator, and a test of this module should not
    depend on the order in which two agents finish. The ``ImportError`` branch
    keeps this module importable while frontend-dev is still building -- the
    link tests then fail with ``NoReverseMatch``, which is the honest answer.
    """
    try:
        from pretix_custom_reports.views.editor import template_editor_urlpatterns
    except ImportError:  # pragma: no cover - only before frontend-dev lands
        return []
    return list(template_editor_urlpatterns)


def install_plugin_urls():
    """Attach every route of wave 1 and 2 to the plugin URLconf.

    Returns the patterns that were added so the caller can remove them again.
    """
    from pretix_custom_reports import urls as plugin_urls
    from pretix_custom_reports.views.crud import event_urlpatterns
    from pretix_custom_reports.views.portability import portability_event_urlpatterns
    from pretix_custom_reports.views.templates import (
        templates_event_urlpatterns,
        templates_organizer_urlpatterns,
    )

    wanted = (
        list(event_urlpatterns)
        + list(portability_event_urlpatterns)
        + list(templates_event_urlpatterns)
        + list(templates_organizer_urlpatterns)
        + _template_editor_urlpatterns()
    )
    known = {p.name for p in plugin_urls.urlpatterns}
    added = [p for p in wanted if p.name not in known]
    plugin_urls.urlpatterns.extend(added)
    _reload_urlconf()
    return added


def remove_plugin_urls(added):
    from pretix_custom_reports import urls as plugin_urls

    for pattern in added:
        if pattern in plugin_urls.urlpatterns:
            plugin_urls.urlpatterns.remove(pattern)
    _reload_urlconf()


@pytest.fixture(scope="module", autouse=True)
def plugin_urls():
    added = install_plugin_urls()
    yield
    remove_plugin_urls(added)


@pytest.fixture(autouse=True)
def clean_registry_cache():
    """The field table is cached per event primary key, which tests reuse."""
    registry_cache.clear_local_cache()
    yield
    registry_cache.clear_local_cache()


@pytest.fixture
def registry():
    return EventFieldRegistry()


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------


def make_questions(event, tshirt_identifier="tshirt-size", with_newsletter=True):
    """The golden-fixture questions, with a configurable choice identifier."""
    with scopes_disabled():
        tshirt = Question.objects.create(
            event=event,
            question="T-shirt size",
            identifier=tshirt_identifier,
            type=Question.TYPE_CHOICE,
        )
        for position, label in enumerate(("S", "M", "L", "XL")):
            QuestionOption.objects.create(
                question=tshirt, answer=label, position=position
            )
        out = {tshirt_identifier: tshirt}
        if with_newsletter:
            out["newsletter"] = Question.objects.create(
                event=event,
                question="Newsletter opt-in",
                identifier="newsletter",
                type=Question.TYPE_BOOLEAN,
            )
    registry_cache.clear_local_cache()
    return out


def make_items(event):
    with scopes_disabled():
        category = ItemCategory.objects.create(event=event, name="Tickets")
        item = Item.objects.create(
            event=event,
            category=category,
            name="Regular ticket",
            internal_name="regular",
            default_price=23,
            admission=True,
        )
    return item


def definition(
    base="orderposition",
    columns=("order.code",),
    filters=None,
    sorting=(),
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "schema_version": contracts.SCHEMA_VERSION,
        "base": base,
        "columns": [{"field": key} for key in columns],
        "sorting": [{"field": key, "direction": "asc"} for key in sorting],
        "options": {
            "include_canceled_positions": False,
            "include_testmode_orders": False,
            "row_limit": None,
        },
    }
    if filters is not None:
        out["filters"] = filters
    return out


def make_report(event=None, organizer=None, name="Attendee list", **kwargs):
    with scopes_disabled():
        return ReportDefinition.objects.create(
            event=event,
            organizer=organizer,
            name=name,
            base=kwargs.pop("base", "orderposition"),
            definition=kwargs.pop("definition", definition()),
            **kwargs,
        )


def invalid_fixture_paths() -> List[pathlib.Path]:
    return sorted(
        path
        for path in (FIXTURE_DIR / "invalid").glob("*.json")
        if not path.name.startswith("_")
    )


def expectations() -> Dict[str, Any]:
    raw = json.loads(
        (FIXTURE_DIR / "invalid" / "_expectations.json").read_text(encoding="utf-8")
    )
    return raw["fixtures"]


def report_path(name, event, **kwargs):
    return reverse(
        f"plugins:pretix_custom_reports:{name}",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            **kwargs,
        },
    )


# ===========================================================================
# 1. The payload gate
# ===========================================================================


def test_a_valid_file_passes_the_gate():
    raw = (FIXTURE_DIR / "portable" / "report_export.json").read_bytes()
    assert isinstance(load_json_object(raw), dict)


def test_bytes_and_text_are_both_accepted():
    assert load_json_object(b'{"a": 1}') == load_json_object('{"a": 1}')


def test_an_oversized_upload_is_refused():
    raw = b'{"padding": "' + b"x" * (MAX_PAYLOAD_BYTES + 10) + b'"}'
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(raw)
    assert excinfo.value.reason == "too_large"


def test_an_empty_upload_is_refused():
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(b"   \n ")
    assert excinfo.value.reason == "empty"


def test_non_utf8_bytes_are_refused():
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(b'{"name": "\xff\xfe"}')
    assert excinfo.value.reason == "not_utf8"


def test_something_that_is_not_json_is_refused():
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(b"columns: [order.code]")
    assert excinfo.value.reason == "not_json"


def test_a_top_level_array_is_refused():
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(b'[{"schema_version": 1}]')
    assert excinfo.value.reason == "not_json"


def test_a_deeply_nested_document_is_refused_before_parsing():
    depth = MAX_DEPTH + 5
    raw = ('{"a": ' + "[" * depth + "]" * depth + "}").encode()
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(raw)
    assert excinfo.value.reason == "too_deep"


def test_a_json_bomb_by_node_count_is_refused():
    raw = "[" + ",".join("0" for _ in range(MAX_NODES + 10)) + "]"
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object('{"columns": %s}' % raw)
    assert excinfo.value.reason in ("too_many_nodes", "too_large")


def test_an_oversized_string_is_refused():
    raw = json.dumps({"name": "x" * (MAX_STRING_CHARS + 1)})
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(raw)
    assert excinfo.value.reason == "string_too_long"


def test_a_decimal_explosion_is_refused():
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object('{"row_limit": %s}' % ("9" * 400))
    assert excinfo.value.reason == "number_too_long"


def test_an_infinite_float_is_refused():
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object('{"row_limit": 1e999}')
    assert excinfo.value.reason == "not_json"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_javascript_constants_are_refused(constant):
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object('{"row_limit": %s}' % constant)
    assert excinfo.value.reason == "not_json"


def test_a_duplicate_member_is_refused():
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object('{"columns": [], "columns": [{"field": "order.code"}]}')
    assert excinfo.value.reason == "duplicate_key"


#: A high surrogate with no low surrogate behind it. Legal JSON syntax, legal
#: Python ``str``, and not encodable as UTF-8 (S-003).
LONE_SURROGATE = "\ud800"


def test_a_lone_surrogate_is_refused_by_the_gate():
    """``"\\ud800"`` parses, and then poisons everything downstream.

    ``json.loads`` returns it without complaint, the structural validator only
    measures lengths, and the model stores it (Django's ``JSONField`` writes
    with ``ensure_ascii=True``). The damage shows up much later, in every
    response that serialises without that flag. The gate is the only place
    where "this document is text" is still a statement about the whole file.
    """
    raw = b'{"schema_version": 1, "base": "order", "columns": [{"field": "order.code", "label": "\\ud800"}]}'  # noqa: E501
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(raw)
    assert excinfo.value.reason == "not_utf8"


def test_a_lone_surrogate_in_a_member_name_is_refused_too():
    """``_walk`` pushes keys as well as values, and the keys are text too."""
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(b'{"\\udfff": 1}')
    assert excinfo.value.reason == "not_utf8"


def test_a_lone_surrogate_pasted_as_text_is_refused():
    """The paste path never goes through ``bytes.decode``, so it needs its own
    check -- and gets the same one."""
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object('{"name": "%s"}' % LONE_SURROGATE)
    assert excinfo.value.reason == "not_utf8"


def test_an_escaped_surrogate_pair_is_still_accepted():
    """Control group: a *pair* is an ordinary character and must pass.

    Exporters escape non-ASCII, so a report named after an emoji arrives here
    as two ``\\uXXXX`` escapes. Refusing those would break the round trip.
    """
    parsed = load_json_object(b'{"name": "party \\ud83c\\udf89"}')
    assert parsed["name"] == "party \U0001f389"


def test_the_package_never_deserialises_anything_but_json():
    """No pickle, no yaml, no eval -- the property the whole gate rests on.

    Checked on the parsed syntax tree, not on the text: the modules *talk*
    about pickle and eval in their docstrings, which is exactly where that
    reasoning belongs.
    """
    import ast

    package = pathlib.Path(payload_mod.__file__).parent
    forbidden_calls = {"eval", "exec", "compile", "__import__"}
    forbidden_modules = {"pickle", "marshal", "yaml", "shelve", "subprocess", "os"}
    offenders = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden_modules:
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in forbidden_modules:
                    offenders.append(f"{path.name}: from {node.module}")
            elif isinstance(node, ast.Call):
                target = node.func
                name = getattr(target, "id", None) or getattr(target, "attr", None)
                if name in forbidden_calls:
                    offenders.append(f"{path.name}: {name}()")
    assert offenders == []


# ===========================================================================
# 2. The file format
# ===========================================================================


@pytest.mark.django_db
def test_an_exported_file_validates_against_the_frozen_envelope(event, registry):
    make_questions(event)
    report = make_report(
        event=event,
        definition=definition(columns=("order.code", "answer.tshirt-size")),
    )
    with scopes_disabled():
        document = build_export_document(report, event=event, registry=registry)
    portable = contracts.validate_portable_document(document)
    assert portable.name == report.name
    assert portable.definition.as_dict() == report.definition


@pytest.mark.django_db
def test_the_export_carries_the_metadata_the_spec_asks_for(
    event, registry, user_with_perms
):
    make_questions(event)
    report = make_report(
        event=event,
        description="Everyone who ordered a shirt",
        created_by=user_with_perms,
        definition=definition(columns=("order.code", "answer.tshirt-size")),
    )
    with scopes_disabled():
        document = build_export_document(report, event=event, registry=registry)

    assert document["schema_version"] == contracts.SCHEMA_VERSION
    assert document["name"] == "Attendee list"
    assert document["description"] == "Everyone who ordered a shirt"
    assert document["source"] == "dummy/dummy"
    assert document["generator"].startswith("pretix-custom-reports ")
    assert document["exported_at"]
    assert document["meta"]["pretix_version"]
    assert document["meta"]["base"] == "orderposition"
    assert document["meta"]["created_by"] == user_with_perms.email
    assert document["meta"]["identifier"] == report.identifier


@pytest.mark.django_db
def test_the_export_describes_event_specific_keys_by_name(event, registry):
    make_questions(event)
    report = make_report(
        event=event,
        definition=definition(columns=("order.code", "answer.tshirt-size")),
    )
    with scopes_disabled():
        document = build_export_document(report, event=event, registry=registry)
    references = document["meta"]["references"]
    assert [ref["key"] for ref in references] == ["answer.tshirt-size"]
    assert references[0]["label"] == "T-shirt size"
    assert references[0]["kind"] == "question"
    assert references[0]["identifier"] == "tshirt-size"


@pytest.mark.django_db
def test_the_export_contains_no_primary_key(event, registry, user_with_perms):
    make_questions(event)
    make_items(event)
    report = make_report(
        event=event,
        created_by=user_with_perms,
        definition=definition(
            columns=("order.code", "item.name", "answer.tshirt-size"),
            filters={
                "op": "and",
                "children": [
                    {
                        "field": "item.name",
                        "operator": "in",
                        "value": ["Regular ticket"],
                    }
                ],
            },
        ),
    )
    with scopes_disabled():
        document = build_export_document(report, event=event, registry=registry)

    # Every primary key that exists in this test. ``schema_version`` is the one
    # legitimate small integer in the format, so it is exempt -- everything else
    # that happens to equal a primary key would be one.
    forbidden = {
        report.pk,
        event.pk,
        event.organizer.pk,
        user_with_perms.pk,
    }
    text = json.dumps(document)
    numbers = {
        value
        for key, value in _walk_pairs(document)
        if isinstance(value, int)
        and not isinstance(value, bool)
        and key != "schema_version"
    }
    assert not (forbidden & numbers), text
    assert '"id"' not in text and '"pk"' not in text


def _walk_pairs(node, key=None):
    """Yield ``(member name, value)`` for every leaf of a JSON document."""
    if isinstance(node, dict):
        for name, value in node.items():
            yield from _walk_pairs(value, name)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_pairs(value, key)
    else:
        yield key, node


@pytest.mark.django_db
def test_the_file_name_is_safe(event):
    report = make_report(event=event, name='../../etc/passwd "; rm -rf /')
    name = export_filename(report)
    assert name.startswith("report_") and name.endswith(".json")
    assert "/" not in name and '"' not in name and ".." not in name


def test_a_bare_definition_is_accepted_as_well_as_an_envelope():
    envelope = json.loads(
        (FIXTURE_DIR / "portable" / "report_export.json").read_text(encoding="utf-8")
    )
    from_envelope = parse_document(envelope)
    from_bare = parse_document(envelope["definition"])
    assert from_envelope.was_envelope is True
    assert from_bare.was_envelope is False
    assert from_envelope.definition == from_bare.definition


def test_the_envelope_metadata_is_cleaned_before_it_is_used():
    raw = json.loads(
        (FIXTURE_DIR / "portable" / "report_export.json").read_text(encoding="utf-8")
    )
    # A null byte and a tab in the name; both would otherwise end up in
    # HTML, in a log entry and in a Content-Disposition header.
    raw["name"] = "Ann" + chr(0) + "ual" + chr(9) + "report"
    raw["meta"] = {"identifier": "not valid!", "references": "nonsense"}
    parsed = parse_document(raw)
    assert parsed.name == "Annualreport"
    assert parsed.identifier == ""
    assert parsed.references == ()


# ===========================================================================
# 3. The round trip
# ===========================================================================


@pytest.mark.django_db
def test_export_then_import_gives_an_identical_definition(event, registry):
    make_questions(event)
    make_items(event)
    original = definition(
        columns=("order.code", "item.name", "answer.tshirt-size"),
        filters={
            "op": "and",
            "children": [
                {"field": "order.status", "operator": "in", "value": ["p", "n"]},
                {
                    "field": "answer.tshirt-size",
                    "operator": "in",
                    "value": ["L", "XL"],
                },
            ],
        },
        sorting=("order.code",),
    )
    report = make_report(event=event, definition=original)

    with scopes_disabled():
        document = build_export_document(report, event=event, registry=registry)
        plan = plan_import(json.dumps(document), event=event, registry=registry)
        assert plan.ok, plan.report.as_dict()
        imported = commit_import(plan)

    assert imported.definition == report.definition
    assert imported.pk != report.pk


@pytest.mark.django_db
def test_the_round_trip_survives_a_second_event_with_the_same_questions(
    event, event_without_plugin, registry
):
    make_questions(event)
    make_questions(event_without_plugin)
    report = make_report(
        event=event,
        definition=definition(columns=("order.code", "answer.tshirt-size")),
    )
    with scopes_disabled():
        document = build_export_document(report, event=event, registry=registry)
        plan = plan_import(
            json.dumps(document),
            event=event_without_plugin,
            registry=registry,
        )
        assert plan.ok
        imported = commit_import(plan)
    assert imported.definition == report.definition
    assert imported.event_id == event_without_plugin.pk


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    sorted(p for p in FIXTURE_DIR.glob("*.json") if not p.name.startswith("_")),
    ids=lambda path: path.name,
)
def test_every_golden_fixture_round_trips_through_a_file(event, registry, path):
    """The valid fixtures are what every other agent tests against."""
    make_questions(event)
    make_items(event)
    from .test_registry_support import make_meta_property

    make_meta_property(event.organizer)
    registry_cache.clear_local_cache()

    source = json.loads(path.read_text(encoding="utf-8"))
    with scopes_disabled():
        report = make_report(
            event=event, base=source["base"], definition=source, name=path.stem
        )
        document = build_export_document(report, event=event, registry=registry)
        parsed = parse_document(document)
    assert parsed.definition.as_dict() == report.definition


# ===========================================================================
# 4. Every invalid fixture, one test each
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.parametrize("path", invalid_fixture_paths(), ids=lambda path: path.name)
def test_every_invalid_fixture_is_rejected_on_import(event, registry, path):
    make_questions(event)
    make_items(event)
    expected = expectations()[path.name]
    text = path.read_text(encoding="utf-8")

    with scopes_disabled():
        if expected["stage"] == "structure":
            with pytest.raises(contracts.DefinitionValidationError) as excinfo:
                plan_import(text, event=event, registry=registry)
            for code in expected.get("codes", []):
                assert code in excinfo.value.codes
        else:
            plan = plan_import(text, event=event, registry=registry)
            assert not plan.ok
            assert plan.report.has_problems
            with pytest.raises(ImportRejected):
                commit_import(plan)
        assert ReportDefinition.objects.count() == 0


@pytest.mark.django_db
def test_a_smuggled_orm_path_is_rejected_not_sanitised(event, registry):
    """The attack the contracts were built against (invalid/smuggled_orm_path.json)."""
    text = (FIXTURE_DIR / "invalid" / "smuggled_orm_path.json").read_text(
        encoding="utf-8"
    )
    with scopes_disabled():
        with pytest.raises(contracts.DefinitionValidationError) as excinfo:
            plan_import(text, event=event, registry=registry)
        codes = excinfo.value.codes
        assert "invalid_field_key" in codes
        assert "unknown_key" in codes
        # Not "the orm_path member was ignored": the whole document is refused.
        assert ReportDefinition.objects.count() == 0


@pytest.mark.django_db
def test_skipping_unknown_keys_still_reports_every_one_of_them(event, registry):
    """The one invalid fixture a *decision* can rescue -- and it is visible."""
    text = (FIXTURE_DIR / "invalid" / "unknown_field_key.json").read_text(
        encoding="utf-8"
    )
    with scopes_disabled():
        plan = plan_import(
            text, event=event, registry=registry, strategy=ResolutionStrategy.SKIP
        )
        assert plan.ok
        dropped = {entry.source for entry in plan.report.dropped}
        assert dropped == {"order.does_not_exist", "answer.question-that-was-renamed"}
        imported = commit_import(plan)
    assert [c["field"] for c in imported.definition["columns"]] == ["order.code"]


@pytest.mark.django_db
def test_an_import_that_would_lose_every_column_is_refused(event, registry):
    text = json.dumps(definition(columns=("answer.gone", "answer.also-gone")))
    with scopes_disabled():
        plan = plan_import(
            text, event=event, registry=registry, strategy=ResolutionStrategy.SKIP
        )
    assert not plan.ok
    assert any("column" in issue for issue in plan.report.issues)


# ===========================================================================
# 5. The resolution layer
# ===========================================================================


@pytest.mark.django_db
def test_an_unknown_key_is_reported_never_swallowed(event, registry):
    document = contracts.validate_definition(
        definition(columns=("order.code", "answer.nope"))
    )
    with scopes_disabled():
        outcome = resolve_definition(document, event=event, registry=registry)
    assert not outcome.ok
    statuses = {e.source: e.status for e in outcome.report.fields}
    assert statuses == {"order.code": STATUS_FOUND, "answer.nope": STATUS_MISSING}
    assert outcome.report.missing[0].path == "columns[1]"


@pytest.mark.django_db
def test_a_differently_spelled_identifier_is_mapped_and_shown(event, registry):
    make_questions(event, tshirt_identifier="tshirt_size")
    document = contracts.validate_definition(
        definition(columns=("order.code", "answer.TShirt-Size"))
    )
    with scopes_disabled():
        outcome = resolve_definition(document, event=event, registry=registry)
    assert outcome.ok
    mapped = outcome.report.mapped[0]
    assert mapped.source == "answer.TShirt-Size"
    assert mapped.target == "answer.tshirt_size"
    assert mapped.match == "identifier"
    assert outcome.as_dict()["columns"][1]["field"] == "answer.tshirt_size"


@pytest.mark.django_db
def test_a_renamed_question_is_matched_by_name_from_the_file(event, registry):
    with scopes_disabled():
        Question.objects.create(
            event=event,
            question="T-shirt size",
            identifier="apparel",
            type=Question.TYPE_STRING,
        )
    registry_cache.clear_local_cache()
    document = contracts.validate_definition(
        definition(columns=("order.code", "answer.shirt"))
    )
    with scopes_disabled():
        outcome = resolve_definition(
            document,
            event=event,
            registry=registry,
            references=[
                Reference(key="answer.shirt", label="T-shirt size", kind="question")
            ],
        )
    assert outcome.ok
    mapped = outcome.report.mapped[0]
    assert mapped.target == "answer.apparel"
    assert mapped.match == "name"
    assert mapped.source_label == "T-shirt size"


@pytest.mark.django_db
def test_two_equally_good_candidates_are_ambiguous_not_a_guess(event, registry):
    with scopes_disabled():
        Question.objects.create(
            event=event,
            question="Size",
            identifier="t-shirt",
            type=Question.TYPE_STRING,
        )
        Question.objects.create(
            event=event, question="Size", identifier="tshirt", type=Question.TYPE_STRING
        )
    registry_cache.clear_local_cache()
    document = contracts.validate_definition(
        definition(columns=("order.code", "answer.t_shirt"))
    )
    with scopes_disabled():
        outcome = resolve_definition(document, event=event, registry=registry)
    assert not outcome.ok
    problem = outcome.report.missing[0]
    assert problem.status == "ambiguous"
    assert "t-shirt" in problem.detail and "tshirt" in problem.detail


@pytest.mark.django_db
def test_a_core_key_is_never_matched_by_similarity(event, registry):
    """``order.code_`` must not quietly become ``order.code``."""
    document = contracts.validate_definition(
        definition(columns=("order.code", "order.CODE"))
    )
    with scopes_disabled():
        outcome = resolve_definition(document, event=event, registry=registry)
    assert not outcome.ok
    assert outcome.report.missing[0].source == "order.CODE"


@pytest.mark.django_db
def test_a_filter_value_is_matched_against_this_events_values(event, registry):
    make_items(event)
    document = contracts.validate_definition(
        definition(
            base="orderposition",
            columns=("order.code",),
            filters={
                "op": "and",
                "children": [
                    {
                        "field": "item.name",
                        "operator": "in",
                        "value": ["regular TICKET", "Gone forever"],
                    }
                ],
            },
        )
    )
    with scopes_disabled():
        outcome = resolve_definition(
            document, event=event, registry=registry, strategy=ResolutionStrategy.SKIP
        )
    assert outcome.ok
    values = {e.source: e for e in outcome.report.values}
    assert values["regular TICKET"].status == STATUS_MAPPED
    assert values["regular TICKET"].target == "Regular ticket"
    assert values["Gone forever"].status == STATUS_MISSING
    assert values["Gone forever"].dropped is True
    condition = outcome.as_dict()["filters"]["children"][0]
    assert condition["value"] == ["Regular ticket"]


@pytest.mark.django_db
def test_a_condition_whose_values_all_vanish_is_removed(event, registry):
    make_items(event)
    document = contracts.validate_definition(
        definition(
            columns=("order.code",),
            filters={
                "op": "and",
                "children": [
                    {"field": "item.name", "operator": "in", "value": ["Nope"]}
                ],
            },
        )
    )
    with scopes_disabled():
        outcome = resolve_definition(
            document, event=event, registry=registry, strategy=ResolutionStrategy.SKIP
        )
    assert outcome.ok
    assert "filters" not in outcome.as_dict()


@pytest.mark.django_db
def test_abort_keeps_the_value_and_blocks(event, registry):
    make_items(event)
    document = contracts.validate_definition(
        definition(
            columns=("order.code",),
            filters={
                "op": "and",
                "children": [
                    {"field": "item.name", "operator": "in", "value": ["Nope"]}
                ],
            },
        )
    )
    with scopes_disabled():
        outcome = resolve_definition(document, event=event, registry=registry)
    assert not outcome.ok
    assert outcome.report.missing[0].dropped is False


@pytest.mark.django_db
def test_keep_never_fails_and_never_drops(event, registry):
    document = contracts.validate_definition(
        definition(columns=("order.code", "answer.gone"))
    )
    with scopes_disabled():
        outcome = resolve_definition(
            document, event=event, registry=registry, strategy=ResolutionStrategy.KEEP
        )
    assert outcome.document is not None
    assert [c["field"] for c in outcome.as_dict()["columns"]] == [
        "order.code",
        "answer.gone",
    ]
    assert outcome.report.missing[0].source == "answer.gone"


@pytest.mark.django_db
def test_an_unknown_strategy_falls_back_to_the_safe_one(event, registry):
    document = contracts.validate_definition(
        definition(columns=("order.code", "answer.gone"))
    )
    with scopes_disabled():
        outcome = resolve_definition(
            document, event=event, registry=registry, strategy="delete-everything"
        )
    assert outcome.report.strategy == ResolutionStrategy.ABORT
    assert not outcome.ok


@pytest.mark.parametrize("value", ["abort", "skip"])
def test_the_two_offered_strategies_survive_the_narrow_coercion(value):
    assert ResolutionStrategy.coerce_user_choice(value) == value


@pytest.mark.parametrize(
    "value", ["keep", "KEEP", " keep", None, "", 1, ["skip"], "delete-everything"]
)
def test_anything_the_form_does_not_offer_becomes_abort(value):
    """``keep`` is the event copy's strategy, not a radio button (S-006).

    It switches off the compiler's own check inside ``resolve_definition``, so
    a request must not be able to ask for it. Everything the interface does not
    offer collapses to the strategy that blocks rather than writes.
    """
    assert ResolutionStrategy.coerce_user_choice(value) == ResolutionStrategy.ABORT


def test_keep_stays_reachable_for_the_programmatic_caller():
    """``eventcopy.py`` asks for it by name; the wide coercion keeps working."""
    assert ResolutionStrategy.coerce("keep") == ResolutionStrategy.KEEP


@pytest.mark.django_db
def test_the_registry_stage_rejects_a_usage_the_target_forbids(event, registry):
    """A position field without an aggregate on base ``order`` (SPEC.md F3)."""
    document = contracts.validate_definition(
        definition(base="order", columns=("order.code", "position.price"))
    )
    with scopes_disabled():
        outcome = resolve_definition(document, event=event, registry=registry)
    assert not outcome.ok
    assert outcome.report.issues


# ===========================================================================
# 6. The importer
# ===========================================================================


@pytest.mark.django_db
def test_planning_an_import_writes_nothing(event, registry):
    text = json.dumps(definition(columns=("order.code",)))
    with scopes_disabled():
        plan = plan_import(text, event=event, registry=registry)
        assert plan.ok
        assert ReportDefinition.objects.count() == 0


@pytest.mark.django_db
def test_committing_an_import_creates_one_row_and_one_log_entry(
    event, registry, user_with_perms
):
    text = json.dumps(definition(columns=("order.code",)))
    with scopes_disabled():
        plan = plan_import(text, event=event, registry=registry)
        report = commit_import(plan, user=user_with_perms)
        assert ReportDefinition.objects.count() == 1
        entries = list(
            LogEntry.objects.filter(action_type=contracts.LOG_ACTION_IMPORTED)
        )
    assert len(entries) == 1
    logged = json.loads(entries[0].data)
    assert logged["import"]["resolution"]["counts"]["found"] == 1
    assert report.event_id == event.pk and report.organizer_id is None


@pytest.mark.django_db
def test_the_identifier_travels_with_the_file_and_is_suffixed_on_collision(
    event, registry
):
    original = make_report(event=event)
    with scopes_disabled():
        document = build_export_document(original, event=event, registry=registry)
        plan = plan_import(json.dumps(document), event=event, registry=registry)
        imported = commit_import(plan)
    assert imported.identifier.startswith(original.identifier[:6])
    assert imported.identifier != original.identifier


@pytest.mark.django_db
def test_a_hostile_identifier_in_the_file_is_dropped(event, registry):
    document = json.loads(
        (FIXTURE_DIR / "portable" / "report_export.json").read_text(encoding="utf-8")
    )
    make_questions(event)
    document["meta"] = {"identifier": "../../secret"}
    with scopes_disabled():
        plan = plan_import(json.dumps(document), event=event, registry=registry)
        imported = commit_import(plan)
    assert imported.identifier and "/" not in imported.identifier


@pytest.mark.django_db
def test_commit_refuses_a_plan_that_is_not_ok(event, registry):
    text = json.dumps(definition(columns=("order.code", "answer.gone")))
    with scopes_disabled():
        plan = plan_import(text, event=event, registry=registry)
        with pytest.raises(ImportRejected) as excinfo:
            commit_import(plan)
        assert ReportDefinition.objects.count() == 0
    assert excinfo.value.report is plan.report


# ===========================================================================
# 7. The views
# ===========================================================================


@pytest.mark.django_db
def test_export_view_serves_a_file(client_with_perms, event):
    report = make_report(event=event)
    response = client_with_perms.get(
        report_path("event.reports.export", event, report=report.pk)
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert "attachment" in response["Content-Disposition"]
    document = json.loads(response.content.decode("utf-8"))
    assert contracts.validate_portable_document(document).name == report.name


@pytest.mark.django_db
def test_export_view_logs_the_download(client_with_perms, event):
    report = make_report(event=event)
    client_with_perms.get(report_path("event.reports.export", event, report=report.pk))
    with scopes_disabled():
        assert LogEntry.objects.filter(
            action_type=contracts.LOG_ACTION_EXPORTED
        ).exists()


@pytest.mark.django_db
def test_export_view_refuses_a_report_of_another_event(
    client_with_perms, event, event_without_plugin
):
    other = make_report(event=event_without_plugin)
    response = client_with_perms.get(
        report_path("event.reports.export", event, report=other.pk)
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_export_view_is_permission_checked(client_without_perms, event):
    report = make_report(event=event)
    response = client_without_perms.get(
        report_path("event.reports.export", event, report=report.pk)
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_the_views_are_404_when_the_plugin_is_off(
    client_with_perms, event_without_plugin
):
    report = make_report(event=event_without_plugin)
    response = client_with_perms.get(
        report_path("event.reports.export", event_without_plugin, report=report.pk)
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_import_view_shows_the_form(client_with_perms, event):
    response = client_with_perms.get(report_path("event.reports.import", event))
    assert response.status_code == 200
    assert b'name="file"' in response.content
    assert b'name="text"' in response.content


@pytest.mark.django_db
def test_import_view_is_permission_checked(client_view_only, event):
    response = client_view_only.get(report_path("event.reports.import", event))
    assert response.status_code == 403


@pytest.mark.django_db
def test_import_view_uploads_a_file_and_asks_before_writing(client_with_perms, event):
    import io

    make_questions(event)
    payload = json.dumps(
        {
            "schema_version": 1,
            "name": "From a file",
            "definition": definition(columns=("order.code", "answer.tshirt-size")),
        }
    ).encode()
    upload = io.BytesIO(payload)
    upload.name = "report.json"

    response = client_with_perms.post(
        report_path("event.reports.import", event), {"file": upload}
    )
    assert response.status_code == 200
    assert b"From a file" in response.content
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0

    response = client_with_perms.post(
        report_path("event.reports.import", event),
        {"document": payload.decode(), "action": "confirm", "strategy": "abort"},
    )
    assert response.status_code == 302
    with scopes_disabled():
        assert ReportDefinition.objects.get().name == "From a file"


@pytest.mark.django_db
def test_the_document_survives_the_confirmation_form(client_with_perms, event):
    """The hidden field really carries the document, quotes and all.

    The confirmation re-parses the original text, so if HTML escaping mangled
    it on the way through the form, step two would fail on a document that step
    one accepted. Posting a hand-built body would not notice that; this test
    takes the value out of the rendered page.
    """
    import html
    import re

    make_questions(event)
    original = json.dumps(
        {
            "schema_version": 1,
            "name": 'Quote "test"',
            "definition": definition(columns=("order.code", "answer.tshirt-size")),
        }
    )
    url = report_path("event.reports.import", event)
    response = client_with_perms.post(url, {"text": original})
    assert response.status_code == 200

    match = re.search(
        r'name="document" value="([^"]*)"', response.content.decode("utf-8")
    )
    assert match, "the confirmation page must carry the original document"
    carried = html.unescape(match.group(1))
    assert json.loads(carried) == json.loads(original)

    response = client_with_perms.post(
        url, {"document": carried, "action": "confirm", "strategy": "abort"}
    )
    assert response.status_code == 302
    with scopes_disabled():
        assert ReportDefinition.objects.get().name == 'Quote "test"'


@pytest.mark.django_db
def test_import_view_pastes_a_bare_definition(client_with_perms, event):
    response = client_with_perms.post(
        report_path("event.reports.import", event),
        {"text": json.dumps(definition(columns=("order.code",)))},
    )
    assert response.status_code == 200
    response = client_with_perms.post(
        report_path("event.reports.import", event),
        {
            "document": json.dumps(definition(columns=("order.code",))),
            "action": "confirm",
        },
    )
    assert response.status_code == 302
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 1


@pytest.mark.django_db
def test_a_successful_import_lands_in_the_graphical_editor(client_with_perms, event):
    """Not in the JSON form.

    Looking at what just arrived is the next thing a user does after an import,
    and the editor is where that happens -- the plain form of ``views/crud.py``
    is a repair path, not a destination (same move as ``ReportDuplicateView``).
    ``editor.edit`` is keyed on the stable identifier, the form on the primary
    key, so the two are not interchangeable.
    """
    response = client_with_perms.post(
        report_path("event.reports.import", event),
        {
            "document": json.dumps(definition(columns=("order.code",))),
            "action": "confirm",
        },
    )
    assert response.status_code == 302
    with scopes_disabled():
        stored = ReportDefinition.objects.get()
    assert response["Location"] == report_path(
        "editor.edit", event, identifier=stored.identifier
    )
    assert not response["Location"].endswith(f"/{stored.pk}/")


@pytest.mark.django_db
def test_import_view_refuses_to_write_while_a_key_is_unresolved(
    client_with_perms, event
):
    text = json.dumps(definition(columns=("order.code", "answer.gone")))
    response = client_with_perms.post(
        report_path("event.reports.import", event),
        {"document": text, "action": "confirm", "strategy": "abort"},
    )
    assert response.status_code == 200
    assert b"answer.gone" in response.content
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


@pytest.mark.django_db
def test_import_view_writes_after_the_user_chose_to_skip(client_with_perms, event):
    text = json.dumps(definition(columns=("order.code", "answer.gone")))
    response = client_with_perms.post(
        report_path("event.reports.import", event),
        {"document": text, "action": "confirm", "strategy": "skip"},
    )
    assert response.status_code == 302
    with scopes_disabled():
        stored = ReportDefinition.objects.get()
    assert [c["field"] for c in stored.definition["columns"]] == ["order.code"]


@pytest.mark.django_db
def test_import_view_reports_a_broken_file_without_a_traceback(
    client_with_perms, event
):
    response = client_with_perms.post(
        report_path("event.reports.import", event), {"text": "{not json"}
    )
    assert response.status_code == 200
    assert b"JSON" in response.content


@pytest.mark.django_db
def test_import_view_shows_structural_problems_of_the_document(
    client_with_perms, event
):
    text = (FIXTURE_DIR / "invalid" / "unknown_top_level_key.json").read_text(
        encoding="utf-8"
    )
    response = client_with_perms.post(
        report_path("event.reports.import", event), {"text": text}
    )
    assert response.status_code == 200
    assert b"raw_sql" in response.content
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


@pytest.mark.django_db
def test_import_view_needs_something_to_import(client_with_perms, event):
    response = client_with_perms.post(report_path("event.reports.import", event), {})
    assert response.status_code == 200


@pytest.mark.django_db
def test_import_view_refuses_a_file_carrying_a_lone_surrogate(client_with_perms, event):
    """The realistic way in for S-003: an uploaded file, not self-inflicted.

    It has to fail *here*, at the gate, and not later with a 500 on the first
    preview of the imported report.
    """
    import io

    payload = (
        b'{"schema_version": 1, "name": "Poisoned", "definition": '
        b'{"schema_version": 1, "base": "orderposition", "columns": '
        b'[{"field": "order.code", "label": "x\\ud800"}], "sorting": [], '
        b'"options": {"include_canceled_positions": false, '
        b'"include_testmode_orders": false, "row_limit": null}}}'
    )
    upload = io.BytesIO(payload)
    upload.name = "report.json"

    response = client_with_perms.post(
        report_path("event.reports.import", event), {"file": upload}
    )
    assert response.status_code == 200
    assert b"Poisoned" not in response.content
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


@pytest.mark.django_db
def test_import_view_refuses_a_pasted_lone_surrogate(client_with_perms, event):
    document = definition(columns=("order.code",))
    document["columns"][0]["label"] = "x" + LONE_SURROGATE
    text = json.dumps(document)  # ensure_ascii=True, so this is "\ud800"
    response = client_with_perms.post(
        report_path("event.reports.import", event), {"text": text}
    )
    assert response.status_code == 200
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


@pytest.mark.django_db
def test_export_view_serves_a_file_even_for_a_stored_lone_surrogate(
    client_with_perms, event
):
    """Rows written before the gate learned about surrogates still exist.

    The definition below cannot arrive through the importer any more, but it
    can already be in the database -- and a download must then be a file, not
    a ``UnicodeEncodeError``.
    """
    document = definition(columns=("order.code",))
    document["columns"][0]["label"] = "x" + LONE_SURROGATE
    report = make_report(event=event, name="Poisoned", definition=document)

    response = client_with_perms.get(
        report_path("event.reports.export", event, report=report.pk)
    )
    assert response.status_code == 200
    parsed = json.loads(response.content.decode("utf-8"))
    assert parsed["definition"]["columns"][0]["label"] == "x" + LONE_SURROGATE


@pytest.mark.django_db
def test_import_view_ignores_a_hand_posted_keep_strategy(client_with_perms, event):
    """``strategy=keep`` is not one of the two radio buttons (S-006).

    ``position.price`` on base ``order`` resolves -- the field exists there --
    but needs an aggregate, which is what the compiler check inside
    ``resolve_definition`` says. Under ``keep`` that check is skipped, so the
    row would be stored. Downgraded to ``abort``, it is not.
    """
    text = json.dumps(definition(base="order", columns=("position.price",)))
    response = client_with_perms.post(
        report_path("event.reports.import", event),
        {"document": text, "action": "confirm", "strategy": "keep"},
    )
    assert response.status_code == 200
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("strategy", ["abort", "skip"])
def test_import_view_refuses_the_same_definition_under_both_offered_strategies(
    client_with_perms, event, strategy
):
    """Control group for the test above: nothing changed for the real choices."""
    text = json.dumps(definition(base="order", columns=("position.price",)))
    response = client_with_perms.post(
        report_path("event.reports.import", event),
        {"document": text, "action": "confirm", "strategy": strategy},
    )
    assert response.status_code == 200
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


# ===========================================================================
# 8. The event copy
# ===========================================================================


@pytest.mark.django_db
def test_reports_travel_with_an_event_copy(event, event_without_plugin, registry):
    make_questions(event)
    make_questions(event_without_plugin)
    source = make_report(
        event=event,
        definition=definition(columns=("order.code", "answer.tshirt-size")),
    )
    with scopes_disabled():
        result = copy_reports_to_event(
            event_without_plugin, event, registry=registry, question_map={1: object()}
        )
        assert result.count == 1
        copy = ReportDefinition.objects.get(event=event_without_plugin)
    assert copy.definition == source.definition
    assert copy.identifier == source.identifier
    assert result.copied[0].resolution.is_clean


@pytest.mark.django_db
def test_an_event_copy_translates_a_renamed_reference(
    event, event_without_plugin, registry
):
    make_questions(event, tshirt_identifier="tshirt-size")
    make_questions(event_without_plugin, tshirt_identifier="tshirt_size")
    make_report(
        event=event,
        definition=definition(columns=("order.code", "answer.tshirt-size")),
    )
    with scopes_disabled():
        copy_reports_to_event(event_without_plugin, event, registry=registry)
        copy = ReportDefinition.objects.get(event=event_without_plugin)
    assert [c["field"] for c in copy.definition["columns"]] == [
        "order.code",
        "answer.tshirt_size",
    ]


@pytest.mark.django_db
def test_an_event_copy_never_loses_a_report_it_cannot_resolve(
    event, event_without_plugin, registry
):
    make_report(
        event=event,
        definition=definition(columns=("order.code", "answer.only-here")),
    )
    with scopes_disabled():
        result = copy_reports_to_event(event_without_plugin, event, registry=registry)
        copy = ReportDefinition.objects.get(event=event_without_plugin)
    assert result.count == 1
    assert [c["field"] for c in copy.definition["columns"]] == [
        "order.code",
        "answer.only-here",
    ]
    assert result.copied[0].resolution.missing[0].source == "answer.only-here"


@pytest.mark.django_db
def test_an_event_copy_still_uses_the_keep_strategy(
    event, event_without_plugin, registry
):
    """Narrowing the *views* must not narrow the event copy (S-006).

    Same definition the import view refuses under ``keep``: resolvable, but
    rejected by the compiler check. A copy carries it anyway, because dropping
    a report during ``Event.copy_data_from`` -- where nobody is at a screen --
    would be a silent loss. The check belongs to the import path, not here.
    """
    make_report(
        event=event,
        name="Needs an aggregate",
        base="order",
        definition=definition(base="order", columns=("position.price",)),
    )
    with scopes_disabled():
        result = copy_reports_to_event(event_without_plugin, event, registry=registry)
        copy = ReportDefinition.objects.get(event=event_without_plugin)
    assert result.count == 1
    assert result.copied[0].resolution.strategy == ResolutionStrategy.KEEP
    assert [c["field"] for c in copy.definition["columns"]] == ["position.price"]


@pytest.mark.django_db
def test_an_event_copy_leaves_the_source_alone(event, event_without_plugin, registry):
    source = make_report(event=event)
    before = dict(source.definition)
    with scopes_disabled():
        copy_reports_to_event(event_without_plugin, event, registry=registry)
        source.refresh_from_db()
        assert source.definition == before
        assert ReportDefinition.objects.filter(event=event).count() == 1


@pytest.fixture
def client_view_only(client, organizer):
    """May read reports but not change them."""
    from pretix.base.models import Team, User

    user = User.objects.create_user("portability-view@example.org", PASSWORD)
    team = Team.objects.create(
        organizer=organizer,
        name="view only",
        all_events=True,
        all_event_permissions=False,
        limit_event_permissions={"event.orders:read": True},
    )
    team.members.add(user)
    client.login(email=user.email, password=PASSWORD)
    return client


@pytest.mark.django_db
def test_an_event_copy_across_organizers_still_finds_the_reports(event, registry):
    """pretix can copy an event into another organizer (``is_cross_organizer``).

    The scope active during the copy is the *target* organizer, so a lookup
    through ``source_event.custom_reports`` would silently return nothing.
    """
    import datetime
    from django.utils.timezone import now
    from pretix.base.models import Event, Organizer

    make_report(event=event, name="Travels along")
    other = Organizer.objects.create(name="Elsewhere", slug="elsewhere")
    with scopes_disabled():
        target = Event.objects.create(
            organizer=other,
            name="Copy",
            slug="copy",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
            live=True,
        )

    with scope(organizer=other):
        result = copy_reports_to_event(target, event, registry=registry)

    assert result.count == 1
    with scopes_disabled():
        assert ReportDefinition.objects.get(event=target).name == "Travels along"


@pytest.mark.django_db
def test_scope_is_not_needed_to_read_the_report_of_a_plan(event, registry):
    """The resolver is pure: no scope, no queries beyond the registry."""
    with scope(organizer=event.organizer):
        document = contracts.validate_definition(definition(columns=("order.code",)))
        outcome = resolve_definition(document, event=event, registry=registry)
    assert outcome.report.as_dict()["counts"]["found"] == 1
