"""Event meta properties as fields.

Owner: registry-dev.

``meta.event.<name>`` for every ``EventMetaProperty`` the organizer defines.
Property names are validated by pretix against ``^[a-zA-Z0-9_]+$``
(``pretix/base/models/event.py:1808-1813``), so a name *can* contain a double
underscore and then cannot become a key (ADR 0001 section 2). Such a property is
skipped and reported, exactly like a question with the same problem.

Why the value is a constant
---------------------------

``Event.meta_data`` layers the organizer-wide default under the event's own
value (``event.py:1365-1373``). Filtering through
``meta_values__property__name`` would therefore silently miss every event that
relies on the default -- the asymmetry between display and filter that
docs/pretix-api-notes.md section 6.7 warns about.

A compiled report belongs to exactly one event (ADR 0001 section 9), so the value
is the same for every row and can be a literal in the SQL. Display and filter
then use the identical value by construction, the join disappears, and a
multi-event export still gets each event's own value because it compiles once per
event.

The price: filtering ``meta.event.campaign = "x"`` either matches all rows or
none. That is the correct semantics -- the property describes the event, not the
order.
"""

from typing import Any, Dict, List, Sequence, Tuple

from django.utils.translation import gettext_lazy as _

from pretix_custom_reports.contracts import (
    GROUP_META,
    Base,
    DataType,
    FieldContext,
    Operator,
    ReportField,
    ValueScope,
    meta_field_key,
    validate_key,
)
from pretix_custom_reports.registry import annotations
from pretix_custom_reports.registry.diagnostics import (
    REASON_DUPLICATE_ALIAS,
    REASON_INVALID_KEY,
    SOURCE_META,
    SkippedField,
)

__all__ = ["META_OPERATORS", "meta_fields"]

#: Textual operators only. A meta value is a ``TextField`` on pretix' side, and
#: the constant we compare against is a string.
META_OPERATORS: Tuple[Operator, ...] = (
    Operator.EXACT,
    Operator.NOT_EXACT,
    Operator.CONTAINS,
    Operator.NOT_CONTAINS,
    Operator.STARTS_WITH,
    Operator.ENDS_WITH,
    Operator.IN,
    Operator.NOT_IN,
    Operator.IS_EMPTY,
    Operator.IS_NOT_EMPTY,
)


def _property_choices(property_pk: int, organizer_pk: int):
    """The property's allowed values, if the organizer restricted them.

    ``EventMetaProperty.choices`` is a ``JSONField`` holding
    ``[{"key": ..., "label": ...}, ...]`` (``event.py:1828-1861``). Read lazily,
    so adding a value does not need a cache invalidation.
    """

    def build(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
        from pretix.base.models import EventMetaProperty

        if ctx.event is None or ctx.event.organizer_id != organizer_pk:
            return []
        prop = EventMetaProperty.objects.filter(
            pk=property_pk, organizer=ctx.event.organizer
        ).first()
        if prop is None or not prop.choices:
            return []
        result: List[Tuple[Any, Any]] = []
        for entry in prop.choices:
            if not isinstance(entry, dict) or "key" not in entry:
                continue
            key = str(entry["key"])
            label = entry.get("label") or key
            if isinstance(label, dict):
                label = key
            result.append((key, str(label)))
        return result

    return build


def meta_fields(
    event: Any, base: Base
) -> Tuple[Dict[str, ReportField], Tuple[SkippedField, ...]]:
    """``meta.event.<name>`` fields for *event*, plus what was skipped.

    Properties are read from the event's own organizer only
    (``organizer.meta_properties``), never globally.
    """
    coerced = Base.coerce(base)
    fields: Dict[str, ReportField] = {}
    skipped: List[SkippedField] = []
    used_aliases: Dict[str, str] = {}

    for prop in event.organizer.meta_properties.all():
        name = prop.name or ""
        key = f"meta.event.{name}"
        try:
            validate_key(key)
        except ValueError as error:
            skipped.append(
                SkippedField(
                    key=key,
                    source=SOURCE_META,
                    reason=REASON_INVALID_KEY,
                    detail=str(error),
                )
            )
            continue

        alias = annotations.alias_for(annotations.ALIAS_META_PREFIX, name)
        if alias in used_aliases:  # pragma: no cover - names cannot collide today
            skipped.append(
                SkippedField(
                    key=key,
                    source=SOURCE_META,
                    reason=REASON_DUPLICATE_ALIAS,
                    detail=f"annotation alias {alias} is already used by "
                    f"{used_aliases[alias]}",
                )
            )
            continue
        used_aliases[alias] = key

        fields[meta_field_key("event", name)] = ReportField(
            key=key,
            label=str(prop.public_label) if prop.public_label else name,
            group=GROUP_META,
            datatype=DataType.CHOICE if prop.choices else DataType.STRING,
            bases=(coerced,),
            orm_path=alias,
            annotation=annotations.meta_annotation(
                property_name=name,
                event_pk=event.pk,
                base=coerced,
                alias=alias,
            ),
            filter_operators=META_OPERATORS,
            sortable=True,
            choices=(
                _property_choices(prop.pk, event.organizer_id) if prop.choices else None
            ),
            value_scope=ValueScope.GLOBAL,
            help_text=_(
                "Meta data of the event, identical for every row of this report."
            ),
        )

    return fields, tuple(skipped)
