# Owner from wave 1 on: integrator (see ORCHESTRIERUNG.md section 5)
#
# Every agent needs receivers in here, therefore this file is deliberately
# serial. Other agents put their copy-ready lines into handoff/requests/ instead
# of editing this file (ORCHESTRIERUNG.md section 5).
#
# Verified against pretix 2026.6.0:
#   * nav_event lives in pretix.control.signals and is an EventPluginSignal, so
#     it only fires for events that have the plugin enabled.
#   * Receiver shape copied from pretix/plugins/webcheckin/signals.py.
#   * "event.orders:read" is the new-style key of the legacy "can_view_orders"
#     (pretix/helpers/permission_migration.py) and the key pretix core itself
#     uses for its export menu entry (pretix/control/navigation.py).
from typing import Optional

from django.dispatch import receiver
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _
from pretix.base.logentrytypes import EventLogEntryType, log_entry_types
from pretix.base.signals import (
    event_copy_data,
    register_data_exporters,
    register_multievent_data_exporters,
)
from pretix.control.signals import nav_event, nav_organizer

from pretix_custom_reports import contracts
from pretix_custom_reports.exporters import (
    register_multievent_report_exporter,
    register_report_exporter,
)
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.portability.templating import ORGANIZER_CHANGE_PERMISSION

# Connects the registry's cache invalidation receivers (post_save/post_delete on
# Question, Item, ItemCategory, SubEvent, Discount, EventMetaProperty). They are
# wired up on import; without this line they would only be connected once
# something happens to touch the registry, and a question renamed before that
# would not invalidate anything. See docs/adr/0002-registry.md section 7.
from pretix_custom_reports.registry import cache as registry_cache  # noqa: F401

#: Permission required to see and open saved reports.
#: Creating/changing/deleting is a stricter key and is decided by persistence-dev.
VIEW_PERMISSION = "event.orders:read"

#: URL namespace pretix builds for this plugin: "plugins:" + AppConfig.label
#: (pretix/multidomain/maindomain_urlconf.py).
URL_NAMESPACE = "plugins:pretix_custom_reports"

#: Django app label of this plugin, used for the "is the plugin on?" question on
#: organizer level, where pretix has no direct answer (plugins are per event).
PLUGIN_MODULE = "pretix_custom_reports"


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


@receiver(nav_event, dispatch_uid="pretix_custom_reports_nav_event")
def navbar_event_entry(sender, request, **kwargs):
    """Add the event-level "Reports" entry to the control panel navigation.

    The label was "Exports" until wave 4 follow-up. It is "Reports" now for two
    reasons: the plugin is a report builder, not another exporter (see
    ``PluginApp.PretixPluginMeta.description``), and the organizer-level entry
    is called "Report templates" -- sharing the word "Report" is what tells a
    user that the two menu entries belong together. Three places carry the same
    string and have to stay in sync: here, ``apps.py::navigation_links`` and
    ``tests/test_smoke.py::NAV_LABEL``.
    """
    if not request.user.has_event_permission(
        request.organizer, request.event, VIEW_PERMISSION, request=request
    ):
        return []
    url = request.resolver_match
    return [
        {
            "label": _("Reports"),
            "url": reverse(
                f"{URL_NAMESPACE}:event.index",
                kwargs={
                    "organizer": request.organizer.slug,
                    "event": request.event.slug,
                },
            ),
            "icon": "table",
            "active": url.namespace == URL_NAMESPACE,
        }
    ]


@receiver(nav_organizer, dispatch_uid="pretix_custom_reports_nav_organizer")
def navbar_organizer_entry(sender, request, organizer, **kwargs):
    """Add the organizer-level "Report templates" entry.

    From handoff/requests/portability-dev-an-integrator-urls.md section 4. Two
    things verified before taking it over (docs/pretix-api-notes.md section
    3.3, stumbling block 3 and section 3, stumbling block 1):

    1. A navigation receiver must always return a *list*, never ``None`` --
       pretix/control/navigation.py feeds the result into ``list()``.
    2. ``nav_organizer`` is an ``OrganizerPluginSignal`` and we are an
       event-level plugin, so ``@receiver`` takes the legacy path and emits a
       ``DeprecationWarning`` at import time. That is expected and shared with
       pretix' own stripe plugin; see the module docstring of exporters.py.

    Added on top of the proposal: the same "is the plugin on anywhere in this
    organizer?" gate the view itself uses
    (``views/templates.py::OrganizerPluginActiveMixin``). Because of (2) this
    receiver runs for *every* organizer, and without the gate the menu entry
    would link to a guaranteed 404 for everybody who does not use this plugin.
    """
    if not request.user.has_organizer_permission(
        organizer, ORGANIZER_CHANGE_PERMISSION, request=request
    ):
        return []
    if not organizer.events.filter(plugins__contains=PLUGIN_MODULE).exists():
        return []
    url = reverse(
        f"{URL_NAMESPACE}:organizer.templates",
        kwargs={"organizer": organizer.slug},
    )
    return [
        {
            "label": _("Report templates"),
            "url": url,
            "active": request.path.startswith(url),
            "icon": "table",
        }
    ]


# ---------------------------------------------------------------------------
# Exporters (exporter-dev, wave 2)
# ---------------------------------------------------------------------------

