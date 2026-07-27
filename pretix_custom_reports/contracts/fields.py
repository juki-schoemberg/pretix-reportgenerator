"""The field contract: :class:`ReportField` plus the enums it is built from.

Owner: contract-architect (wave 0c). Frozen -- see ``contracts/__init__.py``.

A ``ReportField`` is the *only* place where an ORM path, a lookup or an
annotation may originate (CLAUDE.md rule 2). Stored and imported JSON only ever
carries the ``key`` of a field; everything technical is looked up in the
registry at runtime. That is what makes an imported definition harmless even
though it is untrusted input.

This module is deliberately free of Django and pretix imports so that
``from pretix_custom_reports.contracts import *`` works without configured
settings and without a database. See docs/adr/0001-contracts.md section 6.

Key naming
----------

A key is ``<namespace>.<remainder>``, split on the **first** dot::

    order.code                      core field of Order
    position.attendee_name          core field of OrderPosition
    invoice_address.company         core field of a related model
    answer.tshirt-size              answer to Question(identifier="tshirt-size")
    meta.event.campaign             event meta property "campaign"
    plugin.pretix_seating.zone      field contributed by another plugin

Rules, enforced by :func:`validate_key`:

* lowercase namespace from :data:`ALL_NAMESPACES` (``plugin`` for third parties)
* the remainder may contain ``A-Z a-z 0-9 . - _`` -- the exact character set
  ``Question.identifier`` allows (pretix/base/models/items.py:1683-1694), so any
  legal identifier can be expressed verbatim
* **no double underscore anywhere.** ``__`` is Django's lookup separator;
  banning it makes it structurally impossible for a stored key to be mistaken
  for a multi-level ORM path, even by future buggy code
* no empty segments, no leading/trailing dot, at most
  :data:`KEY_MAX_LENGTH` characters

Portability
-----------

Keys are portable across events by construction: they contain no primary keys.
Questions are addressed via ``Question.identifier``, which survives event copies
(pretix/base/models/event.py:1090-1099) but may be renamed by the user at any
time -- "not resolvable" is a regular state, not an error
(docs/pretix-api-notes.md section 6.4).

Filter *values* are a separate matter: see :class:`ValueScope`.
"""

from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import re
from dataclasses import dataclass, field as dataclass_field
from enum import Enum

from pretix_custom_reports.contracts.errors import FieldContractError

__all__ = [
    "AGGREGATES_FOR_DATATYPE",
    "ALL_NAMESPACES",
    "Aggregate",
    "Base",
    "DEFAULT_OPERATORS",
    "DataType",
    "FieldContext",
    "GROUP_ANSWERS",
    "GROUP_CHECKIN",
    "GROUP_INVOICE_ADDRESS",
    "GROUP_ITEM",
    "GROUP_META",
    "GROUP_ORDER",
    "GROUP_PAYMENT",
    "GROUP_POSITION",
    "GROUP_SEAT",
    "GROUP_SUBEVENT",
    "GROUP_VOUCHER",
    "IDENTIFIER_MAX_LENGTH",
    "IDENTIFIER_RE",
    "KEY_MAX_LENGTH",
    "KEY_RE",
    "KEY_SEPARATOR",
    "NS_ANSWER",
    "NS_CHECKIN",
    "NS_COMPUTED",
    "NS_DISCOUNT",
    "NS_INVOICE_ADDRESS",
    "NS_ITEM",
    "NS_META",
    "NS_ORDER",
    "NS_PAYMENT",
    "NS_PLUGIN",
    "NS_POSITION",
    "NS_REFUND",
    "NS_SEAT",
    "NS_SUBEVENT",
    "NS_VARIATION",
    "NS_VOUCHER",
    "ORM_PATH_RE",
    "OPERATOR_SPECS",
    "Operator",
    "OperatorSpec",
    "PROVIDER_CORE",
    "RESERVED_NAMESPACES",
    "ReportField",
    "SortDirection",
    "ValueKind",
    "ValueScope",
    "is_plugin_key",
    "meta_field_key",
    "plugin_field_key",
    "question_field_key",
    "split_key",
    "validate_identifier",
    "validate_key",
]


# ---------------------------------------------------------------------------
# Enum base
# ---------------------------------------------------------------------------


