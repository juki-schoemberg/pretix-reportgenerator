# Owner: frontend-dev (ORCHESTRIERUNG.md section 5)
"""JSON endpoints behind the graphical report editor.

Wave 2 status
-------------

Nothing in here is a stand-in any more. The two seams that used to hold the
wave-1 stubs, :func:`get_registry` and :func:`get_compiler`, now return the real
field registry and the real query compiler. Both function bodies changed and
nothing else did: no template, no stylesheet and no line of JavaScript was
touched for the swap, which is the property the two functions existed to prove
(docs/adr/0005-editor.md section 2).

They stay as functions rather than becoming module-level imports for two
reasons: the import stays lazy, so importing this module does not drag the whole
registry in at URLconf time, and a test can still substitute a deliberately
broken registry or compiler in one place.

Security rules that are *not* negotiable in here
------------------------------------------------

1. Every view is permission checked (:data:`VIEW_PERMISSION`). The preview
   renders real order data; an unauthenticated or under-privileged preview is a
   data leak, not a cosmetic bug.
2. Every mutating/expensive endpoint is ``POST`` and therefore CSRF protected by
   Django's middleware. Nothing in here is ``csrf_exempt``.
3. The preview is *never* executed without a row limit, and since wave 2 that
   limit is applied twice: ``compile(..., preview=True)`` puts a hard ``LIMIT``
   into the SQL (query/report.py) and :meth:`PreviewView._rows` caps the
   iteration on top of it. The limit is
   :data:`~pretix_custom_reports.contracts.definition.PREVIEW_ROW_LIMIT`; a
   client may ask for fewer rows, never for more.
4. Nothing from the request body is ever used as an ORM path, a lookup or a
   field name. The body is run through
   :func:`~pretix_custom_reports.contracts.validate_definition` (structure) and
   then through the registry (existence), in that order, and only the resolved
   :class:`~pretix_custom_reports.contracts.fields.ReportField` objects are used
   afterwards -- CLAUDE.md rule 2.
"""

from typing import Any, Dict, List, Optional, Tuple

import datetime
import decimal
import json
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.http import Http404, JsonResponse
from django.template.loader import render_to_string
from django.urls import re_path
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
from pretix.control.permissions import EventPermissionRequiredMixin

from ..contracts import (
    AGGREGATES_FOR_DATATYPE,
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
    MAX_COLUMNS,
    MAX_FILTER_CONDITIONS,
    MAX_FILTER_GROUPS,
    MAX_GROUP_CHILDREN,
    MAX_LABEL_LENGTH,
    MAX_ROW_LIMIT,
    MAX_SEPARATOR_LENGTH,
    MAX_SORT_ENTRIES,
    MAX_STRING_VALUE_LENGTH,
    MAX_VALUE_ITEMS,
    OPERATOR_SPECS,
    PREVIEW_ROW_LIMIT,
    SCHEMA_VERSION,
    Aggregate,
    Base,
    BooleanStyle,
    BoolOp,
    CompilationError,
    ContractError,
    DataType,
    DateStyle,
    DefinitionValidationError,
    ErrorCode,
    FieldContext,
    FieldResolutionError,
    NumberStyle,
    Operator,
    ReportDefinition,
    SortDirection,
    ValueScope,
    validate_definition,
)
from ..contracts.definition import MAX_DAY_COUNT
from ..signals import VIEW_PERMISSION

__all__ = [
    "FieldLibraryView",
    "PluginActiveMixin",
    "PreviewView",
    "ValidateView",
    "api_urlpatterns",
    "get_compiler",
    "get_registry",
]


# ---------------------------------------------------------------------------
# The two seams: registry and compiler
# ---------------------------------------------------------------------------


def get_registry():
    """The field registry the editor is served from.

    The real one since wave 2. Everything in this module talks to it through
    :class:`~pretix_custom_reports.contracts.protocols.FieldRegistry` only --
    ``get_fields()`` and ``resolve()`` -- so the editor never depends on
    anything the protocol does not promise.

    Note for callers: the registry reads scoped models (``Question``,
    ``EventMetaProperty``) and deliberately does not open a scope of its own
    (registry/library.py). Inside the control panel that is fine, pretix'
    middleware has one open; a management command or a task would have to.
    """
    from ..registry.library import field_registry

    return field_registry()


def get_compiler():
    """The query compiler the preview is executed with.

    The real one since wave 2. It needs the registry as a constructor argument
    on purpose: which allow-list a definition is resolved against is the single
    most security-relevant fact about a compile, and the compiler refuses to
    guess it (query/compiler.py).
    """
    from ..query.compiler import ReportQueryCompiler

    return ReportQueryCompiler(get_registry())


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

