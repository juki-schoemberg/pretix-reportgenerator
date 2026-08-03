"""The resolution layer: one definition, one target event, one report.

Owner from wave 2 on: portability-dev (ORCHESTRIERUNG.md section 5).

Built once, used four times
---------------------------

Importing a file, loading an organizer template into an event, copying an event
and (in the editor) asking "would this still run here?" are the same problem:
a definition written somewhere else references event-specific objects, and the
target event may know them under a different name or not at all. SPEC.md F10
says it in one sentence -- *"gleiche Mechanik wie beim Import (F9), also einmal
implementieren und zweimal nutzen"*. Two implementations of this would drift
apart within a release, and the one that drifts is the one that decides what
ends up in the database.

:func:`resolve_definition` is that single implementation.

The security property
---------------------

Everything this module produces comes out of the **target event's registry**:

* a field key is only ever kept if ``registry.resolve()`` returned a field for
  it, and the key that is written is ``field.key`` -- the registry's spelling,
  not the file's,
* a name match looks up candidates in ``registry.get_fields()`` and returns one
  of *those* keys,
* filter values on event-scoped fields are matched against
  ``field.choices()``, again from the registry,
* nothing else from the document is interpreted: aggregates, operators, styles
  and the base are closed enums that ``contracts.validate_definition`` has
  already checked, and ORM paths simply do not exist in a document (a file
  containing one is rejected by the structural validator, never sanitised).

The result is validated twice more before anybody may store it: structurally
via :func:`contracts.validate_definition` (so dropping references cannot leave a
document that no longer validates) and against the registry via
:func:`pretix_custom_reports.query.plan.check_definition` (so an aggregate that
the target's registry forbids is refused rather than saved).

Three strategies
----------------

``ABORT``
    Anything that does not resolve blocks the whole operation. The default and
    the only one an import starts with -- the user has to look at the report and
    decide.
``SKIP``
    Drop what does not resolve. The user's explicit second choice. Dropping a
    filter condition *widens* the result set, which is why it never happens
    without a confirmed decision.
``KEEP``
    Apply what could be mapped, leave the rest untouched, never fail. This is
    the event-copy case: a stored definition with an unresolvable key is a legal
    state (models.py), and losing a report during an event copy would be far
    worse than carrying a key the editor will flag.
"""

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from dataclasses import dataclass, replace

from pretix_custom_reports import contracts
from pretix_custom_reports.portability.references import (
    Reference,
    is_portable_namespace,
    label_index,
)

__all__ = [
    "KIND_FIELD",
    "KIND_VALUE",
    "MATCH_EXACT",
    "MATCH_IDENTIFIER",
    "MATCH_NAME",
    "MATCH_SPELLING",
    "STATUS_AMBIGUOUS",
    "STATUS_FOUND",
    "STATUS_MAPPED",
    "STATUS_MISSING",
    "STATUS_UNVERIFIED",
    "ResolutionEntry",
    "ResolutionOutcome",
    "ResolutionReport",
    "ResolutionStrategy",
    "resolve_definition",
]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class ResolutionStrategy:
    """What to do with a reference the target event does not know.

    Three strategies, but only two of them are a *choice a user makes*.
    ``KEEP`` belongs to the event copy (``portability/eventcopy.py``), where
    nobody is standing in front of a confirmation page and losing a column
    silently would be worse than carrying an unresolved one along. It also
    switches off the second of the two checks that :func:`resolve_definition`
    runs -- see :func:`_registry_issues` -- which is fine for a copy of a
    definition that already existed, and not fine for a document that just
    arrived from outside. Hence two coercion functions, and
    :meth:`coerce_user_choice` for everything that reads a request (S-006).
    """

    ABORT = "abort"
    SKIP = "skip"
    KEEP = "keep"

    ALL = ("abort", "skip", "keep")

    #: The subset a view may accept. ``KEEP`` is deliberately absent.
    USER_CHOICES = ("abort", "skip")

    @classmethod
    def coerce(cls, value: Any) -> str:
        """Accept only one of the three. Anything else becomes ``ABORT``.

        For programmatic callers -- the event copy asks for ``KEEP`` here.
        Never call this with a value that came out of a request; use
        :meth:`coerce_user_choice` for that.
        """
        if isinstance(value, str) and value in cls.ALL:
            return value
        return cls.ABORT

    @classmethod
    def coerce_user_choice(cls, value: Any) -> str:
        """Accept only what the user interface offers: ``ABORT`` or ``SKIP``.

        Anything else -- missing, misspelled, or ``keep`` posted by hand --
        becomes ``ABORT``, which blocks rather than writes. The import and
        template views offer exactly these two radio buttons, so a request
        carrying anything else is not a user making a choice.
        """
        if isinstance(value, str) and value in cls.USER_CHOICES:
            return value
        return cls.ABORT


