# Owner from wave 1 on: persistence-dev (see ORCHESTRIERUNG.md section 5)
"""Storage for report definitions.

One model, :class:`ReportDefinition`. It stores the definition JSON that
``contracts/`` describes and nothing else -- this module never interprets a
field key, never builds a lookup and never talks to the registry or the query
compiler (CLAUDE.md rule 2).

Three things in here are load bearing and easy to break, so they are spelled out
where they happen:

1. **Structural validation on every write.** :meth:`ReportDefinition.save`
   refuses to store a definition that ``contracts.validate_definition`` rejects.
   Structure only: an unresolvable field key (a renamed question, a disabled
   plugin) is a legal stored state and is checked at import/editor/execution
   time, not here (docs/adr/0001-contracts.md section 4, SPEC.md F9).
2. **event XOR organizer**, enforced by a database check constraint, not only in
   Python: ``event`` set = report of that event, ``organizer`` set = template of
   that organizer (SPEC.md F10).
3. **django-scopes.** ``Event`` and everything below it is organizer scoped.
   Because of the XOR the organizer is reachable via two different paths, so the
   stock ``ScopedManager`` (one path per dimension) does not fit; see
   :class:`ReportDefinitionManager`.

Naming collision worth knowing about: ``contracts.ReportDefinition`` is the
frozen *document* dataclass, ``models.ReportDefinition`` is the *database row*
that carries such a document. Import the modules, not the names, if you need
both:

    from pretix_custom_reports import contracts
    from pretix_custom_reports.models import ReportDefinition
"""

from typing import Any, Dict, Iterable, List, Optional, Set

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils.crypto import get_random_string
from django.utils.translation import gettext_lazy as _
from django_scopes import get_scope, scopes_disabled
from django_scopes.manager import DisabledQuerySet
from pretix.base.models.base import LoggedModel
from pretix.helpers.models import modelcopy

from pretix_custom_reports import contracts

__all__ = [
    "IDENTIFIER_CHARSET",
    "IDENTIFIER_LENGTH",
    "ReportDefinition",
    "ReportDefinitionManager",
    "ReportDefinitionQuerySet",
]


#: Character set for generated identifiers. Same as pretix uses for
#: ``Question.identifier`` (pretix/base/models/items.py:1800-1812): no ``I``,
#: ``O``, ``0``, ``1``, ``2``, ``5``, ``6`` -- nothing that can be misread when
#: someone copies an identifier out of a mail into a scheduled export.
IDENTIFIER_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ3789"

#: Length of a generated identifier, as in pretix.
IDENTIFIER_LENGTH = 8

#: How many ``-2``, ``-3`` ... suffixes :meth:`ReportDefinition.ensure_unique_identifier`
#: tries before it gives up and generates a random identifier instead.
MAX_IDENTIFIER_SUFFIX = 100


# ---------------------------------------------------------------------------
# Queryset and manager
# ---------------------------------------------------------------------------


class ReportDefinitionQuerySet(models.QuerySet):
    """The two questions every caller actually asks, plus their negatives."""

    def for_event(self, event) -> "ReportDefinitionQuerySet":
        """Reports of exactly this event. Never templates."""
        return self.filter(event=event)

    def event_reports(self) -> "ReportDefinitionQuerySet":
        """Everything that belongs to an event rather than to an organizer."""
        return self.filter(event__isnull=False)

    def templates(self) -> "ReportDefinitionQuerySet":
        """Organizer-level templates only (SPEC.md F10)."""
        return self.filter(event__isnull=True)

    def templates_for_organizer(self, organizer) -> "ReportDefinitionQuerySet":
        """Templates of exactly this organizer."""
        return self.filter(organizer=organizer, event__isnull=True)

    def by_identifier(self, identifier: str) -> "ReportDefinitionQuerySet":
        """Filter on the stable identifier.

        Always chain this onto :meth:`for_event` or
        :meth:`templates_for_organizer`. A global lookup by identifier would be
        a cross-organizer leak, because identifiers are only unique per event
        respectively per organizer (docs/adr/0001-contracts.md section 5.1).
        """
        return self.filter(identifier=identifier)


