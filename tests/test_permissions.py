# Owner from wave 1 on: persistence-dev (see ORCHESTRIERUNG.md section 5)
"""CRUD views end to end, with the permission denials spelled out.

Every view is exercised three times: with full permissions, with read-only
permissions and with a permission that is valid but not ours. The denial cases
are not optional -- a report can contain every order field there is, so a leak
here is a leak of the whole order table.

Routing note: ``urls.py`` belongs to the integrator (ORCHESTRIERUNG.md
section 5) and is wired up in wave 4. The module-scoped ``crud_urls`` fixture
below attaches ``views.crud.event_urlpatterns`` to the plugin's URLconf for the
duration of this module, so these tests run through the real resolver, the real
control middleware and the real permission decorators instead of calling view
functions by hand. The copy-ready lines for wave 4 are in
handoff/requests/persistence-dev-an-integrator-urls.md.
"""

import importlib
import json
import pytest
import sys
from django.urls import clear_url_caches, reverse
from django_scopes import scope
from pretix.base.models import LogEntry, Organizer, Team, User

from pretix_custom_reports import contracts
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.views.crud import (
    CHANGE_PERMISSION,
    URL_NAMESPACE,
    VIEW_PERMISSION,
    event_urlpatterns,
)

from .conftest import PASSWORD

URLCONF = "pretix.multidomain.maindomain_urlconf"


@pytest.fixture(scope="module", autouse=True)
def crud_urls():
    """Attach the CRUD routes to the plugin URLconf for this module."""
    from pretix_custom_reports import urls as plugin_urls

    known = {p.name for p in plugin_urls.urlpatterns}
    added = [p for p in event_urlpatterns if p.name not in known]
    plugin_urls.urlpatterns.extend(added)
    _reload_urlconf()
    yield
    for pattern in added:
        plugin_urls.urlpatterns.remove(pattern)
    _reload_urlconf()


def _reload_urlconf():
    if URLCONF in sys.modules:
        importlib.reload(sys.modules[URLCONF])
    else:  # pragma: no cover - only if nothing has resolved a URL yet
        importlib.import_module(URLCONF)
    clear_url_caches()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_definition(base="order", columns=("order.code",)):
    return {
        "schema_version": contracts.SCHEMA_VERSION,
        "base": base,
        "columns": [{"field": key} for key in columns],
        "sorting": [],
        "options": {
            "include_canceled_positions": False,
            "include_testmode_orders": False,
            "row_limit": None,
        },
    }


def form_data(**kwargs):
    data = {
        "name": "Attendee list",
        "description": "",
        "identifier": "",
        "base": "order",
        "definition": json.dumps(make_definition()),
    }
    data.update(kwargs)
    return data


def limited_user(organizer, email, permissions):
    user = User.objects.create_user(email, PASSWORD)
    team = Team.objects.create(
        organizer=organizer,
        name=email,
        all_events=True,
        all_event_permissions=False,
        limit_event_permissions={p: True for p in permissions},
    )
    team.members.add(user)
    return user


@pytest.fixture
def client_view_only(client, organizer):
    """May read reports (``event.orders:read``) but not change them."""
    user = limited_user(organizer, "view-only@example.org", [VIEW_PERMISSION])
    client.login(email=user.email, password=PASSWORD)
    return client


@pytest.fixture
def client_change_only(client, organizer):
    """May change event settings but may not see orders -- and thus no reports."""
    user = limited_user(organizer, "change-only@example.org", [CHANGE_PERMISSION])
    client.login(email=user.email, password=PASSWORD)
    return client


@pytest.fixture
def saved_report(event, organizer):
    with scope(organizer=organizer):
        return ReportDefinition.objects.create(
            event=event,
            name="Attendee list",
            identifier="attendees",
            definition=make_definition(),
        )


def url(name, event, **kwargs):
    return reverse(
        f"{URL_NAMESPACE}:{name}",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            **kwargs,
        },
    )


def all_urls(event, report):
    """(url, http method) for every route of this module."""
    return [
        (url("event.reports", event), "get"),
        (url("event.reports.add", event), "get"),
        (url("event.reports.edit", event, report=report.pk), "get"),
        (url("event.reports.duplicate", event, report=report.pk), "post"),
        (url("event.reports.delete", event, report=report.pk), "get"),
    ]


# ---------------------------------------------------------------------------
# Permission strings
# ---------------------------------------------------------------------------


def test_permission_strings_exist_in_pretix():
    """A typo here is a hard error at URLconf import time, not at request time."""
    from pretix.base.permissions import (
        get_all_event_permissions,
        get_all_organizer_permissions,
    )

    from pretix_custom_reports.views.crud import ORGANIZER_CHANGE_PERMISSION

    assert VIEW_PERMISSION in get_all_event_permissions()
    assert CHANGE_PERMISSION in get_all_event_permissions()
    assert ORGANIZER_CHANGE_PERMISSION in get_all_organizer_permissions()


