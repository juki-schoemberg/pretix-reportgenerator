# Owner from wave 3 on: security-reviewer (see ORCHESTRIERUNG.md section 5)
"""Adversarial tests. Every test in here tries to break something on purpose.

This module is not a second functional test suite. It only contains attacks,
and it is structured by attack surface rather than by module:

1. registry bypass -- can an ORM path, a lookup or an operator reach a queryset
   without coming out of a ``ReportField``?
2. event and tenant isolation
3. permissions, endpoint by endpoint, with three hostile actors
4. import as an attack surface
5. output: injection, file names, escaping
6. background execution: scheduled exports, permissions, django-scopes
7. resources

Findings are documented in ``docs/security-review.md``; every finding names the
test here that proves it. Tests that prove a *current defect* are marked
``@pytest.mark.xfail(strict=True)`` so the suite stays green while the defect
exists and turns red the moment somebody fixes it without removing the marker.
The finding text says which ones those are.

Routing note, same as ``tests/test_permissions.py`` and
``tests/test_portability.py``: ``urls.py`` belongs to the integrator and is
wired in wave 4. The module-scoped ``security_routes`` fixture attaches every
route of waves 1 and 2 so the view tests run through the real resolver, the real
control middleware and the real permission decorators.
"""

from typing import Any, Dict, List

import ast
import datetime
import importlib
import json
import pathlib
import pytest
import re
import warnings
from decimal import Decimal
from django.test import Client
from django.urls import clear_url_caches, reverse
from django.utils.timezone import now
from django_scopes import scope, scopes_disabled
from pretix.base.models import (
    Event,
    Item,
    Order,
    OrderPosition,
    Organizer,
    Question,
    QuestionOption,
    ScheduledEventExport,
    Team,
    User,
)
from pretix.base.services.export import (
    ExportError,
    init_event_exporter,
    init_organizer_exporters,
    run_scheduled_exports,
)
from pretix.base.signals import (
    register_data_exporters,
    register_multievent_data_exporters,
)

import pretix_custom_reports
from pretix_custom_reports import contracts, exporters
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.portability.errors import PayloadRejected
from pretix_custom_reports.portability.importer import plan_import
from pretix_custom_reports.portability.payload import (
    MAX_DEPTH,
    MAX_NODES,
    MAX_PAYLOAD_BYTES,
    MAX_STRING_CHARS,
    load_json_object,
)
from pretix_custom_reports.signals import URL_NAMESPACE
from pretix_custom_reports.views.api import api_urlpatterns
from pretix_custom_reports.views.crud import event_urlpatterns
from pretix_custom_reports.views.editor import editor_urlpatterns
from pretix_custom_reports.views.portability import portability_event_urlpatterns
from pretix_custom_reports.views.templates import (
    templates_event_urlpatterns,
    templates_organizer_urlpatterns,
)

from .conftest import PASSWORD

PLUGIN_ROOT = pathlib.Path(pretix_custom_reports.__file__).resolve().parent
FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "definitions"

CHANGE_PERMISSION = "event.settings.general:write"
VIEW_PERMISSION = "event.orders:read"
ORGANIZER_CHANGE_PERMISSION = "organizer.settings.general:write"

EXPORTER_UID = "pretix_custom_reports_security_exporter"
EXPORTER_MULTI_UID = "pretix_custom_reports_security_multiexporter"

#: A lone UTF-16 high surrogate. ``json.loads`` accepts ``"\ud800"`` -- it is a
#: valid JSON escape -- but the resulting Python string cannot be encoded as
#: UTF-8. Anything that serialises it back out with ``ensure_ascii=False`` dies.
LONE_SURROGATE = "\ud800"


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def security_routes():
    """Attach every plugin route for the duration of this module."""
    from pretix.multidomain import maindomain_urlconf

    import pretix_custom_reports.urls as plugin_urls

    original = list(plugin_urls.urlpatterns)
    plugin_urls.urlpatterns = (
        list(editor_urlpatterns)
        + list(api_urlpatterns)
        + list(event_urlpatterns)
        + list(portability_event_urlpatterns)
        + list(templates_event_urlpatterns)
        + list(templates_organizer_urlpatterns)
        + original
    )
    importlib.reload(maindomain_urlconf)
    clear_url_caches()
    yield
    plugin_urls.urlpatterns = original
    importlib.reload(maindomain_urlconf)
    clear_url_caches()


@pytest.fixture(autouse=True)
def isolated_registry_cache():
    from pretix_custom_reports.registry import cache as registry_cache

    registry_cache.clear_local_cache()
    yield
    registry_cache.clear_local_cache()


def event_url(name: str, event, **kwargs) -> str:
    return reverse(
        f"{URL_NAMESPACE}:{name}",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, **kwargs},
    )


def organizer_url(name: str, organizer, **kwargs) -> str:
    return reverse(
        f"{URL_NAMESPACE}:{name}", kwargs={"organizer": organizer.slug, **kwargs}
    )


def post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def definition(
    base="orderposition",
    columns=("order.code",),
    filters=None,
    sorting=(),
    options=None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "schema_version": contracts.SCHEMA_VERSION,
        "base": base,
        "columns": [
            {"field": key} if isinstance(key, str) else dict(key) for key in columns
        ],
        "sorting": [{"field": key, "direction": "asc"} for key in sorting],
        "options": options
        or {
            "include_canceled_positions": False,
            "include_testmode_orders": False,
            "row_limit": None,
        },
    }
    if filters is not None:
        out["filters"] = filters
    return out


# ---------------------------------------------------------------------------
# Actors
# ---------------------------------------------------------------------------


@pytest.fixture
def rival_organizer(db):
    return Organizer.objects.create(name="Rival", slug="rival")


@pytest.fixture
def rival_event(rival_organizer):
    with scopes_disabled():
        return Event.objects.create(
            organizer=rival_organizer,
            name="Rival Event",
            slug="rivalevent",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
            live=True,
        )


@pytest.fixture
def rival_user(rival_organizer):
    """Full rights -- but only inside the *other* organizer."""
    user = User.objects.create_user("rival@example.org", PASSWORD)
    team = Team.objects.create(
        organizer=rival_organizer,
        name="Rival admins",
        all_events=True,
        all_event_permissions=True,
        all_organizer_permissions=True,
    )
    team.members.add(user)
    return user


@pytest.fixture
def read_only_user(organizer):
    """May read and run reports, must not create or change them."""
    user = User.objects.create_user("readonly@example.org", PASSWORD)
    team = Team.objects.create(
        organizer=organizer,
        name="Order readers",
        all_events=True,
        all_event_permissions=False,
        limit_event_permissions={VIEW_PERMISSION: True},
    )
    team.members.add(user)
    return user


def logged_in(user) -> Client:
    client = Client()
    client.login(email=user.email, password=PASSWORD)
    return client


@pytest.fixture
def admin_client(user_with_perms):
    return logged_in(user_with_perms)


@pytest.fixture
def report(event):
    with scopes_disabled():
        return ReportDefinition.objects.create(
            event=event,
            name="Attendee list",
            identifier="SECURITY",
            base="orderposition",
            definition=definition(),
        )


@pytest.fixture
def template(organizer):
    with scopes_disabled():
        return ReportDefinition.objects.create(
            organizer=organizer,
            name="A template",
            identifier="TEMPLATE",
            base="orderposition",
            definition=definition(),
        )


@pytest.fixture
def orders(event):
    """Enough data that a preview and an export produce rows."""
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        item = Item.objects.create(
            event=event, name="Ticket", internal_name="ticket", default_price=23
        )
        order = Order.objects.create(
            event=event,
            code="AAAAA",
            status=Order.STATUS_PAID,
            email="a@example.org",
            sales_channel=channel,
            datetime=now() - datetime.timedelta(days=1),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("23.00"),
            comment="plain",
        )
        for positionid in (1, 2):
            OrderPosition.objects.create(
                order=order, item=item, price=Decimal("23.00"), positionid=positionid
            )
    return {"order": order, "item": item}


@pytest.fixture
def registered_exporter():
    """Connect the two receivers exactly as the integrator will."""
    register_data_exporters.connect(
        exporters.register_report_exporter, dispatch_uid=EXPORTER_UID
    )
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=".*organizer-level.*", category=DeprecationWarning
        )
        register_multievent_data_exporters.connect(
            exporters.register_multievent_report_exporter,
            dispatch_uid=EXPORTER_MULTI_UID,
        )
    yield
    register_data_exporters.disconnect(dispatch_uid=EXPORTER_UID)
    register_multievent_data_exporters.disconnect(dispatch_uid=EXPORTER_MULTI_UID)


