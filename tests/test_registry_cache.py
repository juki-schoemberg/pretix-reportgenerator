# Owner: registry-dev (tests/test_registry*.py, ORCHESTRIERUNG.md section 5)
#
# A stale field table is worse than no cache: a renamed question would keep
# resolving under its old key and a report would export the wrong column. So
# every test in here is really a test about invalidation, not about speed.
#
# pretix' test settings use the DummyCache (pretix/testutils/settings.py:74-78),
# which cannot store anything. Most tests therefore switch to LocMemCache -- and
# one test deliberately does not, to pin down that the dummy backend degrades to
# "always rebuild" instead of to "wrong answers".
"""Tests for the per-event registry cache and its invalidation."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_scopes import scopes_disabled
from pretix.base.models import Item, Question

from pretix_custom_reports.contracts import Base
from pretix_custom_reports.registry import cache as registry_cache
from tests import test_registry_support as support
from tests.test_registry_support import enable_plugin, make_meta_property

# See the note in tests/test_registry.py about why this is an assignment.
registry = support.registry
event_questions = support.event_questions

LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "pretix-custom-reports-registry-tests",
    }
}


@pytest.fixture
def shared_cache(settings):
    """A cache backend that can actually store, plus an empty local cache."""
    settings.CACHES = LOCMEM
    from django.core.cache import cache

    cache.clear()
    registry_cache.clear_local_cache()
    yield cache
    cache.clear()
    registry_cache.clear_local_cache()


def _build(registry, event, base=Base.ORDER):
    with scopes_disabled():
        return registry.get_fields(event, base)


# ---------------------------------------------------------------------------
# Hits and misses
# ---------------------------------------------------------------------------


def test_second_build_is_served_from_the_cache(registry, event, shared_cache):
    """Building twice queries the database once."""
    _build(registry, event)
    with CaptureQueriesContext(connection) as captured:
        _build(registry, event)
    assert len(captured) == 0


def test_the_two_bases_are_cached_separately(registry, event, shared_cache):
    """``order`` and ``orderposition`` are different field tables."""
    order_fields = _build(registry, event, Base.ORDER)
    position_fields = _build(registry, event, Base.ORDERPOSITION)
    assert order_fields["position.price"].needs_aggregate_on(Base.ORDER)
    assert not position_fields["position.price"].needs_aggregate_on(Base.ORDERPOSITION)


def test_two_events_never_share_an_entry(
    registry, event, event_without_plugin, shared_cache
):
    """The cache key contains the event, so one event cannot answer for another.

    The most consequential of these tests: a shared entry would be a
    cross-tenant data leak, not a display bug.
    """
    with scopes_disabled():
        Question.objects.create(
            event=event,
            question="Only in event one",
            identifier="only-one",
            type=Question.TYPE_STRING,
        )
    assert "answer.only-one" in _build(registry, event)
    assert "answer.only-one" not in _build(registry, event_without_plugin)


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


def test_new_question_invalidates(registry, event, shared_cache):
    """Adding a question makes its key resolvable without any manual step."""
    assert "answer.late-question" not in _build(registry, event)
    with scopes_disabled():
        Question.objects.create(
            event=event,
            question="Added later",
            identifier="late-question",
            type=Question.TYPE_STRING,
        )
    assert "answer.late-question" in _build(registry, event)


def test_renaming_a_question_invalidates(
    registry, event, event_questions, shared_cache
):
    """The realistic failure this cache could cause, and does not.

    ``Question.identifier`` is editable at any time. Without invalidation the old
    key would keep resolving and a report would silently keep a column that no
    longer exists.
    """
    assert "answer.tshirt-size" in _build(registry, event)
    with scopes_disabled():
        question = event_questions["tshirt-size"]
        question.identifier = "shirt-size"
        question.save(update_fields=["identifier"])
    fields = _build(registry, event)
    assert "answer.tshirt-size" not in fields
    assert "answer.shirt-size" in fields


def test_deleting_a_question_invalidates(
    registry, event, event_questions, shared_cache
):
    """post_delete counts too, not only post_save."""
    assert "answer.newsletter" in _build(registry, event)
    with scopes_disabled():
        event_questions["newsletter"].delete()
    assert "answer.newsletter" not in _build(registry, event)


def test_changing_a_question_label_invalidates(
    registry, event, event_questions, shared_cache
):
    """The label is baked into the field, so it has to be invalidated as well."""
    assert (
        str(_build(registry, event)["answer.newsletter"].label) == "Newsletter opt-in"
    )
    with scopes_disabled():
        question = event_questions["newsletter"]
        question.question = "Do you want mail?"
        question.save(update_fields=["question"])
    assert (
        str(_build(registry, event)["answer.newsletter"].label) == "Do you want mail?"
    )


def test_new_meta_property_invalidates_every_event_of_the_organizer(
    registry, event, event_without_plugin, shared_cache
):
    """A meta property is defined once per organizer, so it invalidates broadly."""
    assert "meta.event.campaign" not in _build(registry, event)
    assert "meta.event.campaign" not in _build(registry, event_without_plugin)
    make_meta_property(event.organizer)
    assert "meta.event.campaign" in _build(registry, event)
    assert "meta.event.campaign" in _build(registry, event_without_plugin)


def test_product_change_invalidates(registry, event, shared_cache):
    """Belt and braces: no core field bakes product data in, but the hook is live.

    Product data reaches the registry only through lazy ``choices`` callables, so
    this invalidation is not needed today. It is wired up so that adding a field
    which *does* depend on products later cannot be broken by the cache. This
    test exists to keep the receiver from being deleted as dead weight.
    """
    _build(registry, event)
    before = registry_cache.cache_token(event)
    with scopes_disabled():
        Item.objects.create(event=event, name="Late ticket", default_price=5)
    assert registry_cache.cache_token(event) != before


def test_enabling_a_plugin_changes_the_token(registry, event, shared_cache):
    """``event.plugins`` is part of the token, so plugin fields appear at once.

    Enabling a plugin is a change to ``Event``, not to any of the models the
    receivers watch, which is why it is folded into the token instead.
    """
    before = registry_cache.cache_token(event)
    enable_plugin(event)
    assert registry_cache.cache_token(event) != before


def test_choices_are_not_cached(registry, event, event_questions, shared_cache):
    """A new question option shows up without invalidating anything.

    This is the design constraint the whole strategy rests on: volatile data goes
    behind a callable, not into the field structure.
    """
    from pretix.base.models import QuestionOption

    with scopes_disabled():
        field = _build(registry, event, Base.ORDERPOSITION)["answer.tshirt-size"]
        context = registry.context(event, Base.ORDERPOSITION)
        assert len(list(field.choices(context))) == 4
        QuestionOption.objects.create(
            question=event_questions["tshirt-size"], answer="XXL", position=99
        )
        # Same field object, no rebuild, new option.
        assert len(list(field.choices(context))) == 5


def test_explicit_invalidation(registry, event, shared_cache):
    """``invalidate_event`` is available for callers that change data in bulk."""
    _build(registry, event)
    before = registry_cache.cache_token(event)
    registry_cache.invalidate_event(event.pk)
    assert registry_cache.cache_token(event) != before
    assert registry_cache.get_cached(event, Base.ORDER) is None


def test_invalidation_tolerates_a_missing_id(registry):
    """A receiver firing for an object without the id must not raise."""
    registry_cache.invalidate_event(None)
    registry_cache.invalidate_organizer(None)


# ---------------------------------------------------------------------------
# Bounds and degraded backends
# ---------------------------------------------------------------------------


def test_local_cache_is_bounded(registry, event, shared_cache):
    """The process-local dictionary does not grow without limit.

    A Celery worker touches many events over its lifetime; an unbounded dict
    would be a slow memory leak holding on to closures.
    """
    registry_cache.clear_local_cache()
    for index in range(registry_cache.MAX_ENTRIES + 20):

        class FakeEvent:
            pk = index
            organizer_id = event.organizer_id
            plugins = ""

        registry_cache.set_cached(FakeEvent(), Base.ORDER, {"index": index})
    # Access the private dict on purpose: the bound is the whole point of the
    # test and there is no public way to observe it.
    assert len(registry_cache._LOCAL) == registry_cache.MAX_ENTRIES


def test_dummy_cache_degrades_to_always_rebuild(registry, event, event_questions):
    """With the DummyCache backend nothing is cached -- and nothing is wrong.

    No ``shared_cache`` fixture here, so this runs against pretix' test default.
    Every call gets a fresh token, so every call rebuilds. Slower, never stale;
    that is the deliberate trade-off documented in registry/cache.py.
    """
    first = registry_cache.cache_token(event)
    second = registry_cache.cache_token(event)
    assert first != second

    _build(registry, event)
    with CaptureQueriesContext(connection) as captured:
        fields = _build(registry, event)
    assert len(captured) > 0
    assert "answer.tshirt-size" in fields


def test_receivers_are_connected_once(registry):
    """Importing the module twice must not connect the receivers twice.

    They are connected on import with ``dispatch_uid``s, and the integrator
    imports the module a second time from ``signals.py``.
    """
    from django.db.models.signals import post_save

    registry_cache.connect_invalidation_receivers()
    registry_cache.connect_invalidation_receivers()
    # Django stores (lookup_key, receiver, is_async) triples, and lookup_key is
    # (dispatch_uid or id(receiver), sender_key).
    uids = [
        entry[0][0]
        for entry in post_save.receivers
        if isinstance(entry[0][0], str)
        and entry[0][0].startswith("pretix_custom_reports_registry_cache")
    ]
    assert len(uids) == len(set(uids))
    assert "pretix_custom_reports_registry_cache_question_save" in uids
