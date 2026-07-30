"""Model introspection: base querysets, relation chains, correlated subqueries.

Owner from wave 1 on: query-dev (ORCHESTRIERUNG.md section 5).

This is the only module in ``query/`` that names concrete pretix models.
Everything it does is driven by an ``orm_path`` that came out of a
:class:`~pretix_custom_reports.contracts.fields.ReportField` -- never out of a
definition (CLAUDE.md rule 2). It performs **introspection only** and never runs
a query.

The base switch
---------------

``order`` starts from ``Order``, ``orderposition`` from ``OrderPosition``. Both
are hard-filtered to one event in :func:`base_queryset` and nowhere else, so there
is exactly one line to audit. A
:class:`~pretix_custom_reports.contracts.protocols.CompiledReport` belongs to
exactly one event (docs/adr/0001-contracts.md section 9), so multi-event exports
compile once per event and CLAUDE.md rule 4 holds by construction.

``OrderPosition`` has no ``event`` field: ``event`` is a Python property and the
only usable lookup is ``order__event`` (docs/pretix-api-notes.md section 6.2,
pitfall 2).

Relation chains
---------------

A path is either single-valued from the row's point of view -- a chain of forward
foreign keys or one-to-ones -- or it crosses at least one reverse foreign key or
many-to-many. The two need entirely different SQL, and confusing them is the
classic reporting bug: joining ``Order`` to ``all_positions`` multiplies every
order by its position count.

:func:`relation_chain` walks the path and collects every multi-valued hop, so that
a two-hop path works as well as a one-hop one::

    all_positions__price               1 hop  -> OrderPosition
    all_positions__item__name          1 hop  -> OrderPosition, then item.name
    all_positions__answers__answer     2 hops -> QuestionAnswer

The second hop is not academic: an answer is two reverse foreign keys away from an
order, and "the T-shirt sizes in this order" is among the first things anybody
asks an order-based report (fixture ``order_with_aggregates.json``).

Knowing the whole chain buys the useful part: the reverse lookup path from the
**leaf** model back to the base row. For ``all_positions__answers__answer`` that is
``orderposition__order``, so the aggregate becomes a subquery over
``QuestionAnswer`` correlated straight back to the order -- one grouping, no
intermediate join, and the same shape for one hop as for three.

Correlated subqueries, not joined annotations
---------------------------------------------

Aggregates and existence tests are ``Subquery``/``Exists``, not
``annotate(Sum('all_positions__price'))``. pretix core does the same for the same
reason in ``Order.annotate_overpayments`` (pretix/base/models/orders.py:510-575,
named as the model in docs/adr/0001-contracts.md section 11). With joined
annotations, two aggregates over two different relations form a cross product and
*both* come out multiplied; with subqueries each is computed independently and the
report is still one round trip.

``.order_by()`` on every inner queryset is not cosmetic: ``OrderPosition`` has a
``Meta.ordering``, and a default ordering silently joins the ``GROUP BY``
(docs/pretix-api-notes.md section 6.2, pitfall 6).

Registry conditions point the other way
---------------------------------------

``registry.hints.aggregate_filter`` hands back a ``Q`` written from the **base
model's** point of view -- ``Q(all_positions__canceled=False)`` -- because its
documented use is ``Sum(orm_path, filter=condition)`` on the base queryset. Inside
a correlated subquery the very same rows are addressed from the far end:
``canceled=False`` on ``OrderPosition``, ``orderposition__canceled=False`` on
``QuestionAnswer``. :func:`rebase_condition` translates between the two using the
chain's own accessor paths, so the registry keeps one declaration and the
compiler is free to choose subqueries over joined annotations.

Canceled positions
------------------

``OrderPosition.objects`` filters ``canceled=False``, ``OrderPosition.all`` does
not (docs/pretix-api-notes.md section 6.2). That is exactly
``ReportOptions.include_canceled_positions``, and it has to hold in three places:
the base queryset of an ``orderposition`` report, every subquery of an ``order``
report, and the prefetch behind a ``join`` column. Miss one and the report's
columns disagree with each other. :func:`leaf_queryset` applies it at whichever hop
of a chain is an ``OrderPosition``, which is why it also works for the two-hop
answer chain.

Both managers are ``ScopedManager``s and therefore need an active django-scopes
scope. Deliberate: pretix sets one in the control middleware and in ``EventTask``,
under which the scheduled exports run (docs/pretix-api-notes.md sections 7.1/7.2).
Reaching for ``_base_manager`` to dodge the scope would drop a free safety net, and
the explicit ``event=`` filter is applied on top regardless (pitfall 2 in section
7).
"""

