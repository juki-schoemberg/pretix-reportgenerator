"""Per-event caching of the field table, and how it is invalidated.

Owner: registry-dev. Rationale and rejected alternatives: docs/adr/0002-registry.md.

The problem
-----------

Building the field table for one ``(event, base)`` costs two small queries (the
event's questions, the organizer's meta properties) plus one signal round trip.
The editor asks for it on every keystroke of the field search, the preview asks
for it on every redraw, and an export asks for it once per event. So it has to be
cached -- but a stale field table is worse than no cache: a question that was
just renamed would keep resolving under its old key, and a report would silently
export the wrong column.

Two layers
----------

**1. A process-local dictionary** holds the built ``{key: ReportField}`` mapping.
It has to be process-local: a ``ReportField`` contains closures
(``annotation``, ``choices``, ``value_getter``), and closures cannot be pickled,
so ``django.core.cache`` can never hold the fields themselves.

**2. A token in ``django.core.cache``** decides whether that dictionary entry is
still valid. The token is a random string; invalidating means deleting it, so the
next reader generates a new one and every process notices. In production that
cache is shared (Redis), which is what makes an invalidation in a web worker
visible to a Celery worker.

The token is built from three parts, each answering "what could make the field
table wrong?":

============================  =========================================
event token                   the event's questions changed
organizer token               the organizer's meta properties changed
``event.plugins``             a plugin was enabled or disabled, so the
                              set of contributed fields changed
============================  =========================================

What is *not* in there
----------------------

Products, categories, variations, dates, seats, vouchers and question options do
not appear -- their content reaches the registry exclusively through lazy
``choices`` callables that run at request time. That is a deliberate design
constraint on :mod:`registry.core` and :mod:`registry.choices`: **anything
volatile goes behind a callable, not into the field structure.** It is why
editing a product does not invalidate anything.

The invalidation receivers still cover ``Item``, ``ItemCategory``, ``SubEvent``
and ``Discount`` on top of ``Question``. Those are strictly speaking unnecessary
today; they are there because the day somebody adds a field that *does* bake
product data into its structure, the cache must not be the thing that breaks.
The cost is a rebuild of a small dictionary after a product edit.

Failure modes, both deliberate
------------------------------

* **Dummy cache backend.** ``get_or_set`` cannot store, so every call gets a
  fresh token and the table is rebuilt every time. Correct, just slower.
* **Per-process cache backend** (``LocMemCache`` with several workers). An
  invalidation in one process is invisible to the others until their entry ages
  out. :data:`MAX_AGE` bounds that: a token is also considered stale after two
  minutes, whatever the cache says. That is the one thing a token alone cannot
  give us, and two minutes of a stale field library is an acceptable worst case
  for a misconfigured cache.
"""

from typing import Any, Optional, Tuple

import logging
import time
from collections import OrderedDict
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from uuid import uuid4

__all__ = [
    "CACHE_KEY_PREFIX",
    "MAX_AGE",
    "MAX_ENTRIES",
    "cache_token",
    "clear_local_cache",
    "connect_invalidation_receivers",
    "get_cached",
    "invalidate_event",
    "invalidate_organizer",
    "set_cached",
]

logger = logging.getLogger(__name__)

#: Prefix of both cache keys. Contains the package name so it cannot clash with
#: anything pretix or another plugin stores.
CACHE_KEY_PREFIX = "pretix_custom_reports.registry"

#: Seconds a local entry may be reused even if the shared token still matches.
#: The safety net for cache backends that are not shared between processes.
MAX_AGE = 120

#: Upper bound on the process-local dictionary. A Celery worker can touch many
#: events in its lifetime; without a bound this would be a slow memory leak.
#: Two entries per event (one per base), so 128 covers 64 events.
MAX_ENTRIES = 128

_LOCAL: "OrderedDict[Tuple[int, str], Tuple[str, float, Any]]" = OrderedDict()


def _event_key(event_pk: int) -> str:
    return f"{CACHE_KEY_PREFIX}.event.{event_pk}"


def _organizer_key(organizer_pk: int) -> str:
    return f"{CACHE_KEY_PREFIX}.organizer.{organizer_pk}"


def _token(cache_key: str) -> str:
    """Current token for *cache_key*, generating and storing one if absent."""
    try:
        return str(cache.get_or_set(cache_key, uuid4().hex, timeout=None))
    except Exception:  # pragma: no cover - a broken cache must not break reports
        logger.exception(
            "pretix-custom-reports could not read its registry cache token"
        )
        return uuid4().hex


