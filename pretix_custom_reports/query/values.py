"""Filter value coercion per datatype, and what "empty" means per datatype.

Owner from wave 1 on: query-dev (ORCHESTRIERUNG.md section 5).

The structural validator in ``contracts/definition.py`` checks the *shape* of a
filter value against the operator's
:class:`~pretix_custom_reports.contracts.fields.ValueKind`: no value, one scalar,
a list, a pair, a day count. It cannot check the *type*, because only the
registry knows that ``order.total`` is money (docs/adr/0001-contracts.md section
9, fixture ``invalid/field_type_conflict.json``).

That second half happens here. A stored definition carries JSON scalars only --
``str``, ``int``, ``float``, ``bool`` -- so ``{"field": "order.total",
"operator": "gte", "value": "10.00"}`` is perfectly legal JSON and has to become
``Decimal("10.00")`` before it reaches the ORM. Handing the raw string to Django
would work on some backends and produce a silent mis-comparison on others.

Everything in here raises :class:`~pretix_custom_reports.contracts.errors.CompilationError`
on bad input: the definition is structurally valid and resolvable, it just does
not fit the field it names.
"""

from typing import Any, List, Optional

import datetime as dt
from decimal import Decimal, InvalidOperation
from django.db.models import Q
from django.utils import timezone as django_timezone

from pretix_custom_reports.contracts.errors import CompilationError
from pretix_custom_reports.contracts.fields import DataType, ReportField

__all__ = [
    "BLANKABLE_DATATYPES",
    "coerce_value",
    "coerce_values",
    "emptiness_q",
]

#: Datatypes stored in a text column, where "empty" means ``NULL`` *or* ``''``.
#: pretix mixes both: ``Order.comment`` is ``TextField(blank=True)`` and thus
#: ``''``, while ``Order.email`` is ``null=True`` and thus ``NULL``. A filter
#: that only tested one of them would quietly miss half the empty rows.
BLANKABLE_DATATYPES = frozenset(
    {
        DataType.STRING,
        DataType.TEXT,
        DataType.I18N,
        DataType.EMAIL,
        DataType.PHONE,
        DataType.URL,
        DataType.CHOICE,
        DataType.COUNTRY,
        DataType.MULTICHOICE,
        DataType.LIST,
        DataType.FILE,
    }
)

_TRUE_STRINGS = frozenset({"true", "yes", "y", "1", "on"})
_FALSE_STRINGS = frozenset({"false", "no", "n", "0", "off"})


def coerce_value(field: ReportField, value: Any, event: Any = None) -> Any:
    """Coerce one JSON scalar into the Python type *field* compares against.

    :param field: the resolved field. Only its
        :attr:`~pretix_custom_reports.contracts.fields.ReportField.datatype` is
        used; the caller has already checked that the operator is allowed.
    :param event: needed to interpret a naive datetime literal in the event's
        timezone. Optional, because most datatypes do not care.
    :raises CompilationError: the value cannot mean anything for this datatype.
    """
    datatype = field.datatype

    if value is None:
        raise CompilationError(f"{field.key}: filter value must not be null.")

    try:
        if datatype in (DataType.MONEY, DataType.DECIMAL):
            return _to_decimal(value)
        if datatype is DataType.INTEGER:
            return _to_int(value)
        if datatype is DataType.BOOLEAN:
            return _to_bool(value)
        if datatype is DataType.DATE:
            return _to_date(value)
        if datatype is DataType.DATETIME:
            return _to_datetime(value, event)
        if datatype is DataType.TIME:
            return _to_time(value)
    except (TypeError, ValueError, InvalidOperation) as e:
        raise CompilationError(
            f"{field.key}: {value!r} is not a valid {datatype} value ({e})."
        ) from e

    # Everything else is compared as text. ``bool`` is excluded on purpose:
    # ``True`` would become the string ``"True"`` and match nothing, which is
    # worse than a clear error.
    if isinstance(value, bool):
        raise CompilationError(
            f"{field.key}: a boolean cannot be compared against a {datatype} field."
        )
    return str(value)


def coerce_values(field: ReportField, values: Any, event: Any = None) -> List[Any]:
    """Coerce a list or pair of JSON scalars. Shape was checked structurally."""
    if not isinstance(values, (list, tuple)):
        raise CompilationError(
            f"{field.key}: expected a list of filter values, got {type(values).__name__}."
        )
    return [coerce_value(field, item, event) for item in values]


def emptiness_q(path: str, datatype: DataType) -> Q:
    """``Q`` matching rows where *path* holds no usable value.

    ``NULL`` always counts as empty. The empty string counts as empty for the
    text-ish datatypes in :data:`BLANKABLE_DATATYPES`; adding ``__exact=""`` to a
    numeric or date column would be a database error rather than a wider match.
    """
    empty = Q(**{f"{path}__isnull": True})
    if datatype in BLANKABLE_DATATYPES:
        empty = empty | Q(**{f"{path}__exact": ""})
    return empty


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("a boolean is not a number")
    if isinstance(value, float):
        # str() first: Decimal(0.1) is 0.1000000000000000055511151231257827.
        return Decimal(str(value))
    return Decimal(value)


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("a boolean is not a number")
    if isinstance(value, float):
        if value != int(value):
            raise ValueError("not a whole number")
        return int(value)
    if isinstance(value, str):
        return int(value.strip())
    return int(value)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise ValueError("only 0 and 1 map to a boolean")
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    raise ValueError("not a boolean")


def _to_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        return dt.date.fromisoformat(value.strip()[:10])
    raise ValueError("expected an ISO date such as 2026-07-30")


def _to_datetime(value: Any, event: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, dt.date):
        parsed = dt.datetime.combine(value, dt.time(0, 0, 0))
    elif isinstance(value, str):
        parsed = _parse_datetime_string(value.strip())
    else:
        raise ValueError("expected an ISO datetime such as 2026-07-30T12:00:00+02:00")

    if django_timezone.is_naive(parsed):
        # A stored literal without an offset is meant in the organizer's local
        # time, not the server's. Same reasoning as query/dates.py.
        from pretix_custom_reports.query.dates import event_timezone

        parsed = django_timezone.make_aware(parsed, event_timezone(event))
    return parsed


def _parse_datetime_string(text: str) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        pass
    # A bare date in a datetime filter is a common and harmless shorthand.
    return dt.datetime.combine(dt.date.fromisoformat(text[:10]), dt.time(0, 0, 0))


def _to_time(value: Any) -> dt.time:
    if isinstance(value, dt.datetime):
        return value.time()
    if isinstance(value, dt.time):
        return value
    if isinstance(value, str):
        return dt.time.fromisoformat(value.strip())
    raise ValueError("expected an ISO time such as 18:30")


def optional_event_timezone(event: Any) -> Optional[dt.tzinfo]:
    """The event timezone if it has one, else ``None``. Never raises.

    Used where a timezone is nice to have but not required, so that a caller
    without an event (the editor's structural pre-check) does not blow up.
    """
    return getattr(event, "timezone", None)
