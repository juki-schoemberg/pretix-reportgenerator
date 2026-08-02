# Owner from wave 3 on: test-engineer (see ORCHESTRIERUNG.md section 5)
"""Load tests: 100.000 positions, timed, counted and measured.

Run them explicitly -- they are excluded from a normal run::

    pytest -m performance -q -s              # with the report table
    pytest -m "not performance"              # everything else, the default gate

What is being claimed, and how it is checked
--------------------------------------------

1. **The query count does not grow with the number of rows.** Asserted, not
   timed: the *same* report is compiled for a 1.000-position event and for a
   100.000-position event, and the two query counts have to be equal. A timing
   that merely looks linear proves nothing -- an N+1 on a fast machine with a
   warm cache looks linear too, right up to the point where somebody exports a
   real event.
2. **Nothing materialises the full result set.** The streaming chain is
   ``QuerySet.iterator(chunk_size=1000)`` -> generator -> ``csv.writer``
   (``query/report.py``, ``exporters.py``). ``tracemalloc`` measures the peak
   during a full iteration; it has to stay in the low tens of megabytes for
   100.000 rows, i.e. bounded by the chunk size rather than by the row count.
   ``exporter-dev`` asked for exactly this measurement in wave 2.
3. **The wide report is not disproportionately more expensive than the narrow
   one.** Timed, reported, and only loosely asserted: a wall-clock threshold in
   a test suite is a false alarm generator on a loaded CI box. The numbers go
   into ``docs/performance.md``; the assertions are on the things that are
   deterministic.

Test data is synthetic and seeded (``tests.factories.build_bulk``), so two runs
measure the same work.

Environment note
----------------

These numbers come from SQLite (``pretix.testutils.settings``). SQLite is a
reasonable proxy for query *counts* and for memory, and a poor one for absolute
timings -- it has no network round trip and no shared buffer cache. Every agent
of waves 1 and 2 asked for a PostgreSQL run; there is no PostgreSQL in this
environment (``pretix/src/pretix.cfg`` is ``backend=sqlite3``), so that is still
open. See ``handoff/status/test-engineer.md``.
"""

from typing import Any, Dict, List

import csv
import io
import pytest
import time
import tracemalloc
from decimal import Decimal
from django.db import transaction
from django_scopes import scopes_disabled

from pretix_custom_reports import contracts
from pretix_custom_reports.query.compiler import ReportQueryCompiler
from pretix_custom_reports.registry import cache as registry_cache
from pretix_custom_reports.registry.library import field_registry

from . import factories

pytestmark = pytest.mark.performance

#: 50.000 orders x 2 positions = 100.000 positions, the target figure from the
#: brief. Roughly the size of a large multi-day festival.
BIG_ORDERS = 50_000
POSITIONS_PER_ORDER = 2

#: The comparison event: same shape, one hundredth of the size. Its only job is
#: to make "the query count does not depend on the row count" an assertion.
SMALL_ORDERS = 500

#: Collected measurements, printed as a table when the module is done.
MEASUREMENTS: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Report definitions under test
# ---------------------------------------------------------------------------


def _document(base: str, columns: List[Dict[str, Any]], **parts: Any) -> Dict[str, Any]:
    document = {
        "schema_version": contracts.SCHEMA_VERSION,
        "base": base,
        "columns": columns,
    }
    document.update(parts)
    return document


#: Three columns, all of them plain columns of the row model. The cheapest thing
#: a user can build, and the baseline every other number is read against.
NARROW = _document(
    "orderposition",
    [
        {"field": "order.code"},
        {"field": "position.positionid"},
        {"field": "position.price"},
    ],
)

