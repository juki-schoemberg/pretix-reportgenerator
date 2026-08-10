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
from pretix.base.signals import (
    event_copy_data,
    register_data_exporters,
    register_multievent_data_exporters,
)

import pretix_custom_reports
from pretix_custom_reports.signals import URL_NAMESPACE, VIEW_PERMISSION

MODULE_NAME = "pretix_custom_reports"
#: The event-level navigation label. Defined once here and asserted in both
#: directions (rendered for a permitted user, absent for a limited one). Same
#: string in ``signals.py::navbar_event_entry`` and
#: ``apps.py::PretixPluginMeta.navigation_links``.
NAV_LABEL = "Reports"


def _dispatch_uids(signal):
    """The dispatch_uids currently connected to *signal*.

    ``Signal.receivers`` holds ``(lookup_key, receiver, is_async)`` in Django
    5.2, and ``lookup_key`` is ``(dispatch_uid or id(receiver), id(sender))``.
    Only the receivers connected with an explicit ``dispatch_uid`` have a
    string there, and those are ours.
    """
    return {entry[0][0] for entry in signal.receivers if isinstance(entry[0][0], str)}


#: Snapshot of the receivers ``apps.ready()`` -> ``signals.py`` established,
#: taken at *collection* time, before any test has run.
#:
#: It has to be a snapshot: ``tests/test_exporters.py::registered`` connects the
#: same two dispatch_uids and calls ``disconnect(dispatch_uid=...)`` on teardown,
#: which removes the *production* connection for the rest of the session. That
#: is a defect in that fixture (reported to exporter-dev, see
#: handoff/status/integrator.md), but it must not make this file order
#: dependent.
CONNECTED_AT_IMPORT = {
    "register_data_exporters": _dispatch_uids(register_data_exporters),
    "register_multievent_data_exporters": _dispatch_uids(
        register_multievent_data_exporters
    ),
    "event_copy_data": _dispatch_uids(event_copy_data),
}


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
def test_event_index_opens_the_report_list(client_with_perms, event):
    """``event.index`` is the report list since wave 4.

    Wave 0a put a placeholder behind the menu entry; the integrator repointed
    the name at ``views/crud.py::ReportListView`` when wiring up urls.py, see
    handoff/status/integrator.md.
    """
    url = reverse(
        f"{URL_NAMESPACE}:event.index",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    resp = client_with_perms.get(url)
    assert resp.status_code == 200
    assert "You have not created any reports yet." in resp.content.decode()


@pytest.mark.django_db
def test_event_index_denied_without_permission(client_without_perms, event):
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


# ---------------------------------------------------------------------------
# The wiring itself (wave 4, integrator)
# ---------------------------------------------------------------------------


def _plugin_routes():
    from pretix_custom_reports import urls

    return [(p.name, p.pattern.regex.pattern) for p in urls.urlpatterns]


def test_every_route_list_of_every_agent_is_wired_up():
    """urls.py concatenates seven module variables; none may go missing.

    Each agent maintains their routes next to their views (see
    docs/adr/0006-verdrahtung.md section 1). A dropped ``+`` in urls.py would
    otherwise only show up in that agent's own tests -- or, for
    ``template_editor_urlpatterns``, as a ``NoReverseMatch`` on the template
    list, which links to those two names without a fallback.
    """
    from pretix_custom_reports.views.api import api_urlpatterns
    from pretix_custom_reports.views.crud import event_urlpatterns
    from pretix_custom_reports.views.editor import (
        editor_urlpatterns,
        template_editor_urlpatterns,
    )
    from pretix_custom_reports.views.portability import (
        portability_event_urlpatterns,
    )
    from pretix_custom_reports.views.templates import (
        templates_event_urlpatterns,
        templates_organizer_urlpatterns,
    )

    wired = {name for name, _pattern in _plugin_routes()}
    for source in (
        event_urlpatterns,
        editor_urlpatterns,
        api_urlpatterns,
        portability_event_urlpatterns,
        templates_event_urlpatterns,
        templates_organizer_urlpatterns,
        template_editor_urlpatterns,
    ):
        assert {p.name for p in source} <= wired


def test_no_route_name_and_no_url_pattern_is_used_twice():
    names = [name for name, _pattern in _plugin_routes()]
    patterns = [pattern for _name, pattern in _plugin_routes()]
    assert len(set(names)) == len(names)
    assert len(set(patterns)) == len(patterns)


def test_every_route_reverses_and_resolves_back_to_itself():
    """No route may be shadowed by an earlier, more general one.

    Six route lists from four agents are concatenated; the claim that their
    prefixes do not overlap is checked here rather than believed.
    """
    from django.urls import resolve

    extra = {"report": 1, "template": 2, "identifier": "abc"}
    for name, pattern in _plugin_routes():
        if "organizer/" in pattern and "event/" not in pattern:
            kwargs = {"organizer": "o"}
        else:
            kwargs = {"organizer": "o", "event": "e"}
        kwargs.update({k: v for k, v in extra.items() if f"<{k}>" in pattern})
        url = reverse(f"{URL_NAMESPACE}:{name}", kwargs=kwargs)
        assert resolve(url).url_name == name, url


def test_the_registry_cache_receivers_are_connected_by_importing_signals():
    """signals.py must import registry.cache, or invalidation is late.

    Without that import the post_save/post_delete receivers are only connected
    once something touches the registry, and a question renamed before that
    invalidates nothing. See docs/adr/0002-registry.md section 7.
    """
    from django.db.models.signals import post_save
    from pretix.base.models import Question

    receivers = [str(r[0][0]) for r in post_save.receivers]
    assert any("pretix_custom_reports" in r for r in receivers), receivers
    assert Question  # the model the receiver is attached to still exists


def test_the_seven_log_action_types_are_registered():
    """An unregistered action type shows an empty line in the log viewer."""
    from pretix.base.logentrytypes import log_entry_types

    from pretix_custom_reports import contracts

    for action in (
        contracts.LOG_ACTION_ADDED,
        contracts.LOG_ACTION_CHANGED,
        contracts.LOG_ACTION_DELETED,
        contracts.LOG_ACTION_EXECUTED,
        contracts.LOG_ACTION_EXPORTED,
        contracts.LOG_ACTION_IMPORTED,
        contracts.LOG_ACTION_TEMPLATE_APPLIED,
    ):
        entry, meta = log_entry_types.get(action_type=action)
        assert entry is not None, action
        assert str(entry.display(None, {}))


def test_both_exporters_are_connected():
    """Without these two the exporter exists but is invisible in production.

    It appears in neither the event's nor the organizer's export UI, and no
    scheduled export can be created for it.
    """
    assert (
        "pretix_custom_reports_exporter"
        in CONNECTED_AT_IMPORT["register_data_exporters"]
    )
    assert (
        "pretix_custom_reports_multiexporter"
        in CONNECTED_AT_IMPORT["register_multievent_data_exporters"]
    )


def test_the_event_copy_receiver_is_connected():
    assert "pretix_custom_reports_copy_data" in CONNECTED_AT_IMPORT["event_copy_data"]


@pytest.mark.django_db
def test_the_log_object_link_survives_an_organizer_template(organizer, event):
    """An organizer template has ``event=None`` and must not break the log page.

    ``EventLogEntryType.get_object_link_info`` reverses with
    ``logentry.event.slug``; for a template that attribute is ``None`` and the
    inherited implementation would raise while *rendering the log page*. See
    docs/adr/0006-verdrahtung.md section 5.

    Also pins the *target*: both links lead into the graphical editor, not into
    the raw JSON form -- same fix as the report list got in commit 3a56a0a. The
    two addressing schemes are deliberate: the event editor takes the stable
    identifier, the template editor the primary key.
    """
    from django_scopes import scopes_disabled
    from pretix.base.logentrytypes import log_entry_types

    from pretix_custom_reports import contracts
    from pretix_custom_reports.models import ReportDefinition

    logtype, _meta = log_entry_types.get(action_type=contracts.LOG_ACTION_ADDED)
    document = {
        "schema_version": contracts.SCHEMA_VERSION,
        "base": "order",
        "columns": [{"field": "order.code"}],
    }

    with scopes_disabled():
        template = ReportDefinition.objects.create(
            organizer=organizer,
            name="A template",
            identifier="tmpl",
            definition=document,
        )
        report = ReportDefinition.objects.create(
            event=event, name="A report", identifier="rep", definition=document
        )
        template_entry = template.log_added()
        report_entry = report.log_added()

    template_link = str(logtype.get_object_link(template_entry))
    report_link = str(logtype.get_object_link(report_entry))

    assert "A template" in template_link
    assert f"/customreports/templates/editor/{template.pk}/" in template_link
    assert "A report" in report_link
    assert "/customreports/editor/rep/" in report_link


def test_exactly_one_migration_ships():
    """Migrations belong to persistence-dev, and there must be no duplicates.

    Replaces the wave-0a gate ``test_no_migration_created_yet``, which was
    green exactly as long as no migration existed. Since wave 1 there is
    ``0001_initial.py`` and the gate has done its job, see
    handoff/requests/persistence-dev-an-integrator-urls.md section 2.
    """
    import pathlib

    migrations = pathlib.Path(pretix_custom_reports.__file__).parent / "migrations"
    numbered = sorted(p.name for p in migrations.glob("0*.py"))
    assert numbered == ["0001_initial.py"]
