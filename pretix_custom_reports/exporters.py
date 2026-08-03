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

Column formats live here, in a layer of their own (finding T-001)
------------------------------------------------------------------

``ColumnFormat.date_style``, ``number_style`` and ``boolean_style`` used to be
applied by the preview and by nobody else, so "date only" showed a date on
screen and a full timestamp in the file. The fix is *not* to move formatting
into the query compiler: it hands back ``Decimal``/``datetime`` on purpose, and
that is the only reason XLSX contains real numbers and real dates instead of
text that merely looks like them (see the docstring of ``NumberStyle.RAW``).

So formatting is a third layer, and it lives here as two module-level functions
that anyone may import:

* :func:`format_cell_value` -- value plus format in, display **string** out.
  This is the shared one; ``views/api.py`` (preview) calls the same function, so
  "the preview does not promise anything the file cannot keep" is a property of
  the code rather than of two implementations agreeing by accident.
* :func:`format_export_cell` -- the *policy* the export applies on top: format
  only what the definition explicitly asked to be formatted, and hand every
  other value through **untouched and natively typed**.

That policy is what keeps two promises at once. A report saved before this
existed has no styles set, so every one of its cells still travels as the raw
value it always did; and ``NumberStyle.RAW`` keeps meaning what its docstring
says, a real number in the spreadsheet, because it is the one style that is
deliberately *not* rendered to a string on the way out.

``ColumnFormat.separator`` is not handled here. It is applied by the compiler
(``query/columns.py``), which is the only place that can: it joins the values of
a one-to-many relation *before* they ever become a cell.

One thing the *output format* decides, though, and only one:
:func:`as_spreadsheet_value` strips the timezone off an aware ``datetime`` on
the XLSX path, because openpyxl refuses to write one at all. Not on the CSV
path, where an aware datetime is written correctly and changing it would only
alter the bytes of every existing report.

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

Permission is not the only gate, though. ``self.events`` is filtered by
*permission* only, never by whether this plugin is switched on for an event --
and on organizer level nothing else filters it either, because
``register_multievent_data_exporters`` is an
``OrganizerPluginSignal(allow_legacy_plugins=True)`` and therefore hands an
event-level plugin to *every* organizer (pretix/base/signals.py:100-113). Without
a check of our own, an event whose administrator switched the plugin off would
keep feeding order data into an organizer export -- and, through
``ScheduledOrganizerExport``, into a recurring mail. So every event that reaches
:meth:`CustomReportExporter.report_choices` or
:meth:`CustomReportExporter._prepare` is checked with
:meth:`CustomReportExporter._plugin_is_active`, which is the very test pretix
performs before delivering an event-level plugin signal
(``app.name in event.get_plugins()``, signals.py:100-103). Not
``plugins__contains``: that is a substring match and would also accept an event
carrying a *longer* plugin name that merely starts with ours.

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
import datetime
import decimal
import logging
import re
from collections import OrderedDict
from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.utils import formats, timezone
from django.utils.translation import gettext, gettext_lazy as _, pgettext_lazy
from pretix.base.exporter import ListExporter
from pretix.base.models import Device, TeamAPIToken, User
from pretix.base.models.auth import UserWithStaffSession
from pretix.base.services.export import ExportError

from pretix_custom_reports import contracts
from pretix_custom_reports.contracts import (
    BooleanStyle,
    ColumnFormat,
    DataType,
    DateStyle,
    NumberStyle,
)
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.query.compiler import ReportQueryCompiler
from pretix_custom_reports.registry.library import field_registry