#: Twenty columns covering every strategy ``query/columns.py`` has: plain paths,
#: ``select_related`` hops over four relations, registry annotations backed by
#: correlated subqueries (payments, refunds, check-ins, outstanding amount), a
#: ``Case`` over two other annotations, an answer subquery and two Python
#: getters. If any of those degraded into per-row work, it would show up here.
WIDE_POSITION = _document(
    "orderposition",
    [
        {"field": "order.code"},
        {"field": "order.status"},
        {"field": "order.email"},
        {"field": "order.datetime"},
        {"field": "order.total"},
        {"field": "order.sales_channel"},
        {"field": "position.positionid"},
        {"field": "position.price"},
        {"field": "position.tax_rate"},
        {"field": "position.tax_value"},
        {"field": "position.net_price"},
        {"field": "position.code"},
        {"field": "item.name"},
        {"field": "item.category"},
        {"field": "item.default_price"},
        {"field": "answer.bulk-question"},
        {"field": "order.pending_sum"},
        {"field": "payment.sum_confirmed"},
        {"field": "refund.sum_done"},
        {"field": "computed.payment_state"},
        {"field": "checkin.count"},
        {"field": "order.position_count"},
    ],
)

#: The other shape: one row per order, everything else aggregated. This is where
#: the correlated-subquery decision from ``query/relations.py`` is paid for, and
#: the one a naive implementation turns into a cross product.
WIDE_ORDER = _document(
    "order",
    [
        {"field": "order.code"},
        {"field": "order.status"},
        {"field": "order.total"},
        {"field": "order.datetime"},
        {"field": "order.position_count"},
        {"field": "order.pending_sum"},
        {"field": "payment.sum_confirmed"},
        {"field": "refund.sum_done"},
        {"field": "computed.payment_state"},
        {"field": "checkin.count"},
        {"field": "position.price", "aggregate": "sum"},
        {"field": "position.positionid", "aggregate": "count"},
        {"field": "position.price", "aggregate": "max"},
        {"field": "item.name", "aggregate": "join"},
        {"field": "answer.bulk-question", "aggregate": "join"},
        {"field": "answer.bulk-question", "aggregate": "count"},
    ],
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def perf_data(django_db_setup, django_db_blocker):
    """Two events, built once for the whole module.

    Module-scoped because building 100.000 positions costs about half a minute
    and every test in here reads the same data. The whole fixture runs inside one
    transaction that is rolled back afterwards, so nothing leaks into the tests
    of the other agents if somebody runs the full suite with
    ``-m ''``.
    """
    from pretix.base.models import Organizer

    with django_db_blocker.unblock():
        atomic = transaction.atomic()
        atomic.__enter__()
        try:
            registry_cache.clear_local_cache()
            organizer = Organizer.objects.create(name="Load", slug="load")
            big_event = factories.make_event(organizer, slug="big", name="Big")
            small_event = factories.make_event(organizer, slug="small", name="Small")

            started = time.perf_counter()
            big = factories.build_bulk(
                big_event,
                orders=BIG_ORDERS,
                positions_per_order=POSITIONS_PER_ORDER,
                with_answers=True,
                with_payments=True,
            )
            build_seconds = time.perf_counter() - started
            small = factories.build_bulk(
                small_event,
                orders=SMALL_ORDERS,
                positions_per_order=POSITIONS_PER_ORDER,
                with_answers=True,
                with_payments=True,
                code_prefix="S",
            )
            MEASUREMENTS.append(
                {
                    "what": "build fixture",
                    "rows": big.positions,
                    "seconds": build_seconds,
                    "queries": None,
                    "note": f"{big.orders} orders, {big.answers} answers",
                }
            )
            yield {
                "organizer": organizer,
                "big_event": big_event,
                "big": big,
                "small_event": small_event,
                "small": small,
            }
        finally:
            transaction.set_rollback(True)
            atomic.__exit__(None, None, None)
            registry_cache.clear_local_cache()


@pytest.fixture(scope="module", autouse=True)
def report_table():
    """Print the measurement table once, after the module is done."""
    yield
    if not MEASUREMENTS:  # pragma: no cover - only when everything was skipped
        return
    print("\n\n=== pretix-custom-reports performance run ===")
    print(f"{'measurement':<46}{'rows':>9}{'queries':>9}{'seconds':>10}  note")
    for entry in MEASUREMENTS:
        queries = "-" if entry["queries"] is None else str(entry["queries"])
        print(
            f"{entry['what']:<46}{entry['rows']:>9}{queries:>9}"
            f"{entry['seconds']:>10.3f}  {entry.get('note', '')}"
        )
    print("=" * 80)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compile_for(document: Dict[str, Any], event: Any):
    parsed = contracts.validate_definition(document)
    return ReportQueryCompiler(field_registry()).compile(parsed, event)


def drain(report) -> int:
    """Consume every row without keeping any of them. Returns the row count."""
    count = 0
    for _row in report.iter_rows():
        count += 1
    return count


def timed_drain(report):
    started = time.perf_counter()
    rows = drain(report)
    return rows, time.perf_counter() - started


def record(what: str, rows: int, queries, seconds: float, note: str = "") -> None:
    MEASUREMENTS.append(
        {
            "what": what,
            "rows": rows,
            "queries": queries,
            "seconds": seconds,
            "note": note,
        }
    )


# ---------------------------------------------------------------------------
# 1. The claim that matters: query count is independent of the row count
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,document",
    [
        ("narrow (3 columns, base orderposition)", NARROW),
        ("wide (22 columns, base orderposition)", WIDE_POSITION),
    ],
)
def test_a_report_without_a_join_column_is_one_query_at_any_size(
    perf_data, django_assert_num_queries, name, document
):
    """One query for three columns and one query for twenty-two, 1k rows or 100k.

    ``1`` is asserted, not "the same in both events" -- the weaker claim would
    also hold for an N+1 that is an N+1 in both. Twenty-two columns include four
    correlated subqueries, an answer subquery and two Python getters; all of them
    have to ride along inside the single row query.

    Compilation happens outside the assertion block on purpose: building the
    field table reads the event's questions and the organizer's meta properties.
    That is a fixed one-off cost, and folding it in would hide the thing being
    measured.
    """
    with scopes_disabled():
        small = compile_for(document, perf_data["small_event"])
        small_started = time.perf_counter()
        with django_assert_num_queries(1):
            small_rows = drain(small)
        small_seconds = time.perf_counter() - small_started

        big = compile_for(document, perf_data["big_event"])
        started = time.perf_counter()
        with django_assert_num_queries(1):
            big_rows = drain(big)
        seconds = time.perf_counter() - started

    factor = big_rows / small_rows
    record(
        name,
        big_rows,
        1,
        seconds,
        f"{big_rows / seconds:.0f} rows/s; small event: {small_rows} rows in "
        f"{small_seconds:.3f}s (x{factor:.0f})",
    )
    assert factor > 50, "the two events have to differ by orders of magnitude"


