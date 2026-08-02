# Owner from wave 3 on: test-engineer (see ORCHESTRIERUNG.md section 5)
"""Deterministic test data: a hand-authored reference event and a bulk builder.

Two very different jobs live in this module, and mixing them up is the classic
way a test suite ends up proving nothing:

``build_reference_world(event)``
    A **small, hand-authored** event whose every number can be recomputed with a
    pencil. It is the basis of the correctness tests in
    ``tests/test_integration.py``: six orders covering every ``Order.status``,
    partial payments, an overpayment, a refund, a canceled position, a test-mode
    order, check-ins including an exit scan and a failed scan, a voucher, an
    invoice address, product variations and one question of **every**
    ``Question.type``. The ledger below states what it contains; the *expected
    results* are written out again, independently, in the test module. That
    duplication is on purpose -- an expectation imported from the factory would
    only ever prove that the factory agrees with itself.

``build_bulk(event, ...)``
    Synthetic mass data for ``tests/test_performance.py``. Built with
    ``bulk_create`` and a seeded ``random.Random``, so 100.000 positions take
    seconds rather than an hour and two runs produce byte-identical data.

Determinism
-----------

Nothing here calls ``now()``, ``uuid4()`` or an unseeded ``random``. Every
timestamp is derived from :data:`EPOCH`, every "random" choice comes out of
``random.Random(SEED)``, and every generated secret is a formatted counter. A
report test that is flaky on the last day of a month is worse than no test.

pretix specifics that bite (verified against 2026.6.0, see
docs/pretix-api-notes.md)
--------------------------------------------------------------------------

* ``OrderPosition.objects`` hides canceled positions -- a canceled position has
  to be created through ``OrderPosition.all`` (section 6.2, pitfall 1).
* ``Checkin.objects`` is a manager that filters ``successful=True``; a failed
  scan needs ``Checkin.all`` (section 6.10).
* Payments only count towards "paid" in states ``confirmed`` and ``refunded``,
  refunds only in ``done``/``transit``/``created`` (section 6.9). The world
  below deliberately contains payments and refunds *outside* those sets so a
  report that ignores the distinction produces visibly wrong sums.
* Every scoped model has to be created inside ``scopes_disabled()``; the hook
  pretix uses in its own test suite is not active for out-of-tree plugins
  (see ``tests/conftest.py``).

The ledger of :func:`build_reference_world`
-------------------------------------------

Money in EUR, positions listed as ``price``. "live" = not canceled.

===== ========= ====== ================================ ================== =========
code  status    total  positions                        payments           refunds
===== ========= ====== ================================ ================== =========
PAID1 paid      33.00  23.00 + 10.00 live, 10.00 canc.  33.00 confirmed    --
PART2 pending   46.00  23.00 + 23.00 live               20.00 confirmed,   --
                                                        5.00 pending
PEND3 pending   23.00  23.00 live (voucher)             --                 --
EXPI4 expired   15.00  15.00 live                       --                 --
CANC5 canceled  23.00  23.00 canceled                   23.00 confirmed    23.00 done
OVER6 paid      23.00  23.00 live                       30.00 confirmed    --
TEST7 paid *    7.00   7.00 live                        7.00 confirmed     --
===== ========= ====== ================================ ================== =========

``*`` = ``testmode=True``.

Check-ins hang off the first live position of PAID1: two successful entries, one
exit, one failed entry. Answers hang off the live positions of PAID1 and PART2.
"""

from typing import Any, Dict, List, Optional, Sequence

import datetime as dt
import random
from dataclasses import dataclass, field as dataclass_field
from decimal import Decimal
from django.utils.timezone import make_aware
from django_scopes import scopes_disabled
from pretix.base.models import (
    Checkin,
    CheckinList,
    Event,
    InvoiceAddress,
    Item,
    ItemCategory,
    ItemVariation,
    Order,
    OrderPayment,
    OrderPosition,
    OrderRefund,
    Organizer,
    Question,
    QuestionAnswer,
    QuestionOption,
    SubEvent,
    Voucher,
)

