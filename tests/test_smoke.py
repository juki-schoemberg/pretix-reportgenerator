# Owner from wave 1 on: integrator (see ORCHESTRIERUNG.md section 5)
#
# Walking-skeleton smoke tests: entry point, app config, navigation signal, URL
# routing and permission gate. These must keep passing through every wave; they
# are the cheapest early warning that the plugin no longer loads or that the menu
# entry leaks to users without permission.
"""Smoke tests for the pretix-custom-reports walking skeleton."""

import importlib.metadata
import pytest
from django.apps import apps
from django.urls import reverse
from pretix.base.plugins import get_all_plugins

import pretix_custom_reports
from pretix_custom_reports.signals import URL_NAMESPACE, VIEW_PERMISSION

MODULE_NAME = "pretix_custom_reports"
NAV_LABEL = "Exports"


def test_module_imports():
    assert pretix_custom_reports.__version__


def test_entry_point_registered():
    """``pip install -e .`` must expose us in the pretix.plugin group.

    pretix only evaluates the module part of the entry point
    (pretix/settings.py), so that is what we assert on.
    """
    modules = {
        ep.module for ep in importlib.metadata.entry_points(group="pretix.plugin")
    }
    assert MODULE_NAME in modules


def test_app_config_is_loaded():
    app = apps.get_app_config(MODULE_NAME)
    assert hasattr(app, "PretixPluginMeta")
    assert app.label == MODULE_NAME
    # The URL namespace pretix builds is "plugins:" + app.label.
    assert URL_NAMESPACE == f"plugins:{app.label}"


def test_plugin_meta_is_complete():
    meta = apps.get_app_config(MODULE_NAME).PretixPluginMeta
    assert str(meta.name)
    assert str(meta.description)
    assert meta.version == pretix_custom_reports.__version__
    assert meta.category == "FORMAT"
    assert meta.visible is True
    assert meta.navigation_links


def test_compatibility_matches_installed_pretix():
    """The pin must be exact and must match the installed pretix.

    A mismatch makes pretix call sys.exit(1) at startup, so a wrong pin
    does not fail gracefully.
    """
    meta = apps.get_app_config(MODULE_NAME).PretixPluginMeta
    installed = importlib.metadata.version("pretix")
    assert meta.compatibility == f"pretix=={installed}"


def test_plugin_appears_in_plugin_list(event):
    """The plugin must be offered in the event's plugin settings."""
    names = {p.module for p in get_all_plugins(event=event)}
    assert MODULE_NAME in names


def test_navigation_link_target_reverses(event):
    """PretixPluginMeta.navigation_links entries must resolve.

    pretix re-raises NoReverseMatch for event-level plugins, which breaks
    the plugin settings page.
    """
    meta = apps.get_app_config(MODULE_NAME).PretixPluginMeta
    for _label, urlname, kwargs in meta.navigation_links:
        assert reverse(
            urlname,
            kwargs={
                "organizer": event.organizer.slug,
                "event": event.slug,
                **kwargs,
            },
        )


@pytest.mark.django_db
def test_placeholder_page_opens(client_with_perms, event):
    url = reverse(
        f"{URL_NAMESPACE}:event.index",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    resp = client_with_perms.get(url)
    assert resp.status_code == 200
    assert "customreports-placeholder" in resp.content.decode()


@pytest.mark.django_db
def test_placeholder_page_denied_without_permission(client_without_perms, event):
    url = reverse(
        f"{URL_NAMESPACE}:event.index",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    resp = client_without_perms.get(url)
    assert resp.status_code == 403


@pytest.mark.django_db
def test_nav_entry_visible_with_permission(client_with_perms, event):
    resp = client_with_perms.get(f"/control/event/{event.organizer.slug}/{event.slug}/")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert f"/{event.slug}/customreports/" in content
    assert NAV_LABEL in content


@pytest.mark.django_db
def test_nav_entry_hidden_without_permission(client_without_perms, event):
    """The menu entry must not show up for a user lacking VIEW_PERMISSION.

    The user does have another event permission, so the page itself loads
    -- that is what makes this a real test of hiding rather than of a 403.
    """
    resp = client_without_perms.get(
        f"/control/event/{event.organizer.slug}/{event.slug}/items/"
    )
    assert resp.status_code == 200
    assert "customreports/" not in resp.content.decode()


@pytest.mark.django_db
def test_nav_entry_hidden_when_plugin_disabled(client_with_perms, event_without_plugin):
    """nav_event is an EventPluginSignal, so a disabled plugin must stay silent."""
    resp = client_with_perms.get(
        f"/control/event/{event_without_plugin.organizer.slug}/"
        f"{event_without_plugin.slug}/"
    )
    assert resp.status_code == 200
    assert "customreports/" not in resp.content.decode()


def test_view_permission_is_a_known_pretix_permission():
    from pretix.base.permissions import get_all_event_permissions

    assert VIEW_PERMISSION in get_all_event_permissions()


def test_no_migration_created_yet():
    """No migration may ship yet; migrations belong to persistence-dev."""
    import pathlib

    migrations = pathlib.Path(pretix_custom_reports.__file__).parent / "migrations"
    assert migrations.is_dir()
    assert not [p for p in migrations.glob("0*.py")]
