"""The field registry itself: :class:`EventFieldRegistry`.

Owner: registry-dev.

This is the entry point everyone else uses::

    from pretix_custom_reports.contracts import Base
    from pretix_custom_reports.registry.library import field_registry

    registry = field_registry()
    fields = registry.get_fields(event, Base.ORDERPOSITION)
    field = registry.resolve("answer.tshirt-size", event, Base.ORDERPOSITION)

It satisfies :class:`~pretix_custom_reports.contracts.FieldRegistry` and is a
drop-in replacement for
:class:`~pretix_custom_reports.contracts.stubs.StubFieldRegistry`, with two
additions that the protocol does not require but every consumer wants:
:meth:`EventFieldRegistry.context` for building a ``FieldContext`` and
:meth:`EventFieldRegistry.diagnostics` for "what did you leave out and why".

Sources, in the order SPEC.md section 6 prescribes
--------------------------------------------------

1. the hand-curated core table (:mod:`registry.core`, :mod:`registry.computed`)
2. dynamic event data: questions (:mod:`registry.questions`) and event meta
   properties (:mod:`registry.meta`)
3. fields from other plugins (:mod:`registry.signals`)

The order is the conflict rule: a later source can never overwrite an earlier
key. Core wins over a plugin, and the first plugin wins over the second.

No event, no registry
---------------------

Every method requires a concrete ``Event`` and raises ``ValueError`` without
one. There is deliberately no "all fields of all events" view: the field table
is the allow-list an untrusted document is resolved against, and a list that is
not bound to one event would be an allow-list for the wrong tenant. Everything
this module queries goes through ``event.questions`` or
``event.organizer.meta_properties`` -- reverse accessors of the event itself, so
the restriction is structural rather than a filter that could be forgotten.

django-scopes
-------------

The registry does **not** call ``scopes_disabled()``. Building it touches scoped
models (``Question``, and through the lazy ``choices`` callables ``Item``,
``Voucher``, ...), so it needs an active scope -- which is exactly what the
control backend, the API and every ``EventTask``-based Celery task provide
(docs/pretix-api-notes.md section 7). Disabling scopes here would remove the
tenant separation from the one place that decides which data a report may name.
Tests have to open a scope or use ``scopes_disabled()`` themselves.
"""

from typing import Any, Dict, Mapping, Optional, Tuple, Union

from pretix_custom_reports.contracts import (
    NS_ANSWER,
    PROVIDER_CORE,
    Base,
    FieldContext,
    ReportField,
    validate_key,
)
from pretix_custom_reports.registry import (
    cache as registry_cache,
    computed,
    core,
    meta,
    questions,
    signals,
)
from pretix_custom_reports.registry.diagnostics import (
    RegistryDiagnostics,
    SkippedField,
)

__all__ = ["EventFieldRegistry", "field_registry"]


class _Built:
    """One built ``(event, base)`` field table plus its diagnostics."""

    __slots__ = ("fields", "lowercase", "skipped", "providers")

    def __init__(
        self,
        fields: Dict[str, ReportField],
        skipped: Tuple[SkippedField, ...],
    ) -> None:
        self.fields = fields
        self.skipped = skipped
        # Only answer keys need the case-insensitive index: pretix checks the
        # uniqueness of Question.identifier case-insensitively, so two questions
        # differing only in capitalisation cannot exist, and a user who fixes the
        # capitalisation of an identifier should not break a saved report
        # (ADR 0001 section 3.2).
        self.lowercase = {
            key.lower(): key for key in fields if key.startswith(f"{NS_ANSWER}.")
        }
        providers = (
            [PROVIDER_CORE]
            if any(field.provider == PROVIDER_CORE for field in fields.values())
            else []
        )
        for field in fields.values():
            if field.provider != PROVIDER_CORE and field.provider not in providers:
                providers.append(field.provider)
        self.providers = tuple(providers)