# ===========================================================================
# 1. Registry bypass
# ===========================================================================


def _python_sources(root: pathlib.Path) -> List[pathlib.Path]:
    return sorted(p for p in root.rglob("*.py") if "migrations" not in p.parts)


def test_no_module_evaluates_code_or_builds_raw_sql():
    """No ``eval``/``exec``/``compile``/``.raw()``/``RawSQL``/``.extra()``.

    Over the whole package, not only over ``query/``: the exporter, the views
    and the portability package all handle untrusted input too.
    """
    offenders = []
    banned_calls = {"eval", "exec", "compile", "__import__"}
    banned_attrs = {"raw", "extra"}
    for path in _python_sources(PLUGIN_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in banned_calls:
                    offenders.append(f"{path.name}:{node.lineno} {func.id}()")
                if isinstance(func, ast.Attribute) and func.attr in banned_attrs:
                    offenders.append(f"{path.name}:{node.lineno} .{func.attr}()")
            if isinstance(node, ast.Name) and node.id == "RawSQL":
                offenders.append(f"{path.name}:{node.lineno} RawSQL")
    assert offenders == []


def test_every_dynamic_lookup_keyword_comes_from_a_named_variable():
    """``filter(**{...})`` never builds its key by concatenating literals.

    A grep-level guard against the classic regression: somebody writes
    ``qs.filter(**{path + "__icontains": value})`` with ``path`` from a
    definition. The compiler builds lookups in exactly three places
    (``query/filters.py``, ``query/relations.py``, ``registry/*``) and in all of
    them the left-hand side is either a literal or a variable that was derived
    from ``ReportField.orm_path``. What must never appear anywhere is an
    f-string or a ``+`` whose operands include something read out of a
    definition; the definition-carrying names are enumerated below.
    """
    definition_names = {
        "condition",
        "spec",
        "column",
        "entry",
        "definition",
        "document",
        "form_data",
        "payload",
        "data",
        "request",
    }
    offenders = []
    for path in _python_sources(PLUGIN_ROOT):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            names = {
                sub.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.Name) and sub.id in definition_names
            }
            if not names:
                continue
            segment = ast.get_source_segment(source, node) or ""
            if "__" in segment:
                offenders.append(f"{path.name}:{node.lineno} {segment}")
    assert offenders == [], "an f-string built a lookup from definition data"


@pytest.mark.parametrize(
    "smuggled",
    [
        {"field": "order.code", "orm_path": "event__organizer__name"},
        {"field": "order__code"},
        {"field": "order.code__icontains"},
        {"field": "order.code", "lookup": "icontains"},
        {"field": "order.code", "annotation": "Sum('all_positions__price')"},
    ],
)
def test_a_column_can_never_carry_orm_vocabulary(smuggled):
    """Anything that looks like ORM vocabulary is refused structurally."""
    with pytest.raises(contracts.DefinitionValidationError):
        contracts.validate_definition(definition(columns=[smuggled]))


@pytest.mark.parametrize(
    "operator",
    ["icontains", "regex", "iexact", "__gt", "", "exact__", "in__in"],
)
def test_a_filter_operator_is_never_a_django_lookup(operator):
    body = definition(
        filters={
            "op": "and",
            "children": [{"field": "order.code", "operator": operator, "value": "x"}],
        }
    )
    with pytest.raises(contracts.DefinitionValidationError):
        contracts.validate_definition(body)


def test_the_lookup_suffix_table_is_closed_and_contains_no_user_input():
    """Every value the compiler appends after ``__`` is a hard-coded constant."""
    from pretix_custom_reports.query import filters as filters_mod

    source = pathlib.Path(filters_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    suffixes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Attribute)
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    suffixes.add(value.value)
    assert suffixes >= {"exact", "icontains", "in", "lt", "gte"}
    assert all(re.fullmatch(r"[a-z_]+", s) for s in suffixes if s)


