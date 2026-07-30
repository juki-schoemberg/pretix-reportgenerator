# Owner from wave 1 on: persistence-dev (see ORCHESTRIERUNG.md section 5)
"""Model tests: constraints, identifier, validation on write, scoping, logging.

What is deliberately *not* tested here: whether a field key exists. A stored
definition may reference a key that today's registry cannot resolve (a renamed
question, a disabled plugin) -- that is a legal state and is checked on import,
in the editor and when a report runs, not on save (SPEC.md F9,
docs/adr/0001-contracts.md section 4). ``test_unresolvable_field_key_is_stored``
pins that down so nobody "fixes" it later.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.timezone import now
from django_scopes import ScopeError, scope, scopes_disabled
from pretix.base.models import Event, LogEntry, Organizer

from pretix_custom_reports import contracts
from pretix_custom_reports.models import (
    IDENTIFIER_CHARSET,
    IDENTIFIER_LENGTH,
    ReportDefinition,
)


def make_definition(base="order", columns=("order.code",), **extra):
    """A structurally valid definition in exactly its canonical shape."""
    document = {
        "schema_version": contracts.SCHEMA_VERSION,
        "base": base,
        "columns": [{"field": key} for key in columns],
        "sorting": [],
        "options": {
            "include_canceled_positions": False,
            "include_testmode_orders": False,
            "row_limit": None,
        },
    }
    document.update(extra)
    return document


@pytest.fixture
def other_organizer(db):
    return Organizer.objects.create(name="Other", slug="other")


@pytest.fixture
def second_event(organizer):
    with scopes_disabled():
        return Event.objects.create(
            organizer=organizer,
            name="Second Event",
            slug="second",
            date_from=now(),
            plugins="pretix_custom_reports",
        )


def report(event=None, organizer=None, **kwargs):
    kwargs.setdefault("name", "Attendee list")
    kwargs.setdefault("definition", make_definition())
    return ReportDefinition.objects.create(event=event, organizer=organizer, **kwargs)


# ---------------------------------------------------------------------------
# Ownership: event XOR organizer
# ---------------------------------------------------------------------------


def test_event_report_and_template_can_coexist(event, organizer):
    with scope(organizer=organizer):
        r = report(event=event, identifier="attendees")
        t = report(organizer=organizer, identifier="attendees", name="Template")
        assert r.is_template is False
        assert t.is_template is True
        assert r.owning_organizer == organizer
        assert t.owning_organizer == organizer


def test_both_owners_rejected(event, organizer):
    with scope(organizer=organizer):
        with pytest.raises(ValidationError):
            report(event=event, organizer=organizer)


def test_no_owner_rejected(organizer):
    with scope(organizer=organizer):
        with pytest.raises(ValidationError):
            report()


def test_xor_is_enforced_by_the_database(event, organizer):
    """A code path that bypasses ``save()`` must not be able to break the XOR."""
    with scope(organizer=organizer):
        r = report(event=event)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ReportDefinition.objects.filter(pk=r.pk).update(organizer=organizer)


# ---------------------------------------------------------------------------
# Identifier
# ---------------------------------------------------------------------------


def test_identifier_is_generated(event, organizer):
    with scope(organizer=organizer):
        r = report(event=event)
    assert len(r.identifier) == IDENTIFIER_LENGTH
    assert set(r.identifier) <= set(IDENTIFIER_CHARSET)
    assert contracts.validate_identifier(r.identifier) == r.identifier


def test_given_identifier_is_kept(event, organizer):
    with scope(organizer=organizer):
        r = report(event=event, identifier="attendee-list_2")
        r.name = "Renamed"
        r.save()
    assert r.identifier == "attendee-list_2"


def test_invalid_identifier_rejected(event, organizer):
    with scope(organizer=organizer):
        with pytest.raises(ValidationError):
            report(event=event, identifier="not valid!")


def test_identifier_unique_per_event(event, organizer):
    with scope(organizer=organizer):
        report(event=event, identifier="attendees")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                report(event=event, identifier="attendees", name="Second")


def test_identifier_unique_per_organizer_for_templates(organizer):
    with scope(organizer=organizer):
        report(organizer=organizer, identifier="attendees")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                report(organizer=organizer, identifier="attendees", name="Second")


def test_identifier_may_repeat_in_another_event(event, second_event, organizer):
    """The identifier travels with the report; that is the whole point of it."""
    with scope(organizer=organizer):
        a = report(event=event, identifier="attendees")
        b = report(event=second_event, identifier="attendees")
    assert a.identifier == b.identifier


def test_ensure_unique_identifier_suffixes(event, organizer):
    with scope(organizer=organizer):
        report(event=event, identifier="attendees")
        second = ReportDefinition(
            event=event,
            name="Second",
            definition=make_definition(),
            identifier="attendees",
        )
        assert second.ensure_unique_identifier() == "attendees-2"
        second.save()
        third = ReportDefinition(
            event=event,
            name="Third",
            definition=make_definition(),
            identifier="attendees",
        )
        assert third.ensure_unique_identifier() == "attendees-3"


def test_ensure_unique_identifier_keeps_free_identifier(event, second_event, organizer):
    with scope(organizer=organizer):
        report(event=event, identifier="attendees")
        moved = ReportDefinition(
            event=second_event,
            name="Copy",
            definition=make_definition(),
            identifier="attendees",
        )
        assert moved.ensure_unique_identifier() == "attendees"


# ---------------------------------------------------------------------------
# Definition validation on write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "definition",
    [
        {},
        {"base": "order", "columns": [{"field": "order.code"}]},  # no schema_version
        make_definition(columns=()),  # a report needs at least one column
        make_definition(base="nonsense"),
        make_definition(columns=("order.event__organizer__slug",)),  # double underscore
        make_definition(columns=("nosuchnamespace.code",)),
        {**make_definition(), "schema_version": 99},
        {**make_definition(), "unknown_top_level_key": 1},
        "not even an object",
    ],
)
def test_invalid_definition_never_reaches_the_database(event, organizer, definition):
    with scope(organizer=organizer):
        with pytest.raises(ValidationError):
            report(event=event, definition=definition)
        assert not ReportDefinition.objects.for_event(event).exists()


def test_unresolvable_field_key_is_stored(event, organizer):
    """Structurally valid but unknown to the registry: must be accepted.

    A question can be renamed and renamed back; rejecting the key here would
    make a report unsaveable for reasons the user cannot see, and the registry
    is not even reachable from this layer (CLAUDE.md rule 2).
    """
    definition = make_definition(columns=("answer.this-question-is-long-gone",))
    with scope(organizer=organizer):
        r = report(event=event, definition=definition)
        assert (
            r.definition["columns"][0]["field"] == "answer.this-question-is-long-gone"
        )


def test_definition_is_canonicalised(event, organizer):
    """An empty root filter group means "no filter" and is normalised away."""
    definition = make_definition()
    definition["filters"] = {"op": "and", "children": []}
    with scope(organizer=organizer):
        r = report(event=event, definition=definition)
        r.refresh_from_db()
    assert "filters" not in r.definition
    assert r.definition == make_definition()


def test_base_is_derived_from_the_definition(event, organizer):
    with scope(organizer=organizer):
        r = report(event=event, definition=make_definition(base="orderposition"))
    assert r.base == "orderposition"
    assert r.schema_version == contracts.SCHEMA_VERSION


def test_base_mismatch_rejected(event, organizer):
    with scope(organizer=organizer):
        with pytest.raises(ValidationError):
            report(
                event=event,
                base="order",
                definition=make_definition(base="orderposition"),
            )


def test_validated_definition_returns_the_contract_type(event, organizer):
    with scope(organizer=organizer):
        r = report(event=event, definition=make_definition(base="orderposition"))
        document = r.validated_definition()
    assert isinstance(document, contracts.ReportDefinition)
    assert document.base is contracts.Base.ORDERPOSITION
    assert document.columns[0].field == "order.code"


def test_update_fields_save_still_validates(event, organizer):
    with scope(organizer=organizer):
        r = report(event=event)
        r.name = "Renamed"
        r.save(update_fields=["name"])
        r.refresh_from_db()
        assert r.name == "Renamed"

        r.definition = {"nonsense": True}
        with pytest.raises(ValidationError):
            r.save(update_fields=["definition"])


def test_full_clean_reports_the_definition_field(event, organizer):
    with scope(organizer=organizer):
        r = ReportDefinition(event=event, name="Broken", definition={"base": "order"})
        with pytest.raises(ValidationError) as excinfo:
            r.full_clean()
    assert "definition" in excinfo.value.message_dict


# ---------------------------------------------------------------------------
# Duplication (also the mechanism behind event copy and template instantiation)
# ---------------------------------------------------------------------------


def test_duplicate_in_place(event, organizer, user_with_perms):
    with scope(organizer=organizer):
        source = report(event=event, identifier="attendees")
        copy = source.duplicate(name="Attendee list (copy)", created_by=user_with_perms)
    assert copy.pk != source.pk
    assert copy.event_id == event.pk
    assert copy.identifier == "attendees-2"
    assert copy.name == "Attendee list (copy)"
    assert copy.created_by == user_with_perms
    assert copy.definition == source.definition
    # Not a template instance, so no source_template.
    assert copy.source_template_id is None


def test_duplicate_into_another_event_keeps_the_identifier(
    event, second_event, organizer
):
    with scope(organizer=organizer):
        source = report(event=event, identifier="attendees")
        copy = source.duplicate(event=second_event)
    assert copy.identifier == "attendees"
    assert copy.event_id == second_event.pk
    assert copy.organizer_id is None


def test_template_instance_remembers_its_template(event, organizer):
    with scope(organizer=organizer):
        template = report(organizer=organizer, identifier="attendees", name="Template")
        instance = template.duplicate(event=event)
    assert instance.source_template_id == template.pk
    assert instance.event_id == event.pk
    assert instance.organizer_id is None
    assert instance.identifier == "attendees"


def test_deleting_a_template_keeps_its_instances(event, organizer):
    with scope(organizer=organizer):
        template = report(organizer=organizer, name="Template")
        instance = template.duplicate(event=event)
        template.delete()
        instance.refresh_from_db()
    assert instance.source_template_id is None


def test_deleting_an_event_deletes_its_reports(event, organizer):
    with scope(organizer=organizer):
        report(event=event)
        assert ReportDefinition.objects.for_event(event).count() == 1
    with scopes_disabled():
        event.delete()
        assert ReportDefinition.objects.count() == 0


# ---------------------------------------------------------------------------
# Manager, queryset and scoping
# ---------------------------------------------------------------------------


def test_queryset_helpers(event, second_event, organizer):
    with scope(organizer=organizer):
        mine = report(event=event, identifier="mine")
        theirs = report(event=second_event, identifier="theirs")
        template = report(organizer=organizer, identifier="template", name="T")

        assert list(ReportDefinition.objects.for_event(event)) == [mine]
        assert set(ReportDefinition.objects.event_reports()) == {mine, theirs}
        assert list(ReportDefinition.objects.templates()) == [template]
        assert list(ReportDefinition.objects.templates_for_organizer(organizer)) == [
            template
        ]
        assert list(
            ReportDefinition.objects.for_event(event).by_identifier("mine")
        ) == [mine]


def test_related_managers(event, organizer):
    with scope(organizer=organizer):
        r = report(event=event)
        t = report(organizer=organizer, name="T")
        assert list(event.custom_reports.all()) == [r]
        assert list(organizer.custom_report_templates.all()) == [t]


def test_scope_covers_both_sides_of_the_xor(event, organizer, other_organizer):
    """The regression test for the custom manager.

    With ``ScopedManager(organizer='event__organizer')`` the template would be
    invisible, with ``organizer='organizer'`` the event report would be.
    """
    with scope(organizer=organizer):
        mine = report(event=event, identifier="mine")
        my_template = report(organizer=organizer, identifier="t", name="T")
    with scope(organizer=other_organizer):
        foreign_template = report(organizer=other_organizer, identifier="t", name="T")

    with scope(organizer=organizer):
        assert set(ReportDefinition.objects.all()) == {mine, my_template}
    with scope(organizer=other_organizer):
        assert set(ReportDefinition.objects.all()) == {foreign_template}
    with scope(organizer=[organizer, other_organizer]):
        assert ReportDefinition.objects.count() == 3


def test_queries_without_scope_fail_loudly(event, organizer):
    with scope(organizer=organizer):
        report(event=event)
    with pytest.raises(ScopeError):
        ReportDefinition.objects.all()
    with pytest.raises(ScopeError):
        ReportDefinition.objects.count()


def test_scopes_disabled_still_works(event, organizer):
    with scope(organizer=organizer):
        report(event=event)
    with scopes_disabled():
        assert ReportDefinition.objects.count() == 1


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def logentries(obj):
    return LogEntry.objects.filter(content_type=obj.logs_content_type, object_id=obj.pk)


def test_log_actions_use_the_contract_types(event, organizer, user_with_perms):
    with scope(organizer=organizer):
        r = report(event=event)
        r.log_added(user=user_with_perms)
        r.log_changed(user=user_with_perms, data={"changed_fields": ["name"]})
        r.log_executed(user=user_with_perms, data={"row_count": 17})
        types = list(logentries(r).order_by("pk").values_list("action_type", flat=True))
    assert types == [
        contracts.LOG_ACTION_ADDED,
        contracts.LOG_ACTION_CHANGED,
        contracts.LOG_ACTION_EXECUTED,
    ]


def test_log_payload_is_complete_and_unmasked(event, organizer, user_with_perms):
    """No key may contain 'password', 'secret' or 'api_key'.

    ``log_action`` replaces such values with ``********`` on a substring match
    (pretix/base/models/base.py:153-163), which would gut our audit trail.
    """
    with scope(organizer=organizer):
        r = report(event=event, identifier="attendees")
        entry = r.log_added(user=user_with_perms)
        data = entry.parsed_data
    assert data["identifier"] == "attendees"
    assert data["definition"] == r.definition
    assert data["base"] == "order"
    assert "********" not in str(data)
    for key in data:
        assert "password" not in key and "secret" not in key and "api_key" not in key


def test_log_entry_of_an_event_report_is_attached_to_the_event(
    event, organizer, user_with_perms
):
    with scope(organizer=organizer):
        r = report(event=event)
        entry = r.log_added(user=user_with_perms)
    assert entry.event_id == event.pk
    assert entry.organizer_id == organizer.pk
    assert entry.user == user_with_perms


def test_log_entry_of_a_template_falls_back_to_the_organizer(
    organizer, user_with_perms
):
    with scope(organizer=organizer):
        t = report(organizer=organizer, name="T")
        entry = t.log_added(user=user_with_perms)
    assert entry.event_id is None
    assert entry.organizer_id == organizer.pk


def test_log_deleted_survives_the_deletion(event, organizer, user_with_perms):
    with scope(organizer=organizer):
        r = report(event=event)
        pk = r.pk
        r.log_deleted(user=user_with_perms)
        r.delete()
        entries = LogEntry.objects.filter(
            object_id=pk, action_type=contracts.LOG_ACTION_DELETED
        )
        assert entries.count() == 1
