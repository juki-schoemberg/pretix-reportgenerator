"""Tests for ``pretix_custom_reports/exporters.py``.

Owner from wave 2 on: exporter-dev (ORCHESTRIERUNG.md section 5).

The module under test is thin on purpose -- rows come from the query compiler,
bytes come from ``ListExporter`` -- so these tests concentrate on the seams
where a saved report meets pretix' export machinery, and on the four failure
modes that would otherwise be invisible:

1. a scheduled export whose report was deleted (five Celery retries and the
   word "Internal Error" instead of a sentence naming the report),
2. a multi-event export over an event where a column does not resolve,
3. a relative date filter that gets frozen at save time instead of being
   re-evaluated per run,
4. CSV/XLSX formula injection -- which pretix already neutralises, so the test
   asserts that it happens rather than adding a second layer.

Registration is done by hand here. ``signals.py`` belongs to the integrator and
does not yet connect our receivers (see
handoff/requests/exporter-dev-an-integrator-signals.md); the ``registered``
fixture connects exactly the functions from that request, so these tests fail
the moment the copy-ready lines stop matching reality.
"""

import datetime
import pytest
import warnings
from decimal import Decimal
from django.core import mail as djmail
from django.utils.timezone import now
from django_scopes import scope, scopes_disabled
from freezegun import freeze_time
from pretix.base.exporter import ListExporter
from pretix.base.models import (
    Event,
    Item,
    Order,
    OrderPosition,
    Question,
    QuestionAnswer,
    ScheduledEventExport,
    ScheduledOrganizerExport,
    Team,
    User,
)
from pretix.base.services.export import (
    ExportError,
    init_event_exporter,
    init_event_exporters,
    init_organizer_exporters,
    run_scheduled_exports,
)
from pretix.base.signals import (
    register_data_exporters,
    register_multievent_data_exporters,
)

from pretix_custom_reports import contracts, exporters
from pretix_custom_reports.models import ReportDefinition

DISPATCH_UID = "pretix_custom_reports_exporter"
MULTI_DISPATCH_UID = "pretix_custom_reports_multiexporter"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registered():
    """Connect the two receivers exactly as the integrator will.

    ``register_multievent_data_exporters`` is an
    ``OrganizerPluginSignal(allow_legacy_plugins=True)`` and this plugin is
    event level, so ``connect()`` emits a ``DeprecationWarning``
    (pretix/base/signals.py:301-306). pretix' own test config filters it; ours
    does not, so it is silenced here -- and only here, deliberately narrow, so
    that a *different* deprecation would still be visible.
    """
    register_data_exporters.connect(
        exporters.register_report_exporter, dispatch_uid=DISPATCH_UID
    )
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*organizer-level.*",
            category=DeprecationWarning,
        )
        register_multievent_data_exporters.connect(
            exporters.register_multievent_report_exporter,
            dispatch_uid=MULTI_DISPATCH_UID,
        )
    yield
    register_data_exporters.disconnect(dispatch_uid=DISPATCH_UID)
    register_multievent_data_exporters.disconnect(dispatch_uid=MULTI_DISPATCH_UID)


@pytest.fixture
def orders(event):
    """Two paid orders, one canceled position, one test mode order.

    The test mode order and the canceled position exist so that the run-time
    overrides have something to switch on and off.
    """
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        item = Item.objects.create(
            event=event, name="Ticket", internal_name="ticket", default_price=23
        )
        first = Order.objects.create(
            event=event,
            code="AAAAA",
            status=Order.STATUS_PAID,
            email="a@example.org",
            sales_channel=channel,
            datetime=now() - datetime.timedelta(days=2),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("23.00"),
            comment="plain",
        )
        OrderPosition.objects.create(
            order=first, item=item, price=Decimal("23.00"), positionid=1
        )
        OrderPosition.all.create(
            order=first,
            item=item,
            price=Decimal("5.00"),
            positionid=2,
            canceled=True,
        )
        second = Order.objects.create(
            event=event,
            code="BBBBB",
            status=Order.STATUS_PENDING,
            email="b@example.org",
            sales_channel=channel,
            datetime=now() - datetime.timedelta(days=40),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("11.00"),
            comment="plain",
        )
        OrderPosition.objects.create(
            order=second, item=item, price=Decimal("11.00"), positionid=1
        )
        testmode = Order.objects.create(
            event=event,
            code="CCCCC",
            status=Order.STATUS_PAID,
            email="c@example.org",
            sales_channel=channel,
            testmode=True,
            datetime=now() - datetime.timedelta(days=1),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("7.00"),
            comment="plain",
        )
        OrderPosition.objects.create(
            order=testmode, item=item, price=Decimal("7.00"), positionid=1
        )
        return {"item": item, "first": first, "second": second, "testmode": testmode}