__all__ = [
    "EPOCH",
    "PLUGIN",
    "SEED",
    "BulkData",
    "Catalog",
    "World",
    "add_answer",
    "add_checkin",
    "add_invoice_address",
    "add_payment",
    "add_position",
    "add_refund",
    "build_bulk",
    "build_reference_world",
    "make_catalog",
    "make_checkin_list",
    "make_event",
    "make_order",
    "make_organizer",
    "make_questions",
    "make_subevents",
    "make_voucher",
]

#: Seed for every pseudo-random decision in this module.
SEED = 20260801

#: Module name that has to appear in ``Event.plugins`` for our views and the
#: registry to consider themselves active.
PLUGIN = "pretix_custom_reports"

#: All timestamps are offsets from this instant, in UTC. Deliberately in the
#: past and far away from a DST boundary, so that a test which does *not* care
#: about time zones cannot accidentally depend on one.
EPOCH = dt.datetime(2026, 5, 4, 9, 0, 0, tzinfo=dt.timezone.utc)


def _at(**delta: float) -> dt.datetime:
    """An instant relative to :data:`EPOCH`."""
    return EPOCH + dt.timedelta(**delta)


# ---------------------------------------------------------------------------
# Organizer, event, subevents
# ---------------------------------------------------------------------------


def make_organizer(slug: str = "dummy", name: str = "Dummy") -> Organizer:
    return Organizer.objects.create(name=name, slug=slug)


def make_event(
    organizer: Organizer,
    slug: str = "dummy",
    name: str = "Dummy Event",
    *,
    date_from: Optional[dt.datetime] = None,
    timezone: Optional[str] = None,
    has_subevents: bool = False,
    plugins: str = PLUGIN,
    live: bool = True,
) -> Event:
    """An event with this plugin enabled.

    :param timezone: written to ``event.settings.timezone``. That is where
        ``Event.timezone`` reads from (pretix/base/models/event.py:233-235) and
        therefore the only knob that changes what "today" means for a relative
        date filter.
    """
    with scopes_disabled():
        event = Event.objects.create(
            organizer=organizer,
            name=name,
            slug=slug,
            date_from=date_from if date_from is not None else _at(days=30),
            plugins=plugins,
            live=live,
            has_subevents=has_subevents,
        )
        if timezone is not None:
            event.settings.timezone = timezone
        return event


def make_subevents(event: Event, count: int = 2) -> List[SubEvent]:
    """*count* dates of an event series, one week apart."""
    with scopes_disabled():
        return [
            SubEvent.objects.create(
                event=event,
                name=f"Date {index + 1}",
                date_from=_at(days=30 + 7 * index),
                location=f"Room {index + 1}",
                active=True,
                is_public=True,
            )
            for index in range(count)
        ]


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


@dataclass
class Catalog:
    """Products, categories and variations of the reference event."""

    tickets: ItemCategory
    extras: ItemCategory
    ticket: Item
    workshop: Item
    merch: Item
    beginner: ItemVariation
    advanced: ItemVariation


def make_catalog(event: Event) -> Catalog:
    """Three products, two categories, two variations, one product uncategorised.

    ``merch`` deliberately has **no** category so that ``item.category`` has a
    ``None`` to render -- an empty relation is a different code path from an
    empty string and is where a naive attribute renderer raises.
    """
    with scopes_disabled():
        tickets = ItemCategory.objects.create(event=event, name="Tickets", position=0)
        extras = ItemCategory.objects.create(event=event, name="Extras", position=1)
        ticket = Item.objects.create(
            event=event,
            category=tickets,
            name="Admission ticket",
            internal_name="ticket",
            default_price=Decimal("23.00"),
            admission=True,
            position=0,
        )
        workshop = Item.objects.create(
            event=event,
            category=extras,
            name="Workshop",
            internal_name="workshop",
            default_price=Decimal("10.00"),
            admission=False,
            position=1,
        )
        merch = Item.objects.create(
            event=event,
            category=None,
            name="T-shirt",
            internal_name="merch",
            default_price=Decimal("15.00"),
            admission=False,
            position=2,
        )
        beginner = ItemVariation.objects.create(
            item=workshop, value="Beginner", default_price=Decimal("10.00"), position=0
        )
        advanced = ItemVariation.objects.create(
            item=workshop, value="Advanced", default_price=Decimal("15.00"), position=1
        )
        return Catalog(
            tickets=tickets,
            extras=extras,
            ticket=ticket,
            workshop=workshop,
            merch=merch,
            beginner=beginner,
            advanced=advanced,
        )