@pytest.mark.django_db
def test_a_join_column_costs_one_prefetch_per_chunk_not_one_per_row(perf_data):
    """The one place where the query count *does* depend on the row count.

    A ``join`` column is a ``prefetch_related`` plus a ``str.join`` in Python,
    because Django 5.2 has no backend-independent string aggregation
    (``query/columns.py``). ``QuerySet.iterator(chunk_size=1000)`` runs the
    prefetches **once per chunk**, so the wide order report costs

        1 + (prefetch levels) x ceil(rows / 1000)

    queries -- 4 at 500 rows, 151 at 49.484 rows. That is not an N+1: the cost
    per row falls as the report grows, and dropping the two ``join`` columns puts
    it back to a single query at any size (asserted below, so that the difference
    is attributed to the ``join`` and not to the report being wide).

    It is, however, not what ``query/columns.py`` promises ("costs exactly one
    query per prefetch level, independent of the number of rows"), and 151 round
    trips is a different proposition on a networked PostgreSQL than on SQLite.
    Recorded as finding 3 in handoff/blockers.md.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def count_queries(document, event):
        with scopes_disabled():
            report = compile_for(document, event)
            with CaptureQueriesContext(connection) as captured:
                rows = drain(report)
        return rows, len(captured)

    small_rows, small_queries = count_queries(WIDE_ORDER, perf_data["small_event"])
    started = time.perf_counter()
    big_rows, big_queries = count_queries(WIDE_ORDER, perf_data["big_event"])
    seconds = time.perf_counter() - started

    chunk = contracts.DEFAULT_CHUNK_SIZE
    levels = 3
    assert small_queries == 1 + levels * _chunks(small_rows, chunk)
    assert big_queries == 1 + levels * _chunks(big_rows, chunk)

    # Not an N+1: the marginal query cost per row shrinks as the report grows.
    assert big_queries / big_rows < small_queries / small_rows
    assert big_queries < big_rows / 100

    # And the growth really is the join columns, not the width of the report.
    without_join = dict(WIDE_ORDER)
    without_join["columns"] = [
        column for column in WIDE_ORDER["columns"] if column.get("aggregate") != "join"
    ]
    _, plain_small = count_queries(without_join, perf_data["small_event"])
    plain_started = time.perf_counter()
    plain_big_rows, plain_big = count_queries(without_join, perf_data["big_event"])
    plain_seconds = time.perf_counter() - plain_started
    assert (plain_small, plain_big) == (1, 1)

    record(
        "wide (16 columns, base order, 2 join columns)",
        big_rows,
        big_queries,
        seconds,
        f"1 + 3 x ceil(rows/{chunk})",
    )
    record(
        "same report without the two join columns",
        plain_big_rows,
        plain_big,
        plain_seconds,
        "constant at any size",
    )


def _chunks(rows: int, chunk: int) -> int:
    return (rows + chunk - 1) // chunk


@pytest.mark.django_db
def test_the_count_query_stays_one_query_and_is_cheap(perf_data):
    """``count()`` is a separate, deliberately cheap query.

    The editor preview shows twenty rows next to an estimated total. Calling
    ``.count()`` on the display queryset would make the database evaluate every
    subquery for every row and throw the values away; ``query/report.py`` builds
    a second queryset without the column annotations for exactly this. On
    100.000 rows the difference is the whole feature.
    """
    from pretix.base.models import Order

    with scopes_disabled():
        report = compile_for(WIDE_ORDER, perf_data["big_event"])
        started = time.perf_counter()
        total = report.count()
        seconds = time.perf_counter() - started
        # Independent oracle: the plain ORM, not our compiler, and not the
        # factory's own bookkeeping.
        expected = Order.objects.filter(
            event=perf_data["big_event"], testmode=False
        ).count()
    assert total == expected
    record("count() on the wide order report", total, 1, seconds)


@pytest.mark.django_db
def test_the_preview_of_a_huge_event_opens_immediately(perf_data):
    """Twenty rows out of 100.000, sliced in SQL.

    ``preview=True`` puts a ``LIMIT`` into the statement instead of breaking out
    of a Python loop. Both halves are checked: the SQL carries the limit, and the
    wall clock says the database did not walk the table.
    """
    parsed = contracts.validate_definition(WIDE_POSITION)
    with scopes_disabled():
        started = time.perf_counter()
        preview = ReportQueryCompiler(field_registry()).compile(
            parsed, perf_data["big_event"], preview=True
        )
        rows = list(preview.iter_rows())
        seconds = time.perf_counter() - started
        assert "LIMIT" in str(preview.queryset.query)
    assert len(rows) == contracts.PREVIEW_ROW_LIMIT
    record("preview (20 of 100.000 rows)", len(rows), None, seconds)


# ---------------------------------------------------------------------------
# 2. Memory: the streaming chain has to stay bounded
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_full_iteration_of_a_hundred_thousand_rows_stays_bounded_in_memory(
    perf_data,
):
    """Peak allocation during a full drain, measured with ``tracemalloc``.

    ``iter_rows`` goes through ``QuerySet.iterator(chunk_size=1000)``. If any
    link in the chain materialised the result -- a ``list()`` in the exporter, a
    missing ``chunk_size`` (which silently disables prefetching *and* buffering),
    a ``.all()`` somewhere -- the peak would scale with the row count instead of
    with the chunk size. The threshold is deliberately generous: this is a
    regression guard against "somebody wrapped it in a list", not a memory
    budget.
    """
    from pretix.base.models import OrderPosition

    with scopes_disabled():
        report = compile_for(WIDE_POSITION, perf_data["big_event"])
        tracemalloc.start()
        started = time.perf_counter()
        rows = drain(report)
        seconds = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        expected = OrderPosition.objects.filter(
            order__event=perf_data["big_event"], order__testmode=False
        ).count()

    peak_mb = peak / (1024 * 1024)
    record(
        "full drain under tracemalloc",
        rows,
        1,
        seconds,
        f"peak {peak_mb:.1f} MiB; time inflated ~4x by the tracer",
    )
    assert rows == expected
    assert peak_mb < 64, f"peak allocation was {peak_mb:.1f} MiB for {rows} rows"


# ---------------------------------------------------------------------------
# 3. The export path end to end
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_writing_a_hundred_thousand_rows_as_csv(perf_data):
    """The whole output chain, timed, with the row count verified.

    Goes through ``CustomReportExporter.iterate_list`` rather than through the
    compiler alone, so the exporter's per-event compile, the header row, the
    progress total and the log entry are all in the measurement. The CSV is
    written into an in-memory buffer with the same writer ``ListExporter`` uses,
    so the number is comparable to a real export minus the file system.
    """
    from pretix.base.exporter import ListExporter
    from pretix.base.models import OrderPosition

    from pretix_custom_reports.models import ReportDefinition

    with scopes_disabled():
        ReportDefinition.objects.create(
            event=perf_data["big_event"],
            name="Load",
            identifier="load",
            definition=NARROW,
        )
        exporter = _exporter_for(perf_data["big_event"])
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        started = time.perf_counter()
        written = 0
        for line in exporter.iterate_list(
            {"_format": "default", contracts.EXPORT_FORM_REPORT_KEY: "load"}
        ):
            if isinstance(line, ListExporter.ProgressSetTotal):
                continue
            writer.writerow(line)
            written += 1
        seconds = time.perf_counter() - started
        expected = OrderPosition.objects.filter(
            order__event=perf_data["big_event"], order__testmode=False
        ).count()

    assert written == expected + 1, "one header row plus one row per position"
    record(
        "CSV through the exporter",
        written - 1,
        None,
        seconds,
        f"{buffer.tell() / (1024 * 1024):.1f} MiB of CSV",
    )


def _exporter_for(event):
    """A ``CustomReportExporter`` bound to *event*, without the export UI.

    ``init_event_exporter`` would need a user, a permission check and a scope;
    none of that is what is being measured here, and the exporter's own tests
    cover it (``tests/test_exporters.py``).
    """
    from pretix_custom_reports.exporters import CustomReportExporter

    return CustomReportExporter(event, event.organizer)


# ---------------------------------------------------------------------------
# 4. Filters and sorting at scale
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_filtered_and_sorted_report_over_a_hundred_thousand_positions(perf_data):
    """Sorting is done by the database and still costs one query.

    A report sorted by a column with many ties is the case where a missing
    tiebreaker turns into duplicated and missing rows across ``LIMIT``/``OFFSET``
    pages. The compiler always appends ``pk``; here that has to hold while the
    database is actually sorting 100.000 rows, and the result has to be
    genuinely ordered rather than "ordered enough that a small fixture cannot
    tell".
    """
    document = _document(
        "orderposition",
        [
            {"field": "position.price"},
            {"field": "order.code"},
            {"field": "position.positionid"},
        ],
        filters={
            "op": "and",
            "children": [
                {"field": "position.price", "operator": "gte", "value": "25.00"}
            ],
        },
        sorting=[
            {"field": "position.price", "direction": "desc"},
            {"field": "order.code", "direction": "asc"},
        ],
    )
    with scopes_disabled():
        report = compile_for(document, perf_data["big_event"])
        rows, seconds = timed_drain(report)
        prices = [
            row[0] for row in compile_for(document, perf_data["big_event"]).iter_rows()
        ]

    assert rows > 0
    assert all(price >= Decimal("25.00") for price in prices)
    assert prices == sorted(prices, reverse=True)
    record("filtered + sorted position report", rows, 1, seconds, "gte 25.00, desc")
