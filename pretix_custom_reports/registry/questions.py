"""Dynamic fields for this event's questions.

Owner: registry-dev.

Addressing
----------

A question is addressed by ``Question.identifier``, never by primary key
(``answer.tshirt-size``). Verified properties of that identifier
(docs/pretix-api-notes.md section 6.4):

* unique per event on database level, never empty, auto-generated on first save,
* **survives an event copy** -- ``copy_data_from`` sets ``pk = None`` and
  ``event = self`` and leaves ``identifier`` alone
  (``pretix/base/models/event.py:1090-1099``). That is the whole basis of
  template portability (SPEC.md F10),
* **but changeable by the user at any time**, in the backend and through the
  API.

So "this key no longer resolves" is a normal state, not an error
(ADR 0001 section 3.2). Nothing in here raises because of it.

Fallback when a key cannot be built
-----------------------------------

An identifier containing ``__`` cannot become a key: the double underscore ban
(ADR 0001 section 2) exists so a stored key can never be mistaken for a
multi-level ORM path. The fallback is *not* to mangle the identifier into
something else -- a silently rewritten key would be unstable across events and
would collide with a real identifier. Instead the field is skipped and reported
through :mod:`registry.diagnostics`, so the debug view can tell the user exactly
which question to rename. Auto-generated identifiers use the alphabet
``ABCDEFGHJKLMNPQRSTUVWXYZ3789`` and can never trigger this.

The type problem
----------------

``QuestionAnswer.answer`` is a ``TextField`` for **every** question type
(docs/pretix-api-notes.md section 6.4, pitfall 1). What this module does about
that, per type:

======================  =============  =========================================
``Question.type``       ``DataType``   comparison semantics
======================  =============  =========================================
``B`` boolean           ``boolean``    normalised to a real boolean in SQL,
                                       because pretix stores ``"True"``/
                                       ``"False"`` as text
``D`` date              ``date``       lexicographic on ``YYYY-MM-DD``, which is
                                       exactly correct
``H`` time              ``time``       lexicographic on ``HH:MM:SS``, correct
``C`` choice            ``choice``     the answer is the option label, so
                                       ``in``/``exact`` match by name
``M`` multiple choice   ``multichoice``labels joined by ``", "``, therefore
                                       ``contains`` instead of ``in``
``CC`` country          ``country``    two-letter code
``TEL`` phone           ``phone``      as entered
``N`` number            ``string``     **on purpose**: text ordering would put
                                       "10" before "9", and casting to numeric
                                       would let one malformed row fail the whole
                                       report
``W`` datetime          ``string``     **on purpose**: the stored ISO string with
                                       offset does not compare correctly against
                                       a database datetime
``S``/``T`` text        ``string``/
                        ``text``
``F`` file              ``file``       presence only
======================  =============  =========================================

Base ``order``
--------------

On base ``order`` an answer belongs to a position, not to the order, so it is
one-to-many and needs an aggregate (ADR 0001 section 7). The path is
``all_positions__answers__answer`` plus the question constraint from
:mod:`registry.hints`; ``join``, ``count`` and ``count_distinct`` are the
aggregates that make sense.

Filtering answers is **not** offered on base ``order``. Not an oversight: "order
has a position whose answer is X" and "all positions answered X" are different
questions, the editor has no way to say which one is meant, and a filter that
quietly picks one of the two is worse than no filter. Build the report on base
``orderposition`` to filter answers.
"""

from typing import Any, Dict, List, Sequence, Tuple

from django.utils.translation import gettext_lazy as _
from pretix.base.models import Question

from pretix_custom_reports.contracts import (
    GROUP_ANSWERS,
    Aggregate,
    Base,
    DataType,
    FieldContext,
    Operator,
    ReportField,
    ValueScope,
    question_field_key,
    validate_key,
)
from pretix_custom_reports.registry import annotations
from pretix_custom_reports.registry.choices import country_choices
from pretix_custom_reports.registry.core import POSITION_RELATION
from pretix_custom_reports.registry.diagnostics import (
    REASON_AMBIGUOUS_KEY,
    REASON_DUPLICATE_ALIAS,
    REASON_INVALID_KEY,
    SOURCE_QUESTION,
    SkippedField,
)
from pretix_custom_reports.registry.groups import GROUP_COMPUTED
from pretix_custom_reports.registry.hints import (
    EXTRA_AGGREGATE_QUESTION_PK,
    EXTRA_AGGREGATE_RELATION,
    EXTRA_CANCELED_FLAG,
    EXTRA_LEXICOGRAPHIC,
    EXTRA_QUESTION_IDENTIFIER,
    EXTRA_QUESTION_TYPE,
)

