"""``register_report_fields``: how another plugin contributes fields (SPEC.md F5).

Owner: registry-dev.

This is **our own** signal, not one of pretix'. It lives here and not in
``pretix_custom_reports/signals.py`` (which belongs to the integrator and holds
our *receivers*) so that third-party plugins have a stable import that does not
change when our wiring changes::

    from pretix_custom_reports.registry.signals import register_report_fields

It is an ``EventPluginSignal``, so a receiver only fires for events that have the
contributing plugin enabled, and pretix rejects a receiver that does not belong to
an app with ``PretixPluginMeta`` (``pretix/base/signals.py:261-274``). Both of
those are wanted: a plugin that is switched off must not add columns.

Contract for receivers
----------------------

::

    from django.dispatch import receiver
    from pretix_custom_reports.contracts import (
        Base, DataType, Operator, ReportField, plugin_field_key,
    )
    from pretix_custom_reports.registry.signals import register_report_fields

    APP_LABEL = "pretix_myplugin"

    @receiver(register_report_fields, dispatch_uid="myplugin_report_fields")
    def report_fields(sender, base, **kwargs):
        return [
            ReportField(
                key=plugin_field_key(APP_LABEL, "zone"),
                label="Zone",
                group="My plugin",
                datatype=DataType.STRING,
                bases=(Base.coerce(base),),
                orm_path="pcr_myplugin_zone",
                annotation=lambda ctx: {"pcr_myplugin_zone": ...},
                filter_operators=(Operator.EXACT, Operator.CONTAINS),
                sortable=True,
                provider=APP_LABEL,
            )
        ]

* ``sender`` is the ``Event``; ``base`` is the report base as a string
  (``"order"`` or ``"orderposition"``). Return the fields valid for *that* base.
* Return a list. ``None`` is tolerated, an exception is caught -- but both are
  reported in the registry diagnostics rather than swallowed.
* The key **must** be ``plugin.<django_app_label>.<name>``, and ``provider``
  **must** be that same app label. Django guarantees app labels are unique per
  installation, so two plugins cannot collide (ADR 0001 section 2).

Namespace and conflict rule
---------------------------

The 15 core namespaces (``order``, ``position``, ``answer``, ``meta``,
``computed``, ...) are reserved; a plugin field in one of them is dropped, not
merged. If a plugin key nevertheless duplicates an existing key, **core wins**,
and between two plugins the first receiver wins. pretix orders receivers
deterministically (core modules first, then by ``(__module__, __name__)``,
``pretix/base/signals.py:242-249``), so "first" is reproducible rather than
whatever import order happened.

Rejections are never silent: they are logged at warning level and collected in
:class:`~pretix_custom_reports.registry.diagnostics.SkippedField`.
"""

from typing import Any, Dict, Iterable, List, Tuple

import logging
from pretix.base.signals import EventPluginSignal

from pretix_custom_reports.contracts import (
    KEY_SEPARATOR,
    PROVIDER_CORE,
    REGISTER_FIELDS_SIGNAL_NAME,
    RESERVED_NAMESPACES,
    Base,
    ReportField,
    is_plugin_key,
)
from pretix_custom_reports.registry.diagnostics import (
    REASON_DUPLICATE_KEY,
    REASON_NOT_A_FIELD,
    REASON_RECEIVER_FAILED,
    REASON_RESERVED_NAMESPACE,
    REASON_UNSUPPORTED_BASE,
    REASON_WRONG_PROVIDER,
    SOURCE_PLUGIN,
    SkippedField,
)

__all__ = [
    "SIGNAL_NAME",
    "collect_plugin_fields",
    "register_report_fields",
]

logger = logging.getLogger(__name__)

#: Mirrors ``contracts.REGISTER_FIELDS_SIGNAL_NAME`` so the two cannot drift.
SIGNAL_NAME = REGISTER_FIELDS_SIGNAL_NAME