# ---------------------------------------------------------------------------
# Questions -- one of every type
# ---------------------------------------------------------------------------

#: ``(identifier, question text, Question.type)`` for every type pretix has.
#: The identifiers are spelled with dashes because ``__`` is banned in a field
#: key (ADR 0001 section 2) and because the dash/underscore difference is what
#: the importer's identifier matching is for.
QUESTION_SPECS: Sequence[Sequence[str]] = (
    ("tshirt-size", "T-shirt size", Question.TYPE_CHOICE),
    ("diet", "Dietary requirements", Question.TYPE_CHOICE_MULTIPLE),
    ("nickname", "Nickname", Question.TYPE_STRING),
    ("notes", "Anything we should know?", Question.TYPE_TEXT),
    ("companions", "Number of companions", Question.TYPE_NUMBER),
    ("newsletter", "Subscribe to the newsletter", Question.TYPE_BOOLEAN),
    ("birthdate", "Date of birth", Question.TYPE_DATE),
    ("arrival-time", "Time of arrival", Question.TYPE_TIME),
    ("arrival", "Arrival", Question.TYPE_DATETIME),
    ("home-country", "Country of residence", Question.TYPE_COUNTRYCODE),
    ("phone", "Phone number", Question.TYPE_PHONENUMBER),
    ("passport", "Passport scan", Question.TYPE_FILE),
)

#: Options of the two choice questions, ``{question identifier: (labels...)}``.
QUESTION_OPTIONS: Dict[str, Sequence[str]] = {
    "tshirt-size": ("S", "M", "L", "XL"),
    "diet": ("Vegan", "Gluten-free"),
}


def make_questions(event: Event, items: Sequence[Item] = ()) -> Dict[str, Question]:
    """One question of every ``Question.type``, keyed by identifier.

    ``QuestionOption`` rows are created for the two choice types. Their
    ``identifier`` is set explicitly: an option identifier is what a filter value
    is matched against when a definition is imported into another event, so
    leaving it to the auto-generator would make the round-trip tests depend on a
    random string.
    """
    created: Dict[str, Question] = {}
    with scopes_disabled():
        for position, (identifier, text, qtype) in enumerate(QUESTION_SPECS):
            question = Question.objects.create(
                event=event,
                question=text,
                identifier=identifier,
                type=qtype,
                position=position,
                required=False,
            )
            if items:
                question.items.set(list(items))
            for index, label in enumerate(QUESTION_OPTIONS.get(identifier, ())):
                QuestionOption.objects.create(
                    question=question,
                    answer=label,
                    identifier=f"{identifier}-{label.lower().replace('-', '')}",
                    position=index,
                )
            created[identifier] = question
    return created


# ---------------------------------------------------------------------------
# Orders and everything hanging off them
# ---------------------------------------------------------------------------


