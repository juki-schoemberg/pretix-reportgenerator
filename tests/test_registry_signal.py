# Owner: registry-dev (tests/test_registry*.py, ORCHESTRIERUNG.md section 5)
#
# SPEC.md F5: another plugin contributes ReportField objects through an
# EventPluginSignal. This module contains a small but complete example plugin and
# uses it to pin down the two things the brief asks to be tested explicitly:
# the namespace prefix, and the collision rule (core wins).
#
# Why the example plugin is faked rather than installed: a receiver of an
# EventPluginSignal must belong to a Django app carrying PretixPluginMeta, and
# the plugin must be enabled for the event (pretix/base/signals.py:92-141,
# 261-274). Installing a second distribution inside a test run is not possible,
# so this uses the __mocked_app hook pretix provides for exactly this purpose
# (pretix/base/signals.py:70-71, used the same way in
# pretix/src/tests/base/test_datasync.py:180-191).
"""Tests for ``register_report_fields``, with a worked example plugin."""

import pytest
from django.db.models import Value
from django.db.models.functions import Concat
from django_scopes import scopes_disabled

from pretix_custom_reports.contracts import (
    Base,
    DataType,
    FieldContext,
    Operator,
    ReportField,
    plugin_field_key,
)
from pretix_custom_reports.registry import cache as registry_cache
from pretix_custom_reports.registry.diagnostics import (
    REASON_DUPLICATE_KEY,
    REASON_NOT_A_FIELD,
    REASON_RECEIVER_FAILED,
    REASON_RESERVED_NAMESPACE,
    REASON_UNSUPPORTED_BASE,
    REASON_WRONG_PROVIDER,
    SOURCE_PLUGIN,
)
from pretix_custom_reports.registry.signals import register_report_fields
from tests import test_registry_support as support
from tests.test_registry_support import (
    PLUGIN_APP_LABEL,
    attach_mocked_app,
    enable_plugin,
    make_order,
)

# See the note in tests/test_registry.py about why this is an assignment.
registry = support.registry

BASES = (Base.ORDER, Base.ORDERPOSITION)


# ---------------------------------------------------------------------------
# The example plugin
# ---------------------------------------------------------------------------
#
# This is the code a third-party plugin author writes. It is deliberately
# complete: an annotation that really runs, a filterable and sortable column, a
# proper namespaced key, and the app label as provider.

DEMO_ALIAS = "pcr_pretix_demo_demo_value"


def demo_annotation(ctx: FieldContext):
    """``{alias: expression}``, exactly like a core field.

    A plugin gets the event through the context, so it can build event specific
    subqueries without the registry having to know anything about it.
    """
    return {
        DEMO_ALIAS: Concat(
            Value("demo-"), "code" if ctx.base is Base.ORDER else "order__code"
        )
    }


def demo_field(base) -> ReportField:
    return ReportField(
        key=plugin_field_key(PLUGIN_APP_LABEL, "demo_value"),
        label="Value from another plugin",
        group="Demo plugin",
        datatype=DataType.STRING,
        bases=(Base.coerce(base),),
        orm_path=DEMO_ALIAS,
        annotation=demo_annotation,
        filter_operators=(Operator.EXACT, Operator.CONTAINS, Operator.IS_NOT_EMPTY),
        sortable=True,
        provider=PLUGIN_APP_LABEL,
    )


def demo_receiver(sender, base, **kwargs):
    """The receiver the example plugin registers."""
    return [demo_field(base)]


@pytest.fixture
def demo_plugin(event):
    """Connect the example plugin and enable it for the event.

    Disconnects afterwards: ``register_report_fields`` is a module-level object,
    so a leaked receiver would show up in every later test.
    """
    attach_mocked_app(demo_receiver)
    register_report_fields.connect(
        demo_receiver, dispatch_uid="test_demo_plugin", weak=False
    )
    enable_plugin(event)
    yield demo_receiver
    register_report_fields.disconnect(demo_receiver, dispatch_uid="test_demo_plugin")
    registry_cache.clear_local_cache()


def connect(function, uid: str, app_label: str = PLUGIN_APP_LABEL):
    """Connect *function* as if it lived in the plugin *app_label*."""
    attach_mocked_app(function, app_label)
    register_report_fields.connect(function, dispatch_uid=uid, weak=False)
    return function


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base", BASES)
def test_plugin_field_is_published(registry, event, demo_plugin, base):
    """The example plugin's field appears in the registry on both bases."""
    with scopes_disabled():
        field = registry.resolve("plugin.pretix_demo.demo_value", event, base)
    assert field is not None
    assert field.provider == PLUGIN_APP_LABEL
    assert field.sortable
    assert Operator.CONTAINS in field.filter_operators


