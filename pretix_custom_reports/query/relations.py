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

import datetime
import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal
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
from pretix_custom_reports.contracts.fields import Aggregate, Base, DataType
from pretix_custom_reports.registry.annotations import (
    MONEY_DECIMAL_PLACES,
    MONEY_MAX_DIGITS,
    MoneyField,
)

__all__ = [
    "AGGREGATE_FUNCTIONS",
    "MONEY_AGGREGATES",
    "PrefetchSpec",
    "RelationChain",
    "RelationHop",
    "aggregate_expression",
    "base_model_for",
    "base_queryset",
    "condition_signature",
    "exists_subquery",
    "join_leaf_to_attr",
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
#:
#: Call :func:`aggregate_expression` rather than this table: the money aggregates
#: need an explicit output field, and the table alone does not know about it.
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

#: Aggregates that return an amount when they run over an amount, and therefore
#: have to carry :class:`~pretix_custom_reports.registry.annotations.MoneyField`
#: as their output field. ``COUNT``/``COUNT_DISTINCT`` return a cardinality and
#: have no scale to lose; ``JOIN`` never becomes SQL at all.
MONEY_AGGREGATES = frozenset(
    {Aggregate.SUM, Aggregate.MIN, Aggregate.MAX, Aggregate.AVG}
)


def money_output_field() -> MoneyField:
    """Output field that pins an amount to two decimal places on every backend.

    A fresh instance per call: a Django ``Field`` instance carries state once it
    is attached to an expression, so sharing one between two annotations of one
    queryset is asking for trouble.
    """
    return MoneyField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)