def make_report(event=None, organizer=None, identifier="codes", **kwargs):
    """A minimal saved report: one column, ``order.code``."""
    definition = {
        "schema_version": contracts.SCHEMA_VERSION,
        "base": "order",
        "columns": [{"field": "order.code"}],
    }
    definition.update(kwargs.pop("definition", {}))
    with scopes_disabled():
        return ReportDefinition.objects.create(
            event=event,
            organizer=organizer,
            name=kwargs.pop("name", "Order codes"),
            identifier=identifier,
            definition=definition,
            **kwargs,
        )


def exporter_for_event(event, user, **kwargs):
    """The exporter instance pretix itself would build for an event export."""
    return init_event_exporter(
        identifier=exporters.CustomReportExporter.identifier,
        event=event,
        user=user,
        **kwargs,
    )


def exporter_for_organizer(organizer, user, **kwargs):
    for ex in init_organizer_exporters(organizer=organizer, user=user, **kwargs):
        if ex.identifier == exporters.CustomReportExporter.identifier:
            return ex
    return None


def csv_form_data(identifier="codes", **extra):
    data = {"_format": "default", contracts.EXPORT_FORM_REPORT_KEY: identifier}
    data.update(extra)
    return data


def rows_of(exporter, form_data):
    """Everything ``iterate_list`` yields, minus the progress marker."""
    return [
        line
        for line in exporter.iterate_list(form_data)
        if not isinstance(line, ListExporter.ProgressSetTotal)
    ]


# ---------------------------------------------------------------------------
# 1. It shows up where pretix looks for exporters
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_exporter_appears_in_the_event_export_ui(registered, event, user_with_perms):
    """``init_event_exporters`` is what the event export page enumerates."""
    with scope(organizer=event.organizer):
        found = [
            ex.identifier for ex in init_event_exporters(event, user=user_with_perms)
        ]
    assert exporters.CustomReportExporter.identifier in found


@pytest.mark.django_db
def test_exporter_appears_in_the_organizer_export_ui(
    registered, event, user_with_perms
):
    """``init_organizer_exporters`` is what the organizer export page enumerates."""
    with scope(organizer=event.organizer):
        ex = exporter_for_organizer(event.organizer, user_with_perms)
    assert ex is not None
    assert ex.is_multievent is True
    # The multi-event instance is handed a queryset, not an event.
    assert ex.event is None


@pytest.mark.django_db
def test_exporter_is_hidden_from_a_user_without_the_orders_permission(
    registered, event, user_without_perms
):
    """We keep ``ListExporter``'s ``event.orders:read`` requirement.

    ``init_event_exporters`` drops every exporter the acting account lacks the
    permission for (services/export.py:211-213). This asserts that we did not
    weaken that by overriding ``get_required_event_permission``.
    """
    assert (
        exporters.CustomReportExporter.get_required_event_permission()
        == "event.orders:read"
    )
    with scope(organizer=event.organizer):
        found = [
            ex.identifier for ex in init_event_exporters(event, user=user_without_perms)
        ]
    assert exporters.CustomReportExporter.identifier not in found


@pytest.mark.django_db
def test_the_format_choice_of_listexporter_survives(registered, event, user_with_perms):
    """Overriding ``export_form_fields`` would delete ``_format`` and break render.

    docs/pretix-api-notes.md section 1, pitfall 4. The check is cheap and the
    failure mode (an export that silently produces nothing) is not.
    """
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        fields = ex.export_form_fields
    assert "_format" in fields
    assert list(fields)[0] == "_format"
    assert set(dict(fields["_format"].choices)) == set(exporters.EXPORT_FORMATS)
    assert contracts.EXPORT_FORM_REPORT_KEY in fields


@pytest.mark.django_db
def test_the_report_choices_come_from_the_event(registered, event, user_with_perms):
    with scopes_disabled():
        make_report(event=event, identifier="codes", name="Order codes")
        make_report(event=event, identifier="other", name="Second report")
        other_event = Event.objects.create(
            organizer=event.organizer,
            name="Other",
            slug="other",
            date_from=now(),
            plugins="pretix_custom_reports",
        )
        make_report(event=other_event, identifier="foreign", name="Not mine")
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        choices = dict(ex.report_choices())
    assert set(choices) == {"codes", "other"}


