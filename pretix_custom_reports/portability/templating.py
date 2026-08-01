"""Organizer templates: who may load one, and what "loading" does.

Owner from wave 2 on: portability-dev (ORCHESTRIERUNG.md section 5).

SPEC.md F10 in three sentences: an organizer keeps reports as templates
(``event=None, organizer=<org>``). "Load template" in an event produces a
**copy** (``event=<event>, organizer=None, source_template=<template>``). There
is no live link in v1 -- the copy is editable and never changes again on its
own; ``source_template`` exists so a later version can say "the template has
changed since".

The event-specific parts (questions, products, variations) are translated by the
*same* resolver the file import uses
(:func:`~pretix_custom_reports.portability.resolution.resolve_definition`) and
produce the *same* report, which is the point of SPEC.md F10's "einmal
implementieren und zweimal nutzen".

Permissions
-----------

:func:`assert_template_accessible` checks **both** ends, because checking only
the source organizer is the mistake that turns a template list into a
cross-tenant read:

* the target event always needs the event-level change permission -- loading a
  template writes a report into that event,
* a template of *another* organizer additionally needs the organizer-level
  change permission on **that** organizer.

The v1 user interface only ever offers templates of the event's own organizer
(``available_templates``), so the second case is unreachable from the browser.
The check exists anyway: an API, a management command or the next wave will not
get a second reminder.
"""

from typing import Any, Optional

from django.db import transaction

from pretix_custom_reports import contracts
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.portability.errors import (
    ImportRejected,
    TemplateAccessDenied,
)
from pretix_custom_reports.portability.references import collect_references
from pretix_custom_reports.portability.resolution import (
    ResolutionOutcome,
    ResolutionStrategy,
    resolve_definition,
)

__all__ = [
    "CHANGE_PERMISSION",
    "ORGANIZER_CHANGE_PERMISSION",
    "TemplatePlan",
    "apply_template",
    "assert_template_accessible",
    "available_templates",
    "plan_template",
]

#: Event-level permission needed to write a report. Same string as
#: ``views/crud.py`` uses; taken from docs/pretix-api-notes.md section 8.1.
CHANGE_PERMISSION = "event.settings.general:write"

#: Organizer-level equivalent (docs/pretix-api-notes.md section 8.2).
ORGANIZER_CHANGE_PERMISSION = "organizer.settings.general:write"


def available_templates(event: Any):
    """Templates an event may load: those of its own organizer.

    A queryset, so a view can paginate it. Deliberately not "every organizer the
    user can see" -- a report definition names the questions and products of the
    event it was written for, and offering those across tenants by default is a
    decision nobody asked for.
    """
    return ReportDefinition.objects.templates_for_organizer(event.organizer)


def assert_template_accessible(
    template: ReportDefinition,
    event: Any,
    *,
    user: Any = None,
    request: Any = None,
) -> None:
    """Both-ends permission check for "load this template into that event".

    :raises TemplateAccessDenied: with a message that says which end failed.

    *user* may be ``None`` for programmatic callers (the event-copy receiver),
    in which case only the structural checks apply -- that path has no user and
    no request to check against.
    """
    if not template.is_template:
        raise TemplateAccessDenied("This report is not an organizer template.")
    if event is None or getattr(event, "pk", None) is None:
        raise TemplateAccessDenied("Templates can only be loaded into a saved event.")

    if user is None:
        return

    if not user.has_event_permission(
        event.organizer, event, CHANGE_PERMISSION, request=request
    ):
        raise TemplateAccessDenied("You may not create reports in this event.")

    if template.organizer_id != event.organizer_id:
        if not user.has_organizer_permission(
            template.organizer, ORGANIZER_CHANGE_PERMISSION, request=request
        ):
            raise TemplateAccessDenied("You may not use templates of that organizer.")