class _ValueEnum(str, Enum):
    """String enum whose ``str()`` is the plain value.

    ``str`` mixin so that ``json.dumps`` emits the value and comparisons with
    plain strings work; ``__str__`` overridden because ``str(Base.ORDER)`` would
    otherwise be ``"Base.ORDER"``.
    """

    __str__ = str.__str__

    @classmethod
    def values(cls) -> Tuple[str, ...]:
        """All member values, in declaration order."""
        return tuple(m.value for m in cls)

    @classmethod
    def coerce(cls, value: Any) -> "_ValueEnum":
        """Return the matching member or raise ``ValueError``.

        Accepts members and exact string values only -- never a name, never a
        case-insensitive match. Stored JSON must be unambiguous.
        """
        if isinstance(value, cls):
            return value
        if type(value) is str:  # noqa: E721 - reject str subclasses of other enums
            try:
                return cls(value)
            except ValueError:
                pass
        raise ValueError(
            "{!r} is not a valid {} (allowed: {})".format(
                value, cls.__name__, ", ".join(cls.values())
            )
        )


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Base(_ValueEnum):
    """Row granularity of a report (SPEC.md F3).

    ``ORDER`` yields one row per :class:`~pretix.base.models.Order`,
    ``ORDERPOSITION`` one row per :class:`~pretix.base.models.OrderPosition`.
    """

    ORDER = "order"
    ORDERPOSITION = "orderposition"


class DataType(_ValueEnum):
    """Semantic type of a field's value.

    Drives the default operator set, the editor widget and the renderer. It is
    *not* a Django field type -- ``MONEY`` and ``DECIMAL`` are both stored as
    ``Decimal``, but only ``MONEY`` may be formatted with a currency.
    """

    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    MONEY = "money"
    BOOLEAN = "boolean"
    DATE = "date"
    TIME = "time"
    DATETIME = "datetime"
    CHOICE = "choice"
    MULTICHOICE = "multichoice"
    I18N = "i18n"
    COUNTRY = "country"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    FILE = "file"
    LIST = "list"


class Operator(_ValueEnum):
    """Filter operators (SPEC.md F6).

    Deliberately *semantic*, not ORM lookups: ``CONTAINS`` means
    "case-insensitively contains", and it is the query compiler's job to decide
    that this becomes ``__icontains``. A stored definition must never look like
    an ORM expression.

    The relative operators exist for scheduled reports, which must keep making
    sense every time they run.
    """

    EXACT = "exact"
    NOT_EXACT = "not_exact"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"
    IN = "in"
    NOT_IN = "not_in"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    BETWEEN = "between"
    RELATIVE_TODAY = "relative_today"
    RELATIVE_LAST_DAYS = "relative_last_days"
    RELATIVE_NEXT_DAYS = "relative_next_days"
    RELATIVE_CURRENT_MONTH = "relative_current_month"
    RELATIVE_CURRENT_YEAR = "relative_current_year"
    RELATIVE_SINCE_EVENT_START = "relative_since_event_start"


class ValueKind(_ValueEnum):
    """Shape of the ``value`` an operator expects.

    This is the *only* value check the structural validator can perform without
    a registry, and it is worth a lot: it already catches
    ``relative_last_days: "seven"`` and ``between: 5``.
    """

    NONE = "none"
    """No value at all. ``value`` must be absent or ``null``."""

    SCALAR = "scalar"
    """A single str/int/float/bool."""

    LIST = "list"
    """A non-empty list of scalars."""

    RANGE = "range"
    """Exactly two scalars, ``[from, to]``."""

    DAY_COUNT = "day_count"
    """A positive integer number of days."""


class Aggregate(_ValueEnum):
    """Aggregation applied to a field that is one-to-many for the report base.

    Used when a position-level field is put on an ``order``-based report
    (SPEC.md F3) and for genuinely aggregate fields such as check-ins.
    """

    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    AVG = "avg"
    JOIN = "join"


class SortDirection(_ValueEnum):
    """Direction of one sorting stage (SPEC.md F7)."""

    ASC = "asc"
    DESC = "desc"


