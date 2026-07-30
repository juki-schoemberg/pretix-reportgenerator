"""Pass two: the executable report -- queryset, rows, count, preview.

Owner from wave 1 on: query-dev (ORCHESTRIERUNG.md section 5).

:class:`CompiledReport` satisfies the
:class:`~pretix_custom_reports.contracts.protocols.CompiledReport` protocol. It
holds three querysets built from one :class:`~pretix_custom_reports.query.plan.QueryPlan`:

* :attr:`~CompiledReport.queryset` -- the real thing, annotated, filtered,
  ordered, joined, sliced to ``options.row_limit``,
* a **count** queryset -- the same rows, but without the column annotations,
  without ordering and without joins,
* nothing else. There is no lazily built fourth variant, because two querysets
  that are supposed to select the same rows are already one too many to keep in
  sync by eye; both are derived here, side by side, from the same plan.

Why the count query is separate
-------------------------------

The live preview shows twenty rows plus an estimated total (SPEC.md F2). Calling
``.count()`` on the display queryset would make the database compute every
subquery aggregate and every join for every row, only to throw the values away --
on a six-digit event that is the difference between a preview that opens and one
that times out. So the count query keeps the filters (they decide *which* rows
count), keeps the annotations the filters refer to (without them the filter would
not compile), and drops everything else, including ``order_by`` -- ordering a
``COUNT(*)`` is pure waste and on some backends it is not even free.

Streaming
---------

:meth:`~CompiledReport.iter_rows` goes through ``QuerySet.iterator(chunk_size)``.
That matters twice: it keeps a 200k-row export out of memory, and since Django
4.1 ``iterator()`` honours ``prefetch_related`` per chunk, so the one prefetch a
``join`` column needs stays bounded too instead of loading every position of
every order up front.
"""

from typing import Any, Iterator, List, Optional, Sequence

from django.db.models import QuerySet

from pretix_custom_reports.contracts.definition import (
    PREVIEW_ROW_LIMIT,
    ReportDefinition,
)
from pretix_custom_reports.contracts.fields import Base
from pretix_custom_reports.contracts.protocols import (
    DEFAULT_CHUNK_SIZE,
    CompiledColumn,
)
from pretix_custom_reports.query import relations
from pretix_custom_reports.query.plan import QueryPlan

__all__ = ["CompiledReport", "build_report"]


class CompiledReport:
    """An executable report bound to exactly one event."""

    def __init__(
        self,
        plan: QueryPlan,
        queryset: QuerySet,
        count_queryset: QuerySet,
        effective_limit: Optional[int],
        preview: bool = False,
    ) -> None:
        self.plan = plan
        self.definition: ReportDefinition = plan.definition
        self.base: Base = plan.base
        self.event: Any = plan.event
        self.columns: Sequence[CompiledColumn] = plan.columns
        self.queryset = queryset
        self.count_queryset = count_queryset
        self.effective_limit = effective_limit
        """Row cap actually applied to :attr:`queryset`, ``None`` for uncapped."""

        self.preview = preview
        """True if this instance was compiled for the editor preview."""

    # -- CompiledReport protocol ------------------------------------------

    def headers(self) -> List[str]:
        """Header row, in output order."""
        return [column.label for column in self.columns]

    def iter_rows(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        limit: Optional[int] = None,
    ) -> Iterator[List[Any]]:
        """Yield one list of cell values per row.

        *limit* caps the rows on top of ``options.row_limit`` and is what the
        preview uses; it is applied in Python rather than by re-slicing the
        queryset, so that a caller can ask for ten rows of an already sliced
        queryset without Django having to reason about nested slices.
        """
        if limit is not None and limit <= 0:
            return
        renderers = [column.render for column in self.columns]
        for index, row in enumerate(self.queryset.iterator(chunk_size=chunk_size)):
            if limit is not None and index >= limit:
                return
            yield [render(row) for render in renderers]

    def count(self) -> int:
        """Number of rows this report produces.

        A separate, deliberately cheap ``COUNT(*)`` -- see the module docstring.
        ``options.row_limit`` is applied on top, because a capped report really
        does produce at most that many rows and showing the uncapped total next
        to twenty preview rows would be a lie.
        """
        total = self.count_queryset.count()
        if self.plan.row_limit is not None:
            return min(total, self.plan.row_limit)
        return total

    # -- convenience -------------------------------------------------------

    def rows(self, limit: Optional[int] = None) -> List[List[Any]]:
        """Materialise the rows. For tests and the preview, not for exports."""
        return list(self.iter_rows(limit=limit))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<CompiledReport base={self.base} columns={len(self.columns)} "
            f"preview={self.preview}>"
        )


def build_report(plan: QueryPlan, preview: bool = False) -> CompiledReport:
    """Apply *plan* to real querysets.

    :param preview: cap the queryset at
        :data:`~pretix_custom_reports.contracts.definition.PREVIEW_ROW_LIMIT`
        rows. A hard ``LIMIT`` in SQL, not a Python break: the preview must never
        make the database materialise the full result set (SPEC.md section 4).
    """
    base_qs = relations.base_queryset(
        plan.base, plan.event, plan.include_canceled_positions
    )

    display = base_qs
    if plan.annotations:
        display = display.annotate(**plan.annotations)
    if plan.filter_q is not None:
        display = display.filter(plan.filter_q)
    if plan.select_related:
        display = display.select_related(*plan.select_related)
    if plan.prefetch_related:
        display = display.prefetch_related(*plan.prefetch_related)
    # order_by() replaces the model's Meta.ordering rather than adding to it,
    # which is what we want: Order sorts by ("-datetime", "-pk") by default and
    # that would silently become the primary sort of every report.
    display = display.order_by(*plan.ordering)

    limit = _effective_limit(plan.row_limit, preview)
    if limit is not None:
        display = display[:limit]

    counting = base_qs
    if plan.filter_annotations:
        counting = counting.annotate(**plan.filter_annotations)
    if plan.filter_q is not None:
        counting = counting.filter(plan.filter_q)
    counting = counting.order_by()

    return CompiledReport(
        plan=plan,
        queryset=display,
        count_queryset=counting,
        effective_limit=limit,
        preview=preview,
    )


def _effective_limit(row_limit: Optional[int], preview: bool) -> Optional[int]:
    if not preview:
        return row_limit
    if row_limit is None:
        return PREVIEW_ROW_LIMIT
    return min(row_limit, PREVIEW_ROW_LIMIT)
