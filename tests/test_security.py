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
import io
import json
import pathlib
import pytest
import re
import warnings
import weakref
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
from pretix_custom_reports.portability.resolution import ResolutionStrategy
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

#: The dispatch_uids ``signals.py`` uses. Not test-local ones on purpose, see
#: :func:`registered_exporter`.
EXPORTER_UID = "pretix_custom_reports_exporter"
EXPORTER_MULTI_UID = "pretix_custom_reports_multiexporter"

#: The wiring these tests need, as ``(signal, receiver, dispatch_uid)``.
EXPORTER_WIRING = (
    (register_data_exporters, exporters.register_report_exporter, EXPORTER_UID),
    (
        register_multievent_data_exporters,
        exporters.register_multievent_report_exporter,
        EXPORTER_MULTI_UID,
    ),
)


def connected_receiver(signal, dispatch_uid):
    """The receiver currently connected to *signal* under *dispatch_uid*.

    ``Signal.receivers`` holds ``(lookup_key, receiver, is_async)`` in Django
    5.2 and ``lookup_key`` is ``(dispatch_uid or id(receiver), id(sender))``
    (django/dispatch/dispatcher.py:96-99, 113-117). With the default
    ``weak=True`` the second slot is a ``weakref.ref`` and has to be
    dereferenced before it can be compared against a function.

    Returns ``None`` if nothing is connected under that dispatch_uid.

    Deliberately duplicated from ``tests/test_exporters.py``: two test modules
    owned by two different agents do not import from each other.
    """
    for lookup_key, receiver, *_ in signal.receivers:
        if lookup_key[0] == dispatch_uid:
            if isinstance(receiver, weakref.ReferenceType):
                return receiver()
            return receiver
    return None


def named_receivers(signal):
    """Every receiver on *signal* that was connected with a string dispatch_uid.

    ``{dispatch_uid: receiver}``. Receivers connected without an explicit uid
    have ``id(receiver)`` in that slot and are skipped -- the uid is what the
    plugins in this process identify themselves by, and it is what
    ``disconnect()`` acts on.
    """
    result = {}
    for lookup_key, receiver, *_ in signal.receivers:
        if isinstance(lookup_key[0], str):
            if isinstance(receiver, weakref.ReferenceType):
                receiver = receiver()
            result[lookup_key[0]] = receiver
    return result


def times_connected(signal, function):
    """How often *function* is connected to *signal*, under any lookup key."""
    count = 0
    for _lookup_key, receiver, *_ in signal.receivers:
        if isinstance(receiver, weakref.ReferenceType):
            receiver = receiver()
        if receiver is function:
            count += 1
    return count


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


@pytest.fixture(scope="module", autouse=True)
def exporter_wiring_before_this_module():
    """The exporter wiring as this module found it, for the canary at the end.

    Module scoped and autouse, so it is taken before the first test here runs.
    What this file has to answer for is that it changes nothing -- not that the
    session was healthy when it got the process. Somebody else's leak is
    somebody else's test to fail.
    """
    return {
        signal: named_receivers(signal)
        for signal in (register_data_exporters, register_multievent_data_exporters)
    }


@pytest.fixture
def registered_exporter():
    """Guarantee the two exporter receivers are connected, and restore after.

    Since wave 4 ``signals.py`` connects both of them at plugin import
    (``apps.ready()``), so in a normal run there is nothing left to do here.
    This must not be written as a plain connect/disconnect pair, and that is
    not a style preference -- both halves of such a pair are wrong:

    * ``Signal.connect()`` skips a receiver whose ``(dispatch_uid, sender_id)``
      key is already present (django/dispatch/dispatcher.py:113-117). Under the
      production uid the call is a silent no-op; under a *test-local* uid it is
      worse, because the key differs and the same receiver gets connected a
      second time -- ``init_event_exporters()`` iterates the signal responses
      without deduplicating (pretix/base/services/export.py:198-225), so every
      test would run against an exporter list holding ``customreports`` twice.
      That is what this fixture used to do, with its own
      ``pretix_custom_reports_security_*`` uids.
    * ``Signal.disconnect(dispatch_uid=...)`` matches on that key *alone*; the
      receiver argument is ignored entirely (dispatcher.py:138-153). Under the
      production uid the teardown therefore removes the registration
      ``signals.py`` made at plugin import, for the rest of the pytest session,
      in whatever file follows. ``signals.py`` runs once and never reconnects.
      pretix' ``EventPluginSignal``/``OrganizerPluginSignal`` override
      ``connect`` but not ``disconnect`` (pretix/base/signals.py:261-311), so
      none of this is softened for plugin signals.

    So: use the production uids, connect only what is missing, disconnect only
    what we connected ourselves. If a uid is already taken, assert it is taken
    by the function we expect -- otherwise the no-op ``connect()`` would run
    this whole module against somebody else's receiver without a word.

    ``register_multievent_data_exporters`` is an
    ``OrganizerPluginSignal(allow_legacy_plugins=True)`` and this plugin is
    event level, so ``connect()`` emits a ``DeprecationWarning``
    (pretix/base/signals.py:301-306). pretix' own test config filters it, ours
    does not, so it is silenced here -- narrowly, so that a *different*
    deprecation stays visible.
    """
    connected_by_us = []
    for signal, receiver, dispatch_uid in EXPORTER_WIRING:
        existing = connected_receiver(signal, dispatch_uid)
        if existing is not None:
            assert existing is receiver, (
                f"{dispatch_uid!r} is connected to {existing!r}, not to "
                f"{receiver!r} -- signals.py and exporters.py disagree."
            )
            continue
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=".*organizer-level.*",
                category=DeprecationWarning,
            )
            signal.connect(receiver, dispatch_uid=dispatch_uid)
        connected_by_us.append((signal, dispatch_uid))
    for signal, receiver, _dispatch_uid in EXPORTER_WIRING:
        # The point of the whole dance: exactly one registration, not two.
        assert times_connected(signal, receiver) == 1, (
            f"{receiver.__name__} is connected {times_connected(signal, receiver)} "
            f"times -- the exporter would appear that often in the export UI."
        )
    yield
    for signal, dispatch_uid in connected_by_us:
        signal.disconnect(dispatch_uid=dispatch_uid)


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