class ValueScope(_ValueEnum):
    """Whether the *values* a field filters on are portable between events.

    The field ``key`` is always portable. Its values are not always: filtering
    ``order.status`` on ``"p"`` means the same everywhere, but filtering
    ``item.id`` on ``42`` does not.

    ``EVENT`` fields must therefore be remapped by the portability layer on
    import and on "load organizer template" (SPEC.md F9/F10), using the labels
    from :attr:`ReportField.choices` for name matching. Prefer declaring fields
    whose values are naturally stable -- it removes the whole problem.
    """

    GLOBAL = "global"
    EVENT = "event"


# ---------------------------------------------------------------------------
# Operator metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperatorSpec:
    """Static metadata for one :class:`Operator`."""

    operator: Operator
    value_kind: ValueKind
    relative: bool = False
    """True for the date operators that are resolved against "now" at run time."""

    negated: bool = False
    """True if the operator is the logical negation of another one. Editor hint."""


def _spec(op: Operator, kind: ValueKind, **kw: Any) -> Tuple[Operator, OperatorSpec]:
    return op, OperatorSpec(operator=op, value_kind=kind, **kw)


#: Complete, closed operator table. Adding an entry is a contract change.
OPERATOR_SPECS: Mapping[Operator, OperatorSpec] = dict(
    [
        _spec(Operator.EXACT, ValueKind.SCALAR),
        _spec(Operator.NOT_EXACT, ValueKind.SCALAR, negated=True),
        _spec(Operator.CONTAINS, ValueKind.SCALAR),
        _spec(Operator.NOT_CONTAINS, ValueKind.SCALAR, negated=True),
        _spec(Operator.STARTS_WITH, ValueKind.SCALAR),
        _spec(Operator.ENDS_WITH, ValueKind.SCALAR),
        _spec(Operator.IS_EMPTY, ValueKind.NONE),
        _spec(Operator.IS_NOT_EMPTY, ValueKind.NONE, negated=True),
        _spec(Operator.IN, ValueKind.LIST),
        _spec(Operator.NOT_IN, ValueKind.LIST, negated=True),
        _spec(Operator.LT, ValueKind.SCALAR),
        _spec(Operator.LTE, ValueKind.SCALAR),
        _spec(Operator.GT, ValueKind.SCALAR),
        _spec(Operator.GTE, ValueKind.SCALAR),
        _spec(Operator.BETWEEN, ValueKind.RANGE),
        _spec(Operator.RELATIVE_TODAY, ValueKind.NONE, relative=True),
        _spec(Operator.RELATIVE_LAST_DAYS, ValueKind.DAY_COUNT, relative=True),
        _spec(Operator.RELATIVE_NEXT_DAYS, ValueKind.DAY_COUNT, relative=True),
        _spec(Operator.RELATIVE_CURRENT_MONTH, ValueKind.NONE, relative=True),
        _spec(Operator.RELATIVE_CURRENT_YEAR, ValueKind.NONE, relative=True),
        _spec(Operator.RELATIVE_SINCE_EVENT_START, ValueKind.NONE, relative=True),
    ]
)

_TEXTUAL_OPERATORS: Tuple[Operator, ...] = (
    Operator.EXACT,
    Operator.NOT_EXACT,
    Operator.CONTAINS,
    Operator.NOT_CONTAINS,
    Operator.STARTS_WITH,
    Operator.ENDS_WITH,
    Operator.IN,
    Operator.NOT_IN,
    Operator.IS_EMPTY,
    Operator.IS_NOT_EMPTY,
)

_ORDERED_OPERATORS: Tuple[Operator, ...] = (
    Operator.EXACT,
    Operator.NOT_EXACT,
    Operator.LT,
    Operator.LTE,
    Operator.GT,
    Operator.GTE,
    Operator.BETWEEN,
    Operator.IS_EMPTY,
    Operator.IS_NOT_EMPTY,
)

_RELATIVE_OPERATORS: Tuple[Operator, ...] = (
    Operator.RELATIVE_TODAY,
    Operator.RELATIVE_LAST_DAYS,
    Operator.RELATIVE_NEXT_DAYS,
    Operator.RELATIVE_CURRENT_MONTH,
    Operator.RELATIVE_CURRENT_YEAR,
    Operator.RELATIVE_SINCE_EVENT_START,
)

_SET_OPERATORS: Tuple[Operator, ...] = (
    Operator.EXACT,
    Operator.NOT_EXACT,
    Operator.IN,
    Operator.NOT_IN,
    Operator.IS_EMPTY,
    Operator.IS_NOT_EMPTY,
)