from typing import Any, Dict, List, Optional, Tuple

from dataclasses import dataclass
from django.db.models import (
    Avg,
    Count,
    Exists,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    Sum,
)
from django.db.models.functions import Coalesce
from pretix.base.models import Order, OrderPosition

from pretix_custom_reports.contracts.errors import (
    CompilationError,
    FieldContractError,
)
from pretix_custom_reports.contracts.fields import Aggregate, Base

__all__ = [
    "AGGREGATE_FUNCTIONS",
    "PrefetchSpec",
    "RelationChain",
    "RelationHop",
    "base_model_for",
    "base_queryset",
    "exists_subquery",
    "join_prefetch_specs",
    "leaf_queryset",
    "position_queryset",
    "rebase_condition",
    "relation_chain",
    "select_related_prefix",
    "subquery_aggregate",
    "testmode_q",
]

#: Which model each report base yields one row of.
_BASE_MODELS = {Base.ORDER: Order, Base.ORDERPOSITION: OrderPosition}

#: Aggregate -> callable building the SQL expression. ``JOIN`` is absent on
#: purpose: Django 5.2 has no cross-backend string aggregation (``StringAgg`` lives
#: in ``django.contrib.postgres``, and this plugin has to work on SQLite and MySQL
#: too), so ``join`` goes through a prefetch and a Python join instead -- see
#: :func:`join_prefetch_specs` and ``query/columns.py``.
AGGREGATE_FUNCTIONS = {
    Aggregate.COUNT: lambda expr: Count(expr),
    Aggregate.COUNT_DISTINCT: lambda expr: Count(expr, distinct=True),
    Aggregate.SUM: Sum,
    Aggregate.MIN: Min,
    Aggregate.MAX: Max,
    Aggregate.AVG: Avg,
}

#: Aggregates whose empty result reads better as 0 than as blank. A missing sum is
#: genuinely unknown; a missing count is zero.
_COALESCE_TO_ZERO = frozenset({Aggregate.COUNT, Aggregate.COUNT_DISTINCT})


@dataclass(frozen=True)
class RelationHop:
    """One multi-valued step of a path."""

    accessor: str
    """Name of the relation as seen from the near side."""

    model: Any
    """Model on the far side."""

    back_lookup: str
    """Lookup that leads from :attr:`model` back to the near side."""


@dataclass(frozen=True)
class RelationChain:
    """A path from a base model across one or more multi-valued relations."""

    base_model: Any
    hops: Tuple[RelationHop, ...]
    remainder: str
    """Single-valued tail, relative to :attr:`leaf_model`. May be empty."""

    @property
    def leaf_model(self) -> Any:
        """The model whose rows get aggregated or tested for existence."""
        return self.hops[-1].model

    @property
    def correlation_path(self) -> str:
        """Lookup from :attr:`leaf_model` back to a row of :attr:`base_model`.

        ``all_positions__answers__answer`` -> ``orderposition__order``.
        """
        return "__".join(hop.back_lookup for hop in reversed(self.hops))

    def prefix_to_hop(self, index: int) -> str:
        """Lookup from :attr:`leaf_model` to the model of hop *index*.

        Empty for the last hop, since that *is* the leaf.
        """
        # The bound is a name rather than ``index + 1`` inline because black and
        # flake8 disagree about the spacing of a slice with an expression in it
        # (E203 vs. PEP 8), and both have to pass.
        start = index + 1
        return "__".join(hop.back_lookup for hop in reversed(self.hops[start:]))

    def accessor_path(self, index: int) -> str:
        """Forward lookup from the base model down to hop *index*."""
        return "__".join(hop.accessor for hop in self.hops[: index + 1])


@dataclass(frozen=True)
class PrefetchSpec:
    """A prefetch level a ``join`` column needs, before it becomes a ``Prefetch``.

    Data rather than a ready ``Prefetch`` so that the plan can de-duplicate the
    intermediate levels: Django refuses the same lookup twice with two querysets
    ("lookup was already seen with a different queryset"), and three ``join``
    columns over ``all_positions`` would otherwise collide.
    """

    lookup: str
    queryset: QuerySet
    to_attr: Optional[str] = None

    @property
    def dedup_key(self) -> Tuple[str, Optional[str]]:
        """Two specs with the same key are interchangeable."""
        return (self.lookup, self.to_attr)

    def build(self) -> Prefetch:
        """The real ``Prefetch`` object."""
        return Prefetch(self.lookup, queryset=self.queryset, to_attr=self.to_attr)