#: Registry groups in the order the field library shows them. Groups the
#: registry invents (third-party plugins pick their own string) are appended,
#: sorted, after these.
GROUP_ORDERING: Tuple[str, ...] = (
    GROUP_ORDER,
    GROUP_POSITION,
    GROUP_INVOICE_ADDRESS,
    GROUP_ITEM,
    GROUP_SUBEVENT,
    GROUP_SEAT,
    GROUP_VOUCHER,
    GROUP_PAYMENT,
    GROUP_CHECKIN,
    GROUP_ANSWERS,
    GROUP_META,
)

GROUP_LABELS: Dict[str, Any] = {
    GROUP_ORDER: _("Order"),
    GROUP_POSITION: _("Order position"),
    GROUP_INVOICE_ADDRESS: _("Invoice address"),
    GROUP_ITEM: _("Product"),
    GROUP_SUBEVENT: _("Date"),
    GROUP_SEAT: _("Seat"),
    GROUP_VOUCHER: _("Voucher"),
    GROUP_PAYMENT: _("Payments and refunds"),
    GROUP_CHECKIN: _("Check-in"),
    GROUP_ANSWERS: _("Questions"),
    GROUP_META: _("Meta properties"),
}

BASE_LABELS: Dict[str, Any] = {
    Base.ORDER.value: _("One row per order"),
    Base.ORDERPOSITION.value: _("One row per order position"),
}

BASE_HELP: Dict[str, Any] = {
    Base.ORDER.value: _(
        "Order level. Fields that belong to a single position (product, seat, "
        "answers) are only available as an aggregate here."
    ),
    Base.ORDERPOSITION.value: _(
        "Position level, the classic attendee list. Order fields repeat on "
        "every position of the same order."
    ),
}

#: Human wording for the operators. The wording is deliberately verb-like so a
#: filter row reads as a sentence: "Order date | is within the last | 30 days".
OPERATOR_LABELS: Dict[str, Any] = {
    Operator.EXACT.value: _("is"),
    Operator.NOT_EXACT.value: _("is not"),
    Operator.CONTAINS.value: _("contains"),
    Operator.NOT_CONTAINS.value: _("does not contain"),
    Operator.STARTS_WITH.value: _("starts with"),
    Operator.ENDS_WITH.value: _("ends with"),
    Operator.IS_EMPTY.value: _("is empty"),
    Operator.IS_NOT_EMPTY.value: _("is not empty"),
    Operator.IN.value: _("is one of"),
    Operator.NOT_IN.value: _("is none of"),
    Operator.LT.value: _("is less than"),
    Operator.LTE.value: _("is at most"),
    Operator.GT.value: _("is greater than"),
    Operator.GTE.value: _("is at least"),
    Operator.BETWEEN.value: _("is between"),
    Operator.RELATIVE_TODAY.value: _("is today"),
    Operator.RELATIVE_LAST_DAYS.value: _("is within the last … days"),
    Operator.RELATIVE_NEXT_DAYS.value: _("is within the next … days"),
    Operator.RELATIVE_CURRENT_MONTH.value: _("is in the current month"),
    Operator.RELATIVE_CURRENT_YEAR.value: _("is in the current year"),
    Operator.RELATIVE_SINCE_EVENT_START.value: _("is after the event start"),
}

AGGREGATE_LABELS: Dict[str, Any] = {
    Aggregate.COUNT.value: _("Count"),
    Aggregate.COUNT_DISTINCT.value: _("Count (distinct)"),
    Aggregate.SUM.value: _("Sum"),
    Aggregate.MIN.value: _("Minimum"),
    Aggregate.MAX.value: _("Maximum"),
    Aggregate.AVG.value: _("Average"),
    Aggregate.JOIN.value: _("List, joined"),
}

DATE_STYLE_LABELS: Dict[str, Any] = {
    DateStyle.SHORT.value: _("Short"),
    DateStyle.MEDIUM.value: _("Medium"),
    DateStyle.LONG.value: _("Long"),
    DateStyle.ISO.value: _("ISO 8601"),
    DateStyle.DATE_ONLY.value: _("Date only"),
    DateStyle.TIME_ONLY.value: _("Time only"),
}

NUMBER_STYLE_LABELS: Dict[str, Any] = {
    NumberStyle.RAW.value: _("Plain number"),
    NumberStyle.LOCALIZED.value: _("Localized"),
    NumberStyle.CURRENCY.value: _("With currency"),
}

BOOLEAN_STYLE_LABELS: Dict[str, Any] = {
    BooleanStyle.YES_NO.value: _("Yes / No"),
    BooleanStyle.TRUE_FALSE.value: _("true / false"),
    BooleanStyle.ONE_ZERO.value: _("1 / 0"),
}