def test_every_event_view_404s_when_the_plugin_is_off(
    admin_client, event_without_plugin
):
    """Regression for S-001 (fixed): switching the plugin off is the brake.

    Written in wave 3 as the proof of the defect, kept as a regression test now
    that ``persistence-dev`` has added a ``PluginActiveMixin`` to
    ``views/crud.py``. Widened while verifying that fix, because the original
    version only proved what it happened to request:

    * all **five** views, not the four that answer GET. ``ReportDuplicateView``
      is POST-only, so a GET against it is 405 whether the gate exists or not --
      it was structurally unable to fail here and has to be attacked with the
      method that actually writes.
    * every writing method, not only the form pages. The gate sits in
      ``dispatch()`` and therefore covers both, but "the confirmation page is a
      404" and "the deletion is refused" are two different statements and only
      the second one is the security property.
    * the database afterwards. A 404 that still wrote would be the worst of both
      worlds, and no status code can rule that out.
    """
    with scopes_disabled():
        foreign = ReportDefinition.objects.create(
            event=event_without_plugin,
            name="Left over",
            identifier="LEFTOVER",
            base="orderposition",
            definition=definition(),
        )
    body = {
        "name": "Sneaked in",
        "description": "",
        "identifier": "SNEAKED1",
        "base": "orderposition",
        "definition": json.dumps(definition()),
    }
    attempts = [
        ("get", event_url("event.reports", event_without_plugin), None),
        ("get", event_url("event.reports.add", event_without_plugin), None),
        ("post", event_url("event.reports.add", event_without_plugin), body),
        (
            "get",
            event_url("event.reports.edit", event_without_plugin, report=foreign.pk),
            None,
        ),
        (
            "post",
            event_url("event.reports.edit", event_without_plugin, report=foreign.pk),
            body,
        ),
        # POST-only view: a GET is 405 even with the gate in place, so the only
        # request that says anything about the gate is the one that writes.
        (
            "post",
            event_url(
                "event.reports.duplicate", event_without_plugin, report=foreign.pk
            ),
            {},
        ),
        (
            "get",
            event_url("event.reports.delete", event_without_plugin, report=foreign.pk),
            None,
        ),
        (
            "post",
            event_url("event.reports.delete", event_without_plugin, report=foreign.pk),
            {},
        ),
    ]
    for method, url, data in attempts:
        response = (
            admin_client.get(url) if data is None else admin_client.post(url, data)
        )
        assert response.status_code == 404, "{} {}".format(method.upper(), url)

    with scopes_disabled():
        # Nothing created, nothing renamed, nothing deleted.
        assert list(ReportDefinition.objects.values_list("identifier", flat=True)) == [
            "LEFTOVER"
        ]
        assert ReportDefinition.objects.get(pk=foreign.pk).name == "Left over"


def test_the_permission_check_runs_before_the_plugin_gate_not_after(
    read_only_user, admin_client, event_without_plugin
):
    """Which of the two gates answers first -- measured, not read off the MRO.

    ``persistence-dev`` closed S-001 with the argument that the MRO puts
    ``PluginActiveMixin`` left of ``EventPermissionRequiredMixin`` and that the
    plugin gate therefore runs first, giving 404 instead of 403 for a
    switched-off event (handoff/status/persistence-dev.md, "Nacharbeit vor Welle
    4"). The MRO part is correct -- ``test_no_crud_view_is_missing_the_plugin_gate``
    asserts it -- but the conclusion is not: ``EventPermissionRequiredMixin``
    does not implement ``dispatch`` at all. It overrides ``as_view()`` and wraps
    the finished view in ``event_permission_required(...)``
    (pretix/control/permissions.py:81-91), so the permission decorator sits
    *outside* the whole class-based dispatch chain and runs before any mixin.

    Not a defect -- both gates refuse, and this order is the more careful one,
    because a user who may not see the page cannot learn from the status code
    whether the plugin is on. It is asserted here so that the wrong rationale
    cannot be reused for the next view, and so a future refactor that moves the
    permission check into ``dispatch()`` becomes visible instead of silent.

    ``views/api.py``, ``views/portability.py`` and ``views/templates.py`` carry
    the same mixin combination and therefore behave the same way; the second
    URL in the first loop is a portability view for exactly that reason.
    """
    client = logged_in(read_only_user)
    # Missing permission wins over the plugin gate: 403, not 404.
    for name in ("event.reports.add", "event.reports.import"):
        url = event_url(name, event_without_plugin)
        assert client.get(url).status_code == 403, url

    # And where the permission *is* held, the gate is what answers.
    url = event_url("event.reports", event_without_plugin)
    assert client.get(url).status_code == 404, url
    assert admin_client.get(url).status_code == 404, url


def test_the_endpoints_that_do_have_the_plugin_gate_really_404(
    admin_client, event_without_plugin
):
    """The other four view modules -- api, editor, portability, templates.

    This was the control group for the S-001 xfail: it showed that the modules
    which *did* gate answered 404, so a failing CRUD test could not be blamed on
    the fixture or on the router. Kept now that S-001 is closed, because it is
    the only place that asserts the four gates side by side.
    """
    urls = [
        event_url("api.fields", event_without_plugin),
        event_url("editor.new", event_without_plugin),
        event_url("event.reports.import", event_without_plugin),
        event_url("event.reports.templates", event_without_plugin),
    ]
    for url in urls:
        assert admin_client.get(url).status_code == 404, url


def test_no_crud_view_is_missing_the_plugin_gate():
    """Belt and braces: the gate is a class property, not a URL property.

    The request-level test above can only see the routes this module attaches. A
    sixth view added to ``views/crud.py`` tomorrow would pass it simply by not
    being routed here while being routed in ``urls.py``. So the class hierarchy
    is asserted directly, for every view class the module exports.
    """
    from django.views.generic import View as DjangoView

    from pretix_custom_reports.views import crud

    view_classes = [
        getattr(crud, name)
        for name in crud.__all__
        if isinstance(getattr(crud, name), type)
        and issubclass(getattr(crud, name), DjangoView)
    ]
    assert len(view_classes) == 5, [c.__name__ for c in view_classes]
    for view_class in view_classes:
        mro = [c.__name__ for c in view_class.__mro__]
        assert "PluginActiveMixin" in mro, view_class.__name__
        # ...and left of the permission mixin, or the 403 would come first.
        assert mro.index("PluginActiveMixin") < mro.index(
            "EventPermissionRequiredMixin"
        ), view_class.__name__
        assert view_class.plugin_module == "pretix_custom_reports"


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
    for hostile in ("keep\x00", "KEEP", "', 'skip", None, 42, ["skip"]):
        assert ResolutionStrategy.coerce(hostile) == ResolutionStrategy.ABORT


