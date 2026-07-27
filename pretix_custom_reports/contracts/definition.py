"""The definition JSON: dataclasses, limits and a strict structural validator.

Owner: contract-architect (wave 0c). Frozen -- see ``contracts/__init__.py``.

Scope of this module -- read this before adding anything
--------------------------------------------------------

This validator checks **structure only**:

* every key is known, every value has the right JSON type
* ``schema_version`` is supported
* field keys are *well-formed* (grammar from :mod:`~.fields`)
* operators and aggregates are members of the enums
* the ``value`` matches the operator's :class:`~.fields.ValueKind`
* size limits and nesting depth hold

It deliberately does **not** check:

* whether a field key exists -- that needs an event and the registry
* whether an operator is allowed *for that field* -- ditto
* whether a field is sortable, aggregatable or available on the base -- ditto

The split is not cosmetic. It is what lets ``registry-dev``, ``query-dev``,
``persistence-dev`` and ``frontend-dev`` work in parallel: persistence can
validate and store JSON without a registry, the editor can validate a draft
without a round trip, and the registry stage is a separate, well-named second
pass (:class:`~.errors.FieldResolutionError`). See docs/adr/0001-contracts.md
section 4.

Layout of the document::

    {
      "schema_version": 1,
      "base": "order" | "orderposition",
      "columns":  [ {"field": "...", "label": null, "aggregate": null,
                     "format": {...}, "hidden": false}, ... ],
      "filters":  {"op": "and", "children": [ <condition> | <group>, ... ]},
      "sorting":  [ {"field": "...", "direction": "asc"|"desc"}, ... ],
      "options":  {"include_canceled_positions": false,
                   "include_testmode_orders": false,
                   "row_limit": null}
    }

``filters`` allows exactly one level of nesting (SPEC.md F6: "no arbitrarily
deep nesting in v1"): the root group holds conditions and groups, a nested
group holds conditions only.
"""

from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import json
from dataclasses import dataclass, replace

from pretix_custom_reports.contracts.errors import DefinitionValidationError
from pretix_custom_reports.contracts.fields import (
    OPERATOR_SPECS,
    Aggregate,
    Base,
    Operator,
    SortDirection,
    ValueKind,
    _ValueEnum,
    validate_key,
)

__all__ = [
    "BooleanStyle",
    "BoolOp",
    "Column",
    "ColumnFormat",
    "DateStyle",
    "ErrorCode",
    "FieldReference",
    "FieldUsage",
    "FilterCondition",
    "FilterGroup",
    "MAX_COLUMNS",
    "MAX_FILTER_CONDITIONS",
    "MAX_FILTER_GROUPS",
    "MAX_GROUP_CHILDREN",
    "MAX_LABEL_LENGTH",
    "MAX_ROW_LIMIT",
    "MAX_SEPARATOR_LENGTH",
    "MAX_SORT_ENTRIES",
    "MAX_STRING_VALUE_LENGTH",
    "MAX_VALUE_ITEMS",
    "NumberStyle",
    "PortableReport",
    "ReportDefinition",
    "ReportOptions",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "SortEntry",
    "ValidationIssue",
    "empty_definition",
    "validate_definition",
    "validate_definition_json",
    "validate_portable_document",
]


# ---------------------------------------------------------------------------
# Version and limits
# ---------------------------------------------------------------------------

#: Version of the definition JSON. Bumped only for *incompatible* changes;
#: adding an optional key with a backwards-compatible default does not bump it.
SCHEMA_VERSION = 1

#: Versions this build can read. A future build that can still read v1 keeps it
#: in this tuple and migrates on load.
SUPPORTED_SCHEMA_VERSIONS: Tuple[int, ...] = (1,)

MAX_COLUMNS = 200
MAX_SORT_ENTRIES = 8
MAX_FILTER_CONDITIONS = 100
MAX_FILTER_GROUPS = 25
MAX_GROUP_CHILDREN = 50
MAX_VALUE_ITEMS = 500
MAX_STRING_VALUE_LENGTH = 1000
MAX_LABEL_LENGTH = 200
MAX_SEPARATOR_LENGTH = 8
MAX_ROW_LIMIT = 1_000_000
MAX_DAY_COUNT = 3650

#: Rows the live preview may load at most (SPEC.md F2). Lives here so the
#: editor, the API and the tests cannot drift apart.
PREVIEW_ROW_LIMIT = 20


