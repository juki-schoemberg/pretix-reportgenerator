"""Operator + datatype -> ``Q()``. One level of AND/OR nesting.

Owner from wave 1 on: query-dev (ORCHESTRIERUNG.md section 5).

Operators are semantic, not ORM lookups: ``contains`` means "contains, ignoring
case", and translating that into ``__icontains`` is this module's job
(docs/adr/0001-contracts.md section 9). A stored definition therefore never
resembles an ORM expression, and the lookup suffix always comes out of
:data:`_LOOKUP_SUFFIX` -- a closed table in code -- never out of the definition.

Two things are worth reading before changing anything here.

Multi-valued fields on base ``order``
-------------------------------------

A filter on position-level data in an order-based report needs no aggregate
(docs/adr/0001-contracts.md section 7a) and becomes an ``EXISTS``. That leaves
one genuine question the ADR does not answer: what does the *negation* mean?

For an order with a "Ticket" and a "Workshop" position, ``item.name not_exact
"Ticket"``:

* "at least one position is not a Ticket" -> matches (there is a Workshop),
* "no position is a Ticket" -> does not match.

We pick the second reading, and generally: a **negated** operator becomes
``NOT EXISTS(positive condition)``. The first reading would make ``not_in`` and
``not_contains`` useless on any order with more than one product -- almost always
true and therefore no filter at all. ``is_not_empty`` is the documented exception:
it is a presence test, so "at least one position has a value" is the natural
reading, and ``NOT EXISTS(empty)`` would be vacuously true for an order with no
positions at all. The mapping is spelled out in :data:`_NEGATION_OF` rather than
derived, so it is reviewable.

Relative date operators
-----------------------

They are resolved to concrete bounds in the *event's* timezone before they touch
the ORM -- see :mod:`pretix_custom_reports.query.dates` for why that is not a
detail.
"""

from typing import Any, Dict, List, Optional

from django.db.models import Q

from pretix_custom_reports.contracts.definition import (
    BoolOp,
    FilterCondition,
    FilterGroup,
)
from pretix_custom_reports.contracts.errors import CompilationError
from pretix_custom_reports.contracts.fields import (
    OPERATOR_SPECS,
    Base,
    DataType,
    Operator,
    ReportField,
    ValueKind,
)
from pretix_custom_reports.query import columns, relations
from pretix_custom_reports.query.dates import (
    DateWindow,
    resolve_relative_window,
)
from pretix_custom_reports.query.values import (
    coerce_value,
    coerce_values,
    emptiness_q,
)

__all__ = [
    "FilterContext",
    "compile_condition",
    "compile_filters",
]

#: Operator -> Django lookup suffix. The single place where a lookup name is
#: written down. ``exact`` maps to the empty suffix so that ``Q(**{path: value})``
#: uses the field's own default comparison.
_LOOKUP_SUFFIX: Dict[Operator, str] = {
    Operator.EXACT: "exact",
    Operator.CONTAINS: "icontains",
    Operator.STARTS_WITH: "istartswith",
    Operator.ENDS_WITH: "iendswith",
    Operator.IN: "in",
    Operator.LT: "lt",
    Operator.LTE: "lte",
    Operator.GT: "gt",
    Operator.GTE: "gte",
}

#: Negated operator -> the positive operator it is the negation of. Used both to
#: compile the ``Q`` and to decide where a ``NOT`` goes for multi-valued paths.
_NEGATION_OF: Dict[Operator, Operator] = {
    Operator.NOT_EXACT: Operator.EXACT,
    Operator.NOT_CONTAINS: Operator.CONTAINS,
    Operator.NOT_IN: Operator.IN,
}

#: Operators that are compiled as "at least one related row matches" even though
#: :attr:`~pretix_custom_reports.contracts.fields.OperatorSpec.negated` is set.
#: See the module docstring.
_EXISTS_POSITIVE_DESPITE_NEGATED = frozenset({Operator.IS_NOT_EMPTY})


class FilterContext:
    """Everything filter compilation needs besides the condition itself.

    A small object rather than five parameters, because every helper in here
    needs the same five and they must not drift apart.
    """

    def __init__(
        self,
        base: Base,
        base_model: Any,
        event: Any,
        include_canceled: bool,
        now: Any = None,
    ) -> None:
        self.base = base
        self.base_model = base_model
        self.event = event
        self.include_canceled = include_canceled
        self.now = now


def compile_filters(
    group: Optional[FilterGroup],
    fields: Dict[str, ReportField],
    ctx: FilterContext,
) -> Optional[Q]:
    """Compile the whole filter tree into one ``Q``, or ``None`` for "no filter".

    The root group joins its children with its own operator; a nested group joins
    its conditions with its own operator and is then folded into the root. Exactly
    one nesting level exists (SPEC.md F6), which the structural validator has
    already enforced -- this function does not need to recurse further and
    deliberately does not.
    """
    if group is None:
        return None

    combined: Optional[Q] = None
    for index, child in enumerate(group.children):
        path = f"filters.children[{index}]"
        if isinstance(child, FilterGroup):
            part = _compile_group(child, fields, ctx, path)
        else:
            part = compile_condition(child, fields, ctx, path)
        if part is None:
            continue
        combined = part if combined is None else _join(combined, part, group.op)
    return combined


