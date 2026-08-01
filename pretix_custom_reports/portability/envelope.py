"""The exchange format: what an exported file looks like, and what we accept.

Owner from wave 2 on: portability-dev (ORCHESTRIERUNG.md section 5).

The envelope itself is frozen in ``contracts/definition.py``
(:class:`~pretix_custom_reports.contracts.PortableReport`,
:func:`~pretix_custom_reports.contracts.validate_portable_document`) and the
golden file is ``tests/fixtures/definitions/portable/report_export.json``. This
module fills it in, reads it back, and owns the one part the contract left to us:
the free-form ``meta`` object.

::

    {
      "schema_version": 1,
      "name":        "Attendee list",
      "description": "...",
      "exported_at": "2026-07-30T09:12:00+02:00",
      "generator":   "pretix-custom-reports 0.1.0",
      "source":      "dummy/dummy",
      "meta": {
        "pretix_version": "2026.6.0",
        "base":           "orderposition",
        "identifier":     "K7M2XQTP",
        "created_by":     "someone@example.org",
        "references":     [{"key": "answer.tshirt-size", "label": "Shirt size", ...}]
      },
      "definition": { ... }
    }

Two rules for the file format
-----------------------------

1. **No primary keys, anywhere** (SPEC.md F9). Not for the report, not for
   questions, products or variations. ``source`` carries slugs, ``meta`` carries
   identifiers and names. A test walks a generated file and fails on any member
   that looks like an object id.
2. **Nothing executable and nothing the query layer reads.** The definition is
   written by :meth:`contracts.ReportDefinition.as_dict`, which can only emit
   registry keys and closed enums. ``meta`` is descriptive only: the importer
   uses it for name matching and for nothing else.

Both directions accept a bare definition as well as an envelope, because the
editor's JSON panel shows a bare definition and people paste what they see.
"""

from typing import Any, Dict, Mapping, Optional, Tuple

import datetime
from dataclasses import dataclass, field as dataclass_field

from pretix_custom_reports import __version__, contracts
from pretix_custom_reports.portability.references import (
    Reference,
    collect_references,
    parse_references,
)

__all__ = [
    "GENERATOR",
    "ParsedDocument",
    "build_export_document",
    "export_filename",
    "parse_document",
]

#: Written into ``generator``. Purely informational -- the importer never
#: branches on it, because a hostile file would simply lie.
GENERATOR = f"pretix-custom-reports {__version__}"

#: Longest description we copy out of an imported file.
MAX_DESCRIPTION_CHARS = 5_000


def _pretix_version() -> str:
    try:
        import pretix

        return str(pretix.__version__)
    except Exception:  # pragma: no cover - pretix is always importable here
        return ""


def _source_slug(report: Any) -> str:
    """``organizer/event`` for an event report, ``organizer`` for a template."""
    if report.event_id:
        return f"{report.event.organizer.slug}/{report.event.slug}"
    if report.organizer_id:
        return str(report.organizer.slug)
    return ""  # pragma: no cover - forbidden by the XOR check constraint


