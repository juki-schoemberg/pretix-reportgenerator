"""What the query compiler needs to know beyond ``orm_path``.

Owner: registry-dev.

Most fields are a plain ``orm_path`` and need nothing else. Two situations do:

**1. Aggregated one-to-many data (ADR 0001 section 7).** On base ``order`` a
position field is reached through ``all_positions__...``. That relation contains
canceled positions, because ``all_positions`` is the raw ``related_name`` while
``Order.positions`` is a Python property over the filtered manager
(docs/pretix-api-notes.md section 6.2, pitfall 1). So an aggregate over it has
to carry a ``filter=`` argument, and only the registry knows which one.

**2. Answers on base ``order``.** ``answer.<identifier>`` is reached through
``all_positions__answers__answer``, which without a further condition would
aggregate the answers to *every* question. The constraint on the question is
again registry knowledge.

Both are expressed as **JSON-safe primitives** in :attr:`ReportField.extra` plus
the two functions below, which turn them into a ``Q``. The ``Q`` deliberately
does not live in ``extra``: the editor API serialises fields to JSON, and a
``Q`` object in there would break ``json.dumps``.

Usage in the query compiler::

    from pretix_custom_reports.registry import hints

    relation = hints.aggregate_relation(field)          # None for plain fields
    condition = hints.aggregate_filter(
        field, include_canceled_positions=definition.options.include_canceled_positions
    )
    expression = Sum(field.orm_path, filter=condition)

``aggregate_filter`` returns ``None`` when no condition is needed, which is
exactly what ``filter=None`` means to Django's aggregates.

Nothing in here ever reads a stored or imported document: the inputs are a
``ReportField`` built by this package and one boolean from the validated
options.
"""

from typing import Any, Mapping, Optional

from django.db.models import Q

from pretix_custom_reports.contracts import ReportField

__all__ = [
    "EXTRA_AGGREGATE_QUESTION_PK",
    "EXTRA_AGGREGATE_RELATION",
    "EXTRA_CANCELED_FLAG",
    "EXTRA_LEXICOGRAPHIC",
    "EXTRA_META_PROPERTY",
    "EXTRA_PAYMENT_STATES",
    "EXTRA_QUESTION_IDENTIFIER",
    "EXTRA_QUESTION_TYPE",
    "EXTRA_REFUND_STATES",
    "aggregate_filter",
    "aggregate_relation",
]

#: ``extra`` key: the ``related_name`` path that multiplies rows for this field,
#: e.g. ``"all_positions"``. Present exactly on the fields that declare
#: ``requires_aggregate_on``.
EXTRA_AGGREGATE_RELATION = "aggregate_relation"

#: ``extra`` key: the ORM path of the ``canceled`` flag inside the aggregated
#: relation, e.g. ``"all_positions__canceled"``. ``None``/absent means the
#: relation has no such flag.
EXTRA_CANCELED_FLAG = "canceled_flag"

#: ``extra`` key: primary key of the ``Question`` an aggregated answer column
#: must be restricted to. An int, and it is ours -- it comes from a query
#: against ``event.questions``, never from a document.
EXTRA_AGGREGATE_QUESTION_PK = "aggregate_question_pk"

#: ``extra`` key: ``Question.identifier`` this field was built from.
EXTRA_QUESTION_IDENTIFIER = "question_identifier"

#: ``extra`` key: the raw ``Question.type`` character, for renderers that want
#: to reuse ``QuestionAnswer.to_string`` semantics.
EXTRA_QUESTION_TYPE = "question_type"

#: ``extra`` key: ``True`` when the underlying column is a ``TextField`` and
#: comparisons are therefore lexicographic. True for every ``answer.*`` field --
#: ``QuestionAnswer.answer`` is always text, whatever the question type
#: (docs/pretix-api-notes.md section 6.4, pitfall 1).
EXTRA_LEXICOGRAPHIC = "lexicographic_comparison"

#: ``extra`` key: name of the ``EventMetaProperty`` behind a ``meta.event.*``
#: field.
EXTRA_META_PROPERTY = "meta_property"

#: ``extra`` key: the ``OrderPayment.state`` values that count towards a money
#: sum, as a tuple. Documentation for the reader of the field library; the
#: expression itself already contains them.
EXTRA_PAYMENT_STATES = "payment_states"

#: ``extra`` key: the ``OrderRefund.state`` values that count towards a refund
#: sum.
EXTRA_REFUND_STATES = "refund_states"


def _extra(field: ReportField) -> Mapping[str, Any]:
    return field.extra or {}


def aggregate_relation(field: ReportField) -> Optional[str]:
    """The relation that multiplies rows for *field*, or ``None``.

    A non-``None`` result means: aggregating this field joins a one-to-many
    relation, so a second aggregated column over the *same* relation in the same
    query double-counts unless the compiler uses subqueries or
    ``distinct=True`` where applicable. ADR 0001 section 11 lists that trap
    explicitly.
    """
    value = _extra(field).get(EXTRA_AGGREGATE_RELATION)
    return value or None


def aggregate_filter(
    field: ReportField,
    *,
    include_canceled_positions: bool = False,
) -> Optional[Q]:
    """The ``filter=`` argument an aggregate over *field* must carry.

    Combines two conditions, both of them built here in code:

    * exclude canceled positions unless the report asks for them
      (``options.include_canceled_positions``), because ``all_positions``
      contains them,
    * restrict an answer column to its own question.

    Returns ``None`` if neither applies.
    """
    extra = _extra(field)
    condition: Optional[Q] = None

    canceled_flag = extra.get(EXTRA_CANCELED_FLAG)
    if canceled_flag and not include_canceled_positions:
        condition = Q(**{canceled_flag: False})

    question_pk = extra.get(EXTRA_AGGREGATE_QUESTION_PK)
    relation = extra.get(EXTRA_AGGREGATE_RELATION)
    if question_pk is not None and relation:
        question_condition = Q(**{f"{relation}__question": question_pk})
        condition = (
            question_condition if condition is None else condition & question_condition
        )

    return condition
