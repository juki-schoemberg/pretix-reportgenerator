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
from django.urls import re_path, reverse
from django.utils.translation import gettext
from django.views.generic import TemplateView
from pretix.control.permissions import EventPermissionRequiredMixin

from ..contracts import SCHEMA_VERSION, Base, empty_definition
from ..signals import URL_NAMESPACE, VIEW_PERMISSION
from .api import PluginActiveMixin, mock_available

__all__ = ["ReportEditorView", "editor_urlpatterns"]


class ReportEditorView(PluginActiveMixin, EventPermissionRequiredMixin, TemplateView):
    """Build a report by clicking, with a live preview.

    Permission: :data:`~pretix_custom_reports.signals.VIEW_PERMISSION`. The
    editor shows real order data in its preview, so it is gated exactly like the
    preview endpoint. Saving will be gated more strictly by ``persistence-dev``
    (see ``handoff/requests/frontend-dev-an-integrator-urls.md``).

    Wave 1: the ``identifier`` route argument resolves against the golden
    fixtures, because the model that will hold stored reports does not exist
    yet. Wave 2 replaces :meth:`load_definition` with a database lookup; the
    template and the JavaScript do not change.
    """

    permission = VIEW_PERMISSION
    template_name = "pretix_custom_reports/editor.html"

    # -- data -------------------------------------------------------------

    def load_definition(self) -> Optional[Dict[str, Any]]:
        """The definition the editor opens with, or ``None`` for a new report.

        WAVE 1 -> WAVE 2 SWAP POINT. In wave 2 this becomes::

            report = get_object_or_404(
                Report, event=self.request.event, identifier=identifier
            )
            return report.definition

        Until then an identifier is looked up among the golden fixtures, which
        is what makes "load every golden fixture into the editor" testable
        before persistence exists.
        """
        identifier = self.kwargs.get("identifier")
        if not identifier:
            example = self.request.GET.get("example")
            if not example:
                return None
            identifier = example

        from .api import _load_mock  # local: wave-1 only, see api.py

        if not mock_available():
            raise Http404("Stored reports are not available yet.")
        return _load_mock(identifier)

    # -- context ----------------------------------------------------------

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        request = self.request
        url_kwargs = {
            "organizer": request.organizer.slug,
            "event": request.event.slug,
        }

        def url(name: str) -> str:
            return reverse(f"{URL_NAMESPACE}:{name}", kwargs=url_kwargs)

        definition = self.load_definition() or empty_definition(Base.ORDER)

        ctx["mock_mode"] = mock_available()
        ctx["examples_url"] = url("api.examples") if ctx["mock_mode"] else None
        # Saving belongs to persistence-dev's CRUD view. Until that route
        # exists, the form has no action and the button explains why.
        ctx["save_url"] = None
        ctx["config"] = {
            "schema_version": SCHEMA_VERSION,
            "urls": {
                "fields": url("api.fields"),
                "preview": url("api.preview"),
                "validate": url("api.validate"),
                "examples": url("api.examples") if ctx["mock_mode"] else None,
                # The JS interpolates %(slug)s; reverse() cannot build a URL
                # with a placeholder that the pattern rejects.
                "example": (
                    (url("api.examples") + "%(slug)s/") if ctx["mock_mode"] else None
                ),
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