def _compile_group(
    group: FilterGroup,
    fields: Dict[str, ReportField],
    ctx: FilterContext,
    path: str,
) -> Optional[Q]:
    combined: Optional[Q] = None
    for index, child in enumerate(group.children):
        if isinstance(child, FilterGroup):  # pragma: no cover - rejected structurally
            raise CompilationError(
                f"{path}.children[{index}]: filters allow exactly one level of "
                f"nesting."
            )
        part = compile_condition(child, fields, ctx, f"{path}.children[{index}]")
        if part is None:
            continue
        combined = part if combined is None else _join(combined, part, group.op)
    return combined


def _join(left: Q, right: Q, op: BoolOp) -> Q:
    return (left | right) if op is BoolOp.OR else (left & right)


def compile_condition(
    condition: FilterCondition,
    fields: Dict[str, ReportField],
    ctx: FilterContext,
    path: str = "filters",
) -> Q:
    """Compile one ``field <operator> value`` test.

    :param fields: resolved registry fields, keyed by key. The ``orm_path`` used
        comes from here and only from here.
    :raises CompilationError: the operator is not allowed for the field, the
        value does not fit its datatype, or the field cannot be filtered at all.
    """
    field = fields.get(condition.field)
    if field is None:  # pragma: no cover - resolution happens before this
        raise CompilationError(f"{path}: unknown field {condition.field!r}.")
    if not field.orm_path:
        raise CompilationError(
            f"{path}: {field.key} is computed in Python and cannot be filtered."
        )
    if not field.allows_operator(condition.operator):
        raise CompilationError(
            f"{path}: operator {condition.operator} is not allowed for "
            f"{field.key} ({field.datatype}). Allowed: "
            f"{', '.join(str(o) for o in field.filter_operators) or 'none'}."
        )

    source = columns.relation_source(field, ctx.base_model)
    chain = (
        relations.relation_chain(ctx.base_model, source) if source is not None else None
    )
    if chain is None:
        return _single_valued_q(field, condition, ctx, path, field.orm_path)

    inner_path = chain.remainder or "pk"
    operator = condition.operator
    negate = (
        OPERATOR_SPECS[operator].negated
        and operator not in _EXISTS_POSITIVE_DESPITE_NEGATED
    )
    if negate:
        operator = _NEGATION_OF.get(operator, operator)

    inner_condition = _single_valued_q(
        field,
        FilterCondition(
            field=condition.field, operator=operator, value=condition.value
        ),
        ctx,
        path,
        inner_path,
    )
    exists = relations.exists_subquery(
        ctx.base_model,
        source,
        inner_condition,
        ctx.include_canceled,
        columns.relation_filter(field, chain, ctx.include_canceled),
    )
    return ~Q(exists) if negate else Q(exists)


def _single_valued_q(
    field: ReportField,
    condition: FilterCondition,
    ctx: FilterContext,
    path: str,
    lookup_path: str,
) -> Q:
    """``Q`` for a path that yields at most one value per row."""
    operator = condition.operator
    spec = OPERATOR_SPECS[operator]

    if operator is Operator.IS_EMPTY:
        return emptiness_q(lookup_path, field.datatype)
    if operator is Operator.IS_NOT_EMPTY:
        return ~emptiness_q(lookup_path, field.datatype)

    if spec.relative:
        window = _relative_window(field, condition, ctx, path)
        return _window_q(lookup_path, window)

    if operator is Operator.BETWEEN:
        low, high = coerce_values(field, condition.value, ctx.event)
        return Q(**{f"{lookup_path}__gte": low, f"{lookup_path}__lte": high})

    if operator in _NEGATION_OF:
        positive = _single_valued_q(
            field,
            FilterCondition(
                field=condition.field,
                operator=_NEGATION_OF[operator],
                value=condition.value,
            ),
            ctx,
            path,
            lookup_path,
        )
        # No manual NULL handling here. Django compiles a negated lookup on a
        # nullable column as ``NOT (col = %s AND col IS NOT NULL)``
        # (Query.build_filter, current_negated branch), so rows without a value
        # do show up under "is not X" -- which is what a report user expects.
        # Adding ``| isnull=True`` on top would be redundant and would only make
        # the generated SQL harder to read in tests.
        return ~positive

    suffix = _LOOKUP_SUFFIX.get(operator)
    if suffix is None:  # pragma: no cover - operator table is closed
        raise CompilationError(f"{path}: operator {operator} is not implemented.")

    if spec.value_kind is ValueKind.LIST:
        value: Any = coerce_values(field, condition.value, ctx.event)
    else:
        value = coerce_value(field, condition.value, ctx.event)
    return Q(**{f"{lookup_path}__{suffix}": value})


def _relative_window(
    field: ReportField,
    condition: FilterCondition,
    ctx: FilterContext,
    path: str,
) -> DateWindow:
    if field.datatype not in (DataType.DATE, DataType.DATETIME):
        raise CompilationError(
            f"{path}: {condition.operator} needs a date or datetime field, but "
            f"{field.key} is {field.datatype}."
        )
    return resolve_relative_window(
        condition.operator, condition.value, field.datatype, ctx.event, ctx.now
    )


def _window_q(lookup_path: str, window: DateWindow) -> Q:
    parts: List[Q] = []
    if window.start is not None:
        parts.append(Q(**{f"{lookup_path}__gte": window.start}))
    if window.end is not None:
        suffix = "lte" if window.end_inclusive else "lt"
        parts.append(Q(**{f"{lookup_path}__{suffix}": window.end}))
    if not parts:  # pragma: no cover - every window has at least one bound
        return Q()
    result = parts[0]
    for part in parts[1:]:
        result = result & part
    return result