@pytest.mark.django_db
def test_multievent_report_choices_are_collapsed_by_identifier(
    registered, event, user_with_perms
):
    """The same identifier in two events is one choice, not two.

    That is the whole reason the reference is an identifier and not a primary
    key (ADR 0001 section 5.1): an event copy carries the identifier along, and
    a multi-event export has to resolve it once per event.
    """
    with scopes_disabled():
        second = Event.objects.create(
            organizer=event.organizer,
            name="Second",
            slug="second",
            date_from=now(),
            plugins="pretix_custom_reports",
        )
        make_report(event=event, identifier="codes", name="Order codes")
        make_report(event=second, identifier="codes", name="Order codes (copy)")
    with scope(organizer=event.organizer):
        ex = exporter_for_organizer(event.organizer, user_with_perms)
        choices = dict(ex.report_choices())
    assert list(choices) == ["codes"]
    assert "codes" in choices["codes"]


@pytest.mark.django_db
def test_report_choices_never_leave_the_events_we_were_given(
    registered, event, user_with_perms
):
    """The permission boundary in one assertion.

    ``self.events`` is what pretix restricted to the acting account. A report of
    an event outside that queryset must not be offered even though it lives in
    the same organizer.
    """
    with scopes_disabled():
        hidden = Event.objects.create(
            organizer=event.organizer,
            name="Hidden",
            slug="hidden",
            date_from=now(),
            plugins="pretix_custom_reports",
        )
        make_report(event=event, identifier="visible")
        make_report(event=hidden, identifier="invisible")
    with scope(organizer=event.organizer):
        ex = exporter_for_organizer(
            event.organizer,
            user_with_perms,
            event_qs=Event.objects.filter(pk=event.pk),
        )
        choices = dict(ex.report_choices())
    assert set(choices) == {"visible"}


# ---------------------------------------------------------------------------
# 2. A report runs
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_report_runs_end_to_end_as_csv(registered, event, user_with_perms, orders):
    with scopes_disabled():
        make_report(event=event, identifier="codes")
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        filename, mimetype, content = ex.render(csv_form_data())
    body = content.decode("utf-8")
    assert mimetype == "text/csv"
    assert filename.endswith(".csv")
    assert "codes" in filename
    # Header row plus the two non-testmode orders.
    assert body.splitlines()[0].strip('"') == "Order code"
    assert "AAAAA" in body and "BBBBB" in body
    assert "CCCCC" not in body  # test mode is excluded by default


def render_xlsx(exporter, form_data):
    """Render to a buffer instead of letting pretix buffer it for us.

    ``ListExporter._render_xlsx`` without ``output_file`` writes to a
    ``tempfile.NamedTemporaryFile`` and re-opens it by name
    (pretix/base/exporter.py:322-326). On Windows a NamedTemporaryFile cannot be
    re-opened while it is still open, so that branch raises ``PermissionError``
    there. That is an upstream platform limitation, not something this plugin
    can fix or should work around in production code -- and the ``output_file``
    branch runs the identical ``SafeWorkbook`` code, so the tests use it and
    stay honest on every platform.
    """
    import io

    buffer = io.BytesIO()
    filename, mimetype, content = exporter.render(form_data, output_file=buffer)
    assert content is None  # documented: with output_file, bytes are not returned
    return filename, mimetype, buffer.getvalue()


@pytest.mark.django_db
def test_a_report_runs_end_to_end_as_xlsx(registered, event, user_with_perms, orders):
    with scopes_disabled():
        make_report(event=event, identifier="codes")
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        filename, mimetype, content = render_xlsx(
            ex, {"_format": "xlsx", contracts.EXPORT_FORM_REPORT_KEY: "codes"}
        )
    assert filename.endswith(".xlsx")
    assert mimetype.endswith("spreadsheetml.sheet")
    assert content[:2] == b"PK"  # a zip container, i.e. a real xlsx


@pytest.mark.django_db
def test_running_a_report_writes_an_execution_log_entry(
    registered, event, user_with_perms, orders
):
    """``log_executed`` had no caller before this module (handoff/status/persistence-dev.md)."""
    with scopes_disabled():
        report = make_report(event=event, identifier="codes")
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        ex.render(csv_form_data())
    with scopes_disabled():
        entries = list(report.all_logentries())
    assert len(entries) == 1
    assert entries[0].action_type == contracts.LOG_ACTION_EXECUTED
    assert entries[0].user == user_with_perms
    assert entries[0].parsed_data["row_count"] == 2
    assert entries[0].parsed_data["format"] == "default"


@pytest.mark.django_db
def test_runtime_overrides_change_the_result_without_touching_the_saved_report(
    registered, event, user_with_perms, orders
):
    """Overrides are per run. The stored definition must come out unchanged."""
    with scopes_disabled():
        report = make_report(event=event, identifier="codes")
        stored_before = dict(report.definition)
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        rows = rows_of(
            ex,
            csv_form_data(
                **{
                    exporters.FORM_KEY_INCLUDE_TESTMODE: exporters.OVERRIDE_YES,
                    exporters.FORM_KEY_ROW_LIMIT: 2,
                }
            ),
        )
    codes = [row[0] for row in rows[1:]]
    assert len(codes) == 2  # row_limit
    with scopes_disabled():
        report.refresh_from_db()
    assert report.definition == stored_before


