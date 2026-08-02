# Owner from wave 1 on: test-engineer (see ORCHESTRIERUNG.md section 5)
#
# Base fixtures for all agents. Anything more specific belongs into the agent's
# own test module (or, for shared factories, into tests/factories.py, which is
# owned by test-engineer).
#
# Two pretix specifics that are easy to get wrong and are therefore handled here
# once, verified against pretix 2026.6.0:
#
# 1. django-scopes. pretix's own tests/conftest.py wraps every non-generator
#    fixture in ``scopes_disabled()`` via a pytest hook. That hook is NOT active
#    for out-of-tree plugins, so scoped models (Event and everything below it)
#    must be created inside an explicit ``scopes_disabled()`` block.
# 2. Permissions. pretix derives backend access from Team objects, not from
#    ``is_staff``. In 2026.6.0 the permission keys are the colon form and
#    ``Team.limit_event_permissions`` is a JSONField shaped ``{key: True}``
#    (pretix/base/models/organizer.py), not a list.
"""Shared pytest fixtures for pretix-custom-reports."""

import datetime
import importlib
import pytest
import sys
from django.urls import clear_url_caches
from django.utils.timezone import now
from django_scopes import scopes_disabled
from pretix.base.models import Event, Organizer, Team, User

#: Password used for every test user.
PASSWORD = "dummy"

#: The permission key our reports require for reading/running. Kept here so the
#: negative fixtures stay in sync with pretix_custom_reports.signals.
VIEW_PERMISSION = "event.orders:read"

#: A valid event permission that is explicitly NOT the one we require. Used to
#: build a user who can open the event in the backend but must not see our menu
#: entry. A user without any team would only prove a 404 on the event itself.
OTHER_PERMISSION = "event.items:write"


@pytest.fixture
def organizer(db):
    """Organizer that owns the test event."""
    return Organizer.objects.create(name="Dummy", slug="dummy")


@pytest.fixture
def event(organizer):
    """Event with this plugin enabled.

    ``plugins`` must contain the module name, otherwise the
    EventPluginSignal based navigation hook never fires.
    """
    with scopes_disabled():
        return Event.objects.create(
            organizer=organizer,
            name="Dummy Event",
            slug="dummy",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
            live=True,
        )


@pytest.fixture
def event_without_plugin(organizer):
    """Second event in the same organizer that does NOT have the plugin enabled."""
    with scopes_disabled():
        return Event.objects.create(
            organizer=organizer,
            name="Plain Event",
            slug="plain",
            date_from=now() + datetime.timedelta(days=30),
            plugins="",
            live=True,
        )


@pytest.fixture
def user_with_perms(organizer):
    """User who may view orders in every event of the organizer."""
    user = User.objects.create_user("with-perms@example.org", PASSWORD)
    team = Team.objects.create(
        organizer=organizer,
        name="Full access",
        all_events=True,
        all_event_permissions=True,
        all_organizer_permissions=True,
    )
    team.members.add(user)
    return user


@pytest.fixture
def user_without_perms(organizer):
    """User with backend access to the event but without ``event.orders:read``."""
    user = User.objects.create_user("without-perms@example.org", PASSWORD)
    team = Team.objects.create(
        organizer=organizer,
        name="Products only",
        all_events=True,
        all_event_permissions=False,
        limit_event_permissions={OTHER_PERMISSION: True},
    )
    team.members.add(user)
    return user


@pytest.fixture
def client_with_perms(client, user_with_perms):
    """Django test client logged in as :func:`user_with_perms`."""
    client.login(email=user_with_perms.email, password=PASSWORD)
    return client


@pytest.fixture
def client_without_perms(client, user_without_perms):
    """Django test client logged in as :func:`user_without_perms`."""
    client.login(email=user_without_perms.email, password=PASSWORD)
    return client


@pytest.fixture
def fixture_dir():
    """Path to ``tests/fixtures/`` (golden files are owned by contract-architect)."""
    import pathlib

    return pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Wave 3 additions (test-engineer)
# ---------------------------------------------------------------------------
#
# Everything below exists for the integration and performance tests. It is
# deliberately opt-in -- no autouse fixture -- so that adding it cannot change
# what any other agent's module does.

#: Django's root URLconf under the pretix test settings.
URLCONF = "pretix.multidomain.maindomain_urlconf"


def reload_urlconf():
    """Re-import the root URLconf and drop Django's reverse caches."""
    if URLCONF in sys.modules:
        importlib.reload(sys.modules[URLCONF])
    else:  # pragma: no cover - only if nothing has resolved a URL yet
        importlib.import_module(URLCONF)
    clear_url_caches()


@pytest.fixture(scope="module")
def wired_urls():
    """Attach **every** view module's routes to the plugin URLconf.

    ``urls.py`` belongs to the integrator and is wired up in wave 4
    (ORCHESTRIERUNG.md section 5), so until then the CRUD views, the editor, the
    JSON API, import/export and the organizer templates are only reachable
    through their ``*_urlpatterns`` module variables. ``tests/test_permissions.py``
    and ``tests/test_portability.py`` each attach their own subset; the
    end-to-end tests need all of them at once, because the whole point is that
    the path from the editor to another event's export runs through four
    different agents' views.

    Module-scoped: reloading the URLconf is not free and the routes are static.
    """
    from pretix_custom_reports import urls as plugin_urls
    from pretix_custom_reports.views.api import api_urlpatterns
    from pretix_custom_reports.views.crud import event_urlpatterns
    from pretix_custom_reports.views.editor import editor_urlpatterns
    from pretix_custom_reports.views.portability import portability_event_urlpatterns
    from pretix_custom_reports.views.templates import (
        templates_event_urlpatterns,
        templates_organizer_urlpatterns,
    )

    wanted = (
        list(event_urlpatterns)
        + list(editor_urlpatterns)
        + list(api_urlpatterns)
        + list(portability_event_urlpatterns)
        + list(templates_event_urlpatterns)
        + list(templates_organizer_urlpatterns)
    )
    known = {pattern.name for pattern in plugin_urls.urlpatterns}
    added = [pattern for pattern in wanted if pattern.name not in known]
    plugin_urls.urlpatterns.extend(added)
    reload_urlconf()
    yield
    for pattern in added:
        plugin_urls.urlpatterns.remove(pattern)
    reload_urlconf()