# Saved reports as regular pretix exports. Registering them here is what makes
# them schedulable: pretix' scheduled exports are bound to a registered
# exporter identifier, so we need no scheduler of our own (CLAUDE.md rule 5).
#
# The receivers live in exporters.py rather than being defined here, so that
# there is exactly one definition and the tests connect the same objects the
# production code does. EventPluginSignal.connect resolves the owning app from
# the receiver's __module__ (pretix/base/signals.py:64-88), and both modules
# belong to this plugin, so either location works.
#
# register_multievent_data_exporters is an OrganizerPluginSignal and we are an
# event-level plugin: connecting emits a DeprecationWarning on purpose, see
# handoff/requests/exporter-dev-an-integrator-signals.md section 2. Do not
# "fix" it -- switching to PLUGIN_LEVEL_EVENT_ORGANIZER_HYBRID is a decision
# about the character of the plugin and needs an ADR.
register_data_exporters.connect(
    register_report_exporter,
    dispatch_uid="pretix_custom_reports_exporter",
)
register_multievent_data_exporters.connect(
    register_multievent_report_exporter,
    dispatch_uid="pretix_custom_reports_multiexporter",
)


# ---------------------------------------------------------------------------
# Event copy (portability-dev, wave 2)
# ---------------------------------------------------------------------------


@receiver(event_copy_data, dispatch_uid="pretix_custom_reports_copy_data")
def copy_reports(sender, other, question_map=None, **kwargs):
    """Take the saved reports along when an event is copied (SPEC.md F10).

    ``sender`` is the NEW event, ``other`` the one being copied from
    (docs/pretix-api-notes.md section 3.3). ``**kwargs`` is mandatory: the
    signal sends seven ``*_map`` arguments and a receiver without it would die
    with a ``TypeError`` as soon as pretix adds another one.
    """
    from pretix_custom_reports.portability.eventcopy import copy_reports_to_event

    copy_reports_to_event(sender, other, question_map=question_map)


# ---------------------------------------------------------------------------
# Log entry display
# ---------------------------------------------------------------------------


@log_entry_types.new_from_dict(
    {
        contracts.LOG_ACTION_ADDED: _("The report has been created."),
        contracts.LOG_ACTION_CHANGED: _("The report has been changed."),
        contracts.LOG_ACTION_DELETED: _("The report has been deleted."),
        contracts.LOG_ACTION_EXECUTED: _("The report has been run."),
        contracts.LOG_ACTION_EXPORTED: _("The report has been exported."),
        contracts.LOG_ACTION_IMPORTED: _("The report has been imported."),
        contracts.LOG_ACTION_TEMPLATE_APPLIED: _("A report template has been applied."),
    }
)
class ReportLogEntryType(EventLogEntryType):
    """Human readable text and object link for our seven log actions.

    Without a registered type the log viewer shows an *empty* line rather than
    an error (docs/pretix-api-notes.md section 9), so this is what makes the
    audit trail readable at all.

    ``get_object_link_info`` is overridden instead of using
    ``object_link_viewname``/``object_link_argname``: the inherited
    implementation reverses with ``logentry.event.slug``, and an organizer
    template has no event at all (``ReportDefinition.event`` is ``None``, the
    XOR with ``organizer``). It would raise ``AttributeError`` while *rendering
    the log page* -- the pitfall named in
    handoff/requests/erledigt/persistence-dev-an-integrator-urls.md section 4.

    Both branches link into the *graphical editor*, not into the raw JSON form
    -- same reasoning as the report list (commit 3a56a0a): the editor is the
    place users are meant to work in, the form is the fallback. Note the two
    addressing schemes, they are deliberate and documented in
    handoff/requests/erledigt/frontend-dev-an-integrator-template-editor-urls.md
    section 2: the event-level ``editor.edit`` takes the stable ``identifier``, the
    organizer-level ``organizer.templates.editor.edit`` takes the primary key,
    because it sits next to ``organizer.templates.edit``/``.export``.

    No shredder mixin on purpose: ``shred_pii`` is not called anywhere in
    pretix 2026.6.0, and pretix' own ``CoreEventLogEntryType`` does not declare
    one either. If data shredding ever reaches log entries, this is the place.
    """

    content_type = ReportDefinition
    object_link_wrapper = _("Report {val}")

    def get_object_link_info(self, logentry) -> Optional[dict]:
        report = logentry.content_object
        if report is None:
            # Deleted rows keep their log entry; pretix shows "(deleted)".
            return {"val": _("(deleted)")} if logentry.content_type_id else None
        if not isinstance(report, ReportDefinition):
            return None

        try:
            if report.is_template:
                if report.organizer_id is None:
                    return {"val": str(report)}
                href = reverse(
                    f"{URL_NAMESPACE}:organizer.templates.editor.edit",
                    kwargs={
                        "organizer": report.organizer.slug,
                        "template": report.pk,
                    },
                )
            else:
                href = reverse(
                    f"{URL_NAMESPACE}:editor.edit",
                    kwargs={
                        "organizer": report.event.organizer.slug,
                        "event": report.event.slug,
                        "identifier": report.identifier,
                    },
                )
        except NoReverseMatch:  # pragma: no cover -- URLs are wired in urls.py
            return {"val": str(report)}
        return {"href": href, "val": str(report)}
