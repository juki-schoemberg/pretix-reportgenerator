# Owner: frontend-dev (ORCHESTRIERUNG.md section 5)
"""The graphical report editor page.

Owner: frontend-dev.

The page itself is a thin shell: it renders the control-panel chrome, a
``<script type="application/json">`` block with the editor's configuration and
loads two static files. Everything else happens against the JSON endpoints in
:mod:`pretix_custom_reports.views.api`.

Why a JSON config block and not inline JavaScript: the control panel's CSP has
no ``unsafe-inline`` for ``script-src`` (pretix/base/middleware.py), so inline
script would simply not run. A JSON block is not executable and therefore not
affected.

Why no build chain: everything the editor needs -- jQuery, Bootstrap 3,
Sortable.js, select2, Font Awesome -- is already shipped and self-hosted by
``pretixcontrol/base.html``. No CDN, no npm, no bundler (SPEC.md section 4,
"Frontend").
"""

from typing import Any, Dict, List, Optional

from django.contrib import messages
from django.http import Http404
from django.urls import NoReverseMatch, re_path, reverse
from django.utils.translation import gettext, gettext_lazy as _
from django.views.generic import TemplateView
from pretix.control.permissions import (
    EventPermissionRequiredMixin,
    OrganizerPermissionRequiredMixin,
)

from ..contracts import SCHEMA_VERSION, Base, empty_definition
from ..models import ReportDefinition
from ..portability.templating import ORGANIZER_CHANGE_PERMISSION
from ..signals import URL_NAMESPACE, VIEW_PERMISSION
from .api import PluginActiveMixin
from .crud import CHANGE_PERMISSION, URL_NAME_ADD, URL_NAME_EDIT
from .portability import URL_NAME_EXPORT, URL_NAME_IMPORT
from .templates import (
    URL_NAME_EVENT_PICK as URL_NAME_TEMPLATES,
    URL_NAME_ORG_ADD,
    URL_NAME_ORG_EDIT,
    URL_NAME_ORG_EXPORT,
    URL_NAME_ORG_LIST,
    OrganizerPluginActiveMixin,
)

__all__ = [
    "EditorShellMixin",
    "ReportEditorView",
    "TemplateEditorView",
    "editor_urlpatterns",
    "template_editor_urlpatterns",
]

#: Query parameter naming the event whose field library the template editor uses.
REFERENCE_EVENT_PARAM = "reference_event"