def test_the_import_view_never_accepts_keep_from_the_browser(admin_client, event):
    """``keep`` is the event-copy strategy and must not be user selectable.

    ``keep`` leaves unresolvable keys in place *and* skips the compiler check,
    so offering it through the form would let a file store a report the target
    event cannot run.
    """
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
# The lone surrogate (S-003 and S-007, both closed 2026-08-03)
# ---------------------------------------------------------------------------


def test_a_lone_surrogate_is_refused_by_the_payload_gate():
    """``"\\ud800"`` is legal JSON syntax but not encodable as UTF-8.

    Was S-003, closed 2026-08-03. ``payload._walk`` now re-encodes every string
    it walks. The marker is gone; what replaced it is the reason code, because
    ``pytest.raises(PayloadRejected)`` on its own would also be satisfied by the
    depth, node-count or size gate -- a fix at the wrong end of the file would
    have kept this test green.

    Positions matter as much as the reason. ``_walk`` pushes dictionary *keys*
    onto its stack next to the values, and only because of that is the last case
    here rejected; a version that walked values alone would pass the first two.
    """
    from pretix_custom_reports.portability.errors import REASON_NOT_UTF8

    cases = {
        "label": b'{"schema_version": 1, "base": "order", "columns": '
        b'[{"field": "order.code", "label": "\\ud800"}]}',
        "low surrogate": b'{"schema_version": 1, "base": "order", "columns": '
        b'[{"field": "order.code", "label": "\\udc00"}]}',
        "nested filter value": b'{"schema_version": 1, "base": "order", "columns": '
        b'[{"field": "order.code"}], "filters": {"op": "and", "children": '
        b'[{"field": "order.code", "operator": "contains", "value": "\\ud800"}]}}',
        "object key": b'{"schema_version": 1, "\\ud800": 1, "base": "order", '
        b'"columns": [{"field": "order.code"}]}',
    }
    for what, raw in cases.items():
        with pytest.raises(PayloadRejected) as excinfo:
            load_json_object(raw)
        assert excinfo.value.reason == REASON_NOT_UTF8, what


def test_the_payload_gate_still_accepts_text_outside_the_basic_plane():
    """Control group for the gate above -- and the way it could have overshot.

    ``"\\ud83d\\ude00"`` is a *pair*, which ``json.loads`` folds into one
    astral-plane character. A gate that had rejected the escape ``\\ud800``
    textually, before parsing, would refuse every emoji and every rarely used
    CJK character in a label. The gate has to look at the parsed string, and
    this is what says so.
    """
    raw = (
        b'{"schema_version": 1, "base": "order", "columns": '
        b'[{"field": "order.code", "label": "\\ud83d\\ude00 \\u00fc"}]}'
    )
    document = load_json_object(raw)
    assert document["columns"][0]["label"] == "\U0001f600 \u00fc"


def test_an_imported_file_can_no_longer_carry_a_lone_surrogate(admin_client, event):
    """The gate, reached the way an administrator reaches it.

    :func:`load_json_object` is the unit; this is the route. The import view is
    the one place where a document from outside is handed to the plugin, and the
    finding was that it stored the surrogate and left behind a report whose
    preview answered 500.
    """
    body = (
        '{"schema_version": 1, "base": "order", "columns": '
        '[{"field": "order.code", "label": "\\ud800"}]}'
    )
    response = admin_client.post(
        event_url("event.reports.import", event), {"text": body, "action": "confirm"}
    )
    assert response.status_code == 200
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


def test_the_validate_endpoint_survives_a_lone_surrogate(admin_client, event):
    """Was S-003, closed 2026-08-03 (``_ApiView.json`` -> ``ensure_ascii=True``).

    ``status_code == 200`` alone would not be a measurement. The endpoint echoes
    the definition back, so the assertion that matters is that the surrogate
    *travelled through the serialiser* and came out escaped: the body is pure
    ASCII, it contains the escape, and parsing it returns the very string that
    was posted. A fix that dropped or replaced the character would satisfy
    "200" and fail here -- and would silently rewrite the user's label.
    """
    label = "x" + LONE_SURROGATE
    body = definition(columns=[{"field": "order.code", "label": label}])
    response = post_json(admin_client, event_url("api.validate", event), body_of(body))
    assert response.status_code == 200
    response.content.decode("ascii")  # raises if anything got through unescaped
    assert rb"\ud800" in response.content
    payload = json.loads(response.content)
    assert payload["ok"] is True
    assert payload["definition"]["columns"][0]["label"] == label


def body_of(document):
    return {"definition": document}


def test_the_preview_endpoint_survives_a_lone_surrogate(admin_client, event, orders):
    """Was S-003, closed 2026-08-03.

    Same three-part measurement as the validate endpoint, plus one more: the
    preview must actually have *run*. Without the row assertion this test would
    also pass on a preview that answered 200 with an empty result, which is what
    a rejection dressed up as a success would look like.
    """
    label = "x" + LONE_SURROGATE
    response = post_json(
        admin_client,
        event_url("api.preview", event),
        {"definition": definition(columns=[{"field": "order.code", "label": label}])},
    )
    assert response.status_code == 200
    response.content.decode("ascii")
    assert rb"\ud800" in response.content
    payload = json.loads(response.content)
    assert payload["columns"][0]["label"] == label
    assert payload["rows"] == [["AAAAA"], ["AAAAA"]]


def test_the_export_view_survives_a_stored_lone_surrogate(admin_client, event):
    """Was S-003, closed 2026-08-03 (``views/portability.py``).

    A definition stored *before* the payload gate learned about surrogates is
    still out there, so the reading end has to hold on its own. The file has to
    stay a faithful copy of what is in the database, hence the round-trip.
    """
    label = "x" + LONE_SURROGATE
    with scopes_disabled():
        poisoned = ReportDefinition.objects.create(
            event=event,
            name="Poisoned",
            base="orderposition",
            definition=definition(columns=[{"field": "order.code", "label": label}]),
        )
    response = admin_client.get(
        event_url("event.reports.export", event, report=poisoned.pk)
    )
    assert response.status_code == 200
    response.content.decode("ascii")
    assert rb"\ud800" in response.content
    document = json.loads(response.content)
    assert document["definition"]["columns"][0]["label"] == label