DATATYPE_LABELS: Dict[str, Any] = {
    DataType.STRING.value: _("Text"),
    DataType.TEXT.value: _("Long text"),
    DataType.INTEGER.value: _("Whole number"),
    DataType.DECIMAL.value: _("Decimal number"),
    DataType.MONEY.value: _("Amount"),
    DataType.BOOLEAN.value: _("Yes/No"),
    DataType.DATE.value: _("Date"),
    DataType.TIME.value: _("Time"),
    DataType.DATETIME.value: _("Date and time"),
    DataType.CHOICE.value: _("Choice"),
    DataType.MULTICHOICE.value: _("Multiple choice"),
    DataType.I18N.value: _("Translated text"),
    DataType.COUNTRY.value: _("Country"),
    DataType.EMAIL.value: _("E-mail address"),
    DataType.PHONE.value: _("Phone number"),
    DataType.URL.value: _("URL"),
    DataType.FILE.value: _("File"),
    DataType.LIST.value: _("List"),
}

SORT_DIRECTION_LABELS: Dict[str, Any] = {
    SortDirection.ASC.value: _("ascending"),
    SortDirection.DESC.value: _("descending"),
}

BOOL_OP_LABELS: Dict[str, Any] = {
    BoolOp.AND.value: _("all of these conditions (AND)"),
    BoolOp.OR.value: _("any of these conditions (OR)"),
}

#: Which member of :class:`~pretix_custom_reports.contracts.ColumnFormat` is
#: meaningful for a datatype. Drives which format widget the editor shows for a
#: column; ``None`` means "no formatting options for this datatype".
FORMAT_FAMILY_FOR_DATATYPE: Dict[str, Optional[str]] = {
    DataType.DATE.value: "date_style",
    DataType.DATETIME.value: "date_style",
    DataType.TIME.value: "date_style",
    DataType.INTEGER.value: "number_style",
    DataType.DECIMAL.value: "number_style",
    DataType.MONEY.value: "number_style",
    DataType.BOOLEAN.value: "boolean_style",
}


def _choices(mapping: Dict[str, Any], order) -> List[Dict[str, Any]]:
    return [{"value": v, "label": mapping[v]} for v in order if v in mapping]


# ---------------------------------------------------------------------------
# Shared view plumbing
# ---------------------------------------------------------------------------


class PluginActiveMixin:
    """404 unless this plugin is enabled for the event in the URL.

    pretix wraps a plugin's *presale* URLs with that check
    (pretix/multidomain/plugin_handler.py, ``_event_view(require_plugin=...)``)
    but it does not do so for control-panel URLs: those are included at the URL
    root and are reachable even for an event that has the plugin switched off.
    SPEC.md F1 asks for the opposite, and the navigation entry already hides
    itself, so the views should not stay open.
    """

    plugin_module = "pretix_custom_reports"

    def dispatch(self, request, *args, **kwargs):
        event = getattr(request, "event", None)
        if event is None or self.plugin_module not in event.get_plugins():
            raise Http404("This plugin is not active for this event.")
        return super().dispatch(request, *args, **kwargs)


class _ApiView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    """Base class for the editor's JSON endpoints.

    Permission checked through pretix's own mixin (SPEC.md section 4:
    "Verwende die pretix-Mixins, nicht eigene Prüfungen"). No ``csrf_exempt``
    anywhere -- the POST endpoints rely on Django's CSRF middleware and the
    browser side sends ``X-CSRFToken``.
    """

    permission = VIEW_PERMISSION

    #: Bumped only when the *response* shape changes incompatibly. The editor
    #: refuses to run against a payload it does not know.
    api_version = 1

    def json(self, payload: Dict[str, Any], status: int = 200) -> JsonResponse:
        return JsonResponse(
            payload,
            status=status,
            encoder=DjangoJSONEncoder,
            json_dumps_params={"ensure_ascii": False},
        )

    def fail(
        self,
        stage: str,
        issues: List[Dict[str, Any]],
        status: int = 400,
        **extra: Any,
    ) -> JsonResponse:
        """Uniform error envelope.

        ``stage`` tells the editor *where* it went wrong, which is what decides
        whether the message belongs on a widget or in the global error bar:

        ``request``    the body was not usable at all
        ``structure``  :func:`validate_definition` rejected it; ``path`` points
                       into the document
        ``fields``     structurally fine, but a key does not exist here
        ``compile``    resolvable, but not allowed for the resolved fields
        ``execute``    the query itself blew up
        """
        payload = {"ok": False, "stage": stage, "errors": issues}
        payload.update(extra)
        return self.json(payload, status=status)

    # -- body handling ----------------------------------------------------

    def read_json_body(self) -> Dict[str, Any]:
        """Parse the request body, or raise :class:`_BadRequest`."""
        try:
            raw = self.request.body.decode("utf-8")
        except UnicodeDecodeError:
            raise _BadRequest(ErrorCode.NOT_JSON, "Request body is not valid UTF-8.")
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise _BadRequest(ErrorCode.NOT_JSON, f"Request body is not JSON: {e}")
        if not isinstance(data, dict):
            raise _BadRequest(
                ErrorCode.WRONG_TYPE, "Request body must be a JSON object."
            )
        return data

    def read_definition(self, data: Dict[str, Any]) -> ReportDefinition:
        """Structurally validate ``data["definition"]``.

        Raises :class:`DefinitionValidationError` with *all* issues so the
        editor can mark every broken widget in one pass.
        """
        if "definition" not in data:
            raise _BadRequest(
                ErrorCode.MISSING, "Request body must carry a 'definition' object."
            )
        return validate_definition(data["definition"])