def cache_token(event: Any) -> str:
    """Token that changes whenever the field table for *event* may have changed."""
    return "{}|{}|{}".format(
        _token(_event_key(event.pk)),
        _token(_organizer_key(event.organizer_id)),
        event.plugins or "",
    )


def invalidate_event(event_pk: Optional[int]) -> None:
    """Drop the token of one event. Next reader rebuilds."""
    if event_pk is None:
        return
    try:
        cache.delete(_event_key(event_pk))
    except Exception:  # pragma: no cover - see _token
        logger.exception(
            "pretix-custom-reports could not invalidate its registry cache"
        )
    for local_key in [key for key in _LOCAL if key[0] == event_pk]:
        _LOCAL.pop(local_key, None)


def invalidate_organizer(organizer_pk: Optional[int]) -> None:
    """Drop the token of every event of one organizer.

    Used for ``EventMetaProperty``, which is defined once per organizer and
    affects all of its events.
    """
    if organizer_pk is None:
        return
    try:
        cache.delete(_organizer_key(organizer_pk))
    except Exception:  # pragma: no cover - see _token
        logger.exception(
            "pretix-custom-reports could not invalidate its registry cache"
        )
    _LOCAL.clear()


def clear_local_cache() -> None:
    """Forget everything this process cached. For tests and for shell sessions."""
    _LOCAL.clear()


def get_cached(event: Any, base: Any) -> Optional[Any]:
    """The cached value for ``(event, base)``, or ``None`` if there is none."""
    local_key = (event.pk, str(base))
    entry = _LOCAL.get(local_key)
    if entry is None:
        return None
    token, stored_at, value = entry
    if token != cache_token(event) or (time.monotonic() - stored_at) > MAX_AGE:
        _LOCAL.pop(local_key, None)
        return None
    _LOCAL.move_to_end(local_key)
    return value


def set_cached(event: Any, base: Any, value: Any) -> None:
    """Remember *value* for ``(event, base)`` under the current token."""
    local_key = (event.pk, str(base))
    _LOCAL[local_key] = (cache_token(event), time.monotonic(), value)
    _LOCAL.move_to_end(local_key)
    while len(_LOCAL) > MAX_ENTRIES:
        _LOCAL.popitem(last=False)


# ---------------------------------------------------------------------------
# Invalidation receivers
# ---------------------------------------------------------------------------
#
# Plain Django model signals, not pretix plugin signals: they must fire whether
# or not our plugin is enabled for the event whose data changed. Connected on
# import of this module with dispatch_uids, so importing twice is harmless.
#
# Every receiver reads an ``*_id`` attribute only. No queries: these run inside
# somebody else's save() and must stay cheap, and a query here could deadlock
# behind the transaction that triggered it.

_UID = "pretix_custom_reports_registry_cache"


def _connect_event_scoped(model: Any, name: str) -> None:
    def handler(sender: Any, instance: Any, **kwargs: Any) -> None:
        invalidate_event(getattr(instance, "event_id", None))

    handler.__name__ = f"invalidate_on_{name}"
    post_save.connect(
        handler, sender=model, dispatch_uid=f"{_UID}_{name}_save", weak=False
    )
    post_delete.connect(
        handler, sender=model, dispatch_uid=f"{_UID}_{name}_delete", weak=False
    )


def connect_invalidation_receivers() -> None:
    """Connect the model signal receivers that keep the cache honest.

    Called on import of this module. The integrator additionally imports this
    module from ``pretix_custom_reports/signals.py`` so that the receivers are
    connected as soon as the app is loaded, not only once somebody happens to
    touch the registry -- see
    ``handoff/requests/registry-dev-an-integrator-signals.md``.
    """
    from pretix.base.models import (
        Discount,
        EventMetaProperty,
        Item,
        ItemCategory,
        Question,
        SubEvent,
    )

    # Structurally relevant: identifiers, types and labels of questions are
    # baked into the field table.
    _connect_event_scoped(Question, "question")

    # Belt and braces, see the module docstring.
    _connect_event_scoped(Item, "item")
    _connect_event_scoped(ItemCategory, "itemcategory")
    _connect_event_scoped(SubEvent, "subevent")
    _connect_event_scoped(Discount, "discount")

    def on_meta_property(sender: Any, instance: Any, **kwargs: Any) -> None:
        invalidate_organizer(getattr(instance, "organizer_id", None))

    post_save.connect(
        on_meta_property,
        sender=EventMetaProperty,
        dispatch_uid=f"{_UID}_metaproperty_save",
        weak=False,
    )
    post_delete.connect(
        on_meta_property,
        sender=EventMetaProperty,
        dispatch_uid=f"{_UID}_metaproperty_delete",
        weak=False,
    )


connect_invalidation_receivers()
