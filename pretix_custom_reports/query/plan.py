"""Pass one: resolve, validate, and assemble everything the queryset will need.

Owner from wave 1 on: query-dev (ORCHESTRIERUNG.md section 5).

Why the compiler has two passes
-------------------------------

:func:`build_plan` turns a definition into a :class:`QueryPlan`: compiled
columns, one ``Q``, an ordering, the annotations, the joins. It resolves every
field key through the registry and rejects everything the registry says is not
allowed. It does **not** build a queryset.

:mod:`pretix_custom_reports.query.report` then applies the plan to a queryset.

The split is not decoration. It is what makes the central security property
*checkable* rather than merely intended: an unknown or manipulated field key is
rejected by :func:`check_definition`, which runs first and builds nothing at all.
The proof is a test that asserts zero queries and an exception, not a code review.
The same seam lets the editor validate a draft against the registry without
touching the database.

Neither pass ever runs a query. :func:`build_plan` does *construct* inner
querysets for aggregate subqueries and prefetches, and those come off
``ScopedManager``s, so it needs an active django-scopes scope like everything else
in pretix (docs/pretix-api-notes.md section 7). :func:`check_definition` needs
neither -- which is exactly why the security test can assert zero queries against
it.

Order of checks
---------------

The order is fixed by
:meth:`~pretix_custom_reports.contracts.protocols.QueryCompiler.compile`:

1. resolve every key; raise :class:`~pretix_custom_reports.contracts.errors.FieldResolutionError`
   listing **all** missing keys at once, because an importer has to show the user
   the whole list, not the first entry (ADR 0001 section 3.2),
2. everything only the registry can know -- base support, mandatory and permitted
   aggregates, allowed operators, ``sortable`` -- collected and raised together as
   one :class:`~pretix_custom_reports.contracts.errors.CompilationError`,
3. build the plan.
"""

from typing import Any, Dict, List, Mapping, Optional, Tuple

from dataclasses import dataclass, field as dataclass_field
from django.db.models import F, Q

from pretix_custom_reports.contracts.definition import (
    FieldUsage,
    ReportDefinition,
)
from pretix_custom_reports.contracts.errors import (
    CompilationError,
    FieldResolutionError,
)
from pretix_custom_reports.contracts.fields import (
    OPERATOR_SPECS,
    Base,
    DataType,
    ReportField,
    SortDirection,
)
from pretix_custom_reports.contracts.protocols import CompiledColumn, FieldRegistry
from pretix_custom_reports.query import columns as columns_mod, relations
from pretix_custom_reports.query.filters import FilterContext, compile_filters

__all__ = ["QueryPlan", "build_plan", "check_definition"]


def check_definition(
    definition: ReportDefinition,
    event: Any,
    registry: FieldRegistry,
) -> Mapping[str, ReportField]:
    """Run only the registry-stage checks and return the resolved fields.

    The cheap half of :func:`build_plan`: resolve every key, then verify
    everything only the registry can know. Builds no expression, calls no
    annotation callable, needs no database and no django-scopes scope.

    This is what the editor wants for "is this draft usable?", what the importer
    wants for "will this file run here?", and what a test wants in order to assert
    that a definition is *accepted* without depending on a registry that can
    produce real SQL.

    :raises FieldResolutionError: at least one key does not exist here.
    :raises CompilationError: resolvable, but not valid against the fields.
    """
    base = definition.base
    fields = dict(registry.get_fields(event, base))
    _resolve_or_raise(definition, fields, registry, event, base)
    _validate(definition, fields, base)
    return fields


@dataclass(frozen=True)
class QueryPlan:
    """Everything pass two needs, and nothing it has to work out for itself."""

    definition: ReportDefinition
    base: Base
    base_model: Any
    event: Any
    fields: Mapping[str, ReportField]

    columns: Tuple[CompiledColumn, ...] = ()
    """Visible output columns, in output order. Hidden ones are already gone."""

    annotations: Dict[str, Any] = dataclass_field(default_factory=dict)
    """``alias -> expression`` for the main queryset."""

    filter_annotations: Dict[str, Any] = dataclass_field(default_factory=dict)
    """Subset of :attr:`annotations` that the filter refers to.

    The count query needs these and nothing else; carrying the column
    annotations into a ``COUNT(*)`` would pay for values nobody reads.
    """

    filter_q: Optional[Q] = None
    ordering: Tuple[Any, ...] = ()
    select_related: Tuple[str, ...] = ()
    prefetch_related: Tuple[Any, ...] = ()

    include_canceled_positions: bool = False
    include_testmode_orders: bool = False
    row_limit: Optional[int] = None

    def headers(self) -> List[str]:
        """Header row."""
        return [column.label for column in self.columns]