def test_the_template_export_survives_a_stored_lone_surrogate(
    admin_client, organizer, event
):
    """The organizer half of the same fix (``views/templates.py:285``).

    The finding named this line; nothing measured it. One class serves the
    report export and the template export, but they are two functions in two
    modules, and only one of them was under test.
    """
    label = "x" + LONE_SURROGATE
    with scopes_disabled():
        poisoned = ReportDefinition.objects.create(
            organizer=organizer,
            name="Poisoned template",
            base="orderposition",
            definition=definition(columns=[{"field": "order.code", "label": label}]),
        )
    response = admin_client.get(
        organizer_url("organizer.templates.export", organizer, template=poisoned.pk)
    )
    assert response.status_code == 200
    response.content.decode("ascii")
    document = json.loads(response.content)
    assert document["definition"]["columns"][0]["label"] == label


def test_the_exported_file_of_a_poisoned_report_is_refused_on_the_way_back_in(
    admin_client, event
):
    """The asymmetry the two halves of the S-003 fix create, pinned on purpose.

    The export of a poisoned report succeeds -- that is the point of the
    ``ensure_ascii`` half -- and the resulting file is then rejected by the
    payload gate, which is the other half. That is coherent (the document is
    quarantined rather than propagated), it is not obvious, and if it ever
    changes the change should be a decision rather than a side effect.
    """
    label = "x" + LONE_SURROGATE
    with scopes_disabled():
        poisoned = ReportDefinition.objects.create(
            event=event,
            name="Poisoned",
            base="orderposition",
            definition=definition(columns=[{"field": "order.code", "label": label}]),
        )
    exported = admin_client.get(
        event_url("event.reports.export", event, report=poisoned.pk)
    ).content
    with pytest.raises(PayloadRejected):
        load_json_object(exported)


def test_the_editor_page_survives_a_stored_lone_surrogate(admin_client, event):
    """Boundary of S-003, and the reference the S-007 fix was measured against.

    The editor embeds the whole definition in a ``<script type="application/json">``
    block through pretix' ``escapejson_dumps``, which is ``json.dumps`` with its
    default ``ensure_ascii=True`` (pretix/base/templatetags/escapejson.py:44).
    That is why the graphical editor was never part of the finding -- and it is
    what made the change form's ``ensure_ascii=False`` (S-007) a local mistake
    rather than a missing rule. Both now render the same way; this test is what
    says the rule was there to copy.
    """
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
    response = admin_client.get(event_url("editor.edit", event, identifier="POISONED"))
    assert response.status_code == 200
    assert rb"\ud800" in response.content


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


def test_the_xlsx_path_survives_a_stored_lone_surrogate(
    event, orders, registered_exporter, user_with_perms
):
    """Second bound of S-003, and the one that matters most if it ever moves.

    CSV survives because ``ListExporter`` encodes with ``errors="replace"``.
    XLSX takes a different road entirely -- ``SafeWorkbook`` hands the label to
    openpyxl, which writes XML -- so "CSV is fine" says nothing about it, and
    the finding did not check. It is fine, measured: a real zip container comes
    back.

    The reason to spend a test on it: this is the *unattended* path. An
    exception here does not produce a 500 somebody sees, it produces five Celery
    retries and a scheduled export that silently stops arriving.
    """
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
    buffer = io.BytesIO()
    with scope(organizer=event.organizer):
        exporter = init_event_exporter(
            identifier="customreports", event=event, user=user_with_perms
        )
        filename, _mime, content = exporter.render(
            {"report": "POISONED", "_format": "xlsx"}, output_file=buffer
        )
    # ``output_file`` is used because ``_render_xlsx`` otherwise re-opens a
    # ``NamedTemporaryFile`` by name, which Windows refuses; same code path.
    assert content is None
    assert filename.endswith(".xlsx")
    assert buffer.getvalue()[:2] == b"PK"


def test_the_pretix_event_log_survives_a_stored_lone_surrogate(admin_client, event):
    """Third bound of S-003, on a page that is not ours.

    ``ReportDefinition.log_data()`` puts the **whole definition** into every log
    entry, so a poisoned report leaves a poisoned ``LogEntry.data`` behind, and
    that data is rendered by pretix' own event log view. Had that view formatted
    the payload rather than the action type, one bad label would have taken out
    a core page for the whole event -- a much larger blast radius than anything
    in the finding.

    It does not, measured. The test stays as a tripwire: it costs nothing and it
    is the only thing standing between ``log_data()`` and the next person who
    decides our log entries should display their contents.
    """
    from pretix.base.models import LogEntry

    response = admin_client.post(
        event_url("event.reports.add", event),
        {
            "name": "Logged",
            "description": "",
            "identifier": "",
            "base": "orderposition",
            "definition": json.dumps(
                definition(
                    columns=[{"field": "order.code", "label": "x" + LONE_SURROGATE}]
                )
            ),
        },
    )
    assert response.status_code == 302
    with scopes_disabled():
        entry = LogEntry.objects.get(action_type="pretix_custom_reports.report.added")
    assert entry.parsed_data["definition"]["columns"][0]["label"] == (
        "x" + LONE_SURROGATE
    )
    log_page = admin_client.get(
        reverse(
            "control:event.log",
            kwargs={"organizer": event.organizer.slug, "event": event.slug},
        )
    )
    assert log_page.status_code == 200