def base_model_for(base: Base) -> Any:
    """The model class rows of *base* are instances of."""
    try:
        return _BASE_MODELS[base]
    except KeyError:  # pragma: no cover - Base is a closed enum
        raise CompilationError(f"Unknown report base {base!r}.") from None


def position_queryset(include_canceled: bool) -> QuerySet:
    """``OrderPosition`` queryset honouring ``include_canceled_positions``."""
    manager = OrderPosition.all if include_canceled else OrderPosition.objects
    return manager.all()


def base_queryset(base: Base, event: Any, include_canceled: bool) -> QuerySet:
    """Row source for *base*, hard-limited to *event*."""
    if base is Base.ORDER:
        return Order.objects.filter(event=event)
    if base is Base.ORDERPOSITION:
        return position_queryset(include_canceled).filter(order__event=event)
    raise CompilationError(f"Unknown report base {base!r}.")


def testmode_q(base: Base) -> Q:
    """``Q`` excluding test-mode orders for *base*."""
    if base is Base.ORDER:
        return Q(testmode=False)
    return Q(order__testmode=False)


# ---------------------------------------------------------------------------
# Path analysis
# ---------------------------------------------------------------------------


def relation_chain(model: Any, orm_path: str) -> Optional[RelationChain]:
    """Analyse *orm_path* and return its multi-valued hops, or ``None``.

    ``None`` means "single-valued -- treat this as a plain lookup", which is the
    right answer for a concrete field, for a forward foreign key chain, and for an
    annotation alias the registry added (which is not a model field at all).
    """
    segments = orm_path.split("__")
    hops: List[RelationHop] = []
    current = model
    index = 0
    while index < len(segments):
        field = _get_field(current, segments[index])
        if field is None or not field.is_relation:
            break
        if not (field.one_to_many or field.many_to_many):
            break
        hops.append(
            RelationHop(
                accessor=segments[index],
                model=field.related_model,
                back_lookup=_back_lookup(field, segments[index]),
            )
        )
        current = field.related_model
        index += 1

    if not hops:
        return None
    return RelationChain(
        base_model=model, hops=tuple(hops), remainder="__".join(segments[index:])
    )


def select_related_prefix(model: Any, orm_path: str) -> Optional[str]:
    """Longest ``select_related`` argument covering *orm_path*'s parent.

    ``order__invoice_address__company`` -> ``order__invoice_address``. ``code`` ->
    ``None``. Returns ``None`` as soon as a segment is something ``select_related``
    cannot follow, so the caller can fall back to an ``F()`` annotation rather than
    walk into an N+1.
    """
    segments = orm_path.split("__")
    if len(segments) < 2:
        return None

    current = model
    usable: List[str] = []
    for segment in segments[:-1]:
        field = _get_field(current, segment)
        if field is None or not field.is_relation:
            return None
        # select_related follows forward FK/O2O and reverse O2O only.
        if not (field.many_to_one or field.one_to_one):
            return None
        usable.append(segment)
        current = field.related_model

    if _get_field(current, segments[-1]) is None:
        # The path ends in something that is not a field of the model we reached --
        # an annotation alias, most likely. Joining for it is pointless.
        return None
    return "__".join(usable)


def rebase_condition(chain: RelationChain, condition: Q, origin: str) -> Q:
    """Rewrite *condition* from base-relative lookups to leaf-relative ones.

    ``Q(all_positions__canceled=False)`` on base ``Order`` becomes
    ``Q(canceled=False)`` when the leaf is an ``OrderPosition``, and
    ``Q(orderposition__canceled=False)`` when the leaf is a ``QuestionAnswer`` two
    hops down. See the module docstring for why the two directions exist.

    :param origin: field key, for the error message.
    :raises FieldContractError: a lookup does not run through this chain at all,
        or is not a plain ``field=value`` pair. Both mean the registry declared a
        condition that does not belong to the path it declared, and **silently
        dropping it is the one outcome that must not happen**: a dropped
        ``question`` condition does not fail, it returns an answer aggregate over
        every question of the event.
    """

    def walk(node: Q) -> Q:
        children: List[Any] = []
        for child in node.children:
            if isinstance(child, Q):
                children.append(walk(child))
                continue
            if isinstance(child, (tuple, list)) and len(child) == 2:
                children.append(
                    (_rebase_lookup(chain, str(child[0]), origin), child[1])
                )
                continue
            raise FieldContractError(
                f"{origin}: the aggregate condition contains {child!r}, which is "
                f"not a plain lookup and cannot be rewritten onto "
                f"{chain.leaf_model.__name__}."
            )
        return Q(*children, _connector=node.connector, _negated=node.negated)

    return walk(condition)


