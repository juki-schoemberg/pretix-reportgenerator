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
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from pretix.control.signals import nav_event

#: Permission required to see and open saved reports.
#: Creating/changing/deleting is a stricter key and is decided by persistence-dev.
VIEW_PERMISSION = "event.orders:read"

#: URL namespace pretix builds for this plugin: "plugins:" + AppConfig.label
#: (pretix/multidomain/maindomain_urlconf.py).
URL_NAMESPACE = "plugins:pretix_custom_reports"


@receiver(nav_event, dispatch_uid="pretix_custom_reports_nav_event")
def navbar_event_entry(sender, request, **kwargs):
    """Add the event-level "Exports" entry to the control panel navigation."""
    if not request.user.has_event_permission(
        request.organizer, request.event, VIEW_PERMISSION, request=request
    ):
        return []
    url = request.resolver_match
    return [
        {
            "label": _("Exports"),
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


# ---------------------------------------------------------------------------
# Placeholders for later waves. Do not implement anything here in wave 0a --
# that would pre-empt the contracts frozen in wave 0c.
#
# wave 2  exporter-dev     pretix.base.signals.register_data_exporters
# wave 2  exporter-dev     pretix.base.signals.register_multievent_data_exporters
# wave 2  portability-dev  pretix.control.signals.nav_organizer
# wave 2  portability-dev  pretix.base.signals.event_copy_data
# wave 1  registry-dev     own EventPluginSignal register_report_fields
#                          (declared in contracts/, see docs/extending.md)
# ---------------------------------------------------------------------------