@pytest.mark.django_db
def test_the_row_limit_override_is_range_checked(
    registered, event, user_with_perms, orders
):
    """``export_form_data`` is never revalidated, so we do it (api-notes 5, pitfall 1)."""
    with scopes_disabled():
        make_report(event=event, identifier="codes")
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        for bad in ("many", 0, -1, contracts.MAX_ROW_LIMIT + 1, True, [3]):
            with pytest.raises(ExportError) as excinfo:
                ex.render(csv_form_data(**{exporters.FORM_KEY_ROW_LIMIT: bad}))
            assert "row limit" in str(excinfo.value)


@pytest.mark.django_db
def test_an_unknown_format_says_so_instead_of_pretending_the_export_was_empty(
    registered, event, user_with_perms, orders
):
    """``ListExporter.render`` has no ``else`` branch (api-notes 1, pitfall 1).

    Without our check, ``render`` returns ``None`` and the calling task reports
    "Your export did not contain any data." -- which sends the owner looking for
    missing orders instead of a broken configuration.
    """
    with scopes_disabled():
        make_report(event=event, identifier="codes")
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        assert ListExporter.render(ex, {"_format": "ods"}) is None
        with pytest.raises(ExportError) as excinfo:
            ex.render({"_format": "ods", contracts.EXPORT_FORM_REPORT_KEY: "codes"})
    assert "ods" in str(excinfo.value)


@pytest.mark.django_db
def test_a_missing_or_malformed_report_reference_is_an_exporterror(
    registered, event, user_with_perms, orders
):
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        for bad in (None, "", 17, {"pk": 1}, "not a valid identifier!"):
            with pytest.raises(ExportError):
                ex.render({"_format": "default", contracts.EXPORT_FORM_REPORT_KEY: bad})


# ---------------------------------------------------------------------------
# 3. Multi-event
# ---------------------------------------------------------------------------


@pytest.fixture
def two_events(event):
    """A second event in the same organizer, with its own order."""
    with scopes_disabled():
        second = Event.objects.create(
            organizer=event.organizer,
            name="Second Event",
            slug="second",
            date_from=now() + datetime.timedelta(days=60),
            plugins="pretix_custom_reports",
            live=True,
        )
        channel = event.organizer.sales_channels.get(identifier="web")
        item = Item.objects.create(
            event=second, name="Ticket", internal_name="ticket", default_price=5
        )
        order = Order.objects.create(
            event=second,
            code="ZZZZZ",
            status=Order.STATUS_PAID,
            email="z@example.org",
            sales_channel=channel,
            datetime=now() - datetime.timedelta(days=1),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("5.00"),
        )
        OrderPosition.objects.create(
            order=order, item=item, price=Decimal("5.00"), positionid=1
        )
        return second


@pytest.mark.django_db
def test_multievent_export_covers_every_event_and_names_it(
    registered, event, two_events, user_with_perms, orders
):
    with scopes_disabled():
        make_report(event=event, identifier="codes")
        make_report(event=two_events, identifier="codes")
    with scope(organizer=event.organizer):
        ex = exporter_for_organizer(event.organizer, user_with_perms)
        rows = rows_of(ex, csv_form_data())
    assert rows[0][:2] == ["Event slug", "Event name"]
    body = {(row[0], row[2]) for row in rows[1:]}
    assert ("dummy", "AAAAA") in body
    assert ("second", "ZZZZZ") in body


@pytest.mark.django_db
def test_an_event_whose_report_is_missing_is_skipped_not_fatal(
    registered, event, two_events, user_with_perms, orders
):
    """The report exists in one event only. The export must still deliver."""
    with scopes_disabled():
        make_report(event=event, identifier="codes")
    with scope(organizer=event.organizer):
        ex = exporter_for_organizer(event.organizer, user_with_perms)
        rows = rows_of(ex, csv_form_data())
    slugs = {row[0] for row in rows[1:]}
    assert slugs == {"dummy"}


