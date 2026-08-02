# Owner from wave 1 on: persistence-dev (see ORCHESTRIERUNG.md section 5)
"""Plain CRUD views for saved reports: list, create, change, duplicate, delete.

Scope of this module
--------------------

This is the storage UI, not the report builder. The graphical editor and the
preview endpoints belong to ``frontend-dev`` (``views/editor.py``,
``views/api.py``), organizer templates and import/export belong to
``portability-dev`` (``views/templates.py``, ``views/portability.py``). Those
modules write through the same model and the same form, so the validation and
the audit log are shared.

Permissions (exact strings from docs/pretix-api-notes.md section 8.1)
--------------------------------------------------------------------

* reading and running a report: ``event.orders:read`` -- the same key
  ``BaseExporter.get_required_event_permission()`` defaults to, so a user who
  can see a report can also schedule an export of it
* creating, changing, deleting: ``event.settings.general:write``, the new-style
  spelling of the legacy ``can_change_event_settings``
  (pretix/helpers/permission_migration.py)

A wrong permission string is a hard error at URLconf import time
(``assert_valid_event_permission``), not at request time -- which is why they
are constants here and copied from the reference document rather than typed from
memory.

Every queryset goes through ``request.event.custom_reports``, so a report of
another event answers 404 even for a user who has permission in both events
(CLAUDE.md rule 4).

On top of that, every view here is behind the "is the plugin on for this event?"
gate (:class:`PluginActiveMixin`), the same one the other view modules use.
"""

from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect
from django.urls import re_path, reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, ListView, UpdateView, View
from pretix.control.permissions import EventPermissionRequiredMixin
from pretix.helpers.compat import CompatDeleteView

from pretix_custom_reports.forms import ReportDefinitionForm
from pretix_custom_reports.models import ReportDefinition

__all__ = [
    "CHANGE_PERMISSION",
    "ORGANIZER_CHANGE_PERMISSION",
    "EventReportMixin",
    "PluginActiveMixin",
    "ReportCreateView",
    "ReportDeleteView",
    "ReportDuplicateView",
    "ReportFormMixin",
    "ReportListView",
    "ReportUpdateView",
    "VIEW_PERMISSION",
    "event_urlpatterns",
    "report_url",
]

#: Read and run. Mirrors ``pretix_custom_reports.signals.VIEW_PERMISSION``.
VIEW_PERMISSION = "event.orders:read"

#: Create, change, delete on event level.
CHANGE_PERMISSION = "event.settings.general:write"

#: Organizer-level equivalent of :data:`CHANGE_PERMISSION`, for the template
#: views in wave 2. ``organizer_permission_required`` maps
#: ``event.settings.general:write`` onto this string anyway
#: (pretix/control/permissions.py:100-102); spelling it out is clearer.
ORGANIZER_CHANGE_PERMISSION = "organizer.settings.general:write"

URL_NAMESPACE = "plugins:pretix_custom_reports"
URL_NAME_LIST = "event.reports"
URL_NAME_ADD = "event.reports.add"
URL_NAME_EDIT = "event.reports.edit"
URL_NAME_DUPLICATE = "event.reports.duplicate"
URL_NAME_DELETE = "event.reports.delete"


def report_url(name: str, event, **kwargs) -> str:
    """Reverse one of this module's routes for *event*."""
    return reverse(
        f"{URL_NAMESPACE}:{name}",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            **kwargs,
        },
    )


class PluginActiveMixin:
    """404 unless this plugin is enabled for the event in the URL.

    Same reasoning as ``views/api.py`` and ``views/portability.py``: pretix only
    wraps a plugin's *presale* URLs with the "is the plugin on?" check
    (pretix/multidomain/plugin_handler.py, ``_event_view(require_plugin=...)``),
    control URLs are included at the URL root and stay reachable for an event
    that has the plugin switched off. SPEC.md F1 wants the opposite: switching
    the plugin off is the emergency brake, so neither the list nor any of the
    write forms may answer 200 afterwards.

    Deliberately duplicated rather than imported from ``views/api.py``, exactly
    as ``views/portability.py`` already does it: five lines are cheaper than a
    dependency between two agents' modules, and a divergence would show up in
    the tests of both. Owning the mixin in one shared place is an ownership
    change and belongs to the integrator (security review S-001).
    """

    plugin_module = "pretix_custom_reports"

    def dispatch(self, request, *args, **kwargs):
        event = getattr(request, "event", None)
        if event is None or self.plugin_module not in event.get_plugins():
            raise Http404("This plugin is not active for this event.")
        return super().dispatch(request, *args, **kwargs)


class EventReportMixin(PluginActiveMixin):
    """Shared queryset, 404 behaviour and template context."""

    model = ReportDefinition
    context_object_name = "report"

    def get_queryset(self):
        # Hard event scope. ``custom_reports`` is the related manager of the
        # scoped default manager, so this is also organizer scoped.
        return self.request.event.custom_reports.all()

    def get_object(self, queryset=None) -> ReportDefinition:
        try:
            return self.get_queryset().get(pk=self.kwargs["report"])
        except ReportDefinition.DoesNotExist:
            raise Http404(_("The requested report does not exist."))

    def get_success_url(self) -> str:
        return report_url(URL_NAME_LIST, self.request.event)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_change"] = self.request.user.has_event_permission(
            self.request.organizer,
            self.request.event,
            CHANGE_PERMISSION,
            request=self.request,
        )
        return ctx