@pytest.mark.parametrize("owner", ["event", "organizer"])
def test_the_change_form_survives_a_stored_lone_surrogate(
    admin_client, event, organizer, owner
):
    """Was S-007, closed 2026-08-03 -- the site the S-003 fix had missed.

    Three readers were switched to ``ensure_ascii=True`` in the S-003 round
    (``views/api.py``, ``views/portability.py``, ``views/templates.py``); a
    fourth was not. The change form renders the definition into its textarea
    through :meth:`~pretix_custom_reports.forms.PrettyJSONFormField.prepare_value`,
    which asked for ``ensure_ascii=False``, and the resulting ``str`` died in
    ``django/http/response.py:324`` exactly as the other three used to.

    ``status_code == 200`` alone is not a measurement, for the same reason it
    was not one at the three endpoints: a "fix" that dropped or replaced the
    character would satisfy it while silently rewriting the definition a user is
    about to edit -- and this form *writes back what it shows*, so a lossy
    render here would not merely look wrong, it would save wrong. The textarea
    is therefore parsed out of the page and round-tripped: what the form offers
    for editing has to be, character for character, what is in the database.

    Both owners are parametrised because ``ReportDefinitionForm`` serves the
    event report and the organizer template from one class, so one line broke
    two pages -- and the fix has to hold on both.
    """
    import html as html_module

    label = "x" + LONE_SURROGATE
    poisoned = definition(columns=[{"field": "order.code", "label": label}])
    with scopes_disabled():
        if owner == "event":
            row = ReportDefinition.objects.create(
                event=event,
                name="Poisoned",
                base="orderposition",
                definition=poisoned,
            )
            url = event_url("event.reports.edit", event, report=row.pk)
        else:
            row = ReportDefinition.objects.create(
                organizer=organizer,
                name="Poisoned template",
                base="orderposition",
                definition=poisoned,
            )
            url = organizer_url("organizer.templates.edit", organizer, template=row.pk)

    response = admin_client.get(url)
    assert response.status_code == 200
    assert rb"\ud800" in response.content

    body = response.content.decode("utf-8")
    match = re.search(
        r'<textarea[^>]*name="definition"[^>]*>(.*?)</textarea>', body, re.S
    )
    assert match, "the definition textarea is not on the page"
    shown = json.loads(html_module.unescape(match.group(1)))
    assert shown["columns"][0]["label"] == label


def test_a_poisoned_report_is_still_repairable_through_the_editor(admin_client, event):
    """Both repair paths, end to end. Was the severity argument for S-007.

    While S-007 was open this test carried the reason it was *niedrig* rather
    than a repeat of S-003 at full severity: the change form was one of two ways
    to fix a poisoned report and the only one that broke, because the graphical
    editor renders through ``escapejson_dumps`` and saves through a POST, which
    never reaches ``prepare_value``.

    S-007 is closed and the argument is spent, but the walk is not. Nothing else
    in this module goes editor -> save -> reopen in one sequence, and the last
    assertion is the one that would have caught S-007 in the first place: after
    the repair the change form must open again.
    """
    with scopes_disabled():
        poisoned = ReportDefinition.objects.create(
            event=event,
            name="Poisoned",
            identifier="POISONED",
            base="orderposition",
            definition=definition(
                columns=[{"field": "order.code", "label": "x" + LONE_SURROGATE}]
            ),
        )
    assert (
        admin_client.get(
            event_url("editor.edit", event, identifier="POISONED")
        ).status_code
        == 200
    )
    response = admin_client.post(
        event_url("event.reports.edit", event, report=poisoned.pk),
        {
            "name": "Repaired",
            "description": "",
            "identifier": "POISONED",
            "base": "orderposition",
            "definition": json.dumps(definition()),
        },
    )
    assert response.status_code == 302
    with scopes_disabled():
        poisoned.refresh_from_db()
    assert poisoned.definition["columns"][0].get("label") is None
    assert (
        admin_client.get(
            event_url("event.reports.edit", event, report=poisoned.pk)
        ).status_code
        == 200
    )


def test_the_change_form_is_the_only_way_a_surrogate_still_gets_stored(
    admin_client, event
):
    """The write half, and the reason the read half (S-007) was not cosmetic.

    The payload gate closed the *import*. The change form does not use it: its
    ``clean_definition`` runs ``contracts.validate_definition``, which checks
    lengths and shapes and says nothing about encodability. So a definition with
    a lone surrogate is still storable by anyone with
    ``event.settings.general:write`` -- which is precisely why the reading end
    had to be fixed rather than the writing end guarded: this is the one way in
    that is left, and it stays open.

    Green on purpose: it documents an accepted write, not a defect. Should the
    gate ever be extended to this path, this test turns red -- and the decision
    then is whether S-003's quarantine (store nothing that cannot be encoded) or
    S-007's tolerance (render whatever is stored) is the rule for this form. The
    two are not in conflict today only because the second holds unconditionally.
    """
    body = json.dumps(
        definition(columns=[{"field": "order.code", "label": "x" + LONE_SURROGATE}])
    )
    response = admin_client.post(
        event_url("event.reports.add", event),
        {
            "name": "Self inflicted",
            "description": "",
            "identifier": "",
            "base": "orderposition",
            "definition": body,
        },
    )
    assert response.status_code == 302
    with scopes_disabled():
        stored = ReportDefinition.objects.get(name="Self inflicted")
    assert stored.definition["columns"][0]["label"] == "x" + LONE_SURROGATE


# ---------------------------------------------------------------------------
# The identifier collision (S-004, closed 2026-08-03)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("owner", ["event", "organizer"])
def test_a_duplicate_identifier_is_a_form_error_not_a_500(
    admin_client, event, organizer, report, template, owner
):
    """Was S-004, closed 2026-08-03 by ``ReportDefinitionForm.clean_identifier``.

    Three assertions, and the first one alone would not do. The original version
    of this test checked ``200`` and ``b"identifier" in content``; both are true
    of every re-rendered form, because "identifier" is the name of a field on
    the page. What proves the check ran is that *nothing was written* and that
    the page carries the message the new ``clean_identifier`` raises.

    Parametrised over both owners: ``ReportDefinitionForm`` serves the event
    report and the organizer template, the constraints are
    ``(event, identifier)`` and ``(organizer, identifier)``, and the fix routes
    both through ``ReportDefinition._identifier_taken``. Only the event half was
    in the finding; the template half was never tested and is the same line.
    """
    if owner == "event":
        url = event_url("event.reports.add", event)
        taken = report.identifier
    else:
        url = organizer_url("organizer.templates.add", organizer)
        taken = template.identifier

    with scopes_disabled():
        before = ReportDefinition.objects.count()
    response = admin_client.post(
        url,
        {
            "name": "Second report",
            "description": "",
            "identifier": taken,
            "base": "orderposition",
            "definition": json.dumps(definition()),
        },
    )
    assert response.status_code == 200
    assert b"already in use" in response.content
    with scopes_disabled():
        assert ReportDefinition.objects.count() == before