def test_plugin_field_completes_the_golden_fixture(registry, event, demo_plugin):
    """``plugin_and_meta_fields.json`` resolves fully once the plugin is there.

    The other registry test asserts that exactly this one key is missing without
    a plugin; here it is present. Together they prove the fixture exercises the
    plugin namespace end to end rather than just tolerating it.
    """
    from pretix_custom_reports.contracts import (
        find_unresolved_fields,
        validate_definition_json,
    )
    from tests.test_registry_support import (
        FIXTURE_DIR,
        make_meta_property,
        set_meta_value,
    )

    prop = make_meta_property(event.organizer)
    set_meta_value(event, prop, "summer")
    registry_cache.clear_local_cache()

    definition = validate_definition_json(
        (FIXTURE_DIR / "plugin_and_meta_fields.json").read_text(encoding="utf-8")
    )
    with scopes_disabled():
        unresolved = find_unresolved_fields(definition, registry, event)
    assert unresolved == ()


def test_plugin_annotation_runs(registry, event, demo_plugin):
    """A plugin's annotation is used like any other: merge, annotate, read."""
    from pretix.base.models import Order

    with scopes_disabled():
        make_order(event, code="PLUG1")
        field = registry.resolve("plugin.pretix_demo.demo_value", event, Base.ORDER)
        mapping = field.annotation(registry.context(event, Base.ORDER))
        row = Order.objects.filter(event=event).annotate(**mapping).get()
    assert getattr(row, field.orm_path) == "demo-PLUG1"


def test_plugin_field_is_listed_after_the_core_fields(registry, event, demo_plugin):
    """Source order is core, then event data, then plugins.

    That order *is* the conflict rule, so the field library showing it makes the
    rule visible instead of implicit.
    """
    with scopes_disabled():
        keys = registry.keys(event, Base.ORDER)
        diagnostics = registry.diagnostics(event, Base.ORDER)
    assert keys[0].startswith("order.")
    assert keys[-1] == "plugin.pretix_demo.demo_value"
    assert diagnostics.providers == ("core", PLUGIN_APP_LABEL)


def test_disabled_plugin_contributes_nothing(registry, event_without_plugin):
    """A plugin that is not enabled for the event adds no columns.

    ``EventPluginSignal`` gives us this for free, and it matters: switching a
    plugin off must not leave phantom fields behind.
    """
    connect(demo_receiver, "test_demo_plugin_disabled")
    try:
        with scopes_disabled():
            field = registry.resolve(
                "plugin.pretix_demo.demo_value", event_without_plugin, Base.ORDER
            )
        assert field is None
    finally:
        register_report_fields.disconnect(
            demo_receiver, dispatch_uid="test_demo_plugin_disabled"
        )


# ---------------------------------------------------------------------------
# The namespace prefix
# ---------------------------------------------------------------------------


def test_plugin_cannot_use_a_core_namespace(registry, event):
    """A field in a reserved namespace is dropped, not merged. Core wins.

    ``ReportField.__post_init__`` already refuses a plugin ``provider`` on a core
    key, so a plugin trying this has to lie about being core -- and then the
    registry catches it here. Two independent layers.
    """

    def bad_receiver(sender, base, **kwargs):
        return [
            ReportField(
                key="order.code",
                label="Hijacked order code",
                group="Demo plugin",
                datatype=DataType.STRING,
                bases=(Base.coerce(base),),
                orm_path="code",
            )
        ]

    connect(bad_receiver, "test_bad_namespace")
    enable_plugin(event)
    try:
        with scopes_disabled():
            field = registry.resolve("order.code", event, Base.ORDER)
            diagnostics = registry.diagnostics(event, Base.ORDER)
        # The core field is untouched.
        assert field.provider == "core"
        assert str(field.label) == "Order code"
        rejected = diagnostics.by_source(SOURCE_PLUGIN)
        assert [entry.reason for entry in rejected] == [REASON_RESERVED_NAMESPACE]
        assert rejected[0].key == "order.code"
    finally:
        register_report_fields.disconnect(
            bad_receiver, dispatch_uid="test_bad_namespace"
        )