def make_order(
    event: Event,
    code: str,
    status: str,
    total: Decimal,
    *,
    email: Optional[str] = None,
    testmode: bool = False,
    placed: Optional[dt.datetime] = None,
    expires: Optional[dt.datetime] = None,
    comment: str = "",
    locale: str = "en",
    sales_channel: Any = None,
    cancellation_date: Optional[dt.datetime] = None,
) -> Order:
    with scopes_disabled():
        if sales_channel is None:
            sales_channel = event.organizer.sales_channels.get(identifier="web")
        return Order.objects.create(
            event=event,
            code=code,
            status=status,
            email=email if email is not None else f"{code.lower()}@example.org",
            testmode=testmode,
            locale=locale,
            sales_channel=sales_channel,
            datetime=placed if placed is not None else _at(days=-10),
            expires=expires if expires is not None else _at(days=10),
            total=Decimal(total),
            comment=comment,
            cancellation_date=cancellation_date,
        )


def add_position(
    order: Order,
    item: Item,
    price: Decimal,
    positionid: int,
    *,
    variation: Optional[ItemVariation] = None,
    subevent: Optional[SubEvent] = None,
    canceled: bool = False,
    voucher: Optional[Voucher] = None,
    attendee_name: Optional[str] = None,
    attendee_email: Optional[str] = None,
    tax_rate: Decimal = Decimal("19.00"),
    tax_value: Optional[Decimal] = None,
    addon_to: Optional[OrderPosition] = None,
) -> OrderPosition:
    """One position. ``canceled=True`` goes through ``OrderPosition.all``.

    ``tax_value`` defaults to the gross-inclusive VAT for *tax_rate*, rounded to
    cents, so that ``position.net_price`` (``price - tax_value``) is a number a
    reader can check rather than whatever ``_calculate_tax`` decides today.
    """
    manager = OrderPosition.all if canceled else OrderPosition.objects
    if tax_value is None:
        gross = Decimal(price)
        tax_value = (gross - gross / (1 + Decimal(tax_rate) / 100)).quantize(
            Decimal("0.01")
        )
    with scopes_disabled():
        return manager.create(
            order=order,
            item=item,
            variation=variation,
            subevent=subevent,
            price=Decimal(price),
            positionid=positionid,
            canceled=canceled,
            voucher=voucher,
            attendee_name_parts=({"_legacy": attendee_name} if attendee_name else {}),
            attendee_email=attendee_email,
            tax_rate=Decimal(tax_rate),
            tax_value=Decimal(tax_value),
            addon_to=addon_to,
        )


def add_answer(
    position: OrderPosition,
    question: Question,
    answer: str,
    options: Sequence[QuestionOption] = (),
) -> QuestionAnswer:
    """An answer. For choice questions, pass the ``QuestionOption`` rows as well.

    pretix stores the *label* in ``QuestionAnswer.answer`` and additionally links
    the options; the registry reads the text column, so both have to agree or the
    fixture would not reflect production data.
    """
    with scopes_disabled():
        row = QuestionAnswer.objects.create(
            orderposition=position, question=question, answer=answer
        )
        if options:
            row.options.add(*options)
        return row


def add_payment(
    order: Order,
    amount: Decimal,
    state: str = OrderPayment.PAYMENT_STATE_CONFIRMED,
    *,
    provider: str = "banktransfer",
    payment_date: Optional[dt.datetime] = None,
    local_id: Optional[int] = None,
) -> OrderPayment:
    with scopes_disabled():
        return OrderPayment.objects.create(
            order=order,
            state=state,
            amount=Decimal(amount),
            provider=provider,
            payment_date=payment_date if payment_date is not None else _at(days=-9),
            local_id=(local_id if local_id is not None else order.payments.count() + 1),
        )


def add_refund(
    order: Order,
    amount: Decimal,
    state: str = OrderRefund.REFUND_STATE_DONE,
    *,
    provider: str = "banktransfer",
    source: str = OrderRefund.REFUND_SOURCE_ADMIN,
    execution_date: Optional[dt.datetime] = None,
    local_id: Optional[int] = None,
) -> OrderRefund:
    with scopes_disabled():
        return OrderRefund.objects.create(
            order=order,
            state=state,
            source=source,
            amount=Decimal(amount),
            provider=provider,
            execution_date=(
                execution_date if execution_date is not None else _at(days=-8)
            ),
            local_id=(local_id if local_id is not None else order.refunds.count() + 1),
        )