#: The target event publishes this key.
STATUS_FOUND = "found"

#: Resolved, but to a different key or value than the document asked for.
STATUS_MAPPED = "mapped"

#: Not resolvable here.
STATUS_MISSING = "missing"

#: Several candidates matched equally well. Treated like ``MISSING`` -- guessing
#: which question was meant is exactly the kind of silent decision that turns an
#: import into a data protection incident.
STATUS_AMBIGUOUS = "ambiguous"

#: The value list of this field is truncated, so "not in the list" does not mean
#: "does not exist". Kept unchanged and shown as a note.
STATUS_UNVERIFIED = "unverified"

_BAD_STATUS = (STATUS_MISSING, STATUS_AMBIGUOUS)

#: The entry describes a field key.
KIND_FIELD = "field"

#: The entry describes one value of a filter condition.
KIND_VALUE = "value"

MATCH_EXACT = "exact"
MATCH_SPELLING = "spelling"
MATCH_IDENTIFIER = "identifier"
MATCH_NAME = "name"


def _normalise(text: Any) -> str:
    """Fold a name or identifier for comparison.

    Lower case, and everything that is not a letter or a digit removed, so that
    ``t-shirt size``, ``T_Shirt_Size`` and ``tshirtsize`` compare equal. This is
    the whole "fuzziness" of the name matching: it never edits distances, never
    matches prefixes and never picks the closest of several candidates.
    """
    return "".join(char for char in str(text or "").lower() if char.isalnum())


def _split_key(key: str) -> Tuple[str, str]:
    """``answer.tshirt-size`` -> ``("answer", "tshirt-size")``, last separator."""
    prefix, _, leaf = key.rpartition(contracts.KEY_SEPARATOR)
    return prefix, leaf


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolutionEntry:
    """One line of the resolution report.

    Every field reference in the document produces exactly one entry, whether it
    resolved or not -- "never silently swallow an unknown key" (SPEC.md F9) is
    implemented here, not by a log message.
    """

    kind: str
    """:data:`KIND_FIELD` or :data:`KIND_VALUE`."""

    path: str
    """Where in the document, e.g. ``columns[3]`` or ``filters.children[0]``."""

    status: str
    """One of the ``STATUS_*`` constants."""

    source: str
    """The key (or value) as it appears in the document."""

    target: Optional[str] = None
    """The key (or value) that will be stored, if any."""

    usage: Optional[str] = None
    """``column``, ``filter`` or ``sort`` for field entries."""

    match: Optional[str] = None
    """How it was matched: :data:`MATCH_EXACT`, ``spelling``, ``identifier``,
    ``name``."""

    source_label: Optional[str] = None
    """Name the object had in the source event, where the file tells us."""

    target_label: Optional[str] = None
    """Name of the object it was matched to."""

    dropped: bool = False
    """True if this reference was removed from the definition."""

    detail: str = ""
    """One sentence for the human deciding whether to go ahead."""

    @property
    def is_problem(self) -> bool:
        return self.status in _BAD_STATUS

    def as_dict(self) -> Dict[str, Any]:
        out = {
            "kind": self.kind,
            "path": self.path,
            "status": self.status,
            "source": self.source,
        }
        for name in (
            "target",
            "usage",
            "match",
            "source_label",
            "target_label",
            "detail",
        ):
            value = getattr(self, name)
            if value:
                out[name] = value
        if self.dropped:
            out["dropped"] = True
        return out


