"""Columns: renderers, aggregate strategies and the joins a column needs.

Owner from wave 1 on: query-dev (ORCHESTRIERUNG.md section 5).

One :class:`~pretix_custom_reports.contracts.protocols.CompiledColumn` per
visible column, plus the queryset decorations that column requires. Hidden
columns are dropped here and never appear downstream
(docs/adr/0001-contracts.md section 9), so the exporter does not have to filter.

Four ways a value reaches a cell
--------------------------------

===============================  ==================================  ============
Field declares                   Strategy                            Queries
===============================  ==================================  ============
``value_getter``                 call it with the row object         0 extra
``annotation`` (+ alias)         read the annotation alias           0 extra
plain single-valued ``orm_path`` ``select_related`` + attribute walk 0 extra
multi-valued + ``aggregate``     correlated subquery, or a prefetch  0 / below
===============================  ==================================  ============

"0 extra" means: still one single SELECT for the whole report, whatever the row
count. ``join`` is the one strategy that costs more, and the price is::

    1 + levels x ceil(rows / chunk_size)   queries

because ``QuerySet.iterator(chunk_size=...)`` runs ``prefetch_related`` **once per
chunk** rather than once for the result set. A wide order report with two ``join``
columns therefore measures 4 queries at 494 rows and 151 at 49.484
(docs/performance.md 3.3). That is not an N+1 -- the cost *per row* falls as the
report grows -- and it is what keeps the memory flat at six-digit row counts
(docs/performance.md 3.5); the earlier promise of "one query per prefetch level,
independent of the row count" held for one chunk only and is corrected here
(T-003 in handoff/blockers.md).

*levels* is the number of **distinct** prefetch levels, not the number of ``join``
columns: levels that agree on relation, condition, canceled rule and inner
``select_related`` share one ``Prefetch``, so twenty identical ``join`` columns
cost what one costs, and only genuinely different ones add a level
(:func:`~pretix_custom_reports.query.relations.join_leaf_to_attr`, S-005).
*chunk_size* is the exporter's, defaulting to
:data:`~pretix_custom_reports.contracts.protocols.DEFAULT_CHUNK_SIZE`.

Why ``join`` is a prefetch and not SQL
-------------------------------------

Django 5.2 has no backend-independent string aggregation: ``StringAgg`` lives in
``django.contrib.postgres.aggregates`` and this plugin has to work on SQLite and
MySQL as well. The alternatives were a hand-written ``Aggregate`` subclass with
three dialect branches (``STRING_AGG`` / ``GROUP_CONCAT`` with two different
separator syntaxes, and no portable ordering inside the aggregate), or a
``Prefetch`` plus a Python ``str.join``. The prefetch wins: it is portable, it
respects ``include_canceled_positions`` with the same queryset the aggregates
use, it makes the column's ``separator`` format option trivial, and combined with
``QuerySet.iterator(chunk_size=...)`` it stays memory-bounded because Django
prefetches per chunk rather than for the whole result set.

Why ``select_related`` and not an ``F()`` annotation everywhere
--------------------------------------------------------------

Both give one query. ``select_related`` plus attribute access preserves the
Python objects pretix builds on top of the raw column -- ``LazyI18nString`` for
``I18nCharField``, ``Country`` for ``FastCountryField``, ``PhoneNumber`` -- which
is what the renderer downstream wants to format. An ``F()`` annotation hands back
whatever the database returned. ``F()`` is kept as the fallback for the one case
``select_related`` cannot cover, so no path can silently degrade into an N+1.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

import re
from dataclasses import dataclass, field as dataclass_field
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F, Q
from i18nfield.strings import LazyI18nString

from pretix_custom_reports.contracts.definition import Column
from pretix_custom_reports.contracts.errors import (
    CompilationError,
    FieldContractError,
)
from pretix_custom_reports.contracts.fields import (
    Aggregate,
    Base,
    DataType,
    ReportField,
)
from pretix_custom_reports.contracts.protocols import CompiledColumn
from pretix_custom_reports.query import relations
from pretix_custom_reports.registry import hints

__all__ = [
    "DEFAULT_JOIN_SEPARATOR",
    "EXTRA_RELATION_FILTER",
    "ColumnBuild",
    "build_column",
    "registry_annotations",
    "relation_filter",
    "relation_source",
]

#: Separator for ``join`` columns when the definition does not override it.
DEFAULT_JOIN_SEPARATOR = ", "

#: Prefix for compiler-generated annotation aliases. Contains no double
#: underscore, so it can never be mistaken for a lookup path, and it is namespaced
#: so it cannot collide with a model field.
_ALIAS_PREFIX = "pcr_c"

#: Alias shape the compiler reserves for itself. A registry that uses it is
#: rejected in :func:`registry_annotations`.
_RESERVED_ALIAS_RE = re.compile(r"^pcr_c\d+$")


@dataclass
class ColumnBuild:
    """A compiled column together with what the queryset has to do for it."""

    column: CompiledColumn

    annotations: Dict[str, Any] = dataclass_field(default_factory=dict)
    """``alias -> expression`` to add via ``annotate()``."""

    select_related: Tuple[str, ...] = ()
    """Paths to ``select_related()``."""

    prefetch: Tuple[Any, ...] = ()
    """``Prefetch`` objects or plain paths to ``prefetch_related()``."""


def build_column(
    index: int,
    spec: Column,
    field: ReportField,
    base: Base,
    base_model: Any,
    event: Any,
    include_canceled: bool,
) -> ColumnBuild:
    """Compile one visible column.

    :param index: position in the output, used to mint a collision-free
        annotation alias.
    :param spec: the column as it appears in the definition. Supplies the label
        override, the aggregate and the format -- never an ORM path.
    :param field: the resolved registry field. Supplies everything technical.
    :raises CompilationError: the field cannot produce a value for this base and
        aggregate combination.
    """
    label = spec.label or str(field.label)
    alias = f"{_ALIAS_PREFIX}{index}"
    separator = _separator(spec)

    if spec.aggregate is not None:
        return _aggregated_column(
            spec,
            field,
            base,
            base_model,
            event,
            alias,
            label,
            separator,
            include_canceled,
        )

    if field.value_getter is not None:
        return ColumnBuild(
            column=_column(spec, field, label, field.value_getter),
            select_related=tuple(field.select_related),
            prefetch=tuple(field.prefetch_related),
        )

    if not field.orm_path:  # pragma: no cover - forbidden by ReportField
        raise CompilationError(
            f"{field.key}: field has neither an ORM path nor a value getter."
        )

    if field.annotation is not None:
        # The registry's annotation already carries the value under ``orm_path``.
        return ColumnBuild(
            column=_column(
                spec,
                field,
                label,
                _attribute_renderer(field.orm_path, field.datatype),
            ),
            annotations=registry_annotations(field, event, base),
            select_related=tuple(field.select_related),
            prefetch=tuple(field.prefetch_related),
        )

    prefix = relations.select_related_prefix(base_model, field.orm_path)
    if "__" not in field.orm_path or prefix is not None:
        select_related = tuple(field.select_related)
        if prefix:
            select_related = select_related + (prefix,)
        return ColumnBuild(
            column=_column(
                spec,
                field,
                label,
                _attribute_renderer(field.orm_path, field.datatype),
            ),
            select_related=select_related,
            prefetch=tuple(field.prefetch_related),
        )

    # Multi-segment path that ``select_related`` cannot follow. Rather than walk
    # it attribute by attribute -- one query per row -- pull the value into the
    # main SELECT with an ``F()`` annotation.
    return ColumnBuild(
        column=_column(spec, field, label, _attribute_renderer(alias, field.datatype)),
        annotations={alias: F(field.orm_path)},
        prefetch=tuple(field.prefetch_related),
    )


#: ``ReportField.extra`` key for a *leaf-relative* condition on the related rows.
#:
#: The escape hatch next to :mod:`registry.hints`, and strictly secondary to it.
#: ``hints.aggregate_filter`` knows two conditions, both of them pretix-core
#: specific: exclude canceled positions, and restrict an answer to its question
#: (by primary key). A field contributed by another plugin over its own
#: one-to-many relation (SPEC.md F5, docs/extending.md) may need something else --
#: a check-in relation narrowed to ``successful=True``, which ``Checkin.objects``
#: does *not* do across a relation lookup (docs/pretix-api-notes.md section
#: 6.10) -- and without this key it could not express that at all.
#:
#: ``ReportField.extra`` is documented as free-form and "never interpreted by the
#: contracts", which leaves *consumers* free to agree on a convention. This is
#: ours: a ``Q`` relative to the **leaf** model of the relation chain, written in
#: registry code and never in JSON, ANDed onto whatever ``hints`` supplies. A
#: registry that ignores the key is unaffected -- which is exactly what the core
#: registry does, so nothing here may depend on it.
EXTRA_RELATION_FILTER = "relation_filter"


def relation_source(field: ReportField, base_model: Any) -> Optional[str]:
    """The relation path a filter or aggregate for *field* runs over, or ``None``.

    1. ``None`` when the registry computes the value itself (an ``annotation``) or
       has no ORM path at all: whatever the value is, it is already one per row.
    2. ``field.orm_path``, when it crosses a multi-valued relation on the base
       model. The ordinary case: ``position.price`` on base ``order`` is
       ``all_positions__price``.
    3. ``None`` -- single-valued, so there is nothing to aggregate and nothing to
       put an ``EXISTS`` around.

    The **full** path, not the relation prefix. ``hints.aggregate_relation``
    returns the prefix (``all_positions__answers``) and is deliberately *not* used
    as the source: the part of the path beyond the relation is the aggregation
    target, so a ``sum`` over the prefix would sum primary keys. The prefix is
    used as a cross-check instead -- a registry whose declared relation is not the
    one its path crosses is a bug we would otherwise notice as a wrong number.
    """
    if field.annotation is not None or not field.orm_path:
        return None
    chain = relations.relation_chain(base_model, field.orm_path)
    _check_declared_relation(field, chain)
    if chain is None:
        return None
    return field.orm_path


def _check_declared_relation(
    field: ReportField, chain: Optional[relations.RelationChain]
) -> None:
    """``hints.aggregate_relation`` and ``orm_path`` must describe one relation."""
    declared = hints.aggregate_relation(field)
    if declared is None:
        return
    if chain is None or not (
        field.orm_path == declared or field.orm_path.startswith(f"{declared}__")
    ):
        raise FieldContractError(
            f"{field.key}: the field declares the aggregate relation "
            f"{declared!r}, but its ORM path {field.orm_path!r} does not run "
            f"through it."
        )


def relation_filter(
    field: ReportField,
    chain: relations.RelationChain,
    include_canceled: bool,
) -> Optional[Q]:
    """Condition on the related rows, as a ``Q`` relative to the leaf model.

    Applied to every subquery and every prefetch built over the relation. Two
    sources, ANDed:

    1. :func:`registry.hints.aggregate_filter` -- the documented seam
       (handoff/requests/registry-dev-an-query-dev-annotationen-und-aggregate.md
       section 2). It is what makes ``answer.<identifier>`` correct at all:
       without ``question=<pk>`` an answer aggregate mixes every question of the
       event into one cell. Its ``Q`` is written from the base model's point of
       view and :func:`relations.rebase_condition` turns it around.
    2. :data:`EXTRA_RELATION_FILTER` -- the escape hatch, already leaf-relative.

    The canceled-position rule arrives twice on purpose. It is applied
    structurally by ``include_canceled`` -- in the base queryset, in
    :func:`relations.leaf_queryset`, and at every level of a ``join`` prefetch,
    including the intermediate ones that never see a ``Q`` from here -- and again
    as ``canceled_flag`` inside ``hints.aggregate_filter``. ANDing the identical
    condition a second time is idempotent, and it costs nothing in SQL: the
    duplicate is either a local column of the leaf or a lookup across a
    single-valued relation, which Django resolves onto the join it already made.

    The alternative would be to suppress the hint's half by asking for it with
    ``include_canceled_positions=True``. That reads as a lie and would silently
    drop any *future* condition the registry derives from that flag. Keeping both
    means neither source is load bearing on its own.
    """
    declared = hints.aggregate_filter(
        field, include_canceled_positions=include_canceled
    )
    condition = (
        relations.rebase_condition(chain, declared, field.key)
        if declared is not None
        else None
    )

    extra = (field.extra or {}).get(EXTRA_RELATION_FILTER)
    if extra is None:
        return condition
    if not isinstance(extra, Q):
        raise CompilationError(
            f"{field.key}: extra[{EXTRA_RELATION_FILTER!r}] must be a Q object."
        )
    return extra if condition is None else (condition & extra)


def _aggregated_column(
    spec: Column,
    field: ReportField,
    base: Base,
    base_model: Any,
    event: Any,
    alias: str,
    label: str,
    separator: str,
    include_canceled: bool,
) -> ColumnBuild:
    """A one-to-many field used as a single cell (SPEC.md F3, ADR 0001 §7)."""
    source = relation_source(field, base_model)

    if source is None:
        if field.annotation is None:
            raise CompilationError(
                f"{field.key}: aggregate {spec.aggregate} was requested, but the "
                f"field yields one value per row on base {base} -- there is "
                f"nothing to aggregate."
            )
        # The registry computes this value itself and has declared, by having an
        # annotation and no relation source, that it is already a single value. We
        # record the requested aggregate on the column and read the alias. A
        # ``join`` whose annotation returns a collection is formatted with the
        # column's separator, so both plausible registry designs -- aggregate in
        # SQL, or hand back an array -- work without a contract change.
        renderer = (
            _sequence_join_renderer(field.orm_path, separator)
            if spec.aggregate is Aggregate.JOIN
            else _attribute_renderer(field.orm_path, field.datatype)
        )
        return ColumnBuild(
            column=_column(spec, field, label, renderer),
            annotations=registry_annotations(field, event, base),
            select_related=tuple(field.select_related),
            prefetch=tuple(field.prefetch_related),
        )

    chain = relations.relation_chain(base_model, source)
    if chain is None:  # pragma: no cover - relation_source guarantees one
        raise CompilationError(
            f"{field.key}: {source!r} is not a multi-valued relation on "
            f"{base_model.__name__}; it cannot be aggregated."
        )

    condition = relation_filter(field, chain, include_canceled)
    if spec.aggregate is Aggregate.JOIN:
        # ``alias`` is only the fallback name for the prefetch: two ``join``
        # columns that select the same related rows share one, keyed by what the
        # queryset does rather than by which column asked first (S-005).
        specs, read_path = relations.join_prefetch_specs(
            base_model, source, include_canceled, condition, alias
        )
        return ColumnBuild(
            column=_column(spec, field, label, _join_renderer(read_path, separator)),
            prefetch=specs,
        )

    expression = relations.subquery_aggregate(
        base_model,
        source,
        spec.aggregate,
        include_canceled,
        condition,
        # The registry's declaration, so that a summed amount keeps its two
        # decimal places on every backend (T-002). Nothing here inspects the
        # model field for it.
        datatype=field.datatype,
    )
    return ColumnBuild(
        column=_column(spec, field, label, _attribute_renderer(alias)),
        annotations={alias: expression},
    )


def _column(
    spec: Column, field: ReportField, label: str, renderer: Callable[[Any], Any]
) -> CompiledColumn:
    return CompiledColumn(
        key=spec.field,
        label=label,
        datatype=field.datatype,
        render=renderer,
        aggregate=spec.aggregate,
        field=field,
    )


def _separator(spec: Column) -> str:
    if spec.format is not None and spec.format.separator is not None:
        return spec.format.separator
    return DEFAULT_JOIN_SEPARATOR


def registry_annotations(field: ReportField, event: Any, base: Base) -> Dict[str, Any]:
    """Call the field's annotation callable and check what comes back.

    Every failure in here is a :class:`FieldContractError`: a malformed annotation
    is a bug in our registry or in a third-party plugin, never something a user can
    trigger from a definition.

    The reserved-alias check is the one that matters most. The compiler mints
    ``pcr_c<column index>`` for its own aggregate and ``F()`` annotations; a
    registry using the same shape would make one column silently display another
    column's value -- a bug that survives review and only shows up in an exported
    spreadsheet.
    """
    from pretix_custom_reports.contracts.errors import FieldContractError
    from pretix_custom_reports.contracts.fields import FieldContext

    produced = field.annotation(FieldContext(event=event, base=base))
    if not isinstance(produced, dict) or not produced:
        raise FieldContractError(
            f"{field.key}: annotation() must return a non-empty "
            f"{{alias: expression}} mapping, got {produced!r}."
        )
    if field.orm_path not in produced:
        raise FieldContractError(
            f"{field.key}: annotation() must include the field's orm_path "
            f"{field.orm_path!r} as one of its aliases, got {sorted(produced)}."
        )
    for alias, expression in produced.items():
        if _RESERVED_ALIAS_RE.match(alias):
            raise FieldContractError(
                f"{field.key}: annotation alias {alias!r} is reserved for the query "
                f"compiler (pattern {_ALIAS_PREFIX}<number>). Pick another name."
            )
        if expression is None:
            raise FieldContractError(
                f"{field.key}: annotation alias {alias!r} maps to None, which is not "
                f"a query expression. (The stub registry in contracts/stubs.py does "
                f"this deliberately -- it is a structural stand-in and cannot build "
                f"a real queryset. Use a registry with real ORM paths to compile.)"
            )
    return dict(produced)


def _attribute_renderer(
    orm_path: str, datatype: Optional[DataType] = None
) -> Callable[[Any], Any]:
    """Walk ``a__b__c`` as ``row.a.b.c``, tolerating missing *relations*.

    ``None`` is the right cell value for a missing invoice address or an unset
    variation. A reverse one-to-one that does not exist raises
    ``RelatedObjectDoesNotExist`` on attribute access even after
    ``select_related``, so that is caught -- and because that exception derives
    from ``ObjectDoesNotExist``, catching it does not also swallow a plain
    ``AttributeError``.

    That distinction is deliberate, and it is the *only* thing standing between a
    registry that declares a path no model has and a column full of blanks: no
    error, no log line, just wrong output. Missing data is silent; a wrong
    declaration must not be.

    There is no up-front check of declared paths to fall back on. It was
    considered and rejected: ``contracts/stubs.py`` is frozen and deliberately
    declares fictional paths (``pcnt``, ``payment_sum``, ``checkin_count``) with
    no annotation behind them, so a compiler that verified every ``orm_path``
    against ``Model._meta`` at plan time would reject the contract's own stub.
    ``tests/test_query_orm_path.py::test_an_unresolvable_path_from_the_registry_fails_loudly``
    pins down where each strategy breaks instead.
    """
    segments = tuple(orm_path.split("__"))
    normalise = _i18n_normaliser(datatype)

    def render(row: Any) -> Any:
        value = row
        for segment in segments:
            if value is None:
                return None
            try:
                value = getattr(value, segment)
            except ObjectDoesNotExist:
                return None
        return normalise(value)

    return render


def _i18n_normaliser(datatype: Optional[DataType]) -> Callable[[Any], Any]:
    """Make an ``I18N`` column render the same whichever strategy fed it.

    ``I18nCharField``/``I18nTextField`` hold either a plain string or a JSON object
    of translations. Attribute access on a ``select_related`` object goes through
    Django's descriptor and yields a ``LazyI18nString``; an annotation alias or an
    ``F()`` fallback yields the raw database value, which for a multilingual entry is
    a JSON blob and would land in the spreadsheet verbatim.

    Wrapping the raw value costs nothing and removes the difference, so a column's
    output does not depend on which internal path the compiler happened to pick.
    ``registry-dev`` flagged ``datatype is DataType.I18N`` as the intended signal
    for this (handoff/requests/registry-dev-an-query-dev-annotationen-und-aggregate.md,
    section 4).
    """
    if datatype is not DataType.I18N:
        return lambda value: value

    def normalise(value: Any) -> Any:
        if value is None or isinstance(value, LazyI18nString):
            return value
        if isinstance(value, str):
            return LazyI18nString(value)
        return value

    return normalise


def _expand(value: Any) -> List[Any]:
    """One value or many? Related managers and prefetch lists both become lists.

    A prefetch level with ``to_attr`` hands back a plain ``list``; one without
    hands back a related manager whose ``.all()`` is served from the prefetch
    cache. Both have to flatten, because a ``join`` may cross two relations
    (``all_positions__answers__answer``) and the intermediate level is then a
    manager while the leaf is a list.
    """
    if value is None:
        return []
    if hasattr(value, "all"):
        return list(value.all())
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _join_renderer(read_path: Tuple[str, ...], separator: str) -> Callable[[Any], Any]:
    """Walk a prefetched relation chain and join every leaf value into one cell.

    *read_path* comes from
    :func:`~pretix_custom_reports.query.relations.join_prefetch_specs`: relation
    accessors, then the leaf's ``to_attr``, then the single-valued tail. Every step
    flattens, so one hop and three hops use the same code.
    """

    def render(row: Any) -> Any:
        current: List[Any] = [row]
        for segment in read_path:
            collected: List[Any] = []
            for item in current:
                if item is None:
                    continue
                try:
                    value = getattr(item, segment)
                except (ObjectDoesNotExist, AttributeError):
                    continue
                collected.extend(_expand(value))
            current = collected
        values = [str(value) for value in current if value is not None and value != ""]
        return separator.join(values)

    return render


def _sequence_join_renderer(orm_path: str, separator: str) -> Callable[[Any], Any]:
    """Format an annotation whose value is already a collection.

    Covers the registry that answers a ``join`` column with an array-valued
    annotation (a Postgres ``ARRAY_AGG``, say). A plain string passes through
    untouched, so a registry that already joined server-side also works.
    """
    read = _attribute_renderer(orm_path)

    def render(row: Any) -> Any:
        value = read(row)
        if value is None or isinstance(value, str):
            return value
        try:
            items = list(value)
        except TypeError:
            return value
        return separator.join(str(item) for item in items if item is not None)

    return render