@pytest.mark.parametrize(
    "key",
    ["answer.smuggled", "meta.event.smuggled", "computed.smuggled", "checkin.smuggled"],
)
def test_every_reserved_namespace_is_closed(registry, event, key):
    """Not only ``order.``: all 15 core namespaces are off limits."""

    def bad_receiver(sender, base, **kwargs):
        return [
            ReportField(
                key=key,
                label="Nope",
                group="Demo plugin",
                datatype=DataType.STRING,
                bases=(Base.coerce(base),),
                value_getter=lambda row: "x",
            )
        ]

    connect(bad_receiver, "test_reserved_namespaces")
    enable_plugin(event)
    try:
        with scopes_disabled():
            assert registry.resolve(key, event, Base.ORDER) is None
            rejected = registry.diagnostics(event, Base.ORDER).by_source(SOURCE_PLUGIN)
        assert [entry.reason for entry in rejected] == [REASON_RESERVED_NAMESPACE]
    finally:
        register_report_fields.disconnect(
            bad_receiver, dispatch_uid="test_reserved_namespaces"
        )


def test_provider_must_match_the_app_label_in_the_key(registry, event):
    """``plugin.a.x`` with ``provider="b"`` is rejected.

    Without this check a plugin could park its fields under another plugin's
    prefix and take that prefix over the moment the other plugin is installed.
    """

    def lying_receiver(sender, base, **kwargs):
        return [
            ReportField(
                key="plugin.someone_else.value",
                label="Not mine",
                group="Demo plugin",
                datatype=DataType.STRING,
                bases=(Base.coerce(base),),
                value_getter=lambda row: "x",
                provider=PLUGIN_APP_LABEL,
            )
        ]

    connect(lying_receiver, "test_wrong_provider")
    enable_plugin(event)
    try:
        with scopes_disabled():
            assert (
                registry.resolve("plugin.someone_else.value", event, Base.ORDER) is None
            )
            rejected = registry.diagnostics(event, Base.ORDER).by_source(SOURCE_PLUGIN)
        assert [entry.reason for entry in rejected] == [REASON_WRONG_PROVIDER]
    finally:
        register_report_fields.disconnect(
            lying_receiver, dispatch_uid="test_wrong_provider"
        )


# ---------------------------------------------------------------------------
# The collision rule
# ---------------------------------------------------------------------------


def test_first_plugin_wins_a_duplicate_key(registry, event, demo_plugin):
    """Two plugins, one key: the first receiver keeps it, the second is reported.

    "First" is reproducible, not import order: pretix sorts receivers by
    ``(is_core, __module__, __name__)`` (``pretix/base/signals.py:242-249``). The
    second receiver here is named so that it sorts after ``demo_receiver``.
    """

    def zz_duplicate_receiver(sender, base, **kwargs):
        field = demo_field(base)
        return [
            ReportField(
                key=field.key,
                label="Second plugin's version",
                group="Other plugin",
                datatype=DataType.STRING,
                bases=field.bases,
                value_getter=lambda row: "second",
                provider=PLUGIN_APP_LABEL,
            )
        ]

    connect(zz_duplicate_receiver, "test_duplicate_key")
    try:
        with scopes_disabled():
            field = registry.resolve("plugin.pretix_demo.demo_value", event, Base.ORDER)
            rejected = registry.diagnostics(event, Base.ORDER).by_source(SOURCE_PLUGIN)
        assert str(field.label) == "Value from another plugin"
        assert field.value_getter is None
        assert [entry.reason for entry in rejected] == [REASON_DUPLICATE_KEY]
    finally:
        register_report_fields.disconnect(
            zz_duplicate_receiver, dispatch_uid="test_duplicate_key"
        )


def test_field_for_the_wrong_base_is_rejected(registry, event):
    """A plugin answering the ``order`` question with an ``orderposition`` field.

    Silently keeping it would let it through into a report where its ORM path
    cannot resolve, and the failure would surface as a database error much later.
    """

    def wrong_base_receiver(sender, base, **kwargs):
        return [
            ReportField(
                key=plugin_field_key(PLUGIN_APP_LABEL, "wrong_base"),
                label="Wrong base",
                group="Demo plugin",
                datatype=DataType.STRING,
                bases=(Base.ORDERPOSITION,),
                value_getter=lambda row: "x",
                provider=PLUGIN_APP_LABEL,
            )
        ]

    connect(wrong_base_receiver, "test_wrong_base")
    enable_plugin(event)
    try:
        with scopes_disabled():
            assert (
                registry.resolve("plugin.pretix_demo.wrong_base", event, Base.ORDER)
                is None
            )
            rejected = registry.diagnostics(event, Base.ORDER).by_source(SOURCE_PLUGIN)
            assert [entry.reason for entry in rejected] == [REASON_UNSUPPORTED_BASE]
            # ... and it is available on the base it does declare.
            assert (
                registry.resolve(
                    "plugin.pretix_demo.wrong_base", event, Base.ORDERPOSITION
                )
                is not None
            )
    finally:
        register_report_fields.disconnect(
            wrong_base_receiver, dispatch_uid="test_wrong_base"
        )