def make_checkin_list(event: Event, name: str = "Entry") -> CheckinList:
    with scopes_disabled():
        return CheckinList.objects.create(event=event, name=name, all_products=True)


def add_checkin(
    position: OrderPosition,
    checkin_list: CheckinList,
    *,
    at: Optional[dt.datetime] = None,
    type: str = Checkin.TYPE_ENTRY,
    successful: bool = True,
    error_reason: Optional[str] = None,
) -> Checkin:
    """A scan. ``successful=False`` goes through ``Checkin.all``."""
    manager = Checkin.objects if successful else Checkin.all
    with scopes_disabled():
        return manager.create(
            position=position,
            list=checkin_list,
            datetime=at if at is not None else _at(days=30, hours=1),
            type=type,
            successful=successful,
            error_reason=error_reason,
        )


def make_voucher(
    event: Event,
    code: str,
    *,
    item: Optional[Item] = None,
    tag: str = "",
    comment: str = "",
    max_usages: int = 1,
) -> Voucher:
    with scopes_disabled():
        return Voucher.objects.create(
            event=event,
            code=code,
            item=item,
            tag=tag,
            comment=comment,
            max_usages=max_usages,
            valid_until=_at(days=60),
        )


def add_invoice_address(order: Order, **kwargs: Any) -> InvoiceAddress:
    defaults = {
        "name_parts": {"_legacy": "Ada Lovelace"},
        "company": "Analytical Engines Ltd",
        "street": "1 Difference Lane",
        "zipcode": "12345",
        "city": "London",
        "country": "GB",
        "is_business": True,
        "internal_reference": "PO-4711",
    }
    defaults.update(kwargs)
    with scopes_disabled():
        return InvoiceAddress.objects.create(order=order, **defaults)


# ---------------------------------------------------------------------------
# The reference world
# ---------------------------------------------------------------------------


@dataclass
class World:
    """Everything :func:`build_reference_world` created, addressable by name."""

    event: Event
    catalog: Catalog
    questions: Dict[str, Question]
    options: Dict[str, Dict[str, QuestionOption]]
    orders: Dict[str, Order]
    positions: Dict[str, List[OrderPosition]] = dataclass_field(default_factory=dict)
    checkin_list: Optional[CheckinList] = None
    voucher: Optional[Voucher] = None

    def order(self, code: str) -> Order:
        return self.orders[code]

    def position(self, code: str, positionid: int) -> OrderPosition:
        for position in self.positions[code]:
            if position.positionid == positionid:
                return position
        raise KeyError(f"{code}-{positionid}")

    def option(self, question: str, label: str) -> QuestionOption:
        return self.options[question][label]