def _rebase_lookup(chain: RelationChain, lookup: str, origin: str) -> str:
    """One base-relative lookup, seen from ``chain.leaf_model``.

    Longest accessor path first, so ``all_positions__answers__question`` is read
    as "the ``question`` of an answer" and not as "the ``answers__question`` of a
    position" -- which happens to address the same column here, but would not once
    a chain contains two relations of the same name.
    """
    for index in reversed(range(len(chain.hops))):
        accessor = chain.accessor_path(index)
        if lookup == accessor:
            remainder = ""
        elif lookup.startswith(f"{accessor}__"):
            start = len(accessor) + 2  # named for the same reason as in prefix_to_hop
            remainder = lookup[start:]
        else:
            continue
        back = chain.prefix_to_hop(index)
        if not remainder:
            # A condition on the related object itself, e.g. ``Q(all_positions=42)``.
            return back or "pk"
        return f"{back}__{remainder}" if back else remainder
    raise FieldContractError(
        f"{origin}: the aggregate condition constrains {lookup!r}, which does not "
        f"run through the relation the field's ORM path crosses "
        f"({' -> '.join(hop.accessor for hop in chain.hops)}). The condition and "
        f"the path have to describe the same relation."
    )


# ---------------------------------------------------------------------------
# Querysets over the far side of a chain
# ---------------------------------------------------------------------------


def leaf_queryset(chain: RelationChain, include_canceled: bool) -> QuerySet:
    """Queryset over ``chain.leaf_model``, with the canceled rule applied.

    The rule has to hold at whichever hop is an ``OrderPosition``, not only when
    the leaf happens to be one: on the ``answers`` chain the leaf is a
    ``QuestionAnswer`` and the canceled flag lives one hop up, reachable as
    ``orderposition__canceled``.
    """
    leaf = chain.leaf_model
    if leaf is OrderPosition:
        queryset = position_queryset(include_canceled)
    else:
        queryset = leaf._default_manager.all()

    if not include_canceled:
        for index, hop in enumerate(chain.hops):
            if hop.model is OrderPosition and index != len(chain.hops) - 1:
                prefix = chain.prefix_to_hop(index)
                lookup = f"{prefix}__canceled" if prefix else "canceled"
                queryset = queryset.filter(**{lookup: False})
    return queryset


def subquery_aggregate(
    model: Any,
    orm_path: str,
    aggregate: Aggregate,
    include_canceled: bool,
    relation_filter: Optional[Q] = None,
) -> Any:
    """A correlated aggregate over a multi-valued relation, as one expression.

    :param relation_filter: ``Q`` on the leaf model, narrowing which related rows
        take part. This is what makes ``answer.<identifier>`` expressible at all:
        without ``question__identifier=...`` the aggregate would mix every
        question's answers together.
    """
    chain = relation_chain(model, orm_path)
    if chain is None:
        raise CompilationError(
            f"{orm_path!r} is not a multi-valued relation on {model.__name__}; "
            f"it cannot be aggregated."
        )
    func = AGGREGATE_FUNCTIONS.get(aggregate)
    if func is None:
        raise CompilationError(
            f"Aggregate {aggregate} is not available as a database expression."
        )

    correlation = chain.correlation_path
    inner = leaf_queryset(chain, include_canceled).filter(
        **{correlation: OuterRef("pk")}
    )
    if relation_filter is not None:
        inner = inner.filter(relation_filter)

    target = chain.remainder or "pk"
    inner = (
        inner.order_by()
        .values(correlation)
        .annotate(_pcr_value=func(target))
        .values("_pcr_value")[:1]
    )

    if aggregate in _COALESCE_TO_ZERO:
        return Coalesce(
            Subquery(inner, output_field=IntegerField()),
            0,
            output_field=IntegerField(),
        )
    return Subquery(inner)