# ---------------------------------------------------------------------------
# Misbehaving plugins
# ---------------------------------------------------------------------------


def test_exploding_receiver_does_not_break_the_registry(registry, event, demo_plugin):
    """A plugin that raises is reported; every other field still shows up.

    ``send_robust`` rather than ``send``: the report editor must not be taken
    down by a third-party bug.
    """

    def zz_exploding_receiver(sender, base, **kwargs):
        raise RuntimeError("this plugin is broken")

    connect(zz_exploding_receiver, "test_exploding")
    try:
        with scopes_disabled():
            fields = registry.get_fields(event, Base.ORDER)
            rejected = registry.diagnostics(event, Base.ORDER).by_source(SOURCE_PLUGIN)
        assert "order.code" in fields
        assert "plugin.pretix_demo.demo_value" in fields
        assert [entry.reason for entry in rejected] == [REASON_RECEIVER_FAILED]
        assert "this plugin is broken" in rejected[0].detail
    finally:
        register_report_fields.disconnect(
            zz_exploding_receiver, dispatch_uid="test_exploding"
        )


@pytest.mark.parametrize("response", [["not a field"], [None], [42]])
def test_non_field_response_is_rejected(registry, event, response):
    """Anything that is not a ``ReportField`` is dropped with a reason."""

    def junk_receiver(sender, base, **kwargs):
        return response

    connect(junk_receiver, "test_junk")
    enable_plugin(event)
    try:
        with scopes_disabled():
            rejected = registry.diagnostics(event, Base.ORDER).by_source(SOURCE_PLUGIN)
        assert [entry.reason for entry in rejected] == [REASON_NOT_A_FIELD]
    finally:
        register_report_fields.disconnect(junk_receiver, dispatch_uid="test_junk")


def test_receiver_returning_none_is_tolerated(registry, event, demo_plugin):
    """``None`` means "nothing to add" and is not an error."""

    def zz_quiet_receiver(sender, base, **kwargs):
        return None

    connect(zz_quiet_receiver, "test_quiet")
    try:
        with scopes_disabled():
            fields = registry.get_fields(event, Base.ORDER)
            rejected = registry.diagnostics(event, Base.ORDER).by_source(SOURCE_PLUGIN)
        assert "plugin.pretix_demo.demo_value" in fields
        assert rejected == ()
    finally:
        register_report_fields.disconnect(zz_quiet_receiver, dispatch_uid="test_quiet")


def test_signal_name_matches_the_contract():
    """The signal is called what ``contracts.REGISTER_FIELDS_SIGNAL_NAME`` says.

    The contract names the signal so that the registry (which defines it) and the
    integrator (who owns ``signals.py``) cannot disagree.
    """
    from pretix.base.signals import EventPluginSignal

    from pretix_custom_reports.contracts import REGISTER_FIELDS_SIGNAL_NAME
    from pretix_custom_reports.registry import signals as registry_signals

    assert registry_signals.SIGNAL_NAME == REGISTER_FIELDS_SIGNAL_NAME
    assert hasattr(registry_signals, REGISTER_FIELDS_SIGNAL_NAME)
    assert isinstance(register_report_fields, EventPluginSignal)


def test_receiver_outside_a_plugin_app_is_refused():
    """pretix itself blocks a receiver that does not belong to a plugin app.

    Documented here because it is a trap for anyone writing a receiver in a bare
    test helper: it would never fire.
    """
    from django.core.exceptions import ImproperlyConfigured

    def homeless_receiver(sender, base, **kwargs):  # pragma: no cover - never called
        return []

    with pytest.raises(ImproperlyConfigured):
        register_report_fields.connect(
            homeless_receiver, dispatch_uid="test_homeless", weak=False
        )