class EventFieldRegistry:
    """Which fields exist for one event and one report base.

    Stateless apart from the cache; a single process-wide instance is enough and
    :func:`field_registry` provides it.
    """

    # -- FieldRegistry protocol -------------------------------------------

    def get_fields(
        self, event: Any, base: Union[Base, str]
    ) -> Mapping[str, ReportField]:
        """All fields usable on *base* for *event*, keyed by field key.

        Iteration order is stable: core fields in table order, then questions in
        the order the organizer sorted them, then meta properties, then plugin
        fields. The editor renders the field library straight from it.

        Deprecated fields are included -- an old report has to keep resolving --
        and the editor filters them out of the library itself.

        Returns a copy, so a caller cannot mutate the cache.
        """
        return dict(self._built(event, base).fields)

    def resolve(
        self, key: str, event: Any, base: Union[Base, str]
    ) -> Optional[ReportField]:
        """One field, or ``None`` if *key* is unknown for this event and base.

        ``None`` rather than an exception is the contract (ADR 0001 section 3.2):
        a renamed ``Question.identifier``, a deleted product or a deactivated
        plugin makes a key unresolvable, and that is a regular state the importer
        and the editor have to display, not crash on.

        A malformed key also returns ``None``. It cannot match anything, and
        making callers guard against an exception for something an untrusted
        document controls would just move the crash around.
        """
        if not isinstance(key, str):
            return None
        built = self._built(event, base)
        field = built.fields.get(key)
        if field is not None:
            return field
        try:
            validate_key(key)
        except ValueError:
            return None
        if key.startswith(f"{NS_ANSWER}."):
            canonical = built.lowercase.get(key.lower())
            if canonical is not None:
                return built.fields[canonical]
        return None

    # -- convenience -------------------------------------------------------

    def context(self, event: Any, base: Union[Base, str]) -> FieldContext:
        """Build the :class:`FieldContext` that ``annotation`` and ``choices`` need."""
        self._require_event(event)
        return FieldContext(event=event, base=Base.coerce(base))

    def diagnostics(self, event: Any, base: Union[Base, str]) -> RegistryDiagnostics:
        """What was published and what was left out, for the debug view."""
        built = self._built(event, base)
        return RegistryDiagnostics(
            base=Base.coerce(base),
            field_count=len(built.fields),
            skipped=built.skipped,
            providers=built.providers,
        )

    def keys(self, event: Any, base: Union[Base, str]) -> Tuple[str, ...]:
        """Every resolvable key, in field-library order."""
        return tuple(self._built(event, base).fields.keys())

    # -- building ----------------------------------------------------------

    @staticmethod
    def _require_event(event: Any) -> None:
        if event is None or getattr(event, "pk", None) is None:
            raise ValueError(
                "The report field registry is always built for one saved event. "
                "There is no event-independent field list."
            )

    def _built(self, event: Any, base: Union[Base, str]) -> _Built:
        self._require_event(event)
        coerced = Base.coerce(base)
        cached = registry_cache.get_cached(event, coerced)
        if cached is not None:
            return cached
        built = self._build(event, coerced)
        registry_cache.set_cached(event, coerced, built)
        return built

    @staticmethod
    def _build(event: Any, base: Base) -> _Built:
        fields: Dict[str, ReportField] = {}
        skipped: list = []

        # 1. core, hand-curated and event independent
        fields.update(core.core_fields(base))
        fields.update(computed.computed_fields(base))

        # 2. dynamic event data
        question_fields, question_skipped = questions.question_fields(event, base)
        skipped.extend(question_skipped)
        for key, field in question_fields.items():
            if key in fields:  # pragma: no cover - core uses no answer.* key
                continue
            fields[key] = field

        meta_fields, meta_skipped = meta.meta_fields(event, base)
        skipped.extend(meta_skipped)
        for key, field in meta_fields.items():
            if key in fields:  # pragma: no cover - core uses no meta.* key
                continue
            fields[key] = field

        # 3. other plugins. Last, so it cannot shadow anything above.
        plugin_fields, plugin_skipped = signals.collect_plugin_fields(
            event, base, fields.keys()
        )
        skipped.extend(plugin_skipped)
        fields.update(plugin_fields)

        return _Built(fields=fields, skipped=tuple(skipped))


_DEFAULT: Optional[EventFieldRegistry] = None


def field_registry() -> EventFieldRegistry:
    """The process-wide :class:`EventFieldRegistry`.

    A single instance is correct because the registry holds no per-event state:
    the cache is keyed by event and lives in :mod:`registry.cache`.
    """
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = EventFieldRegistry()
    return _DEFAULT