def test_a_report_may_keep_its_own_identifier_when_it_is_changed(
    admin_client, event, report
):
    """Control group one: the check must exclude the row being edited.

    Without ``.exclude(pk=...)`` every save of an existing report would fail on
    its own identifier -- a "fix" that turns the change form into a dead end.
    The editor posts the identifier back in a hidden input, so this is the
    ordinary path, not an edge case.
    """
    response = admin_client.post(
        event_url("event.reports.edit", event, report=report.pk),
        {
            "name": "Renamed",
            "description": "",
            "identifier": report.identifier,
            "base": "orderposition",
            "definition": json.dumps(definition()),
        },
    )
    assert response.status_code == 302
    with scopes_disabled():
        report.refresh_from_db()
    assert report.name == "Renamed"


def test_the_same_identifier_may_be_used_again_in_another_event(
    admin_client, event, rival_event
):
    """Control group two: the check must not be wider than the constraint.

    The uniqueness is ``(event, identifier)``, and an identifier is deliberately
    stable across an event copy (ADR 0001 section 5) -- two events holding the
    same one is the *normal* state after copying an event, not a collision. A
    ``clean_identifier`` that queried globally would pass the test above and
    quietly break event copies, so it is measured here.
    """
    with scopes_disabled():
        ReportDefinition.objects.create(
            event=rival_event,
            name="Elsewhere",
            identifier="SHARED42",
            base="orderposition",
            definition=definition(),
        )
    response = admin_client.post(
        event_url("event.reports.add", event),
        {
            "name": "Here too",
            "description": "",
            "identifier": "SHARED42",
            "base": "orderposition",
            "definition": json.dumps(definition()),
        },
    )
    assert response.status_code == 302
    with scopes_disabled():
        assert (
            ReportDefinition.objects.filter(identifier="SHARED42", event=event).count()
            == 1
        )


def test_the_duplicate_check_survives_without_an_active_scope(event, report):
    """Why the fix does *not* use the manager the review recommended.

    ``docs/security-review.md`` suggested
    ``ReportDefinition.objects.for_event(...).by_identifier(...)``.
    ``ReportDefinition.objects`` is scope-bound (``ReportDefinitionManager``
    mirrors ``ScopedManager`` and hands out a ``DisabledQuerySet`` when the
    ``organizer`` scope is missing), and ``ReportDefinition._identifier_taken``
    runs under ``scopes_disabled()`` with a hard ``event_id``/``organizer_id``
    filter instead. This test holds ``persistence-dev`` to the stronger of the
    two properties: the form validates with no scope active at all, and it still
    refuses the duplicate.

    Note the scope of the claim. Through the control panel both variants would
    have worked -- ``pretix/control/middleware.py:199`` wraps every request in
    ``scope(organizer=request.organizer)`` -- so "the recommendation would have
    raised ``ScopeError``" is not true of the view path. It is true of every
    other caller of the form, and unbound is the safer of the two.
    """
    from pretix_custom_reports.forms import ReportDefinitionForm

    form = ReportDefinitionForm(
        data={
            "name": "Second",
            "description": "",
            "identifier": report.identifier,
            "base": "orderposition",
            "definition": json.dumps(definition()),
        },
        event=event,
    )
    assert form.is_valid() is False
    assert "identifier" in form.errors
    assert form.errors.as_data()["identifier"][0].code == "duplicate_identifier"


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


def _organizer_exporter(organizer, user):
    """The one ``customreports`` exporter pretix builds for an organizer run."""
    found = [
        ex
        for ex in init_organizer_exporters(organizer=organizer, user=user)
        if ex.identifier == "customreports"
    ]
    assert found, "the multi-event exporter was not offered at all"
    return found[0]


def _leftover_in_both_events(organizer, event, event_without_plugin):
    """Report ``LEFTOVER`` plus one order in each of the two events.

    Deliberately in **both**: with the report only in the switched-off event,
    the whole export has nothing left to produce and dies with an
    ``ExportError`` before any assertion about the file content can be made --
    which would prove that the export failed, not that ``OFFEV`` is absent. Both
    events holding the same identifier is also the realistic shape: identifiers
    are unique per event, and an event copy or an organizer template is exactly
    how the same report ends up in several events.
    """
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
        for owner in (event, event_without_plugin):
            ReportDefinition.objects.create(
                event=owner,
                name="Left over",
                identifier="LEFTOVER",
                base="orderposition",
                definition=definition(),
            )


def test_an_organizer_export_skips_events_with_the_plugin_switched_off(
    event, event_without_plugin, registered_exporter, user_with_perms, organizer, orders
):
    """Regression for S-002 (fixed): the organizer export honours the brake.

    ``register_multievent_data_exporters`` is an
    ``OrganizerPluginSignal(allow_legacy_plugins=True)``, so pretix hands this
    event-level plugin to every organizer, and ``self.events`` from
    ``init_organizer_exporters`` is filtered by *permission* only. The per-event
    plugin check therefore has to be the exporter's own, and this is the test
    that it exists.

    ``event`` (plugin on) and ``event_without_plugin`` (plugin off) both hold
    the report ``LEFTOVER`` and both hold an order, so the file has something to
    contain either way: the only thing that can keep ``OFFEV`` out is the gate.
    The original wave-3 version put the report only in the switched-off event;
    after the fix that made ``render()`` raise ``ExportError`` for having no
    usable event left, and the test would then have failed for a reason that has
    nothing to do with the leak.
    """
    _leftover_in_both_events(organizer, event, event_without_plugin)
    with scope(organizer=organizer):
        exporter = _organizer_exporter(organizer, user_with_perms)
        _name, _mime, data = exporter.render(
            {"report": "LEFTOVER", "_format": "default"}
        )
    # The order code and the slug of the switched-off event must not be in the
    # file; the event that still has the plugin must be unaffected by the skip.
    assert b"OFFEV" not in data
    assert b"plain" not in data
    assert b"AAAAA" in data
    assert b"dummy" in data


def test_the_organizer_export_form_never_offers_a_switched_off_events_report(
    event, event_without_plugin, registered_exporter, user_with_perms, organizer
):
    """The gate has to hold in the choice list too, not only when rows are read.

    ``report_choices()`` runs over ``self.events`` as well. Offering the report
    of an event that ``_prepare()`` is then going to refuse would be a choice
    that cannot be honoured -- and, worse, the report *name* of a switched-off
    event is itself information that the brake was supposed to stop.
    """
    with scopes_disabled():
        ReportDefinition.objects.create(
            event=event_without_plugin,
            name="Only in the switched-off event",
            identifier="OFFONLY",
            base="orderposition",
            definition=definition(),
        )
        ReportDefinition.objects.create(
            event=event,
            name="Still live",
            identifier="LIVEONE",
            base="orderposition",
            definition=definition(),
        )
    with scope(organizer=organizer):
        choices = dict(_organizer_exporter(organizer, user_with_perms).report_choices())
    assert "OFFONLY" not in choices
    assert "LIVEONE" in choices
    assert not any("switched-off" in label for label in choices.values())


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


