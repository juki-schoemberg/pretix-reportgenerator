"""The public entry point: definition in, executable report out.

Owner from wave 1 on: query-dev (ORCHESTRIERUNG.md section 5).

Usage from wave 2 onwards::

    from pretix_custom_reports.query.compiler import ReportQueryCompiler

    compiler = ReportQueryCompiler(registry)
    report = compiler.compile(definition, event)

    for row in report.iter_rows():
        ...

and for the editor preview::

    report = compiler.compile(definition, event, preview=True)
    rows, total = report.rows(), report.count()

:class:`ReportQueryCompiler` satisfies the
:class:`~pretix_custom_reports.contracts.protocols.QueryCompiler` protocol. The
extra keyword arguments (``preview``, ``now``) have defaults, so a consumer typed
against the protocol keeps working.

The registry is a required argument
----------------------------------

There is no default and no lazy fallback. Two reasons:

* the registry is the *only* source of ORM paths, lookups and operators
  (CLAUDE.md rule 2), so which registry is in play is exactly the thing a reader
  of the calling code should be able to see, and
* docs/adr/0001-contracts.md section 6 asks that a stub in the production path be
  visible in the diff. A default that quietly resolved to something would defeat
  that.

Errors, and what the exporter does with them
--------------------------------------------

Everything raised here is a
:class:`~pretix_custom_reports.contracts.errors.ContractError`:

* :class:`~pretix_custom_reports.contracts.errors.FieldResolutionError` -- a key
  does not exist for this event and base. Regular and expected: question
  identifiers get renamed, products get deleted, plugins get switched off
  (ADR 0001 section 3.2). Fatal when *running* a report, not when loading it.
* :class:`~pretix_custom_reports.contracts.errors.CompilationError` -- everything
  resolves, but the definition asks for something the fields do not allow.
* :class:`~pretix_custom_reports.contracts.errors.FieldContractError` -- the
  registry itself is malformed. A bug in our code or in a third-party plugin, not
  something a user can trigger.

``exporters.py`` catches ``ContractError`` and re-raises pretix'
``ExportError``, which turns a broken saved report into an immediate readable
failure mail instead of five Celery retries and the words "Internal Error"
(docs/pretix-api-notes.md section 5.6, ADR 0001 section 5.2).
"""

from typing import Any, Optional

import datetime as dt

from pretix_custom_reports.contracts.definition import ReportDefinition
from pretix_custom_reports.contracts.protocols import FieldRegistry
from pretix_custom_reports.query.plan import QueryPlan, build_plan
from pretix_custom_reports.query.report import CompiledReport, build_report

__all__ = [
    "CompiledReport",
    "QueryPlan",
    "ReportQueryCompiler",
    "compile_report",
    "plan_report",
]


class ReportQueryCompiler:
    """Turns a validated :class:`ReportDefinition` into a runnable report."""

    def __init__(self, registry: FieldRegistry) -> None:
        """:param registry: the field registry to resolve every key through."""
        self.registry = registry

    def compile(
        self,
        definition: ReportDefinition,
        event: Any,
        preview: bool = False,
        now: Optional[dt.datetime] = None,
    ) -> CompiledReport:
        """Compile *definition* for *event*.

        :param preview: build the cheap, hard-limited preview variant
            (:data:`~pretix_custom_reports.contracts.definition.PREVIEW_ROW_LIMIT`
            rows) instead of the full report.
        :param now: reference instant for relative date filters, resolved in the
            *event's* timezone. Defaults to the current time; injectable so a test
            can pin "today" without freezing the process clock.
        :raises FieldResolutionError: a referenced key does not exist here.
        :raises CompilationError: resolvable, but not valid against the fields.
        """
        plan = self.plan(definition, event, now=now)
        return build_report(plan, preview=preview)

    def plan(
        self,
        definition: ReportDefinition,
        event: Any,
        now: Optional[dt.datetime] = None,
    ) -> QueryPlan:
        """Run only pass one: resolve, validate, plan. Builds no queryset.

        Useful for the editor, which wants to know whether a draft is valid
        against the registry without running anything, and for tests that want to
        assert on the plan rather than on generated SQL.
        """
        return build_plan(definition, event, self.registry, now=now)


def compile_report(
    definition: ReportDefinition,
    event: Any,
    registry: FieldRegistry,
    preview: bool = False,
    now: Optional[dt.datetime] = None,
) -> CompiledReport:
    """Function form of :meth:`ReportQueryCompiler.compile`."""
    return ReportQueryCompiler(registry).compile(
        definition, event, preview=preview, now=now
    )


def plan_report(
    definition: ReportDefinition,
    event: Any,
    registry: FieldRegistry,
    now: Optional[dt.datetime] = None,
) -> QueryPlan:
    """Function form of :meth:`ReportQueryCompiler.plan`."""
    return ReportQueryCompiler(registry).plan(definition, event, now=now)