class ReportDefinitionManager(
    models.Manager.from_queryset(ReportDefinitionQuerySet)  # type: ignore[misc]
):
    """Organizer-scoped manager that copes with the event XOR organizer split.

    ``django_scopes.ScopedManager(organizer='...')`` maps one scope dimension to
    exactly one ORM path. Our organizer is reachable as ``event__organizer``
    for event reports and as ``organizer`` for templates, and every row has
    exactly one of the two (the check constraint guarantees it). A single path
    would therefore silently hide half the table -- with
    ``organizer='event__organizer'`` no template would ever be visible.

    Everything else is deliberately identical to ``ScopedManager``, including
    returning django-scopes' own ``DisabledQuerySet`` when no scope is active
    (so the failure mode is the familiar ``ScopeError``, not an empty result).
    """

    #: Scope dimensions this manager requires, as in ``ScopedManager``.
    required_scopes = frozenset({"organizer"})

    def get_queryset(self):
        current_scope = get_scope()
        if not current_scope.get("_enabled", True):
            return super().get_queryset()
        missing_scopes = self.required_scopes - set(current_scope.keys())
        if missing_scopes:
            return DisabledQuerySet(
                self.model, using=self._db, missing_scopes=missing_scopes
            )
        value = current_scope["organizer"]
        if value is None:
            return super().get_queryset()
        if isinstance(value, (list, tuple)):
            condition = Q(event__organizer__in=value) | Q(organizer__in=value)
        else:
            condition = Q(event__organizer=value) | Q(organizer=value)
        return super().get_queryset().filter(condition)

    def all(self):
        # Mirrors ScopedManager.all(): turn the lazy DisabledQuerySet into an
        # immediate ScopeError, which is far easier to debug.
        qs = super().all()
        if isinstance(qs, DisabledQuerySet):
            qs = qs.all()
        return qs


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class ReportDefinition(LoggedModel):
    """A saved report: metadata plus the definition document as JSON.

    ``LoggedModel`` gives us :meth:`log_action`; it derives the log entry's
    event from ``self.event`` and falls back to ``self.organizer_id``
    (pretix/base/models/base.py:123-134), which is exactly the XOR we have.
    """

    BASE_CHOICES = (
        (contracts.Base.ORDER.value, _("One row per order")),
        (contracts.Base.ORDERPOSITION.value, _("One row per order position")),
    )

    id = models.BigAutoField(primary_key=True)

    event = models.ForeignKey(
        "pretixbase.Event",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="custom_reports",
        verbose_name=_("Event"),
    )
    organizer = models.ForeignKey(
        "pretixbase.Organizer",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="custom_report_templates",
        verbose_name=_("Organizer"),
        help_text=_("Set instead of an event to make this report a template."),
    )

    name = models.CharField(
        max_length=190,
        verbose_name=_("Name"),
    )
    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("Description"),
    )
    identifier = models.CharField(
        max_length=contracts.IDENTIFIER_MAX_LENGTH,
        blank=True,
        verbose_name=_("Internal identifier"),
        help_text=_(
            "Stable name used to reference this report from scheduled exports. "
            "If you leave this empty, we generate one for you."
        ),
        validators=[
            RegexValidator(
                regex=contracts.IDENTIFIER_RE,
                message=_(
                    "The identifier may only contain letters, numbers, dots, "
                    "dashes, and underscores."
                ),
            ),
        ],
    )
    base = models.CharField(
        max_length=20,
        choices=BASE_CHOICES,
        verbose_name=_("Report base"),
        help_text=_("Row granularity. Must match the base inside the definition."),
    )
    definition = models.JSONField(
        default=dict,
        encoder=DjangoJSONEncoder,
        verbose_name=_("Definition"),
        help_text=_("Columns, filters, sorting and options as JSON."),
    )
    schema_version = models.PositiveSmallIntegerField(
        default=contracts.SCHEMA_VERSION,
        verbose_name=_("Schema version"),
        help_text=_(
            "Denormalised copy of the schema_version inside the definition, "
            "which stays authoritative."
        ),
    )
    source_template = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="instances",
        verbose_name=_("Created from template"),
    )

    created_by = models.ForeignKey(
        "pretixbase.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Created by"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ReportDefinitionManager()

    class Meta:
        verbose_name = _("Report")
        verbose_name_plural = _("Reports")
        ordering = ("name", "pk")
        constraints = [
            # Exactly one owner. Enforced in the database because Python-level
            # checks do not survive bulk_create, .update() or a future data
            # migration.
            models.CheckConstraint(
                condition=(
                    Q(event__isnull=False, organizer__isnull=True)
                    | Q(event__isnull=True, organizer__isnull=False)
                ),
                name="pcr_event_xor_organizer",
            ),
            # Unique per event. Rows with event IS NULL are not covered (NULLs
            # are distinct in every supported backend), which is what the second
            # constraint is for.
            models.UniqueConstraint(
                fields=["event", "identifier"],
                name="pcr_uniq_identifier_event",
            ),
            models.UniqueConstraint(
                fields=["organizer", "identifier"],
                condition=Q(event__isnull=True),
                name="pcr_uniq_identifier_orga",
            ),
        ]

    def __str__(self) -> str:
        return str(self.name)

    # -- convenience -------------------------------------------------------

    @property
    def is_template(self) -> bool:
        """True if this is an organizer-level template (SPEC.md F10)."""
        return self.event_id is None

    @property
    def owning_organizer(self):
        """The organizer this row belongs to, whichever side of the XOR is set."""
        if self.organizer_id:
            return self.organizer
        if self.event_id:
            return self.event.organizer
        return None

    def validated_definition(self) -> contracts.ReportDefinition:
        """The stored document as a validated :class:`contracts.ReportDefinition`.

        This is the intended entry point for the query compiler and the
        exporter: they get enums instead of raw strings and know the structure
        holds.

        :raises contracts.DefinitionValidationError: the stored JSON does not
            validate. Can only happen if a row was written around
            :meth:`save` (raw SQL, ``bulk_create``, ``QuerySet.update``).
        """
        return contracts.validate_definition(self.definition)

    # -- validation --------------------------------------------------------

    def _issue_messages(self, issues: Iterable[contracts.ValidationIssue]) -> List[str]:
        out = []
        for issue in issues:
            out.append(
                f"{issue.path}: {issue.message}" if issue.path else issue.message
            )
        return out

    def _normalize_definition(self) -> Set[str]:
        """Validate the definition, canonicalise it, sync denormalised columns.

        :returns: names of the fields this call changed, for ``update_fields``.
        :raises django.core.exceptions.ValidationError: keyed on the offending
            field, so a ``ModelForm`` shows it in the right place.
        """
        try:
            document = contracts.validate_definition(self.definition)
        except contracts.DefinitionValidationError as e:
            raise ValidationError({"definition": self._issue_messages(e.issues)})

        changed: Set[str] = set()

        # Exactly one representation of a given definition in the database.
        # validate_definition rejects unknown keys, so this cannot lose data.
        canonical = document.as_dict()
        if canonical != self.definition:
            self.definition = canonical
            changed.add("definition")

        if not self.base:
            self.base = document.base.value
            changed.add("base")
        elif self.base != document.base.value:
            raise ValidationError(
                {
                    "base": [
                        _(
                            "The report base does not match the base inside the "
                            "definition (%(definition_base)s)."
                        )
                        % {"definition_base": document.base.value}
                    ]
                }
            )

        if self.schema_version != document.schema_version:
            self.schema_version = document.schema_version
            changed.add("schema_version")

        return changed

    def _check_ownership(self) -> None:
        if bool(self.event_id) == bool(self.organizer_id):
            raise ValidationError(
                _(
                    "A report belongs either to an event or, as a template, to an "
                    "organizer -- never to both and never to neither."
                )
            )

    def _check_identifier(self) -> None:
        if not self.identifier:
            return
        try:
            contracts.validate_identifier(self.identifier)
        except ValueError as e:
            raise ValidationError({"identifier": [str(e)]})

    def clean(self) -> None:
        """Same checks as :meth:`save`, so ``full_clean`` reports them as field errors."""
        super().clean()
        self._check_ownership()
        self._check_identifier()
        self._normalize_definition()

    # -- identifier --------------------------------------------------------

    @scopes_disabled()
    def _identifier_taken(self, identifier: str) -> bool:
        """Is *identifier* already used inside this row's uniqueness scope?

        ``scopes_disabled`` because this runs from :meth:`save`, which may be
        called from a Celery task or an event copy without an active scope. It
        is not a scope hole: the query is always narrowed to this row's own
        event or organizer, exactly mirroring the two unique constraints.
        """
        qs = ReportDefinition.objects.filter(identifier=identifier)
        if self.event_id:
            qs = qs.filter(event_id=self.event_id)
        else:
            qs = qs.filter(organizer_id=self.organizer_id, event__isnull=True)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        return qs.exists()

    def _generate_identifier(self) -> str:
        while True:
            code = get_random_string(
                length=IDENTIFIER_LENGTH, allowed_chars=IDENTIFIER_CHARSET
            )
            if not self._identifier_taken(code):
                return code

    def ensure_unique_identifier(self) -> str:
        """Keep the current identifier if it is free, otherwise add a suffix.

        This is the rule for event copies and for instantiating an organizer
        template (docs/adr/0001-contracts.md section 5.1): the identifier
        travels with the report so that a scheduled multi-event export keeps
        working, and only a genuine collision in the target changes it.

        Call this **before** saving a copy; :meth:`save` itself never renames,
        because silently renaming an edit would hide a real conflict.
        """
        current = self.identifier
        if not current:
            self.identifier = self._generate_identifier()
            return self.identifier
        if not self._identifier_taken(current):
            return current
        for n in range(2, MAX_IDENTIFIER_SUFFIX):
            suffix = f"-{n}"
            candidate = (
                current[: contracts.IDENTIFIER_MAX_LENGTH - len(suffix)] + suffix
            )
            if not self._identifier_taken(candidate):
                self.identifier = candidate
                return candidate
        self.identifier = self._generate_identifier()
        return self.identifier

    # -- writing -----------------------------------------------------------

    def save(self, *args: Any, **kwargs: Any):
        """Validate, canonicalise, generate an identifier if needed, then store.

        The validation here is the hard gate demanded by the brief: invalid
        JSON must never reach the database, no matter which code path writes
        the row. Forms get the same errors earlier via :meth:`clean`.
        """
        self._check_ownership()
        self._check_identifier()
        changed = self._normalize_definition()

        if not self.identifier:
            self.identifier = self._generate_identifier()
            changed.add("identifier")

        if kwargs.get("update_fields"):
            kwargs["update_fields"] = set(kwargs["update_fields"]) | changed

        return super().save(*args, **kwargs)

    def duplicate(
        self,
        *,
        event=None,
        organizer=None,
        name: Optional[str] = None,
        created_by=None,
        source_template=None,
        keep_identifier: bool = True,
        save: bool = True,
    ) -> "ReportDefinition":
        """Copy this report, optionally into another event or organizer.

        Default target is this row's own scope, i.e. "duplicate in place".
        Pass exactly one of *event* or *organizer* to move the copy elsewhere
        (event copy, template instantiation).

        The identifier is preserved and only suffixed on collision unless
        *keep_identifier* is ``False``, in which case a fresh one is generated.
        """
        copy = modelcopy(self)
        copy.pk = None
        copy.created_at = None
        copy.updated_at = None
        copy.created_by = created_by

        if event is not None or organizer is not None:
            copy.event = event
            copy.organizer = organizer
        if name is not None:
            copy.name = name
        if source_template is not None:
            copy.source_template = source_template
        elif copy.event_id and self.is_template:
            # An event-level copy of a template remembers where it came from,
            # so a later version can offer "the template changed" hints
            # (SPEC.md F10).
            copy.source_template = self

        copy.identifier = self.identifier if keep_identifier else ""
        copy.ensure_unique_identifier()

        if save:
            copy.save()
        return copy

    # -- logging -----------------------------------------------------------

    def log_data(self) -> Dict[str, Any]:
        """Payload for :meth:`log_action`.

        No key contains ``password``, ``secret`` or ``api_key``: ``log_action``
        would replace the value with ``********`` **and mutate the dict in
        place** (pretix/base/models/base.py:153-163).
        """
        return {
            "name": self.name,
            "identifier": self.identifier,
            "base": self.base,
            "schema_version": self.schema_version,
            "definition": self.definition,
            "is_template": self.is_template,
            "source_template": self.source_template_id,
        }

    def _log(self, action: str, user=None, auth=None, data=None, **kwargs):
        payload = self.log_data()
        if data:
            payload.update(data)
        return self.log_action(action, data=payload, user=user, auth=auth, **kwargs)

    def log_added(self, user=None, auth=None, data=None, **kwargs):
        """Log creation (``pretix_custom_reports.report.added``)."""
        return self._log(contracts.LOG_ACTION_ADDED, user, auth, data, **kwargs)

    def log_changed(self, user=None, auth=None, data=None, **kwargs):
        """Log a change (``pretix_custom_reports.report.changed``)."""
        return self._log(contracts.LOG_ACTION_CHANGED, user, auth, data, **kwargs)

    def log_deleted(self, user=None, auth=None, data=None, **kwargs):
        """Log deletion. Call this **before** ``delete()``.

        A ``LogEntry`` stores a generic relation, so after ``delete()`` the
        primary key is gone and the entry could not be attached.
        """
        return self._log(contracts.LOG_ACTION_DELETED, user, auth, data, **kwargs)

    def log_executed(self, user=None, auth=None, data=None, **kwargs):
        """Log a run (``pretix_custom_reports.report.executed``).

        Used by the preview, the export and the exporter; pass extra context
        such as ``{"row_count": 1234, "format": "xlsx"}`` in *data*.
        """
        return self._log(contracts.LOG_ACTION_EXECUTED, user, auth, data, **kwargs)