def test_a_report_full_of_join_columns_costs_what_one_column_costs(event, orders):
    """Was S-005 (query amplification), closed 2026-08-03 -- now the fix proof.

    Until 2026-08-03 this test was called
    ``test_a_report_full_of_join_columns_costs_one_query_per_column`` and
    asserted the opposite: it *measured the defect*, because every ``join``
    column got a ``Prefetch`` whose ``to_attr`` was derived from the column
    index, so the de-duplication rule ``(lookup, to_attr)`` could never match and
    twenty identical columns cost twenty prefetch queries.
    ``query/relations.py::join_leaf_to_attr`` now derives that name from the
    identity of the leaf queryset -- relation, condition, canceled rule, inner
    ``select_related`` -- and identical columns collapse into one prefetch.

    The measurement is deliberately made at ``MAX_COLUMNS`` as well as at 20.
    ``MAX_COLUMNS`` is the number the *structural* validator allows, so it is the
    real bound on what a single ``POST api/preview/`` can ask for, and it is the
    number the finding quoted ("~200 round trips").
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

    one = count_for(1)
    assert count_for(2) == one
    assert count_for(20) == one
    assert count_for(contracts.MAX_COLUMNS) == one


def test_join_columns_that_want_different_rows_are_still_kept_apart(event, orders):
    """The other side of the S-005 fix, and the way it could have gone wrong.

    Collapsing prefetches is only safe while "same key" means "same rows". Two
    ``join`` columns over ``answer.<identifier>`` cross the same relation and
    differ solely in their leaf condition (``question__identifier=...``); if the
    de-duplication keyed on the relation alone, one question's answers would
    appear under the other question's heading. That is a cross-column data leak
    inside one event -- no error, no log line, a wrong file -- which is why it is
    measured here and not only in ``tests/test_query_plan.py``.

    Two assertions, because either alone can be satisfied by a broken build: the
    query count says the two prefetches exist separately, the values say they
    carry what their own column asked for.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from pretix_custom_reports.query.compiler import ReportQueryCompiler
    from pretix_custom_reports.registry.library import field_registry

    from .factories import add_answer

    with scopes_disabled():
        position = OrderPosition.objects.filter(order=orders["order"]).first()
        answers = {}
        for key, value in (("size", "L"), ("meal", "vegan")):
            question = Question.objects.create(
                event=event,
                question="Q-{}".format(key),
                identifier=key,
                type=Question.TYPE_STRING,
            )
            add_answer(position, question, value)
            answers[key] = value

    body = definition(
        base="order",
        columns=[
            {"field": "answer.size", "aggregate": "join"},
            {"field": "answer.meal", "aggregate": "join"},
        ],
    )
    document = contracts.validate_definition(body)
    with scope(organizer=event.organizer):
        compiled = ReportQueryCompiler(field_registry()).compile(
            document, event, preview=True
        )
        with CaptureQueriesContext(connection) as captured:
            rows = list(compiled.iter_rows())

    assert rows == [["L", "vegan"]]
    # One row query, one shared intermediate level, one leaf per question.
    assert len(captured.captured_queries) == 4