#: Advisory default operator set per datatype. The registry may narrow this per
#: field (a text column backed by an un-indexed annotation may drop CONTAINS);
#: it may also widen it, because :attr:`ReportField.filter_operators` is the
#: single source of truth at run time. The editor uses this table for the
#: field library preview before a concrete field is chosen.
DEFAULT_OPERATORS: Mapping[DataType, Tuple[Operator, ...]] = {
    DataType.STRING: _TEXTUAL_OPERATORS,
    DataType.TEXT: _TEXTUAL_OPERATORS,
    DataType.I18N: _TEXTUAL_OPERATORS,
    DataType.EMAIL: _TEXTUAL_OPERATORS,
    DataType.PHONE: _TEXTUAL_OPERATORS,
    DataType.URL: _TEXTUAL_OPERATORS,
    DataType.INTEGER: _ORDERED_OPERATORS,
    DataType.DECIMAL: _ORDERED_OPERATORS,
    DataType.MONEY: _ORDERED_OPERATORS,
    DataType.TIME: _ORDERED_OPERATORS,
    DataType.DATE: _ORDERED_OPERATORS + _RELATIVE_OPERATORS,
    DataType.DATETIME: _ORDERED_OPERATORS + _RELATIVE_OPERATORS,
    DataType.BOOLEAN: (Operator.EXACT, Operator.NOT_EXACT),
    DataType.CHOICE: _SET_OPERATORS,
    DataType.COUNTRY: _SET_OPERATORS,
    DataType.MULTICHOICE: (
        Operator.IN,
        Operator.NOT_IN,
        Operator.IS_EMPTY,
        Operator.IS_NOT_EMPTY,
    ),
    DataType.LIST: (
        Operator.CONTAINS,
        Operator.NOT_CONTAINS,
        Operator.IS_EMPTY,
        Operator.IS_NOT_EMPTY,
    ),
    DataType.FILE: (Operator.IS_EMPTY, Operator.IS_NOT_EMPTY),
}

_NUMERIC_AGGREGATES: Tuple[Aggregate, ...] = (
    Aggregate.COUNT,
    Aggregate.COUNT_DISTINCT,
    Aggregate.SUM,
    Aggregate.MIN,
    Aggregate.MAX,
    Aggregate.AVG,
)

_ORDERABLE_AGGREGATES: Tuple[Aggregate, ...] = (
    Aggregate.COUNT,
    Aggregate.COUNT_DISTINCT,
    Aggregate.MIN,
    Aggregate.MAX,
)

_LABEL_AGGREGATES: Tuple[Aggregate, ...] = (
    Aggregate.COUNT,
    Aggregate.COUNT_DISTINCT,
    Aggregate.JOIN,
)

#: Advisory: which aggregates make sense for a datatype. Same rules as
#: :data:`DEFAULT_OPERATORS` -- :attr:`ReportField.aggregates` wins at run time.
AGGREGATES_FOR_DATATYPE: Mapping[DataType, Tuple[Aggregate, ...]] = {
    DataType.INTEGER: _NUMERIC_AGGREGATES,
    DataType.DECIMAL: _NUMERIC_AGGREGATES,
    DataType.MONEY: _NUMERIC_AGGREGATES,
    DataType.DATE: _ORDERABLE_AGGREGATES,
    DataType.DATETIME: _ORDERABLE_AGGREGATES,
    DataType.TIME: _ORDERABLE_AGGREGATES,
    DataType.STRING: _LABEL_AGGREGATES,
    DataType.TEXT: _LABEL_AGGREGATES,
    DataType.I18N: _LABEL_AGGREGATES,
    DataType.EMAIL: _LABEL_AGGREGATES,
    DataType.PHONE: _LABEL_AGGREGATES,
    DataType.URL: _LABEL_AGGREGATES,
    DataType.CHOICE: _LABEL_AGGREGATES,
    DataType.COUNTRY: _LABEL_AGGREGATES,
    DataType.MULTICHOICE: (Aggregate.COUNT, Aggregate.COUNT_DISTINCT),
    DataType.BOOLEAN: (Aggregate.COUNT, Aggregate.COUNT_DISTINCT),
    DataType.FILE: (Aggregate.COUNT,),
    DataType.LIST: (Aggregate.COUNT,),
}