class EditorShellMixin:
    """Everything the editor page needs, without any permission decision.

    Two views render the same shell: :class:`ReportEditorView` for a report
    inside an event and :class:`TemplateEditorView` for an organizer-level
    template. They differ in the gate (event vs. organizer permission), in where
    the form posts to and in which URL kwargs their routes carry -- not in the
    page, not in the JavaScript and not in the JSON contract. Hence one mixin
    with three seams (:meth:`url_kwargs`, :meth:`api_url`, :meth:`get_report`)
    and no second template.
    """

    template_name = "pretix_custom_reports/editor.html"

    #: Which control-panel chrome to extend. The event pages hang below
    #: ``pretixcontrol/event/base.html``, the organizer pages below
    #: ``pretixcontrol/organizers/base.html``; both leave ``{% block content %}``
    #: to the page (verified in pretix 2026.6.0 -- the organizer base only wraps
    #: an extra ``inner`` block inside it, which we simply do not use).
    editor_base_template = "pretixcontrol/event/base.html"

    #: Template mode: labels, the reference-event hint and the reduced set of
    #: portability buttons.
    is_template = False

    # -- data -------------------------------------------------------------

    def get_report(self) -> Optional[ReportDefinition]:
        raise NotImplementedError  # pragma: no cover

    def load_definition(self) -> Optional[Dict[str, Any]]:
        """The definition document the editor opens with.

        Straight out of the database: the model canonicalises on save, so what
        comes back here is exactly what the round trip has to reproduce.
        Deliberately *not* validated again -- an old report whose structure no
        longer validates must still be openable, otherwise it can never be
        repaired. The browser normalises on load and the ``api/validate/``
        endpoint reports what is wrong with it.
        """
        report = self.get_report()
        return report.definition if report is not None else None

    # -- urls -------------------------------------------------------------

    def may_change(self) -> bool:
        raise NotImplementedError  # pragma: no cover

    def save_url(self, report: Optional[ReportDefinition]) -> Optional[str]:
        raise NotImplementedError  # pragma: no cover

    def portability_urls(self, report: Optional[ReportDefinition]) -> Dict[str, Any]:
        raise NotImplementedError  # pragma: no cover

    def url_kwargs(self) -> Dict[str, Any]:
        """Route arguments every URL of this view's level needs."""
        raise NotImplementedError  # pragma: no cover

    def url(self, name: str, **extra: Any) -> str:
        return reverse(
            f"{URL_NAMESPACE}:{name}",
            kwargs={**self.url_kwargs(), **extra},
        )

    def url_or_none(self, name: str, **extra: Any) -> Optional[str]:
        """:meth:`url`, but ``None`` for a route that is not wired up.

        Deliberately not a ``{% url %}`` tag in the template: that raises, and a
        half-wired ``urls.py`` would then take down the whole editor instead of
        one button (the API and CRUD routes come from three different handoff
        requests, and they will not all land in the same commit).
        """
        try:
            return self.url(name, **extra)
        except NoReverseMatch:
            return None

    def api_url(self, name: str) -> str:
        """One of the three JSON endpoints from ``views/api.py``.

        Those are event-level by construction -- a field library only exists for
        a concrete event -- so the template editor points them at its reference
        event while everything else stays on the organizer.
        """
        return self.url(name)

    # -- strings for the browser ------------------------------------------

    @staticmethod
    def js_strings() -> Dict[str, str]:
        """Strings the JavaScript needs, translated server side.

        Deliberately not Django's JavaScript catalogue: ``makemessages`` picks
        these up from this file like any other Python string, so the German
        catalogue the integrator maintains covers them without extra tooling
        (CLAUDE.md rule 8 -- source strings are English).
        """
        return {
            "add_column": gettext("Add as column"),
            "add_condition": gettext("Add condition"),
            "add_filter": gettext("Add as filter"),
            "add_sort": gettext("Add as sorting stage"),
            "add_value": gettext("Add"),
            "aggregate_none": gettext("no aggregate"),
            "and": gettext("and"),
            "badge_aggregate": gettext("aggregate"),
            "badge_aggregate_help": gettext(
                "Belongs to a single position: needs an aggregate on this base."
            ),
            "badge_event_values": gettext("event values"),
            "badge_event_values_help": gettext(
                "Filter values for this field are specific to this event and have "
                "to be remapped on import."
            ),
            "badge_plugin_help": gettext("Provided by another plugin."),
            "badge_unavailable": gettext("not on this base"),
            "base_add_aggregate": gettext("These columns will get an aggregate:"),
            "base_drop_aggregate": gettext("These columns lose their aggregate:"),
            "base_drop_columns": gettext("These columns will be removed:"),
            "base_drop_filters": gettext("These filters will be removed:"),
            "base_drop_sorting": gettext("These sorting stages will be removed:"),
            "base_switch_confirm": gettext("Switch anyway"),
            "base_switch_title": gettext(
                "Switching the report base changes the available fields."
            ),
            "cancel": gettext("Cancel"),
            "choose_field": gettext("Choose a field …"),
            "choose_value": gettext("Choose …"),
            "column_hidden_off": gettext("Visible in the output."),
            "column_hidden_on": gettext(
                "Hidden: kept in the definition, not written to the output."
            ),
            "days": gettext("days"),
            "drag": gettext("Drag to reorder"),
            "drop_here": gettext("Drop a field here to add it as a column."),
            "errors_title": gettext("The report is not valid yet."),
            "event_values_note": gettext(
                "The values of this field are specific to this event and are "
                "remapped on import."
            ),
            "field_other_base": gettext("Not available on this report base."),
            "format_default": gettext("default"),
            "group": gettext("Group:"),
            "group_empty": gettext(
                "Empty group: add a condition or it will be dropped."
            ),
            "issue_aggregate_not_allowed": gettext(
                "A column uses an aggregate the field does not support."
            ),
            "issue_aggregate_required": gettext(
                "A column needs an aggregate on this report base."
            ),
            "issue_duplicate_sorting": gettext(
                "The same field is used twice for sorting."
            ),
            "issue_field_unavailable": gettext(
                "A field is not available on this report base."
            ),
            "issue_incomplete_condition": gettext(
                "A filter has no field or no operator yet and is ignored."
            ),
            "issue_incomplete_sorting": gettext("A sorting stage has no field yet."),
            "issue_invalid_row_limit": gettext("The row limit is out of range."),
            "issue_label_too_long": gettext("A column title is too long."),
            "issue_missing_value": gettext("A filter is missing its value."),
            "issue_no_columns": gettext("A report needs at least one column."),
            "issue_not_sortable": gettext("A field used for sorting is not sortable."),
            "issue_operator_not_allowed": gettext(
                "An operator is not allowed for its field."
            ),
            "issue_too_many_columns": gettext("Too many columns."),
            "issue_too_many_sorting": gettext("Too many sorting stages."),
            "issue_too_many_values": gettext("Too many values in one filter."),
            "leave_unsaved": gettext(
                "This page has unsaved changes that will be lost. Continue?"
            ),
            "leave_unsaved_export": gettext(
                "This page has unsaved changes. The file will contain the saved "
                "version, not your current changes. Continue?"
            ),
            "library_empty": gettext("No field matches your search."),
            "load_failed": gettext("The editor could not be loaded."),
            "move_down": gettext("Move down"),
            "move_up": gettext("Move up"),
            "multiselect_help": gettext("Select one or more values."),
            "no": gettext("No"),
            "no_values": gettext("no values yet"),
            "preview_failed": gettext("No preview: the report is not valid yet."),
            "preview_incomplete": gettext(
                "Complete the highlighted entries to refresh the preview."
            ),
            "preview_limit": gettext("preview limited to %(limit)s rows"),
            "preview_loading": gettext("Loading preview …"),
            "preview_paused": gettext("Automatic preview is off."),
            "preview_rows": gettext("Showing %(shown)s of %(total)s rows"),
            "remove": gettext("Remove"),
            "remove_group": gettext("Remove group"),
            "separator": gettext("Separator between the joined values"),
            "stage_compile": gettext(
                "The report refers to fields in a way this event does not allow."
            ),
            "stage_execute": gettext("The preview could not be executed."),
            "stage_fields": gettext(
                "The report refers to fields that do not exist here."
            ),
            "stage_request": gettext(
                "The editor sent something the server could not read."
            ),
            "stage_structure": gettext("The report definition is not valid."),
            "unknown_field": gettext("unknown"),
            "value_na": "—",
            "value_none": gettext("no value needed"),
            "warnings_title": gettext("Warnings"),
            "yes": gettext("Yes"),
        }

    # -- context ----------------------------------------------------------

    def shell_extra_context(self) -> Dict[str, Any]:
        """Hook for the template-mode additions. Empty at event level."""
        return {}

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        report = self.get_report()
        definition = self.load_definition() or empty_definition(Base.ORDER)

        ctx["editor_base_template"] = self.editor_base_template
        ctx["is_template"] = self.is_template
        ctx["report"] = report
        ctx["report_name"] = report.name if report is not None else ""
        ctx["report_description"] = report.description if report is not None else ""
        # Posted back unchanged when editing. Without it the ModelForm would
        # clean an empty identifier and the model would generate a fresh one on
        # every save -- silently breaking every scheduled export that refers to
        # the report by its stable identifier.
        ctx["report_identifier"] = report.identifier if report is not None else ""
        ctx["save_url"] = self.save_url(report)
        ctx["portability"] = self.portability_urls(report)
        ctx["config"] = {
            "schema_version": SCHEMA_VERSION,
            "urls": {
                "fields": self.api_url("api.fields"),
                "preview": self.api_url("api.preview"),
                "validate": self.api_url("api.validate"),
            },
            "initial": definition,
            "i18n": self.js_strings(),
        }
        ctx.update(self.shell_extra_context())
        return ctx


