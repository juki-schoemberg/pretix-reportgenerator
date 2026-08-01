# Owner from wave 2 on: exporter-dev (see ORCHESTRIERUNG.md section 5)
"""Saved reports as regular pretix exports.

One class, :class:`CustomReportExporter`. It makes every saved
:class:`~pretix_custom_reports.models.ReportDefinition` show up in the ordinary
export UI of an event and of an organizer -- and therefore, without a single
line of scheduling code of our own, in pretix' *scheduled exports*
(``ScheduledEventExport`` / ``ScheduledOrganizerExport``). That is the whole
point of subclassing ``ListExporter`` instead of writing a view: scheduling,
recipient management, retry accounting and mail delivery already exist and are
bound to registered exporters (CLAUDE.md rule 5).

What this module deliberately does **not** do
---------------------------------------------

* **No query logic.** Every row comes out of
  :class:`~pretix_custom_reports.query.compiler.ReportQueryCompiler`. This
  module never builds a ``Q()``, never names an ORM path and never touches
  ``Order``/``OrderPosition`` (CLAUDE.md rules 2 and 3).
* **No serialisation.** CSV and XLSX come from ``ListExporter``. That is not
  only convenience: the injection neutralisation lives there (see below).
* **No scheduler.** ``pretix.base.services.export.run_scheduled_exports`` runs
  us; we only have to be findable by ``export_identifier``.
* **No permission logic.** pretix decides which events we may see and hands them
  to us; see "Permissions" below.

Injection: already handled, verified, not duplicated
----------------------------------------------------

``ListExporter`` imports ``defusedcsv`` instead of the stdlib ``csv``
(pretix/base/exporter.py:42) and writes XLSX through ``SafeWorkbook``
(pretix/base/exporter.py:51-53, 298, 417). ``defusedcsv`` prefixes any cell
starting with ``@ + - = | %`` with an apostrophe (defusedcsv/csv.py:28-38);
``SafeWorkbook`` forces formula-looking cells to text and strips XML-illegal
characters (pretix/helpers/safe_openpyxl.py:39-84). A report cell contains
arbitrary attendee input, so this matters -- but re-escaping here would double
the apostrophes and corrupt honest data. So: nothing added, only asserted, in
``tests/test_exporters.py::test_csv_injection_is_neutralised_by_listexporter``
and its XLSX sibling.

The one thing that is *not* covered is the file name (api-notes section 2,
pitfall 3), which is why :meth:`CustomReportExporter.get_filename` sanitises.

Relative date filters are evaluated when the export *runs*
----------------------------------------------------------

``compiler.compile(definition, event)`` resolves ``relative_last_days`` and
friends against "now" at compile time (query/dates.py). We compile inside
:meth:`CustomReportExporter.iterate_list`, i.e. once per run, and store nothing
but the report's identifier in ``export_form_data``. A schedule saved in January
with "last 30 days" therefore means "the 30 days before *this* run" in July --
which is the only reading that makes a recurring export useful. Nothing in this
module is allowed to pre-compute a date at save time; the test
``test_relative_filter_is_evaluated_per_run_not_at_save_time`` runs the same
schedule twice under two frozen clocks and expects two different results.

Permissions, including in the background
----------------------------------------

We never widen what we were given. ``BaseExporter.__init__`` sets
``self.events``; for an organizer-level run that queryset is already restricted
to the events the acting account holds ``event.orders:read`` on
(``init_organizer_exporters``, pretix/base/services/export.py:238-283), and for
a scheduled run the acting account is ``schedule.owner``
(services/export.py:441-447, 471-477). Consequently **every** query in here goes
through ``self.events`` and never through ``self.organizer.events``. Same for
the report lookup: always ``ReportDefinition.objects.for_event(event)``, never a
global lookup by identifier -- identifiers are unique per event only, so a
global one would be a cross-organizer leak (ADR 0001 section 5.1).

We keep the inherited ``get_required_event_permission() == 'event.orders:read'``,
which is the key ``persistence-dev`` chose for reading and running reports.

django-scopes
-------------

Nothing here disables scopes and nothing opens one. Every caller already does:
``ProfiledEventTask``/``EventTask`` and ``OrganizerTask``/``OrganizerUserTask``
wrap the task body in ``scope(organizer=...)``
(pretix/base/services/tasks.py:87-135), the control backend does it in
middleware (pretix/control/middleware.py:199), and the ``export`` management
command does it explicitly (base/management/commands/export.py:65). Opening a
scope of our own would only paper over a future caller that forgot to.

Stored form data is untrusted
-----------------------------

``export_form_data`` is **not** revalidated when a schedule runs
(services/export.py:366-370): the exporter is handed raw JSON from the database,
written possibly months ago, possibly through the API. Everything read out of
*form_data* in here is therefore type-checked and range-checked before use, and
every failure becomes an ``ExportError`` -- see the next section.

Failure handling (the reason this module has so much error code)
----------------------------------------------------------------

If a scheduled export raises anything that is not ``ExportError``, pretix
retries the Celery task five times at 120 s and then mails the owner the words
"Internal Error" (services/export.py:392-397). Ten minutes of compute for a
message nobody can act on -- and after five such failures the schedule is
silently dropped from the periodic query (``error_counter__lt=5``,
services/export.py:502) with no further notification.

So the contract with ourselves is: *nothing* leaves this module except
``ExportError`` with a sentence naming the report and the event. That covers a
deleted report (the single most likely failure), a report whose fields no longer
resolve, a hand-edited ``_format``, and a hand-edited row limit. See ADR 0001
section 5.2 and handoff/requests/contract-architect-an-exporter-dev-exporterror.md.

Registration
------------

The two receivers below are plain functions on purpose. ``signals.py`` belongs
to the ``integrator``; the copy-ready lines are in
``handoff/requests/exporter-dev-an-integrator-signals.md``. Keeping the functions
*here* also keeps ``EventPluginSignal.connect``'s app check happy no matter
which module does the connecting, because it resolves the app from the
receiver's ``__module__`` (pretix/base/signals.py:64-88).
"""

