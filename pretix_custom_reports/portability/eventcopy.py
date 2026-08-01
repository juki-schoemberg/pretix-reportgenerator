"""Carrying reports along when an event is copied.

Owner from wave 2 on: portability-dev (ORCHESTRIERUNG.md section 5).

The signal
----------

``event_copy_data`` (docs/pretix-api-notes.md section 3.3, verified verbatim
against ``pretix/base/signals.py:900-916``):

* ``sender`` is the **new** event, ``other`` the one being copied from,
* the ``*_map`` arguments map **old primary key -> new object**, and the note in
  section 3.3 matters here: ``question_map[old_pk]`` is the *new* ``Question``
  object, whose ``identifier`` is unchanged. Which is exactly why our keys need
  no translation in the normal case -- they are built from identifiers, never
  from primary keys.

The receiver registration itself lives in ``signals.py``, which belongs to the
integrator (ORCHESTRIERUNG.md section 5). The copy-ready lines are in
``handoff/requests/portability-dev-an-integrator-signals.md``; this module holds
the logic so that the integrator's file stays a two-line wiring job and so that
the behaviour is testable without the signal.

Why the strategy is ``KEEP``
----------------------------

An event copy is not an import decision -- nobody is standing in front of a
confirmation page, and the user asked to copy an event, not to review reports.
So:

* references that *can* be mapped are mapped (a question that was renamed in the
  target after the copy, a differently spelled identifier),
* references that cannot are **kept as they are**. A stored definition with an
  unresolvable key is an explicitly legal state (models.py, SPEC.md F9); the
  editor shows it and the exporter fails cleanly. Dropping columns silently
  during an event copy, or skipping the report altogether, would be the two
  worse options,
* the resolution report goes into the log entry of the new report, so "why does
  this copied report warn about a field" has an answer.

Nothing here writes to ``other``. Every queryset is bound to one event.
"""

from typing import Any, List, Mapping, Optional, Tuple

from dataclasses import dataclass
from django_scopes import scopes_disabled

from pretix_custom_reports import contracts
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.portability.resolution import (
    ResolutionReport,
    ResolutionStrategy,
    resolve_definition,
)

__all__ = ["CopiedReport", "CopyResult", "copy_reports_to_event"]


@dataclass(frozen=True)
class CopiedReport:
    """One report that made it into the new event."""

    source_identifier: str
    report: ReportDefinition
    resolution: Optional[ResolutionReport] = None


@dataclass(frozen=True)
class CopyResult:
    """What :func:`copy_reports_to_event` did, for tests and for logging."""

    copied: Tuple[CopiedReport, ...] = ()
    failed: Tuple[Tuple[str, str], ...] = ()
    """``(identifier, reason)`` for reports that could not be copied at all."""

    @property
    def count(self) -> int:
        return len(self.copied)


def copy_reports_to_event(
    target_event: Any,
    source_event: Any,
    *,
    question_map: Optional[Mapping[Any, Any]] = None,
    registry: Any = None,
    user: Any = None,
) -> CopyResult:
    """Copy every report of *source_event* into *target_event*.

    :param question_map: the map ``event_copy_data`` provides. Only used for the
        log entry: our field keys carry ``Question.identifier``, and the copy
        preserves identifiers, so there is nothing to translate through primary
        keys. Accepting it keeps the receiver's signature honest and gives the
        log a record of how many questions came along.
    :returns: a :class:`CopyResult`. Never raises for a single broken report --
        an event copy that dies half way through because one saved definition is
        odd would leave the new event in a state nobody can explain.
    """
    copied: List[CopiedReport] = []
    failed: List[Tuple[str, str]] = []

    # ``scopes_disabled`` and *then* a hard ``event=`` filter, not the other way
    # round. pretix can copy an event across organizers
    # (``Event.copy_data_from``, ``is_cross_organizer``), and the scope active
    # during the copy is the **target** organizer. Going through
    # ``source_event.custom_reports`` would then quietly return nothing and the
    # user would find an event without reports and no error anywhere. The filter
    # is on the source event itself, so this is not a scope hole -- same
    # reasoning as ``ReportDefinition._identifier_taken`` (models.py).
    with scopes_disabled():
        sources = list(ReportDefinition.objects.filter(event=source_event))

    for source in sources:
        identifier = source.identifier
        try:
            outcome = None
            try:
                document = source.validated_definition()
            except contracts.DefinitionValidationError:
                # A row written around save() (raw SQL, a data migration). Copy
                # it verbatim; refusing would lose it.
                document = None
            if document is not None:
                outcome = resolve_definition(
                    document,
                    event=target_event,
                    registry=registry,
                    strategy=ResolutionStrategy.KEEP,
                )
            copy = source.duplicate(
                event=target_event,
                organizer=None,
                created_by=user if getattr(user, "pk", None) else None,
                save=False,
            )
            if outcome is not None and outcome.document is not None:
                copy.definition = outcome.as_dict()
            copy.save()
        except Exception as e:  # pragma: no cover - defensive
            failed.append((identifier, str(e)))
            continue

        resolution = outcome.report if outcome is not None else None
        copy.log_action(
            contracts.LOG_ACTION_ADDED,
            data={
                **copy.log_data(),
                "copied_from_event": {
                    "organizer": source_event.organizer.slug,
                    "event": source_event.slug,
                    "identifier": identifier,
                    "questions_mapped": len(question_map or {}),
                    "resolution": resolution.as_dict() if resolution else None,
                },
            },
            user=user,
        )
        copied.append(
            CopiedReport(
                source_identifier=identifier,
                report=copy,
                resolution=resolution,
            )
        )

    return CopyResult(copied=tuple(copied), failed=tuple(failed))