class ReportEditorView(
    PluginActiveMixin, EventPermissionRequiredMixin, EditorShellMixin, TemplateView
):
    """Build a report by clicking, with a live preview.

    Permission: :data:`~pretix_custom_reports.signals.VIEW_PERMISSION`. The
    editor shows real order data in its preview, so it is gated exactly like the
    preview endpoint. *Saving* is gated more strictly, by ``persistence-dev``'s
    CRUD views (``event.settings.general:write``); a user who may look but not
    change gets the editor without a save target, see :meth:`save_url`.

    The ``identifier`` route argument is the stable
    :attr:`~pretix_custom_reports.models.ReportDefinition.identifier`, not the
    primary key: this URL ends up in bookmarks and in documentation, and the
    identifier survives an event copy (ADR 0001 section 5). The CRUD routes use
    the primary key; both are reachable from here, the difference is documented
    in ``handoff/requests/frontend-dev-an-integrator-urls.md`` section 1.
    """

    permission = VIEW_PERMISSION

    # -- data -------------------------------------------------------------

    def get_report(self) -> Optional[ReportDefinition]:
        """The stored report this URL addresses, or ``None`` for a new one.

        The queryset goes through ``event.custom_reports``, so a report of
        another event answers 404 even for a user who has permission in both
        (CLAUDE.md rule 4). Organizer templates have ``event=None`` and are
        therefore invisible here by construction, not by a filter someone has
        to remember.
        """
        if getattr(self, "_report_loaded", False):
            return self._report
        identifier = self.kwargs.get("identifier")
        report = None
        if identifier:
            report = (
                self.request.event.custom_reports.by_identifier(identifier)
                .order_by("pk")
                .first()
            )
            if report is None:
                raise Http404("The requested report does not exist.")
        self._report = report
        self._report_loaded = True
        return report

    # -- urls -------------------------------------------------------------

    def may_change(self) -> bool:
        """Whether this user may store reports at all.

        The editor itself hangs on ``event.orders:read`` because its preview
        shows real order data; creating and changing needs
        ``event.settings.general:write`` (views/crud.py, views/portability.py,
        views/templates.py all agree on that string). Anything that writes is
        hidden rather than rendered into a 403.
        """
        request = self.request
        return request.user.has_event_permission(
            request.organizer, request.event, CHANGE_PERMISSION, request=request
        )

    def save_url(self, report: Optional[ReportDefinition]) -> Optional[str]:
        """Where the editor's form posts to, or ``None`` if it must not.

        ``None`` in two cases, and the template explains both: the user may read
        but not change reports, or the CRUD routes are not wired into
        ``urls.py`` yet (that file belongs to the integrator, wave 4). Returning
        ``None`` rather than rendering a dead action is what keeps the save
        button honest.
        """
        if not self.may_change():
            return None
        if report is None:
            return self.url_or_none(URL_NAME_ADD)
        return self.url_or_none(URL_NAME_EDIT, report=report.pk)

    def portability_urls(self, report: Optional[ReportDefinition]) -> Dict[str, Any]:
        """Links into portability-dev's views: export, import, templates.

        Same rules as :meth:`save_url`, for the same reasons:

        * ``export`` needs a stored report -- there is nothing to download for a
          report that was never saved, and the URL is built from its primary
          key. It exports what is **in the database**, not what is currently in
          the editor; the template says so and the JavaScript asks before
          leaving with unsaved changes.
        * ``import`` and ``templates`` write, so they follow
          :meth:`may_change`. ``export`` only reads and is offered to anyone who
          may open the editor.
        * every one of them is ``None`` while ``urls.py`` does not carry the
          routes yet (integrator, wave 4). The names themselves are contract,
          see handoff/requests/portability-dev-an-integrator-urls.md section 2.
        """
        writable = self.may_change()
        return {
            "export": (
                self.url_or_none(URL_NAME_EXPORT, report=report.pk)
                if report is not None
                else None
            ),
            "import": self.url_or_none(URL_NAME_IMPORT) if writable else None,
            "templates": self.url_or_none(URL_NAME_TEMPLATES) if writable else None,
        }

    def url_kwargs(self) -> Dict[str, Any]:
        return {
            "organizer": self.request.organizer.slug,
            "event": self.request.event.slug,
        }