def build_plan(
    definition: ReportDefinition,
    event: Any,
    registry: FieldRegistry,
    now: Any = None,
) -> QueryPlan:
    """Resolve and validate *definition* for *event*, and plan the queryset.

    :param registry: the only source of ORM paths, lookups and operators
        (CLAUDE.md rule 2). Required -- there is deliberately no default, so a
        stub registry can never sneak into the production path
        (docs/adr/0001-contracts.md section 6).
    :param now: reference instant for relative date filters. Injectable so that a
        test can pin "today" without freezing the process clock.
    :raises FieldResolutionError: at least one key does not exist for this
        event and base.
    :raises CompilationError: everything resolves, but the definition asks for
        something the resolved fields do not allow.
    """
    base = definition.base
    base_model = relations.base_model_for(base)
    fields = dict(check_definition(definition, event, registry))

    options = definition.options
    filter_ctx = FilterContext(
        base=base,
        base_model=base_model,
        event=event,
        include_canceled=options.include_canceled_positions,
        now=now,
    )

    annotations: Dict[str, Any] = {}
    select_related: List[str] = []
    prefetch_entries: List[Any] = []
    compiled_columns: List[CompiledColumn] = []

    for index, spec in enumerate(definition.columns):
        if spec.hidden:
            continue
        build = columns_mod.build_column(
            index=index,
            spec=spec,
            field=fields[spec.field],
            base=base,
            base_model=base_model,
            event=event,
            include_canceled=options.include_canceled_positions,
        )
        compiled_columns.append(build.column)
        _merge_annotations(annotations, build.annotations, spec.field)
        for path in build.select_related:
            if path and path not in select_related:
                select_related.append(path)
        prefetch_entries.extend(build.prefetch)

    if not compiled_columns:
        # Structural validation only demands at least one column, not at least
        # one *visible* one. A report whose every column is hidden produces an
        # empty file, which pretix turns into a failure mail on every scheduled
        # run (docs/pretix-api-notes.md section 5.6).
        raise CompilationError(
            "Every column of this report is hidden, so it would produce an empty "
            "export. Make at least one column visible."
        )

    filter_annotations = _annotations_for_usage(
        definition, fields, event, base, FieldUsage.FILTER
    )
    _merge_annotations(annotations, filter_annotations, "filters")
    sort_annotations = _annotations_for_usage(
        definition, fields, event, base, FieldUsage.SORT
    )
    _merge_annotations(annotations, sort_annotations, "sorting")

    filter_q = compile_filters(definition.filters, fields, filter_ctx)
    if not options.include_testmode_orders:
        testmode = relations.testmode_q(base)
        filter_q = testmode if filter_q is None else (filter_q & testmode)

    return QueryPlan(
        definition=definition,
        base=base,
        base_model=base_model,
        event=event,
        fields=fields,
        columns=tuple(compiled_columns),
        annotations=annotations,
        filter_annotations=filter_annotations,
        filter_q=filter_q,
        ordering=_build_ordering(definition, fields),
        select_related=tuple(select_related),
        prefetch_related=_dedupe_prefetches(prefetch_entries),
        include_canceled_positions=options.include_canceled_positions,
        include_testmode_orders=options.include_testmode_orders,
        row_limit=options.row_limit,
    )


# ---------------------------------------------------------------------------
# Pass 1a: resolution
# ---------------------------------------------------------------------------


def _resolve_or_raise(
    definition: ReportDefinition,
    fields: Dict[str, ReportField],
    registry: FieldRegistry,
    event: Any,
    base: Base,
) -> None:
    """Every key must exist. Collect all misses, then raise once.

    ``get_fields`` is the bulk view and ``resolve`` the single lookup; a registry
    may legitimately resolve a key it does not list (a deprecated alias, say), so
    a miss in the mapping gets a second chance through ``resolve`` before it
    counts as missing.
    """
    missing: List[str] = []
    for ref in definition.iter_field_references():
        if ref.key in fields:
            continue
        resolved = registry.resolve(ref.key, event, base)
        if resolved is None:
            if ref.key not in missing:
                missing.append(ref.key)
        else:
            fields[ref.key] = resolved
    if missing:
        raise FieldResolutionError(sorted(missing), base=base)


