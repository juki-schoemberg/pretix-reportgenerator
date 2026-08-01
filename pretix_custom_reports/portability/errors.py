"""Exceptions raised while importing, exporting or instantiating definitions.

Owner from wave 2 on: portability-dev (ORCHESTRIERUNG.md section 5).

Everything here derives from :class:`~pretix_custom_reports.contracts.ContractError`,
so a caller that already catches contract errors (the exporter does, see
``contracts/errors.py``) keeps working without knowing this module.

Two of them carry structured data rather than only a message, because the views
have to *show* the reason rather than log it:

* :class:`PayloadRejected` carries a stable ``reason`` code -- the upload was
  too large, was not JSON, was nested too deeply. These are the answers to
  "why did you not even look at my file".
* :class:`ImportRejected` carries the
  :class:`~pretix_custom_reports.portability.resolution.ResolutionReport`, so the
  confirmation page can list every field that did not resolve next to the
  refusal.
"""

from typing import Any, Optional, Sequence, Tuple

from pretix_custom_reports.contracts import ContractError

__all__ = [
    "REASON_DUPLICATE_KEY",
    "REASON_EMPTY",
    "REASON_NOT_JSON",
    "REASON_NOT_UTF8",
    "REASON_NUMBER_TOO_LONG",
    "REASON_STRING_TOO_LONG",
    "REASON_TOO_DEEP",
    "REASON_TOO_LARGE",
    "REASON_TOO_MANY_NODES",
    "ImportRejected",
    "PayloadRejected",
    "PortabilityError",
    "TemplateAccessDenied",
]


class PortabilityError(ContractError):
    """Base class for every failure of the import/export layer."""


#: The upload exceeds :data:`~.payload.MAX_PAYLOAD_BYTES`.
REASON_TOO_LARGE = "too_large"

#: Nothing was uploaded or pasted.
REASON_EMPTY = "empty"

#: The bytes are not valid UTF-8.
REASON_NOT_UTF8 = "not_utf8"

#: The text is not a JSON document, or not a JSON *object*.
REASON_NOT_JSON = "not_json"

#: More nesting than :data:`~.payload.MAX_DEPTH`.
REASON_TOO_DEEP = "too_deep"

#: More values than :data:`~.payload.MAX_NODES`.
REASON_TOO_MANY_NODES = "too_many_nodes"

#: A single string exceeds :data:`~.payload.MAX_STRING_CHARS`.
REASON_STRING_TOO_LONG = "string_too_long"

#: A number literal is longer than :data:`~.payload.MAX_NUMBER_DIGITS`.
REASON_NUMBER_TOO_LONG = "number_too_long"

#: The same member appears twice in one JSON object.
REASON_DUPLICATE_KEY = "duplicate_key"


class PayloadRejected(PortabilityError):
    """The uploaded or pasted bytes were refused before they were interpreted.

    This is the outermost gate: size, encoding, JSON well-formedness, nesting
    depth, node count. Nothing here knows what a report is -- the point is that
    a hostile file never reaches code that does.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class ImportRejected(PortabilityError):
    """The document was understood but must not be stored.

    Raised by :func:`~pretix_custom_reports.portability.importer.commit_import`
    and by the template instantiation when the user asked to write something
    that does not resolve in the target event, or that the registry refuses.
    """

    def __init__(
        self,
        message: str,
        *,
        report: Optional[Any] = None,
        issues: Sequence[str] = (),
    ) -> None:
        self.report = report
        self.issues: Tuple[str, ...] = tuple(issues)
        super().__init__(message)


class TemplateAccessDenied(PortabilityError):
    """The user may not read this template or may not write to the target event.

    Deliberately not Django's ``PermissionDenied``: the callers of the
    portability layer are not all views (the event-copy receiver is not), and a
    403 page is a view decision. ``views/templates.py`` translates it.
    """