class TemplatePlan:
    """A template, a target event and what the resolver made of the pair.

    Same shape as
    :class:`~pretix_custom_reports.portability.importer.ImportPlan` on purpose:
    the confirmation page is the same page.
    """

    def __init__(
        self,
        template: ReportDefinition,
        event: Any,
        outcome: ResolutionOutcome,
        strategy: str,
    ) -> None:
        self.template = template
        self.event = event
        self.outcome = outcome
        self.strategy = strategy

    @property
    def report(self):
        return self.outcome.report

    @property
    def ok(self) -> bool:
        return self.outcome.ok

    @property
    def name(self) -> str:
        return str(self.template.name)

    @property
    def definition(self):
        return self.outcome.as_dict()


def plan_template(
    template: ReportDefinition,
    event: Any,
    *,
    registry: Any = None,
    strategy: str = ResolutionStrategy.ABORT,
    user: Any = None,
    request: Any = None,
) -> TemplatePlan:
    """Resolve *template* against *event* without writing anything.

    Name hints come from the template's own organizer where possible: a template
    is not bound to an event, so there is no source registry to ask. What the
    definition does carry is its keys, and the resolver matches those by
    identifier spelling -- which is the realistic template case (``tshirt-size``
    here, ``tshirt_size`` there).
    """
    assert_template_accessible(template, event, user=user, request=request)
    document = template.validated_definition()
    outcome = resolve_definition(
        document,
        event=event,
        registry=registry,
        references=_template_references(template, document, registry),
        strategy=strategy,
    )
    return TemplatePlan(
        template=template,
        event=event,
        outcome=outcome,
        strategy=ResolutionStrategy.coerce(strategy),
    )


def _template_references(
    template: ReportDefinition,
    document: contracts.ReportDefinition,
    registry: Any,
):
    """Name hints for a template, if any event of the organizer can supply them.

    A template has no event, so ``ReportField.label`` is not available for its
    question keys. Rather than give up on name matching, we ask the event the
    template was copied *from* -- ``source_template`` points the other way, so
    this only ever finds something for templates that were themselves created
    from an event report. Returns an empty tuple otherwise, and the resolver
    falls back to identifier matching.
    """
    try:
        source_event = None
        for instance in ReportDefinition.objects.filter(
            source_template=template, event__isnull=False
        ).select_related("event")[:1]:
            source_event = instance.event
        if source_event is None:
            return ()
        if registry is None:
            from pretix_custom_reports.registry.library import field_registry

            registry = field_registry()
        return collect_references(document, source_event, registry)
    except Exception:
        # Hints are optional by definition -- a missing scope, a deleted event
        # or a registry that refuses must not stop a template from loading. The
        # resolver then matches by identifier only, and says so in its report.
        return ()


@transaction.atomic
def apply_template(
    plan: TemplatePlan,
    *,
    user: Any = None,
    auth: Any = None,
    name: Optional[str] = None,
) -> ReportDefinition:
    """Create the event-level copy described by *plan*.

    :raises ImportRejected: the plan is not committable.

    Uses :meth:`ReportDefinition.duplicate`, the single copy path
    ``persistence-dev`` built, so the identifier rule and ``source_template``
    behave exactly as they do for a plain duplicate.
    """
    if not plan.ok:
        raise ImportRejected(
            "This template cannot be loaded into this event without a decision "
            "about the fields listed below.",
            report=plan.report,
            issues=plan.report.issues,
        )

    copy = plan.template.duplicate(
        event=plan.event,
        organizer=None,
        name=name,
        created_by=user if getattr(user, "pk", None) else None,
        source_template=plan.template,
        save=False,
    )
    definition = plan.definition
    copy.definition = definition
    copy.base = definition["base"]
    copy.save()
    copy.log_action(
        contracts.LOG_ACTION_TEMPLATE_APPLIED,
        data={
            **copy.log_data(),
            "template": {
                "identifier": plan.template.identifier,
                "name": str(plan.template.name),
                "organizer": plan.template.organizer.slug,
                "resolution": plan.report.as_dict(),
            },
        },
        user=user,
        auth=auth,
    )
    return copy