register_report_fields = EventPluginSignal()
"""
Arguments: ``base``

Sent out to collect :class:`~pretix_custom_reports.contracts.ReportField`
objects from other plugins. Receivers should return a list of ``ReportField``
whose keys are all in the ``plugin.<app_label>.`` namespace.

As with all event-plugin signals, the ``sender`` keyword argument contains the
event. ``base`` is the report base as a string.
"""


def _reject(
    skipped: List[SkippedField], key: str, reason: str, detail: str = ""
) -> None:
    entry = SkippedField(key=key, source=SOURCE_PLUGIN, reason=reason, detail=detail)
    skipped.append(entry)
    logger.warning("pretix-custom-reports rejected a plugin report field: %s", entry)


def _receiver_name(receiver: Any) -> str:
    module = getattr(receiver, "__module__", "?")
    name = getattr(receiver, "__qualname__", getattr(receiver, "__name__", "?"))
    return f"{module}.{name}"


def collect_plugin_fields(
    event: Any,
    base: Base,
    taken_keys: Iterable[str],
) -> Tuple[Dict[str, ReportField], Tuple[SkippedField, ...]]:
    """Ask every enabled plugin for its fields and validate the answers.

    *taken_keys* are the keys the core registry already published; anything
    colliding with them is dropped (core wins).

    Uses ``send_robust``: a broken third-party plugin must not take the report
    editor down with it. The exception is reported, not re-raised.
    """
    coerced = Base.coerce(base)
    fields: Dict[str, ReportField] = {}
    skipped: List[SkippedField] = []
    used = set(taken_keys)

    responses = register_report_fields.send_robust(event, base=str(coerced))
    for receiver, response in responses:
        if isinstance(response, Exception):
            _reject(
                skipped,
                key=_receiver_name(receiver),
                reason=REASON_RECEIVER_FAILED,
                detail=f"{type(response).__name__}: {response}",
            )
            continue
        if not response:
            continue
        try:
            candidates = list(response)
        except TypeError:
            _reject(
                skipped,
                key=_receiver_name(receiver),
                reason=REASON_NOT_A_FIELD,
                detail="receiver did not return an iterable of ReportField",
            )
            continue

        for candidate in candidates:
            if not isinstance(candidate, ReportField):
                _reject(
                    skipped,
                    key=_receiver_name(receiver),
                    reason=REASON_NOT_A_FIELD,
                    detail=f"got {type(candidate).__name__}",
                )
                continue

            key = candidate.key
            namespace = key.split(KEY_SEPARATOR, 1)[0]

            if namespace in RESERVED_NAMESPACES or not is_plugin_key(key):
                _reject(
                    skipped,
                    key=key,
                    reason=REASON_RESERVED_NAMESPACE,
                    detail=(
                        "namespace {!r} belongs to the core registry; use "
                        "plugin_field_key(app_label, name)".format(namespace)
                    ),
                )
                continue

            provider = candidate.provider
            expected_prefix = f"plugin{KEY_SEPARATOR}{provider}{KEY_SEPARATOR}"
            if provider == PROVIDER_CORE or not key.startswith(expected_prefix):
                _reject(
                    skipped,
                    key=key,
                    reason=REASON_WRONG_PROVIDER,
                    detail=(
                        "provider {!r} does not match the app label in the key; "
                        "expected the key to start with {!r}".format(
                            provider, expected_prefix
                        )
                    ),
                )
                continue

            if not candidate.supports_base(coerced):
                _reject(
                    skipped,
                    key=key,
                    reason=REASON_UNSUPPORTED_BASE,
                    detail=f"field does not declare base {coerced}",
                )
                continue

            if key in used:
                _reject(
                    skipped,
                    key=key,
                    reason=REASON_DUPLICATE_KEY,
                    detail="a field with this key already exists; core and the "
                    "first plugin win",
                )
                continue

            used.add(key)
            fields[key] = candidate

    return fields, tuple(skipped)