# ---------------------------------------------------------------------------
# Key namespaces
# ---------------------------------------------------------------------------

KEY_SEPARATOR = "."

NS_ORDER = "order"
NS_POSITION = "position"
NS_INVOICE_ADDRESS = "invoice_address"
NS_ITEM = "item"
NS_VARIATION = "variation"
NS_SUBEVENT = "subevent"
NS_SEAT = "seat"
NS_VOUCHER = "voucher"
NS_DISCOUNT = "discount"
NS_PAYMENT = "payment"
NS_REFUND = "refund"
NS_CHECKIN = "checkin"
NS_ANSWER = "answer"
NS_META = "meta"
NS_COMPUTED = "computed"
NS_PLUGIN = "plugin"

#: Namespaces only the core registry may populate. A third-party plugin that
#: returns a field in one of these must be rejected by the registry -- that is
#: the conflict rule from SPEC.md section 6 ("core wins, plugins get a prefix").
RESERVED_NAMESPACES: frozenset = frozenset(
    {
        NS_ORDER,
        NS_POSITION,
        NS_INVOICE_ADDRESS,
        NS_ITEM,
        NS_VARIATION,
        NS_SUBEVENT,
        NS_SEAT,
        NS_VOUCHER,
        NS_DISCOUNT,
        NS_PAYMENT,
        NS_REFUND,
        NS_CHECKIN,
        NS_ANSWER,
        NS_META,
        NS_COMPUTED,
    }
)

#: Every namespace a valid key may start with.
ALL_NAMESPACES: frozenset = RESERVED_NAMESPACES | {NS_PLUGIN}

#: Value of :attr:`ReportField.provider` for fields declared by this plugin.
PROVIDER_CORE = "core"

# UI groups. Free strings, but these are the ones the core registry uses; a
# plugin should either reuse one or introduce its own translated group name.
GROUP_ORDER = "order"
GROUP_POSITION = "position"
GROUP_INVOICE_ADDRESS = "invoice_address"
GROUP_ITEM = "item"
GROUP_SUBEVENT = "subevent"
GROUP_SEAT = "seat"
GROUP_VOUCHER = "voucher"
GROUP_PAYMENT = "payment"
GROUP_CHECKIN = "checkin"
GROUP_ANSWERS = "answers"
GROUP_META = "meta"


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------

KEY_MAX_LENGTH = 250

#: ``<lowercase namespace>.<remainder>``. The remainder uses exactly the
#: character set of ``Question.identifier`` so every legal identifier fits.
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*\.[A-Za-z0-9_.\-]+$")

#: Sanity check for ORM paths declared *in code* by the registry. Never applied
#: to anything coming from JSON -- JSON never contains ORM paths.
ORM_PATH_RE = re.compile(r"^[a-z_][A-Za-z0-9_]*(?:__[A-Za-z0-9_]+)*$")

#: Stable identifier of a stored report, see docs/adr/0001-contracts.md
#: section 5. Same character set and length as ``Question.identifier``.
IDENTIFIER_MAX_LENGTH = 190
IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9.\-_]+$")


def validate_key(key: Any) -> str:
    """Return *key* if it is a well-formed field key, else raise ``ValueError``.

    Structural only: says nothing about whether the field exists.
    """
    if not isinstance(key, str):
        raise ValueError(f"Field key must be a string, got {type(key).__name__}.")
    if not key:
        raise ValueError("Field key must not be empty.")
    if len(key) > KEY_MAX_LENGTH:
        raise ValueError(f"Field key exceeds {KEY_MAX_LENGTH} characters.")
    if "__" in key:
        raise ValueError(
            "Field key must not contain a double underscore -- that is Django's "
            "lookup separator and never valid in a report field key."
        )
    if ".." in key or key.endswith("."):
        raise ValueError("Field key must not contain empty segments.")
    if not KEY_RE.match(key):
        raise ValueError(
            "Field key must look like '<namespace>.<name>', e.g. 'order.code'."
        )
    namespace = key.split(KEY_SEPARATOR, 1)[0]
    if namespace not in ALL_NAMESPACES:
        raise ValueError(
            "Unknown field key namespace {!r} (allowed: {}).".format(
                namespace, ", ".join(sorted(ALL_NAMESPACES))
            )
        )
    if namespace == NS_PLUGIN and key.count(KEY_SEPARATOR) < 2:
        raise ValueError(
            "A plugin field key must be 'plugin.<django_app_label>.<name>'."
        )
    return key