class ErrorCode:
    """Stable, translation-independent error codes.

    Assert on these in tests and switch on them in the UI; the messages are
    English prose and may change.
    """

    MISSING = "missing"
    UNKNOWN_KEY = "unknown_key"
    WRONG_TYPE = "wrong_type"
    INVALID_VALUE = "invalid_value"
    INVALID_FIELD_KEY = "invalid_field_key"
    UNKNOWN_OPERATOR = "unknown_operator"
    UNKNOWN_AGGREGATE = "unknown_aggregate"
    UNKNOWN_BASE = "unknown_base"
    UNKNOWN_DIRECTION = "unknown_direction"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
    VALUE_SHAPE_MISMATCH = "value_shape_mismatch"
    TOO_MANY = "too_many"
    TOO_LONG = "too_long"
    DUPLICATE = "duplicate"
    EMPTY = "empty"
    TOO_DEEP = "too_deep"
    AMBIGUOUS_NODE = "ambiguous_node"
    NOT_JSON = "not_json"


@dataclass(frozen=True)
class ValidationIssue:
    """One structural problem, addressed by a JSON-pointer-ish path."""

    path: str
    """Where the problem is, e.g. ``columns[2].aggregate``."""

    code: str
    """One of :class:`ErrorCode`."""

    message: str
    """English prose for developers and for the raw API error payload."""

    def as_dict(self) -> Dict[str, str]:
        """JSON-serialisable form, for API responses."""
        return {"path": self.path, "code": self.code, "message": self.message}


# ---------------------------------------------------------------------------
# Small enums used by the document
# ---------------------------------------------------------------------------


class BoolOp(_ValueEnum):
    """Boolean operator joining the children of a filter group."""

    AND = "and"
    OR = "or"


class DateStyle(_ValueEnum):
    """Named date/datetime formats.

    Named styles rather than free strftime patterns on purpose: the stored
    definition is untrusted input, and a format string is code-ish enough that
    an allow-list is cheaper than reasoning about it.
    """

    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"
    ISO = "iso"
    DATE_ONLY = "date_only"
    TIME_ONLY = "time_only"


class NumberStyle(_ValueEnum):
    """How numeric values are written into a cell.

    ``RAW`` keeps the ``Decimal``/``int`` so XLSX gets a real number;
    ``LOCALIZED`` and ``CURRENCY`` produce strings. Note that ``ListExporter``
    localises ``Decimal`` in the CSV path but not in the XLSX path
    (docs/pretix-api-notes.md section 1, pitfall 3).
    """

    RAW = "raw"
    LOCALIZED = "localized"
    CURRENCY = "currency"


class BooleanStyle(_ValueEnum):
    """How booleans are written into a cell."""

    YES_NO = "yes_no"
    TRUE_FALSE = "true_false"
    ONE_ZERO = "one_zero"


class FieldUsage(_ValueEnum):
    """Where in the document a field key is referenced."""

    COLUMN = "column"
    FILTER = "filter"
    SORT = "sort"


# ---------------------------------------------------------------------------
# Document dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnFormat:
    """Per-column rendering options (SPEC.md F2).

    All members optional; ``None`` means "use the renderer's default for the
    field's datatype".
    """

    date_style: Optional[DateStyle] = None
    number_style: Optional[NumberStyle] = None
    boolean_style: Optional[BooleanStyle] = None
    separator: Optional[str] = None
    """Separator for :attr:`~.fields.Aggregate.JOIN` columns. Default ``", "``."""

    def is_empty(self) -> bool:
        """True if nothing is overridden."""
        return not any(
            (self.date_style, self.number_style, self.boolean_style, self.separator)
        )

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.date_style is not None:
            out["date_style"] = self.date_style.value
        if self.number_style is not None:
            out["number_style"] = self.number_style.value
        if self.boolean_style is not None:
            out["boolean_style"] = self.boolean_style.value
        if self.separator is not None:
            out["separator"] = self.separator
        return out


@dataclass(frozen=True)
class Column:
    """One output column."""

    field: str
    """Registry key. Never an ORM path."""

    label: Optional[str] = None
    """Override for the field's own label. ``None`` = use the field's label."""

    aggregate: Optional[Aggregate] = None
    """Required when the field is one-to-many for the report base (SPEC.md F3)."""

    format: Optional[ColumnFormat] = None
    hidden: bool = False
    """Kept in the definition but not written to the output.

    Useful to keep a column around while trying things out. The compiler drops
    hidden columns; :attr:`CompiledReport.columns` never contains them.
    """

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"field": self.field}
        if self.label is not None:
            out["label"] = self.label
        if self.aggregate is not None:
            out["aggregate"] = self.aggregate.value
        if self.format is not None and not self.format.is_empty():
            out["format"] = self.format.as_dict()
        if self.hidden:
            out["hidden"] = True
        return out


@dataclass(frozen=True)
class FilterCondition:
    """A single ``field <operator> value`` test."""

    field: str
    operator: Operator
    value: Any = None

    @property
    def value_kind(self) -> ValueKind:
        """Shape the operator expects."""
        return OPERATOR_SPECS[self.operator].value_kind

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "field": self.field,
            "operator": self.operator.value,
        }
        if self.value_kind is not ValueKind.NONE:
            out["value"] = self.value
        return out


