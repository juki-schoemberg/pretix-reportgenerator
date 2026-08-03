# Owner: registry-dev (tests/test_registry*.py, ORCHESTRIERUNG.md section 5)
#
# Regression tests for blockers T-002: an aggregated money column lost its
# scale, so one CSV mixed "23.50" (a plain column) with "20.5" (a Subquery sum).
#
# Every assertion here looks at the *characters* of the value, never at the
# number. ``Decimal("23.5") == Decimal("23.50")`` is True in Python, which is
# exactly why the whole existing suite -- which compares money as Decimal --
# stayed green while the exported files were wrong. A test written the obvious
# way would reproduce that blind spot instead of closing it.
"""Money annotations keep two decimal places, on every database backend."""

import pytest
from decimal import Decimal
from django_scopes import scopes_disabled
from pretix.base.models import Order, OrderPayment, OrderPosition, OrderRefund

from pretix_custom_reports.contracts import Base, DataType
from pretix_custom_reports.registry import annotations
from tests import test_registry_support as support
from tests.test_registry_support import make_order

registry = support.registry

#: Money fields that come out of an expression rather than a plain column.
#: ``order.total`` and ``position.price`` are deliberately absent: those are
#: columns, Django already quantises them, and they are the reference the
#: aggregated ones have to match.
AGGREGATED_MONEY_KEYS = (
    ("order.pending_sum", Base.ORDER),
    ("payment.sum_confirmed", Base.ORDER),
    ("refund.sum_done", Base.ORDER),
    ("payment.sum_confirmed", Base.ORDERPOSITION),
    ("refund.sum_done", Base.ORDERPOSITION),
    ("position.net_price", Base.ORDERPOSITION),
)


def scale_of(value):
    """Number of decimal places in the *representation* of *value*.

    ``-Decimal("20.50").as_tuple().exponent`` is 2, ``Decimal("20.5")`` is 1.
    The numeric comparison of the two is ``True``, this one is not.
    """
    assert isinstance(value, Decimal), f"expected a Decimal, got {value!r}"
    return -value.as_tuple().exponent


def annotated_row(registry, event, base, key):
    """Evaluate the single row of *event* with the annotation of *key*."""
    field = registry.resolve(key, event, base)
    assert field is not None, key
    assert field.datatype is DataType.MONEY, key
    mapping = field.annotation(registry.context(event, base))
    if base is Base.ORDER:
        queryset = Order.objects.filter(event=event)
    else:
        queryset = OrderPosition.all.filter(order__event=event)
    return getattr(queryset.annotate(**mapping).get(), field.orm_path)


def money_world(event):
    """An order whose every amount ends in a digit that a lossy path drops.

    ``23.50`` total, ``20.50`` paid, ``3.50`` refunded, ``tax_value`` chosen so
    that ``net_price`` is ``20.00`` -- two trailing zeros, the worst case for a
    backend that strips them.
    """
    order = make_order(event)
    Order.objects.filter(pk=order.pk).update(total=Decimal("23.50"))
    order.refresh_from_db()
    OrderPosition.all.filter(order=order).update(
        price=Decimal("23.50"), tax_value=Decimal("3.50")
    )
    OrderPayment.objects.filter(order=order).update(amount=Decimal("20.50"))
    OrderRefund.objects.create(
        order=order,
        state=OrderRefund.REFUND_STATE_DONE,
        source=OrderRefund.REFUND_SOURCE_ADMIN,
        provider="banktransfer",
        amount=Decimal("3.50"),
        local_id=1,
    )
    return order


@pytest.mark.django_db
@pytest.mark.parametrize("key,base", AGGREGATED_MONEY_KEYS)
def test_an_aggregated_money_column_has_exactly_two_decimal_places(
    registry, event, key, base
):
    """The core of T-002, one field at a time.

    ``Subquery``/``Coalesce``/``Sum``/``F() - F()`` all take the branch of
    Django's SQLite converter that skips quantisation, so before the fix these
    returned ``Decimal("20.5")`` while ``order.total`` next to them returned
    ``Decimal("23.50")``.
    """
    with scopes_disabled():
        money_world(event)
        value = annotated_row(registry, event, base, key)
    assert scale_of(value) == 2, f"{key} rendered as {value!s}"


@pytest.mark.django_db
def test_an_aggregated_money_column_renders_the_same_string_as_a_plain_column(
    registry, event
):
    """The symptom as a reader of the CSV sees it: one row, one format.

    ``order.total`` is a plain column and has always been right. This asserts
    that the annotated amounts print in the same shape -- which is the property
    that makes the export reproducible across backends, since PostgreSQL keeps
    the scale of ``numeric(13, 2)`` through ``SUM`` and SQLite does not.
    """
    with scopes_disabled():
        order = money_world(event)
        rendered = {
            "order.total": str(order.total),
            "payment.sum_confirmed": str(
                annotated_row(registry, event, Base.ORDER, "payment.sum_confirmed")
            ),
            "refund.sum_done": str(
                annotated_row(registry, event, Base.ORDER, "refund.sum_done")
            ),
            "order.pending_sum": str(
                annotated_row(registry, event, Base.ORDER, "order.pending_sum")
            ),
            "position.net_price": str(
                annotated_row(registry, event, Base.ORDERPOSITION, "position.net_price")
            ),
        }
    assert rendered == {
        "order.total": "23.50",
        "payment.sum_confirmed": "20.50",
        "refund.sum_done": "3.50",
        # 23.50 - 20.50 + 3.50
        "order.pending_sum": "6.50",
        # 23.50 - 3.50, the trailing-zero case
        "position.net_price": "20.00",
    }


