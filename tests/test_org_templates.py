# Owner from wave 2 on: portability-dev (see ORCHESTRIERUNG.md section 5)
"""Organizer templates and "load template" in an event (SPEC.md F10).

The interesting test in here is
``test_loading_a_template_into_an_event_with_different_questions``: a template
written for one event, loaded into another whose questions are spelled
differently and where one question does not exist at all. That is the case the
resolution layer exists for, and the one the definition of done names.

Everything else is the boring half that has to be right anyway: the organizer
views are permission checked, they cannot reach an event report, and "load"
produces a copy with ``source_template`` set rather than a live link.

Routing note as in ``tests/test_portability.py``: the routes are attached to the
plugin URLconf for the duration of this module because ``urls.py`` belongs to
the integrator.
"""

import json
import pytest
from django_scopes import scope, scopes_disabled
from pretix.base.models import Event, LogEntry, Organizer, Question, Team, User

from pretix_custom_reports import contracts
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.portability.errors import (
    ImportRejected,
    TemplateAccessDenied,
)
from pretix_custom_reports.portability.resolution import ResolutionStrategy
from pretix_custom_reports.portability.templating import (
    ORGANIZER_CHANGE_PERMISSION,
    apply_template,
    assert_template_accessible,
    available_templates,
    plan_template,
)
from pretix_custom_reports.registry import cache as registry_cache
from pretix_custom_reports.registry.library import EventFieldRegistry

from .conftest import PASSWORD
from .test_portability import (
    definition,
    install_plugin_urls,
    make_questions,
    make_report,
    remove_plugin_urls,
    report_path,
)


@pytest.fixture(scope="module", autouse=True)
def plugin_urls():
    added = install_plugin_urls()
    yield
    remove_plugin_urls(added)


@pytest.fixture(autouse=True)
def clean_registry_cache():
    registry_cache.clear_local_cache()
    yield
    registry_cache.clear_local_cache()


@pytest.fixture
def registry():
    return EventFieldRegistry()


def organizer_path(name, organizer, **kwargs):
    from django.urls import reverse

    return reverse(
        f"plugins:pretix_custom_reports:{name}",
        kwargs={"organizer": organizer.slug, **kwargs},
    )


def make_template(organizer, name="Shirt list", **kwargs):
    with scopes_disabled():
        return ReportDefinition.objects.create(
            organizer=organizer,
            event=None,
            name=name,
            base=kwargs.pop("base", "orderposition"),
            definition=kwargs.pop(
                "definition",
                definition(columns=("order.code", "answer.tshirt-size")),
            ),
            **kwargs,
        )


@pytest.fixture
def template(organizer):
    return make_template(organizer)


@pytest.fixture
def other_organizer(db):
    return Organizer.objects.create(name="Other", slug="other")


@pytest.fixture
def other_event(other_organizer):
    import datetime
    from django.utils.timezone import now

    with scopes_disabled():
        return Event.objects.create(
            organizer=other_organizer,
            name="Other Event",
            slug="otherevent",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
            live=True,
        )


def limited_user(organizer, email, event_permissions=(), organizer_permissions=()):
    user = User.objects.create_user(email, PASSWORD)
    team = Team.objects.create(
        organizer=organizer,
        name=email,
        all_events=True,
        all_event_permissions=False,
        limit_event_permissions={p: True for p in event_permissions},
        all_organizer_permissions=False,
        limit_organizer_permissions={p: True for p in organizer_permissions},
    )
    team.members.add(user)
    return user


# ===========================================================================
# The model side of a template
# ===========================================================================


@pytest.mark.django_db
def test_a_template_belongs_to_an_organizer_and_to_no_event(organizer, template):
    assert template.is_template is True
    assert template.event_id is None
    assert template.organizer_id == organizer.pk
    with scope(organizer=organizer):
        assert list(ReportDefinition.objects.templates_for_organizer(organizer)) == [
            template
        ]