from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import dataclasses
import logging
import re
from collections import OrderedDict
from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext, gettext_lazy as _, pgettext_lazy
from pretix.base.exporter import ListExporter
from pretix.base.models import Device, TeamAPIToken, User
from pretix.base.models.auth import UserWithStaffSession
from pretix.base.services.export import ExportError

from pretix_custom_reports import contracts
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.query.compiler import ReportQueryCompiler
from pretix_custom_reports.registry.library import field_registry

__all__ = [
    "CustomReportExporter",
    "EXPORT_FORMATS",
    "FORM_KEY_INCLUDE_CANCELED",
    "FORM_KEY_INCLUDE_TESTMODE",
    "FORM_KEY_ON_UNAVAILABLE",
    "FORM_KEY_REPORT",
    "FORM_KEY_ROW_LIMIT",
    "register_multievent_report_exporter",
    "register_report_exporter",
]

logger = logging.getLogger(__name__)


#: The four values ``ListExporter.render`` dispatches on
#: (pretix/base/exporter.py:328-336). There is no ``else`` branch there: any
#: other value makes ``render`` return ``None``, which pretix reports as "Your
#: export did not contain any data." -- a wrong and unactionable message. We
#: check the value ourselves, see :meth:`CustomReportExporter.render`.
#:
#: ODS is **not** in this list because pretix 2026.6.0 does not offer it;
#: ``ListExporter`` knows XLSX and three CSV dialects and nothing else. Adding
#: ODS would mean hand-rolling a serialiser, which CLAUDE.md rule 6 forbids.
EXPORT_FORMATS = ("xlsx", "default", "csv-excel", "semicolon")