@dataclass(frozen=True)
class ResolutionReport:
    """What the target event made of every reference in the document.

    Shown to the user *before* anything is written (SPEC.md F9). Also the
    payload of the ``pretix_custom_reports.report.imported`` log entry, so the
    decision stays visible after the fact.
    """

    base: contracts.Base
    strategy: str
    entries: Tuple[ResolutionEntry, ...] = ()
    issues: Tuple[str, ...] = ()
    """Problems that no decision can fix: the document does not validate after
    resolution, or the registry refuses how a field is used."""

    values_checked: int = 0
    values_ok: int = 0

    # -- slices the templates and tests want ------------------------------

    def of_kind(self, kind: str) -> Tuple[ResolutionEntry, ...]:
        return tuple(e for e in self.entries if e.kind == kind)

    @property
    def fields(self) -> Tuple[ResolutionEntry, ...]:
        return self.of_kind(KIND_FIELD)

    @property
    def values(self) -> Tuple[ResolutionEntry, ...]:
        return self.of_kind(KIND_VALUE)

    @property
    def found(self) -> Tuple[ResolutionEntry, ...]:
        return tuple(e for e in self.entries if e.status == STATUS_FOUND)

    @property
    def mapped(self) -> Tuple[ResolutionEntry, ...]:
        return tuple(e for e in self.entries if e.status == STATUS_MAPPED)

    @property
    def missing(self) -> Tuple[ResolutionEntry, ...]:
        return tuple(e for e in self.entries if e.status in _BAD_STATUS)

    @property
    def unverified(self) -> Tuple[ResolutionEntry, ...]:
        return tuple(e for e in self.entries if e.status == STATUS_UNVERIFIED)

    @property
    def dropped(self) -> Tuple[ResolutionEntry, ...]:
        return tuple(e for e in self.entries if e.dropped)

    @property
    def blocking(self) -> Tuple[ResolutionEntry, ...]:
        """Entries that stop the import as long as the strategy stays as it is."""
        return tuple(e for e in self.entries if e.is_problem and not e.dropped)

    @property
    def has_problems(self) -> bool:
        return bool(self.missing) or bool(self.issues)

    @property
    def is_clean(self) -> bool:
        """Everything resolved exactly, nothing was mapped or dropped."""
        return not self.has_problems and not self.mapped and not self.unverified

    def as_dict(self) -> Dict[str, Any]:
        return {
            "base": self.base.value,
            "strategy": self.strategy,
            "counts": {
                "found": len(self.found),
                "mapped": len(self.mapped),
                "missing": len(self.missing),
                "unverified": len(self.unverified),
                "dropped": len(self.dropped),
                "values_checked": self.values_checked,
                "values_ok": self.values_ok,
            },
            "entries": [e.as_dict() for e in self.entries],
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class ResolutionOutcome:
    """Result of :func:`resolve_definition`: a report, and maybe a definition."""

    report: ResolutionReport

    document: Optional[contracts.ReportDefinition] = None
    """The resolved definition, or ``None`` if it must not be stored."""

    @property
    def ok(self) -> bool:
        """True if the result may be written to the database."""
        return self.document is not None and not self.report.blocking

    def as_dict(self) -> Optional[Dict[str, Any]]:
        """The resolved definition in canonical form, ready for the model."""
        return self.document.as_dict() if self.document is not None else None


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _KeyResult:
    status: str
    target: Optional[str] = None
    match: Optional[str] = None
    source_label: Optional[str] = None
    target_label: Optional[str] = None
    detail: str = ""


def _resolve_key(
    key: str,
    *,
    event: Any,
    base: contracts.Base,
    registry: Any,
    fields: Mapping[str, Any],
    labels: Mapping[str, Optional[str]],
) -> _KeyResult:
    """Find *key* in the target event's registry, or a rename of it."""
    source_label = labels.get(key)

    field = registry.resolve(key, event, base)
    if field is not None:
        if field.key == key:
            return _KeyResult(
                status=STATUS_FOUND,
                target=key,
                match=MATCH_EXACT,
                source_label=source_label,
                target_label=str(field.label or "") or None,
            )
        # The registry resolved a differently spelled key -- for answers it
        # compares identifiers case-insensitively (ADR 0001 section 3.2). Write
        # the registry's spelling and say so.
        return _KeyResult(
            status=STATUS_MAPPED,
            target=field.key,
            match=MATCH_SPELLING,
            source_label=source_label,
            target_label=str(field.label or "") or None,
            detail="Matched a field whose identifier differs only in "
            "capitalisation.",
        )

    if not is_portable_namespace(key):
        return _KeyResult(
            status=STATUS_MISSING,
            source_label=source_label,
            detail="This event has no field with this key.",
        )

    prefix, leaf = _split_key(key)
    candidates = {
        candidate_key: candidate
        for candidate_key, candidate in fields.items()
        if _split_key(candidate_key)[0] == prefix
    }

    hits = [
        candidate_key
        for candidate_key in candidates
        if _normalise(_split_key(candidate_key)[1]) == _normalise(leaf)
    ]
    if len(hits) == 1:
        target = hits[0]
        return _KeyResult(
            status=STATUS_MAPPED,
            target=target,
            match=MATCH_IDENTIFIER,
            source_label=source_label,
            target_label=str(candidates[target].label or "") or None,
            detail="Matched by identifier, ignoring dashes, underscores and "
            "capitalisation.",
        )
    if len(hits) > 1:
        return _KeyResult(
            status=STATUS_AMBIGUOUS,
            source_label=source_label,
            detail="Several fields of this event have a similar identifier: "
            + ", ".join(sorted(hits)[:5]),
        )

    if source_label:
        wanted = _normalise(source_label)
        by_name = [
            candidate_key
            for candidate_key, candidate in candidates.items()
            if wanted and _normalise(candidate.label) == wanted
        ]
        if len(by_name) == 1:
            target = by_name[0]
            return _KeyResult(
                status=STATUS_MAPPED,
                target=target,
                match=MATCH_NAME,
                source_label=source_label,
                target_label=str(candidates[target].label or "") or None,
                detail="Matched by name, because no field with this identifier "
                "exists here.",
            )
        if len(by_name) > 1:
            return _KeyResult(
                status=STATUS_AMBIGUOUS,
                source_label=source_label,
                detail="Several fields of this event carry this name: "
                + ", ".join(sorted(by_name)[:5]),
            )

    return _KeyResult(
        status=STATUS_MISSING,
        source_label=source_label,
        detail=(
            "No field of this event matches this key, neither by identifier nor "
            "by name."
        ),
    )


# ---------------------------------------------------------------------------
# Value resolution
# ---------------------------------------------------------------------------


def _choice_values(
    field: Any, event: Any, base: contracts.Base
) -> Tuple[List[Any], bool]:
    """``([choice values], truncated)`` for *field*, or ``([], False)``.

    The callable comes from the registry and hits the database. A field whose
    ``choices`` raise must not take the whole import down: an unusable value
    list means "cannot verify", not "reject".
    """
    from pretix_custom_reports.registry.choices import MAX_CHOICES

    if field is None or field.choices is None:
        return [], False
    try:
        pairs = list(field.choices(contracts.FieldContext(event=event, base=base)))
    except Exception:  # pragma: no cover - defensive, registry decides
        return [], True
    return [pair[0] for pair in pairs if isinstance(pair, (tuple, list)) and pair], (
        len(pairs) >= MAX_CHOICES
    )


def _resolve_value_list(
    items: Sequence[Any],
    *,
    path: str,
    field: Any,
    event: Any,
    base: contracts.Base,
    strategy: str,
) -> Tuple[List[Any], List[ResolutionEntry], int, int]:
    """Match every item against the field's value list.

    :returns: ``(kept items, entries, exact matches, items checked)``. Only
        items that are *not* an exact match produce an entry -- a filter with
        200 matching values would otherwise bury the three that do not.
    """
    choices, truncated = _choice_values(field, event, base)
    if not choices and not truncated:
        # The field is event scoped but publishes no value list (a voucher code,
        # for instance). Nothing to match against, so nothing is claimed.
        return list(items), [], 0, 0

    exact = {str(value) for value in choices}
    folded: Dict[str, Any] = {}
    for value in choices:
        folded.setdefault(_normalise(value), value)

    kept: List[Any] = []
    entries: List[ResolutionEntry] = []
    ok = 0
    for item in items:
        if not isinstance(item, str):
            # A boolean or number against a name list: not something name
            # matching can help with. Leave it to the query compiler.
            kept.append(item)
            continue
        if item in exact:
            kept.append(item)
            ok += 1
            continue
        match = folded.get(_normalise(item))
        if match is not None:
            kept.append(match)
            entries.append(
                ResolutionEntry(
                    kind=KIND_VALUE,
                    path=path,
                    status=STATUS_MAPPED,
                    source=item,
                    target=str(match),
                    match=MATCH_NAME,
                    detail="Matched a value of this event that differs only in "
                    "spelling.",
                )
            )
            continue
        if truncated:
            kept.append(item)
            entries.append(
                ResolutionEntry(
                    kind=KIND_VALUE,
                    path=path,
                    status=STATUS_UNVERIFIED,
                    source=item,
                    target=item,
                    detail="This event has too many values to check them all. "
                    "The filter is kept unchanged.",
                )
            )
            continue
        drop = strategy == ResolutionStrategy.SKIP
        if not drop:
            kept.append(item)
        entries.append(
            ResolutionEntry(
                kind=KIND_VALUE,
                path=path,
                status=STATUS_MISSING,
                source=item,
                dropped=drop,
                detail="No such value exists in this event.",
            )
        )
    return kept, entries, ok, len(items)


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def resolve_definition(
    definition: contracts.ReportDefinition,
    *,
    event: Any,
    registry: Any = None,
    references: Iterable[Reference] = (),
    strategy: str = ResolutionStrategy.ABORT,
) -> ResolutionOutcome:
    """Translate *definition* into something *event* can run, and report on it.

    :param definition: a structurally valid document. Callers get one from
        :func:`contracts.validate_definition`; this function never sees raw JSON.
    :param event: the **target** event. Everything is resolved against its
        registry, never against the source event's.
    :param registry: a :class:`~pretix_custom_reports.contracts.FieldRegistry`.
        Defaults to the real one.
    :param references: name hints from the file, see
        :mod:`~pretix_custom_reports.portability.references`.
    :param strategy: one of :class:`ResolutionStrategy`.
    :returns: a :class:`ResolutionOutcome`. Never raises for anything the
        document did -- a refusal is a report, not an exception, because the
        user has to see all of it at once.
    """
    if registry is None:
        from pretix_custom_reports.registry.library import field_registry

        registry = field_registry()

    strategy = ResolutionStrategy.coerce(strategy)
    base = definition.base
    fields = registry.get_fields(event, base)
    labels = label_index(tuple(references))

    resolved: Dict[str, _KeyResult] = {
        key: _resolve_key(
            key,
            event=event,
            base=base,
            registry=registry,
            fields=fields,
            labels=labels,
        )
        for key in definition.field_keys()
    }

    entries: List[ResolutionEntry] = []
    values_checked = 0
    values_ok = 0

    def _field_entry(path: str, key: str, usage: str) -> bool:
        """Append the entry for one field reference; return True if it stays."""
        result = resolved[key]
        drop = result.status in _BAD_STATUS and strategy == ResolutionStrategy.SKIP
        entries.append(
            ResolutionEntry(
                kind=KIND_FIELD,
                path=path,
                usage=usage,
                status=result.status,
                source=key,
                target=result.target,
                match=result.match,
                source_label=result.source_label,
                target_label=result.target_label,
                dropped=drop,
                detail=result.detail,
            )
        )
        return not drop

    # -- columns ----------------------------------------------------------
    columns: List[contracts.Column] = []
    for index, column in enumerate(definition.columns):
        if _field_entry(f"columns[{index}]", column.field, "column"):
            columns.append(
                replace(column, field=resolved[column.field].target or column.field)
            )

    # -- filters ----------------------------------------------------------
    def _rebuild_condition(
        condition: contracts.FilterCondition, path: str
    ) -> Optional[contracts.FilterCondition]:
        nonlocal values_checked, values_ok
        if not _field_entry(path, condition.field, "filter"):
            return None
        key = resolved[condition.field].target or condition.field
        field = fields.get(key)
        kind = contracts.OPERATOR_SPECS[condition.operator].value_kind
        if (
            field is None
            or field.value_scope != contracts.ValueScope.EVENT
            or kind not in (contracts.ValueKind.SCALAR, contracts.ValueKind.LIST)
        ):
            return replace(condition, field=key)

        items = (
            list(condition.value)
            if kind is contracts.ValueKind.LIST and isinstance(condition.value, list)
            else [condition.value]
        )
        kept, value_entries, ok, checked = _resolve_value_list(
            items,
            path=path,
            field=field,
            event=event,
            base=base,
            strategy=strategy,
        )
        entries.extend(value_entries)
        values_ok += ok
        values_checked += checked
        if not kept:
            # Every value of this condition is gone. Keeping the condition with
            # an empty list would either fail validation or, worse, silently
            # match nothing.
            entries.append(
                ResolutionEntry(
                    kind=KIND_VALUE,
                    path=path,
                    status=STATUS_MISSING,
                    source=condition.field,
                    dropped=True,
                    detail="The whole filter condition was removed: none of its "
                    "values exists in this event.",
                )
            )
            return None
        value = kept if kind is contracts.ValueKind.LIST else kept[0]
        return replace(condition, field=key, value=value)

    filters: Optional[contracts.FilterGroup] = None
    if definition.filters is not None:
        children: List[Any] = []
        for index, child in enumerate(definition.filters.children):
            path = f"filters.children[{index}]"
            if isinstance(child, contracts.FilterCondition):
                rebuilt = _rebuild_condition(child, path)
                if rebuilt is not None:
                    children.append(rebuilt)
            else:
                inner: List[Any] = []
                for jndex, grandchild in enumerate(child.children):
                    if not isinstance(grandchild, contracts.FilterCondition):
                        continue  # pragma: no cover - one nesting level only
                    rebuilt = _rebuild_condition(
                        grandchild, f"{path}.children[{jndex}]"
                    )
                    if rebuilt is not None:
                        inner.append(rebuilt)
                if inner:
                    children.append(replace(child, children=tuple(inner)))
        if children:
            filters = replace(definition.filters, children=tuple(children))

    # -- sorting ----------------------------------------------------------
    sorting: List[contracts.SortEntry] = []
    for index, entry in enumerate(definition.sorting):
        if _field_entry(f"sorting[{index}]", entry.field, "sort"):
            sorting.append(
                replace(entry, field=resolved[entry.field].target or entry.field)
            )

    candidate = replace(
        definition,
        columns=tuple(columns),
        filters=filters,
        sorting=tuple(sorting),
    )

    issues: List[str] = []
    document: Optional[contracts.ReportDefinition] = None
    try:
        # Re-validate rather than trust the surgery above: dropping references
        # can leave a document that no longer validates (no columns at all is
        # the obvious one), and that must never reach the model.
        document = contracts.validate_definition(candidate.as_dict())
    except contracts.DefinitionValidationError as e:
        issues.extend(
            f"{issue.path}: {issue.message}" if issue.path else issue.message
            for issue in e.issues
        )

    blocking = [e for e in entries if e.is_problem and not e.dropped]
    if document is not None and not blocking and strategy != ResolutionStrategy.KEEP:
        # Only worth asking once every reference is settled: with an unresolved
        # key still in the document the compiler would just repeat what the
        # entries above already say.
        issues.extend(_registry_issues(document, event, registry))

    report = ResolutionReport(
        base=base,
        strategy=strategy,
        entries=tuple(entries),
        issues=tuple(issues),
        values_checked=values_checked,
        values_ok=values_ok,
    )
    if issues:
        document = None
    return ResolutionOutcome(report=report, document=document)


def _registry_issues(
    document: contracts.ReportDefinition, event: Any, registry: Any
) -> List[str]:
    """Ask the query compiler's first pass whether this would run here.

    Deliberately the compiler's own check
    (:func:`pretix_custom_reports.query.plan.check_definition`) rather than a
    second implementation of the same rules: "the aggregate is mandatory on base
    order", "this field is not sortable here", "this operator is not allowed for
    a money field". If the importer were more permissive than the compiler, we
    would store reports that fail on their first scheduled run
    (docs/pretix-api-notes.md section 5.6). It resolves and checks only -- no
    queryset, no database.
    """
    from pretix_custom_reports.query.plan import check_definition

    try:
        check_definition(document, event, registry)
    except contracts.FieldResolutionError as e:
        return ["Unknown field for this event: {}".format(", ".join(sorted(e.keys)))]
    except contracts.CompilationError as e:
        return [str(e)]
    return []