def build_reference_world(event: Event) -> World:
    """The hand-authored event described in the module docstring.

    Every value in here was chosen so that at least one report column would be
    wrong if the code under test took a shortcut:

    * PAID1's canceled 10.00 position separates ``include_canceled_positions``
      from "sum of the order total",
    * PART2's ``pending`` payment separates "amount paid" from "sum of all
      payments",
    * CANC5 is canceled *and* fully refunded, which makes its outstanding amount
      0.00 -- a report that subtracts payments from ``total`` without the
      canceled special case says -23.00,
    * OVER6 is overpaid, the only way to see the fourth payment state,
    * TEST7 is in test mode and must be absent unless asked for.
    """
    catalog = make_catalog(event)
    questions = make_questions(
        event, items=[catalog.ticket, catalog.workshop, catalog.merch]
    )
    # ``QuestionOption.answer`` is an ``I18nCharField`` and its value is a
    # ``LazyI18nString``, which is not hashable -- hence ``str()`` around it.
    options = {
        identifier: {
            str(option.answer): option for option in questions[identifier].options.all()
        }
        for identifier in QUESTION_OPTIONS
    }
    checkin_list = make_checkin_list(event)
    voucher = make_voucher(
        event, code="EARLYBIRD", item=catalog.ticket, tag="promo", comment="press"
    )

    orders: Dict[str, Order] = {}
    positions: Dict[str, List[OrderPosition]] = {}

    # -- PAID1: paid in full, one canceled position, all the answers ---------
    paid1 = make_order(
        event,
        "PAID1",
        Order.STATUS_PAID,
        Decimal("33.00"),
        placed=_at(days=-10),
        comment="first",
    )
    add_invoice_address(paid1)
    p1_1 = add_position(
        paid1,
        catalog.ticket,
        Decimal("23.00"),
        1,
        attendee_name="Ada Lovelace",
        attendee_email="ada@example.org",
    )
    p1_2 = add_position(
        paid1,
        catalog.workshop,
        Decimal("10.00"),
        2,
        variation=catalog.beginner,
    )
    p1_3 = add_position(paid1, catalog.workshop, Decimal("10.00"), 3, canceled=True)
    add_payment(paid1, Decimal("33.00"))
    orders["PAID1"] = paid1
    positions["PAID1"] = [p1_1, p1_2, p1_3]

    # Answers: the live positions carry one answer of every type between them,
    # the canceled one answers the T-shirt question too so that
    # include_canceled_positions is visible in an aggregate.
    add_answer(p1_1, questions["tshirt-size"], "L", [options["tshirt-size"]["L"]])
    add_answer(
        p1_1,
        questions["diet"],
        "Vegan, Gluten-free",
        [options["diet"]["Vegan"], options["diet"]["Gluten-free"]],
    )
    add_answer(p1_1, questions["nickname"], "Ada")
    add_answer(p1_1, questions["notes"], "Two lines\nof text")
    add_answer(p1_1, questions["companions"], "2")
    add_answer(p1_1, questions["newsletter"], "True")
    add_answer(p1_1, questions["birthdate"], "1990-06-15")
    add_answer(p1_1, questions["arrival-time"], "14:30:00")
    add_answer(p1_1, questions["arrival"], "2026-06-03T14:30:00+02:00")
    add_answer(p1_1, questions["home-country"], "GB")
    add_answer(p1_1, questions["phone"], "+441234567890")
    add_answer(p1_1, questions["passport"], "file://passport.pdf")
    add_answer(p1_2, questions["tshirt-size"], "XL", [options["tshirt-size"]["XL"]])
    add_answer(p1_3, questions["tshirt-size"], "S", [options["tshirt-size"]["S"]])

    add_checkin(p1_1, checkin_list, at=_at(days=30, hours=1))
    add_checkin(p1_1, checkin_list, at=_at(days=30, hours=5))
    add_checkin(p1_1, checkin_list, at=_at(days=30, hours=3), type=Checkin.TYPE_EXIT)
    add_checkin(
        p1_1,
        checkin_list,
        at=_at(days=30, hours=6),
        successful=False,
        error_reason=Checkin.REASON_ALREADY_REDEEMED,
    )

    # -- PART2: partially paid, one payment in a state that does not count ---
    part2 = make_order(
        event,
        "PART2",
        Order.STATUS_PENDING,
        Decimal("46.00"),
        placed=_at(days=-5),
    )
    p2_1 = add_position(part2, catalog.ticket, Decimal("23.00"), 1)
    p2_2 = add_position(part2, catalog.ticket, Decimal("23.00"), 2)
    add_payment(part2, Decimal("20.00"), OrderPayment.PAYMENT_STATE_CONFIRMED)
    add_payment(part2, Decimal("5.00"), OrderPayment.PAYMENT_STATE_PENDING)
    add_answer(p2_1, questions["tshirt-size"], "M", [options["tshirt-size"]["M"]])
    add_answer(p2_1, questions["birthdate"], "2010-06-15")
    orders["PART2"] = part2
    positions["PART2"] = [p2_1, p2_2]

    # -- PEND3: nothing paid, bought with a voucher -------------------------
    pend3 = make_order(
        event,
        "PEND3",
        Order.STATUS_PENDING,
        Decimal("23.00"),
        placed=_at(days=-3),
    )
    p3_1 = add_position(pend3, catalog.ticket, Decimal("23.00"), 1, voucher=voucher)
    orders["PEND3"] = pend3
    positions["PEND3"] = [p3_1]

    # -- EXPI4: expired ------------------------------------------------------
    expi4 = make_order(
        event,
        "EXPI4",
        Order.STATUS_EXPIRED,
        Decimal("15.00"),
        placed=_at(days=-2),
        expires=_at(days=-1),
    )
    p4_1 = add_position(expi4, catalog.merch, Decimal("15.00"), 1)
    orders["EXPI4"] = expi4
    positions["EXPI4"] = [p4_1]

    # -- CANC5: canceled and refunded ---------------------------------------
    canc5 = make_order(
        event,
        "CANC5",
        Order.STATUS_CANCELED,
        Decimal("23.00"),
        placed=_at(days=-8),
        cancellation_date=_at(days=-7),
    )
    p5_1 = add_position(canc5, catalog.ticket, Decimal("23.00"), 1, canceled=True)
    add_payment(canc5, Decimal("23.00"))
    add_refund(canc5, Decimal("23.00"))
    orders["CANC5"] = canc5
    positions["CANC5"] = [p5_1]

    # -- OVER6: overpaid -----------------------------------------------------
    over6 = make_order(
        event,
        "OVER6",
        Order.STATUS_PAID,
        Decimal("23.00"),
        placed=_at(days=-1),
    )
    p6_1 = add_position(over6, catalog.ticket, Decimal("23.00"), 1)
    add_payment(over6, Decimal("30.00"))
    orders["OVER6"] = over6
    positions["OVER6"] = [p6_1]

    # -- TEST7: test mode ----------------------------------------------------
    test7 = make_order(
        event,
        "TEST7",
        Order.STATUS_PAID,
        Decimal("7.00"),
        testmode=True,
        placed=_at(hours=-1),
    )
    p7_1 = add_position(test7, catalog.merch, Decimal("7.00"), 1)
    add_payment(test7, Decimal("7.00"))
    orders["TEST7"] = test7
    positions["TEST7"] = [p7_1]

    return World(
        event=event,
        catalog=catalog,
        questions=questions,
        options=options,
        orders=orders,
        positions=positions,
        checkin_list=checkin_list,
        voucher=voucher,
    )