#: Key holding the report reference in ``export_form_data``. Frozen contract
#: (``contracts.EXPORT_FORM_REPORT_KEY``), value ``"report"``. The value stored
#: under it is the report's stable ``identifier`` string, never a primary key --
#: a PK points into exactly one event and could not be resolved per event in a
#: multi-event export (ADR 0001 section 5.1).
FORM_KEY_REPORT = contracts.EXPORT_FORM_REPORT_KEY

FORM_KEY_INCLUDE_CANCELED = "include_canceled_positions"
FORM_KEY_INCLUDE_TESTMODE = "include_testmode_orders"
FORM_KEY_ROW_LIMIT = "row_limit"
FORM_KEY_ON_UNAVAILABLE = "on_unavailable"

#: Tri-state for the two boolean run-time overrides. Plain strings rather than
#: ``NullBooleanField`` because they survive the round trip through
#: ``export_form_data`` JSON and back through ``Field.to_python`` unchanged
#: (control/views/orders.py:2675-2685), and because "" is an unambiguous
#: "do not override" that a boolean cannot express.
OVERRIDE_UNSET = ""
OVERRIDE_YES = "yes"
OVERRIDE_NO = "no"

ON_UNAVAILABLE_SKIP = "skip"
ON_UNAVAILABLE_FAIL = "fail"

#: Characters allowed in the generated file name. ``get_filename()`` feeds
#: ``CachedFile.filename`` and the name of the mail attachment of a scheduled
#: export; the injection protection of ``ListExporter`` covers cell contents
#: only (docs/pretix-api-notes.md section 2, pitfall 3).
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


class _EventProblem(Exception):
    """A report could not be prepared for one event.

    Internal to this module. It exists so that the multi-event path can decide
    *afterwards* whether one unusable event is a skip or the end of the export,
    without the decision being buried in the place that detects the problem.
    Never escapes: it is either re-raised as :class:`ExportError` or collected.
    """

    def __init__(self, event: Any, message: str) -> None:
        self.event = event
        self.message = message
        super().__init__(message)