@dataclass(frozen=True)
class FilterGroup:
    """A boolean group. One nesting level only (SPEC.md F6)."""

    op: BoolOp
    children: Tuple[Union["FilterCondition", "FilterGroup"], ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "op": self.op.value,
            "children": [c.as_dict() for c in self.children],
        }

    def iter_conditions(self) -> Iterator[Tuple[str, FilterCondition]]:
        """Yield ``(path, condition)`` for every condition, depth first."""
        for i, child in enumerate(self.children):
            path = f"filters.children[{i}]"
            if isinstance(child, FilterCondition):
                yield path, child
            else:
                for j, grandchild in enumerate(child.children):
                    if isinstance(grandchild, FilterCondition):
                        yield f"{path}.children[{j}]", grandchild


@dataclass(frozen=True)
class SortEntry:
    """One stage of the multi-level sorting (SPEC.md F7)."""

    field: str
    direction: SortDirection = SortDirection.ASC

    def as_dict(self) -> Dict[str, Any]:
        return {"field": self.field, "direction": self.direction.value}


@dataclass(frozen=True)
class ReportOptions:
    """Report-wide switches.

    Kept deliberately short. Anything expressible as a filter belongs in
    ``filters``, not here -- for example "only paid orders" is
    ``order.status in ["p"]``, not an option.
    """

    include_canceled_positions: bool = False
    """Use ``OrderPosition.all`` instead of ``OrderPosition.objects``.

    ``OrderPosition.objects`` filters ``canceled=False``
    (docs/pretix-api-notes.md section 6.2). Off by default because "what was
    sold" is the common question; accounting reports turn it on.
    """

    include_testmode_orders: bool = False
    """Include orders with ``testmode=True``. Off by default."""

    row_limit: Optional[int] = None
    """Hard cap on output rows, ``1..MAX_ROW_LIMIT``. ``None`` = no cap."""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "include_canceled_positions": self.include_canceled_positions,
            "include_testmode_orders": self.include_testmode_orders,
            "row_limit": self.row_limit,
        }


@dataclass(frozen=True)
class FieldReference:
    """One place in the document where a field key is used.

    Produced by :meth:`ReportDefinition.iter_field_references`. This is the
    hand-over point between structural validation and registry validation.
    """

    path: str
    key: str
    usage: FieldUsage
    aggregate: Optional[Aggregate] = None
    operator: Optional[Operator] = None


@dataclass(frozen=True)
class ReportDefinition:
    """A structurally valid report definition.

    Only ever created by :func:`validate_definition` (or by hand in tests).
    Holding an instance means "the structure is sound"; it does *not* mean the
    fields exist.
    """

    base: Base
    columns: Tuple[Column, ...]
    filters: Optional[FilterGroup] = None
    sorting: Tuple[SortEntry, ...] = ()
    options: ReportOptions = ReportOptions()
    schema_version: int = SCHEMA_VERSION

    # -- construction -----------------------------------------------------

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReportDefinition":
        """Alias for :func:`validate_definition`."""
        return validate_definition(data)

    # -- serialisation ----------------------------------------------------

    def as_dict(self) -> Dict[str, Any]:
        """Canonical JSON-serialisable form.

        Round-trip stable: ``validate_definition(d.as_dict()) == d``.
        """
        out: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "base": self.base.value,
            "columns": [c.as_dict() for c in self.columns],
        }
        if self.filters is not None:
            out["filters"] = self.filters.as_dict()
        out["sorting"] = [s.as_dict() for s in self.sorting]
        out["options"] = self.options.as_dict()
        return out

    def as_json(self, **kwargs: Any) -> str:
        """Canonical JSON string. ``sort_keys`` is *not* forced -- key order is
        already canonical and more readable than alphabetical."""
        kwargs.setdefault("ensure_ascii", False)
        return json.dumps(self.as_dict(), **kwargs)

    # -- helpers used by every downstream agent ---------------------------

    def iter_field_references(self) -> Iterator[FieldReference]:
        """Yield every field key used anywhere, with its context.

        Registry-stage validation is a loop over this::

            missing = [
                ref for ref in definition.iter_field_references()
                if registry.resolve(ref.key, event, definition.base) is None
            ]
        """
        for i, column in enumerate(self.columns):
            yield FieldReference(
                path=f"columns[{i}]",
                key=column.field,
                usage=FieldUsage.COLUMN,
                aggregate=column.aggregate,
            )
        if self.filters is not None:
            for path, condition in self.filters.iter_conditions():
                yield FieldReference(
                    path=path,
                    key=condition.field,
                    usage=FieldUsage.FILTER,
                    operator=condition.operator,
                )
        for i, entry in enumerate(self.sorting):
            yield FieldReference(
                path=f"sorting[{i}]",
                key=entry.field,
                usage=FieldUsage.SORT,
            )

    def field_keys(self) -> Tuple[str, ...]:
        """All referenced keys, de-duplicated, in order of first appearance."""
        seen: List[str] = []
        for ref in self.iter_field_references():
            if ref.key not in seen:
                seen.append(ref.key)
        return tuple(seen)

    def replace(self, **kwargs: Any) -> "ReportDefinition":
        """Copy with fields replaced (``dataclasses.replace``)."""
        return replace(self, **kwargs)


