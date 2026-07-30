"""Fields that exist only as a calculation.

Owner: registry-dev.

Two of the calculated fields are event independent and live here. The third,
``computed.age.<identifier>``, depends on the event's questions and is built in
:mod:`registry.questions`.

Both fields here are annotations rather than ``value_getter`` callables, and that
is the point: a value computed in Python cannot be filtered or sorted by the
database, and the contract refuses to pretend otherwise
(``ReportField.__post_init__``). "Payment status" is exactly the kind of column
people want to filter on, so it has to be an expression.

``computed.payment_state`` returns a **code**, not a translated word:
``unpaid`` / ``partially_paid`` / ``paid`` / ``overpaid``, with the translation
in ``choices``. A stored filter value therefore stays portable and keeps working
when the report runs in another language -- a scheduled export runs under the
schedule's locale, not the author's (docs/pretix-api-notes.md section 5).
``computed.order_status_label`` is the opposite case: it exists precisely to
produce the word pretix shows, so it carries no filter operators at all --
filter ``order.status`` instead, which is the same information as a stable code.
"""

from typing import Any, Dict, Tuple

from django.utils.translation import gettext_lazy as _

from pretix_custom_reports.contracts import (
    GROUP_ORDER,
    GROUP_PAYMENT,
    Base,
    DataType,
    Operator,
    ReportField,
    ValueScope,
)
from pretix_custom_reports.registry import annotations

__all__ = ["computed_fields"]

_STATE_OPERATORS: Tuple[Operator, ...] = (
    Operator.EXACT,
    Operator.NOT_EXACT,
    Operator.IN,
    Operator.NOT_IN,
)


def _payment_state_choices(ctx: Any):
    return list(annotations.PAYMENT_STATE_CHOICES)


def computed_fields(base: Base) -> Dict[str, ReportField]:
    """The event-independent ``computed.*`` fields for *base*."""
    coerced = Base.coerce(base)
    fields = [
        ReportField(
            key="computed.order_status_label",
            label=_("Order status (text)"),
            group=GROUP_ORDER,
            datatype=DataType.STRING,
            bases=(coerced,),
            orm_path=annotations.ALIAS_STATUS_LABEL,
            annotation=annotations.status_label_annotation(coerced),
            # No filters: the value is a translated word, so a stored filter
            # value would stop matching as soon as the report runs in another
            # language. Filter order.status instead.
            filter_operators=(),
            sortable=True,
            help_text=_(
                'The order status spelled out, for the export. Filter on "Order '
                'status" instead -- its values are stable letters.'
            ),
        ),
        ReportField(
            key="computed.payment_state",
            label=_("Payment status"),
            group=GROUP_PAYMENT,
            datatype=DataType.CHOICE,
            bases=(coerced,),
            orm_path=annotations.ALIAS_PAYMENT_STATE,
            annotation=annotations.payment_state_annotation(coerced),
            filter_operators=_STATE_OPERATORS,
            sortable=True,
            choices=_payment_state_choices,
            value_scope=ValueScope.GLOBAL,
            help_text=_(
                "Derived from the outstanding amount: overpaid, paid in full, "
                "partially paid or not paid. A canceled order counts as paid in "
                "full because it owes nothing."
            ),
        ),
    ]
    return {field.key: field for field in fields}