@pytest.mark.django_db
def test_available_templates_are_those_of_the_events_own_organizer(
    event, template, other_organizer
):
    make_template(other_organizer, name="Not yours")
    with scope(organizer=event.organizer):
        assert list(available_templates(event)) == [template]


# ===========================================================================
# Loading a template into an event
# ===========================================================================


@pytest.mark.django_db
def test_loading_a_template_produces_a_copy_not_a_link(event, template, registry):
    make_questions(event)
    with scopes_disabled():
        plan = plan_template(template, event, registry=registry)
        assert plan.ok
        copy = apply_template(plan)

    assert copy.pk != template.pk
    assert copy.event_id == event.pk
    assert copy.organizer_id is None
    assert copy.source_template_id == template.pk
    assert copy.definition == template.definition

    # No live link: changing the template afterwards leaves the copy alone.
    with scopes_disabled():
        template.definition = definition(columns=("order.email",))
        template.save()
        copy.refresh_from_db()
    assert [c["field"] for c in copy.definition["columns"]] == [
        "order.code",
        "answer.tshirt-size",
    ]


@pytest.mark.django_db
def test_loading_a_template_into_an_event_with_different_questions(
    event, organizer, registry
):
    """The case the resolution layer exists for (definition of done).

    The template names two questions. The target event spells one of them
    differently and does not have the other at all.
    """
    make_questions(event, tshirt_identifier="tshirt_size", with_newsletter=False)
    template = make_template(
        organizer,
        definition=definition(
            columns=("order.code", "answer.tshirt-size", "answer.newsletter")
        ),
    )

    with scopes_disabled():
        plan = plan_template(template, event, registry=registry)

    report = plan.report
    by_source = {entry.source: entry for entry in report.fields}

    # 1. found unchanged
    assert by_source["order.code"].status == "found"
    # 2. mapped onto a differently spelled identifier, visibly
    mapped = by_source["answer.tshirt-size"]
    assert mapped.status == "mapped"
    assert mapped.target == "answer.tshirt_size"
    assert mapped.target_label == "T-shirt size"
    # 3. not resolvable, and not swallowed
    missing = by_source["answer.newsletter"]
    assert missing.status == "missing"
    assert missing.target is None
    assert missing.path == "columns[2]"

    # As long as one reference is unresolved, nothing may be written.
    assert not plan.ok
    with scopes_disabled():
        with pytest.raises(ImportRejected):
            apply_template(plan)
        assert ReportDefinition.objects.filter(event=event).count() == 0

        # The user decides to skip it.
        plan = plan_template(
            template, event, registry=registry, strategy=ResolutionStrategy.SKIP
        )
        assert plan.ok
        copy = apply_template(plan)

    assert [c["field"] for c in copy.definition["columns"]] == [
        "order.code",
        "answer.tshirt_size",
    ]
    assert copy.source_template_id == template.pk


@pytest.mark.django_db
def test_loading_a_template_is_logged_with_its_resolution_report(
    event, template, registry, user_with_perms
):
    make_questions(event)
    with scopes_disabled():
        plan = plan_template(template, event, registry=registry)
        copy = apply_template(plan, user=user_with_perms)
        entries = list(
            LogEntry.objects.filter(action_type=contracts.LOG_ACTION_TEMPLATE_APPLIED)
        )
    assert len(entries) == 1
    data = json.loads(entries[0].data)
    assert data["template"]["identifier"] == template.identifier
    assert data["template"]["resolution"]["counts"]["found"] == 2
    assert copy.created_by_id == user_with_perms.pk


@pytest.mark.django_db
def test_the_identifier_of_a_template_travels_into_the_event(event, template, registry):
    make_questions(event)
    with scopes_disabled():
        plan = plan_template(template, event, registry=registry)
        copy = apply_template(plan)
    assert copy.identifier == template.identifier


# ===========================================================================
# Permissions on both ends
# ===========================================================================


@pytest.mark.django_db
def test_a_user_without_write_access_to_the_target_event_is_refused(
    event, template, organizer
):
    user = limited_user(organizer, "reader@example.org", ["event.orders:read"])
    with pytest.raises(TemplateAccessDenied):
        assert_template_accessible(template, event, user=user)


