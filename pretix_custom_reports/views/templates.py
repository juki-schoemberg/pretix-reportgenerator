# Owner from wave 2 on: portability-dev (see ORCHESTRIERUNG.md section 5)
"""Organizer templates: management on the organizer, "load" in the event.

SPEC.md F10. Two groups of views that deliberately look different because they
live at different levels:

Organizer level (``/control/organizer/<organizer>/customreports/templates/``)
    List, create, change, delete, export. Reports here have ``event=None`` and
    ``organizer=<org>`` -- the XOR from ``models.py``. They are configuration,
    not data, so they need the organizer-level change permission
    (``organizer.settings.general:write``, docs/pretix-api-notes.md section 8.2).

Event level (``.../customreports/reports/templates/``)
    Pick a template, look at the resolution report, create the copy. Needs the
    event-level change permission, because that is where the new report lands.

The "load" step is not a link but a two-request flow with a confirmation, for
the same reason the file import has one: the target event may not know every
question the template names, and dropping a column is a decision the user makes,
not one we make for them (SPEC.md F9/F10).

What this module does *not* do
------------------------------

It does not resolve anything itself. Every translation of an event-specific
reference goes through
:func:`~pretix_custom_reports.portability.resolution.resolve_definition`, the
same function the file import uses. Two implementations of "find this question
in that event" would disagree within one release, and the disagreement would be
invisible until someone's report silently lost a column.
"""

import json
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import re_path, reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, ListView, TemplateView, UpdateView, View
from pretix.control.permissions import (
    EventPermissionRequiredMixin,
    OrganizerPermissionRequiredMixin,
)
from pretix.helpers.compat import CompatDeleteView

from pretix_custom_reports import contracts
from pretix_custom_reports.forms import ReportDefinitionForm
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.portability.envelope import (
    build_export_document,
    export_filename,
)
from pretix_custom_reports.portability.errors import (
    ImportRejected,
    TemplateAccessDenied,
)
from pretix_custom_reports.portability.resolution import ResolutionStrategy
from pretix_custom_reports.portability.templating import (
    ORGANIZER_CHANGE_PERMISSION,
    apply_template,
    available_templates,
    plan_template,
)
from pretix_custom_reports.views.portability import (
    CHANGE_PERMISSION,
    PluginActiveMixin,
    report_url,
)

__all__ = [
    "OrganizerPluginActiveMixin",
    "TemplateApplyView",
    "TemplateCreateView",
    "TemplateDeleteView",
    "TemplateExportView",
    "TemplateListView",
    "TemplatePickView",
    "TemplateUpdateView",
    "templates_event_urlpatterns",
    "templates_organizer_urlpatterns",
]

URL_NAMESPACE = "plugins:pretix_custom_reports"

URL_NAME_ORG_LIST = "organizer.templates"
URL_NAME_ORG_ADD = "organizer.templates.add"
URL_NAME_ORG_EDIT = "organizer.templates.edit"
URL_NAME_ORG_DELETE = "organizer.templates.delete"
URL_NAME_ORG_EXPORT = "organizer.templates.export"

URL_NAME_EVENT_PICK = "event.reports.templates"
URL_NAME_EVENT_APPLY = "event.reports.templates.apply"

URL_NAME_EVENT_LIST = "event.reports"
URL_NAME_EVENT_EDIT = "event.reports.edit"

FORM_STRATEGY = "strategy"
FORM_ACTION = "action"
ACTION_CONFIRM = "confirm"


def organizer_url(name: str, organizer, **kwargs) -> str:
    return reverse(
        f"{URL_NAMESPACE}:{name}",
        kwargs={"organizer": organizer.slug, **kwargs},
    )