# ---------------------------------------------------------------------------
# Pass 1b: everything only the registry knows
# ---------------------------------------------------------------------------


def _validate(
    definition: ReportDefinition,
    fields: Dict[str, ReportField],
    base: Base,
) -> None:
    problems: List[str] = []
    base_model = relations.base_model_for(base)

    for ref in definition.iter_field_references():
        field = fields[ref.key]

        if not field.supports_base(base):
            problems.append(f"{ref.path}: {ref.key} is not available on base {base}.")
            continue

        if ref.usage is FieldUsage.COLUMN:
            problems.extend(_column_problems(ref, field, base, base_model))
        elif ref.usage is FieldUsage.FILTER:
            problems.extend(_filter_problems(ref, field))
        elif ref.usage is FieldUsage.SORT:
            problems.extend(_sort_problems(ref, field, base))

    if problems:
        raise CompilationError(" ".join(problems))


def _column_problems(
    ref: Any, field: ReportField, base: Base, base_model: Any
) -> List[str]:
    problems: List[str] = []
    if field.needs_aggregate_on(base) and ref.aggregate is None:
        problems.append(
            f"{ref.path}: {ref.key} holds several values per row on base {base} "
            f"and therefore needs an aggregate "
            f"({', '.join(str(a) for a in field.aggregates) or 'none available'})."
        )
    if ref.aggregate is not None:
        if not field.allows_aggregate(ref.aggregate):
            problems.append(
                f"{ref.path}: {ref.key} does not support aggregate {ref.aggregate} "
                f"(allowed: "
                f"{', '.join(str(a) for a in field.aggregates) or 'none'})."
            )
        elif (
            columns_mod.relation_source(field, base_model) is None
            and field.annotation is None
        ):
            problems.append(
                f"{ref.path}: {ref.key} yields one value per row on base {base}, "
                f"so aggregate {ref.aggregate} has nothing to aggregate."
            )
    if field.orm_path is None and field.value_getter is None:  # pragma: no cover
        problems.append(f"{ref.path}: {ref.key} cannot produce a value.")
    return problems


def _filter_problems(ref: Any, field: ReportField) -> List[str]:
    problems: List[str] = []
    if not field.orm_path:
        problems.append(
            f"{ref.path}: {ref.key} is computed in Python and cannot be filtered."
        )
        return problems
    if ref.operator is not None and not field.allows_operator(ref.operator):
        problems.append(
            f"{ref.path}: operator {ref.operator} is not allowed for {ref.key} "
            f"({field.datatype}). Allowed: "
            f"{', '.join(str(o) for o in field.filter_operators) or 'none'}."
        )
        return problems
    if (
        ref.operator is not None
        and OPERATOR_SPECS[ref.operator].relative
        and field.datatype not in (DataType.DATE, DataType.DATETIME)
    ):
        # Belt and braces: a registry that offers a relative operator on a
        # non-date field is a registry bug, and it would otherwise surface as a
        # confusing error deep inside date resolution.
        problems.append(
            f"{ref.path}: {ref.operator} needs a date or datetime field, but "
            f"{ref.key} is {field.datatype}."
        )
    return problems


def _sort_problems(ref: Any, field: ReportField, base: Base) -> List[str]:
    if not field.sortable:
        return [f"{ref.path}: {ref.key} is not sortable on base {base}."]
    if field.needs_aggregate_on(base):
        # The registry is supposed to mark these not sortable already
        # (ADR 0001 section 7b). Checked again because a wrong sort silently
        # multiplies rows through the join it needs.
        return [
            f"{ref.path}: {ref.key} is an aggregated value on base {base}; "
            f"sorting by aggregates is out of scope for v1."
        ]
    if not field.orm_path:  # pragma: no cover - forbidden by ReportField
        return [f"{ref.path}: {ref.key} has no database path to sort by."]
    return []


# ---------------------------------------------------------------------------
# Pass 1c: assembly
# ---------------------------------------------------------------------------


