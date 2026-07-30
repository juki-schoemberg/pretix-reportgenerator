"""Relative date filters resolve in the event's timezone.

Owner: query-dev (ORCHESTRIERUNG.md section 5).

These tests need no database. They pin "now" explicitly, because the whole point
of the module under test is that its output depends on a reference instant *and*
on the event's timezone, and a test that reads the wall clock cannot show that.

The two properties worth protecting:

1. **Timezone.** Two events with different timezones and the same instant resolve
   "today" to two different calendar days when that instant falls near midnight.
   A report that used the server timezone would be silently wrong for every
   organizer not sitting in it -- and only near midnight, which is the worst kind
   of bug to reproduce.
2. **Boundaries.** Datetime windows are half-open ``[start, end)``. Off by one
   second at midnight means a row counted twice or not at all in two consecutive
   daily reports.
"""

from typing import Any

import datetime
import pytest
from zoneinfo import ZoneInfo

from pretix_custom_reports.contracts.errors import CompilationError
from pretix_custom_reports.contracts.fields import DataType, Operator
from pretix_custom_reports.query.dates import (
    RELATIVE_OPERATORS,
    event_timezone,
    resolve_relative_window,
    today_in_event_timezone,
)

from .test_query_support import FakeEvent

BERLIN = ZoneInfo("Europe/Berlin")
AUCKLAND = ZoneInfo("Pacific/Auckland")
LOS_ANGELES = ZoneInfo("America/Los_Angeles")

#: 2026-07-30 23:30 in Berlin. In Auckland it is already the 31st, in Los Angeles
#: still the 30th -- so "today" differs per event at this instant.
LATE_EVENING = datetime.datetime(2026, 7, 30, 21, 30, tzinfo=datetime.timezone.utc)


def _window(operator: Operator, datatype: DataType, event: Any, value: Any = None):
    return resolve_relative_window(operator, value, datatype, event, LATE_EVENING)


# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "timezone,expected_day",
    [
        ("Europe/Berlin", datetime.date(2026, 7, 30)),
        ("Pacific/Auckland", datetime.date(2026, 7, 31)),
        ("America/Los_Angeles", datetime.date(2026, 7, 30)),
        ("UTC", datetime.date(2026, 7, 30)),
    ],
)
def test_today_uses_the_event_timezone(timezone, expected_day):
    """The same instant is a different calendar day in different timezones."""
    event = FakeEvent(timezone=timezone)
    assert today_in_event_timezone(event, LATE_EVENING) == expected_day


def test_relative_today_window_differs_between_timezones():
    """Not just the label: the actual SQL bounds move with the event timezone."""
    berlin = _window(
        Operator.RELATIVE_TODAY, DataType.DATETIME, FakeEvent("Europe/Berlin")
    )
    auckland = _window(
        Operator.RELATIVE_TODAY, DataType.DATETIME, FakeEvent("Pacific/Auckland")
    )
    assert berlin.start != auckland.start
    assert berlin.reference_date == datetime.date(2026, 7, 30)
    assert auckland.reference_date == datetime.date(2026, 7, 31)


def test_window_bounds_are_midnight_in_the_event_timezone():
    event = FakeEvent("Europe/Berlin")
    window = _window(Operator.RELATIVE_TODAY, DataType.DATETIME, event)
    assert window.start == datetime.datetime(2026, 7, 30, 0, 0, tzinfo=BERLIN)
    assert window.end == datetime.datetime(2026, 7, 31, 0, 0, tzinfo=BERLIN)
    # Not 00:00 UTC, which is what a server-timezone implementation would produce.
    assert window.start.utcoffset() == datetime.timedelta(hours=2)


def test_server_timezone_is_not_consulted(settings):
    """Changing the Django timezone must not move a single bound."""
    event = FakeEvent("Europe/Berlin")
    before = _window(Operator.RELATIVE_CURRENT_MONTH, DataType.DATETIME, event)
    settings.TIME_ZONE = "Pacific/Auckland"
    after = _window(Operator.RELATIVE_CURRENT_MONTH, DataType.DATETIME, event)
    assert before == after


def test_event_without_timezone_is_a_compilation_error():
    """No silent fallback to the server timezone -- see query/dates.py."""

    class Bare:
        timezone = None

    with pytest.raises(CompilationError) as excinfo:
        event_timezone(Bare())
    assert "timezone" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The six operators
# ---------------------------------------------------------------------------


def test_relative_today_on_a_date_field_is_a_single_closed_day():
    window = _window(Operator.RELATIVE_TODAY, DataType.DATE, FakeEvent())
    assert (window.start, window.end) == (
        datetime.date(2026, 7, 30),
        datetime.date(2026, 7, 30),
    )
    assert window.end_inclusive is True


def test_relative_last_days_includes_today():
    """Follows pretix's own ``days_last7`` (pretix/base/timeframes.py)."""
    window = _window(Operator.RELATIVE_LAST_DAYS, DataType.DATE, FakeEvent(), value=7)
    assert window.start == datetime.date(2026, 7, 24)
    assert window.end == datetime.date(2026, 7, 30)