def validate_identifier(identifier: Any) -> str:
    """Return *identifier* if it is a usable report identifier, else raise.

    Mirrors ``Question.identifier`` (pretix/base/models/items.py:1683-1694) on
    purpose: same character set, same length, same "generate one if empty"
    semantics on the model side.
    """
    if not isinstance(identifier, str):
        raise ValueError(
            f"Report identifier must be a string, got {type(identifier).__name__}."
        )
    if not identifier:
        raise ValueError("Report identifier must not be empty.")
    if len(identifier) > IDENTIFIER_MAX_LENGTH:
        raise ValueError(
            f"Report identifier exceeds {IDENTIFIER_MAX_LENGTH} characters."
        )
    if not IDENTIFIER_RE.match(identifier):
        raise ValueError(
            "Report identifier may only contain letters, numbers, dots, dashes "
            "and underscores."
        )
    return identifier


def split_key(key: str) -> Tuple[str, str]:
    """Split a key into ``(namespace, remainder)`` on the **first** dot.

    Splitting on the first dot only is what makes question identifiers that
    themselves contain dots (which pretix allows) unambiguous.
    """
    validate_key(key)
    namespace, remainder = key.split(KEY_SEPARATOR, 1)
    return namespace, remainder


def question_field_key(identifier: str) -> str:
    """Build the key for the answer to the question with this identifier."""
    key = f"{NS_ANSWER}{KEY_SEPARATOR}{identifier}"
    validate_key(key)
    return key


def meta_field_key(scope: str, name: str) -> str:
    """Build the key for a meta property, e.g. ``meta.event.campaign``.

    *scope* is ``event``, ``subevent``, ``item`` or ``variation``.
    """
    key = f"{NS_META}{KEY_SEPARATOR}{scope}{KEY_SEPARATOR}{name}"
    validate_key(key)
    return key


def plugin_field_key(app_label: str, name: str) -> str:
    """Build a collision-free key for a field contributed by another plugin.

    *app_label* is the Django app label of the contributing plugin. Django
    guarantees app labels are unique within an installation, so two plugins can
    never produce the same key -- which is exactly the guarantee SPEC.md F5
    asks for.
    """
    key = f"{NS_PLUGIN}{KEY_SEPARATOR}{app_label}{KEY_SEPARATOR}{name}"
    validate_key(key)
    return key


def is_plugin_key(key: str) -> bool:
    """True if *key* belongs to the third-party namespace."""
    return key.split(KEY_SEPARATOR, 1)[0] == NS_PLUGIN


# ---------------------------------------------------------------------------
# ReportField
# ---------------------------------------------------------------------------

#: A human-readable label. ``str``, a Django lazy translation object or a
#: pretix ``LazyI18nString`` -- anything that survives ``str()``. Typed loosely
#: so this module needs no Django import.
Label = Any


@dataclass(frozen=True)
class FieldContext:
    """What a field's callables get to work with.

    Passed to :attr:`ReportField.annotation` and :attr:`ReportField.choices`.
    A frozen object rather than a bare ``event`` argument so that adding
    context later does not break every callable ever written by a plugin.
    """

    event: Any
    """The ``pretix.base.models.Event`` the report runs against."""

    base: Base
    """The report base the field was requested for."""


