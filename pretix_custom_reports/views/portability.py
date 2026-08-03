# Owner from wave 2 on: portability-dev (see ORCHESTRIERUNG.md section 5)
"""Download a report as a file, and read one back in (SPEC.md F9).

Three views, one of which is interesting:

``ReportExportView``
    ``GET`` a report as a JSON file. Read permission is enough -- the file
    contains the definition, not order data.
``ReportImportView``
    Upload a file **or** paste a JSON block, look at the resolution report,
    then decide. Never writes on the first request.
``ReportImportConfirmView``
    Is the same class. The confirmation posts the *original document* back and
    the whole pipeline runs again from those bytes.

Why the confirmation re-runs everything
---------------------------------------

The obvious implementation keeps the resolved definition in the session (or in
a hidden field) and stores it when the user clicks "yes". That would mean the
thing we write is a document that came back from the browser -- which is
untrusted input again, only now with a checkmark next to it. So the hidden
field carries the *original* text, and step two repeats size check, JSON
parsing, structural validation and registry resolution. The extra work is
milliseconds; the property it buys is that there is exactly one code path into
the database and it always starts at
:func:`~pretix_custom_reports.portability.payload.load_json_object`.

Permissions (docs/pretix-api-notes.md section 8.1)
--------------------------------------------------

* export: ``event.orders:read`` -- same key as viewing a report
* import: ``event.settings.general:write`` -- it creates a report

Every queryset goes through ``request.event.custom_reports``, so a report of
another event is a 404 even for a user with permissions in both (CLAUDE.md
rule 4).
"""

from typing import Optional

import json
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import re_path, reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View
from pretix.control.permissions import EventPermissionRequiredMixin

from pretix_custom_reports import contracts
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.portability.envelope import (
    build_export_document,
    export_filename,
)
from pretix_custom_reports.portability.errors import (
    ImportRejected,
    PayloadRejected,
)
from pretix_custom_reports.portability.importer import commit_import, plan_import
from pretix_custom_reports.portability.payload import MAX_PAYLOAD_BYTES
from pretix_custom_reports.portability.resolution import ResolutionStrategy

__all__ = [
    "CHANGE_PERMISSION",
    "ReportExportView",
    "ReportImportView",
    "VIEW_PERMISSION",
    "portability_event_urlpatterns",
]

#: Read and run. Same string as ``views/crud.py``.
VIEW_PERMISSION = "event.orders:read"

#: Create, change, delete on event level.
CHANGE_PERMISSION = "event.settings.general:write"

URL_NAMESPACE = "plugins:pretix_custom_reports"
URL_NAME_LIST = "event.reports"
URL_NAME_EXPORT = "event.reports.export"
URL_NAME_IMPORT = "event.reports.import"
URL_NAME_EDIT = "event.reports.edit"

#: Field name of the hidden input that carries the original document into the
#: confirmation step.
FORM_DOCUMENT = "document"
FORM_FILE = "file"
FORM_TEXT = "text"
FORM_STRATEGY = "strategy"
FORM_ACTION = "action"

ACTION_CONFIRM = "confirm"


class PluginActiveMixin:
    """404 unless this plugin is enabled for the event in the URL.

    Same reasoning as ``views/api.py``: pretix only wraps a plugin's *presale*
    URLs with the "is the plugin on?" check (pretix/multidomain/plugin_handler.py),
    control URLs stay reachable. SPEC.md F1 wants the opposite. Deliberately
    duplicated rather than imported from ``views/api.py``: five lines are
    cheaper than a dependency between two agents' modules, and a divergence
    here would be visible in the tests of both.
    """

    plugin_module = "pretix_custom_reports"

    def dispatch(self, request, *args, **kwargs):
        event = getattr(request, "event", None)
        if event is None or self.plugin_module not in event.get_plugins():
            raise Http404("This plugin is not active for this event.")
        return super().dispatch(request, *args, **kwargs)


def report_url(name: str, event, **kwargs) -> str:
    return reverse(
        f"{URL_NAMESPACE}:{name}",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            **kwargs,
        },
    )