class CustomReportExporter(ListExporter):
    """Runs one saved report and writes it as CSV or XLSX.

    Bound to one event in the event export UI and to a set of events in the
    organizer export UI. Both cases go through the same code; the only
    difference is that the multi-event output gains two leading columns naming
    the event, because otherwise rows from four events would be
    indistinguishable.
    """

    identifier = "customreports"
    verbose_name = _("Custom report")
    category = pgettext_lazy("export_category", "Custom reports")
    description = _(
        "Run one of the reports you defined in the report editor. Relative date "
        'filters such as "last 30 days" are evaluated when the export runs, so '
        "a scheduled export keeps producing a current result."
    )

    #: A report can select six-digit position counts. ``BaseExporter`` defaults
    #: this to ``True``, which would hold a REPEATABLE READ transaction open for
    #: the whole run; the base class docstring itself recommends turning it off
    #: for long exports (pretix/base/exporter.py:121-131), and the core's own
    #: ``WaitingListExporter`` does.
    repeatable_read = False

    # -- form ---------------------------------------------------------------

    @property
    def additional_form_fields(self) -> "OrderedDict[str, forms.Field]":
        """Report selection, run-time overrides, multi-event failure policy.

        ``additional_form_fields`` rather than ``export_form_fields``: the base
        class builds the ``_format`` choice in ``export_form_fields`` and
        ``render`` dispatches on it, so overriding that property removes the
        format selection and breaks the dispatch (docs/pretix-api-notes.md
        section 1, pitfall 4).
        """
        fields: "OrderedDict[str, forms.Field]" = OrderedDict()
        fields[FORM_KEY_REPORT] = forms.ChoiceField(
            label=_("Report"),
            # Callable: this hits the database, and ``export_form_fields`` is
            # read on every exporter instance the export page builds, including
            # the ones nobody opens.
            choices=self.report_choices,
            required=True,
            help_text=(
                _(
                    "Reports are matched by their internal identifier, so the same "
                    "report can be exported across several events."
                )
                if self.is_multievent
                else _("One of the reports saved for this event.")
            ),
        )
        fields[FORM_KEY_INCLUDE_CANCELED] = forms.ChoiceField(
            label=_("Include canceled positions"),
            choices=self._override_choices(),
            required=False,
            help_text=_(
                "Overrides the setting saved with the report, for this run only."
            ),
        )
        fields[FORM_KEY_INCLUDE_TESTMODE] = forms.ChoiceField(
            label=_("Include test mode orders"),
            choices=self._override_choices(),
            required=False,
            help_text=_(
                "Overrides the setting saved with the report, for this run only."
            ),
        )
        fields[FORM_KEY_ROW_LIMIT] = forms.IntegerField(
            label=_("Maximum number of rows"),
            required=False,
            min_value=1,
            max_value=contracts.MAX_ROW_LIMIT,
            help_text=_(
                "Leave empty to use the limit saved with the report. Scheduled "
                "exports are capped at 20 MB by pretix, so a large report needs a "
                "limit here to arrive at all."
            ),
        )
        if self.is_multievent:
            fields[FORM_KEY_ON_UNAVAILABLE] = forms.ChoiceField(
                label=_("If an event cannot supply this report"),
                required=False,
                choices=(
                    (ON_UNAVAILABLE_SKIP, _("Skip the event and export the rest")),
                    (ON_UNAVAILABLE_FAIL, _("Fail the whole export")),
                ),
                initial=ON_UNAVAILABLE_SKIP,
                help_text=_(
                    "An event may not have this report at all, or a column may "
                    "refer to a question that does not exist there."
                ),
            )
        return fields

    @staticmethod
    def _override_choices() -> Tuple[Tuple[str, Any], ...]:
        return (
            (OVERRIDE_UNSET, _("Use the setting saved with the report")),
            (OVERRIDE_YES, _("Yes")),
            (OVERRIDE_NO, _("No")),
        )

    def report_choices(self) -> List[Tuple[str, str]]:
        """``(identifier, label)`` for every report reachable in this context.

        Event level: the reports of that event. Organizer level: the reports of
        every event we were handed, collapsed by identifier -- that is what a
        multi-event export selects on, and what survives an event copy
        (ADR 0001 section 5.1).

        Deliberately built from ``self.events`` rather than from
        ``self.organizer.events``: the former is already restricted to the
        events the acting account may read.
        """
        reports = (
            ReportDefinition.objects.filter(event__in=self.events)
            .order_by("name", "identifier", "pk")
            .values_list("identifier", "name")
        )
        if not self.is_multievent:
            return [(identifier, name) for identifier, name in reports]

        # Same identifier in several events is the normal case (event copy,
        # organizer template), and the names may legitimately differ. Show the
        # identifier so the choice is unambiguous.
        seen: Dict[str, str] = {}
        for identifier, name in reports:
            seen.setdefault(identifier, name)
        return [
            (identifier, "{} ({})".format(name, identifier))
            for identifier, name in seen.items()
        ]

    # -- rendering ----------------------------------------------------------

    def render(self, form_data: dict, output_file=None):
        """Check ``_format`` before the base class silently gives up on it.

        ``ListExporter.render`` has no ``else`` branch: an unknown ``_format``
        returns ``None``, which the calling task turns into "Your export did not
        contain any data." (services/export.py:106-109). For an interactive run
        that is merely confusing; for a scheduled run, whose ``export_form_data``
        is never revalidated, it is a wrong diagnosis that costs the owner a
        long search.
        """
        fmt = form_data.get("_format")
        if fmt not in EXPORT_FORMATS:
            raise ExportError(
                gettext(
                    'This export cannot be written in the format "{format}". '
                    "Please open the export configuration and pick a format again."
                ).format(format=fmt)
            )
        return super().render(form_data, output_file=output_file)

    def iterate_list(self, form_data: dict) -> Iterator[Any]:
        """Header row, progress total, then one list per report row.

        Compiles **once per event**: a ``CompiledReport`` belongs to exactly one
        event (handoff/status/query-dev.md, "Nächster Schritt" 1). All events are
        compiled up front, before the first row is yielded, for two reasons:
        the header row has to be written first, and a failure that is going to
        abort the export should abort it before half a file has been produced.
        Compiling builds querysets, it does not execute them, so this is cheap.
        """
        identifier = self._read_identifier(form_data)
        overrides = self._read_overrides(form_data)
        skip_unavailable = self._read_skip_unavailable(form_data)

        events = list(self.events.order_by("date_from", "slug", "pk"))
        if not events:
            raise ExportError(gettext("There is no event you may run this export for."))

        prepared: List[Tuple[Any, ReportDefinition, Any]] = []
        problems: List[str] = []
        expected_width: Optional[int] = None

        for event in events:
            try:
                report, compiled = self._prepare(event, identifier, overrides)
                width = len(compiled.headers())
                if expected_width is None:
                    expected_width = width
                elif width != expected_width:
                    # Two events can hold different reports under the same
                    # identifier (identifiers are unique per event only). Mixing
                    # them would produce a ragged file in which nobody could
                    # tell which column is which.
                    raise _EventProblem(
                        event,
                        gettext(
                            'The report "{identifier}" has {found} columns in event '
                            "{event}, but {expected} in the events before it."
                        ).format(
                            identifier=identifier,
                            event=event.slug,
                            found=width,
                            expected=expected_width,
                        ),
                    )
            except _EventProblem as problem:
                if not skip_unavailable:
                    raise ExportError(problem.message)
                logger.warning(
                    "pretix_custom_reports: skipping event %s in export of report "
                    "%r: %s",
                    getattr(problem.event, "slug", "?"),
                    identifier,
                    problem.message,
                )
                problems.append(problem.message)
                continue
            prepared.append((event, report, compiled))

        if not prepared:
            # Everything failed. Never return an empty result here: pretix would
            # turn that into ExportEmptyError, which is a *soft* failure and does
            # not increase the error counter (services/export.py:371-374,
            # 388-389) -- a schedule pointing at a deleted report would then mail
            # "no data" forever instead of naming the problem once.
            raise ExportError(
                gettext("This report could not be run for any of the selected events.")
                + " "
                + " ".join(problems)
            )

        self._last_report_identifier = identifier

        headers = list(prepared[0][2].headers())
        if self.is_multievent:
            headers = [gettext("Event slug"), gettext("Event name")] + headers
        yield headers

        yield self.ProgressSetTotal(total=self._total_rows(prepared))

        fmt = form_data.get("_format")
        for event, report, compiled in prepared:
            prefix = [event.slug, str(event.name)] if self.is_multievent else []
            rows = 0
            for row in compiled.iter_rows(chunk_size=contracts.DEFAULT_CHUNK_SIZE):
                rows += 1
                yield prefix + row
            self._log_execution(report, rows=rows, fmt=fmt)

    # -- per event ----------------------------------------------------------

    def _prepare(
        self, event: Any, identifier: str, overrides: Dict[str, Any]
    ) -> Tuple[ReportDefinition, Any]:
        """Look the report up in *event* and compile it. Never raises anything else.

        :raises _EventProblem: the report is gone, its stored JSON no longer
            validates, or a field it uses does not exist in this event.
        """
        try:
            report = (
                ReportDefinition.objects.for_event(event)
                .by_identifier(identifier)
                .get()
            )
        except ObjectDoesNotExist:
            # The failure this whole module is shaped around. Without this
            # branch, the DoesNotExist would travel into the generic handler in
            # services/export.py:392-397 and become five retries plus the word
            # "Internal Error" (api-notes section 5.6, case B).
            raise _EventProblem(
                event,
                gettext(
                    'The report "{identifier}" does not exist in event {event}. '
                    "It was probably deleted or renamed after this export was "
                    "configured."
                ).format(identifier=identifier, event=event.slug),
            )

        try:
            definition = self._with_overrides(report.validated_definition(), overrides)
            compiled = self.compiler.compile(definition, event)
        except contracts.ContractError as e:
            # FieldResolutionError (a question that exists in event A but not in
            # event B), CompilationError, DefinitionValidationError,
            # FieldContractError -- one base class covers all of them
            # (ADR 0001 section 5.2).
            raise _EventProblem(
                event,
                gettext(
                    'The report "{name}" cannot be run for event {event}: {error}'
                ).format(name=report.name, event=event.slug, error=str(e)),
            )
        return report, compiled

    @staticmethod
    def _with_overrides(
        definition: contracts.ReportDefinition, overrides: Dict[str, Any]
    ) -> contracts.ReportDefinition:
        """Apply the run-time overrides to a copy of the stored definition.

        ``dataclasses.replace`` because the contract types are frozen -- the
        stored definition must not be mutated, it is going to be compiled again
        for the next event.
        """
        if not overrides:
            return definition
        options = dataclasses.replace(definition.options, **overrides)
        return dataclasses.replace(definition, options=options)

    @staticmethod
    def _total_rows(prepared: Sequence[Tuple[Any, Any, Any]]) -> int:
        """Row total for the progress bar; never a reason to fail the export."""
        total = 0
        for _event, _report, compiled in prepared:
            try:
                total += compiled.count()
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "pretix_custom_reports: could not count rows for the progress bar"
                )
                return 0
        return total

    # -- reading untrusted form data ----------------------------------------

    def _read_identifier(self, form_data: dict) -> str:
        raw = form_data.get(FORM_KEY_REPORT)
        try:
            return contracts.validate_identifier(raw)
        except ValueError:
            raise ExportError(
                gettext(
                    "No report was selected for this export, or the stored "
                    "selection is not a valid report identifier."
                )
            )

    def _read_overrides(self, form_data: dict) -> Dict[str, Any]:
        overrides: Dict[str, Any] = {}
        for key in (FORM_KEY_INCLUDE_CANCELED, FORM_KEY_INCLUDE_TESTMODE):
            raw = form_data.get(key)
            if raw in (None, OVERRIDE_UNSET):
                continue
            if raw not in (OVERRIDE_YES, OVERRIDE_NO):
                raise ExportError(
                    gettext(
                        'The stored value "{value}" for option "{option}" is not '
                        "one of yes/no."
                    ).format(value=raw, option=key)
                )
            overrides[key] = raw == OVERRIDE_YES

        raw_limit = form_data.get(FORM_KEY_ROW_LIMIT)
        if raw_limit not in (None, ""):
            # bool is an int in Python; a JSON ``true`` here would otherwise
            # become a limit of one row.
            if isinstance(raw_limit, bool) or not isinstance(raw_limit, (int, str)):
                raise ExportError(self._bad_row_limit(raw_limit))
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                raise ExportError(self._bad_row_limit(raw_limit))
            if not 1 <= limit <= contracts.MAX_ROW_LIMIT:
                raise ExportError(self._bad_row_limit(raw_limit))
            overrides[FORM_KEY_ROW_LIMIT] = limit
        return overrides

    @staticmethod
    def _bad_row_limit(value: Any) -> str:
        return gettext(
            'The stored row limit "{value}" is not a whole number between 1 and '
            "{maximum}."
        ).format(value=value, maximum=contracts.MAX_ROW_LIMIT)

    def _read_skip_unavailable(self, form_data: dict) -> bool:
        """Should an event without a usable report be skipped?

        Never at event level: there is exactly one event there, so "skip it"
        would mean "produce an empty file and say nothing", which is precisely
        the silent failure this plugin is supposed to avoid.
        """
        if not self.is_multievent:
            return False
        return form_data.get(FORM_KEY_ON_UNAVAILABLE, ON_UNAVAILABLE_SKIP) != (
            ON_UNAVAILABLE_FAIL
        )

    # -- output metadata ----------------------------------------------------

    def get_filename(self) -> str:
        """``<slug>_<report>`` -- sanitised, because this is not a cell.

        The identifier is picked up in :meth:`iterate_list`; both render paths
        consume the generator completely before calling this
        (pretix/base/exporter.py:253-292 and 297-326), so it is set by then. If
        it is not, we fall back rather than guess.
        """
        if self.is_multievent:
            first = self.events.first()
            slug = (
                self.organizer.slug
                if self.organizer
                else (first.slug if first else "export")
            )
        else:
            slug = self.event.slug
        name = "{}_report".format(slug)
        identifier = getattr(self, "_last_report_identifier", None)
        if identifier:
            name = "{}_{}".format(name, _FILENAME_SAFE.sub("-", identifier)[:60])
        return name

    # -- audit log ----------------------------------------------------------

    def _log_execution(self, report: ReportDefinition, rows: int, fmt: Any) -> None:
        """Write ``pretix_custom_reports.report.executed``.

        Failure to log must not fail the export: ``log_action`` also enqueues
        notification and webhook tasks, so a broker outage would otherwise turn
        every export into a failed one. It is logged loudly instead of being
        swallowed.
        """
        user, auth = self._actor()
        try:
            report.log_executed(
                user=user,
                auth=auth,
                data={
                    "row_count": rows,
                    "format": fmt if isinstance(fmt, str) else None,
                    "exporter": self.identifier,
                    "multievent": self.is_multievent,
                },
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "pretix_custom_reports: could not write the execution log entry "
                "for report %r",
                report.identifier,
            )

    def _actor(self) -> Tuple[Optional[User], Optional[Any]]:
        """Split ``permission_holder`` into ``log_action``'s ``user``/``auth``.

        ``permission_holder`` is a ``User``, a ``TeamAPIToken``, a ``Device``, a
        ``UserWithStaffSession`` wrapper (services/export.py:216) -- or, from the
        ``export`` management command, a progress callback, because that command
        passes it positionally into the wrong parameter
        (base/management/commands/export.py:108). Hence the explicit type checks
        and no ``else`` that assumes anything.
        """
        holder = self.permission_holder
        if isinstance(holder, UserWithStaffSession):
            holder = holder.user
        if isinstance(holder, User):
            return holder, None
        if isinstance(holder, (TeamAPIToken, Device)):
            return None, holder
        return None, None

    # -- collaborators ------------------------------------------------------

    @property
    def compiler(self) -> ReportQueryCompiler:
        """The one and only way rows are produced (CLAUDE.md rule 2).

        A property rather than a constructor argument because pretix
        instantiates exporters itself and we must not change the signature; a
        test that wants a different registry replaces this attribute.
        """
        if getattr(self, "_compiler", None) is None:
            self._compiler = ReportQueryCompiler(field_registry())
        return self._compiler

    @compiler.setter
    def compiler(self, value: ReportQueryCompiler) -> None:
        self._compiler = value


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
#
# Receivers, not decorated. ``pretix_custom_reports/signals.py`` belongs to the
# integrator; the two ``connect()`` calls are in
# handoff/requests/exporter-dev-an-integrator-signals.md, copy ready.


def register_report_exporter(sender, **kwargs):
    """Receiver for ``pretix.base.signals.register_data_exporters``.

    ``sender`` is the ``Event``. Returns the class, not an instance --
    ``init_event_exporters`` instantiates (services/export.py:198-222).
    """
    return CustomReportExporter


def register_multievent_report_exporter(sender, **kwargs):
    """Receiver for ``pretix.base.signals.register_multievent_data_exporters``.

    ``sender`` is the ``Organizer``. That signal is an
    ``OrganizerPluginSignal(allow_legacy_plugins=True)`` and this plugin is
    event level, so connecting emits a ``DeprecationWarning`` and the exporter is
    considered active for every organizer (pretix/base/plugins.py:107-113). The
    consequence is harmless and deliberate: the export appears in the organizer
    UI even for an organizer that has never enabled the plugin, where it then
    offers an empty list of reports.
    """
    return CustomReportExporter