def test_a_definition_that_bypasses_the_json_validator_still_dies_at_the_registry(
    event,
):
    """Hand-built dataclasses with a hostile key never reach the ORM.

    ``contracts.Column`` is a frozen dataclass; constructing one directly skips
    every structural check. The registry allow-list is the second gate, and it
    is the one that has to hold.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from pretix_custom_reports.query.compiler import ReportQueryCompiler
    from pretix_custom_reports.registry.library import field_registry

    hostile = contracts.ReportDefinition(
        base=contracts.Base.ORDERPOSITION,
        columns=(contracts.Column(field="order__event__organizer__name"),),
    )
    compiler = ReportQueryCompiler(field_registry())
    with scope(organizer=event.organizer):
        with CaptureQueriesContext(connection) as captured:
            with pytest.raises(contracts.FieldResolutionError):
                compiler.compile(hostile, event)
    executed = [q["sql"] for q in captured.captured_queries]
    # Building the field table reads the event's questions and the organizer's
    # meta properties -- that is the allow-list itself. What must not happen is
    # that the smuggled path reaches a row query.
    assert not any('organizer"."name' in sql for sql in executed), executed
    assert not any(
        "pretixbase_orderposition" in sql or 'pretixbase_order"' in sql
        for sql in executed
    ), executed


def test_column_format_separator_never_becomes_sql(event, orders):
    """``separator`` is a Python ``str.join`` argument, never a lookup."""
    from pretix_custom_reports.query.compiler import ReportQueryCompiler
    from pretix_custom_reports.registry.library import field_registry

    body = definition(
        base="order",
        columns=[
            {
                "field": "position.attendee_name",
                "aggregate": "join",
                "format": {"separator": "'; DROP"},
            }
        ],
    )
    document = contracts.validate_definition(body)
    with scope(organizer=event.organizer):
        compiled = ReportQueryCompiler(field_registry()).compile(document, event)
        sql = str(compiled.queryset.query)
        rows = list(compiled.iter_rows())
    assert "DROP" not in sql
    assert rows


# ===========================================================================
# 2. Event and tenant isolation
# ===========================================================================


def test_a_report_of_another_event_is_not_reachable_through_any_view(
    admin_client, event, event_without_plugin, report
):
    """The pk of a report of event A, requested under event B, is a 404."""
    with scopes_disabled():
        other = ReportDefinition.objects.create(
            event=event_without_plugin,
            name="Foreign",
            base="orderposition",
            definition=definition(),
        )
    for name in ("event.reports.edit", "event.reports.export"):
        url = event_url(name, event, report=other.pk)
        assert admin_client.get(url).status_code == 404, name


def test_the_editor_cannot_open_a_report_of_another_event(
    admin_client, event, event_without_plugin
):
    with scopes_disabled():
        ReportDefinition.objects.create(
            event=event_without_plugin,
            name="Foreign",
            identifier="FOREIGN1",
            base="orderposition",
            definition=definition(),
        )
    url = event_url("editor.edit", event, identifier="FOREIGN1")
    assert admin_client.get(url).status_code == 404


def test_an_organizer_template_is_invisible_through_the_event_report_views(
    admin_client, event, template
):
    """A template has ``event=None``; the event views must not reach it."""
    for name in ("event.reports.edit", "event.reports.export"):
        url = event_url(name, event, report=template.pk)
        assert admin_client.get(url).status_code == 404, name


def test_a_template_of_a_foreign_organizer_cannot_be_applied(
    event, rival_organizer, user_with_perms
):
    from pretix_custom_reports.portability.errors import TemplateAccessDenied
    from pretix_custom_reports.portability.templating import plan_template

    with scopes_disabled():
        foreign = ReportDefinition.objects.create(
            organizer=rival_organizer,
            name="Foreign template",
            base="orderposition",
            definition=definition(),
        )
    with scope(organizer=event.organizer):
        with pytest.raises(TemplateAccessDenied):
            plan_template(foreign, event, user=user_with_perms)


def test_the_template_pick_list_never_shows_a_foreign_organizers_template(
    event, rival_organizer
):
    from pretix_custom_reports.portability.templating import available_templates

    with scopes_disabled():
        ReportDefinition.objects.create(
            organizer=rival_organizer,
            name="Foreign template",
            base="orderposition",
            definition=definition(),
        )
    with scope(organizer=event.organizer):
        assert list(available_templates(event)) == []


def test_the_preview_only_ever_shows_rows_of_the_event_in_the_url(
    admin_client, event, orders, organizer
):
    """A second event of the same organizer must not bleed into the preview."""
    with scopes_disabled():
        second = Event.objects.create(
            organizer=organizer,
            name="Second",
            slug="second",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
            live=True,
        )
        channel = organizer.sales_channels.get(identifier="web")
        item = Item.objects.create(
            event=second, name="T", internal_name="t", default_price=1
        )
        order = Order.objects.create(
            event=second,
            code="ZZZZZ",
            status=Order.STATUS_PAID,
            email="z@example.org",
            sales_channel=channel,
            datetime=now(),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("1.00"),
        )
        OrderPosition.objects.create(
            order=order, item=item, price=Decimal("1.00"), positionid=1
        )

    response = post_json(
        admin_client,
        event_url("api.preview", event),
        {"definition": definition(columns=("order.code",))},
    )
    assert response.status_code == 200
    codes = {row[0] for row in response.json()["rows"]}
    assert codes == {"AAAAA"}


def test_the_exporter_never_resolves_a_report_identifier_globally(
    event, rival_event, registered_exporter, user_with_perms, orders
):
    """A stored identifier of a foreign event must not be reachable.

    ``export_form_data`` is not revalidated when a schedule runs, so the
    identifier in it is attacker-controlled for anybody who can write it.
    """
    with scopes_disabled():
        ReportDefinition.objects.create(
            event=rival_event,
            name="Rival report",
            identifier="RIVALREP",
            base="orderposition",
            definition=definition(),
        )
    with scope(organizer=event.organizer):
        exporter = init_event_exporter(
            identifier="customreports", event=event, user=user_with_perms
        )
        with pytest.raises(ExportError) as excinfo:
            list(exporter.iterate_list({"report": "RIVALREP", "_format": "default"}))
    assert "does not exist" in str(excinfo.value)


def test_an_organizer_export_never_reaches_another_organizers_events(
    event, rival_event, registered_exporter, user_with_perms, orders
):
    with scopes_disabled():
        ReportDefinition.objects.create(
            event=rival_event,
            name="Rival report",
            identifier="SHARED",
            base="orderposition",
            definition=definition(),
        )
        ReportDefinition.objects.create(
            event=event,
            name="Own report",
            identifier="SHARED",
            base="orderposition",
            definition=definition(),
        )
    with scope(organizer=event.organizer):
        found = [
            ex
            for ex in init_organizer_exporters(
                organizer=event.organizer, user=user_with_perms
            )
            if ex.identifier == "customreports"
        ]
        assert found
        assert set(found[0].events.values_list("slug", flat=True)) == {event.slug}


def test_the_registry_of_one_event_never_publishes_another_events_questions(
    event, organizer
):
    from pretix_custom_reports.registry.library import field_registry

    with scopes_disabled():
        second = Event.objects.create(
            organizer=organizer,
            name="Second",
            slug="second",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
        )
        Question.objects.create(
            event=second,
            question="Secret question",
            identifier="secret",
            type=Question.TYPE_STRING,
        )
    registry = field_registry()
    with scope(organizer=organizer):
        keys = registry.get_fields(event, contracts.Base.ORDERPOSITION)
    assert "answer.secret" not in keys


def test_an_annotation_built_for_one_event_refuses_a_foreign_context(event, organizer):
    """The closure guard in ``registry/annotations.py``, attacked directly."""
    from pretix_custom_reports.registry.library import field_registry

    with scopes_disabled():
        Question.objects.create(
            event=event,
            question="Size",
            identifier="size",
            type=Question.TYPE_STRING,
        )
        second = Event.objects.create(
            organizer=organizer,
            name="Second",
            slug="second",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
        )
    registry = field_registry()
    with scope(organizer=organizer):
        field = registry.resolve("answer.size", event, contracts.Base.ORDERPOSITION)
        assert field is not None
        with pytest.raises(contracts.FieldContractError):
            field.annotation(
                contracts.FieldContext(event=second, base=contracts.Base.ORDERPOSITION)
            )


# ===========================================================================
# 3. Permissions
# ===========================================================================
#
# Every endpoint, three hostile actors: nobody logged in, a user of this
# organizer without the required permission, and a full admin of a *different*
# organizer.


def _event_endpoints(event, report):
    """``(name, method, url)`` for every event-level endpoint."""
    return [
        ("api.fields", "get", event_url("api.fields", event)),
        ("api.validate", "post", event_url("api.validate", event)),
        ("api.preview", "post", event_url("api.preview", event)),
        ("editor.new", "get", event_url("editor.new", event)),
        (
            "editor.edit",
            "get",
            event_url("editor.edit", event, identifier=report.identifier),
        ),
        ("event.reports", "get", event_url("event.reports", event)),
        ("event.reports.add", "get", event_url("event.reports.add", event)),
        (
            "event.reports.edit",
            "get",
            event_url("event.reports.edit", event, report=report.pk),
        ),
        (
            "event.reports.duplicate",
            "post",
            event_url("event.reports.duplicate", event, report=report.pk),
        ),
        (
            "event.reports.delete",
            "get",
            event_url("event.reports.delete", event, report=report.pk),
        ),
        (
            "event.reports.export",
            "get",
            event_url("event.reports.export", event, report=report.pk),
        ),
        ("event.reports.import", "get", event_url("event.reports.import", event)),
        (
            "event.reports.templates",
            "get",
            event_url("event.reports.templates", event),
        ),
    ]


def test_no_event_endpoint_answers_an_anonymous_request(client, event, report):
    for name, method, url in _event_endpoints(event, report):
        response = getattr(client, method)(url)
        assert response.status_code in (302, 403), f"{name} -> {response.status_code}"
        if response.status_code == 302:
            assert "/login" in response["Location"], name


def test_no_event_endpoint_answers_a_user_without_any_report_permission(
    client_without_perms, event, report
):
    """``user_without_perms`` holds ``event.items:write`` and nothing else."""
    for name, method, url in _event_endpoints(event, report):
        response = getattr(client_without_perms, method)(url)
        assert response.status_code in (403, 404), f"{name} -> {response.status_code}"


def test_no_event_endpoint_answers_a_full_admin_of_another_organizer(
    rival_user, event, report
):
    client = logged_in(rival_user)
    for name, method, url in _event_endpoints(event, report):
        response = getattr(client, method)(url)
        assert response.status_code in (403, 404), f"{name} -> {response.status_code}"


def test_a_read_only_user_may_preview_but_never_write(
    read_only_user, event, report, orders
):
    client = logged_in(read_only_user)
    allowed = [
        event_url("api.fields", event),
        event_url("editor.new", event),
        event_url("event.reports", event),
        event_url("event.reports.export", event, report=report.pk),
    ]
    for url in allowed:
        assert client.get(url).status_code == 200, url
    assert (
        post_json(
            client, event_url("api.preview", event), {"definition": definition()}
        ).status_code
        == 200
    )
    forbidden = [
        ("get", event_url("event.reports.add", event)),
        ("get", event_url("event.reports.edit", event, report=report.pk)),
        ("post", event_url("event.reports.duplicate", event, report=report.pk)),
        ("get", event_url("event.reports.delete", event, report=report.pk)),
        ("get", event_url("event.reports.import", event)),
        ("get", event_url("event.reports.templates", event)),
    ]
    for method, url in forbidden:
        assert getattr(client, method)(url).status_code == 403, url


def test_the_import_resolution_preview_is_behind_the_write_permission(
    read_only_user, event
):
    """Step one of the import already runs the registry against real data."""
    client = logged_in(read_only_user)
    response = client.post(
        event_url("event.reports.import", event),
        {"text": json.dumps(definition())},
    )
    assert response.status_code == 403


def test_the_organizer_template_views_reject_a_foreign_organizer_admin(
    rival_user, organizer, event, template
):
    client = logged_in(rival_user)
    urls = [
        organizer_url("organizer.templates", organizer),
        organizer_url("organizer.templates.add", organizer),
        organizer_url("organizer.templates.edit", organizer, template=template.pk),
        organizer_url("organizer.templates.delete", organizer, template=template.pk),
        organizer_url("organizer.templates.export", organizer, template=template.pk),
    ]
    for url in urls:
        assert client.get(url).status_code in (403, 404), url


def test_the_organizer_template_views_reject_a_read_only_user(
    read_only_user, organizer, event, template
):
    client = logged_in(read_only_user)
    for url in [
        organizer_url("organizer.templates", organizer),
        organizer_url("organizer.templates.edit", organizer, template=template.pk),
        organizer_url("organizer.templates.export", organizer, template=template.pk),
    ]:
        assert client.get(url).status_code in (403, 404), url


def test_no_json_endpoint_is_csrf_exempt():
    """Checked over the syntax tree, not the text -- api.py *documents* that it
    is not csrf_exempt, and a text search would happily accept that sentence as
    the thing it forbids."""
    for name in ("api", "editor", "crud", "portability", "templates"):
        module = importlib.import_module(f"pretix_custom_reports.views.{name}")
        tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "csrf_exempt" not in used, name


def test_the_post_endpoints_reject_a_request_without_a_csrf_token(
    user_with_perms, event
):
    client = Client(enforce_csrf_checks=True)
    client.login(email=user_with_perms.email, password=PASSWORD)
    for name in ("api.validate", "api.preview"):
        response = client.post(
            event_url(name, event),
            data=json.dumps({"definition": definition()}),
            content_type="application/json",
        )
        assert response.status_code == 403, name


@pytest.mark.xfail(
    strict=True,
    reason="S-001: views/crud.py has no PluginActiveMixin (all other view "
    "modules do), so the CRUD views stay reachable for an event that has the "
    "plugin switched off.",
)
def test_every_event_view_404s_when_the_plugin_is_off(
    admin_client, event_without_plugin
):
    with scopes_disabled():
        foreign = ReportDefinition.objects.create(
            event=event_without_plugin,
            name="Left over",
            base="orderposition",
            definition=definition(),
        )
    urls = [
        event_url("event.reports", event_without_plugin),
        event_url("event.reports.add", event_without_plugin),
        event_url("event.reports.edit", event_without_plugin, report=foreign.pk),
        event_url("event.reports.delete", event_without_plugin, report=foreign.pk),
    ]
    for url in urls:
        assert admin_client.get(url).status_code == 404, url


def test_the_endpoints_that_do_have_the_plugin_gate_really_404(
    admin_client, event_without_plugin
):
    """Control group for the xfail above -- api, editor and portability do."""
    urls = [
        event_url("api.fields", event_without_plugin),
        event_url("editor.new", event_without_plugin),
        event_url("event.reports.import", event_without_plugin),
        event_url("event.reports.templates", event_without_plugin),
    ]
    for url in urls:
        assert admin_client.get(url).status_code == 404, url


# ===========================================================================
# 4. Import as an attack surface
# ===========================================================================


def test_every_invalid_fixture_is_still_refused(event):
    """Belt and braces over portability-dev's own parametrised test."""
    paths = sorted(
        p
        for p in (FIXTURE_DIR / "invalid").glob("*.json")
        if not p.name.startswith("_")
    )
    assert len(paths) >= 17
    with scope(organizer=event.organizer):
        for path in paths:
            raw = path.read_bytes()
            try:
                plan = plan_import(raw, event=event)
            except (PayloadRejected, contracts.DefinitionValidationError):
                continue
            assert not plan.ok, path.name
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version": 2, "base": "order", "columns": [{"field": "order.code"}]}',
        b'{"schema_version": 999, "base": "order", "columns": [{"field": "order.code"}]}',
        b'{"schema_version": 0, "base": "order", "columns": [{"field": "order.code"}]}',
        b'{"schema_version": -1, "base": "order", "columns": [{"field": "order.code"}]}',
        b'{"schema_version": 1.0, "base": "order", "columns": [{"field": "order.code"}]}',
        b'{"schema_version": true, "base": "order", "columns": [{"field": "order.code"}]}',
        b'{"schema_version": "1", "base": "order", "columns": [{"field": "order.code"}]}',
    ],
)
def test_a_schema_version_from_the_future_or_the_wrong_type_is_refused(raw, event):
    with scope(organizer=event.organizer):
        with pytest.raises((PayloadRejected, contracts.DefinitionValidationError)):
            plan_import(raw, event=event)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b'{"a": 1, "a": 2}', id="duplicate-member"),
        pytest.param(b'{"a": NaN}', id="nan"),
        pytest.param(b'{"a": Infinity}', id="infinity"),
        pytest.param(b'{"a": 1e999}', id="overflow"),
        pytest.param(b'["not", "an", "object"]', id="top-level-array"),
        pytest.param(b'"just a string"', id="top-level-string"),
        pytest.param(b"\xff\xfe{}", id="not-utf8"),
        pytest.param(b"", id="empty"),
    ],
)
def test_the_payload_gate_refuses_these_outright(raw):
    with pytest.raises(PayloadRejected):
        load_json_object(raw)