class EventReportMixin(PluginActiveMixin):
    """Hard event scope for everything in this module."""

    def get_queryset(self):
        return self.request.event.custom_reports.all()

    def get_report(self) -> ReportDefinition:
        try:
            return self.get_queryset().get(pk=self.kwargs["report"])
        except ReportDefinition.DoesNotExist:
            raise Http404(_("The requested report does not exist."))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ReportExportView(EventReportMixin, EventPermissionRequiredMixin, View):
    """Download one report as a JSON file."""

    permission = VIEW_PERMISSION

    def get(self, request, *args, **kwargs):
        report = self.get_report()
        try:
            document = build_export_document(report, event=request.event)
        except contracts.DefinitionValidationError as e:
            # Only reachable for a row written around save(); refusing with a
            # message beats handing out a file nobody can import.
            messages.error(
                request,
                _("This report cannot be exported: %(problem)s") % {"problem": str(e)},
            )
            return redirect(report_url(URL_NAME_LIST, request.event))

        # ``ensure_ascii=True``: a definition written before the payload gate
        # learned about unpaired surrogates (S-003) can still hold one, and
        # ``"\ud800".encode("utf-8")`` is a 500, not a file. ``\uXXXX`` escapes
        # are the same document to every JSON reader, importer included.
        payload = json.dumps(document, indent=2, ensure_ascii=True)
        response = HttpResponse(
            payload.encode("utf-8"), content_type="application/json"
        )
        # The file name is built from ASCII characters only (envelope.py), so it
        # cannot break out of the header.
        response["Content-Disposition"] = (
            f'attachment; filename="{export_filename(report)}"'
        )
        report.log_action(
            contracts.LOG_ACTION_EXPORTED,
            data={"identifier": report.identifier, "format": "json"},
            user=request.user,
        )
        return response


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class ReportImportView(EventReportMixin, EventPermissionRequiredMixin, TemplateView):
    """Upload or paste a report file, review the resolution, then decide."""

    permission = CHANGE_PERMISSION
    template_name = "pretix_custom_reports/import_form.html"
    confirm_template_name = "pretix_custom_reports/import_confirm.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("max_bytes", MAX_PAYLOAD_BYTES)
        ctx.setdefault("max_kib", MAX_PAYLOAD_BYTES // 1024)
        ctx.setdefault("cancel_url", report_url(URL_NAME_LIST, self.request.event))
        return ctx

    # -- reading the request ----------------------------------------------

    def _document_text(self) -> Optional[str]:
        """The document as text, from the upload, the textarea or step one.

        Returns ``None`` if the user sent nothing at all. Reads at most
        :data:`MAX_PAYLOAD_BYTES` + 1 bytes from an upload -- enough to notice
        that it is too big, not enough to be a way of filling memory.
        """
        upload = self.request.FILES.get(FORM_FILE)
        if upload is not None:
            if upload.size is not None and upload.size > MAX_PAYLOAD_BYTES:
                raise PayloadRejected(
                    "too_large",
                    str(
                        _("The file is larger than %(kib)s KiB.")
                        % {"kib": MAX_PAYLOAD_BYTES // 1024}
                    ),
                )
            raw = upload.read(MAX_PAYLOAD_BYTES + 1)
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                raise PayloadRejected(
                    "not_utf8",
                    str(
                        _("The file is not UTF-8 encoded text. Report files are JSON.")
                    ),
                )
        for name in (FORM_DOCUMENT, FORM_TEXT):
            text = self.request.POST.get(name)
            if text and text.strip():
                return text
        return None

    # -- the two steps ----------------------------------------------------

    def post(self, request, *args, **kwargs):
        # ``coerce_user_choice``, not ``coerce``: the form offers "abort" and
        # "skip". "keep" is the event copy's strategy and skips the compiler
        # check inside ``resolve_definition``; a POST field must not be able to
        # turn that off (S-006).
        strategy = ResolutionStrategy.coerce_user_choice(
            request.POST.get(FORM_STRATEGY)
        )
        confirm = request.POST.get(FORM_ACTION) == ACTION_CONFIRM

        try:
            text = self._document_text()
            if text is None:
                messages.error(
                    request, _("Please choose a file or paste a report definition.")
                )
                return self.render_to_response(self.get_context_data())
            plan = plan_import(text, event=request.event, strategy=strategy)
        except PayloadRejected as e:
            messages.error(request, str(e))
            return self.render_to_response(self.get_context_data())
        except contracts.DefinitionValidationError as e:
            return self.render_to_response(
                self.get_context_data(
                    definition_issues=[
                        (issue.path, issue.message) for issue in e.issues
                    ],
                    document_text=text,
                )
            )

        if not confirm or not plan.ok:
            return self._render_confirmation(plan, text, attempted=confirm)

        try:
            report = commit_import(plan, user=request.user)
        except ImportRejected as e:  # pragma: no cover - plan.ok was just checked
            messages.error(request, str(e))
            return self._render_confirmation(plan, text, attempted=True)

        messages.success(request, _("The report has been imported."))
        return redirect(report_url(URL_NAME_EDIT, request.event, report=report.pk))

    def _render_confirmation(self, plan, text: str, attempted: bool):
        if attempted and not plan.ok:
            messages.error(
                self.request,
                _(
                    "This report cannot be imported as it is. Please choose how "
                    "to deal with the fields listed below."
                ),
            )
        ctx = self.get_context_data(
            plan=plan,
            report=plan.report,
            entries=plan.report.entries,
            document_text=text,
            strategy=plan.strategy,
            strategy_skip=ResolutionStrategy.SKIP,
            strategy_abort=ResolutionStrategy.ABORT,
        )
        return render(self.request, self.confirm_template_name, ctx)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
#
# ``urls.py`` belongs to the integrator (ORCHESTRIERUNG.md section 5); this list
# is the copy-ready hand-over, see
# handoff/requests/portability-dev-an-integrator-urls.md.

_EVENT_PREFIX = r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports"

portability_event_urlpatterns = [
    re_path(
        _EVENT_PREFIX + r"/reports/import/$",
        ReportImportView.as_view(),
        name=URL_NAME_IMPORT,
    ),
    re_path(
        _EVENT_PREFIX + r"/reports/(?P<report>\d+)/export/$",
        ReportExportView.as_view(),
        name=URL_NAME_EXPORT,
    ),
]