__all__ = [
    "AGE_KEY_PREFIX",
    "ANSWER_AGGREGATES",
    "QUESTION_DATATYPES",
    "question_fields",
]

#: ``computed.age.<identifier>`` -- age in full years at the event date, offered
#: for every date question. See :func:`_age_field`.
AGE_KEY_PREFIX = "computed.age."

#: Aggregates that make sense for an answer column on base ``order``.
ANSWER_AGGREGATES: Tuple[Aggregate, ...] = (
    Aggregate.JOIN,
    Aggregate.COUNT,
    Aggregate.COUNT_DISTINCT,
)

#: ``Question.type`` to :class:`DataType`. See the module docstring for the two
#: mappings that look wrong and are not.
QUESTION_DATATYPES: Dict[str, DataType] = {
    Question.TYPE_NUMBER: DataType.STRING,
    Question.TYPE_STRING: DataType.STRING,
    Question.TYPE_TEXT: DataType.TEXT,
    Question.TYPE_BOOLEAN: DataType.BOOLEAN,
    Question.TYPE_CHOICE: DataType.CHOICE,
    Question.TYPE_CHOICE_MULTIPLE: DataType.MULTICHOICE,
    Question.TYPE_FILE: DataType.FILE,
    Question.TYPE_DATE: DataType.DATE,
    Question.TYPE_TIME: DataType.TIME,
    Question.TYPE_DATETIME: DataType.STRING,
    Question.TYPE_COUNTRYCODE: DataType.COUNTRY,
    Question.TYPE_PHONENUMBER: DataType.PHONE,
}