@pytest.mark.django_db
def test_a_zero_money_annotation_is_zero_with_two_places(registry, event):
    """``Coalesce(..., 0.00)`` on an order without payments or refunds.

    The default of an empty aggregate travels through the same expression, so it
    has to arrive in the same shape. ``Decimal("0")`` would print as ``0``.
    """
    with scopes_disabled():
        order = make_order(event)
        Order.objects.filter(pk=order.pk).update(total=Decimal("0.00"))
        OrderPayment.objects.filter(order=order).delete()
        for key in ("payment.sum_confirmed", "refund.sum_done", "order.pending_sum"):
            value = annotated_row(registry, event, Base.ORDER, key)
            assert str(value) == "0.00", f"{key} rendered as {value!s}"


@pytest.mark.django_db
def test_the_payment_state_field_emits_its_money_aliases_with_scale(registry, event):
    """``computed.payment_state`` re-emits ``pcr_payment_sum``/``pcr_pending_sum``.

    Those two aliases must stay byte-identical to the ones
    ``payment.sum_confirmed`` and ``order.pending_sum`` emit, because the
    compiler merges them into a single ``annotate()``. If only one of the two
    call sites had been fixed, this is where it would show.
    """
    with scopes_disabled():
        money_world(event)
        field = registry.resolve("computed.payment_state", event, Base.ORDER)
        mapping = field.annotation(registry.context(event, Base.ORDER))
        row = Order.objects.filter(event=event).annotate(**mapping).get()
        assert str(getattr(row, annotations.ALIAS_PAYMENT_SUM)) == "20.50"
        assert str(getattr(row, annotations.ALIAS_PENDING_SUM)) == "6.50"
        assert getattr(row, annotations.ALIAS_PAYMENT_STATE) == "partially_paid"


@pytest.mark.django_db
def test_the_merged_mapping_still_collapses_the_shared_money_aliases(registry, event):
    """Two fields, one alias, one expression -- the merge promise of the module.

    ``_payment_sum_coalesced`` exists so that both call sites build the same
    thing. Django raises ``ValueError`` on ``annotate()`` if two annotations of
    the same name disagree, so evaluating the merge is the assertion.
    """
    with scopes_disabled():
        money_world(event)
        context = registry.context(event, Base.ORDER)
        merged = {}
        for key in (
            "payment.sum_confirmed",
            "order.pending_sum",
            "computed.payment_state",
        ):
            merged.update(registry.resolve(key, event, Base.ORDER).annotation(context))
        row = Order.objects.filter(event=event).annotate(**merged).get()
    assert str(getattr(row, annotations.ALIAS_PAYMENT_SUM)) == "20.50"
    assert str(getattr(row, annotations.ALIAS_PENDING_SUM)) == "6.50"


def test_the_money_field_quantises_without_a_database():
    """:class:`~registry.annotations.MoneyField` in isolation.

    A unit test next to the integration ones so that a future reader can see
    what the class does without setting up an order, and so that the widened
    context for an over-wide sum is covered at all -- no fixture can produce a
    number that large through the ORM.
    """
    field = annotations.MoneyField(
        max_digits=annotations.MONEY_MAX_DIGITS,
        decimal_places=annotations.MONEY_DECIMAL_PLACES,
    )
    convert = field.from_db_value

    assert convert(None, None, None) is None
    assert str(convert(Decimal("20.5"), None, None)) == "20.50"
    assert str(convert(Decimal("20.50"), None, None)) == "20.50"
    assert str(convert(Decimal("0"), None, None)) == "0.00"
    assert str(convert(Decimal("-3.5"), None, None)) == "-3.50"
    # A backend that hands back a float instead of a Decimal.
    assert str(convert(20.5, None, None)) == "20.50"
    # Wider than max_digits: must still come back quantised, not raise.
    wide = Decimal("123456789012345678.5")
    assert str(convert(wide, None, None)) == "123456789012345678.50"


def test_the_money_scale_matches_the_pretix_model_fields():
    """Guard against pretix widening its money columns under us.

    ``max_digits``/``decimal_places`` are duplicated into this plugin, so they
    have to be checked against the source rather than remembered. If pretix ever
    moves to four decimal places, this fails before a report starts rounding
    other people's money.
    """
    checks = (
        (Order, "total"),
        (OrderPayment, "amount"),
        (OrderRefund, "amount"),
        (OrderPosition, "price"),
        (OrderPosition, "tax_value"),
    )
    for model, name in checks:
        model_field = model._meta.get_field(name)
        assert model_field.max_digits == annotations.MONEY_MAX_DIGITS, name
        assert model_field.decimal_places == annotations.MONEY_DECIMAL_PLACES, name