@pytest.mark.django_db
def test_an_unresolvable_field_in_one_event_is_skipped_not_fatal(
    registered, event, two_events, user_with_perms, orders
):
    """A question exists in event A and not in event B -- SPEC.md F9's daily case.

    The compiler raises ``FieldResolutionError`` for event B. Without the
    translation into a per-event skip, the whole organizer export would die and
    the user would never see event A's rows.
    """
    with scopes_disabled():
        question = Question.objects.create(
            event=event,
            question="T-shirt size",
            identifier="tshirt-size",
            type=Question.TYPE_TEXT,
            position=0,
        )
        position = OrderPosition.objects.filter(order__event=event).first()
        QuestionAnswer.objects.create(
            orderposition=position, question=question, answer="L"
        )
        definition = {
            "schema_version": contracts.SCHEMA_VERSION,
            "base": "orderposition",
            "columns": [
                {"field": "order.code"},
                {"field": "answer.tshirt-size"},
            ],
        }
        for target in (event, two_events):
            make_report(event=target, identifier="withquestion", definition=definition)
    with scope(organizer=event.organizer):
        ex = exporter_for_organizer(event.organizer, user_with_perms)
        rows = rows_of(ex, csv_form_data(identifier="withquestion"))
        # ... and with the strict policy, the message has to name the field, not
        # just the event. Otherwise "skipped" and "report missing" would be
        # indistinguishable to whoever gets the mail.
        with pytest.raises(ExportError) as excinfo:
            ex.render(
                csv_form_data(
                    identifier="withquestion",
                    **{
                        exporters.FORM_KEY_ON_UNAVAILABLE: exporters.ON_UNAVAILABLE_FAIL
                    },
                )
            )
    assert {row[0] for row in rows[1:]} == {"dummy"}
    assert ["L"] == [row[3] for row in rows[1:] if row[3]]
    assert "answer.tshirt-size" in str(excinfo.value)
    assert "second" in str(excinfo.value)


@pytest.mark.django_db
def test_fail_policy_turns_the_same_situation_into_an_exporterror(
    registered, event, two_events, user_with_perms, orders
):
    """Skipping is the default, not the only option.

    Silently dropping a whole event from a report someone bills against is its
    own kind of wrong answer, so the choice is explicit in the form.
    """
    with scopes_disabled():
        make_report(event=event, identifier="codes")
    with scope(organizer=event.organizer):
        ex = exporter_for_organizer(event.organizer, user_with_perms)
        with pytest.raises(ExportError) as excinfo:
            ex.render(
                csv_form_data(
                    **{exporters.FORM_KEY_ON_UNAVAILABLE: exporters.ON_UNAVAILABLE_FAIL}
                )
            )
    assert "second" in str(excinfo.value)


@pytest.mark.django_db
def test_if_no_event_can_supply_the_report_the_export_fails_loudly(
    registered, event, two_events, user_with_perms, orders
):
    """Not an empty file.

    An empty result becomes ``ExportEmptyError``, which pretix treats as a
    *soft* failure: mail sent, error counter untouched (services/export.py:
    371-374, 388-389). A schedule pointing at a deleted report would then keep
    mailing "no data" forever without ever naming the cause.
    """
    with scope(organizer=event.organizer):
        ex = exporter_for_organizer(event.organizer, user_with_perms)
        with pytest.raises(ExportError) as excinfo:
            ex.render(csv_form_data(identifier="ghost"))
    assert "ghost" in str(excinfo.value)


@pytest.mark.django_db
def test_two_reports_sharing_an_identifier_but_not_a_shape_do_not_produce_ragged_rows(
    registered, event, two_events, user_with_perms, orders
):
    """Identifiers are unique per event, so this really can happen."""
    with scopes_disabled():
        make_report(event=event, identifier="codes")
        make_report(
            event=two_events,
            identifier="codes",
            definition={
                "schema_version": contracts.SCHEMA_VERSION,
                "base": "order",
                "columns": [{"field": "order.code"}, {"field": "order.email"}],
            },
        )
    with scope(organizer=event.organizer):
        ex = exporter_for_organizer(event.organizer, user_with_perms)
        rows = rows_of(ex, csv_form_data())
    widths = {len(row) for row in rows}
    assert len(widths) == 1
    assert {row[0] for row in rows[1:]} == {"dummy"}


@pytest.mark.django_db
def test_a_single_event_export_never_skips(registered, event, user_with_perms, orders):
    """At event level there is nothing left to export, so skipping is a lie."""
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        with pytest.raises(ExportError) as excinfo:
            ex.render(
                csv_form_data(
                    identifier="ghost",
                    **{
                        exporters.FORM_KEY_ON_UNAVAILABLE: exporters.ON_UNAVAILABLE_SKIP
                    },
                )
            )
    assert "ghost" in str(excinfo.value)
    assert "dummy" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 4. Scheduled exports -- the deleted report case
# ---------------------------------------------------------------------------