# ---------------------------------------------------------------------------
# Bulk data for the performance tests
# ---------------------------------------------------------------------------


@dataclass
class BulkData:
    """What :func:`build_bulk` created, plus the counts it promised."""

    orders: int
    positions: int
    answers: int
    payments: int
    items: List[Item]
    question: Optional[Question] = None


def build_bulk(
    event: Event,
    *,
    orders: int = 1000,
    positions_per_order: int = 2,
    seed: int = SEED,
    with_answers: bool = False,
    with_payments: bool = False,
    batch_size: int = 2000,
    code_prefix: str = "B",
) -> BulkData:
    """Synthetic mass data, created with ``bulk_create``.

    ``bulk_create`` skips ``Model.save()``, which for ``OrderPosition`` means the
    ticket secret, the pseudonymisation id, the denormalised organizer and the
    tax fields are **not** filled in (pretix/base/models/orders.py). Every one of
    them is therefore set here explicitly, deterministically, from a counter --
    a random secret would make two runs of the same performance test produce
    different data and would defeat the seed.

    Payments and answers are optional because they are what makes the difference
    between a narrow and a wide report measurable: without them the aggregate
    subqueries have nothing to aggregate and the timing says nothing.

    :param code_prefix: ``Order.code`` is unique per **organizer**, not per
        event, and so are the ticket secrets. Two bulk events under one organizer
        therefore need different prefixes.
    """
    rng = random.Random(seed)
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        category = ItemCategory.objects.create(event=event, name="Bulk", position=0)
        items = [
            Item.objects.create(
                event=event,
                category=category,
                name=f"Bulk item {index}",
                internal_name=f"bulk-{index}",
                default_price=Decimal("10.00") + index,
                position=index,
            )
            for index in range(4)
        ]
        question = None
        if with_answers:
            question = Question.objects.create(
                event=event,
                question="Bulk question",
                identifier="bulk-question",
                type=Question.TYPE_STRING,
                position=0,
            )

        statuses = [
            Order.STATUS_PAID,
            Order.STATUS_PENDING,
            Order.STATUS_EXPIRED,
            Order.STATUS_CANCELED,
        ]
        order_rows = []
        for index in range(orders):
            total = Decimal("10.00") * positions_per_order + index % 7
            order_rows.append(
                Order(
                    event=event,
                    organizer_id=event.organizer_id,
                    code=f"{code_prefix}{index:06X}",
                    status=statuses[index % len(statuses)],
                    email=f"bulk{index}@example.org",
                    locale="en",
                    testmode=(index % 97 == 0),
                    sales_channel=channel,
                    datetime=EPOCH - dt.timedelta(minutes=index),
                    expires=EPOCH + dt.timedelta(days=14),
                    total=total,
                    comment="",
                )
            )
        Order.objects.bulk_create(order_rows, batch_size=batch_size)
        stored = list(
            Order.objects.filter(event=event)
            .order_by("pk")
            .values_list("pk", flat=True)
        )

        position_rows = []
        counter = 0
        for order_pk in stored:
            for slot in range(positions_per_order):
                counter += 1
                item = items[rng.randrange(len(items))]
                price = Decimal("10.00") + rng.randrange(0, 40)
                tax_value = (price * Decimal("0.19") / Decimal("1.19")).quantize(
                    Decimal("0.01")
                )
                position_rows.append(
                    OrderPosition(
                        order_id=order_pk,
                        organizer_id=event.organizer_id,
                        item=item,
                        price=price,
                        tax_rate=Decimal("19.00"),
                        tax_value=tax_value,
                        positionid=slot + 1,
                        canceled=(counter % 23 == 0),
                        secret=f"{code_prefix}bulk{counter:012d}",
                        pseudonymization_id=f"{code_prefix}P{counter:014d}",
                    )
                )
        OrderPosition.all.bulk_create(position_rows, batch_size=batch_size)

        answers = 0
        if with_answers:
            position_pks = list(
                OrderPosition.all.filter(order__event=event)
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            answer_rows = [
                QuestionAnswer(
                    orderposition_id=pk,
                    question=question,
                    answer=f"answer-{index % 50}",
                )
                for index, pk in enumerate(position_pks)
                if index % 2 == 0
            ]
            QuestionAnswer.objects.bulk_create(answer_rows, batch_size=batch_size)
            answers = len(answer_rows)

        payments = 0
        if with_payments:
            payment_rows = [
                OrderPayment(
                    order_id=order_pk,
                    local_id=1,
                    state=OrderPayment.PAYMENT_STATE_CONFIRMED,
                    amount=Decimal("10.00"),
                    provider="banktransfer",
                    payment_date=EPOCH,
                )
                for order_pk in stored
            ]
            OrderPayment.objects.bulk_create(payment_rows, batch_size=batch_size)
            payments = len(payment_rows)

        return BulkData(
            orders=len(stored),
            positions=len(position_rows),
            answers=answers,
            payments=payments,
            items=items,
            question=question,
        )


def aware(naive: dt.datetime, tz: Any) -> dt.datetime:
    """``make_aware`` under a name that says which timezone is meant.

    Used by the time-zone tests, which have to build instants in the *event's*
    zone rather than in the server's.
    """
    return make_aware(naive, tz)