def empty_definition(base: Union[Base, str] = Base.ORDER) -> Dict[str, Any]:
    """A minimal valid document skeleton, for the editor's "new report" state.

    Has no columns and therefore does **not** validate -- see the note on
    ``columns`` in :func:`validate_definition`. It is a UI starting point, not
    something to store.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "base": str(Base.coerce(base)),
        "columns": [],
        "filters": None,
        "sorting": [],
        "options": ReportOptions().as_dict(),
    }


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

_SCALAR_TYPES = (str, int, float, bool)

_DEFINITION_KEYS = frozenset(
    {"schema_version", "base", "columns", "filters", "sorting", "options"}
)
_COLUMN_KEYS = frozenset({"field", "label", "aggregate", "format", "hidden"})
_FORMAT_KEYS = frozenset({"date_style", "number_style", "boolean_style", "separator"})
_CONDITION_KEYS = frozenset({"field", "operator", "value"})
_GROUP_KEYS = frozenset({"op", "children"})
_SORT_KEYS = frozenset({"field", "direction"})
_OPTION_KEYS = frozenset(
    {"include_canceled_positions", "include_testmode_orders", "row_limit"}
)
_PORTABLE_KEYS = frozenset(
    {
        "schema_version",
        "name",
        "description",
        "definition",
        "exported_at",
        "generator",
        "source",
        "meta",
    }
)


class _Collector:
    """Collects issues instead of raising on the first one."""

    def __init__(self) -> None:
        self.issues: List[ValidationIssue] = []

    def add(self, path: str, code: str, message: str) -> None:
        self.issues.append(ValidationIssue(path=path, code=code, message=message))

    def __bool__(self) -> bool:
        return bool(self.issues)


def _require_mapping(c: _Collector, value: Any, path: str) -> Optional[Mapping]:
    if not isinstance(value, Mapping):
        c.add(path, ErrorCode.WRONG_TYPE, f"Expected an object, got {_tn(value)}.")
        return None
    return value


def _require_list(c: _Collector, value: Any, path: str) -> Optional[Sequence]:
    if not isinstance(value, (list, tuple)):
        c.add(path, ErrorCode.WRONG_TYPE, f"Expected an array, got {_tn(value)}.")
        return None
    return value


def _tn(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return type(value).__name__


def _reject_unknown(
    c: _Collector, data: Mapping, allowed: frozenset, path: str
) -> None:
    for key in data:
        if key not in allowed:
            c.add(
                f"{path}.{key}" if path else str(key),
                ErrorCode.UNKNOWN_KEY,
                "Unknown key {!r}. Allowed: {}.".format(
                    key, ", ".join(sorted(allowed))
                ),
            )


def _enum(
    c: _Collector, enum_cls: Any, value: Any, path: str, code: str
) -> Optional[Any]:
    try:
        return enum_cls.coerce(value)
    except ValueError as e:
        c.add(path, code, str(e))
        return None


def _field_key(c: _Collector, value: Any, path: str) -> Optional[str]:
    try:
        return validate_key(value)
    except ValueError as e:
        c.add(path, ErrorCode.INVALID_FIELD_KEY, str(e))
        return None


def _optional_label(c: _Collector, value: Any, path: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        c.add(
            path, ErrorCode.WRONG_TYPE, f"Expected a string or null, got {_tn(value)}."
        )
        return None
    if len(value) > MAX_LABEL_LENGTH:
        c.add(
            path,
            ErrorCode.TOO_LONG,
            f"Label exceeds {MAX_LABEL_LENGTH} characters.",
        )
        return None
    return value


def _is_scalar(value: Any) -> bool:
    return isinstance(value, _SCALAR_TYPES)


def _check_scalar(c: _Collector, value: Any, path: str) -> bool:
    if not _is_scalar(value):
        c.add(
            path,
            ErrorCode.VALUE_SHAPE_MISMATCH,
            f"Expected a string, number or boolean, got {_tn(value)}.",
        )
        return False
    if isinstance(value, str) and len(value) > MAX_STRING_VALUE_LENGTH:
        c.add(
            path,
            ErrorCode.TOO_LONG,
            f"Filter value exceeds {MAX_STRING_VALUE_LENGTH} characters.",
        )
        return False
    return True


def _check_value(c: _Collector, operator: Operator, value: Any, path: str) -> Any:
    """Check ``value`` against the operator's :class:`ValueKind`.

    This is as far as structural validation can go without a registry -- and it
    already catches the common "wrong type in the filter value" mistakes.
    """
    kind = OPERATOR_SPECS[operator].value_kind
    vpath = f"{path}.value"

    if kind is ValueKind.NONE:
        if value is not None:
            c.add(
                vpath,
                ErrorCode.VALUE_SHAPE_MISMATCH,
                f"Operator {operator} takes no value.",
            )
        return None

    if kind is ValueKind.SCALAR:
        if value is None:
            c.add(vpath, ErrorCode.MISSING, f"Operator {operator} requires a value.")
            return None
        return value if _check_scalar(c, value, vpath) else None

    if kind is ValueKind.DAY_COUNT:
        if isinstance(value, bool) or not isinstance(value, int):
            c.add(
                vpath,
                ErrorCode.VALUE_SHAPE_MISMATCH,
                f"Operator {operator} requires a whole number of days, "
                f"got {_tn(value)}.",
            )
            return None
        if not 1 <= value <= MAX_DAY_COUNT:
            c.add(
                vpath,
                ErrorCode.INVALID_VALUE,
                f"Number of days must be between 1 and {MAX_DAY_COUNT}.",
            )
            return None
        return value

    if not isinstance(value, (list, tuple)):
        c.add(
            vpath,
            ErrorCode.VALUE_SHAPE_MISMATCH,
            f"Operator {operator} requires an array of values, got {_tn(value)}.",
        )
        return None
    items: Sequence[Any] = value

    if kind is ValueKind.RANGE:
        if len(items) != 2:
            c.add(
                vpath,
                ErrorCode.VALUE_SHAPE_MISMATCH,
                f"Operator {operator} requires exactly two values, got {len(items)}.",
            )
            return None
    else:  # ValueKind.LIST
        if not items:
            c.add(
                vpath,
                ErrorCode.EMPTY,
                f"Operator {operator} requires at least one value.",
            )
            return None
        if len(items) > MAX_VALUE_ITEMS:
            c.add(
                vpath,
                ErrorCode.TOO_MANY,
                f"At most {MAX_VALUE_ITEMS} values allowed, got {len(items)}.",
            )
            return None

    ok = True
    for i, item in enumerate(items):
        ok = _check_scalar(c, item, f"{vpath}[{i}]") and ok
    return list(items) if ok else None


def _validate_format(c: _Collector, raw: Any, path: str) -> Optional[ColumnFormat]:
    data = _require_mapping(c, raw, path)
    if data is None:
        return None
    _reject_unknown(c, data, _FORMAT_KEYS, path)

    date_style = None
    if data.get("date_style") is not None:
        date_style = _enum(
            c,
            DateStyle,
            data["date_style"],
            f"{path}.date_style",
            ErrorCode.INVALID_VALUE,
        )
    number_style = None
    if data.get("number_style") is not None:
        number_style = _enum(
            c,
            NumberStyle,
            data["number_style"],
            f"{path}.number_style",
            ErrorCode.INVALID_VALUE,
        )
    boolean_style = None
    if data.get("boolean_style") is not None:
        boolean_style = _enum(
            c,
            BooleanStyle,
            data["boolean_style"],
            f"{path}.boolean_style",
            ErrorCode.INVALID_VALUE,
        )
    separator = data.get("separator")
    if separator is not None:
        if not isinstance(separator, str):
            c.add(
                f"{path}.separator",
                ErrorCode.WRONG_TYPE,
                f"Expected a string, got {_tn(separator)}.",
            )
            separator = None
        elif len(separator) > MAX_SEPARATOR_LENGTH:
            c.add(
                f"{path}.separator",
                ErrorCode.TOO_LONG,
                f"Separator must be at most {MAX_SEPARATOR_LENGTH} characters.",
            )
            separator = None

    return ColumnFormat(
        date_style=date_style,
        number_style=number_style,
        boolean_style=boolean_style,
        separator=separator,
    )


def _validate_column(c: _Collector, raw: Any, path: str) -> Optional[Column]:
    data = _require_mapping(c, raw, path)
    if data is None:
        return None
    _reject_unknown(c, data, _COLUMN_KEYS, path)

    if "field" not in data:
        c.add(f"{path}.field", ErrorCode.MISSING, "A column must name a field.")
        return None
    key = _field_key(c, data["field"], f"{path}.field")

    label = _optional_label(c, data.get("label"), f"{path}.label")

    aggregate = None
    if data.get("aggregate") is not None:
        aggregate = _enum(
            c,
            Aggregate,
            data["aggregate"],
            f"{path}.aggregate",
            ErrorCode.UNKNOWN_AGGREGATE,
        )

    fmt = None
    if data.get("format") is not None:
        fmt = _validate_format(c, data["format"], f"{path}.format")

    hidden = data.get("hidden", False)
    if not isinstance(hidden, bool):
        c.add(
            f"{path}.hidden",
            ErrorCode.WRONG_TYPE,
            f"Expected a boolean, got {_tn(hidden)}.",
        )
        hidden = False

    if key is None:
        return None
    return Column(
        field=key, label=label, aggregate=aggregate, format=fmt, hidden=hidden
    )


def _validate_condition(
    c: _Collector, data: Mapping, path: str
) -> Optional[FilterCondition]:
    _reject_unknown(c, data, _CONDITION_KEYS, path)

    key = _field_key(c, data.get("field"), f"{path}.field")
    if "operator" not in data:
        c.add(f"{path}.operator", ErrorCode.MISSING, "A filter needs an operator.")
        return None
    operator = _enum(
        c, Operator, data["operator"], f"{path}.operator", ErrorCode.UNKNOWN_OPERATOR
    )
    if operator is None or key is None:
        return None

    value = _check_value(c, operator, data.get("value"), path)
    return FilterCondition(field=key, operator=operator, value=value)


def _validate_group(
    c: _Collector, raw: Any, path: str, depth: int, counts: Dict[str, int]
) -> Optional[FilterGroup]:
    data = _require_mapping(c, raw, path)
    if data is None:
        return None
    _reject_unknown(c, data, _GROUP_KEYS, path)

    op = _enum(c, BoolOp, data.get("op"), f"{path}.op", ErrorCode.INVALID_VALUE)
    children_raw = _require_list(c, data.get("children", []), f"{path}.children")
    if children_raw is None:
        return None

    if len(children_raw) > MAX_GROUP_CHILDREN:
        c.add(
            f"{path}.children",
            ErrorCode.TOO_MANY,
            f"At most {MAX_GROUP_CHILDREN} entries per group.",
        )
        return None
    if depth > 0 and not children_raw:
        c.add(
            f"{path}.children",
            ErrorCode.EMPTY,
            "A nested filter group must contain at least one condition; an empty "
            "OR group has no defined meaning.",
        )
        return None

    children: List[Union[FilterCondition, FilterGroup]] = []
    for i, child in enumerate(children_raw):
        cpath = f"{path}.children[{i}]"
        cdata = _require_mapping(c, child, cpath)
        if cdata is None:
            continue

        is_group = "op" in cdata or "children" in cdata
        is_condition = "field" in cdata or "operator" in cdata
        if is_group and is_condition:
            c.add(
                cpath,
                ErrorCode.AMBIGUOUS_NODE,
                "A filter node is either a group ('op'/'children') or a condition "
                "('field'/'operator'), never both.",
            )
            continue
        if not is_group and not is_condition:
            c.add(
                cpath,
                ErrorCode.AMBIGUOUS_NODE,
                "A filter node must be a group ('op'/'children') or a condition "
                "('field'/'operator').",
            )
            continue

        if is_group:
            if depth >= 1:
                c.add(
                    cpath,
                    ErrorCode.TOO_DEEP,
                    "Filters allow exactly one level of nesting (SPEC.md F6).",
                )
                continue
            counts["groups"] += 1
            if counts["groups"] > MAX_FILTER_GROUPS:
                c.add(
                    cpath,
                    ErrorCode.TOO_MANY,
                    f"At most {MAX_FILTER_GROUPS} filter groups.",
                )
                continue
            nested = _validate_group(c, cdata, cpath, depth + 1, counts)
            if nested is not None:
                children.append(nested)
        else:
            counts["conditions"] += 1
            if counts["conditions"] > MAX_FILTER_CONDITIONS:
                c.add(
                    cpath,
                    ErrorCode.TOO_MANY,
                    f"At most {MAX_FILTER_CONDITIONS} filter conditions.",
                )
                continue
            condition = _validate_condition(c, cdata, cpath)
            if condition is not None:
                children.append(condition)

    if op is None:
        return None
    return FilterGroup(op=op, children=tuple(children))


def _validate_sorting(c: _Collector, raw: Any) -> Tuple[SortEntry, ...]:
    entries = _require_list(c, raw, "sorting")
    if entries is None:
        return ()
    if len(entries) > MAX_SORT_ENTRIES:
        c.add(
            "sorting",
            ErrorCode.TOO_MANY,
            f"At most {MAX_SORT_ENTRIES} sorting stages.",
        )
        return ()

    out: List[SortEntry] = []
    seen: List[str] = []
    for i, raw_entry in enumerate(entries):
        path = f"sorting[{i}]"
        data = _require_mapping(c, raw_entry, path)
        if data is None:
            continue
        _reject_unknown(c, data, _SORT_KEYS, path)
        key = _field_key(c, data.get("field"), f"{path}.field")
        direction = SortDirection.ASC
        if data.get("direction") is not None:
            coerced = _enum(
                c,
                SortDirection,
                data["direction"],
                f"{path}.direction",
                ErrorCode.UNKNOWN_DIRECTION,
            )
            if coerced is None:
                continue
            direction = coerced
        if key is None:
            continue
        if key in seen:
            c.add(
                f"{path}.field",
                ErrorCode.DUPLICATE,
                f"Field {key!r} is already used in an earlier sorting stage.",
            )
            continue
        seen.append(key)
        out.append(SortEntry(field=key, direction=direction))
    return tuple(out)


def _validate_options(c: _Collector, raw: Any) -> ReportOptions:
    data = _require_mapping(c, raw, "options")
    if data is None:
        return ReportOptions()
    _reject_unknown(c, data, _OPTION_KEYS, "options")

    def _flag(name: str, default: bool) -> bool:
        value = data.get(name, default)
        if not isinstance(value, bool):
            c.add(
                f"options.{name}",
                ErrorCode.WRONG_TYPE,
                f"Expected a boolean, got {_tn(value)}.",
            )
            return default
        return value

    row_limit = data.get("row_limit")
    if row_limit is not None:
        if isinstance(row_limit, bool) or not isinstance(row_limit, int):
            c.add(
                "options.row_limit",
                ErrorCode.WRONG_TYPE,
                f"Expected a whole number or null, got {_tn(row_limit)}.",
            )
            row_limit = None
        elif not 1 <= row_limit <= MAX_ROW_LIMIT:
            c.add(
                "options.row_limit",
                ErrorCode.INVALID_VALUE,
                f"Row limit must be between 1 and {MAX_ROW_LIMIT}.",
            )
            row_limit = None

    return ReportOptions(
        include_canceled_positions=_flag("include_canceled_positions", False),
        include_testmode_orders=_flag("include_testmode_orders", False),
        row_limit=row_limit,
    )


def validate_definition(data: Any) -> ReportDefinition:
    """Validate the *structure* of a definition document.

    :param data: the parsed JSON object (a mapping).
    :returns: a :class:`ReportDefinition` with everything coerced to enums.
    :raises DefinitionValidationError: with **all** issues found, not just the
        first one, so the editor can highlight them in one go.

    ``columns`` must contain at least one entry: a report without columns can
    only ever produce an empty export, which pretix turns into a soft failure
    plus a mail on every scheduled run (docs/pretix-api-notes.md section 5.6).
    Rejecting it here is friendlier than shipping it.
    """
    c = _Collector()

    top = _require_mapping(c, data, "")
    if top is None:
        raise DefinitionValidationError(c.issues)

    _reject_unknown(c, top, _DEFINITION_KEYS, "")

    version = top.get("schema_version")
    if version is None:
        c.add(
            "schema_version",
            ErrorCode.MISSING,
            "The definition must carry its own schema_version.",
        )
    elif isinstance(version, bool) or not isinstance(version, int):
        c.add(
            "schema_version",
            ErrorCode.WRONG_TYPE,
            f"Expected a whole number, got {_tn(version)}.",
        )
        version = None
    elif version not in SUPPORTED_SCHEMA_VERSIONS:
        c.add(
            "schema_version",
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            "schema_version {} is not supported by this version of the plugin "
            "(supported: {}).".format(
                version, ", ".join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS)
            ),
        )
        version = None

    base = None
    if "base" not in top:
        c.add("base", ErrorCode.MISSING, "The definition must name a report base.")
    else:
        base = _enum(c, Base, top["base"], "base", ErrorCode.UNKNOWN_BASE)

    columns: Tuple[Column, ...] = ()
    if "columns" not in top:
        c.add(
            "columns", ErrorCode.MISSING, "The definition must have a 'columns' array."
        )
    else:
        raw_columns = _require_list(c, top["columns"], "columns")
        if raw_columns is not None:
            if not raw_columns:
                c.add("columns", ErrorCode.EMPTY, "A report needs at least one column.")
            elif len(raw_columns) > MAX_COLUMNS:
                c.add(
                    "columns",
                    ErrorCode.TOO_MANY,
                    f"At most {MAX_COLUMNS} columns.",
                )
            else:
                built = [
                    _validate_column(c, raw, f"columns[{i}]")
                    for i, raw in enumerate(raw_columns)
                ]
                columns = tuple(col for col in built if col is not None)

    filters: Optional[FilterGroup] = None
    if top.get("filters") is not None:
        counts = {"conditions": 0, "groups": 0}
        filters = _validate_group(c, top["filters"], "filters", 0, counts)
        if filters is not None and not filters.children:
            # An empty root group means "no filter"; normalise it away so that
            # downstream code has exactly one representation of that state.
            filters = None

    sorting = _validate_sorting(c, top.get("sorting", []))
    options = _validate_options(c, top.get("options", {}))

    if c:
        raise DefinitionValidationError(c.issues)

    return ReportDefinition(
        base=base,
        columns=columns,
        filters=filters,
        sorting=sorting,
        options=options,
        schema_version=version,
    )


def validate_definition_json(text: str) -> ReportDefinition:
    """Parse and validate a JSON *string* (import via copy-paste, SPEC.md F9)."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise DefinitionValidationError(
            [ValidationIssue(path="", code=ErrorCode.NOT_JSON, message=str(e))]
        ) from e
    return validate_definition(data)


