"""Human-readable hints about the event-specific objects a definition names.

Owner from wave 2 on: portability-dev (ORCHESTRIERUNG.md section 5).

Why this exists
---------------

A field key such as ``answer.tshirt-size`` is portable in the sense that it
contains no primary key (SPEC.md F9, ADR 0001 section 3.1). It is not portable
in the sense that the target event is guaranteed to have a question with that
identifier: identifiers are typed by humans, and the same question is
``tshirt-size`` in one event and ``t_shirt_size`` in the next.

When that happens, the only thing left to match on is the *name*, and the name
is not in the key. So the exporter writes one entry per event-specific key into
``meta.references`` of the export envelope, and the resolver uses it for name
matching (:mod:`~pretix_custom_reports.portability.resolution`).

Trust model
-----------

The list in an imported file is a **hint, never data**:

* it can only ever be used to *find* a key that the target registry already
  publishes -- the resolver looks the match up in the registry's own key table,
  so a reference cannot introduce a key, a path or an operator,
* an entry whose ``key`` is not actually used by the definition is ignored,
  because it could only ever influence a field nobody references,
* a malformed entry is ignored rather than fatal: dropping a hint can at worst
  turn a "mapped" line of the resolution report into a "not found" line, which
  is the safe direction. No field key is ever hidden this way -- every key in
  the definition gets its own line whether a hint exists for it or not.
"""

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from dataclasses import dataclass

from pretix_custom_reports import contracts

__all__ = [
    "KIND_FIELD",
    "KIND_META",
    "KIND_PLUGIN",
    "KIND_QUESTION",
    "MAX_REFERENCES",
    "PORTABLE_NAMESPACES",
    "Reference",
    "collect_references",
    "is_portable_namespace",
    "parse_references",
]

KIND_QUESTION = "question"
KIND_META = "meta"
KIND_PLUGIN = "plugin"
KIND_FIELD = "field"

_KINDS = frozenset({KIND_QUESTION, KIND_META, KIND_PLUGIN, KIND_FIELD})

#: Namespaces whose keys carry a user-chosen identifier and can therefore differ
#: between two events that mean the same thing. Only keys in these namespaces
#: take part in name matching; a core key such as ``order.code`` either exists or
#: does not, and "almost matching" one would be a typo, not a rename.
PORTABLE_NAMESPACES = frozenset(
    {contracts.NS_ANSWER, contracts.NS_META, contracts.NS_PLUGIN}
)

#: Upper bound for the reference list of one document. A definition cannot use
#: more than ``MAX_COLUMNS`` + ``MAX_FILTER_CONDITIONS`` + ``MAX_SORT_ENTRIES``
#: distinct keys, so anything beyond this is padding.
MAX_REFERENCES = 400

#: Maximum length of a stored label. Same limit the structural validator applies
#: to a column label.
MAX_LABEL_CHARS = contracts.MAX_LABEL_LENGTH


def is_portable_namespace(key: str) -> bool:
    """True if *key* lives in a namespace whose identifiers are user-chosen."""
    if not isinstance(key, str) or contracts.KEY_SEPARATOR not in key:
        return False
    return key.split(contracts.KEY_SEPARATOR, 1)[0] in PORTABLE_NAMESPACES


@dataclass(frozen=True)
class Reference:
    """One event-specific object a definition refers to, by name.

    ``key`` is the field key as used in the definition, ``label`` the name the
    object had in the source event, ``identifier`` the stable identifier where
    one exists (``Question.identifier``).
    """

    key: str
    label: str = ""
    kind: str = KIND_FIELD
    identifier: str = ""

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"key": self.key, "kind": self.kind}
        if self.label:
            out["label"] = self.label
        if self.identifier:
            out["identifier"] = self.identifier
        return out


def _kind_for(key: str) -> str:
    namespace = key.split(contracts.KEY_SEPARATOR, 1)[0]
    if namespace == contracts.NS_ANSWER:
        return KIND_QUESTION
    if namespace == contracts.NS_META:
        return KIND_META
    if namespace == contracts.NS_PLUGIN:
        return KIND_PLUGIN
    return KIND_FIELD


def collect_references(
    definition: contracts.ReportDefinition,
    event: Any,
    registry: Any,
) -> Tuple[Reference, ...]:
    """Describe every event-specific key of *definition* as it exists *now*.

    Called on export. Keys that do not resolve in the source event get an entry
    without a label -- their name is not knowable any more, and saying so is
    better than omitting the key.
    """
    from pretix_custom_reports.registry.hints import EXTRA_QUESTION_IDENTIFIER

    out: List[Reference] = []
    for key in definition.field_keys():
        if not is_portable_namespace(key):
            continue
        field = registry.resolve(key, event, definition.base)
        label = ""
        identifier = ""
        if field is not None:
            label = str(field.label or "")[:MAX_LABEL_CHARS]
            identifier = str(field.extra.get(EXTRA_QUESTION_IDENTIFIER) or "")
        if not identifier:
            identifier = key.rsplit(contracts.KEY_SEPARATOR, 1)[-1]
        out.append(
            Reference(
                key=key,
                label=label,
                kind=_kind_for(key),
                identifier=identifier,
            )
        )
        if len(out) >= MAX_REFERENCES:
            break
    return tuple(out)


def parse_references(
    raw: Any,
    *,
    allowed_keys: Iterable[str] = (),
) -> Tuple[Reference, ...]:
    """Read the ``meta.references`` block of an imported file.

    :param allowed_keys: keys the definition actually uses. Entries for anything
        else are dropped -- they could only describe a field nobody references.

    Never raises. See the trust model in the module docstring.
    """
    if not isinstance(raw, (list, tuple)):
        return ()
    allowed = {key for key in allowed_keys if isinstance(key, str)}
    seen = set()
    out: List[Reference] = []
    for entry in raw[:MAX_REFERENCES]:
        if not isinstance(entry, Mapping):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or key in seen:
            continue
        if allowed and key not in allowed:
            continue
        try:
            contracts.validate_key(key)
        except ValueError:
            continue
        label = entry.get("label")
        label = label[:MAX_LABEL_CHARS] if isinstance(label, str) else ""
        kind = entry.get("kind")
        kind = kind if isinstance(kind, str) and kind in _KINDS else _kind_for(key)
        identifier = entry.get("identifier")
        if isinstance(identifier, str):
            try:
                contracts.validate_identifier(identifier)
            except ValueError:
                identifier = ""
        else:
            identifier = ""
        seen.add(key)
        out.append(Reference(key=key, label=label, kind=kind, identifier=identifier))
    return tuple(out)


def label_index(references: Sequence[Reference]) -> Dict[str, Optional[str]]:
    """``key -> label`` for quick lookups during resolution."""
    return {ref.key: (ref.label or None) for ref in references}
