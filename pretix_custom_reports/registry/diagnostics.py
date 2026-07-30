"""What the registry decided not to offer, and why.

Owner: registry-dev.

A registry that silently drops things is impossible to support. Three situations
produce a field that *could* have existed but does not:

* a ``Question.identifier`` or ``EventMetaProperty.name`` that cannot be
  expressed as a key -- the double underscore ban from ADR 0001 section 2 is the
  realistic case,
* two questions whose identifiers collapse onto the same key or the same
  annotation alias,
* a third-party plugin returning something the registry refuses (reserved
  namespace, wrong provider, duplicate key, wrong base, or an exception).

All of them end up here instead of in a log line nobody reads. The debug view
(SPEC.md phase P2) and the editor can show them, and the tests assert on them.
"""

from typing import Any, Tuple

from dataclasses import dataclass

__all__ = [
    "REASON_AMBIGUOUS_KEY",
    "REASON_DUPLICATE_ALIAS",
    "REASON_DUPLICATE_KEY",
    "REASON_INVALID_KEY",
    "REASON_NOT_A_FIELD",
    "REASON_RECEIVER_FAILED",
    "REASON_RESERVED_NAMESPACE",
    "REASON_UNSUPPORTED_BASE",
    "REASON_WRONG_PROVIDER",
    "SOURCE_META",
    "SOURCE_PLUGIN",
    "SOURCE_QUESTION",
    "RegistryDiagnostics",
    "SkippedField",
]

SOURCE_QUESTION = "question"
SOURCE_META = "meta"
SOURCE_PLUGIN = "plugin"

#: The key would not pass ``contracts.validate_key`` -- almost always a double
#: underscore in a ``Question.identifier``.
REASON_INVALID_KEY = "invalid_key"

#: Two questions map onto the same key. Possible because our answer lookup is
#: case-insensitive (ADR 0001 section 3.2) while the database constraint on
#: ``(event, identifier)`` is not.
REASON_AMBIGUOUS_KEY = "ambiguous_key"

#: Two identifiers collapse onto the same annotation alias.
REASON_DUPLICATE_ALIAS = "duplicate_alias"

#: A plugin returned a key that already exists. Core wins, first plugin wins.
REASON_DUPLICATE_KEY = "duplicate_key"

#: A plugin returned a key in one of the reserved core namespaces.
REASON_RESERVED_NAMESPACE = "reserved_namespace"

#: A plugin field's ``provider`` does not match the app label in its key.
REASON_WRONG_PROVIDER = "wrong_provider"

#: A plugin returned something that is not a ``ReportField``.
REASON_NOT_A_FIELD = "not_a_field"

#: A plugin's signal receiver raised.
REASON_RECEIVER_FAILED = "receiver_failed"

#: A plugin field does not declare the base it was asked for.
REASON_UNSUPPORTED_BASE = "unsupported_base"


@dataclass(frozen=True)
class SkippedField:
    """One field that was not published, with a machine-readable reason."""

    key: str
    """The key that would have been used. May itself be invalid."""

    source: str
    """:data:`SOURCE_QUESTION`, :data:`SOURCE_META` or :data:`SOURCE_PLUGIN`."""

    reason: str
    """One of the ``REASON_*`` constants. Stable across translations."""

    detail: str = ""
    """Free text for the human reading the debug view."""

    def __str__(self) -> str:
        suffix = f" ({self.detail})" if self.detail else ""
        return f"{self.key} [{self.source}/{self.reason}]{suffix}"


@dataclass(frozen=True)
class RegistryDiagnostics:
    """Everything the registry wants to say about one ``(event, base)`` build."""

    base: Any
    field_count: int
    skipped: Tuple[SkippedField, ...] = ()
    providers: Tuple[str, ...] = ()
    """Every distinct ``ReportField.provider``, ``"core"`` first."""

    def by_source(self, source: str) -> Tuple[SkippedField, ...]:
        """Skipped entries from one source."""
        return tuple(entry for entry in self.skipped if entry.source == source)

    def by_reason(self, reason: str) -> Tuple[SkippedField, ...]:
        """Skipped entries with one reason."""
        return tuple(entry for entry in self.skipped if entry.reason == reason)