@pytest.mark.django_db
def test_a_template_of_another_organizer_needs_that_organizers_permission(
    event, organizer, other_organizer
):
    foreign = make_template(other_organizer, name="Foreign")
    user = limited_user(
        organizer, "local-admin@example.org", ["event.settings.general:write"]
    )
    with pytest.raises(TemplateAccessDenied):
        assert_template_accessible(foreign, event, user=user)

    # The same user, now with the organizer permission on the *source*.
    team = Team.objects.create(
        organizer=other_organizer,
        name="cross",
        all_events=False,
        all_organizer_permissions=False,
        limit_organizer_permissions={ORGANIZER_CHANGE_PERMISSION: True},
    )
    team.members.add(user)
    # ``User`` caches the teams it looked up per request (``_teamcache``), so a
    # fresh instance is what the next request would see.
    user = User.objects.get(pk=user.pk)
    assert_template_accessible(foreign, event, user=user)


@pytest.mark.django_db
def test_an_event_report_is_not_a_template(event):
    report = make_report(event=event)
    with pytest.raises(TemplateAccessDenied):
        assert_template_accessible(report, event)


# ===========================================================================
# Organizer-level views
# ===========================================================================


@pytest.mark.django_db
def test_the_template_list_shows_templates_and_not_event_reports(
    client_with_perms, organizer, event, template
):
    make_report(event=event, name="An event report")
    response = client_with_perms.get(organizer_path("organizer.templates", organizer))
    assert response.status_code == 200
    assert b"Shirt list" in response.content
    assert b"An event report" not in response.content


# ---------------------------------------------------------------------------
# Where the template list sends the user
# ---------------------------------------------------------------------------
#
# Same UX bug as the one fixed for ``report_list.html`` (see
# ``tests/test_permissions.py``, section "Where the list sends the user"): the
# list linked to this module's plain JSON form, so the graphical editor was
# reachable by typing its URL only. The two form routes stay alive -- they are
# the editor's POST target and the hand-repair page for a template whose JSON is
# too broken to render -- they are just not linked from the list any more.
#
# Both route pairs are keyed on the numeric primary key here: a template has no
# event, so there is nothing stabler than the pk to address it by, unlike the
# event-level ``editor.edit`` which takes the identifier.


@pytest.mark.django_db
def test_the_template_list_links_creation_to_the_graphical_editor(
    client_with_perms, organizer, event, template
):
    content = client_with_perms.get(
        organizer_path("organizer.templates", organizer)
    ).content.decode()
    editor = organizer_path("organizer.templates.editor.new", organizer)
    assert f'href="{editor}"' in content
    assert f'href="{organizer_path("organizer.templates.add", organizer)}"' not in (
        content
    )


@pytest.mark.django_db
def test_the_empty_template_list_links_creation_to_the_graphical_editor(
    client_with_perms, organizer, event
):
    """The other branch of the page: no template rows at all.

    The "create" button sits above the ``{% if templates %}``, so it is the same
    markup -- but a test that only ever renders the filled page would keep
    passing if someone moves it inside.
    """
    content = client_with_perms.get(
        organizer_path("organizer.templates", organizer)
    ).content.decode()
    assert "no report templates yet" in content
    editor = organizer_path("organizer.templates.editor.new", organizer)
    assert f'href="{editor}"' in content
    assert f'href="{organizer_path("organizer.templates.add", organizer)}"' not in (
        content
    )


@pytest.mark.django_db
def test_the_template_list_links_editing_to_the_graphical_editor(
    client_with_perms, organizer, event, template
):
    """Name link and pencil icon both go to the editor."""
    content = client_with_perms.get(
        organizer_path("organizer.templates", organizer)
    ).content.decode()
    editor = organizer_path(
        "organizer.templates.editor.edit", organizer, template=template.pk
    )
    # Twice: the template name and the pencil button.
    assert content.count(f'href="{editor}"') == 2
    # The trailing quote matters: the form's URL is a prefix of the export and
    # delete URLs, which are still linked.
    crud = organizer_path("organizer.templates.edit", organizer, template=template.pk)
    assert f'href="{crud}"' not in content