_TEXT_OPERATORS: Tuple[Operator, ...] = (
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

_SET_OPERATORS: Tuple[Operator, ...] = (
    Operator.EXACT,
    Operator.NOT_EXACT,
    Operator.IN,
    Operator.NOT_IN,
    Operator.IS_EMPTY,
    Operator.IS_NOT_EMPTY,
)

_ORDERED_OPERATORS: Tuple[Operator, ...] = (
    Operator.EXACT,
    Operator.NOT_EXACT,
    Operator.LT,
    Operator.LTE,
    Operator.GT,
    Operator.GTE,
    Operator.BETWEEN,
    Operator.IS_EMPTY,
    Operator.IS_NOT_EMPTY,
)

_LIST_OPERATORS: Tuple[Operator, ...] = (
    Operator.CONTAINS,
    Operator.NOT_CONTAINS,
    Operator.IS_EMPTY,
    Operator.IS_NOT_EMPTY,
)

#: Operators per question type, for base ``orderposition``. Deliberately not
#: ``DEFAULT_OPERATORS[datatype]``: the underlying column is text, so the
#: relative date operators are missing even for date questions -- they resolve to
#: datetime boundaries, and comparing those against a text column is guesswork.
_OPERATORS_BY_TYPE: Dict[str, Tuple[Operator, ...]] = {
    Question.TYPE_NUMBER: _TEXT_OPERATORS,
    Question.TYPE_STRING: _TEXT_OPERATORS,
    Question.TYPE_TEXT: _TEXT_OPERATORS,
    Question.TYPE_BOOLEAN: (
        Operator.EXACT,
        Operator.NOT_EXACT,
        Operator.IS_EMPTY,
        Operator.IS_NOT_EMPTY,
    ),
    Question.TYPE_CHOICE: _SET_OPERATORS,
    Question.TYPE_CHOICE_MULTIPLE: _LIST_OPERATORS,
    Question.TYPE_FILE: (Operator.IS_EMPTY, Operator.IS_NOT_EMPTY),
    Question.TYPE_DATE: _ORDERED_OPERATORS,
    Question.TYPE_TIME: _ORDERED_OPERATORS,
    Question.TYPE_DATETIME: _TEXT_OPERATORS,
    Question.TYPE_COUNTRYCODE: _SET_OPERATORS,
    Question.TYPE_PHONENUMBER: _TEXT_OPERATORS,
}

_HELP_LEXICOGRAPHIC = _(
    "pretix stores every answer as text. Comparisons on this field are therefore "
    "textual, not numeric."
)

_HELP_ORDER_BASE = _(
    "Answers belong to a position. On an order-based report this column needs an "
    "aggregate, and answers cannot be filtered -- build the report on base "
    '"order position" for that.'
)


def _option_choices(question_pk: int, event_pk: int):
    """Lazy option list of one choice question, by label.

    The stored answer text *is* the option label at the time of answering
    (``QuestionAnswer.to_string``, ``pretix/base/models/orders.py:1440-1447``),
    so the label is both value and label here. Values must be matched by name on
    import, which is why the field declares ``ValueScope.EVENT``.
    """

    def build(ctx: FieldContext) -> Sequence[Tuple[Any, Any]]:
        if ctx.event is None or ctx.event.pk != event_pk:
            return []
        options = (
            Question.objects.filter(pk=question_pk, event=ctx.event)
            .prefetch_related("options")
            .first()
        )
        if options is None:
            return []
        return [
            (str(option.answer), str(option.answer)) for option in options.options.all()
        ]

    return build


def _answer_field_position(question: Any, event_pk: int, alias: str) -> ReportField:
    datatype = QUESTION_DATATYPES[question.type]
    is_choice = question.type in (Question.TYPE_CHOICE, Question.TYPE_CHOICE_MULTIPLE)
    field_choices = None
    if is_choice:
        field_choices = _option_choices(question.pk, event_pk)
    elif question.type == Question.TYPE_COUNTRYCODE:
        field_choices = country_choices

    # Kept lazy: str() only for the emptiness check, the value stays an
    # I18nString so the active language still decides.
    help_text = question.help_text if str(question.help_text or "") else None
    if question.type in (Question.TYPE_NUMBER, Question.TYPE_DATETIME):
        help_text = _HELP_LEXICOGRAPHIC

    return ReportField(
        key=question_field_key(question.identifier),
        label=question.question,
        group=GROUP_ANSWERS,
        datatype=datatype,
        bases=(Base.ORDERPOSITION,),
        orm_path=alias,
        annotation=annotations.answer_annotation(
            question_pk=question.pk,
            event_pk=event_pk,
            question_type=question.type,
            alias=alias,
        ),
        filter_operators=_OPERATORS_BY_TYPE.get(question.type, _TEXT_OPERATORS),
        sortable=True,
        choices=field_choices,
        value_scope=ValueScope.EVENT if is_choice else ValueScope.GLOBAL,
        help_text=help_text,
        extra={
            EXTRA_QUESTION_IDENTIFIER: question.identifier,
            EXTRA_QUESTION_TYPE: question.type,
            EXTRA_LEXICOGRAPHIC: question.type != Question.TYPE_BOOLEAN,
        },
    )


def _answer_field_order(question: Any, event_pk: int) -> ReportField:
    datatype = QUESTION_DATATYPES[question.type]
    relation = f"{POSITION_RELATION}__answers"
    return ReportField(
        key=question_field_key(question.identifier),
        label=question.question,
        group=GROUP_ANSWERS,
        datatype=datatype,
        bases=(Base.ORDER,),
        orm_path=f"{relation}__answer",
        # No filters here on purpose, see the module docstring.
        filter_operators=(),
        sortable=False,
        aggregates=ANSWER_AGGREGATES,
        requires_aggregate_on=(Base.ORDER,),
        value_scope=ValueScope.GLOBAL,
        help_text=_HELP_ORDER_BASE,
        extra={
            EXTRA_QUESTION_IDENTIFIER: question.identifier,
            EXTRA_QUESTION_TYPE: question.type,
            EXTRA_LEXICOGRAPHIC: True,
            EXTRA_AGGREGATE_RELATION: relation,
            EXTRA_CANCELED_FLAG: f"{POSITION_RELATION}__canceled",
            EXTRA_AGGREGATE_QUESTION_PK: question.pk,
        },
    )


def _reference_date(event: Any):
    """The date an age is calculated against: the event's start, local time.

    ``Event.date_from`` is a ``DateTimeField`` stored in UTC; an age has to be
    computed against the day as the organizer sees it, hence the conversion into
    ``Event.timezone``. For an event series this is the series' own start date --
    a per-date age would need the subevent of the row, which is a different
    field and not worth the extra join in v1.
    """
    return event.date_from.astimezone(event.timezone).date()


def _age_field(question: Any, event: Any, alias: str) -> ReportField:
    return ReportField(
        key=f"{AGE_KEY_PREFIX}{question.identifier}",
        label=_("Age at the event date: {question}").format(
            question=str(question.question)
        ),
        group=GROUP_COMPUTED,
        datatype=DataType.INTEGER,
        bases=(Base.ORDERPOSITION,),
        orm_path=alias,
        annotation=annotations.age_at_event_annotation(
            question_pk=question.pk,
            event_pk=event.pk,
            alias=alias,
            reference_date=_reference_date(event),
        ),
        filter_operators=_ORDERED_OPERATORS,
        sortable=True,
        help_text=_(
            "Full years between the answer to this date question and the start of "
            "the event, calculated in the database."
        ),
        extra={
            EXTRA_QUESTION_IDENTIFIER: question.identifier,
            EXTRA_QUESTION_TYPE: question.type,
        },
    )


def question_fields(
    event: Any, base: Base
) -> Tuple[Dict[str, ReportField], Tuple[SkippedField, ...]]:
    """Answer fields (and age fields) for every question of *event*.

    Returns ``(fields, skipped)``. ``fields`` is keyed by field key in question
    order (``Question.Meta.ordering = ('position', 'id')``), so the field library
    lists them the way the organizer sorted them.

    Only ``event``'s own questions are read. ``event.questions`` is the reverse
    accessor of ``Question.event``, so the restriction is structural rather than
    a filter somebody could forget.
    """
    coerced = Base.coerce(base)
    fields: Dict[str, ReportField] = {}
    skipped: List[SkippedField] = []
    used_aliases: Dict[str, str] = {}
    lowercase_keys: Dict[str, str] = {}

    questions = list(event.questions.all())
    for question in questions:
        identifier = question.identifier or ""
        key = f"answer.{identifier}"
        try:
            validate_key(key)
        except ValueError as error:
            skipped.append(
                SkippedField(
                    key=key,
                    source=SOURCE_QUESTION,
                    reason=REASON_INVALID_KEY,
                    detail=str(error),
                )
            )
            continue

        if key.lower() in lowercase_keys:
            skipped.append(
                SkippedField(
                    key=key,
                    source=SOURCE_QUESTION,
                    reason=REASON_AMBIGUOUS_KEY,
                    detail=(
                        "differs from {} only in capitalisation, and answer keys "
                        "resolve case-insensitively".format(lowercase_keys[key.lower()])
                    ),
                )
            )
            continue

        alias = annotations.alias_for(annotations.ALIAS_ANSWER_PREFIX, identifier)
        if alias in used_aliases:
            skipped.append(
                SkippedField(
                    key=key,
                    source=SOURCE_QUESTION,
                    reason=REASON_DUPLICATE_ALIAS,
                    detail=(
                        "annotation alias {} is already used by {}".format(
                            alias, used_aliases[alias]
                        )
                    ),
                )
            )
            continue

        if question.type not in QUESTION_DATATYPES:  # pragma: no cover - defensive
            continue

        used_aliases[alias] = key
        lowercase_keys[key.lower()] = key

        if coerced is Base.ORDERPOSITION:
            fields[key] = _answer_field_position(question, event.pk, alias)
        else:
            fields[key] = _answer_field_order(question, event.pk)

    if coerced is Base.ORDERPOSITION:
        for question in questions:
            if question.type != Question.TYPE_DATE:
                continue
            answer_key = f"answer.{question.identifier or ''}"
            if answer_key not in fields:
                continue
            age_key = f"{AGE_KEY_PREFIX}{question.identifier}"
            try:
                validate_key(age_key)
            except ValueError as error:
                skipped.append(
                    SkippedField(
                        key=age_key,
                        source=SOURCE_QUESTION,
                        reason=REASON_INVALID_KEY,
                        detail=str(error),
                    )
                )
                continue
            alias = annotations.alias_for(
                annotations.ALIAS_PREFIX + "age_", question.identifier
            )
            fields[age_key] = _age_field(question, event, alias)

    return fields, tuple(skipped)