# ---------------------------------------------------------------------------
# Portable document (import/export envelope, SPEC.md F9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortableReport:
    """Envelope around a definition for file export/import.

    Strict where it matters (``schema_version``, ``definition``), lenient about
    the descriptive parts: ``meta`` is free-form and unknown content in it is
    preserved, never rejected. ``portability-dev`` owns what goes in there.
    """

    name: str
    definition: ReportDefinition
    description: Optional[str] = None
    schema_version: int = SCHEMA_VERSION
    exported_at: Optional[str] = None
    generator: Optional[str] = None
    source: Optional[str] = None
    meta: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.meta is None:
            object.__setattr__(self, "meta", {})

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "name": self.name,
            "definition": self.definition.as_dict(),
        }
        if self.description is not None:
            out["description"] = self.description
        if self.exported_at is not None:
            out["exported_at"] = self.exported_at
        if self.generator is not None:
            out["generator"] = self.generator
        if self.source is not None:
            out["source"] = self.source
        if self.meta:
            out["meta"] = dict(self.meta)
        return out


def validate_portable_document(data: Any) -> PortableReport:
    """Validate an exported report file.

    :raises DefinitionValidationError: with all issues, inner ones prefixed
        ``definition.``.
    """
    c = _Collector()
    top = _require_mapping(c, data, "")
    if top is None:
        raise DefinitionValidationError(c.issues)

    _reject_unknown(c, top, _PORTABLE_KEYS, "")

    version = top.get("schema_version")
    if version is None:
        c.add("schema_version", ErrorCode.MISSING, "Missing schema_version.")
    elif isinstance(version, bool) or not isinstance(version, int):
        c.add(
            "schema_version",
            ErrorCode.WRONG_TYPE,
            f"Expected a whole number, got {_tn(version)}.",
        )
        version = None
    elif version not in SUPPORTED_SCHEMA_VERSIONS:
        c.add(
            "schema_version",
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            f"schema_version {version} is not supported.",
        )
        version = None

    name = top.get("name")
    if not isinstance(name, str) or not name.strip():
        c.add("name", ErrorCode.MISSING, "A report file must carry a non-empty name.")
        name = None
    elif len(name) > MAX_LABEL_LENGTH:
        c.add(
            "name", ErrorCode.TOO_LONG, f"Name exceeds {MAX_LABEL_LENGTH} characters."
        )
        name = None

    description = top.get("description")
    if description is not None and not isinstance(description, str):
        c.add(
            "description",
            ErrorCode.WRONG_TYPE,
            f"Expected a string or null, got {_tn(description)}.",
        )
        description = None

    for optional in ("exported_at", "generator", "source"):
        value = top.get(optional)
        if value is not None and not isinstance(value, str):
            c.add(
                optional,
                ErrorCode.WRONG_TYPE,
                f"Expected a string or null, got {_tn(value)}.",
            )

    meta = top.get("meta")
    if meta is not None and not isinstance(meta, Mapping):
        c.add("meta", ErrorCode.WRONG_TYPE, f"Expected an object, got {_tn(meta)}.")
        meta = None

    definition: Optional[ReportDefinition] = None
    if "definition" not in top:
        c.add("definition", ErrorCode.MISSING, "A report file must carry a definition.")
    else:
        try:
            definition = validate_definition(top["definition"])
        except DefinitionValidationError as e:
            for issue in e.issues:
                path = f"definition.{issue.path}" if issue.path else "definition"
                c.add(path, issue.code, issue.message)

    if definition is not None and version is not None:
        if definition.schema_version != version:
            c.add(
                "schema_version",
                ErrorCode.INVALID_VALUE,
                "The envelope's schema_version ({}) must match the definition's "
                "({}).".format(version, definition.schema_version),
            )

    if c:
        raise DefinitionValidationError(c.issues)

    return PortableReport(
        name=name,
        definition=definition,
        description=description,
        schema_version=version,
        exported_at=top.get("exported_at"),
        generator=top.get("generator"),
        source=top.get("source"),
        meta=dict(meta) if meta else {},
    )
