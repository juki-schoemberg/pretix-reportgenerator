"""UI groups used by the core field table.

Owner: registry-dev.

``ReportField.group`` is a free string (contracts/fields.py). The core registry
uses the ``GROUP_*`` constants from the contracts wherever one fits and adds two
of its own for the groups the contracts do not name. The labels live here so the
editor does not have to invent them and so the ``de`` catalogue has exactly one
place to translate.
"""

from typing import Dict, Tuple

from django.utils.translation import gettext_lazy as _

from pretix_custom_reports.contracts import (
    GROUP_ANSWERS,
    GROUP_CHECKIN,
    GROUP_INVOICE_ADDRESS,
    GROUP_ITEM,
    GROUP_META,
    GROUP_ORDER,
    GROUP_PAYMENT,
    GROUP_POSITION,
    GROUP_SEAT,
    GROUP_SUBEVENT,
    GROUP_VOUCHER,
)

__all__ = [
    "GROUP_COMPUTED",
    "GROUP_DISCOUNT",
    "GROUP_LABELS",
    "GROUP_ORDERING",
    "group_label",
]

#: Discounts (pretix ``Discount``). The contracts do not name this group.
GROUP_DISCOUNT = "discount"

#: Fields that exist only as a calculation, e.g. age at the event date.
GROUP_COMPUTED = "computed"

#: Display order of the groups in the field library. Anything not listed
#: (a third-party plugin's own group) is appended, sorted by label.
GROUP_ORDERING: Tuple[str, ...] = (
    GROUP_ORDER,
    GROUP_PAYMENT,
    GROUP_CHECKIN,
    GROUP_INVOICE_ADDRESS,
    GROUP_POSITION,
    GROUP_ITEM,
    GROUP_SUBEVENT,
    GROUP_SEAT,
    GROUP_VOUCHER,
    GROUP_DISCOUNT,
    GROUP_ANSWERS,
    GROUP_META,
    GROUP_COMPUTED,
)

#: Human readable group names. Lazy, so the active language decides.
GROUP_LABELS: Dict[str, object] = {
    GROUP_ORDER: _("Order"),
    GROUP_PAYMENT: _("Payments and refunds"),
    GROUP_CHECKIN: _("Check-in"),
    GROUP_INVOICE_ADDRESS: _("Invoice address"),
    GROUP_POSITION: _("Position"),
    GROUP_ITEM: _("Product"),
    GROUP_SUBEVENT: _("Date"),
    GROUP_SEAT: _("Seat"),
    GROUP_VOUCHER: _("Voucher"),
    GROUP_DISCOUNT: _("Discount"),
    GROUP_ANSWERS: _("Questions"),
    GROUP_META: _("Meta data"),
    GROUP_COMPUTED: _("Calculated"),
}


def group_label(group: str) -> object:
    """Label for *group*, or the raw group string if we do not know it."""
    return GROUP_LABELS.get(group, group)
