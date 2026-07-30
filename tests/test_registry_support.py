# Owner: registry-dev (tests/test_registry*.py, ORCHESTRIERUNG.md section 5)
#
# Shared helpers and fixtures for the three registry test modules. It carries no
# tests of its own; the name matches tests/test_registry*.py because that is the
# path this agent owns. tests/factories.py, which would be the natural home for
# some of this later, belongs to test-engineer and does not exist yet.
"""Fixtures and helpers shared by the registry tests."""

from typing import Any, Dict

import json
import pathlib
import pytest
from django_scopes import scopes_disabled
from pretix.base.models import (
    EventMetaProperty,
    EventMetaValue,
    Item,
    ItemCategory,
    Order,
    OrderPayment,
    OrderPosition,
    Question,
    QuestionAnswer,
    QuestionOption,
)
from pretix.base.models.orders import InvoiceAddress

from pretix_custom_reports.registry import cache as registry_cache
from pretix_custom_reports.registry.library import EventFieldRegistry

#: App label of the example plugin used to exercise the field signal. Matches
#: the key the golden fixture ``plugin_and_meta_fields.json`` expects.
PLUGIN_APP_LABEL = "pretix_demo"

#: The question identifiers ``tests/fixtures/definitions/_index.json`` promises
#: the test data will have.
QUESTION_IDENTIFIERS = ("tshirt-size", "arrival-date", "newsletter")

#: Meta property the same file promises.
META_PROPERTY = "campaign"

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "definitions"


def load_index() -> Dict[str, Any]:
    """``tests/fixtures/definitions/_index.json`` as a dict."""
    return json.loads((FIXTURE_DIR / "_index.json").read_text(encoding="utf-8"))


def valid_fixture_paths():
    """Every valid definition fixture, ``portable/`` and metadata excluded."""
    return sorted(
        path for path in FIXTURE_DIR.glob("*.json") if not path.name.startswith("_")
    )


def make_questions(event) -> Dict[str, Question]:
    """The three questions the golden fixtures use, with options for the choice.

    Question types are taken from ``_index.json``: choice, date, boolean.
    """
    with scopes_disabled():
        tshirt = Question.objects.create(
            event=event,
            question="T-shirt size",
            identifier="tshirt-size",
            type=Question.TYPE_CHOICE,
        )
        for position, label in enumerate(("S", "M", "L", "XL")):
            QuestionOption.objects.create(
                question=tshirt, answer=label, position=position
            )
        arrival = Question.objects.create(
            event=event,
            question="Day of arrival",
            identifier="arrival-date",
            type=Question.TYPE_DATE,
        )
        newsletter = Question.objects.create(
            event=event,
            question="Newsletter opt-in",
            identifier="newsletter",
            type=Question.TYPE_BOOLEAN,
        )
    return {
        "tshirt-size": tshirt,
        "arrival-date": arrival,
        "newsletter": newsletter,
    }


def make_meta_property(organizer, name: str = META_PROPERTY, default: str = ""):
    """An ``EventMetaProperty`` on the organizer."""
    return EventMetaProperty.objects.create(
        organizer=organizer, name=name, default=default
    )


def set_meta_value(event, prop, value: str):
    """Give *event* an explicit value for *prop*."""
    with scopes_disabled():
        return EventMetaValue.objects.create(event=event, property=prop, value=value)


def make_order(event, code: str = "ORDER1", **kwargs) -> Order:
    """A minimal paid order with one item and one position."""
    from django.utils.timezone import now

    with scopes_disabled():
        category = ItemCategory.objects.create(event=event, name="Tickets")
        item = Item.objects.create(
            event=event,
            category=category,
            name="Regular ticket",
            internal_name="regular",
            default_price=23,
            admission=True,
        )
        order = Order.objects.create(
            event=event,
            code=code,
            status=Order.STATUS_PAID,
            email="buyer@example.org",
            datetime=now(),
            expires=now(),
            total=23,
            sales_channel=event.organizer.sales_channels.get(identifier="web"),
            **kwargs,
        )
        InvoiceAddress.objects.create(order=order, company="ACME", city="Berlin")
        OrderPosition.objects.create(
            order=order,
            item=item,
            variation=None,
            price=23,
            tax_rate=0,
            tax_value=0,
            attendee_name_parts={"_legacy": "Alice Example"},
            positionid=1,
        )
        OrderPayment.objects.create(
            order=order,
            provider="banktransfer",
            state=OrderPayment.PAYMENT_STATE_CONFIRMED,
            amount=23,
            payment_date=now(),
        )
    return order


def answer(position, question, value: str) -> QuestionAnswer:
    """Store *value* as the answer of *position* to *question*."""
    with scopes_disabled():
        return QuestionAnswer.objects.create(
            orderposition=position, question=question, answer=value
        )


# ---------------------------------------------------------------------------
# The example plugin
# ---------------------------------------------------------------------------


def fake_plugin_app(app_label: str = PLUGIN_APP_LABEL):
    """A stand-in for another plugin's ``AppConfig``.

    ``EventPluginSignal.connect`` refuses a receiver that does not belong to an
    app with ``PretixPluginMeta``, and ``is_app_active`` then checks that the app
    is enabled for the event (``pretix/base/signals.py:92-141, 261-274``).
    Installing a second real package inside a test run is not possible, so this
    uses the ``__mocked_app`` escape hatch pretix itself provides
    (``pretix/base/signals.py:70-71``) and uses in
    ``src/tests/base/test_datasync.py:180-191``.
    """

    class App:
        name = app_label

        class PretixPluginMeta:
            name = "Demo plugin"
            version = "1.0.0"

    return App


def attach_mocked_app(function, app_label: str = PLUGIN_APP_LABEL):
    """Pretend *function* is defined inside the plugin *app_label*."""
    function.__mocked_app = fake_plugin_app(app_label)
    return function


def enable_plugin(event, app_label: str = PLUGIN_APP_LABEL) -> None:
    """Enable a plugin for *event* on top of the ones already enabled."""
    active = [name for name in (event.plugins or "").split(",") if name]
    if app_label not in active:
        active.append(app_label)
    event.plugins = ",".join(active)
    with scopes_disabled():
        event.save(update_fields=["plugins"])
    registry_cache.clear_local_cache()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():
    """A fresh :class:`EventFieldRegistry` with an empty process-local cache.

    A new instance rather than ``field_registry()`` so one test cannot leak a
    cached field table into the next.
    """
    registry_cache.clear_local_cache()
    yield EventFieldRegistry()
    registry_cache.clear_local_cache()


@pytest.fixture
def event_questions(event):
    """The three golden-fixture questions on the shared ``event`` fixture."""
    return make_questions(event)


@pytest.fixture
def event_meta(event):
    """The ``campaign`` meta property, with a value set on the event."""
    prop = make_meta_property(event.organizer, default="")
    set_meta_value(event, prop, "summer")
    registry_cache.clear_local_cache()
    return prop