def test_a_deeply_nested_document_is_refused_before_json_loads():
    body = "[" * (MAX_DEPTH + 5) + "]" * (MAX_DEPTH + 5)
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(('{"a": ' + body + "}").encode())
    assert excinfo.value.reason == "too_deep"


def test_the_depth_scan_cannot_be_fooled_by_brackets_inside_strings():
    """A string full of ``[`` must not be counted, and must not be a bypass."""
    payload = json.dumps({"a": "[" * 100 + "]" * 100}).encode()
    assert load_json_object(payload)["a"].startswith("[")


def test_a_document_with_too_many_nodes_is_refused():
    payload = json.dumps({"a": list(range(MAX_NODES + 10))}).encode()
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(payload)
    assert excinfo.value.reason == "too_many_nodes"


def test_an_oversized_string_is_refused_even_inside_free_form_meta():
    payload = json.dumps({"meta": {"x": "a" * (MAX_STRING_CHARS + 1)}}).encode()
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(payload)
    assert excinfo.value.reason == "string_too_long"


def test_an_oversized_upload_is_refused_on_the_bytes():
    payload = b'{"a": "' + b"x" * MAX_PAYLOAD_BYTES + b'"}'
    with pytest.raises(PayloadRejected) as excinfo:
        load_json_object(payload)
    assert excinfo.value.reason == "too_large"