@pytest.mark.django_db
def test_the_template_list_still_links_export_and_delete_to_this_module(
    client_with_perms, organizer, event, template
):
    """Only add and edit moved; the rest of the row is unchanged."""
    content = client_with_perms.get(
        organizer_path("organizer.templates", organizer)
    ).content.decode()
    for name in ("organizer.templates.export", "organizer.templates.delete"):
        url = organizer_path(name, organizer, template=template.pk)
        assert f'href="{url}"' in content, name


@pytest.mark.django_db
def test_the_template_list_renders_no_raw_django_comment(
    client_with_perms, organizer, event, template
):
    """The page explains itself in comments; none of them may reach the user.

    ``{# ... #}`` is lexed by ``django.template.base.tag_re``, compiled without
    ``re.DOTALL``: a comment spanning lines is not a comment at all and lands in
    the response verbatim. That happened on ``report_list.html``.
    """
    content = client_with_perms.get(
        organizer_path("organizer.templates", organizer)
    ).content.decode()
    assert "{#" not in content
    assert "#}" not in content
    assert "{% comment %}" not in content
    assert "Creating and changing a template goes to the graphical editor" not in (
        content
    )


def test_no_template_of_this_module_uses_a_multiline_hash_comment():
    """Static guard over the templates owned by portability-dev.

    Counterpart of the persistence-dev guard in ``tests/test_permissions.py``.
    A rendering test only covers the branches some test happens to reach; this
    one reads the files and rejects a ``{#`` that is not closed on the same line.
    """
    import re
    from pathlib import Path

    import pretix_custom_reports

    root = Path(pretix_custom_reports.__file__).parent / "templates"
    owned = [
        "import_confirm.html",
        "import_form.html",
        "resolution_report.html",
        "template_apply.html",
        "template_confirm_delete.html",
        "template_form.html",
        "template_list.html",
        "template_pick.html",
    ]
    offenders = []
    for name in owned:
        path = root / "pretix_custom_reports" / name
        assert path.exists(), name
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.finditer(r"\{#", line):
                start = match.end()
                if "#}" not in line[start:]:
                    offenders.append(f"{name}:{lineno}")
    hint = "Django's {# #} comment cannot span lines -- use {% comment %}: "
    assert not offenders, hint + ", ".join(offenders)


@pytest.mark.django_db
def test_creating_a_template_stores_it_on_the_organizer(
    client_with_perms, organizer, event
):
    response = client_with_perms.post(
        organizer_path("organizer.templates.add", organizer),
        {
            "name": "From the form",
            "description": "",
            "identifier": "",
            "base": "order",
            "definition": json.dumps(definition(base="order")),
        },
    )
    assert response.status_code == 302
    with scopes_disabled():
        stored = ReportDefinition.objects.get(name="From the form")
        assert stored.organizer_id == organizer.pk
        assert stored.event_id is None
        assert LogEntry.objects.filter(action_type=contracts.LOG_ACTION_ADDED).exists()


@pytest.mark.django_db
def test_changing_a_template_is_logged(client_with_perms, organizer, event, template):
    response = client_with_perms.post(
        organizer_path("organizer.templates.edit", organizer, template=template.pk),
        {
            "name": "Renamed",
            "description": "",
            "identifier": template.identifier,
            "base": template.base,
            "definition": json.dumps(template.definition),
        },
    )
    assert response.status_code == 302
    with scopes_disabled():
        template.refresh_from_db()
        assert template.name == "Renamed"
        assert LogEntry.objects.filter(
            action_type=contracts.LOG_ACTION_CHANGED
        ).exists()