def build_export_document(
    report: Any,
    *,
    event: Any = None,
    registry: Any = None,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Serialise a stored :class:`~pretix_custom_reports.models.ReportDefinition`.

    :param event: event whose registry describes the event-specific keys.
        Defaults to the report's own event; ``None`` for an organizer template,
        which simply means the file carries no name hints.
    :param registry: field registry, defaults to the real one.

    Metadata is exactly what SPEC.md F9 asks for: name, description, base,
    created by, created at, pretix version -- plus the schema version and the
    definition itself.
    """
    document = report.validated_definition()

    if event is None:
        event = report.event if report.event_id else None
    references: Tuple[Reference, ...] = ()
    if event is not None:
        if registry is None:
            from pretix_custom_reports.registry.library import field_registry

            registry = field_registry()
        references = collect_references(document, event, registry)

    meta: Dict[str, Any] = {
        "pretix_version": _pretix_version(),
        "base": document.base.value,
        "identifier": report.identifier,
    }
    if report.created_by_id and report.created_by:
        meta["created_by"] = report.created_by.email
    if report.created_at:
        meta["created_at"] = report.created_at.isoformat()
    if references:
        meta["references"] = [ref.as_dict() for ref in references]

    exported_at = now or datetime.datetime.now(datetime.timezone.utc)

    portable = contracts.PortableReport(
        name=str(report.name),
        definition=document,
        description=str(report.description or "") or None,
        schema_version=document.schema_version,
        exported_at=exported_at.isoformat(),
        generator=GENERATOR,
        source=_source_slug(report),
        meta=meta,
    )
    return portable.as_dict()


def export_filename(report: Any) -> str:
    """A file name that survives every operating system.

    Only ASCII letters, digits, dash and underscore -- the report name is user
    input and would otherwise end up in a ``Content-Disposition`` header.
    """
    stem = "".join(
        char if (char.isascii() and (char.isalnum() or char in "-_")) else "-"
        for char in str(report.name or "report")
    ).strip("-")
    stem = stem[:60] or "report"
    identifier = "".join(
        char
        for char in str(report.identifier or "")
        if char.isascii() and char.isalnum()
    )
    suffix = f"_{identifier}" if identifier else ""
    return f"report_{stem}{suffix}.json"


@dataclass(frozen=True)
class ParsedDocument:
    """An imported file after structural validation, before any registry work."""

    definition: contracts.ReportDefinition
    name: str = ""
    description: str = ""
    references: Tuple[Reference, ...] = ()
    identifier: str = ""
    source: str = ""
    generator: str = ""
    exported_at: str = ""
    was_envelope: bool = False
    meta: Mapping[str, Any] = dataclass_field(default_factory=dict)

    @property
    def base(self) -> contracts.Base:
        return self.definition.base


def _clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    # Control characters have no business in a name that ends up in HTML, a
    # log entry and a file name.
    cleaned = "".join(char for char in value if char.isprintable() or char == "\n")
    return cleaned.strip()[:limit]


def parse_document(data: Mapping[str, Any]) -> ParsedDocument:
    """Validate a parsed JSON object as an envelope or as a bare definition.

    :raises contracts.DefinitionValidationError: with every structural problem
        found, paths prefixed ``definition.`` for the envelope case.

    The distinction is made on the presence of a ``definition`` member, not on
    heuristics: an envelope has one, a definition never does (``unknown_key``
    would reject it).
    """
    if not isinstance(data, Mapping):  # pragma: no cover - payload guarantees this
        raise contracts.DefinitionValidationError(
            [
                contracts.ValidationIssue(
                    path="",
                    code=contracts.ErrorCode.WRONG_TYPE,
                    message="A report file must be a JSON object.",
                )
            ]
        )

    if "definition" in data:
        portable = contracts.validate_portable_document(data)
        meta = portable.meta if isinstance(portable.meta, Mapping) else {}
        identifier = meta.get("identifier")
        if isinstance(identifier, str):
            try:
                contracts.validate_identifier(identifier)
            except ValueError:
                identifier = ""
        else:
            identifier = ""
        return ParsedDocument(
            definition=portable.definition,
            name=_clean_text(portable.name, contracts.MAX_LABEL_LENGTH),
            description=_clean_text(portable.description, MAX_DESCRIPTION_CHARS),
            references=parse_references(
                meta.get("references"),
                allowed_keys=portable.definition.field_keys(),
            ),
            identifier=identifier,
            source=_clean_text(portable.source, 200),
            generator=_clean_text(portable.generator, 200),
            exported_at=_clean_text(portable.exported_at, 60),
            was_envelope=True,
            meta=dict(meta),
        )

    definition = contracts.validate_definition(data)
    return ParsedDocument(definition=definition, was_envelope=False)