class TemplateEditorView(
    OrganizerPluginActiveMixin,
    OrganizerPermissionRequiredMixin,
    EditorShellMixin,
    TemplateView,
):
    """The same editor, for an organizer-level report template (SPEC.md F10).

    A template has no event, but a field library only exists *for* an event:
    which questions there are, which items, which meta properties -- all of that
    is event data. So the user picks a **reference event** and the editor works
    against that event's registry, preview and validation endpoints. Fields whose
    filter values are event specific already carry the "event values" badge the
    import flow uses, which is exactly the warning a template author needs.

    Permission: ``organizer.settings.general:write``
    (:data:`~pretix_custom_reports.portability.templating.ORGANIZER_CHANGE_PERMISSION`),
    the same single gate ``views/templates.py`` puts on the template list -- there
    is no read-only mode for templates, so :meth:`may_change` is that check.

    The reference event is additionally gated on
    :data:`~pretix_custom_reports.signals.VIEW_PERMISSION` **per event**: the
    preview shows real order data of that event, and the API endpoints it talks
    to enforce the same thing (``views/api.py``). An organizer admin without
    order access in any event therefore gets a clear message instead of a page
    whose every request answers 403.
    """

    permission = ORGANIZER_CHANGE_PERMISSION
    editor_base_template = "pretixcontrol/organizers/base.html"
    is_template = True
    choose_event_template = "pretix_custom_reports/editor_choose_event.html"

    #: Set by :meth:`get` before the shell is rendered; never ``None`` there.
    reference_event = None

    #: Per-request cache for :meth:`allowed_events`.
    _allowed_events = None

    # -- reference event --------------------------------------------------

    def allowed_events(self) -> List[Any]:
        """Events of this organizer this user may build a template against.

        Two conditions, both necessary:

        * the plugin is enabled for the event -- otherwise its API routes answer
          404 (``views/api.py``, ``PluginActiveMixin``);
        * the user has :data:`VIEW_PERMISSION` on the event -- otherwise the
          field library and the preview answer 403.

        Checked in Python rather than in SQL because ``has_event_permission``
        walks team memberships and staff sessions; an organizer rarely has enough
        events for that to matter, and getting it wrong would mean offering a
        broken choice.

        Cached per request: :meth:`get` asks once to resolve the reference event
        and :meth:`shell_extra_context` asks again to decide whether offering a
        different one makes sense.
        """
        if getattr(self, "_allowed_events", None) is not None:
            return self._allowed_events
        request = self.request
        events = request.organizer.events.filter(
            plugins__contains="pretix_custom_reports"
        ).order_by("-date_from", "slug")
        self._allowed_events = [
            event
            for event in events
            if request.user.has_event_permission(
                request.organizer, event, VIEW_PERMISSION, request=request
            )
        ]
        return self._allowed_events

    def resolve_reference_event(self, events: List[Any]) -> Optional[Any]:
        """The event named in ``?reference_event=``, or the obvious one.

        ``None`` means "ask the user". A slug that is not in ``events`` -- it does
        not exist, the plugin is off there, or the user may not read its orders --
        is deliberately not distinguished from a missing one: all three are "you
        have to choose", and telling them apart would leak which slugs exist.
        """
        slug = self.request.GET.get(REFERENCE_EVENT_PARAM) or ""
        if slug:
            for event in events:
                if event.slug == slug:
                    return event
            messages.error(
                self.request,
                _(
                    "That event cannot be used as a reference: it does not exist, "
                    "this plugin is not enabled for it, or you may not view its "
                    "orders. Please pick one of the events below."
                ),
            )
            return None
        if len(events) == 1:
            return events[0]
        return None

    def get(self, request, *args, **kwargs):
        events = self.allowed_events()
        # Load the template before anything else, so a wrong primary key is a
        # 404 rather than an event picker for a template that does not exist.
        self.get_report()
        reference = self.resolve_reference_event(events) if events else None
        if reference is None:
            return self.render_choose_event(events)
        self.reference_event = reference
        return super().get(request, *args, **kwargs)

    def render_choose_event(self, events: List[Any]):
        """The small in-between page, and the "no usable event" message.

        One template for both cases on purpose: an empty list *is* the message,
        and a second template would be a second place to keep the explanation.
        """
        report = self.get_report()
        context = self.get_choose_event_context(events, report)
        return self.response_class(
            request=self.request,
            template=[self.choose_event_template],
            context=context,
            using=self.template_engine,
        )

    def get_choose_event_context(
        self, events: List[Any], report: Optional[ReportDefinition]
    ) -> Dict[str, Any]:
        return {
            "organizer": self.request.organizer,
            "events": events,
            "template": report,
            "report_name": report.name if report is not None else "",
            "reference_event_param": REFERENCE_EVENT_PARAM,
            "list_url": self.url_or_none(URL_NAME_ORG_LIST),
            "is_template": True,
        }

    # -- data -------------------------------------------------------------

    def get_report(self) -> Optional[ReportDefinition]:
        """The stored template this URL addresses, or ``None`` for a new one.

        ``templates_for_organizer`` is both the tenant boundary and the XOR
        filter (``organizer=<this one>`` **and** ``event IS NULL``), so an
        event-level report can never be opened through this route -- not even by
        its own organizer's admin (CLAUDE.md rule 4). Deliberately the model
        manager and not a helper from ``views/templates.py``: that module has
        another owner.
        """
        if getattr(self, "_report_loaded", False):
            return self._report
        pk = self.kwargs.get("template")
        report = None
        if pk:
            try:
                report = ReportDefinition.objects.templates_for_organizer(
                    self.request.organizer
                ).get(pk=pk)
            except ReportDefinition.DoesNotExist:
                raise Http404("The requested template does not exist.")
        self._report = report
        self._report_loaded = True
        return report

    # -- urls -------------------------------------------------------------

    def may_change(self) -> bool:
        """The organizer gate from above, nothing else.

        ``views/templates.py`` puts ``ORGANIZER_CHANGE_PERMISSION`` on the list,
        the form and the export alike, so a user who got this far may save. The
        method stays because the shell asks for it.
        """
        request = self.request
        return request.user.has_organizer_permission(
            request.organizer, ORGANIZER_CHANGE_PERMISSION, request=request
        )

    def save_url(self, report: Optional[ReportDefinition]) -> Optional[str]:
        """portability-dev's create/change views take the editor's POST as is.

        Verified against ``forms.ReportDefinitionForm``: its fields are exactly
        ``name``, ``description``, ``identifier``, ``base`` and ``definition``,
        the owner comes from the view rather than from the request body, and
        ``definition`` is a ``JSONField`` that parses the JSON string the
        editor's hidden input carries.
        ``tests/test_editor_api.py::test_template_editor_post_round_trip`` keeps
        that assumption honest.
        """
        if not self.may_change():
            return None
        if report is None:
            return self.url_or_none(URL_NAME_ORG_ADD)
        return self.url_or_none(URL_NAME_ORG_EDIT, template=report.pk)

    def portability_urls(self, report: Optional[ReportDefinition]) -> Dict[str, Any]:
        """Export only.

        There is no organizer-level file *import* (``views/portability.py`` is
        event level), and "load a template" into a template is meaningless -- a
        template is already the thing that gets loaded.
        """
        return {
            "export": (
                self.url_or_none(URL_NAME_ORG_EXPORT, template=report.pk)
                if report is not None
                else None
            ),
            "import": None,
            "templates": None,
        }

    def url_kwargs(self) -> Dict[str, Any]:
        return {"organizer": self.request.organizer.slug}

    def api_url(self, name: str) -> str:
        """The reference event's endpoint, not an organizer-level one.

        ``views/api.py`` stays untouched: it is event scoped, gated on the event
        and on the plugin being active there, and that is exactly what we want
        for a preview that shows that event's orders.
        """
        return reverse(
            f"{URL_NAMESPACE}:{name}",
            kwargs={
                "organizer": self.request.organizer.slug,
                "event": self.reference_event.slug,
            },
        )

    # -- context ----------------------------------------------------------

    def shell_extra_context(self) -> Dict[str, Any]:
        # Only worth offering when there is something to choose from; with a
        # single usable event the link would lead straight back here.
        multiple = len(self.allowed_events()) > 1
        return {
            "reference_event": self.reference_event,
            "choose_event_url": self.request.path if multiple else None,
            "list_url": self.url_or_none(URL_NAME_ORG_LIST),
        }


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
#
# Owned by frontend-dev, wired into urls.py by the integrator -- see
# handoff/requests/frontend-dev-an-integrator-urls.md and the note on the
# "control/" prefix in api.py.

