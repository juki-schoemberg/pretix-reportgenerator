"""Relative date filters, resolved in the **event's** timezone.

Owner from wave 1 on: query-dev (ORCHESTRIERUNG.md section 5).

Why this module is not a convenience feature
--------------------------------------------

A scheduled report with a fixed date range produces nonsense from its second run
on: "orders between 2026-07-01 and 2026-07-31" is a July report forever. The six
relative operators in :class:`~pretix_custom_reports.contracts.fields.Operator`
exist so that a schedule keeps meaning the same thing every time it fires
(SPEC.md F6/F8, docs/adr/0001-contracts.md section 9).

Why the event timezone and not the server timezone
--------------------------------------------------

"Today" is a calendar concept and calendars are local. Three different timezones
are in play when a scheduled export runs:

* ``settings.TIME_ZONE`` -- the server default, irrelevant to the organizer,
* ``schedule.tz`` -- pretix activates this via ``override(schedule.tz)`` while
  running a scheduled export (docs/pretix-api-notes.md section 5, pitfall 7), so
  ``django.utils.timezone.localtime()`` follows the *schedule*, not the event,
* ``event.timezone`` -- ``Event.settings.timezone``
  (pretix/base/models/event.py:233-235).

We resolve against ``event.timezone`` because the report is about *this event's*
data and must not change its meaning when somebody edits a schedule or moves the
server. Using the ambient current timezone would make the same report return
different rows depending on who triggered it, which is the kind of bug nobody
finds for months.

Interval conventions
--------------------

Datetime fields get a **half-open** interval ``[start, end)``, exactly like
``pretix.base.timeframes.resolve_timeframe_to_datetime_start_inclusive_end_exclusive``
(pretix/base/timeframes.py, end of file). Half-open is the only convention that
cannot lose or double-count a row at midnight.

Date fields get a **closed** interval ``[start, end]``, because a ``DateField``
has no sub-day resolution and ``lt next_day`` would just be a slower ``lte
last_day``.

Day counts follow pretix's own reporting timeframes so that our "last 7 days"
means the same as the one in the pretix backend:

* ``relative_last_days(n)`` -- today and the ``n - 1`` days before it
  (``days_last7`` = ``ref_d - timedelta(days=6)`` .. ``ref_d``),
* ``relative_next_days(n)`` -- the ``n`` days starting **tomorrow**, today
  excluded (``days_next7`` = ``ref_d + timedelta(days=1)`` .. ``+ 6``).
"""

from typing import Any, Optional, Union

import calendar
import datetime as dt
from dataclasses import dataclass
from django.utils import timezone as django_timezone

from pretix_custom_reports.contracts.errors import CompilationError
from pretix_custom_reports.contracts.fields import DataType, Operator

__all__ = [
    "DATE_DATATYPES",
    "RELATIVE_OPERATORS",
    "DateWindow",
    "event_timezone",
    "resolve_relative_window",
    "today_in_event_timezone",
]

#: The datatypes a relative operator may be used on. Everything else is a
#: programming error in the registry or a definition the compiler must reject.
DATE_DATATYPES = frozenset({DataType.DATE, DataType.DATETIME})

#: The relative operators this module resolves. Kept as an explicit frozenset
#: rather than derived from ``OPERATOR_SPECS[...].relative`` so that a new
#: relative operator in a future contract version fails loudly here instead of
#: silently falling through to an unbounded window.
RELATIVE_OPERATORS = frozenset(
    {
        Operator.RELATIVE_TODAY,
        Operator.RELATIVE_LAST_DAYS,
        Operator.RELATIVE_NEXT_DAYS,
        Operator.RELATIVE_CURRENT_MONTH,
        Operator.RELATIVE_CURRENT_YEAR,
        Operator.RELATIVE_SINCE_EVENT_START,
    }
)


@dataclass(frozen=True)
class DateWindow:
    """A resolved time window, ready to be turned into two comparisons.

    Both bounds are optional: ``relative_since_event_start`` has no upper bound.
    The bound *types* match the field's datatype -- ``date`` objects for
    :attr:`~pretix_custom_reports.contracts.fields.DataType.DATE`, aware
    ``datetime`` objects for
    :attr:`~pretix_custom_reports.contracts.fields.DataType.DATETIME` -- so the
    caller never has to guess.
    """

    start: Optional[Union[dt.date, dt.datetime]]
    """Lower bound, inclusive. ``None`` means unbounded."""

    end: Optional[Union[dt.date, dt.datetime]]
    """Upper bound. Inclusive for dates, **exclusive** for datetimes.

    ``None`` means unbounded.
    """

    end_inclusive: bool
    """True if :attr:`end` belongs to the window (``lte``), false for ``lt``."""

    datatype: DataType
    """The datatype the bounds were built for."""

    reference_date: dt.date
    """"Today" in the event's timezone, kept for error messages and tests."""


def event_timezone(event: Any) -> dt.tzinfo:
    """The event's timezone.

    :param event: a ``pretix.base.models.Event`` (or anything exposing a
        ``timezone`` attribute -- test doubles do).
    :raises CompilationError: if *event* cannot tell us its timezone.

    Deliberately no fallback to :func:`django.utils.timezone.get_current_timezone`.
    A silent fallback would turn a wrong result into an invisible one: the report
    would still run and would still be wrong, just only for organizers whose
    timezone happens to differ from the server's.
    """
    tz = getattr(event, "timezone", None)
    if tz is None:
        raise CompilationError(
            "Relative date filters need the event's timezone, but the given "
            "event does not provide one. A report with relative date filters "
            "cannot be compiled without an event."
        )
    return tz


