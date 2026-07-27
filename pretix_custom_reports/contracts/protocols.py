"""Structural interfaces between registry, query compiler and their consumers.

Owner: contract-architect (wave 0c). Frozen -- see ``contracts/__init__.py``.

These are :class:`typing.Protocol` classes: nothing has to inherit from them.
The real ``FieldRegistry`` in ``registry/`` and the real ``QueryCompiler`` in
``query/`` simply have to match the signatures. Consumers type against the
protocol and can therefore be developed against
:mod:`pretix_custom_reports.contracts.stubs` before either exists.

Django and pretix types appear only under ``TYPE_CHECKING`` so that this module
imports without configured settings.
"""

from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
    runtime_checkable,
)

from dataclasses import dataclass

from pretix_custom_reports.contracts.definition import (
    FieldReference,
    ReportDefinition,
)
from pretix_custom_reports.contracts.fields import (
    Aggregate,
    Base,
    DataType,
    ReportField,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from django.db.models import QuerySet
    from pretix.base.models import Event

    EventType = Event
    QuerySetType = QuerySet
else:
    EventType = Any
    QuerySetType = Any

__all__ = [
    "CompiledColumn",
    "CompiledReport",
    "DEFAULT_CHUNK_SIZE",
    "EXPORT_FORM_REPORT_KEY",
    "FieldRegistry",
    "LOG_ACTION_ADDED",
    "LOG_ACTION_CHANGED",
    "LOG_ACTION_DELETED",
    "LOG_ACTION_EXECUTED",
    "LOG_ACTION_EXPORTED",
    "LOG_ACTION_IMPORTED",
    "LOG_ACTION_PREFIX",
    "LOG_ACTION_TEMPLATE_APPLIED",
    "QueryCompiler",
    "REGISTER_FIELDS_SIGNAL_NAME",
    "RowRenderer",
    "find_unresolved_fields",
]


#: Rows fetched per database round trip when streaming a report
#: (``QuerySet.iterator(chunk_size=...)``). Reports run against events with
#: six-digit position counts (SPEC.md section 4).
DEFAULT_CHUNK_SIZE = 1000

#: Key under which the exporter stores the report reference in
#: ``ScheduledExport.export_form_data``. The value is the report's stable
#: ``identifier`` string, never a primary key -- see
#: docs/adr/0001-contracts.md section 5.
EXPORT_FORM_REPORT_KEY = "report"

#: Name of the ``EventPluginSignal`` third-party plugins connect to in order to
#: contribute :class:`~.fields.ReportField` objects (SPEC.md F5). Declared here
#: so that ``registry-dev`` (which defines the signal) and ``integrator`` (who
#: owns ``signals.py``) cannot disagree about it.
REGISTER_FIELDS_SIGNAL_NAME = "register_report_fields"

#: Prefix for ``log_action`` action types. pretix's own documentation
#: (doc/development/implementation/logging.rst) asks plugins to prefix with
#: their package name; core plugins use ``pretix.plugins.<name>.*``, which a
#: third-party plugin has no claim to.
LOG_ACTION_PREFIX = "pretix_custom_reports"

LOG_ACTION_ADDED = f"{LOG_ACTION_PREFIX}.report.added"
LOG_ACTION_CHANGED = f"{LOG_ACTION_PREFIX}.report.changed"
LOG_ACTION_DELETED = f"{LOG_ACTION_PREFIX}.report.deleted"
LOG_ACTION_EXECUTED = f"{LOG_ACTION_PREFIX}.report.executed"
LOG_ACTION_EXPORTED = f"{LOG_ACTION_PREFIX}.report.exported"
LOG_ACTION_IMPORTED = f"{LOG_ACTION_PREFIX}.report.imported"
LOG_ACTION_TEMPLATE_APPLIED = f"{LOG_ACTION_PREFIX}.report.template_applied"

#: ``row_object -> cell value``. See :attr:`CompiledColumn.render`.
RowRenderer = Callable[[Any], Any]


# ---------------------------------------------------------------------------
# Field registry
# ---------------------------------------------------------------------------


@runtime_checkable
class FieldRegistry(Protocol):
    """Source of truth for which fields exist and what they may do.

    Implemented by ``pretix_custom_reports.registry`` (wave 1) and by
    :class:`~pretix_custom_reports.contracts.stubs.StubFieldRegistry`.

    The registry is built per ``(event, base)`` because the available fields
    differ: questions and meta properties are event specific, and a field can
    be directly usable on one base and only aggregatable on the other. Results
    are expected to be cached per event; the contract says nothing about how.
    """

    def get_fields(
        self, event: EventType, base: Union[Base, str]
    ) -> Mapping[str, ReportField]:
        """All fields usable on *base* for *event*, keyed by
        :attr:`~.fields.ReportField.key`.

        Must be deterministic in iteration order (the editor renders the field
        library from it) and must never contain a key whose namespace is
        reserved but whose ``provider`` is a plugin -- that is the duplicate-key
        rule from SPEC.md section 6: core wins, plugins are namespaced.

        Fields marked :attr:`~.fields.ReportField.deprecated` are included --
        they still have to resolve for old reports -- and the editor filters
        them out of the library.
        """
        ...

    def resolve(
        self, key: str, event: EventType, base: Union[Base, str]
    ) -> Optional[ReportField]:
        """Look up a single key, or ``None`` if it is unknown for this event/base.

        Returning ``None`` rather than raising is deliberate: an unresolvable
        key is a regular state (a renamed ``Question.identifier``, a deleted
        product, a deactivated plugin), and the importer has to offer
        "skip / abort" rather than crash (SPEC.md F9).
        """
        ...


def find_unresolved_fields(
    definition: ReportDefinition,
    registry: FieldRegistry,
    event: EventType,
) -> Tuple[FieldReference, ...]:
    """Every field reference in *definition* that the registry cannot resolve.

    Thin convenience so that the importer, the editor and the exporter perform
    the *same* second-stage check. Pure; raises nothing.
    """
    unresolved: List[FieldReference] = []
    for ref in definition.iter_field_references():
        if registry.resolve(ref.key, event, definition.base) is None:
            unresolved.append(ref)
    return tuple(unresolved)


# ---------------------------------------------------------------------------
# Compiled report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompiledColumn:
    """One output column, ready to be written.

    Produced by the query compiler; consumed by the exporter, the preview and
    the tests. Concrete dataclass rather than a protocol because everyone needs
    to *construct* these, not just accept them.
    """

    key: str
    """The registry key this column came from."""

    label: str
    """Final header text: the column's override, else the field's label,
    already coerced to ``str`` in the active language."""

    datatype: DataType
    """The field's datatype, for downstream formatting decisions."""

    render: RowRenderer
    """``row_object -> cell value``.

    Gets whatever the queryset yields for a row and returns the value that goes
    into the cell. Must not touch the database: everything it needs is either a
    plain attribute, an annotation alias or prefetched.

    Return ``None``, ``str``, ``int``, ``float``, ``Decimal``, ``date``,
    ``datetime`` or ``bool``. ``ListExporter`` neutralises formula injection in
    both CSV and XLSX on its own (docs/pretix-api-notes.md section 2), so no
    escaping belongs in here.
    """

    aggregate: Optional[Aggregate] = None
    """The aggregate applied, if any."""

    field: Optional[ReportField] = None
    """The resolved field. Optional so tests can build columns cheaply."""


@runtime_checkable
class CompiledReport(Protocol):
    """An executable report: queryset, ordered columns, renderers, row iterator.

    One instance is bound to exactly one event. Multi-event exports compile
    once per event and concatenate -- that keeps CLAUDE.md rule 4 (every
    queryset hard-limited to one event) trivially true.
    """

    definition: ReportDefinition
    """The definition this was compiled from."""

    base: Base
    """Row granularity, mirrored from the definition for convenience."""

    event: EventType
    """The event this instance is bound to."""

    columns: Sequence[CompiledColumn]
    """Output columns in output order. Hidden columns are already dropped."""

    queryset: QuerySetType
    """The prepared queryset: filtered, ordered, annotated, event-scoped.

    Exposed for tests (``assertNumQueries``, ``str(qs.query)``) and for the
    preview, which slices it. Consumers must not re-filter it -- everything the
    definition asks for is already applied.
    """

    def headers(self) -> List[str]:
        """Header row, i.e. ``[c.label for c in self.columns]``."""
        ...

    def iter_rows(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        limit: Optional[int] = None,
    ) -> Iterator[List[Any]]:
        """Yield one list of cell values per row, in column order.

        Streams: uses ``QuerySet.iterator(chunk_size=...)`` so a six-digit
        report does not materialise in memory. *limit* caps the number of rows
        and is what the preview uses
        (:data:`~.definition.PREVIEW_ROW_LIMIT`); it is applied on top of
        ``options.row_limit``, never instead of it.
        """
        ...

    def count(self) -> int:
        """Number of rows the report would produce.

        A separate ``COUNT(*)``; may be expensive. The preview shows it as the
        estimated total next to its 20 sample rows (SPEC.md F2).
        """
        ...


@runtime_checkable
class QueryCompiler(Protocol):
    """Turns a validated definition into an executable report.

    Implemented by ``pretix_custom_reports.query`` (wave 1) and by
    :class:`~pretix_custom_reports.contracts.stubs.StubQueryCompiler`.
    """

    def compile(self, definition: ReportDefinition, event: EventType) -> CompiledReport:
        """Compile *definition* for *event*.

        The implementation must, in this order:

        1. resolve every key via the registry -- raise
           :class:`~.errors.FieldResolutionError` listing *all* missing keys
        2. check what only the registry can know: base support, mandatory and
           permitted aggregates, allowed operators per field, ``sortable`` --
           raise :class:`~.errors.CompilationError`
        3. build the queryset, hard-scoped to *event*, honouring
           ``options.include_canceled_positions`` (``OrderPosition.all`` vs
           ``.objects``) and ``options.include_testmode_orders``
        4. add ``select_related``/``prefetch_related``/``annotate`` for exactly
           the fields used -- nothing more

        It must **never** read an ORM path, a lookup or an operator from the
        definition: those come from the resolved :class:`~.fields.ReportField`
        only (CLAUDE.md rule 2).

        :raises FieldResolutionError: a referenced key does not exist here.
        :raises CompilationError: the definition is resolvable but not valid
            against the resolved fields.
        """
        ...