class OrganizerPluginActiveMixin:
    """404 unless the plugin is enabled in at least one event of the organizer.

    The organizer-level counterpart of ``views/api.py``'s ``PluginActiveMixin``.
    pretix activates plugins per event, so "is this plugin on for the
    organizer?" has no direct answer; pretix's own organizer-level plugin views
    ask the same question the same way (``pretix/plugins/banktransfer/views.py``,
    ``OrganizerBanktransferView``).
    """

    plugin_module = "pretix_custom_reports"

    def dispatch(self, request, *args, **kwargs):
        organizer = getattr(request, "organizer", None)
        if organizer is None:
            raise Http404("No organizer in this request.")
        if not organizer.events.filter(plugins__contains=self.plugin_module).exists():
            raise Http404("This plugin is not active in any event of this organizer.")
        return super().dispatch(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Organizer level: manage the templates
# ---------------------------------------------------------------------------


class OrganizerTemplateMixin(OrganizerPluginActiveMixin):
    """Shared queryset and 404 behaviour for the organizer-level views.

    ``templates_for_organizer`` is both the tenant boundary and the XOR filter:
    ``organizer=<this one>`` **and** ``event IS NULL``. An event-level report
    can therefore never be reached, changed or deleted through these URLs, not
    even by its own organizer's admin (CLAUDE.md rule 4).
    """

    model = ReportDefinition
    context_object_name = "template"

    def get_queryset(self):
        return ReportDefinition.objects.templates_for_organizer(self.request.organizer)

    def get_object(self, queryset=None) -> ReportDefinition:
        try:
            return self.get_queryset().get(pk=self.kwargs["template"])
        except ReportDefinition.DoesNotExist:
            raise Http404(_("The requested template does not exist."))

    def get_success_url(self) -> str:
        return organizer_url(URL_NAME_ORG_LIST, self.request.organizer)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["organizer"] = self.request.organizer
        return ctx


class TemplateListView(
    OrganizerTemplateMixin, OrganizerPermissionRequiredMixin, ListView
):
    """Every report template of this organizer."""

    permission = ORGANIZER_CHANGE_PERMISSION
    template_name = "pretix_custom_reports/template_list.html"
    context_object_name = "templates"
    paginate_by = 25


class TemplateFormMixin(OrganizerTemplateMixin):
    form_class = ReportDefinitionForm
    template_name = "pretix_custom_reports/template_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # ``organizer=`` (not ``event=``) is what makes the row a template;
        # the form sets both sides of the XOR, so it cannot be forged by a
        # hidden input.
        kwargs["organizer"] = self.request.organizer
        return kwargs

    def form_invalid(self, form):
        messages.error(
            self.request, _("We could not save your changes. See below for details.")
        )
        return super().form_invalid(form)


class TemplateCreateView(
    TemplateFormMixin, OrganizerPermissionRequiredMixin, CreateView
):
    """Create a template from a JSON definition."""

    permission = ORGANIZER_CHANGE_PERMISSION

    @transaction.atomic
    def form_valid(self, form):
        form.instance.organizer = self.request.organizer
        form.instance.event = None
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        self.object.log_added(user=self.request.user)
        messages.success(self.request, _("The template has been created."))
        return response


class TemplateUpdateView(
    TemplateFormMixin, OrganizerPermissionRequiredMixin, UpdateView
):
    """Change a template. Existing copies in events are not touched (no live
    link in v1, SPEC.md F10)."""

    permission = ORGANIZER_CHANGE_PERMISSION

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


class TemplateDeleteView(
    OrganizerTemplateMixin, OrganizerPermissionRequiredMixin, CompatDeleteView
):
    """Delete a template after confirmation.

    Copies already created in events survive: ``source_template`` is
    ``SET_NULL`` (models.py), so they lose the back reference and nothing else.
    """

    permission = ORGANIZER_CHANGE_PERMISSION
    template_name = "pretix_custom_reports/template_confirm_delete.html"

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        # Log before deleting: a LogEntry points at its object through a
        # generic relation and needs the primary key to still exist.
        self.object.log_deleted(user=request.user)
        self.object.delete()
        messages.success(request, _("The template has been deleted."))
        return redirect(self.get_success_url())


class TemplateExportView(
    OrganizerTemplateMixin, OrganizerPermissionRequiredMixin, View
):
    """Download a template as a JSON file.

    The file carries no name hints (``meta.references``): a template has no
    event, so there is no registry that could tell us what ``answer.tshirt-size``
    is called. Importing it elsewhere therefore matches by identifier, which is
    the same thing "load template" does.
    """

    permission = ORGANIZER_CHANGE_PERMISSION

    def get(self, request, *args, **kwargs):
        template = self.get_object()
        try:
            document = build_export_document(template, event=None)
        except contracts.DefinitionValidationError as e:
            messages.error(
                request,
                _("This template cannot be exported: %(problem)s")
                % {"problem": str(e)},
            )
            return redirect(self.get_success_url())
        response = HttpResponse(
            json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{export_filename(template)}"'
        )
        template.log_action(
            contracts.LOG_ACTION_EXPORTED,
            data={"identifier": template.identifier, "format": "json"},
            user=request.user,
        )
        return response


# ---------------------------------------------------------------------------
# Event level: load a template
# ---------------------------------------------------------------------------


class EventTemplateMixin(PluginActiveMixin):
    """Templates an event may see, and the permission to write to it."""

    def get_queryset(self):
        return available_templates(self.request.event)

    def get_template_object(self) -> ReportDefinition:
        try:
            return self.get_queryset().get(pk=self.kwargs["template"])
        except ReportDefinition.DoesNotExist:
            raise Http404(_("The requested template does not exist."))


class TemplatePickView(EventTemplateMixin, EventPermissionRequiredMixin, ListView):
    """The templates this event can load."""

    permission = CHANGE_PERMISSION
    model = ReportDefinition
    template_name = "pretix_custom_reports/template_pick.html"
    context_object_name = "templates"
    paginate_by = 25

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["cancel_url"] = report_url(URL_NAME_EVENT_LIST, self.request.event)
        return ctx


class TemplateApplyView(EventTemplateMixin, EventPermissionRequiredMixin, TemplateView):
    """Show what loading this template into this event would do, then do it."""

    permission = CHANGE_PERMISSION
    template_name = "pretix_custom_reports/template_apply.html"

    def _plan(self, strategy: str):
        template = self.get_template_object()
        return plan_template(
            template,
            self.request.event,
            strategy=strategy,
            user=self.request.user,
            request=self.request,
        )

    def _context(self, plan, **kwargs):
        return self.get_context_data(
            plan=plan,
            template=plan.template,
            report=plan.report,
            entries=plan.report.entries,
            strategy=plan.strategy,
            strategy_skip=ResolutionStrategy.SKIP,
            strategy_abort=ResolutionStrategy.ABORT,
            cancel_url=report_url(URL_NAME_EVENT_LIST, self.request.event),
            **kwargs,
        )

    def get(self, request, *args, **kwargs):
        try:
            plan = self._plan(ResolutionStrategy.ABORT)
        except TemplateAccessDenied as e:
            messages.error(request, str(e))
            return redirect(report_url(URL_NAME_EVENT_LIST, request.event))
        return self.render_to_response(self._context(plan))

    def post(self, request, *args, **kwargs):
        strategy = ResolutionStrategy.coerce(request.POST.get(FORM_STRATEGY))
        try:
            plan = self._plan(strategy)
        except TemplateAccessDenied as e:
            messages.error(request, str(e))
            return redirect(report_url(URL_NAME_EVENT_LIST, request.event))

        if request.POST.get(FORM_ACTION) != ACTION_CONFIRM or not plan.ok:
            if request.POST.get(FORM_ACTION) == ACTION_CONFIRM:
                messages.error(
                    request,
                    _(
                        "This template cannot be loaded as it is. Please choose "
                        "how to deal with the fields listed below."
                    ),
                )
            return self.render_to_response(self._context(plan))

        try:
            copy = apply_template(plan, user=request.user)
        except ImportRejected as e:  # pragma: no cover - plan.ok was just checked
            messages.error(request, str(e))
            return self.render_to_response(self._context(plan))

        messages.success(request, _("The template has been loaded into this event."))
        return redirect(report_url(URL_NAME_EVENT_EDIT, request.event, report=copy.pk))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
#
# ``urls.py`` belongs to the integrator (ORCHESTRIERUNG.md section 5); this is
# the copy-ready hand-over, see
# handoff/requests/portability-dev-an-integrator-urls.md.
#
# A plugin's ``urlpatterns`` are included at the URL *root*, so both prefixes
# have to be spelled out (docs/pretix-api-notes.md section 10). The organizer
# prefix mirrors pretix/plugins/banktransfer/urls.py.

_EVENT_PREFIX = r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports"
_ORG_PREFIX = r"^control/organizer/(?P<organizer>[^/]+)/customreports"

templates_event_urlpatterns = [
    re_path(
        _EVENT_PREFIX + r"/reports/templates/$",
        TemplatePickView.as_view(),
        name=URL_NAME_EVENT_PICK,
    ),
    re_path(
        _EVENT_PREFIX + r"/reports/templates/(?P<template>\d+)/$",
        TemplateApplyView.as_view(),
        name=URL_NAME_EVENT_APPLY,
    ),
]

templates_organizer_urlpatterns = [
    re_path(
        _ORG_PREFIX + r"/templates/$",
        TemplateListView.as_view(),
        name=URL_NAME_ORG_LIST,
    ),
    re_path(
        _ORG_PREFIX + r"/templates/add/$",
        TemplateCreateView.as_view(),
        name=URL_NAME_ORG_ADD,
    ),
    re_path(
        _ORG_PREFIX + r"/templates/(?P<template>\d+)/$",
        TemplateUpdateView.as_view(),
        name=URL_NAME_ORG_EDIT,
    ),
    re_path(
        _ORG_PREFIX + r"/templates/(?P<template>\d+)/delete/$",
        TemplateDeleteView.as_view(),
        name=URL_NAME_ORG_DELETE,
    ),
    re_path(
        _ORG_PREFIX + r"/templates/(?P<template>\d+)/export/$",
        TemplateExportView.as_view(),
        name=URL_NAME_ORG_EXPORT,
    ),
]