def test_an_upload_that_lies_about_its_size_is_still_capped(admin_client, event):
    """``upload.size`` comes from the browser; the read limit is the real gate."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    class LyingFile(SimpleUploadedFile):
        @property
        def size(self):
            return 10

        @size.setter
        def size(self, value):
            pass

    payload = b'{"pad": "' + b"x" * (MAX_PAYLOAD_BYTES + 1000) + b'"}'
    upload = LyingFile("report.json", payload, content_type="application/json")
    response = admin_client.post(
        event_url("event.reports.import", event), {"file": upload}
    )
    assert response.status_code == 200
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


@pytest.mark.parametrize(
    "key",
    [
        "order.code\x00",
        "order.‮code",
        "order.códe",
        "﻿ordercode",
        "order.ＣＯＤＥ",
        "answer.a​b",
    ],
)
def test_a_unicode_trick_in_a_field_key_never_resolves_to_a_core_field(key, event):
    """Homoglyphs, bidi overrides, zero-width joiners, null bytes.

    Either the structural validator refuses the key outright, or the registry
    fails to resolve it -- what must not happen is that it silently becomes
    ``order.code``.
    """
    from pretix_custom_reports.registry.library import field_registry

    try:
        document = contracts.validate_definition(definition(columns=(key,)))
    except contracts.DefinitionValidationError:
        return
    with scope(organizer=event.organizer):
        outcome = _resolve(document, event, field_registry())
    assert outcome != "order.code", key


def _resolve(document, event, registry):
    from pretix_custom_reports.portability.resolution import resolve_definition

    outcome = resolve_definition(document, event=event, registry=registry)
    if outcome.document is None:
        return None
    return outcome.document.columns[0].field


def test_a_null_byte_in_a_filter_value_never_reaches_the_database(event, orders):
    """PostgreSQL refuses ``\\x00`` in text; a stored one is a time bomb."""
    from pretix_custom_reports.query.compiler import ReportQueryCompiler
    from pretix_custom_reports.registry.library import field_registry

    body = definition(
        filters={
            "op": "and",
            "children": [
                {"field": "order.code", "operator": "exact", "value": "A\x00A"}
            ],
        }
    )
    document = contracts.validate_definition(body)
    with scope(organizer=event.organizer):
        compiled = ReportQueryCompiler(field_registry()).compile(document, event)
        rows = list(compiled.iter_rows())
    assert rows == []


def test_an_import_never_writes_anything_before_the_confirmation(admin_client, event):
    body = json.dumps(definition())
    response = admin_client.post(
        event_url("event.reports.import", event), {"text": body}
    )
    assert response.status_code == 200
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


def test_the_confirmation_step_ignores_a_resolved_definition_from_the_browser(
    admin_client, event
):
    """The hidden field carries the *original* text; step two re-parses it.

    Posting a different, hostile document alongside must not be able to make the
    importer store something the first step never showed.
    """
    honest = json.dumps(definition(columns=("order.code",)))
    response = admin_client.post(
        event_url("event.reports.import", event),
        {
            "document": honest,
            "action": "confirm",
            "strategy": "abort",
            # A second, hostile document under the key the resolved definition
            # would have used if the flow kept one.
            "definition": json.dumps(definition(columns=("order.email",))),
        },
    )
    assert response.status_code in (200, 302)
    with scopes_disabled():
        stored = list(ReportDefinition.objects.all())
    assert len(stored) == 1
    assert [c["field"] for c in stored[0].definition["columns"]] == ["order.code"]


def test_an_unknown_strategy_falls_back_to_abort(event):
    from pretix_custom_reports.portability.resolution import ResolutionStrategy

    for hostile in ("keep\x00", "KEEP", "', 'skip", None, 42, ["skip"]):
        assert ResolutionStrategy.coerce(hostile) == ResolutionStrategy.ABORT


def test_the_import_view_never_accepts_keep_from_the_browser(admin_client, event):
    """``keep`` is the event-copy strategy and must not be user selectable.

    ``keep`` leaves unresolvable keys in place *and* skips the compiler check,
    so offering it through the form would let a file store a report the target
    event cannot run.
    """
    from pretix_custom_reports.portability.resolution import ResolutionStrategy

    body = json.dumps(definition(columns=("answer.does-not-exist",)))
    response = admin_client.post(
        event_url("event.reports.import", event),
        {"text": body, "strategy": ResolutionStrategy.KEEP, "action": "confirm"},
    )
    assert response.status_code == 200
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


def test_the_portability_package_never_deserialises_anything_but_json():
    banned_modules = {"pickle", "marshal", "yaml", "shelve", "subprocess", "os"}
    from pretix_custom_reports import portability

    root = pathlib.Path(portability.__file__).parent
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned_modules, path.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert (
                    node.module.split(".")[0] not in banned_modules
                ), f"{path.name}:{node.lineno}"


# ---------------------------------------------------------------------------
# The lone surrogate (S-003)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="S-003: neither the payload gate nor the structural validator "
    "rejects an unpaired surrogate, so it travels straight into the database.",
)
def test_a_lone_surrogate_is_refused_by_the_payload_gate():
    """``"\\ud800"`` is legal JSON syntax but not encodable as UTF-8."""
    raw = b'{"schema_version": 1, "base": "order", "columns": [{"field": "order.code", "label": "\\ud800"}]}'
    with pytest.raises((PayloadRejected, contracts.DefinitionValidationError)):
        load_json_object(raw)


@pytest.mark.xfail(
    strict=True,
    reason="S-003: nothing rejects unpaired surrogates. api/validate/ echoes "
    "the definition with ensure_ascii=False and dies with UnicodeEncodeError.",
)
def test_the_validate_endpoint_survives_a_lone_surrogate(admin_client, event):
    body = definition(columns=[{"field": "order.code", "label": "x" + LONE_SURROGATE}])
    response = post_json(admin_client, event_url("api.validate", event), body_of(body))
    assert response.status_code in (200, 400)


def body_of(document):
    return {"definition": document}


@pytest.mark.xfail(
    strict=True,
    reason="S-003: a stored lone surrogate makes the JSON export view raise "
    "UnicodeEncodeError (500) instead of producing a file.",
)
def test_the_export_view_survives_a_stored_lone_surrogate(admin_client, event):
    with scopes_disabled():
        poisoned = ReportDefinition.objects.create(
            event=event,
            name="Poisoned",
            base="orderposition",
            definition=definition(
                columns=[{"field": "order.code", "label": "x" + LONE_SURROGATE}]
            ),
        )
    response = admin_client.get(
        event_url("event.reports.export", event, report=poisoned.pk)
    )
    assert response.status_code == 200


def test_the_csv_path_survives_a_stored_lone_surrogate(
    event, orders, registered_exporter, user_with_perms
):
    """The bound of S-003: ``ListExporter`` encodes with ``errors="replace"``
    (pretix/base/exporter.py:290), so the CSV export does *not* blow up. This
    test exists so the finding cannot be over-stated -- and so it turns red if
    somebody ever writes their own serialiser (CLAUDE.md rule 6)."""
    with scopes_disabled():
        ReportDefinition.objects.create(
            event=event,
            name="Poisoned",
            identifier="POISONED",
            base="orderposition",
            definition=definition(
                columns=[{"field": "order.code", "label": "x" + LONE_SURROGATE}]
            ),
        )
    with scope(organizer=event.organizer):
        exporter = init_event_exporter(
            identifier="customreports", event=event, user=user_with_perms
        )
        _name, _mime, data = exporter.render(
            {"report": "POISONED", "_format": "default"}
        )
    assert isinstance(data, bytes)
    assert b"AAAAA" in data


@pytest.mark.xfail(
    strict=True,
    reason="S-003: a lone surrogate in a column label makes POST api/preview/ "
    "raise UnicodeEncodeError while building the JSON response (500).",
)
def test_the_preview_endpoint_survives_a_lone_surrogate(admin_client, event, orders):
    response = post_json(
        admin_client,
        event_url("api.preview", event),
        {
            "definition": definition(
                columns=[{"field": "order.code", "label": "x" + LONE_SURROGATE}]
            )
        },
    )
    assert response.status_code in (200, 400)


# ---------------------------------------------------------------------------
# The identifier collision (S-004)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="S-004: ReportDefinitionForm exposes 'identifier' but the uniqueness "
    "constraint is (event, identifier); 'event' is not a form field, so Django "
    "skips the check and the duplicate reaches the database as an IntegrityError.",
)
def test_a_duplicate_identifier_is_a_form_error_not_a_500(admin_client, event, report):
    response = admin_client.post(
        event_url("event.reports.add", event),
        {
            "name": "Second report",
            "description": "",
            "identifier": report.identifier,
            "base": "orderposition",
            "definition": json.dumps(definition()),
        },
    )
    assert response.status_code == 200
    assert b"identifier" in response.content


# ===========================================================================
# 5. Output
# ===========================================================================


def test_csv_injection_is_neutralised_in_cells_and_in_the_header(
    event, orders, registered_exporter, user_with_perms
):
    """defusedcsv covers cells. It has to cover the *header* too, because the
    column label is free text the user typed."""
    with scopes_disabled():
        order = orders["order"]
        order.comment = '=1+cmd|" /C calc"!A0'
        order.save(update_fields=["comment"])
        ReportDefinition.objects.create(
            event=event,
            name="Injection",
            identifier="INJECT",
            base="orderposition",
            definition=definition(
                columns=[
                    {"field": "order.comment", "label": "=cmd|' /C calc'!A0"},
                ]
            ),
        )
    with scope(organizer=event.organizer):
        exporter = init_event_exporter(
            identifier="customreports", event=event, user=user_with_perms
        )
        _name, _mime, data = exporter.render({"report": "INJECT", "_format": "default"})
    text = data.decode("utf-8")
    assert "\n=1+cmd" not in text
    assert ",=1+cmd" not in text
    assert text.splitlines()[0].lstrip("﻿").startswith("'=cmd") or text.splitlines()[
        0
    ].lstrip("﻿").startswith("\"'=cmd")


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        '"; rm -rf /',
        "report\r\nX-Injected: 1",
        "a" * 500,
        "üñïçø∂é",
    ],
)
def test_a_hostile_report_name_cannot_escape_the_content_disposition_header(
    admin_client, event, name
):
    from pretix_custom_reports.portability.envelope import export_filename

    with scopes_disabled():
        poisoned = ReportDefinition.objects.create(
            event=event,
            name=name,
            base="orderposition",
            definition=definition(),
        )
    filename = export_filename(poisoned)
    assert filename.isascii()
    assert "/" not in filename and "\\" not in filename
    assert ".." not in filename
    assert "\r" not in filename and "\n" not in filename and '"' not in filename

    response = admin_client.get(
        event_url("event.reports.export", event, report=poisoned.pk)
    )
    assert response.status_code == 200
    assert "\n" not in response["Content-Disposition"]


@pytest.mark.parametrize("identifier", ["a.b.c", "..", "a-b_c.d", "A" * 60])
def test_the_export_file_name_of_the_exporter_stays_a_bare_name(identifier):
    exporter = exporters.CustomReportExporter.__new__(exporters.CustomReportExporter)
    exporter.is_multievent = False
    exporter.event = type("E", (), {"slug": "dummy"})()
    exporter._last_report_identifier = identifier
    name = exporter.get_filename()
    assert re.fullmatch(r"[A-Za-z0-9_-]+", name), name


def test_the_preview_escapes_order_data_in_its_html_fragment(
    admin_client, event, orders
):
    with scopes_disabled():
        order = orders["order"]
        order.comment = '<script>alert("xss")</script>'
        order.save(update_fields=["comment"])
    response = post_json(
        admin_client,
        event_url("api.preview", event),
        {"definition": definition(columns=("order.comment",))},
    )
    assert response.status_code == 200
    html = response.json()["html"]
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_the_preview_escapes_a_hostile_column_label(admin_client, event, orders):
    response = post_json(
        admin_client,
        event_url("api.preview", event),
        {
            "definition": definition(
                columns=[{"field": "order.code", "label": "<img src=x onerror=1>"}]
            )
        },
    )
    assert response.status_code == 200
    html = response.json()["html"]
    assert "<img src=x" not in html


def test_the_editor_page_never_emits_a_closing_script_tag_from_stored_data(
    admin_client, event
):
    """``escapejson_dumps`` hex-encodes ``<``, so a stored ``</script>`` cannot
    break out of the JSON config block."""
    with scopes_disabled():
        ReportDefinition.objects.create(
            event=event,
            name="Breakout",
            identifier="BREAKOUT",
            base="orderposition",
            definition=definition(
                columns=[
                    {
                        "field": "order.code",
                        "label": "</script><script>alert(1)</script>",
                    }
                ]
            ),
        )
    response = admin_client.get(event_url("editor.edit", event, identifier="BREAKOUT"))
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    block = body.split('id="pcr-config">', 1)[1]
    # The config block must end at the *template's* closing tag, not at one the
    # stored label smuggled in.
    marker = block.split("</script>", 1)[0]
    assert "<" not in marker and ">" not in marker
    assert json.loads(marker)["initial"]["columns"][0]["label"].startswith("</script>")


# ===========================================================================
# 6. Background execution
# ===========================================================================


def test_a_scheduled_export_whose_owner_lost_the_permission_produces_nothing(
    event, orders, registered_exporter, organizer, django_capture_on_commit_callbacks
):
    """pretix checks the owner's permission per run; we must not widen it."""
    from django.core import mail as djmail

    owner = User.objects.create_user("owner@example.org", PASSWORD)
    team = Team.objects.create(
        organizer=organizer,
        name="Owner team",
        all_events=True,
        all_event_permissions=True,
    )
    team.members.add(owner)
    with scopes_disabled():
        ReportDefinition.objects.create(
            event=event,
            name="Scheduled",
            identifier="SCHED",
            base="orderposition",
            definition=definition(),
        )
        schedule = ScheduledEventExport.objects.create(
            event=event,
            owner=owner,
            export_identifier="customreports",
            export_form_data={"report": "SCHED", "_format": "default"},
            mail_subject="Report",
            mail_template="here",
            schedule_rrule="DTSTART:20260101T000000\nRRULE:FREQ=DAILY",
            schedule_rrule_time=datetime.time(4, 0),
            schedule_next_run=now() - datetime.timedelta(minutes=5),
        )
    # Revoke: the team keeps the user but loses every event permission.
    team.all_event_permissions = False
    team.limit_event_permissions = {"event.items:write": True}
    team.save()

    djmail.outbox = []
    run_scheduled_exports(None)
    schedule.refresh_from_db()
    assert schedule.error_counter == 1
    assert all(not m.attachments for m in djmail.outbox)