@dataclass(frozen=True)
class ReportField:
    """One selectable field in the report builder.

    Instances are created by the registry (core fields, questions, meta
    properties) or returned by third-party plugins through the
    ``register_report_fields`` signal (SPEC.md F5). They are immutable and
    hashable-by-key; a registry is a ``Mapping[str, ReportField]``.

    Exactly one of :attr:`orm_path`, :attr:`annotation` or :attr:`value_getter`
    is *required* -- but the combinations are not free:

    ==================================  =================================
    Declared                            Capabilities
    ==================================  =================================
    ``orm_path``                        display, filter, sort, aggregate
    ``annotation`` + ``orm_path``       display, filter, sort, aggregate
    ``value_getter`` only               display only
    ==================================  =================================

    A field that only has a ``value_getter`` is computed in Python and can
    therefore neither be filtered nor sorted in the database; declaring
    ``filter_operators`` or ``sortable`` on it is a contract error, not a
    silent downgrade. That rule exists so nobody builds a filter that quietly
    does nothing.
    """

    key: str
    """Stable, portable identifier. See the module docstring for the grammar."""

    label: Label
    """Human-readable name shown in the field library and as column header."""

    group: str
    """UI grouping, e.g. :data:`GROUP_ORDER`. Free string; see ``GROUP_*``."""

    datatype: DataType
    """Semantic type. Drives widgets, default operators and rendering."""

    bases: Tuple[Base, ...]
    """Report bases this field may be used on. Must not be empty."""

    orm_path: Optional[str] = None
    """Django lookup path relative to the base model, e.g. ``order__code``.

    Never built from user input, never read from JSON. If :attr:`annotation` is
    set, this must be the alias of the annotation that carries the value.
    """

    annotation: Optional[Callable[[FieldContext], Mapping[str, Any]]] = None
    """``ctx -> {alias: expression}`` added to the queryset via ``annotate()``.

    Must include :attr:`orm_path` as one of its aliases. Use ``Subquery``/
    ``Coalesce`` rather than per-row queries: reports run against events with
    six-digit position counts (SPEC.md section 4).
    """

    value_getter: Optional[Callable[[Any], Any]] = None
    """``row_object -> cell value``. Rendering only, executed per row.

    Gets the object the queryset yields. Must not hit the database.
    """

    filter_operators: Tuple[Operator, ...] = ()
    """Operators the editor offers and the compiler accepts for this field."""

    sortable: bool = False
    """Whether this field may appear in ``sorting``.

    Base-dependent: the registry may return the same key as sortable for base
    ``orderposition`` and not sortable for base ``order``, because sorting by
    an aggregated value is out of scope for v1.
    """

    choices: Optional[Callable[[FieldContext], Sequence[Tuple[Any, Any]]]] = None
    """``ctx -> [(value, label), ...]``, evaluated lazily and per event.

    Values must be JSON-serialisable: they end up in the stored definition.
    If they are event-local (primary keys), set :attr:`value_scope` to
    :attr:`ValueScope.EVENT`.
    """

    aggregates: Tuple[Aggregate, ...] = ()
    """Aggregations allowed for this field. Empty means "cannot be aggregated"."""

    requires_aggregate_on: Tuple[Base, ...] = ()
    """Bases on which a column referencing this field *must* carry an aggregate.

    This is how position-level data reaches an ``order``-based report
    (SPEC.md F3). Must be a subset of :attr:`bases` and implies a non-empty
    :attr:`aggregates`.
    """

    select_related: Tuple[str, ...] = ()
    """Paths the compiler should ``select_related()`` when this field is used."""

    prefetch_related: Tuple[str, ...] = ()
    """Paths the compiler should ``prefetch_related()`` when this field is used."""

    value_scope: ValueScope = ValueScope.GLOBAL
    """Whether stored *filter values* for this field are portable between events."""

    provider: str = PROVIDER_CORE
    """:data:`PROVIDER_CORE` or the Django app label of the contributing plugin."""

    help_text: Label = None
    """Optional explanation shown in the field library."""

    deprecated: bool = False
    """Still resolvable, but hidden from the field library. For renamed fields."""

    extra: Mapping[str, Any] = dataclass_field(default_factory=dict)
    """Free-form room for the provider. Never interpreted by the contracts."""

    # -- normalisation and invariants ------------------------------------

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "datatype", _coerce(DataType, self.datatype, "datatype")
        )
        object.__setattr__(self, "bases", _coerce_tuple(Base, self.bases, "bases"))
        object.__setattr__(
            self,
            "filter_operators",
            _coerce_tuple(Operator, self.filter_operators, "filter_operators"),
        )
        object.__setattr__(
            self, "aggregates", _coerce_tuple(Aggregate, self.aggregates, "aggregates")
        )
        object.__setattr__(
            self,
            "requires_aggregate_on",
            _coerce_tuple(Base, self.requires_aggregate_on, "requires_aggregate_on"),
        )
        object.__setattr__(
            self, "value_scope", _coerce(ValueScope, self.value_scope, "value_scope")
        )
        object.__setattr__(self, "select_related", tuple(self.select_related or ()))
        object.__setattr__(self, "prefetch_related", tuple(self.prefetch_related or ()))

        try:
            validate_key(self.key)
        except ValueError as e:
            raise FieldContractError(str(e)) from e

        if not self.bases:
            raise FieldContractError(f"{self.key}: 'bases' must not be empty.")
        if not self.group:
            raise FieldContractError(f"{self.key}: 'group' must not be empty.")
        if self.label is None or self.label == "":
            raise FieldContractError(f"{self.key}: 'label' must not be empty.")

        if (
            self.orm_path is None
            and self.annotation is None
            and self.value_getter is None
        ):
            raise FieldContractError(
                f"{self.key}: declare at least one of 'orm_path', 'annotation' "
                f"or 'value_getter'."
            )
        if self.annotation is not None and not self.orm_path:
            raise FieldContractError(
                f"{self.key}: a field with an annotation must also declare "
                f"'orm_path' naming the annotation alias that holds the value."
            )
        if self.orm_path is not None and not ORM_PATH_RE.match(self.orm_path):
            raise FieldContractError(
                f"{self.key}: {self.orm_path!r} is not a plausible ORM path."
            )
        if self.orm_path is None:
            if self.filter_operators:
                raise FieldContractError(
                    f"{self.key}: a Python-only field (value_getter without "
                    f"orm_path) cannot be filtered; drop 'filter_operators'."
                )
            if self.sortable:
                raise FieldContractError(
                    f"{self.key}: a Python-only field cannot be sorted in the "
                    f"database; set sortable=False."
                )
            if self.aggregates:
                raise FieldContractError(
                    f"{self.key}: a Python-only field cannot be aggregated."
                )

        unknown_bases = set(self.requires_aggregate_on) - set(self.bases)
        if unknown_bases:
            raise FieldContractError(
                f"{self.key}: 'requires_aggregate_on' must be a subset of 'bases', "
                f"unexpected: {sorted(str(b) for b in unknown_bases)}."
            )
        if self.requires_aggregate_on and not self.aggregates:
            raise FieldContractError(
                f"{self.key}: 'requires_aggregate_on' needs at least one entry in "
                f"'aggregates'."
            )
        if is_plugin_key(self.key) and self.provider == PROVIDER_CORE:
            raise FieldContractError(
                f"{self.key}: plugin fields must set 'provider' to their app label."
            )
        if not is_plugin_key(self.key) and self.provider != PROVIDER_CORE:
            raise FieldContractError(
                f"{self.key}: only keys in the '{NS_PLUGIN}.' namespace may be "
                f"provided by a plugin."
            )

    # -- convenience ------------------------------------------------------

    @property
    def namespace(self) -> str:
        """Namespace part of the key, e.g. ``order``."""
        return self.key.split(KEY_SEPARATOR, 1)[0]

    def supports_base(self, base: Union[Base, str]) -> bool:
        """True if this field may be used on *base* at all."""
        return _coerce(Base, base, "base") in self.bases

    def needs_aggregate_on(self, base: Union[Base, str]) -> bool:
        """True if a column on *base* must declare an aggregate for this field."""
        return _coerce(Base, base, "base") in self.requires_aggregate_on

    def allows_operator(self, operator: Union[Operator, str]) -> bool:
        """True if *operator* may be used to filter this field."""
        try:
            return _coerce(Operator, operator, "operator") in self.filter_operators
        except FieldContractError:
            return False

    def allows_aggregate(self, aggregate: Union[Aggregate, str]) -> bool:
        """True if *aggregate* may be applied to this field."""
        try:
            return _coerce(Aggregate, aggregate, "aggregate") in self.aggregates
        except FieldContractError:
            return False


def _coerce(enum_cls: Any, value: Any, what: str) -> Any:
    try:
        return enum_cls.coerce(value)
    except ValueError as e:
        raise FieldContractError(f"Invalid {what}: {e}") from e


def _coerce_tuple(enum_cls: Any, values: Any, what: str) -> Tuple[Any, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise FieldContractError(f"Invalid {what}: expected a sequence.")
    seen = []
    for v in values:
        member = _coerce(enum_cls, v, what)
        if member not in seen:
            seen.append(member)
    return tuple(seen)
