"""Exception hierarchy shared by every layer of the plugin.

Owner: contract-architect (wave 0c). Frozen -- see ``contracts/__init__.py``.

The hierarchy exists so that the exporter can do the one thing pretix does not
do for us: turn a broken saved report into a clean ``ExportError`` instead of
letting a ``DoesNotExist`` bubble into the generic exception handler.

Background (docs/pretix-api-notes.md section 5.6, case B): ``export_form_data``
is *not* revalidated when a scheduled export runs. If the referenced object is
gone, pretix retries the Celery task five times at 120 s and then mails the
user the text "Internal Error". Catching :class:`ContractError` (plus the
"report not found" case) and re-raising ``pretix.base.services.export.ExportError``
turns that into an immediate, readable failure mail.

Recommended shape for wave 2 (``exporters.py``)::

    from pretix.base.services.export import ExportError
    from pretix_custom_reports.contracts import ContractError

    try:
        definition = load_report(...)          # raises ReportNotFoundError
        compiled = compiler.compile(definition, event)
    except ContractError as e:
        raise ExportError(str(e))
"""

from typing import TYPE_CHECKING, Any, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pretix_custom_reports.contracts.definition import ValidationIssue

__all__ = [
    "ContractError",
    "DefinitionValidationError",
    "FieldContractError",
    "FieldResolutionError",
    "ReportNotFoundError",
    "CompilationError",
]


class ContractError(Exception):
    """Base class for every error raised by the contracts and their consumers.

    Anything a saved or imported report definition can do wrong ends up as a
    subclass of this. Catch this one in the exporter.
    """


class DefinitionValidationError(ContractError):
    """The definition JSON does not match the structural schema.

    Raised by :func:`pretix_custom_reports.contracts.definition.validate_definition`.
    Carries *all* issues found, not just the first one, so the editor can show
    them at once.
    """

    def __init__(self, issues: Sequence["ValidationIssue"]) -> None:
        self.issues: Tuple["ValidationIssue", ...] = tuple(issues)
        if not self.issues:  # pragma: no cover - defensive
            super().__init__("Invalid report definition.")
            return
        super().__init__(
            "Invalid report definition ({} issue(s)): {}".format(
                len(self.issues),
                "; ".join(f"{i.path}: {i.message}" for i in self.issues[:10]),
            )
        )

    @property
    def codes(self) -> Tuple[str, ...]:
        """All error codes, in order of appearance. Stable across translations."""
        return tuple(i.code for i in self.issues)


class FieldContractError(ContractError):
    """A :class:`~pretix_custom_reports.contracts.fields.ReportField` is malformed.

    Raised while *building* the registry, i.e. it is a programming error in our
    code or in a third-party plugin -- never something a user can trigger.
    """


class FieldResolutionError(ContractError):
    """A referenced field key does not exist in the registry for this event/base.

    This is a regular, expected state, not a bug: question identifiers may be
    renamed, products deleted, plugins deactivated (docs/pretix-api-notes.md
    section 6.4). The importer offers "skip / abort", the exporter fails cleanly.
    """

    def __init__(
        self,
        keys: Sequence[str],
        base: Optional[Any] = None,
        message: Optional[str] = None,
    ) -> None:
        self.keys: Tuple[str, ...] = tuple(keys)
        self.base = base
        super().__init__(
            message
            or "Unknown or unavailable report field(s): {}".format(", ".join(self.keys))
        )


class ReportNotFoundError(ContractError):
    """The stored report definition a schedule or exporter refers to is gone.

    Deliberately its own class: it is the single most likely failure of a
    scheduled export and deserves its own message in the failure mail.
    """


class CompilationError(ContractError):
    """The definition is structurally valid and resolvable but cannot be compiled.

    Examples: a position field used on base ``order`` without an aggregate, a
    sort on a field the registry marks as not sortable, an aggregate the field
    does not support.
    """