class _BadRequest(Exception):
    """Malformed request envelope (as opposed to a malformed definition)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def as_issues(self) -> List[Dict[str, Any]]:
        return [{"path": "", "code": self.code, "message": self.message}]


# ---------------------------------------------------------------------------
# Field library
# ---------------------------------------------------------------------------


class FieldLibraryView(_ApiView):
    """``GET`` the whole field library plus every enum the editor renders.

    One request, both report bases. That is deliberate: the base switcher has to
    tell the user *which fields fall away* before the switch happens (SPEC.md
    F3), and a second round trip for that would either be slow or race with
    local edits. Each field therefore carries a per-base availability block.

    Response (abridged)::

        {
          "ok": true, "api_version": 1, "schema_version": 1,
          "source": "stub",
          "bases": [{"value": "order", "label": "...", "help": "..."}],
          "groups": [{"id": "order", "label": "Order"}],
          "operators": {"exact": {"label": "is", "value_kind": "scalar",
                                  "relative": false, "negated": false}},
          "aggregates": {"sum": {"label": "Sum"}},
          "formats": {"date_style": [{"value": "iso", "label": "ISO 8601"}]},
          "format_family_for_datatype": {"money": "number_style"},
          "limits": {"columns": 200, "preview_rows": 20},
          "fields": [
            {"key": "order.status", "label": "Order status", "group": "order",
             "datatype": "choice", "value_scope": "global", "provider": "core",
             "choices": [{"value": "p", "label": "paid"}],
             "bases": {"order": {"available": true, "sortable": true,
                                 "requires_aggregate": false,
                                 "operators": ["exact", "in"],
                                 "aggregates": []},
                       "orderposition": {"available": false}}}
          ]
        }
    """

    def get(self, request, *args, **kwargs) -> JsonResponse:
        registry = get_registry()
        event = request.event

        per_base: Dict[str, Dict[str, Any]] = {}
        for base in Base:
            per_base[base.value] = dict(registry.get_fields(event, base))

        # Stable order: first appearance on base "order", then whatever only
        # exists on base "orderposition". The registry guarantees a
        # deterministic iteration order (protocols.FieldRegistry.get_fields).
        ordered_keys: List[str] = []
        for base in Base:
            for key in per_base[base.value]:
                if key not in ordered_keys:
                    ordered_keys.append(key)

        fields: List[Dict[str, Any]] = []
        seen_groups: List[str] = []
        for key in ordered_keys:
            payload = self._field_payload(key, per_base, event)
            if payload is None:
                continue
            fields.append(payload)
            if payload["group"] not in seen_groups:
                seen_groups.append(payload["group"])

        return self.json(
            {
                "ok": True,
                "api_version": self.api_version,
                "schema_version": SCHEMA_VERSION,
                "source": type(registry).__name__,
                "event": {
                    "slug": event.slug,
                    "currency": event.currency,
                    "name": str(event.name),
                },
                "bases": [
                    {
                        "value": base.value,
                        "label": BASE_LABELS[base.value],
                        "help": BASE_HELP[base.value],
                    }
                    for base in Base
                ],
                "groups": self._groups(seen_groups),
                "operators": {
                    op.value: {
                        "label": OPERATOR_LABELS.get(op.value, op.value),
                        "value_kind": OPERATOR_SPECS[op].value_kind.value,
                        "relative": OPERATOR_SPECS[op].relative,
                        "negated": OPERATOR_SPECS[op].negated,
                    }
                    for op in Operator
                },
                "aggregates": {
                    agg.value: {"label": AGGREGATE_LABELS[agg.value]}
                    for agg in Aggregate
                },
                "datatypes": {
                    dt.value: {"label": DATATYPE_LABELS.get(dt.value, dt.value)}
                    for dt in DataType
                },
                "sort_directions": _choices(
                    SORT_DIRECTION_LABELS, [d.value for d in SortDirection]
                ),
                "bool_ops": _choices(BOOL_OP_LABELS, [o.value for o in BoolOp]),
                "formats": {
                    "date_style": _choices(
                        DATE_STYLE_LABELS, [s.value for s in DateStyle]
                    ),
                    "number_style": _choices(
                        NUMBER_STYLE_LABELS, [s.value for s in NumberStyle]
                    ),
                    "boolean_style": _choices(
                        BOOLEAN_STYLE_LABELS, [s.value for s in BooleanStyle]
                    ),
                },
                "format_family_for_datatype": FORMAT_FAMILY_FOR_DATATYPE,
                "limits": {
                    "columns": MAX_COLUMNS,
                    "sort_entries": MAX_SORT_ENTRIES,
                    "filter_conditions": MAX_FILTER_CONDITIONS,
                    "filter_groups": MAX_FILTER_GROUPS,
                    "group_children": MAX_GROUP_CHILDREN,
                    "value_items": MAX_VALUE_ITEMS,
                    "string_value_length": MAX_STRING_VALUE_LENGTH,
                    "label_length": MAX_LABEL_LENGTH,
                    "separator_length": MAX_SEPARATOR_LENGTH,
                    "row_limit": MAX_ROW_LIMIT,
                    "day_count": MAX_DAY_COUNT,
                    "preview_rows": PREVIEW_ROW_LIMIT,
                },
                "fields": fields,
            }
        )

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _groups(seen: List[str]) -> List[Dict[str, Any]]:
        known = [g for g in GROUP_ORDERING if g in seen]
        unknown = sorted(g for g in seen if g not in GROUP_ORDERING)
        return [{"id": g, "label": GROUP_LABELS.get(g, g)} for g in known + unknown]

    def _field_payload(
        self, key: str, per_base: Dict[str, Dict[str, Any]], event
    ) -> Optional[Dict[str, Any]]:
        sample = self._sample(key, per_base)
        if sample is None:  # pragma: no cover - defensive
            return None
        if sample.deprecated:
            # Still resolvable for old reports, hidden from the library
            # (protocols.FieldRegistry.get_fields).
            return None

        bases: Dict[str, Any] = {}
        for base in Base:
            field = per_base[base.value].get(key)
            if field is None:
                bases[base.value] = {"available": False}
                continue
            aggregates = list(field.aggregates) or list(
                AGGREGATES_FOR_DATATYPE.get(field.datatype, ())
            )
            bases[base.value] = {
                "available": True,
                "sortable": bool(field.sortable),
                "requires_aggregate": field.needs_aggregate_on(base),
                "operators": [op.value for op in field.filter_operators],
                "aggregates": (
                    [a.value for a in field.aggregates] if field.aggregates else []
                ),
                "suggested_aggregates": [
                    a.value for a in aggregates if a in field.aggregates
                ],
            }

        return {
            "key": key,
            "label": str(sample.label),
            "group": sample.group,
            "datatype": sample.datatype.value,
            "help_text": str(sample.help_text) if sample.help_text else None,
            "provider": sample.provider,
            "value_scope": self._value_scope(key, per_base),
            "choices": self._choices_across_bases(key, per_base, event),
            "bases": bases,
        }

    # -- one key, two variants --------------------------------------------
    #
    # The same key can be two different ReportField objects, one per base --
    # the registry builds them separately because base support, aggregates and
    # allowed operators differ (registry/questions.py is the clearest case: on
    # base "orderposition" a question is a filterable, sortable column with its
    # options as choices, on base "order" it is an aggregate without filters).
    #
    # Everything base-dependent already lives in the per-base block. The three
    # helpers below decide what the *shared* half of the payload says, and they
    # exist because taking "whatever variant came first" silently turned a
    # choice question into a free-text filter box (SPEC.md F6 asks for the
    # opposite).

    @staticmethod
    def _sample(key: str, per_base: Dict[str, Dict[str, Any]]):
        """The variant a field is described from.

        The first one that stands on its own, i.e. that does not need an
        aggregate on its own base -- that is the variant carrying the field's
        real label, help text and choices. Falls back to the first variant
        found, so a field that is aggregate-only everywhere still gets a
        payload.
        """
        fallback = None
        for base in Base:
            field = per_base[base.value].get(key)
            if field is None:
                continue
            if fallback is None:
                fallback = field
            if not field.needs_aggregate_on(base):
                return field
        return fallback

    def _choices_across_bases(
        self, key: str, per_base: Dict[str, Dict[str, Any]], event
    ) -> Optional[List[Dict[str, Any]]]:
        """The value list of a field, from whichever variant declares one.

        Which values a field can hold does not depend on the report base, so a
        variant that omits the choices is missing information rather than
        describing a different domain.
        """
        for base in Base:
            field = per_base[base.value].get(key)
            if field is None or field.choices is None:
                continue
            resolved = self._field_choices(field, event)
            if resolved:
                return resolved
        return None

    @staticmethod
    def _value_scope(key: str, per_base: Dict[str, Dict[str, Any]]) -> str:
        """``event`` if *any* variant says so, otherwise ``global``.

        The strict answer has to win: this flag is what tells import that the
        stored filter values have to be remapped for the target event. Guessing
        ``global`` for a field that is in fact event specific would silently
        import references to another event's objects.
        """
        scope = None
        for base in Base:
            field = per_base[base.value].get(key)
            if field is None:
                continue
            if field.value_scope is not ValueScope.GLOBAL:
                return field.value_scope.value
            scope = field.value_scope
        return (scope or ValueScope.GLOBAL).value

    @staticmethod
    def _field_choices(field, event) -> Optional[List[Dict[str, Any]]]:
        """Evaluate the field's lazy choice callable.

        A field that offers choices gets a real select widget in the filter
        area; a field that does not falls back to a value list the user types
        into. Third-party callables are wrapped: a plugin raising in here must
        degrade that one field, not break the whole editor.
        """
        if field.choices is None:
            return None
        try:
            raw = field.choices(FieldContext(event=event, base=field.bases[0]))
        except Exception:
            return None
        out: List[Dict[str, Any]] = []
        for entry in raw or ():
            try:
                value, label = entry
            except (TypeError, ValueError):  # pragma: no cover - defensive
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                out.append({"value": value, "label": str(label)})
        return out or None


# ---------------------------------------------------------------------------
# Validation / round trip
# ---------------------------------------------------------------------------


class ValidateView(_ApiView):
    """``POST`` a draft definition, get the canonical document or the errors.

    This is the server side of the editor's round trip: whatever the browser
    built is normalised by :func:`validate_definition` and returned as
    ``definition``, byte-identical to what would be stored. The editor uses it
    for the "JSON" panel and before handing the form to the CRUD view.

    Request::

        {"definition": {...}}

    Response 200::

        {"ok": true, "definition": {...canonical...},
         "warnings": [{"path": "columns[3]", "code": "...", "message": "..."}]}

    Warnings are the registry-stage findings that do not prevent saving: an
    unresolvable key is a regular state (a renamed question identifier), and
    refusing to open such a report would make it unfixable.
    """

    def post(self, request, *args, **kwargs) -> JsonResponse:
        try:
            data = self.read_json_body()
            definition = self.read_definition(data)
        except _BadRequest as e:
            return self.fail("request", e.as_issues())
        except DefinitionValidationError as e:
            return self.fail("structure", [i.as_dict() for i in e.issues])

        return self.json(
            {
                "ok": True,
                "api_version": self.api_version,
                "definition": definition.as_dict(),
                "warnings": registry_warnings(definition, request.event),
            }
        )


def registry_warnings(definition: ReportDefinition, event: Any) -> List[Dict[str, Any]]:
    """Registry-stage findings for a structurally valid definition.

    Everything :func:`validate_definition` cannot know because it has no
    registry: unknown keys, fields not available on the chosen base, a missing
    or forbidden aggregate, an operator the field does not allow, a sort on a
    field that is not sortable. Same list the compiler would raise on -- but
    reported per document path so the editor can mark the exact widget.
    """
    registry = get_registry()
    base = definition.base
    out: List[Dict[str, Any]] = []

    def add(path: str, code: str, message: str) -> None:
        out.append({"path": path, "code": code, "message": message})

    for ref in definition.iter_field_references():
        field = registry.resolve(ref.key, event, base)
        if field is None:
            add(
                ref.path,
                "unknown_field",
                f"Field '{ref.key}' does not exist for this event on base "
                f"'{base}'.",
            )
            continue
        if ref.usage.value == "column":
            if field.needs_aggregate_on(base) and ref.aggregate is None:
                add(
                    ref.path,
                    "aggregate_required",
                    f"Field '{ref.key}' belongs to a single position and needs "
                    f"an aggregate on base '{base}'.",
                )
            if ref.aggregate is not None and not field.allows_aggregate(ref.aggregate):
                add(
                    ref.path,
                    "aggregate_not_allowed",
                    f"Field '{ref.key}' does not support aggregate "
                    f"'{ref.aggregate}'.",
                )
        elif ref.usage.value == "filter":
            if ref.operator is not None and not field.allows_operator(ref.operator):
                add(
                    ref.path,
                    "operator_not_allowed",
                    f"Operator '{ref.operator}' is not allowed for field "
                    f"'{ref.key}'.",
                )
        elif ref.usage.value == "sort" and not field.sortable:
            add(
                ref.path,
                "not_sortable",
                f"Field '{ref.key}' cannot be sorted on base '{base}'.",
            )
    return out


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class PreviewView(_ApiView):
    """``POST`` a draft definition, get at most :data:`PREVIEW_ROW_LIMIT` rows.

    Request::

        {"definition": {...}, "limit": 20, "total": true}

    ``limit`` is clamped into ``1..PREVIEW_ROW_LIMIT``; a client asking for more
    gets the limit, never more. ``total`` may be switched off by the client
    because the estimated total is a separate ``COUNT(*)`` and the expensive
    half of this endpoint.

    Response 200::

        {"ok": true,
         "columns": [{"key": "...", "label": "...", "datatype": "...",
                      "aggregate": null}],
         "rows": [["ABCDE", "19.00 EUR"]],
         "row_count": 20, "total": 137, "limit": 20, "truncated": true,
         "html": "<table ...>", "warnings": [...]}

    ``rows`` holds display strings, formatted on the server exactly like the
    preview table shows them (SPEC.md F2 asks for a server-rendered preview).
    ``html`` is the same data rendered through ``preview_table.html`` so the
    browser does not have to build a table and cannot mis-escape a cell.
    """

    def post(self, request, *args, **kwargs) -> JsonResponse:
        try:
            data = self.read_json_body()
            definition = self.read_definition(data)
        except _BadRequest as e:
            return self.fail("request", e.as_issues())
        except DefinitionValidationError as e:
            return self.fail("structure", [i.as_dict() for i in e.issues])

        limit = self._limit(data.get("limit"))
        want_total = data.get("total", True) is not False

        try:
            # preview=True is what puts the LIMIT into the SQL. Without it the
            # database would materialise the full result set and the row cap
            # would be a Python-side illusion (query/report.py::build_report).
            compiled = get_compiler().compile(definition, request.event, preview=True)
        except FieldResolutionError as e:
            return self.fail(
                "fields",
                registry_warnings(definition, request.event)
                or [{"path": "", "code": "unknown_field", "message": str(e)}],
                missing=list(e.keys),
            )
        except CompilationError as e:
            return self.fail(
                "compile",
                registry_warnings(definition, request.event)
                or [{"path": "", "code": "compile", "message": str(e)}],
            )
        except ContractError as e:  # pragma: no cover - defensive
            return self.fail(
                "compile", [{"path": "", "code": "compile", "message": str(e)}]
            )

        columns = [
            {
                "key": column.key,
                "label": column.label,
                "datatype": column.datatype.value,
                "aggregate": column.aggregate.value if column.aggregate else None,
            }
            for column in compiled.columns
        ]

        try:
            rows = self._rows(compiled, definition, limit, request.event)
            total = compiled.count() if want_total else None
        except Exception as e:  # noqa: BLE001 - a broken field must not 500 the editor
            return self.fail(
                "execute",
                [
                    {
                        "path": "",
                        "code": "execution_failed",
                        "message": (
                            str(e)
                            if settings.DEBUG
                            else _(
                                "The preview could not be executed. Please check "
                                "the selected fields."
                            )
                        ),
                    }
                ],
                status=400,
            )

        payload: Dict[str, Any] = {
            "ok": True,
            "api_version": self.api_version,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "total": total,
            "limit": limit,
            "truncated": total is not None and total > len(rows),
            "warnings": registry_warnings(definition, request.event),
        }
        payload["html"] = render_to_string(
            "pretix_custom_reports/preview_table.html",
            {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "total": total,
                "limit": limit,
                "truncated": payload["truncated"],
            },
            request=request,
        )
        return self.json(payload)

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _limit(raw: Any) -> int:
        """Clamp the requested row count. Never above :data:`PREVIEW_ROW_LIMIT`."""
        if isinstance(raw, bool) or not isinstance(raw, int):
            return PREVIEW_ROW_LIMIT
        return max(1, min(raw, PREVIEW_ROW_LIMIT))

    def _rows(
        self,
        compiled: Any,
        definition: ReportDefinition,
        limit: int,
        event: Any,
    ) -> List[List[str]]:
        formats_by_index = self._formats_by_index(definition, compiled)
        out: List[List[str]] = []
        for row in compiled.iter_rows(limit=limit):
            out.append(
                [
                    format_cell(
                        value,
                        formats_by_index[index][0],
                        formats_by_index[index][1],
                        event,
                    )
                    for index, value in enumerate(row)
                ]
            )
            if len(out) >= limit:  # belt and braces: never exceed the limit
                break
        return out

    @staticmethod
    def _formats_by_index(definition: ReportDefinition, compiled: Any):
        """Pair every compiled column with the format from the definition.

        ``CompiledReport.columns`` has hidden columns already dropped, so the
        indices of ``definition.columns`` and ``compiled.columns`` do not line
        up; walk the visible ones in order.
        """
        visible = [c for c in definition.columns if not c.hidden]
        out = []
        for index, column in enumerate(compiled.columns):
            fmt = visible[index].format if index < len(visible) else None
            out.append((fmt, column.datatype))
        return out


def format_cell(value: Any, fmt: Any, datatype: Any, event: Any) -> str:
    """Render one preview cell as a display string.

    Preview-only. This is *not* the exporter's renderer: CSV and XLSX go through
    ``ListExporter`` (CLAUDE.md rule 6) and keep ``Decimal``/``datetime`` as
    native types. Values that already arrive as ``str`` are passed through
    unchanged, so this stays a no-op if the compiler formats a value itself.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value

    if isinstance(value, bool):
        style = getattr(fmt, "boolean_style", None)
        if style is BooleanStyle.TRUE_FALSE:
            return "true" if value else "false"
        if style is BooleanStyle.ONE_ZERO:
            return "1" if value else "0"
        return str(_("Yes")) if value else str(_("No"))

    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return _format_temporal(value, getattr(fmt, "date_style", None), event)

    if isinstance(value, (decimal.Decimal, int, float)):
        return _format_number(
            value, getattr(fmt, "number_style", None), datatype, event
        )

    return str(value)


