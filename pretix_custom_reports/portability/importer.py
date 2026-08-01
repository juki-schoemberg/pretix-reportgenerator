"""Import: bytes in, a resolution report out, and only then a database row.

Owner from wave 2 on: portability-dev (ORCHESTRIERUNG.md section 5).

The pipeline, in the order it runs, because the order *is* the security design::

    raw bytes / pasted text
      -> payload.load_json_object()     size, depth, node count, JSON only
      -> envelope.parse_document()      structure, closed enums, no ORM paths
      -> resolution.resolve_definition() every key against the TARGET registry
      -> ImportPlan                     shown to the user, nothing written yet
      -> commit_import()                one row, one log entry

Nothing after step two ever looks at the file again. The definition that gets
stored is rebuilt from registry keys, canonicalised by the contracts and
validated a second time by :meth:`ReportDefinition.save`.

Two-step by design
------------------

:func:`plan_import` never writes. The view shows the plan, the user picks
"skip the missing fields" or "cancel", and the confirmation *re-runs the whole
pipeline from the original bytes* -- see ``views/portability.py``. The browser
never sends back a resolved definition, because a resolved definition coming
from a browser is just another untrusted document with a friendlier name.
"""

from typing import Any, Dict, Optional

from dataclasses import dataclass
from django.db import transaction

from pretix_custom_reports import contracts
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.portability.envelope import ParsedDocument, parse_document
from pretix_custom_reports.portability.errors import ImportRejected
from pretix_custom_reports.portability.payload import load_json_object
from pretix_custom_reports.portability.resolution import (
    ResolutionOutcome,
    ResolutionReport,
    ResolutionStrategy,
    resolve_definition,
)

__all__ = ["ImportPlan", "commit_import", "plan_import"]

#: Fallback name for a document that carries none (a pasted bare definition).
DEFAULT_NAME = "Imported report"


@dataclass(frozen=True)
class ImportPlan:
    """Everything the confirmation page needs, and nothing that is stored yet."""

    source: ParsedDocument
    outcome: ResolutionOutcome
    event: Any
    strategy: str

    @property
    def report(self) -> ResolutionReport:
        return self.outcome.report

    @property
    def ok(self) -> bool:
        """True if :func:`commit_import` would succeed right now."""
        return self.outcome.ok

    @property
    def name(self) -> str:
        return self.source.name or DEFAULT_NAME

    @property
    def definition(self) -> Optional[Dict[str, Any]]:
        return self.outcome.as_dict()


def plan_import(
    raw: Any,
    *,
    event: Any,
    registry: Any = None,
    strategy: str = ResolutionStrategy.ABORT,
) -> ImportPlan:
    """Run the whole read path without writing anything.

    :param raw: the uploaded bytes, the pasted text, or an already parsed
        :class:`~pretix_custom_reports.portability.envelope.ParsedDocument`
        (used when the same document is resolved twice with different
        strategies).
    :param event: the target event. Its registry is the allow-list.
    :raises PayloadRejected: the bytes are not an acceptable JSON document.
    :raises contracts.DefinitionValidationError: the document is not a valid
        report definition. Both are refusals of the *file*, so they are
        exceptions; everything the file may legitimately get wrong about *this
        event* ends up in the plan's report instead.
    """
    source = (
        raw
        if isinstance(raw, ParsedDocument)
        else parse_document(load_json_object(raw))
    )
    outcome = resolve_definition(
        source.definition,
        event=event,
        registry=registry,
        references=source.references,
        strategy=strategy,
    )
    return ImportPlan(
        source=source,
        outcome=outcome,
        event=event,
        strategy=ResolutionStrategy.coerce(strategy),
    )


@transaction.atomic
def commit_import(
    plan: ImportPlan,
    *,
    user: Any = None,
    auth: Any = None,
    name: Optional[str] = None,
) -> ReportDefinition:
    """Store the resolved definition as a new report of the plan's event.

    :raises ImportRejected: the plan is not committable -- unresolved
        references under the ``abort`` strategy, or a document that the registry
        refuses. The exception carries the report so the caller can show it.

    Always creates, never updates: an import that could overwrite an existing
    report would turn "have a look at this file" into a destructive operation.
    """
    if not plan.ok:
        raise ImportRejected(
            "This report cannot be imported into this event without a decision "
            "about the fields listed below.",
            report=plan.report,
            issues=plan.report.issues,
        )

    definition = plan.definition
    assert definition is not None  # guaranteed by plan.ok

    report = ReportDefinition(
        event=plan.event,
        organizer=None,
        name=(name or plan.name)[: _name_limit()],
        description=plan.source.description,
        identifier=plan.source.identifier,
        base=definition["base"],
        definition=definition,
        created_by=user if _is_user(user) else None,
    )
    # The identifier travels with the report so a scheduled export keeps
    # working, and only a real collision in this event changes it
    # (ADR 0001 section 5.1).
    report.ensure_unique_identifier()
    report.save()
    report.log_action(
        contracts.LOG_ACTION_IMPORTED,
        data={
            **report.log_data(),
            "import": {
                "source": plan.source.source,
                "generator": plan.source.generator,
                "exported_at": plan.source.exported_at,
                "was_envelope": plan.source.was_envelope,
                "resolution": plan.report.as_dict(),
            },
        },
        user=user,
        auth=auth,
    )
    return report


def _name_limit() -> int:
    return ReportDefinition._meta.get_field("name").max_length


def _is_user(user: Any) -> bool:
    """``request.user`` is an ``AnonymousUser`` when nobody is logged in."""
    return bool(user is not None and getattr(user, "pk", None))