def test_a_scheduled_export_runs_inside_an_active_django_scopes_scope(
    event, orders, registered_exporter, user_with_perms
):
    """If the task ran without a scope, the report lookup would raise ScopeError.

    The proof is indirect but strict: the exporter reads ``ReportDefinition``
    through a ``ScopedManager``, so a successful run *is* the assertion.
    """
    from django.core import mail as djmail

    with scopes_disabled():
        ReportDefinition.objects.create(
            event=event,
            name="Scheduled",
            identifier="SCOPED",
            base="orderposition",
            definition=definition(),
        )
        ScheduledEventExport.objects.create(
            event=event,
            owner=user_with_perms,
            export_identifier="customreports",
            export_form_data={"report": "SCOPED", "_format": "default"},
            mail_subject="Report",
            mail_template="here",
            schedule_rrule="DTSTART:20260101T000000\nRRULE:FREQ=DAILY",
            schedule_rrule_time=datetime.time(4, 0),
            schedule_next_run=now() - datetime.timedelta(minutes=5),
        )
    djmail.outbox = []
    run_scheduled_exports(None)
    assert any(m.attachments for m in djmail.outbox)


def _scope_calls(path: pathlib.Path) -> set:
    """Names of django-scopes helpers actually *called* in *path*.

    Over the syntax tree, because several modules discuss ``scopes_disabled`` in
    their docstrings and a text search cannot tell prose from code.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("scope", "scopes_disabled"):
                out.add(node.func.id)
        if isinstance(node, ast.Name) and node.id == "scopes_disabled":
            parent_is_decorator = False
            out.add("scopes_disabled") if parent_is_decorator else None
    return out


def test_the_exporter_does_not_open_a_scope_of_its_own():
    """Widening or disabling the scope in the exporter would remove the one
    safety net a Celery task still has (CLAUDE.md rule 4)."""
    assert _scope_calls(PLUGIN_ROOT / "exporters.py") == set()


def test_scopes_disabled_is_only_used_where_it_is_argued_for():
    """Two places, each with a hard ``event=``/``organizer=`` filter next to it."""
    allowed = {"models.py", "eventcopy.py"}
    offenders = sorted(
        path.name
        for path in _python_sources(PLUGIN_ROOT)
        if "scopes_disabled" in _scope_calls(path) and path.name not in allowed
    )
    assert offenders == []


def test_the_event_copy_reads_only_the_source_event(event, rival_event, organizer):
    """``scopes_disabled()`` plus a hard filter -- not a hole."""
    from pretix_custom_reports.portability.eventcopy import copy_reports_to_event

    with scopes_disabled():
        ReportDefinition.objects.create(
            event=event,
            name="Mine",
            identifier="MINE",
            base="orderposition",
            definition=definition(),
        )
        ReportDefinition.objects.create(
            event=rival_event,
            name="Theirs",
            identifier="THEIRS",
            base="orderposition",
            definition=definition(),
        )
        target = Event.objects.create(
            organizer=organizer,
            name="Copy target",
            slug="copytarget",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
        )
    with scope(organizer=organizer):
        result = copy_reports_to_event(target, event)
    assert [c.source_identifier for c in result.copied] == ["MINE"]
    with scopes_disabled():
        assert set(
            ReportDefinition.objects.filter(event=target).values_list(
                "identifier", flat=True
            )
        ) == {"MINE"}


def test_stored_export_form_data_is_type_checked_before_use(
    event, orders, registered_exporter, user_with_perms
):
    """``export_form_data`` is raw JSON from the database, never revalidated."""
    hostile = [
        {"report": {"$ne": None}, "_format": "default"},
        {"report": ["SCHED"], "_format": "default"},
        {"report": "SCHED", "_format": "../../etc/passwd"},
        {"report": "SCHED", "_format": "default", "row_limit": True},
        {"report": "SCHED", "_format": "default", "row_limit": -1},
        {"report": "SCHED", "_format": "default", "row_limit": "1; DROP"},
        {
            "report": "SCHED",
            "_format": "default",
            "include_canceled_positions": "maybe",
        },
    ]
    with scopes_disabled():
        ReportDefinition.objects.create(
            event=event,
            name="Scheduled",
            identifier="SCHED",
            base="orderposition",
            definition=definition(),
        )
    with scope(organizer=event.organizer):
        for form_data in hostile:
            exporter = init_event_exporter(
                identifier="customreports", event=event, user=user_with_perms
            )
            with pytest.raises(ExportError):
                exporter.render(form_data)


@pytest.mark.xfail(
    strict=True,
    reason="S-002: the multi-event exporter never asks whether the plugin is "
    "active for an event, so switching the plugin off does not stop its reports "
    "from being exported through the organizer-level export.",
)
def test_an_organizer_export_skips_events_with_the_plugin_switched_off(
    event, event_without_plugin, registered_exporter, user_with_perms, organizer
):
    with scopes_disabled():
        channel = organizer.sales_channels.get(identifier="web")
        item = Item.objects.create(
            event=event_without_plugin,
            name="T",
            internal_name="t",
            default_price=1,
        )
        order = Order.objects.create(
            event=event_without_plugin,
            code="OFFEV",
            status=Order.STATUS_PAID,
            email="off@example.org",
            sales_channel=channel,
            datetime=now(),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("1.00"),
        )
        OrderPosition.objects.create(
            order=order, item=item, price=Decimal("1.00"), positionid=1
        )
        ReportDefinition.objects.create(
            event=event_without_plugin,
            name="Left over",
            identifier="LEFTOVER",
            base="orderposition",
            definition=definition(),
        )
    with scope(organizer=organizer):
        found = [
            ex
            for ex in init_organizer_exporters(
                organizer=organizer, user=user_with_perms
            )
            if ex.identifier == "customreports"
        ]
        assert found
        _name, _mime, data = found[0].render(
            {"report": "LEFTOVER", "_format": "default"}
        )
    # The order code of the event that has the plugin switched off must not be
    # in the file.
    assert b"OFFEV" not in data


# ===========================================================================
# 7. Resources
# ===========================================================================


def test_the_preview_is_limited_in_sql_not_in_python(admin_client, event, orders):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        response = post_json(
            admin_client,
            event_url("api.preview", event),
            {"definition": definition(), "limit": 10_000},
        )
    assert response.status_code == 200
    assert response.json()["limit"] == contracts.PREVIEW_ROW_LIMIT
    selects = [
        q["sql"]
        for q in captured.captured_queries
        if q["sql"].lower().startswith("select") and "orderposition" in q["sql"].lower()
    ]
    assert any("LIMIT" in sql for sql in selects), selects


@pytest.mark.parametrize("limit", [0, -1, 10**9, "20", 3.5, True, None, [20]])
def test_the_preview_limit_can_never_be_widened(admin_client, event, orders, limit):
    response = post_json(
        admin_client,
        event_url("api.preview", event),
        {"definition": definition(), "limit": limit},
    )
    assert response.status_code == 200
    payload = response.json()
    assert 1 <= payload["limit"] <= contracts.PREVIEW_ROW_LIMIT
    assert len(payload["rows"]) <= contracts.PREVIEW_ROW_LIMIT


def test_a_report_full_of_join_columns_costs_one_query_per_column(event, orders):
    """Documented amplification, measured (S-005).

    Each ``join`` column gets its own ``Prefetch`` with a unique ``to_attr``, so
    the query count grows linearly with the number of columns. With
    ``MAX_COLUMNS`` at 200 a single preview request is ~200 round trips.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from pretix_custom_reports.query.compiler import ReportQueryCompiler
    from pretix_custom_reports.registry.library import field_registry

    def count_for(n):
        body = definition(
            base="order",
            columns=[
                {"field": "position.attendee_name", "aggregate": "join"}
                for _ in range(n)
            ],
        )
        document = contracts.validate_definition(body)
        with scope(organizer=event.organizer):
            compiled = ReportQueryCompiler(field_registry()).compile(
                document, event, preview=True
            )
            with CaptureQueriesContext(connection) as captured:
                list(compiled.iter_rows())
        return len(captured.captured_queries)

    few, many = count_for(2), count_for(20)
    assert many - few >= 15, (few, many)