def exists_subquery(
    model: Any,
    orm_path: str,
    condition: Q,
    include_canceled: bool,
    relation_filter: Optional[Q] = None,
) -> Any:
    """``Exists(...)`` over the relation *orm_path* crosses.

    A filter on position-level data in an order-based report is an existence test,
    not a join (docs/adr/0001-contracts.md section 7a: "orders that contain product
    X"). ``Order.objects.filter(all_positions__item=...)`` would return an order
    once per matching position; ``Exists`` returns it once and needs no
    ``distinct()``, which would otherwise interact badly with ordering and with
    ``iterator()``.
    """
    chain = relation_chain(model, orm_path)
    if chain is None:
        raise CompilationError(
            f"{orm_path!r} is not a multi-valued relation on {model.__name__}; "
            f"it cannot be turned into an existence filter."
        )
    inner = leaf_queryset(chain, include_canceled).filter(
        **{chain.correlation_path: OuterRef("pk")}
    )
    if relation_filter is not None:
        inner = inner.filter(relation_filter)
    return Exists(inner.filter(condition).order_by().values("pk"))


def join_prefetch_specs(
    model: Any,
    orm_path: str,
    include_canceled: bool,
    relation_filter: Optional[Q],
    leaf_to_attr: str,
) -> Tuple[Tuple[PrefetchSpec, ...], Tuple[str, ...]]:
    """Prefetch levels and the attribute path a ``join`` renderer walks.

    Returns ``(specs, read_path)``. *specs* has one entry per multi-valued hop,
    outermost first -- Django needs the parent of a nested prefetch prefetched too.
    Only the innermost carries the ``relation_filter``, the ``select_related`` for
    the single-valued tail, and a *unique* ``to_attr``.

    The unique ``to_attr`` on the leaf is what lets three ``join`` columns coexist
    over the same relation with three different filters. The intermediate levels
    deliberately get no ``to_attr``, so they de-duplicate to one query no matter how
    many columns need them.
    """
    chain = relation_chain(model, orm_path)
    if chain is None:
        raise CompilationError(
            f"{orm_path!r} is not a multi-valued relation on {model.__name__}; "
            f"aggregate 'join' needs one."
        )

    specs: List[PrefetchSpec] = []
    last = len(chain.hops) - 1
    for index, hop in enumerate(chain.hops):
        is_leaf = index == last
        if hop.model is OrderPosition:
            queryset = position_queryset(include_canceled)
        else:
            queryset = hop.model._default_manager.all()
        if is_leaf:
            if relation_filter is not None:
                queryset = queryset.filter(relation_filter)
            inner_select = select_related_prefix(hop.model, chain.remainder)
            if inner_select:
                queryset = queryset.select_related(inner_select)
        # Deterministic order inside the cell: without it the content depends on
        # whatever order the database returns, and the same report produces two
        # different files on two runs. ``pk`` exists on every model.
        specs.append(
            PrefetchSpec(
                lookup=chain.accessor_path(index),
                queryset=queryset.order_by("pk"),
                to_attr=leaf_to_attr if is_leaf else None,
            )
        )

    read_path = tuple(hop.accessor for hop in chain.hops[:-1]) + (leaf_to_attr,)
    if chain.remainder:
        read_path = read_path + tuple(chain.remainder.split("__"))
    return tuple(specs), read_path


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


def _back_lookup(field: Any, accessor: str) -> str:
    """Lookup from ``field.related_model`` back to the model declaring *field*."""
    if getattr(field, "auto_created", False) and not getattr(field, "concrete", True):
        # Reverse relation (ManyToOneRel / ManyToManyRel): the forward field on the
        # related model is the way back.
        return field.field.name
    # Forward many-to-many declared on this model.
    try:
        return field.related_query_name()
    except Exception:  # pragma: no cover - defensive
        return accessor


_FIELD_CACHE: Dict[Tuple[Any, str], Any] = {}


def _get_field(model: Any, name: str) -> Any:
    """``model._meta.get_field(name)`` or ``None``. Never raises, never queries.

    Introspection only. That is what lets the whole validation pass run before a
    queryset exists, which in turn is what makes "an unknown field key never
    reaches the ORM" provable instead of merely intended.
    """
    key = (model, name)
    if key in _FIELD_CACHE:
        return _FIELD_CACHE[key]
    try:
        field = model._meta.get_field(name)
    except Exception:  # FieldDoesNotExist, plus whatever a custom field raises
        field = None
    _FIELD_CACHE[key] = field
    return field