__all__ = [
    "CustomReportExporter",
    "EXPORT_FORMATS",
    "FORMAT_XLSX",
    "FORM_KEY_INCLUDE_CANCELED",
    "FORM_KEY_INCLUDE_TESTMODE",
    "FORM_KEY_ON_UNAVAILABLE",
    "FORM_KEY_REPORT",
    "FORM_KEY_ROW_LIMIT",
    "as_spreadsheet_value",
    "format_cell_value",
    "format_export_cell",
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
#:
#: :data:`FORMAT_XLSX` is named because it is the one value that changes what a
#: cell may contain, see :func:`as_spreadsheet_value`.
FORMAT_XLSX = "xlsx"
EXPORT_FORMATS = (FORMAT_XLSX, "default", "csv-excel", "semicolon")

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


# ---------------------------------------------------------------------------
# Cell formatting (finding T-001)
# ---------------------------------------------------------------------------
#
# Public on purpose: the preview in views/api.py renders the same cells and must
# render them the same way. Two functions rather than one, because "how does a
# value look once it is a string" and "which values does the export turn into
# strings at all" are two different questions with two different answers.


def format_cell_value(
    value: Any,
    fmt: Optional[ColumnFormat],
    datatype: Optional[DataType] = None,
    event: Optional[Any] = None,
) -> str:
    """Render one cell value as a display string, honouring *fmt*.

    The single formatting implementation of this plugin. Called by the exporter
    (through :func:`format_export_cell`) and by the editor preview, so that what
    a user sees on screen is what lands in the file.

    :param value: the raw cell value the compiler produced: ``None``, ``str``,
        ``bool``, ``int``, ``float``, ``Decimal``, ``date``, ``datetime`` or
        ``time``.
    :param fmt: the column's
        :class:`~pretix_custom_reports.contracts.ColumnFormat`, or ``None``.
        Read with ``getattr``, so any object carrying the three style attributes
        works and ``None`` means "every style at its default".
    :param datatype: the column's
        :class:`~pretix_custom_reports.contracts.DataType`. Only consulted to
        decide whether an unstyled number is money.
    :param event: the event the row belongs to; supplies the timezone for aware
        datetimes and the currency for money. May be ``None``, in which case
        both fall back to a plain rendering rather than failing.
    :return: always a string. ``None`` becomes ``""``.

    Note what this does **not** do: it never keeps a native type. A caller that
    needs ``Decimal``/``datetime`` to survive -- XLSX does -- must decide *not to
    call it*, which is exactly what :func:`format_export_cell` decides.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value

    if isinstance(value, bool):
        style = getattr(fmt, "boolean_style", None)
        if style is BooleanStyle.TRUE_FALSE:
            return "true" if value else "false"
        if style is BooleanStyle.ONE_ZERO:
            return "1" if value else "0"
        return str(_("Yes")) if value else str(_("No"))

    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return _format_temporal(value, getattr(fmt, "date_style", None), event)

    if isinstance(value, (decimal.Decimal, int, float)):
        return _format_number(
            value, getattr(fmt, "number_style", None), datatype, event
        )

    return str(value)


def format_export_cell(
    value: Any,
    fmt: Optional[ColumnFormat],
    datatype: Optional[DataType] = None,
    event: Optional[Any] = None,
) -> Any:
    """Apply *fmt* to *value* for a file -- or hand the value back untouched.

    Same arguments as :func:`format_cell_value`; the difference is the return
    value, which is a formatted ``str`` only where the definition asked for one
    and the **unchanged input** everywhere else.

    Untouched means untouched, and each of the four cases below is a promise to
    somebody:

    * *No style for this value's type* -- including a ``fmt`` of ``None``, i.e.
      every report saved before column formats reached the export. Its files do
      not change.
    * :attr:`~pretix_custom_reports.contracts.NumberStyle.RAW` -- the one style
      whose documented meaning is "do not make a string out of me", so that XLSX
      keeps a number a spreadsheet can add up.
    * ``None`` -- stays ``None``, which CSV writes as an empty field and XLSX as
      an empty cell. Turning it into ``""`` would fill an XLSX with strings.
    * ``str`` -- already a string; the compiler formatted it (a ``join`` column
      with its separator), and re-rendering it would be a second opinion.
    """
    if not _style_applies(value, fmt):
        return value
    try:
        return format_cell_value(value, fmt, datatype, event)
    except Exception:
        # A stored definition can pair a style with a datatype it does not fit
        # -- ``date_only`` on a time-of-day column, say -- and a definition is
        # untrusted input that nothing revalidates on the way to a scheduled
        # run. An unformatted cell is a cosmetic defect; an exception here would
        # be five Celery retries and the word "Internal Error"
        # (services/export.py:392-397).
        logger.warning(
            "pretix_custom_reports: could not apply the column format %r to a "
            "value of type %s, exporting it unformatted",
            fmt,
            type(value).__name__,
            exc_info=True,
        )
        return value


def as_spreadsheet_value(value: Any, event: Optional[Any] = None) -> Any:
    """Make one cell value acceptable to openpyxl. XLSX path only.

    openpyxl refuses a timezone-aware ``datetime`` outright -- *"Excel does not
    support timezones in datetimes. The tzinfo in the datetime/time object must
    be set to None"* -- and ``ListExporter._render_xlsx`` hands our cell values
    to ``ws.append`` unchanged (pretix/base/exporter.py:305-311). Our rows are
    aware, because ``USE_TZ`` is on and the compiler returns native types on
    purpose. Without this function, **every** XLSX export of a report with a
    date column raised ``TypeError``: not an ``ExportError`` naming the problem,
    but five Celery retries and the word "Internal Error", and after five of
    those the schedule drops out of the periodic query unnoticed
    (services/export.py:392-397, 502).

    So an aware value is converted to *event-local* time and then stripped of
    its ``tzinfo``. Local rather than UTC because the cell can no longer say
    which zone it is in, and the event's own zone is the only one its organizer
    reads a spreadsheet in -- and because
    :func:`format_cell_value` localises the same value the same way, so a styled
    and an unstyled datetime column of one report show the same wall clock.

    Everything else is returned unchanged, including naive datetimes,
    ``date`` (openpyxl accepts those) and every non-temporal type.

    Deliberately *not* applied to the CSV path: there an aware ``datetime`` is
    written as ``2026-04-24 09:00:00+00:00`` and works, so touching it would
    change the bytes of every existing report for no gain.
    """
    if isinstance(value, datetime.datetime):
        if not timezone.is_aware(value):
            return value
        try:
            value = timezone.localtime(value, event.timezone)
        except Exception:  # pragma: no cover - defensive
            # No event, or a broken timezone setting. Dropping the tzinfo off
            # the value we already have keeps the export alive; the cell then
            # shows UTC, which is wrong by hours and not by a crash.
            logger.warning(
                "pretix_custom_reports: could not localise a datetime for the "
                "XLSX export, writing it as it arrived",
                exc_info=True,
            )
        return value.replace(tzinfo=None)
    if isinstance(value, datetime.time) and value.tzinfo is not None:
        # A time carries no date, so there is nothing to convert it *by*; the
        # zone can only be dropped. Rare -- no core field produces one -- but a
        # third-party registry field may.
        return value.replace(tzinfo=None)
    return value


def _style_applies(value: Any, fmt: Optional[ColumnFormat]) -> bool:
    """Does *fmt* say anything about a value of this type? See above for why."""
    if fmt is None or value is None or isinstance(value, str):
        return False
    if isinstance(value, bool):  # before int: bool is an int in Python
        return getattr(fmt, "boolean_style", None) is not None
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return getattr(fmt, "date_style", None) is not None
    if isinstance(value, (decimal.Decimal, int, float)):
        style = getattr(fmt, "number_style", None)
        return style is not None and style is not NumberStyle.RAW
    return False


def _has_style(fmt: Optional[ColumnFormat]) -> bool:
    """True if *fmt* sets any of the three styles.

    ``separator`` alone does not count: the compiler has already applied it by
    the time a value reaches us.
    """
    return fmt is not None and any(
        (
            getattr(fmt, "date_style", None),
            getattr(fmt, "number_style", None),
            getattr(fmt, "boolean_style", None),
        )
    )


def _format_temporal(value: Any, style: Any, event: Any) -> str:
    if isinstance(value, datetime.datetime) and timezone.is_aware(value):
        try:
            value = timezone.localtime(value, event.timezone)
        except Exception:  # pragma: no cover - defensive
            pass
    is_datetime = isinstance(value, datetime.datetime)
    is_time = isinstance(value, datetime.time) and not is_datetime

    if style is DateStyle.ISO:
        return value.isoformat()
    if style is DateStyle.TIME_ONLY or (is_time and style is None):
        return formats.date_format(value, "TIME_FORMAT")
    if style is DateStyle.DATE_ONLY:
        return formats.date_format(value, "SHORT_DATE_FORMAT")
    if style is DateStyle.SHORT:
        return formats.date_format(
            value, "SHORT_DATETIME_FORMAT" if is_datetime else "SHORT_DATE_FORMAT"
        )
    if style is DateStyle.LONG:
        return formats.date_format(value, "l, j F Y H:i" if is_datetime else "l, j F Y")
    return formats.date_format(
        value, "DATETIME_FORMAT" if is_datetime else "DATE_FORMAT"
    )


def _format_number(value: Any, style: Any, datatype: Any, event: Any) -> str:
    if style is NumberStyle.CURRENCY or (style is None and datatype is DataType.MONEY):
        try:
            from pretix.base.templatetags.money import money_filter

            return money_filter(decimal.Decimal(str(value)), event.currency)
        except Exception:  # pragma: no cover - defensive
            return str(value)
    if style is NumberStyle.LOCALIZED:
        return formats.number_format(value, use_l10n=True)
    return str(value)


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

    #: Our Django app label, exactly as it appears in ``Event.plugins`` (see
    #: ``apps.PluginApp.name``). Same constant and same comparison the view
    #: modules use in their ``PluginActiveMixin``; kept as a class attribute so a
    #: test can point it elsewhere without patching a module global.
    plugin_module = "pretix_custom_reports"

    # -- plugin gate --------------------------------------------------------

    def _plugin_is_active(self, event: Any) -> bool:
        """Is this plugin switched on for *event*?

        The check pretix itself performs before it delivers an event-level
        plugin signal (``is_app_active``, pretix/base/signals.py:91-113):
        membership in ``Event.get_plugins()``, which splits the stored
        comma-separated list (base/models/event.py:794-800). Deliberately not a
        ``plugins__contains`` query -- that matches substrings and would count a
        hypothetical ``pretix_custom_reports_extra`` as us.

        On event level this can only ever be ``True`` when pretix built us,
        because ``register_data_exporters`` is an ``EventPluginSignal`` and does
        not even fire for an event without the plugin. On organizer level it is
        the only gate there is.
        """
        return self.plugin_module in event.get_plugins()

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
        events the acting account may read. That restriction is about
        *permission* only, so events without the plugin are dropped here as well
        -- offering a report that :meth:`_prepare` is going to refuse would be a
        choice that cannot be honoured.
        """
        events = [event for event in self.events if self._plugin_is_active(event)]
        reports = (
            ReportDefinition.objects.filter(event__in=events)
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
        # The only place the output format changes what a *cell* may hold: a
        # spreadsheet cannot take a timezone-aware datetime, a CSV file can.
        for_spreadsheet = fmt == FORMAT_XLSX
        for event, report, compiled in prepared:
            prefix = [event.slug, str(event.name)] if self.is_multievent else []
            # Once per event, not once per row: the pairing is a property of the
            # definition, and a six-digit report would otherwise rebuild it a
            # six-digit number of times.
            cell_formats = self._cell_formats(compiled)
            rows = 0
            for row in compiled.iter_rows(chunk_size=contracts.DEFAULT_CHUNK_SIZE):
                rows += 1
                if cell_formats is not None:
                    row = [
                        format_export_cell(value, column_format, datatype, event)
                        for value, (column_format, datatype) in zip(row, cell_formats)
                    ]
                if for_spreadsheet:
                    # After the formatting, not before: a styled cell is a
                    # string by now and passes through untouched.
                    row = [as_spreadsheet_value(value, event) for value in row]
                yield prefix + row
            self._log_execution(report, rows=rows, fmt=fmt)

    # -- per event ----------------------------------------------------------

    def _prepare(
        self, event: Any, identifier: str, overrides: Dict[str, Any]
    ) -> Tuple[ReportDefinition, Any]:
        """Look the report up in *event* and compile it. Never raises anything else.

        :raises _EventProblem: the plugin is switched off for this event, the
            report is gone, its stored JSON no longer validates, or a field it
            uses does not exist in this event.
        """
        if not self._plugin_is_active(event):
            # Checked *before* the report lookup, not after: an event whose
            # administrator switched the plugin off must not have its reports
            # read at all, let alone its orders. On organizer level this is the
            # only thing standing between a leftover report and a recurring mail
            # -- self.events is permission-filtered, nothing more (see the
            # "Permissions" section of the module docstring).
            raise _EventProblem(
                event,
                gettext(
                    'The report "{identifier}" cannot be exported for event '
                    "{event}: the plugin is not enabled for this event."
                ).format(identifier=identifier, event=event.slug),
            )

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
    def _cell_formats(
        compiled: Any,
    ) -> Optional[List[Tuple[Optional[ColumnFormat], Any]]]:
        """Pair every output column with its ``ColumnFormat`` and its datatype.

        Returns ``None`` -- meaning "yield the rows exactly as the compiler
        produced them" -- when no column of this report sets a style. That is
        the overwhelmingly common case, and it makes the old code path the
        literal old code path rather than a formatting pass that happens to be a
        no-op.

        The pairing is by *position among the visible columns*, the same way the
        preview does it (``views/api.py::_formats_by_index``):
        ``CompiledReport.columns`` has hidden columns dropped already, so it does
        not line up index-for-index with ``definition.columns``. The definition
        read here is the compiled one, so the run-time overrides are included.
        """
        definition = getattr(compiled, "definition", None)
        if definition is None:  # pragma: no cover - defensive
            return None
        visible = [column for column in definition.columns if not column.hidden]
        pairs: List[Tuple[Optional[ColumnFormat], Any]] = []
        for index, column in enumerate(compiled.columns):
            column_format = visible[index].format if index < len(visible) else None
            pairs.append((column_format, column.datatype))
        if not any(_has_style(column_format) for column_format, _datatype in pairs):
            return None
        return pairs

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
    export therefore appears in the organizer UI even for an organizer that has
    never enabled the plugin.

    That is *not* harmless by itself, and this docstring used to claim it was
    (security review S-002): the events the exporter is handed are filtered by
    permission only, so the per-event gate has to be ours.
    :meth:`CustomReportExporter._plugin_is_active` is that gate; it applies to
    the choices in the form and to every event of a run, so an organizer with no
    plugin-enabled event sees an empty report list and can export nothing.
    """
    return CustomReportExporter