def aggregate_expression(
    aggregate: Aggregate, target: Any, datatype: Optional[DataType] = None
) -> Any:
    """Build the aggregate expression for *target*, with the right output field.

    Money is the one datatype where the aggregate has to say what it produces.
    ``Sum("price")`` infers its output field from the model column -- a plain
    ``DecimalField`` -- and SQLite's converter only quantises a *column*, not a
    computed expression, so a summed amount comes back as ``Decimal("23.5")``
    while ``order.total`` in the next cell of the same row comes back as
    ``Decimal("23.50")``. One report, two notations, and a different file on
    PostgreSQL, which keeps the scale of ``numeric(13, 2)`` across ``SUM``. That
    is T-002 in handoff/blockers.md; ``registry/annotations.py`` fixed it for the
    registry's own money expressions, and this is the same fix for the aggregate
    the *user* picks in the editor.

    Neither ``Cast`` nor ``Round`` helps -- measured by registry-dev and written
    up in the :class:`~pretix_custom_reports.registry.annotations.MoneyField`
    docstring: the scale is lost in the converter, not in the SQL, so the fix has
    to hang on the output field.

    **``AVG`` is quantised too, and that is a rounding decision, not a format
    correction.** An average of three amounts is mathematically not an amount
    with two decimal places, and 43,00 / 3 is a periodic number that no scale
    represents. It is rounded here anyway, because the alternative is worse: the
    unrounded value is whatever the backend's own arithmetic produced --
    ``14.333333333333334`` from SQLite's float path, a ``numeric`` of
    backend-defined scale from PostgreSQL -- so leaving it alone does not
    preserve precision, it exports an artefact of the installation. A cell
    labelled "average price" reads as money, is compared against money and gets
    added up by hand in a spreadsheet, so it is presented as money, rounded
    half-even by ``DecimalField``'s own context. Anyone who needs the exact
    quotient has the sum and the count as separate columns.

    :param datatype: the **registry's** declaration for the field, not something
        inferred from the model (CLAUDE.md rule 2). ``None`` means "not money",
        which is the safe default: it changes nothing.
    """
    func = AGGREGATE_FUNCTIONS.get(aggregate)
    if func is None:
        raise CompilationError(
            f"Aggregate {aggregate} is not available as a database expression."
        )
    if datatype is DataType.MONEY and aggregate in MONEY_AGGREGATES:
        return func(target, output_field=money_output_field())
    return func(target)


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

    Data rather than a ready ``Prefetch`` so that the plan can de-duplicate
    levels: Django refuses the same lookup twice with two querysets ("lookup was
    already seen with a different queryset"), and three ``join`` columns over
    ``all_positions`` would otherwise collide.
    """

    lookup: str
    queryset: QuerySet
    to_attr: Optional[str] = None

    @property
    def dedup_key(self) -> Tuple[str, Optional[str]]:
        """Two specs with the same key are interchangeable.

        That is a real claim, not a hope, and it rests on where ``to_attr`` comes
        from: :func:`join_prefetch_specs` derives it from everything that shapes
        the leaf queryset -- relation, condition, canceled rule, inner
        ``select_related`` -- so an equal key means an equivalent queryset. An
        intermediate level has no ``to_attr`` and is built the same way for every
        column that crosses it. See :func:`join_leaf_to_attr` (S-005).
        """
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
    datatype: Optional[DataType] = None,
) -> Any:
    """A correlated aggregate over a multi-valued relation, as one expression.

    :param relation_filter: ``Q`` on the leaf model, narrowing which related rows
        take part. This is what makes ``answer.<identifier>`` expressible at all:
        without ``question__identifier=...`` the aggregate would mix every
        question's answers together.
    :param datatype: what the registry says the field is. Only
        :attr:`~pretix_custom_reports.contracts.fields.DataType.MONEY` changes
        anything -- see :func:`aggregate_expression`. The output field travels out
        of the inner queryset with the selected column, so the ``Subquery`` around
        it inherits the quantisation without being told again.
    """
    chain = relation_chain(model, orm_path)
    if chain is None:
        raise CompilationError(
            f"{orm_path!r} is not a multi-valued relation on {model.__name__}; "
            f"it cannot be aggregated."
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
        .annotate(_pcr_value=aggregate_expression(aggregate, target, datatype))
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


#: Prefix of a content-derived ``to_attr``. No double underscore, so it can never
#: be read as a lookup path, and namespaced so it cannot shadow a model attribute.
_JOIN_ATTR_PREFIX = "pcr_j"

#: Value types a condition may contain and still be compared as text: ``repr`` is
#: unambiguous for all of them and tells ``1``, ``True``, ``1.0``, ``"1"`` and
#: ``Decimal("1")`` apart. Anything else -- a model instance above all -- gets no
#: signature, because two of them can share a ``repr`` and differ in the database.
_SIGNATURE_SCALARS = (
    bool,
    int,
    float,
    str,
    bytes,
    Decimal,
    datetime.date,
    datetime.time,
    datetime.timedelta,
    uuid.UUID,
)


def condition_signature(condition: Optional[Q]) -> Optional[str]:
    """Text that two equivalent ``Q`` objects share, or ``None`` if unrepresentable.

    A stricter cousin of ``str(Q)``. The looser form would do for the conditions
    :mod:`pretix_custom_reports.registry.hints` builds -- they are JSON-safe
    primitives by contract -- but ``str`` renders a model instance through its
    ``__str__``, and two ``Question`` rows with the same label would then look
    equal. Merging those two prefetches would put one question's answers in the
    other question's column: wrong output, no error. So unrepresentable values
    yield ``None``, and the caller falls back to keeping the prefetches apart.

    ``None`` as *input* is a condition in its own right ("no condition") and gets
    its own signature; ``None`` as *output* means "cannot say".

    The result is stable within a process, which is all it is used for: keying the
    prefetches of one compile run. It is not canonical -- ``Q(a=1) & Q(b=2)`` and
    ``Q(b=2) & Q(a=1)`` sign differently -- and it does not need to be. Two
    columns that ask for the same thing build their condition the same way,
    because both come from the same registry field.
    """
    if condition is None:
        return "none"
    return _q_signature(condition)


def _q_signature(node: Q) -> Optional[str]:
    parts: List[str] = []
    for child in node.children:
        if isinstance(child, Q):
            token = _q_signature(child)
        elif isinstance(child, (tuple, list)) and len(child) == 2:
            value = _value_signature(child[1])
            token = None if value is None else f"{child[0]}={value}"
        else:
            token = None
        if token is None:
            return None
        parts.append(token)
    prefix = "NOT" if node.negated else ""
    return f"{prefix}({node.connector}:{','.join(parts)})"


def _value_signature(value: Any) -> Optional[str]:
    if value is None or isinstance(value, _SIGNATURE_SCALARS):
        return repr(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_value_signature(item) for item in value]
        if any(item is None for item in items):
            return None
        # An ``in`` lookup against a set has no inherent order. Sorting the tokens
        # makes two equal sets sign equally; for a list the order is part of the
        # value and is kept.
        if isinstance(value, (set, frozenset)):
            items = sorted(items)
        return "[" + ",".join(items) + "]"
    return None


def join_leaf_to_attr(
    leaf_model: Any,
    lookup: str,
    include_canceled: bool,
    inner_select: Optional[str],
    condition: Optional[Q],
) -> Optional[str]:
    """Name for the leaf ``to_attr``, shared by every column that wants the same rows.

    Two ``join`` columns may share one ``Prefetch`` exactly when their leaf
    querysets select the same rows *and* carry the same ``select_related`` -- so
    the name is derived from all four things that shape it, and from nothing else.
    Notably not from the column index: that is what made the de-duplication in
    :func:`~pretix_custom_reports.query.plan._dedupe_prefetches` a no-op and let
    twenty identical ``join`` columns cost twenty prefetch queries (S-005 in
    docs/security-review.md).

    ``inner_select`` is part of the identity because dropping it would be an N+1
    dressed up as a saving: ``item.name`` needs ``select_related("item")`` on the
    prefetched positions, ``position.attendee_name`` does not, and a shared
    prefetch built for the second would make the first fetch an item per row.

    Returns ``None`` when the condition has no faithful text form
    (:func:`condition_signature`); the caller then falls back to a per-column name
    and the old, always-separate behaviour.
    """
    signature = condition_signature(condition)
    if signature is None:
        return None
    parts = (
        leaf_model._meta.label_lower,
        lookup,
        "with-canceled" if include_canceled else "without-canceled",
        inner_select or "",
        signature,
    )
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{_JOIN_ATTR_PREFIX}{digest[:16]}"


def join_prefetch_specs(
    model: Any,
    orm_path: str,
    include_canceled: bool,
    relation_filter: Optional[Q],
    fallback_to_attr: str,
) -> Tuple[Tuple[PrefetchSpec, ...], Tuple[str, ...]]:
    """Prefetch levels and the attribute path a ``join`` renderer walks.

    Returns ``(specs, read_path)``. *specs* has one entry per multi-valued hop,
    outermost first -- Django needs the parent of a nested prefetch prefetched too.
    Only the innermost carries the ``relation_filter``, the ``select_related`` for
    the single-valued tail, and a ``to_attr``.

    The leaf ``to_attr`` is what lets three ``join`` columns coexist over the same
    relation with three different filters -- and, since it is derived from the
    queryset's identity rather than from the column (:func:`join_leaf_to_attr`),
    what lets three columns that want the *same* rows share one query. The
    intermediate levels deliberately get no ``to_attr``, so they collapse to one
    query no matter how many columns need them.

    :param fallback_to_attr: per-column name, used only when the leaf queryset has
        no derivable identity. Must be unique across the report.
    """
    chain = relation_chain(model, orm_path)
    if chain is None:
        raise CompilationError(
            f"{orm_path!r} is not a multi-valued relation on {model.__name__}; "
            f"aggregate 'join' needs one."
        )

    last = len(chain.hops) - 1
    leaf_hop = chain.hops[last]
    inner_select = select_related_prefix(leaf_hop.model, chain.remainder)
    leaf_to_attr = (
        join_leaf_to_attr(
            leaf_hop.model,
            chain.accessor_path(last),
            include_canceled,
            inner_select,
            relation_filter,
        )
        or fallback_to_attr
    )

    specs: List[PrefetchSpec] = []
    for index, hop in enumerate(chain.hops):
        is_leaf = index == last
        if hop.model is OrderPosition:
            queryset = position_queryset(include_canceled)
        else:
            queryset = hop.model._default_manager.all()
        if is_leaf:
            if relation_filter is not None:
                queryset = queryset.filter(relation_filter)
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