def _annotations_for_usage(
    definition: ReportDefinition,
    fields: Dict[str, ReportField],
    event: Any,
    base: Base,
    usage: FieldUsage,
) -> Dict[str, Any]:
    """Registry annotations required by the fields used in *usage*."""
    collected: Dict[str, Any] = {}
    for ref in definition.iter_field_references():
        if ref.usage is not usage:
            continue
        field = fields[ref.key]
        if field.annotation is None:
            continue
        _merge_annotations(
            collected,
            columns_mod.registry_annotations(field, event, base),
            ref.key,
        )
    return collected


def _dedupe_prefetches(entries: List[Any]) -> Tuple[Any, ...]:
    """Turn per-column prefetch requests into a minimal, conflict-free tuple.

    Django rejects the same lookup twice when the second one carries a queryset
    ("... was already seen with a different queryset"), and three ``join`` columns
    over ``all_positions`` all want that lookup. Two rules resolve it:

    * a :class:`~pretix_custom_reports.query.relations.PrefetchSpec` is keyed by
      ``(lookup, to_attr)``. Intermediate levels have no ``to_attr`` and therefore
      collapse into one query however many columns need them; leaf levels have a
      per-column ``to_attr`` and stay independent, so their filters cannot
      interfere.
    * anything else -- a plain path or a ``Prefetch`` a registry field declared in
      ``prefetch_related`` -- is de-duplicated by equality and passed through.

    Order is preserved, which matters: Django needs the parent of a nested prefetch
    to come first.
    """
    seen_specs: Dict[Tuple[str, Optional[str]], Any] = {}
    out: List[Any] = []
    for entry in entries:
        if isinstance(entry, relations.PrefetchSpec):
            if entry.dedup_key in seen_specs:
                continue
            seen_specs[entry.dedup_key] = entry
            out.append(entry.build())
            continue
        if entry not in out:
            out.append(entry)
    return tuple(out)


def _merge_annotations(
    target: Dict[str, Any], new: Mapping[str, Any], origin: str
) -> None:
    """Merge annotation aliases into *target*. First claim wins.

    Sharing an alias between two fields is legitimate and expected: an
    "outstanding amount" field is ``total - payments + refunds`` and naturally
    re-declares the payment and refund sub-annotations that the "amount paid" and
    "amount refunded" fields also declare. Both callables build a fresh but
    equivalent expression, so the first one in wins and the second is dropped.

    There is deliberately no "are these two expressions the same?" check.
    ``Subquery`` and friends compare by object identity and their ``repr`` contains
    the object address, so any such comparison would reject the legitimate case
    above every single time. Alias uniqueness *within* one meaning is the
    registry's responsibility; what the compiler does guard is that a registry
    alias never collides with a compiler-generated one, which
    :func:`~pretix_custom_reports.query.columns.registry_annotations` checks at the
    point where registry aliases enter.
    """
    del origin  # kept in the signature: it names the culprit in future checks
    for alias, expression in new.items():
        target.setdefault(alias, expression)


def _build_ordering(
    definition: ReportDefinition, fields: Dict[str, ReportField]
) -> Tuple[Any, ...]:
    """Multi-level ordering plus a stable tiebreaker.

    Two decisions worth stating:

    * **The primary key is always appended.** Without it, two rows that agree on
      every sort field come back in whatever order the database feels like, and
      that order can differ between two ``LIMIT``/``OFFSET`` pages of the same
      query -- rows appear twice or not at all in a paginated preview. Both base
      models have a ``Meta.ordering`` that ends in the pk for exactly this reason
      (``Order``: ``("-datetime", "-pk")``).
    * **NULLs sort last in both directions.** Backends disagree by default
      (PostgreSQL puts NULLs last ascending and first descending, SQLite the
      other way round), so leaving it to the database would make the same
      definition produce different files on different installations. "No value at
      the end" is also what a reader of a report expects.
    """
    ordering: List[Any] = []
    for entry in definition.sorting:
        field = fields[entry.field]
        expression = F(field.orm_path)
        if entry.direction is SortDirection.DESC:
            ordering.append(expression.desc(nulls_last=True))
        else:
            ordering.append(expression.asc(nulls_last=True))
    ordering.append(F("pk").asc())
    return tuple(ordering)