class ReportFormMixin(EventReportMixin):
    """Adds the owning event to the form; used by create and change."""

    form_class = ReportDefinitionForm
    template_name = "pretix_custom_reports/report_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["event"] = self.request.event
        return kwargs

    def form_invalid(self, form):
        messages.error(
            self.request, _("We could not save your changes. See below for details.")
        )
        return super().form_invalid(form)


class ReportListView(EventReportMixin, EventPermissionRequiredMixin, ListView):
    """All reports of this event."""

    permission = VIEW_PERMISSION
    template_name = "pretix_custom_reports/report_list.html"
    context_object_name = "reports"
    paginate_by = 25

    def get_queryset(self):
        return super().get_queryset().select_related("source_template")


class ReportCreateView(ReportFormMixin, EventPermissionRequiredMixin, CreateView):
    """Create a report from a JSON definition."""

    permission = CHANGE_PERMISSION

    @transaction.atomic
    def form_valid(self, form):
        form.instance.event = self.request.event
        form.instance.organizer = None
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        self.object.log_added(user=self.request.user)
        messages.success(self.request, _("The report has been created."))
        return response


class ReportUpdateView(ReportFormMixin, EventPermissionRequiredMixin, UpdateView):
    """Change name, identifier, base or definition of an existing report."""

    permission = CHANGE_PERMISSION

    @transaction.atomic
    def form_valid(self, form):
        changed = list(form.changed_data)
        response = super().form_valid(form)
        if changed:
            self.object.log_changed(
                user=self.request.user, data={"changed_fields": changed}
            )
        messages.success(self.request, _("Your changes have been saved."))
        return response


class ReportDuplicateView(EventReportMixin, EventPermissionRequiredMixin, View):
    """POST-only copy of a report inside the same event.

    POST-only on purpose: a GET that writes to the database would be triggered
    by every link prefetcher and would not be CSRF protected.
    """

    permission = CHANGE_PERMISSION

    def get(self, request, *args, **kwargs):
        return self.http_method_not_allowed(request, *args, **kwargs)

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        source = self.get_object()
        # str() now, inside the request: the active language is what the user
        # sees, and a lazy proxy has no reliable ``len()``.
        suffix = str(_("(copy)"))
        # ``name`` is a CharField(190); PostgreSQL would reject an overlong
        # value, SQLite would silently keep it.
        limit = ReportDefinition._meta.get_field("name").max_length
        name = f"{source.name[: limit - len(suffix) - 1]} {suffix}"
        copy = source.duplicate(name=name, created_by=request.user)
        copy.log_added(user=request.user, data={"duplicated_from": source.pk})
        messages.success(request, _("The report has been duplicated."))
        return redirect(report_url(URL_NAME_EDIT, request.event, report=copy.pk))


class ReportDeleteView(
    EventReportMixin, EventPermissionRequiredMixin, CompatDeleteView
):
    """Delete a report after confirmation."""

    permission = CHANGE_PERMISSION
    template_name = "pretix_custom_reports/report_confirm_delete.html"

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        # Log first: a LogEntry points at its object through a generic
        # relation, which needs the primary key to still exist.
        self.object.log_deleted(user=request.user)
        self.object.delete()
        messages.success(request, _("The report has been deleted."))
        return redirect(self.get_success_url())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
#
# ``urls.py`` belongs to the integrator (ORCHESTRIERUNG.md section 5), so this
# list is the copy-ready hand-over: ``urlpatterns += event_urlpatterns``. See
# handoff/requests/persistence-dev-an-integrator-urls.md.
#
# A plugin's ``urlpatterns`` are included at the URL *root*, so the control
# prefix has to be spelled out (docs/pretix-api-notes.md section 10).

_EVENT_PREFIX = r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports"

event_urlpatterns = [
    re_path(
        _EVENT_PREFIX + r"/reports/$",
        ReportListView.as_view(),
        name=URL_NAME_LIST,
    ),
    re_path(
        _EVENT_PREFIX + r"/reports/add/$",
        ReportCreateView.as_view(),
        name=URL_NAME_ADD,
    ),
    re_path(
        _EVENT_PREFIX + r"/reports/(?P<report>\d+)/$",
        ReportUpdateView.as_view(),
        name=URL_NAME_EDIT,
    ),
    re_path(
        _EVENT_PREFIX + r"/reports/(?P<report>\d+)/duplicate/$",
        ReportDuplicateView.as_view(),
        name=URL_NAME_DUPLICATE,
    ),
    re_path(
        _EVENT_PREFIX + r"/reports/(?P<report>\d+)/delete/$",
        ReportDeleteView.as_view(),
        name=URL_NAME_DELETE,
    ),
]