def _format_temporal(value: Any, style: Any, event: Any) -> str:
    if isinstance(value, datetime.datetime) and timezone.is_aware(value):
        try:
            value = timezone.localtime(value, event.timezone)
        except Exception:  # pragma: no cover - defensive
            pass
    is_datetime = isinstance(value, datetime.datetime)
    is_time = isinstance(value, datetime.time) and not is_datetime

    if style is DateStyle.ISO:
        return value.isoformat()
    if style is DateStyle.TIME_ONLY or (is_time and style is None):
        return formats.date_format(value, "TIME_FORMAT")
    if style is DateStyle.DATE_ONLY:
        return formats.date_format(value, "SHORT_DATE_FORMAT")
    if style is DateStyle.SHORT:
        return formats.date_format(
            value, "SHORT_DATETIME_FORMAT" if is_datetime else "SHORT_DATE_FORMAT"
        )
    if style is DateStyle.LONG:
        return formats.date_format(value, "l, j F Y H:i" if is_datetime else "l, j F Y")
    return formats.date_format(
        value, "DATETIME_FORMAT" if is_datetime else "DATE_FORMAT"
    )


def _format_number(value: Any, style: Any, datatype: Any, event: Any) -> str:
    if style is NumberStyle.CURRENCY or (style is None and datatype is DataType.MONEY):
        try:
            from pretix.base.templatetags.money import money_filter

            return money_filter(decimal.Decimal(str(value)), event.currency)
        except Exception:  # pragma: no cover - defensive
            return str(value)
    if style is NumberStyle.LOCALIZED:
        return formats.number_format(value, use_l10n=True)
    return str(value)


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
#
# Owned by frontend-dev, wired into urls.py by the integrator -- see
# handoff/requests/frontend-dev-an-integrator-urls.md. Keeping the patterns next
# to the views is what lets the editor's tests build a URLconf without touching
# urls.py, which belongs to the integrator (ORCHESTRIERUNG.md section 5).
#
# The full "control/event/..." prefix is spelled out because a plugin's
# urlpatterns are included at the URL root, not below /control/
# (pretix/multidomain/maindomain_urlconf.py; see the comment in urls.py).

_EVENT_PREFIX = r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports/"

api_urlpatterns = [
    re_path(
        _EVENT_PREFIX + r"api/fields/$",
        FieldLibraryView.as_view(),
        name="api.fields",
    ),
    re_path(
        _EVENT_PREFIX + r"api/validate/$",
        ValidateView.as_view(),
        name="api.validate",
    ),
    re_path(
        _EVENT_PREFIX + r"api/preview/$",
        PreviewView.as_view(),
        name="api.preview",
    ),
]