def test_view_permission_matches_the_navigation_entry():
    """The menu entry and the list view must agree, or the menu leads to a 403."""
    from pretix_custom_reports.signals import VIEW_PERMISSION as NAV_PERMISSION

    assert VIEW_PERMISSION == NAV_PERMISSION


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_list_shows_reports(client_with_perms, event, saved_report):
    response = client_with_perms.get(url("event.reports", event))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Attendee list" in content
    assert "attendees" in content


def test_list_is_empty_without_reports(client_with_perms, event):
    response = client_with_perms.get(url("event.reports", event))
    assert response.status_code == 200
    assert "have not created any reports yet" in response.content.decode()


def test_list_hides_reports_of_other_events(
    client_with_perms, event, event_without_plugin, organizer
):
    with scope(organizer=organizer):
        ReportDefinition.objects.create(
            event=event_without_plugin,
            name="Foreign report",
            definition=make_definition(),
        )
    response = client_with_perms.get(url("event.reports", event))
    assert "Foreign report" not in response.content.decode()


def test_create(client_with_perms, event, organizer, user_with_perms):
    response = client_with_perms.post(
        url("event.reports.add", event), form_data(), follow=True
    )
    assert response.status_code == 200
    with scope(organizer=organizer):
        report = ReportDefinition.objects.for_event(event).get()
        assert report.name == "Attendee list"
        assert report.base == "order"
        assert report.identifier  # generated
        assert report.created_by == user_with_perms
        assert report.definition == make_definition()
        entry = LogEntry.objects.get(
            content_type=report.logs_content_type, object_id=report.pk
        )
        assert entry.action_type == contracts.LOG_ACTION_ADDED
        assert entry.user == user_with_perms


def test_create_form_renders(client_with_perms, event):
    response = client_with_perms.get(url("event.reports.add", event))
    assert response.status_code == 200
    content = response.content.decode()
    # The prefilled starting definition must be valid, not the empty skeleton.
    assert "schema_version" in content
    assert "order.code" in content


@pytest.mark.parametrize(
    "definition,expected_field",
    [
        ("{not json", "definition"),
        ('{"schema_version": 1, "base": "order", "columns": []}', "definition"),
        (
            json.dumps(make_definition(columns=("order.event__organizer__slug",))),
            "definition",
        ),
        (json.dumps(make_definition(base="orderposition")), "base"),
    ],
)
def test_create_rejects_broken_input(
    client_with_perms, event, organizer, definition, expected_field
):
    response = client_with_perms.post(
        url("event.reports.add", event), form_data(definition=definition)
    )
    assert response.status_code == 200
    assert response.context["form"].errors.get(expected_field)
    with scope(organizer=organizer):
        assert not ReportDefinition.objects.for_event(event).exists()


def test_change(client_with_perms, event, organizer, saved_report, user_with_perms):
    response = client_with_perms.post(
        url("event.reports.edit", event, report=saved_report.pk),
        form_data(name="Renamed", identifier="attendees"),
        follow=True,
    )
    assert response.status_code == 200
    with scope(organizer=organizer):
        saved_report.refresh_from_db()
        assert saved_report.name == "Renamed"
        entry = LogEntry.objects.get(
            content_type=saved_report.logs_content_type,
            object_id=saved_report.pk,
            action_type=contracts.LOG_ACTION_CHANGED,
        )
        assert entry.parsed_data["changed_fields"] == ["name"]