def today_in_event_timezone(event: Any, now: Optional[dt.datetime] = None) -> dt.date:
    """ "Today" as the organizer of *event* would call it.

    :param now: reference instant, defaults to :func:`django.utils.timezone.now`.
        Injectable so tests can pin a date without freezing the clock globally.
    """
    reference = now if now is not None else django_timezone.now()
    if django_timezone.is_naive(reference):
        # A naive reference instant is a caller bug, but interpreting it in the
        # event timezone is the least surprising repair and keeps tests that
        # pass plain datetimes honest.
        reference = django_timezone.make_aware(reference, event_timezone(event))
    return reference.astimezone(event_timezone(event)).date()


def _month_end(day: dt.date) -> dt.date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _date_bounds(
    operator: Operator, value: Any, today: dt.date, event: Any
) -> tuple[Optional[dt.date], Optional[dt.date]]:
    """Closed ``[first, last]`` day range for *operator*, both may be ``None``."""
    if operator is Operator.RELATIVE_TODAY:
        return today, today

    if operator is Operator.RELATIVE_LAST_DAYS:
        days = _day_count(operator, value)
        return today - dt.timedelta(days=days - 1), today

    if operator is Operator.RELATIVE_NEXT_DAYS:
        days = _day_count(operator, value)
        start = today + dt.timedelta(days=1)
        return start, start + dt.timedelta(days=days - 1)

    if operator is Operator.RELATIVE_CURRENT_MONTH:
        first = today.replace(day=1)
        return first, _month_end(first)

    if operator is Operator.RELATIVE_CURRENT_YEAR:
        return dt.date(today.year, 1, 1), dt.date(today.year, 12, 31)

    if operator is Operator.RELATIVE_SINCE_EVENT_START:
        return _event_start_date(event), None

    raise CompilationError(f"{operator} is not a relative date operator.")


def _day_count(operator: Operator, value: Any) -> int:
    """The structural validator already checked this; re-check, do not trust."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CompilationError(
            f"Operator {operator} needs a positive whole number of days, "
            f"got {value!r}."
        )
    return value


def _event_start(event: Any) -> dt.datetime:
    start = getattr(event, "date_from", None)
    if start is None:
        raise CompilationError(
            "Operator relative_since_event_start needs the event's start date, "
            "but the event does not have one."
        )
    if django_timezone.is_naive(start):
        start = django_timezone.make_aware(start, event_timezone(event))
    return start


def _event_start_date(event: Any) -> dt.date:
    return _event_start(event).astimezone(event_timezone(event)).date()


def resolve_relative_window(
    operator: Operator,
    value: Any,
    datatype: DataType,
    event: Any,
    now: Optional[dt.datetime] = None,
) -> DateWindow:
    """Turn a relative operator into concrete bounds in the event's timezone.

    :param operator: one of :data:`RELATIVE_OPERATORS`.
    :param value: the operator's value -- a day count for
        ``relative_last_days`` / ``relative_next_days``, ``None`` otherwise.
    :param datatype: the *field's* datatype. Decides whether the bounds come out
        as dates or as aware datetimes.
    :param event: the event whose timezone and start date apply.
    :param now: reference instant, see :func:`today_in_event_timezone`.
    :raises CompilationError: unknown operator, wrong datatype, bad day count or
        an event that cannot supply a timezone / start date.
    """
    if operator not in RELATIVE_OPERATORS:
        raise CompilationError(f"{operator} is not a relative date operator.")
    if datatype not in DATE_DATATYPES:
        raise CompilationError(
            f"Operator {operator} is only defined for date and datetime fields, "
            f"not for {datatype}."
        )

    tz = event_timezone(event)
    today = today_in_event_timezone(event, now)
    first_day, last_day = _date_bounds(operator, value, today, event)

    if datatype is DataType.DATE:
        return DateWindow(
            start=first_day,
            end=last_day,
            end_inclusive=True,
            datatype=datatype,
            reference_date=today,
        )

    # Datetime: midnight-to-midnight in the event timezone, upper bound
    # exclusive. ``relative_since_event_start`` is the one operator whose lower
    # bound is a real instant rather than a midnight, because "since the event
    # started" means the event's start time, not the start of that day.
    if operator is Operator.RELATIVE_SINCE_EVENT_START:
        return DateWindow(
            start=_event_start(event),
            end=None,
            end_inclusive=False,
            datatype=datatype,
            reference_date=today,
        )

    return DateWindow(
        start=_midnight(first_day, tz),
        end=_midnight(last_day + dt.timedelta(days=1), tz) if last_day else None,
        end_inclusive=False,
        datatype=datatype,
        reference_date=today,
    )


def _midnight(day: Optional[dt.date], tz: dt.tzinfo) -> Optional[dt.datetime]:
    """Start of *day* in *tz*.

    Same construction as pretix's own timeframe helper
    (``make_aware(datetime.combine(d, time(0, 0, 0)), timezone)``). On a DST
    transition day where 00:00 does not exist this can be off by an hour; pretix
    core accepts the same imprecision, and no timezone in use shifts at
    midnight.
    """
    if day is None:
        return None
    return django_timezone.make_aware(dt.datetime.combine(day, dt.time(0, 0, 0)), tz)