def test_a_hundred_filter_conditions_still_compile_to_one_query(event, orders):
    """A wide filter must not turn into N queries."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from pretix_custom_reports.query.compiler import ReportQueryCompiler
    from pretix_custom_reports.registry.library import field_registry

    half = contracts.MAX_FILTER_CONDITIONS // 2
    group = {
        "op": "or",
        "children": [
            {"field": "order.code", "operator": "contains", "value": f"x{i}"}
            for i in range(half)
        ],
    }
    document = contracts.validate_definition(
        definition(filters={"op": "or", "children": [group, dict(group)]})
    )
    with scope(organizer=event.organizer):
        compiled = ReportQueryCompiler(field_registry()).compile(document, event)
        with CaptureQueriesContext(connection) as captured:
            list(compiled.iter_rows())
    assert len(captured.captured_queries) == 1


def test_more_columns_than_the_limit_are_refused_structurally():
    body = definition(columns=["order.code"] * (contracts.MAX_COLUMNS + 1))
    with pytest.raises(contracts.DefinitionValidationError):
        contracts.validate_definition(body)


def test_a_row_limit_beyond_the_maximum_is_refused(event):
    body = definition(
        options={
            "include_canceled_positions": False,
            "include_testmode_orders": False,
            "row_limit": contracts.MAX_ROW_LIMIT + 1,
        }
    )
    with pytest.raises(contracts.DefinitionValidationError):
        contracts.validate_definition(body)


# ===========================================================================
# 8. Second round: forged form fields, method matrix, identifier hijacking
# ===========================================================================


def test_the_owner_of_a_report_cannot_be_forged_through_a_hidden_input(
    admin_client, event, rival_event
):
    """``event``/``organizer`` come from the URL, never from the POST body."""
    response = admin_client.post(
        event_url("event.reports.add", event),
        {
            "name": "Forged",
            "description": "",
            "identifier": "",
            "base": "orderposition",
            "definition": json.dumps(definition()),
            # Everything an attacker would try.
            "event": rival_event.pk,
            "organizer": rival_event.organizer.pk,
            "instance-event": rival_event.pk,
        },
    )
    assert response.status_code in (200, 302)
    with scopes_disabled():
        stored = ReportDefinition.objects.get(name="Forged")
    assert stored.event_id == event.pk
    assert stored.organizer_id is None


def test_a_template_cannot_be_turned_into_an_event_report_by_a_hidden_input(
    admin_client, organizer, event
):
    response = admin_client.post(
        organizer_url("organizer.templates.add", organizer),
        {
            "name": "Forged template",
            "description": "",
            "identifier": "",
            "base": "orderposition",
            "definition": json.dumps(definition()),
            "event": event.pk,
        },
    )
    assert response.status_code in (200, 302)
    with scopes_disabled():
        stored = ReportDefinition.objects.get(name="Forged template")
    assert stored.event_id is None
    assert stored.organizer_id == organizer.pk


def test_the_xor_constraint_holds_in_the_database_not_only_in_python(event, organizer):
    """``bulk_create`` bypasses ``save()`` -- the check constraint must catch it."""
    from django.db import IntegrityError, transaction

    with scopes_disabled():
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ReportDefinition.objects.bulk_create(
                    [
                        ReportDefinition(
                            event=event,
                            organizer=organizer,
                            name="Both",
                            identifier="BOTH",
                            base="orderposition",
                            definition=definition(),
                        )
                    ]
                )


def test_an_imported_file_cannot_hijack_the_identifier_of_an_existing_report(
    admin_client, event, report
):
    """A scheduled export addresses a report by its identifier.

    A file that declares the ``meta.identifier`` of an existing report must not
    be able to take that identifier over, or the next scheduled run would export
    the attacker's report instead of the intended one.
    """
    document = {
        "schema_version": contracts.SCHEMA_VERSION,
        "name": "Impostor",
        "exported_at": "2026-01-01T00:00:00+00:00",
        "generator": "hand written",
        "source": "elsewhere/elsewhere",
        "meta": {"identifier": report.identifier},
        "definition": definition(),
    }
    response = admin_client.post(
        event_url("event.reports.import", event),
        {"text": json.dumps(document), "action": "confirm", "strategy": "abort"},
    )
    assert response.status_code in (200, 302)
    with scopes_disabled():
        impostor = ReportDefinition.objects.get(name="Impostor")
        original = ReportDefinition.objects.get(pk=report.pk)
    assert impostor.identifier != original.identifier
    assert original.identifier == "SECURITY"


def test_loading_a_template_twice_does_not_collide_on_the_identifier(
    event, template, user_with_perms
):
    from pretix_custom_reports.portability.templating import (
        apply_template,
        plan_template,
    )

    with scope(organizer=event.organizer):
        first = apply_template(
            plan_template(template, event, user=user_with_perms),
            user=user_with_perms,
        )
        second = apply_template(
            plan_template(template, event, user=user_with_perms),
            user=user_with_perms,
        )
    assert first.identifier != second.identifier


@pytest.mark.parametrize(
    "name,method,expected",
    [
        ("api.fields", "post", 405),
        ("api.validate", "get", 405),
        ("api.preview", "get", 405),
    ],
)
def test_the_json_endpoints_reject_the_wrong_http_method(
    admin_client, event, name, method, expected
):
    response = getattr(admin_client, method)(event_url(name, event))
    assert response.status_code == expected


def test_duplicating_a_report_is_post_only(admin_client, event, report):
    """A GET that writes would fire on every link prefetch and skip CSRF."""
    url = event_url("event.reports.duplicate", event, report=report.pk)
    assert admin_client.get(url).status_code == 405
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 1


def test_the_write_endpoints_reject_a_request_without_a_csrf_token(
    user_with_perms, event, report
):
    client = Client(enforce_csrf_checks=True)
    client.login(email=user_with_perms.email, password=PASSWORD)
    posts = [
        (event_url("event.reports.duplicate", event, report=report.pk), {}),
        (event_url("event.reports.import", event), {"text": "{}"}),
        (
            event_url("event.reports.add", event),
            {"name": "x", "base": "orderposition", "definition": "{}"},
        ),
    ]
    for url, data in posts:
        assert client.post(url, data).status_code == 403, url
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 1


# ===========================================================================
# 9. The resolution layer's central promise
# ===========================================================================


def test_everything_the_resolver_outputs_comes_from_the_target_registry(event):
    """The one property the whole import path rests on.

    A hostile file names keys that do not exist and supplies ``meta.references``
    designed to talk the resolver into a mapping. Whatever comes out has to
    resolve in the target event's registry -- no exceptions, no pass-through.
    """
    from pretix_custom_reports.portability.envelope import parse_document
    from pretix_custom_reports.portability.resolution import resolve_definition
    from pretix_custom_reports.registry.library import field_registry

    with scopes_disabled():
        question = Question.objects.create(
            event=event,
            question="Shirt size",
            identifier="tshirt_size",
            type=Question.TYPE_CHOICE,
        )
        for label in ("S", "M"):
            QuestionOption.objects.create(question=question, answer=label)

    document = {
        "schema_version": contracts.SCHEMA_VERSION,
        "name": "Hostile",
        "meta": {
            "references": [
                # A hint pointing at a key the definition does not use.
                {"key": "order.email", "label": "Shirt size", "kind": "field"},
                # A hint whose label matches a real question of this event.
                {
                    "key": "answer.T-Shirt-Size",
                    "label": "Shirt size",
                    "kind": "field",
                },
                {"key": "answer.nonsense", "label": "Shirt size", "kind": "field"},
                {"key": 42, "label": None, "kind": "field"},
            ]
        },
        "definition": definition(
            columns=("order.code", "answer.T-Shirt-Size", "answer.nonsense"),
        ),
    }
    parsed = parse_document(document)
    registry = field_registry()
    with scope(organizer=event.organizer):
        outcome = resolve_definition(
            parsed.definition,
            event=event,
            registry=registry,
            references=parsed.references,
            strategy="skip",
        )
        assert outcome.document is not None
        published = registry.get_fields(event, outcome.document.base)
        for key in outcome.document.field_keys():
            assert key in published, key


def test_a_reference_hint_can_never_invent_a_key_of_its_own(event):
    """``meta.references`` is a hint, never data."""
    from pretix_custom_reports.portability.envelope import parse_document

    document = {
        "schema_version": contracts.SCHEMA_VERSION,
        "name": "Hostile",
        "meta": {
            "references": [
                {"key": "order.email", "label": "x", "kind": "field"},
                {"key": "answer.unused", "label": "y", "kind": "field"},
            ]
        },
        "definition": definition(columns=("order.code",)),
    }
    parsed = parse_document(document)
    # Only hints for keys the definition actually uses survive.
    assert {r.key for r in parsed.references} <= {"order.code"}


def test_a_core_key_is_never_matched_by_similarity(event):
    """``order.c-o-d-e`` must not silently become ``order.code``."""
    from pretix_custom_reports.portability.resolution import (
        STATUS_MISSING,
        resolve_definition,
    )
    from pretix_custom_reports.registry.library import field_registry

    document = contracts.ReportDefinition(
        base=contracts.Base.ORDERPOSITION,
        columns=(contracts.Column(field="order.c-o-d-e"),),
    )
    with scope(organizer=event.organizer):
        outcome = resolve_definition(document, event=event, registry=field_registry())
    assert [e.status for e in outcome.report.fields] == [STATUS_MISSING]
    # The document survives so the confirmation page can show it, but it is
    # blocked -- commit_import() refuses anything whose plan is not ok.
    assert not outcome.ok
    assert outcome.report.blocking


# ===========================================================================
# 10. Measurements that are not (yet) findings
# ===========================================================================


def test_a_null_byte_in_a_label_is_accepted_by_the_structural_validator():
    """Documented, not fixed: SQLite stores it, PostgreSQL ``jsonb`` will not.

    See docs/security-review.md, section "Unbestaetigt" -- the failure mode
    needs a PostgreSQL run to be proven, and the test environment is SQLite.
    """
    document = contracts.validate_definition(
        definition(columns=[{"field": "order.code", "label": "a\x00b"}])
    )
    assert "\x00" in document.columns[0].label


def test_the_field_library_endpoint_costs_a_bounded_number_of_queries(
    admin_client, event, orders
):
    """It evaluates every event-scoped ``choices`` callable on every request."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with scopes_disabled():
        for index in range(5):
            Question.objects.create(
                event=event,
                question="Q{}".format(index),
                identifier="q{}".format(index),
                type=Question.TYPE_CHOICE,
            )
    with CaptureQueriesContext(connection) as captured:
        response = admin_client.get(event_url("api.fields", event))
    assert response.status_code == 200
    assert len(captured.captured_queries) < 60, len(captured.captured_queries)