_EVENT_PREFIX = r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports/"

editor_urlpatterns = [
    re_path(
        _EVENT_PREFIX + r"editor/$",
        ReportEditorView.as_view(),
        name="editor.new",
    ),
    re_path(
        _EVENT_PREFIX + r"editor/(?P<identifier>[a-zA-Z0-9._-]+)/$",
        ReportEditorView.as_view(),
        name="editor.edit",
    ),
]

# The organizer-level editor, kept in its own list so the integrator can wire it
# next to ``templates_organizer_urlpatterns`` -- see
# handoff/requests/frontend-dev-an-integrator-template-editor-urls.md.
#
# ``template`` is the primary key, not the identifier: these routes sit next to
# portability-dev's ``organizer.templates.edit``/``.export``, which use the pk,
# and the editor posts and links to those. The event-level editor's use of the
# stable identifier is a deliberate exception documented in
# handoff/requests/frontend-dev-an-integrator-urls.md section 1.
_ORG_PREFIX = r"^control/organizer/(?P<organizer>[^/]+)/customreports"

template_editor_urlpatterns = [
    re_path(
        _ORG_PREFIX + r"/templates/editor/$",
        TemplateEditorView.as_view(),
        name="organizer.templates.editor.new",
    ),
    re_path(
        _ORG_PREFIX + r"/templates/editor/(?P<template>\d+)/$",
        TemplateEditorView.as_view(),
        name="organizer.templates.editor.edit",
    ),
]
