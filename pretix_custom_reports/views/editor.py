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

from typing import Any, Dict, Optional

from django.http import Http404
from django.urls import NoReverseMatch, re_path, reverse
from django.utils.translation import gettext
from django.views.generic import TemplateView
from pretix.control.permissions import EventPermissionRequiredMixin

from ..contracts import SCHEMA_VERSION, Base, empty_definition
from ..models import ReportDefinition
from ..signals import URL_NAMESPACE, VIEW_PERMISSION
from .api import PluginActiveMixin
from .crud import CHANGE_PERMISSION, URL_NAME_ADD, URL_NAME_EDIT
from .portability import URL_NAME_EXPORT, URL_NAME_IMPORT
from .templates import URL_NAME_EVENT_PICK as URL_NAME_TEMPLATES

__all__ = ["ReportEditorView", "editor_urlpatterns"]


class ReportEditorView(PluginActiveMixin, EventPermissionRequiredMixin, TemplateView):
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
    template_name = "pretix_custom_reports/editor.html"

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

    def url(self, name: str, **extra: Any) -> str:
        return reverse(
            f"{URL_NAMESPACE}:{name}",
            kwargs={
                "organizer": self.request.organizer.slug,
                "event": self.request.event.slug,
                **extra,
            },
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

    # -- context ----------------------------------------------------------

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        report = self.get_report()
        definition = self.load_definition() or empty_definition(Base.ORDER)

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
                "fields": self.url("api.fields"),
                "preview": self.url("api.preview"),
                "validate": self.url("api.validate"),
            },
            "initial": definition,
            "i18n": self.js_strings(),
        }
        return ctx

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