@pytest.fixture
def schedule_user(organizer):
    user = User.objects.create_user("owner@example.org", "dummy")
    team = Team.objects.create(
        organizer=organizer,
        name="Owner team",
        all_events=True,
        all_event_permissions=True,
        all_organizer_permissions=True,
    )
    team.members.add(user)
    return user


def make_schedule(event, owner, identifier="codes", **form_data):
    data = {contracts.EXPORT_FORM_REPORT_KEY: identifier, "_format": "default"}
    data.update(form_data)
    schedule = ScheduledEventExport(event=event, owner=owner)
    schedule.export_identifier = exporters.CustomReportExporter.identifier
    schedule.export_form_data = data
    schedule.locale = "en"
    schedule.mail_subject = "Your report"
    schedule.mail_template = "Here you go."
    schedule.schedule_rrule = (
        "DTSTART:20260118T000000\nRRULE:FREQ=DAILY;INTERVAL=1;WKST=MO"
    )
    schedule.schedule_rrule_time = datetime.time(2, 30, 0)
    schedule.schedule_next_run = now() - datetime.timedelta(minutes=5)
    schedule.save()
    return schedule


@pytest.mark.django_db
def test_a_scheduled_export_can_be_created_and_runs(
    registered, event, schedule_user, orders
):
    """The end-to-end proof that we hang off pretix' scheduler, not our own.

    ``run_scheduled_exports`` is the ``periodic_task`` receiver; with
    ``CELERY_TASK_ALWAYS_EAGER`` the dispatched task runs inline, so this
    exercises ``EventTask`` (which opens the django-scopes scope),
    ``init_event_exporter`` (which re-checks the owner's permission) and
    ``_run_scheduled_export`` (which mails the file).
    """
    djmail.outbox = []
    with scopes_disabled():
        make_report(event=event, identifier="codes")
        schedule = make_schedule(event, schedule_user)

    run_scheduled_exports(None)

    schedule.refresh_from_db()
    assert schedule.error_counter == 0
    assert schedule.schedule_next_run > now()
    assert len(djmail.outbox) == 1
    assert djmail.outbox[0].subject == "Your report"
    attachment = djmail.outbox[0].attachments[0]
    assert attachment[0].endswith(".csv")
    payload = attachment[1]
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    assert "AAAAA" in payload


@pytest.mark.django_db
def test_a_scheduled_export_whose_report_was_deleted_explains_itself(
    registered, event, schedule_user, orders
):
    """The failure this module exists for.

    Without the ``ObjectDoesNotExist`` branch in ``_prepare``, the
    ``DoesNotExist`` would reach ``services/export.py:392-397``: five Celery
    retries at 120 s, then a mail saying "Internal Error", then -- after five
    such runs -- the schedule disappears from the periodic query without another
    word. What we want instead is one mail, immediately, naming the report and
    the event.
    """
    djmail.outbox = []
    with scopes_disabled():
        report = make_report(event=event, identifier="codes")
        schedule = make_schedule(event, schedule_user)
        report.delete()

    run_scheduled_exports(None)

    schedule.refresh_from_db()
    assert schedule.error_counter == 1
    assert "codes" in schedule.error_last_message
    assert event.slug in schedule.error_last_message
    assert "Internal Error" not in schedule.error_last_message
    assert len(djmail.outbox) == 1
    assert djmail.outbox[0].subject == "Export failed"
    assert "codes" in djmail.outbox[0].body


@pytest.mark.django_db
def test_the_deleted_report_error_reaches_the_event_log(
    registered, event, schedule_user, orders
):
    """The failure is recorded where an administrator will find it later."""
    with scopes_disabled():
        report = make_report(event=event, identifier="codes")
        make_schedule(event, schedule_user)
        report.delete()

    run_scheduled_exports(None)

    with scopes_disabled():
        entry = (
            event.logentry_set.filter(action_type="pretix.event.export.schedule.failed")
            .order_by("-pk")
            .first()
        )
    assert entry is not None
    assert "codes" in entry.parsed_data["reason"]
    assert entry.parsed_data["soft"] is False