def test_relative_last_days_of_one_is_today():
    window = _window(Operator.RELATIVE_LAST_DAYS, DataType.DATE, FakeEvent(), value=1)
    assert window.start == window.end == datetime.date(2026, 7, 30)


def test_relative_next_days_starts_tomorrow():
    """Also pretix's convention: ``days_next7`` excludes today."""
    window = _window(Operator.RELATIVE_NEXT_DAYS, DataType.DATE, FakeEvent(), value=7)
    assert window.start == datetime.date(2026, 7, 31)
    assert window.end == datetime.date(2026, 8, 6)


def test_relative_current_month_spans_the_whole_month():
    window = _window(Operator.RELATIVE_CURRENT_MONTH, DataType.DATE, FakeEvent())
    assert window.start == datetime.date(2026, 7, 1)
    assert window.end == datetime.date(2026, 7, 31)


def test_relative_current_month_handles_february():
    event = FakeEvent()
    reference = datetime.datetime(2028, 2, 10, 12, 0, tzinfo=BERLIN)
    window = resolve_relative_window(
        Operator.RELATIVE_CURRENT_MONTH, None, DataType.DATE, event, reference
    )
    assert window.start == datetime.date(2028, 2, 1)
    assert window.end == datetime.date(2028, 2, 29)


def test_relative_current_year_spans_the_whole_year():
    window = _window(Operator.RELATIVE_CURRENT_YEAR, DataType.DATETIME, FakeEvent())
    assert window.start == datetime.datetime(2026, 1, 1, tzinfo=BERLIN)
    assert window.end == datetime.datetime(2027, 1, 1, tzinfo=BERLIN)
    assert window.end_inclusive is False


def test_relative_since_event_start_is_the_event_start_instant():
    """Not midnight of the event's first day: "since the event started"."""
    start = datetime.datetime(2026, 9, 1, 18, 30, tzinfo=BERLIN)
    event = FakeEvent(date_from=start)
    window = _window(Operator.RELATIVE_SINCE_EVENT_START, DataType.DATETIME, event)
    assert window.start == start
    assert window.end is None


def test_relative_since_event_start_on_a_date_field_uses_the_day():
    start = datetime.datetime(2026, 9, 1, 18, 30, tzinfo=BERLIN)
    event = FakeEvent(date_from=start)
    window = _window(Operator.RELATIVE_SINCE_EVENT_START, DataType.DATE, event)
    assert window.start == datetime.date(2026, 9, 1)
    assert window.end is None


def test_relative_since_event_start_needs_a_start_date():
    event = FakeEvent()
    event.date_from = None
    with pytest.raises(CompilationError):
        _window(Operator.RELATIVE_SINCE_EVENT_START, DataType.DATETIME, event)


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operator", sorted(RELATIVE_OPERATORS, key=str))
def test_every_relative_operator_resolves_for_both_date_types(operator):
    """No operator may fall through to an unbounded window."""
    value = 5 if operator.value.endswith("_days") else None
    for datatype in (DataType.DATE, DataType.DATETIME):
        window = resolve_relative_window(
            operator, value, datatype, FakeEvent(), LATE_EVENING
        )
        assert window.start is not None or window.end is not None
        assert window.datatype is datatype


@pytest.mark.parametrize(
    "datatype", [DataType.STRING, DataType.MONEY, DataType.BOOLEAN, DataType.TIME]
)
def test_relative_operators_reject_non_date_datatypes(datatype):
    with pytest.raises(CompilationError):
        resolve_relative_window(
            Operator.RELATIVE_TODAY, None, datatype, FakeEvent(), LATE_EVENING
        )


def test_non_relative_operator_is_rejected():
    with pytest.raises(CompilationError):
        resolve_relative_window(
            Operator.EXACT, None, DataType.DATETIME, FakeEvent(), LATE_EVENING
        )


@pytest.mark.parametrize("value", [0, -3, "seven", 2.5, True, None])
def test_bad_day_counts_are_rejected(value):
    with pytest.raises(CompilationError):
        resolve_relative_window(
            Operator.RELATIVE_LAST_DAYS, value, DataType.DATE, FakeEvent(), LATE_EVENING
        )


def test_datetime_windows_are_half_open_and_therefore_gapless():
    """Two consecutive daily windows must touch exactly once.

    If ``relative_today`` used an inclusive upper bound, a row at exactly midnight
    would appear in both days' reports.
    """
    event = FakeEvent("Europe/Berlin")
    day_one = resolve_relative_window(
        Operator.RELATIVE_TODAY,
        None,
        DataType.DATETIME,
        event,
        datetime.datetime(2026, 7, 30, 12, 0, tzinfo=BERLIN),
    )
    day_two = resolve_relative_window(
        Operator.RELATIVE_TODAY,
        None,
        DataType.DATETIME,
        event,
        datetime.datetime(2026, 7, 31, 12, 0, tzinfo=BERLIN),
    )
    assert day_one.end == day_two.start
    assert day_one.end_inclusive is False


def test_naive_reference_instant_is_interpreted_in_the_event_timezone():
    event = FakeEvent("Pacific/Auckland")
    naive = datetime.datetime(2026, 7, 30, 23, 30)
    assert today_in_event_timezone(event, naive) == datetime.date(2026, 7, 30)