def test_the_residual_cost_of_join_columns_is_bounded_by_distinct_conditions(
    event, orders
):
    """What is left of S-005 after the fix, stated as a number.

    The amplification is gone for *identical* columns; it is not gone in
    principle. Ten ``join`` columns over ten different questions are ten
    genuinely different prefetches and cost ten queries, and ``MAX_COLUMNS`` is
    200. The difference to the finding is the price of admission: it now takes
    N distinct questions in the event -- created with
    ``event.can_change_items``, not with the ``event.orders:read`` that the
    preview needs -- where before a single field repeated 200 times was enough
    for anyone who could open the editor.

    Green, and it documents a residual rather than a defect. If somebody caps
    ``join`` columns later (the other option the finding offered) this turns red
    and should be re-cut around the cap, not deleted.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from pretix_custom_reports.query.compiler import ReportQueryCompiler
    from pretix_custom_reports.registry.library import field_registry

    distinct = 10
    with scopes_disabled():
        for index in range(distinct):
            Question.objects.create(
                event=event,
                question="Q{}".format(index),
                identifier="k{}".format(index),
                type=Question.TYPE_STRING,
            )

    body = definition(
        base="order",
        columns=[
            {"field": "answer.k{}".format(index), "aggregate": "join"}
            for index in range(distinct)
        ],
    )
    document = contracts.validate_definition(body)
    with scope(organizer=event.organizer):
        compiled = ReportQueryCompiler(field_registry()).compile(
            document, event, preview=True
        )
        with CaptureQueriesContext(connection) as captured:
            list(compiled.iter_rows())
    # 1 row query + 1 shared intermediate + one leaf per question.
    assert len(captured.captured_queries) == distinct + 2


def test_the_condition_signature_refuses_to_merge_what_it_cannot_read(event):
    """The deliberate gap in ``condition_signature``, held in place.

    ``query-dev`` chose a stricter comparison than the ``str(Q)`` the finding
    suggested: only scalars and lists of scalars are signed, everything else
    yields ``None`` and the caller falls back to the old per-column name. That
    is the right way round -- ``str(Q)`` renders a model instance through its
    ``__str__``, so two ``Question`` rows with the same label would look equal
    and their prefetches would be merged, which is the leak the test above
    guards against.

    Failing open (no signature, no merge) costs a query; failing closed would
    cost correctness. This test pins the direction so a later "optimisation"
    cannot quietly widen the signature to model instances.
    """
    from django.db.models import Q

    from pretix_custom_reports.query.relations import condition_signature

    assert condition_signature(None) == "none"
    assert condition_signature(Q(a=1)) is not None
    assert condition_signature(Q(a=1)) != condition_signature(Q(a="1"))
    assert condition_signature(Q(a=1)) != condition_signature(Q(a=True))
    assert condition_signature(Q(a__in=[1, 2])) != condition_signature(Q(a__in=[2, 1]))
    # Anything the signature cannot state faithfully must yield ``None``.
    assert condition_signature(Q(event=event)) is None
    assert condition_signature(Q(a=object())) is None
    assert condition_signature(Q(a=1) & Q(event=event)) is None


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


def test_the_import_view_cannot_be_talked_into_the_event_copy_strategy(
    admin_client, event
):
    """Was S-006, closed 2026-08-03 by ``ResolutionStrategy.coerce_user_choice``.

    ``keep`` exists for the event copy, where nobody is at a screen. Reached
    from a browser it removed the second of the two gates ``resolution.py``
    documents: the compiler's own ``check_definition``. The definition below
    resolves -- ``position.price`` exists on base ``order`` -- but needs an
    aggregate there, which is exactly what ``check_definition`` says.

    "Nothing was stored" is the assertion that carries the finding, but on its
    own it would also hold if the import had failed for an unrelated reason, so
    the effective strategy is read back off the confirmation page: the view must
    have fallen back to ``abort``, not merely refused.
    """
    body = json.dumps(definition(base="order", columns=("position.price",)))
    response = admin_client.post(
        event_url("event.reports.import", event),
        {"text": body, "strategy": "keep", "action": "confirm"},
    )
    assert response.status_code == 200
    assert response.context["plan"].strategy == ResolutionStrategy.ABORT
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 0


def test_the_template_apply_view_cannot_be_talked_into_the_event_copy_strategy(
    admin_client, event, organizer
):
    """The second view the fix had to touch, which the finding did not test.

    ``views/templates.py:370`` reads the same POST field for "load this
    organizer template into this event". It is the same class of gate as the
    import -- a definition arriving from outside this event -- and it was
    passing the raw value into ``coerce`` too.
    """
    with scopes_disabled():
        bad = ReportDefinition.objects.create(
            organizer=organizer,
            name="Not compilable",
            identifier="BADTPL",
            base="order",
            definition=definition(base="order", columns=("position.price",)),
        )
    response = admin_client.post(
        event_url("event.reports.templates.apply", event, template=bad.pk),
        {"strategy": "keep", "action": "confirm"},
    )
    assert response.status_code == 200
    assert response.context["plan"].strategy == ResolutionStrategy.ABORT
    with scopes_disabled():
        assert ReportDefinition.objects.filter(event=event).count() == 0


@pytest.mark.parametrize(
    "hostile",
    [
        "keep",
        "KEEP",
        " keep",
        "keep\x00",
        "keep ",
        "abort,keep",
        ["keep"],
        None,
        42,
        True,
    ],
)
def test_no_posted_value_whatsoever_yields_the_event_copy_strategy(hostile):
    """The coercion itself, over the shapes a POST field can actually take.

    ``request.POST.get()`` yields a ``str`` or ``None``; a ``QueryDict`` can be
    talked into a list, and JSON is not involved here, so the type zoo is small
    and fully enumerated. Whitespace and the null byte are in the list because
    "trim then compare" is the obvious next refactor and it would open the hole
    again.
    """
    assert ResolutionStrategy.coerce_user_choice(hostile) == ResolutionStrategy.ABORT


def test_the_event_copy_can_still_ask_for_keep():
    """Control group: the narrow coercion must not have removed the strategy.

    ``portability/eventcopy.py:130`` passes ``ResolutionStrategy.KEEP``
    programmatically, and it needs to keep working -- an event copy that lost
    columns instead of carrying them along unresolved would be a different, and
    worse, bug. The split is between the two functions, not between the three
    strategies.
    """
    assert ResolutionStrategy.coerce("keep") == ResolutionStrategy.KEEP
    assert ResolutionStrategy.KEEP not in ResolutionStrategy.USER_CHOICES
    assert set(ResolutionStrategy.USER_CHOICES) == {
        ResolutionStrategy.ABORT,
        ResolutionStrategy.SKIP,
    }


def test_no_view_hands_a_request_value_to_the_wide_coercion():
    """The rule behind S-006, checked over the syntax tree rather than per view.

    Two views were fixed. A third that reads ``strategy`` from a request and
    calls the wide ``coerce`` would reopen the finding without failing any of
    the tests above, so the shape itself is forbidden: inside ``views/``, no
    call to ``ResolutionStrategy.coerce`` may take an argument that mentions
    ``request``.
    """
    root = PLUGIN_ROOT / "views"
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "coerce":
                continue
            source = " ".join(ast.dump(arg) for arg in node.args)
            assert "request" not in source, f"{path.name}:{node.lineno}"


def test_the_same_definition_is_refused_under_the_offered_strategies(
    admin_client, event
):
    """Control group for the two views above: ``abort`` and ``skip`` both refuse."""
    body = json.dumps(definition(base="order", columns=("position.price",)))
    for strategy in ("abort", "skip"):
        response = admin_client.post(
            event_url("event.reports.import", event),
            {"text": body, "strategy": strategy, "action": "confirm"},
        )
        assert response.status_code == 200, strategy
        assert response.context["plan"].strategy == strategy
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


# NOTE: keep this last. pytest runs the tests of a module in definition order,
# so a check placed here has seen every ``registered_exporter`` teardown above.
def test_this_module_hands_the_exporter_wiring_back_untouched(
    exporter_wiring_before_this_module,
):
    """Whatever wiring this module was handed, it hands back.

    Nothing added, nothing removed, nothing rebound under a known uid. A leak
    here is order dependent and would surface in somebody else's file, which is
    the worst possible way to find it.

    This covers the *lasting* half of the fixture defect (a teardown that drops
    the production registration for the rest of the session). It cannot cover
    the transient half -- the old fixture's connect/disconnect pair under
    test-local uids was balanced and left nothing behind at module end; that
    the receivers are connected exactly once *while* a test runs is asserted in
    :func:`registered_exporter` itself.

    The second block only runs when we really were handed the production
    wiring, so that this test keeps reporting *our* leaks and not another
    module's.
    """
    after = {
        signal: named_receivers(signal)
        for signal in (register_data_exporters, register_multievent_data_exporters)
    }
    assert after == exporter_wiring_before_this_module

    # Only meaningful when we really were handed the production wiring.
    if all(
        connected_receiver(signal, dispatch_uid) is receiver
        for signal, receiver, dispatch_uid in EXPORTER_WIRING
    ):
        # has_listeners() and the identity check fail for different reasons: a
        # signal emptied wholesale versus our own uid dropped or rebound.
        assert register_data_exporters.has_listeners()
        assert register_multievent_data_exporters.has_listeners()