@pytest.mark.django_db
def test_a_scheduled_organizer_export_runs_over_several_events(
    registered, event, two_events, schedule_user, orders
):
    """``ScheduledOrganizerExport`` plus the ``all_events`` marker of the UI.

    ``all_events``/``events`` are injected by the organizer export view, not by
    the exporter (api-notes section 5.3); the task filters on them before we are
    constructed, which is exactly why we must read only ``self.events``.
    """
    djmail.outbox = []
    with scopes_disabled():
        make_report(event=event, identifier="codes")
        make_report(event=two_events, identifier="codes")
        schedule = ScheduledOrganizerExport(
            organizer=event.organizer, owner=schedule_user
        )
        schedule.export_identifier = exporters.CustomReportExporter.identifier
        schedule.export_form_data = {
            contracts.EXPORT_FORM_REPORT_KEY: "codes",
            "_format": "default",
            "all_events": True,
        }
        schedule.locale = "en"
        schedule.mail_subject = "All events"
        schedule.mail_template = "Here you go."
        schedule.schedule_rrule = (
            "DTSTART:20260118T000000\nRRULE:FREQ=DAILY;INTERVAL=1;WKST=MO"
        )
        schedule.schedule_rrule_time = datetime.time(2, 30, 0)
        schedule.schedule_next_run = now() - datetime.timedelta(minutes=5)
        schedule.save()

    run_scheduled_exports(None)

    schedule.refresh_from_db()
    assert schedule.error_counter == 0, schedule.error_last_message
    payload = djmail.outbox[0].attachments[0][1]
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    assert "AAAAA" in payload
    assert "ZZZZZ" in payload


@pytest.mark.django_db
def test_a_scheduled_export_of_an_inactive_owner_is_refused_by_pretix(
    registered, event, schedule_user, orders
):
    """Background runs use the owner's permissions, not nobody's.

    pretix checks ``owner.is_active`` and re-runs ``init_event_exporter`` with
    the owner as the acting account (services/export.py:471-477). This test is
    here so that a later "let us just run it as a superuser" refactor of ours
    would be caught.
    """
    with scopes_disabled():
        make_report(event=event, identifier="codes")
        schedule = make_schedule(event, schedule_user)
        schedule_user.is_active = False
        schedule_user.save()

    run_scheduled_exports(None)

    schedule.refresh_from_db()
    assert schedule.error_counter == 1


# ---------------------------------------------------------------------------
# 5. Relative date filters are evaluated per run
# ---------------------------------------------------------------------------


RELATIVE_DEFINITION = {
    "schema_version": contracts.SCHEMA_VERSION,
    "base": "order",
    "columns": [{"field": "order.code"}],
    "filters": {
        "op": "and",
        "children": [
            {"field": "order.datetime", "operator": "relative_last_days", "value": 7}
        ],
    },
}


@pytest.mark.django_db
def test_relative_filter_is_evaluated_per_run_not_at_save_time(
    registered, event, user_with_perms
):
    """Two runs of one saved report under two frozen clocks, two results.

    The order is placed on 1 March. Run the "orders of the last 7 days" report
    on 3 March and it is in; run the very same stored report on 20 March and it
    must be gone. If anything resolved the window at save time -- in the form,
    in ``export_form_data``, in a cached compile -- both runs would agree and
    this test would fail.
    """
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        Item.objects.create(event=event, name="Ticket", default_price=1)
        Order.objects.create(
            event=event,
            code="MARCH",
            status=Order.STATUS_PAID,
            email="m@example.org",
            sales_channel=channel,
            datetime=datetime.datetime(2026, 3, 1, 12, 0, tzinfo=datetime.timezone.utc),
            expires=datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.timezone.utc),
            total=Decimal("1.00"),
        )
        make_report(event=event, identifier="recent", definition=RELATIVE_DEFINITION)

    def run():
        with scope(organizer=event.organizer):
            ex = exporter_for_event(event, user_with_perms)
            return rows_of(ex, csv_form_data(identifier="recent"))

    with freeze_time("2026-03-03 09:00:00+00:00"):
        inside = run()
    with freeze_time("2026-03-20 09:00:00+00:00"):
        outside = run()

    assert [row[0] for row in inside[1:]] == ["MARCH"]
    assert outside[1:] == []


@pytest.mark.django_db
def test_a_scheduled_relative_report_re_evaluates_on_every_scheduled_run(
    registered, event, schedule_user
):
    """The same property through the scheduler, because that is where it matters.

    A daily "yesterday's orders" mail that silently keeps reporting the day the
    schedule was created is the kind of bug nobody notices for a quarter.
    """
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        Item.objects.create(event=event, name="Ticket", default_price=1)
        Order.objects.create(
            event=event,
            code="MARCH",
            status=Order.STATUS_PAID,
            email="m@example.org",
            sales_channel=channel,
            datetime=datetime.datetime(2026, 3, 1, 12, 0, tzinfo=datetime.timezone.utc),
            expires=datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.timezone.utc),
            total=Decimal("1.00"),
        )
        make_report(event=event, identifier="recent", definition=RELATIVE_DEFINITION)
        schedule = make_schedule(event, schedule_user, identifier="recent")

    bodies = []
    for moment in ("2026-03-03 09:00:00+00:00", "2026-03-20 09:00:00+00:00"):
        with freeze_time(moment):
            djmail.outbox = []
            with scopes_disabled():
                schedule.schedule_next_run = now() - datetime.timedelta(minutes=5)
                schedule.save(update_fields=["schedule_next_run"])
            run_scheduled_exports(None)
            if djmail.outbox and djmail.outbox[0].attachments:
                payload = djmail.outbox[0].attachments[0][1]
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8")
                bodies.append(payload)
            else:
                bodies.append("")

    assert "MARCH" in bodies[0]
    assert "MARCH" not in bodies[1]