def test_change_form_shows_the_stored_definition(
    client_with_perms, event, saved_report
):
    response = client_with_perms.get(
        url("event.reports.edit", event, report=saved_report.pk)
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "attendees" in content
    # Indented, not a single line: an unreadable textarea is an unusable one.
    assert "&quot;order.code&quot;" in content


def test_duplicate(client_with_perms, event, organizer, saved_report):
    response = client_with_perms.post(
        url("event.reports.duplicate", event, report=saved_report.pk)
    )
    assert response.status_code == 302
    with scope(organizer=organizer):
        copy = (
            ReportDefinition.objects.for_event(event).exclude(pk=saved_report.pk).get()
        )
        assert copy.identifier == "attendees-2"
        assert copy.definition == saved_report.definition
        assert response["Location"].endswith(f"/{copy.pk}/")
        assert LogEntry.objects.filter(
            object_id=copy.pk, action_type=contracts.LOG_ACTION_ADDED
        ).exists()


def test_duplicate_rejects_get(client_with_perms, event, saved_report):
    response = client_with_perms.get(
        url("event.reports.duplicate", event, report=saved_report.pk)
    )
    assert response.status_code == 405


def test_delete(client_with_perms, event, organizer, saved_report):
    confirm = client_with_perms.get(
        url("event.reports.delete", event, report=saved_report.pk)
    )
    assert confirm.status_code == 200
    assert "Attendee list" in confirm.content.decode()

    response = client_with_perms.post(
        url("event.reports.delete", event, report=saved_report.pk), {}
    )
    assert response.status_code == 302
    with scope(organizer=organizer):
        assert not ReportDefinition.objects.filter(pk=saved_report.pk).exists()
    assert LogEntry.objects.filter(
        object_id=saved_report.pk, action_type=contracts.LOG_ACTION_DELETED
    ).exists()


# ---------------------------------------------------------------------------
# Denials
# ---------------------------------------------------------------------------


def test_every_view_denies_a_user_with_an_unrelated_permission(
    client_without_perms, event, saved_report
):
    """The user can open the event in the backend but holds only event.items:write."""
    for target, method in all_urls(event, saved_report):
        response = getattr(client_without_perms, method)(target)
        assert response.status_code == 403, target


def test_read_only_user_may_list_but_not_change(
    client_view_only, event, organizer, saved_report
):
    assert client_view_only.get(url("event.reports", event)).status_code == 200

    denied = [
        (url("event.reports.add", event), "get"),
        (url("event.reports.add", event), "post"),
        (url("event.reports.edit", event, report=saved_report.pk), "get"),
        (url("event.reports.edit", event, report=saved_report.pk), "post"),
        (url("event.reports.duplicate", event, report=saved_report.pk), "post"),
        (url("event.reports.delete", event, report=saved_report.pk), "get"),
        (url("event.reports.delete", event, report=saved_report.pk), "post"),
    ]
    for target, method in denied:
        response = getattr(client_view_only, method)(target, form_data())
        assert response.status_code == 403, f"{method} {target}"

    with scope(organizer=organizer):
        assert ReportDefinition.objects.for_event(event).count() == 1


def test_read_only_user_sees_no_write_buttons(client_view_only, event, saved_report):
    content = client_view_only.get(url("event.reports", event)).content.decode()
    assert "Attendee list" in content
    assert "/delete" not in content
    assert "/duplicate" not in content


def test_change_only_user_cannot_list(client_change_only, event, saved_report):
    """Reading is gated on event.orders:read, deliberately not on settings write."""
    response = client_change_only.get(url("event.reports", event))
    assert response.status_code == 403


def test_anonymous_user_is_sent_to_the_login(client, event, saved_report):
    for target, method in all_urls(event, saved_report):
        response = getattr(client, method)(target)
        assert response.status_code == 302, target
        assert "/control/login" in response["Location"]


def test_report_of_another_event_is_not_reachable(
    client_with_perms, event, event_without_plugin, organizer
):
    """Same organizer, same user, other event: the URL must 404, not 200."""
    with scope(organizer=organizer):
        foreign = ReportDefinition.objects.create(
            event=event_without_plugin,
            name="Foreign report",
            definition=make_definition(),
        )
    for target, method in [
        (url("event.reports.edit", event, report=foreign.pk), "get"),
        (url("event.reports.edit", event, report=foreign.pk), "post"),
        (url("event.reports.duplicate", event, report=foreign.pk), "post"),
        (url("event.reports.delete", event, report=foreign.pk), "get"),
        (url("event.reports.delete", event, report=foreign.pk), "post"),
    ]:
        response = getattr(client_with_perms, method)(target, form_data())
        assert response.status_code == 404, f"{method} {target}"
    with scope(organizer=organizer):
        assert ReportDefinition.objects.filter(pk=foreign.pk).exists()


def test_organizer_template_is_not_editable_through_an_event_url(
    client_with_perms, event, organizer
):
    """Templates are portability-dev's territory; the event CRUD must not touch them."""
    with scope(organizer=organizer):
        template = ReportDefinition.objects.create(
            organizer=organizer, name="Template", definition=make_definition()
        )
    response = client_with_perms.get(
        url("event.reports.edit", event, report=template.pk)
    )
    assert response.status_code == 404


def test_user_of_another_organizer_gets_nothing(client, event, saved_report):
    """No team for our organizer: pretix 404s the event before we are asked."""
    other = Organizer.objects.create(name="Other", slug="other-org")
    user = limited_user(other, "outsider@example.org", [VIEW_PERMISSION])
    client.login(email=user.email, password=PASSWORD)
    for target, method in all_urls(event, saved_report):
        response = getattr(client, method)(target)
        assert response.status_code == 404, target