def test_the_editor_javascript_never_renders_server_text_as_markup():
    """Server error messages carry the definition's own field keys.

    They are rendered through ``textContent``; the ``html`` escape hatch of the
    ``el()`` helper must stay unused.
    """
    js = (
        PLUGIN_ROOT / "static" / "pretix_custom_reports" / "js" / "report-editor.js"
    ).read_text(encoding="utf-8")
    assert "html:" not in js
    # Two only: el()'s escape hatch itself, and the server-rendered preview.
    assert js.count("innerHTML") == 2


# ===========================================================================
# 11. Third round: the "keep" strategy and the plugin gate on the exporter
# ===========================================================================


@pytest.mark.xfail(
    strict=True,
    reason="S-006: the import view passes the POSTed strategy straight into "
    "ResolutionStrategy.coerce, and 'keep' is one of the three it accepts. "
    "resolve_definition() skips query.plan.check_definition() under 'keep', so "
    "a POST that the UI never offers stores a report the compiler refuses.",
)
def test_the_import_view_cannot_be_talked_into_the_event_copy_strategy(
    admin_client, event
):
    """``keep`` exists for the event copy, where nobody is at a screen.

    Reached from a browser it removes the second of the two gates
    ``resolution.py`` documents: the compiler's own ``check_definition``. The
    definition below resolves -- ``position.price`` exists on base ``order`` --
    but needs an aggregate there, which is exactly what ``check_definition``
    would have said.
    """
    body = json.dumps(definition(base="order", columns=("position.price",)))
    response = admin_client.post(
        event_url("event.reports.import", event),
        {"text": body, "strategy": "keep", "action": "confirm"},
    )
    assert response.status_code in (200, 302)
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


def test_the_same_definition_is_refused_under_the_offered_strategies(
    admin_client, event
):
    """Control group for the xfail above: ``abort`` and ``skip`` both refuse."""
    body = json.dumps(definition(base="order", columns=("position.price",)))
    for strategy in ("abort", "skip"):
        response = admin_client.post(
            event_url("event.reports.import", event),
            {"text": body, "strategy": strategy, "action": "confirm"},
        )
        assert response.status_code == 200, strategy
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


def test_the_event_level_exporter_is_invisible_when_the_plugin_is_off(
    event_without_plugin, registered_exporter, user_with_perms
):
    """``register_data_exporters`` is an ``EventPluginSignal``.

    The event-level half of S-002 is fine; only the organizer-level half is not.
    """
    from pretix.base.services.export import init_event_exporters

    with scope(organizer=event_without_plugin.organizer):
        identifiers = [
            ex.identifier
            for ex in init_event_exporters(
                event=event_without_plugin, user=user_with_perms
            )
        ]
    assert "customreports" not in identifiers


def test_the_generated_export_file_name_survives_a_dotted_event_slug(
    organizer, orders, event, registered_exporter, user_with_perms
):
    """``get_filename()`` interpolates the slug without sanitising it.

    pretix' slug validator keeps that safe today (no ``/``, no quote, no
    newline). The test pins the assumption so a future slug rule cannot silently
    turn the file name into a header injection.
    """
    with scopes_disabled():
        dotted = Event.objects.create(
            organizer=organizer,
            name="Dotted",
            slug="a.b-c_d",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
        )
        ReportDefinition.objects.create(
            event=dotted,
            name="R",
            identifier="DOTTED",
            base="orderposition",
            definition=definition(),
        )
    with scope(organizer=organizer):
        exporter = init_event_exporter(
            identifier="customreports", event=dotted, user=user_with_perms
        )
        name, _mime, _data = exporter.render({"report": "DOTTED", "_format": "default"})
    assert name.isascii()
    for forbidden in ("/", "\\", '"', "\r", "\n", ".."):
        assert forbidden not in name, name