# ---------------------------------------------------------------------------
# 6. Injection: verify, do not duplicate
# ---------------------------------------------------------------------------


INJECTION = '=1+cmd|" /C calc"!A0'


@pytest.fixture
def injected_order(event):
    with scopes_disabled():
        channel = event.organizer.sales_channels.get(identifier="web")
        Order.objects.create(
            event=event,
            code="EVILL",
            status=Order.STATUS_PAID,
            email="e@example.org",
            sales_channel=channel,
            datetime=now(),
            expires=now() + datetime.timedelta(days=1),
            total=Decimal("1.00"),
            comment=INJECTION,
        )
        make_report(
            event=event,
            identifier="comments",
            definition={
                "schema_version": contracts.SCHEMA_VERSION,
                "base": "order",
                "columns": [{"field": "order.comment"}],
            },
        )


@pytest.mark.django_db
def test_csv_injection_is_neutralised_by_listexporter(
    registered, event, user_with_perms, injected_order
):
    """``ListExporter`` imports ``defusedcsv`` (pretix/base/exporter.py:42).

    We do not escape anything ourselves -- doing it twice would put two
    apostrophes in front of honest data. This test is the substitute for that
    code: it fails if a future refactor ever hand-rolls a ``csv.writer``.
    """
    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        _fn, _mime, content = ex.render(csv_form_data(identifier="comments"))
    body = content.decode("utf-8")
    assert INJECTION not in body
    assert "'=1+cmd" in body


@pytest.mark.django_db
def test_xlsx_injection_is_neutralised_by_safeworkbook(
    registered, event, user_with_perms, injected_order
):
    """``_render_xlsx`` builds a ``SafeWorkbook`` (pretix/base/exporter.py:298).

    ``SafeCell`` forces a cell openpyxl would type as a formula back to text
    (pretix/helpers/safe_openpyxl.py:75-84), so the value survives verbatim but
    Excel will not execute it.
    """
    import io
    from openpyxl import load_workbook

    with scope(organizer=event.organizer):
        ex = exporter_for_event(event, user_with_perms)
        _fn, _mime, content = render_xlsx(
            ex, {"_format": "xlsx", contracts.EXPORT_FORM_REPORT_KEY: "comments"}
        )
    sheet = load_workbook(io.BytesIO(content)).active
    cell = sheet.cell(row=2, column=1)
    assert cell.value == INJECTION
    assert cell.data_type == "s"  # string, not formula


# ---------------------------------------------------------------------------
# 7. Structural guarantees
# ---------------------------------------------------------------------------


def test_the_exporter_contains_no_query_logic_of_its_own():
    """CLAUDE.md rules 2 and 3, asserted rather than promised.

    Every row must come from the query compiler. Naming an ORM path, importing
    ``Order``/``OrderPosition`` or building a ``Q()`` here would move part of
    the allow-list out of the registry, which is the one place it is reviewed.
    """
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "pretix_custom_reports"
        / "exporters.py"
    ).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    # The module docstring mentions these names in prose; strip it first.
    code = code.split('"""', 2)[-1]
    for forbidden in (
        "eval(",
        "exec(",
        ".raw(",
        "RawSQL",
        ".extra(",
        "Q(",
        "OrderPosition",
        "filter(order__",
    ):
        assert forbidden not in code, forbidden


def test_the_registration_receivers_return_the_class_not_an_instance():
    """``init_*_exporters`` calls ``response(...)`` on the return value."""
    assert exporters.register_report_exporter(None) is exporters.CustomReportExporter
    assert (
        exporters.register_multievent_report_exporter(None)
        is exporters.CustomReportExporter
    )


def test_the_identifier_is_lowercase_and_short():
    """It becomes ``ScheduledExport.export_identifier`` and an HTML field prefix.

    ``max_length=190`` on the column, and the control UI renders form fields as
    ``"<identifier>-<fieldname>"`` (control/views/orders.py:2695-2699), so a
    dash or an uppercase letter here would be a lasting annoyance.
    """
    identifier = exporters.CustomReportExporter.identifier
    assert identifier.isalpha() and identifier.islower()
    assert len(identifier) <= 190