@pytest.mark.django_db
def test_deleting_a_template_keeps_the_copies(
    client_with_perms, organizer, event, template, registry
):
    make_questions(event)
    with scopes_disabled():
        copy = apply_template(plan_template(template, event, registry=registry))
    response = client_with_perms.post(
        organizer_path("organizer.templates.delete", organizer, template=template.pk)
    )
    assert response.status_code == 302
    with scopes_disabled():
        copy.refresh_from_db()
        assert copy.pk
        assert copy.source_template_id is None
        assert not ReportDefinition.objects.filter(pk=template.pk).exists()


@pytest.mark.django_db
def test_exporting_a_template_serves_a_file(
    client_with_perms, organizer, event, template
):
    response = client_with_perms.get(
        organizer_path("organizer.templates.export", organizer, template=template.pk)
    )
    assert response.status_code == 200
    document = json.loads(response.content.decode("utf-8"))
    portable = contracts.validate_portable_document(document)
    assert portable.name == template.name
    assert document["source"] == organizer.slug


@pytest.mark.django_db
def test_the_organizer_views_refuse_an_event_report(
    client_with_perms, organizer, event
):
    report = make_report(event=event)
    response = client_with_perms.get(
        organizer_path("organizer.templates.edit", organizer, template=report.pk)
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_the_organizer_views_refuse_a_template_of_another_organizer(
    client_with_perms, organizer, other_organizer
):
    foreign = make_template(other_organizer, name="Foreign")
    response = client_with_perms.get(
        organizer_path("organizer.templates.edit", organizer, template=foreign.pk)
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_the_organizer_views_need_the_organizer_permission(client, organizer, template):
    user = limited_user(
        organizer,
        "no-org-perm@example.org",
        ["event.settings.general:write"],
    )
    client.login(email=user.email, password=PASSWORD)
    response = client.get(organizer_path("organizer.templates", organizer))
    assert response.status_code in (403, 404)


@pytest.mark.django_db
def test_the_organizer_views_are_404_without_an_event_using_the_plugin(
    client_with_perms, organizer, event, template
):
    with scopes_disabled():
        event.plugins = ""
        event.save(update_fields=["plugins"])
    response = client_with_perms.get(organizer_path("organizer.templates", organizer))
    assert response.status_code == 404


# ===========================================================================
# Event-level views
# ===========================================================================


@pytest.mark.django_db
def test_the_event_offers_the_templates_of_its_organizer(
    client_with_perms, event, template, other_organizer
):
    make_template(other_organizer, name="Not yours")
    response = client_with_perms.get(report_path("event.reports.templates", event))
    assert response.status_code == 200
    assert b"Shirt list" in response.content
    assert b"Not yours" not in response.content


@pytest.mark.django_db
def test_the_apply_page_shows_the_resolution_before_writing(
    client_with_perms, event, organizer
):
    make_questions(event, tshirt_identifier="tshirt_size", with_newsletter=False)
    template = make_template(
        organizer,
        definition=definition(
            columns=("order.code", "answer.tshirt-size", "answer.newsletter")
        ),
    )
    response = client_with_perms.get(
        report_path("event.reports.templates.apply", event, template=template.pk)
    )
    assert response.status_code == 200
    assert b"answer.newsletter" in response.content
    assert b"answer.tshirt_size" in response.content
    with scopes_disabled():
        assert ReportDefinition.objects.filter(event=event).count() == 0


@pytest.mark.django_db
def test_the_apply_page_refuses_to_write_until_a_choice_is_made(
    client_with_perms, event, organizer
):
    make_questions(event, with_newsletter=False)
    template = make_template(
        organizer,
        definition=definition(columns=("order.code", "answer.newsletter")),
    )
    url = report_path("event.reports.templates.apply", event, template=template.pk)

    response = client_with_perms.post(url, {"action": "confirm", "strategy": "abort"})
    assert response.status_code == 200
    with scopes_disabled():
        assert ReportDefinition.objects.filter(event=event).count() == 0

    response = client_with_perms.post(url, {"action": "confirm", "strategy": "skip"})
    assert response.status_code == 302
    with scopes_disabled():
        copy = ReportDefinition.objects.get(event=event)
    assert [c["field"] for c in copy.definition["columns"]] == ["order.code"]


@pytest.mark.django_db
def test_a_loaded_template_lands_in_the_graphical_editor(
    client_with_perms, event, template
):
    """The copy is handed to the user in the editor, not in the JSON form.

    Loading a template is the start of editing, not the end of it: the copy
    usually needs a name of its own and often a column more. Keyed on the stable
    identifier like every other link into the editor.
    """
    make_questions(event)
    url = report_path("event.reports.templates.apply", event, template=template.pk)
    response = client_with_perms.post(url, {"action": "confirm", "strategy": "abort"})
    assert response.status_code == 302
    with scopes_disabled():
        copy = ReportDefinition.objects.get(event=event)
    assert response["Location"] == report_path(
        "editor.edit", event, identifier=copy.identifier
    )
    assert not response["Location"].endswith(f"/{copy.pk}/")


@pytest.mark.django_db
def test_the_apply_page_ignores_a_hand_posted_keep_strategy(
    client_with_perms, event, organizer
):
    """S-006, the template half: the page offers ``abort`` and ``skip`` only.

    ``keep`` belongs to the event copy and switches off the compiler check in
    ``resolve_definition``. ``position.price`` on base ``order`` resolves but
    needs an aggregate, so this template must not become a report.
    """
    template = make_template(
        organizer,
        name="Needs an aggregate",
        base="order",
        definition=definition(base="order", columns=("position.price",)),
    )
    url = report_path("event.reports.templates.apply", event, template=template.pk)

    response = client_with_perms.post(url, {"action": "confirm", "strategy": "keep"})
    assert response.status_code == 200
    with scopes_disabled():
        assert ReportDefinition.objects.filter(event=event).count() == 0

    # Control group: the two strategies the page really offers behave as before.
    for strategy in ("abort", "skip"):
        response = client_with_perms.post(
            url, {"action": "confirm", "strategy": strategy}
        )
        assert response.status_code == 200, strategy
    with scopes_disabled():
        assert ReportDefinition.objects.filter(event=event).count() == 0


@pytest.mark.django_db
def test_exporting_a_template_survives_a_stored_lone_surrogate(
    client_with_perms, organizer, event
):
    """S-003: ``ensure_ascii=True`` keeps the download a download.

    A template row written before the payload gate rejected unpaired
    surrogates can still hold one; serialising it with ``ensure_ascii=False``
    raises ``UnicodeEncodeError`` on the way into the response body.
    """
    poisoned = definition(columns=("order.code",))
    poisoned["columns"][0]["label"] = "x\ud800"
    template = make_template(organizer, name="Poisoned", definition=poisoned)

    response = client_with_perms.get(
        organizer_path("organizer.templates.export", organizer, template=template.pk)
    )
    assert response.status_code == 200
    document = json.loads(response.content.decode("utf-8"))
    assert document["definition"]["columns"][0]["label"] == "x\ud800"


@pytest.mark.django_db
def test_the_apply_view_is_permission_checked(client, organizer, event, template):
    user = limited_user(organizer, "read-only@example.org", ["event.orders:read"])
    client.login(email=user.email, password=PASSWORD)
    response = client.get(
        report_path("event.reports.templates.apply", event, template=template.pk)
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_the_apply_view_refuses_a_template_of_another_organizer(
    client_with_perms, event, other_organizer
):
    foreign = make_template(other_organizer, name="Foreign")
    response = client_with_perms.get(
        report_path("event.reports.templates.apply", event, template=foreign.pk)
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_a_template_with_a_question_this_event_does_not_know_at_all(
    event, organizer, registry
):
    """No questions at all in the target: everything answer-shaped is missing."""
    with scopes_disabled():
        assert not Question.objects.filter(event=event).exists()
    template = make_template(organizer)
    with scopes_disabled():
        plan = plan_template(template, event, registry=registry)
    assert not plan.ok
    assert [e.source for e in plan.report.missing] == ["answer.tshirt-size"]
