"""Lazy value lists for the editor's filter widgets.

Owner: registry-dev.

``ReportField.choices`` is a callable, evaluated per event and only when someone
actually looks at the field. That is not an optimisation, it is what keeps the
cached field table free of volatile data: a new product, a renamed category or a
new voucher tag changes the *choices*, not the field, so nothing has to be
invalidated (docs/adr/0002-registry.md).

Every function here takes a :class:`~pretix_custom_reports.contracts.FieldContext`
and returns ``[(value, label), ...]`` with JSON-serialisable values -- they end
up in a stored definition.

Two rules:

* **Values are never primary keys.** Products are addressed by internal name,
  categories and dates by name, seats by zone. A definition must survive being
  exported and imported into another event (ADR 0001 section 3.1), and a name
  can be matched there while a primary key cannot.
* **Every query is restricted to ``ctx.event``.** ``Seat`` in particular has no
  ``ScopedManager`` at all (docs/pretix-api-notes.md section 6.8), so the
  ``event=`` filter is the only thing keeping it in bounds.
"""

from typing import Any, List, Sequence, Tuple

from django.utils.translation import gettext_lazy as _
from django_countries import countries
from pretix.base.models import (
    Discount,
    Item,
    ItemCategory,
    ItemVariation,
    Order,
    SalesChannel,
    Seat,
    SubEvent,
    Voucher,
)

from pretix_custom_reports.contracts import FieldContext

__all__ = [
    "MAX_CHOICES",
    "boolean_choices",
    "category_choices",
    "country_choices",
    "discount_choices",
    "item_internal_name_choices",
    "item_name_choices",
    "locale_choices",
    "order_status_choices",
    "sales_channel_choices",
    "seat_zone_choices",
    "subevent_name_choices",
    "variation_choices",
    "voucher_tag_choices",
]

#: Upper bound for any list derived from event data. An event with 20.000
#: products must not turn the editor into a 20.000 entry ``<select>``, and an
#: unbounded list would be a cheap way to make the field library expensive.
#: The filter widget falls back to free text input when the list is truncated,
#: which is why cutting it off is safe rather than lossy.
MAX_CHOICES = 500


def _pairs(values: Sequence[Any]) -> List[Tuple[str, str]]:
    """De-duplicate, drop empties, sort, and return ``(value, value)`` pairs.

    Value and label are identical on purpose for every name-based list: the
    label *is* the value, so an imported definition can be matched by name
    without a translation table.
    """
    seen = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    seen.sort()
    return [(text, text) for text in seen[:MAX_CHOICES]]


def order_status_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """The four order states.

    ``STATUS_REFUNDED`` is not offered: it is a deprecated alias of
    ``STATUS_CANCELED`` with the identical value ``"c"``
    (docs/pretix-api-notes.md section 6.1, pitfall 5).
    """
    return [(code, label) for code, label in Order.STATUS_CHOICE]


def locale_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Languages this event is available in."""
    from django.conf import settings

    names = dict(settings.LANGUAGES)
    return [
        (code, names.get(code, code)) for code in (ctx.event.settings.locales or [])
    ]


def sales_channel_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Sales channels of the organizer, by stable identifier.

    ``SalesChannel.identifier`` is unique per organizer and is what the order
    stores, so the value stays meaningful in another event of the same
    organizer.
    """
    channels = SalesChannel.objects.filter(organizer=ctx.event.organizer).order_by(
        "position", "identifier"
    )[:MAX_CHOICES]
    return [(channel.identifier, str(channel.label)) for channel in channels]


def country_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """ISO 3166-1 alpha-2 codes with translated names."""
    return [(code, name) for code, name in countries]


def item_name_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Public product names of this event."""
    items = Item.objects.filter(event=ctx.event).order_by("position", "pk")[
        :MAX_CHOICES
    ]
    return _pairs([item.name for item in items])


def item_internal_name_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Internal product names of this event, where set."""
    values = (
        Item.objects.filter(event=ctx.event)
        .exclude(internal_name__isnull=True)
        .exclude(internal_name="")
        .order_by("position", "pk")
        .values_list("internal_name", flat=True)[:MAX_CHOICES]
    )
    return _pairs(list(values))


def category_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Product category names of this event."""
    categories = ItemCategory.objects.filter(event=ctx.event).order_by(
        "position", "pk"
    )[:MAX_CHOICES]
    return _pairs([category.name for category in categories])


def variation_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Variation values of this event's products."""
    variations = ItemVariation.objects.filter(item__event=ctx.event).order_by(
        "position", "pk"
    )[:MAX_CHOICES]
    return _pairs([variation.value for variation in variations])


def subevent_name_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Names of the dates of an event series. Empty for a single event."""
    if not ctx.event.has_subevents:
        return []
    subevents = SubEvent.objects.filter(event=ctx.event).order_by("date_from", "pk")[
        :MAX_CHOICES
    ]
    return _pairs([subevent.name for subevent in subevents])


def seat_zone_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Seating zones of this event.

    ``Seat`` has no ``ScopedManager``, so this ``event=`` filter is the whole
    tenant separation for this query (docs/pretix-api-notes.md section 6.8).
    """
    values = (
        Seat.objects.filter(event=ctx.event)
        .exclude(zone_name="")
        .order_by("zone_name")
        .values_list("zone_name", flat=True)
        .distinct()[:MAX_CHOICES]
    )
    return _pairs(list(values))


def voucher_tag_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Voucher tags used in this event."""
    values = (
        Voucher.objects.filter(event=ctx.event)
        .exclude(tag="")
        .order_by("tag")
        .values_list("tag", flat=True)
        .distinct()[:MAX_CHOICES]
    )
    return _pairs(list(values))


def discount_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Internal names of this event's discounts."""
    values = (
        Discount.objects.filter(event=ctx.event)
        .order_by("position", "pk")
        .values_list("internal_name", flat=True)[:MAX_CHOICES]
    )
    return _pairs(list(values))


def boolean_choices(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
    """Yes/no, for question types that pretix stores as text."""
    return [(True, _("Yes")), (False, _("No"))]
