# Owner: frontend-dev (ORCHESTRIERUNG.md section 5)
#
# Tests for the report editor: the page, its JSON endpoints, the permission and
# CSRF gates, the preview limit and -- the point of the whole exercise -- a full
# round trip of every golden fixture through the editor's own JavaScript model.
#
# Wave 2: everything in here runs against the **real** field registry
# (registry.library.field_registry) and the **real** query compiler
# (query.compiler.ReportQueryCompiler). The wave-1 stubs are gone, and so are
# the two api/examples/ routes that served the golden fixtures as stand-ins for
# stored reports; a report now comes out of models.ReportDefinition.
#
# What that costs, and why it is worth it: the editor's tests now need real
# event data. A registry is built for one concrete event, so "answer.tshirt-size
# exists" is a statement about Question rows, "meta.event.campaign exists" one
# about an EventMetaProperty, and a preview only shows rows if there are orders.
# The fixtures below build exactly the event tests/fixtures/definitions/_index.json
# promises, which is what makes the golden fixtures usable end to end.
#
# Four things in here are unusual and deliberate:
#
# 1. The URLconf. urls.py belongs to the integrator (ORCHESTRIERUNG.md section
#    5), so neither the editor routes nor persistence-dev's CRUD routes are
#    wired up yet. They live next to their views (api.api_urlpatterns,
#    editor.editor_urlpatterns, crud.event_urlpatterns) and this module injects
#    them the same way pretix would: it appends them to the plugin's own
#    urlpatterns and reloads pretix.multidomain.maindomain_urlconf, which builds
#    the "plugins:<app_label>" namespace at import time. That means these tests
#    exercise the real namespace, the real prefix and the real middleware chain,
#    and they keep passing unchanged once the integrator adds the two lines from
#    handoff/requests/frontend-dev-an-integrator-urls.md.
# 2. The node subprocess. The editor's state <-> JSON mapping lives in
#    report-editor-model.js because that is where the browser needs it. Testing
#    it from Python would test a re-implementation, so the real file is executed
#    under node instead. Skipped when node is not installed.
# 3. The example plugin. plugin_and_meta_fields.json needs a field from another
#    plugin, and a receiver of an EventPluginSignal must belong to an app with
#    PretixPluginMeta that is enabled for the event. Installing a second
#    distribution mid-test-run is impossible, so this uses the __mocked_app
#    escape hatch pretix provides for exactly this
#    (pretix/base/signals.py:70-71).
# 4. The compiler doubles. Two tests need a renderer that misbehaves. They wrap
#    the real compiler and replace the renderers on the compiled columns instead
#    of substituting a fake compiler, so the query, the event scope and the row
#    limit stay real.
"""Tests for the graphical report editor and its JSON endpoints."""

import datetime
import importlib
import json
import pathlib
import pytest
import shutil
import subprocess
from dataclasses import replace
from decimal import Decimal
from django.test import Client
from django.urls import clear_url_caches, reverse
from django.utils.timezone import now
from django_scopes import scopes_disabled
from pretix.base.models import (
    EventMetaProperty,
    EventMetaValue,
    Item,
    ItemCategory,
    Order,
    OrderPayment,
    OrderPosition,
    Question,
    QuestionAnswer,
    QuestionOption,
    Team,
    User,
)
from pretix.base.models.orders import InvoiceAddress

import pretix_custom_reports
from pretix_custom_reports.contracts import (
    OPERATOR_SPECS,
    PREVIEW_ROW_LIMIT,
    Base,
    DefinitionValidationError,
    Operator,
    validate_definition,
    validate_portable_document,
)
from pretix_custom_reports.models import ReportDefinition
from pretix_custom_reports.signals import URL_NAMESPACE
from pretix_custom_reports.views.api import api_urlpatterns
from pretix_custom_reports.views.crud import event_urlpatterns
from pretix_custom_reports.views.editor import (
    editor_urlpatterns,
    template_editor_urlpatterns,
)
from pretix_custom_reports.views.portability import portability_event_urlpatterns
from pretix_custom_reports.views.templates import (
    templates_event_urlpatterns,
    templates_organizer_urlpatterns,
)

from .conftest import PASSWORD, VIEW_PERMISSION

PLUGIN_ROOT = pathlib.Path(pretix_custom_reports.__file__).resolve().parent
MODEL_JS = (
    PLUGIN_ROOT / "static" / "pretix_custom_reports" / "js" / "report-editor-model.js"
)
FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "definitions"

#: Every valid golden fixture, by slug.
FIXTURE_SLUGS = sorted(
    p.stem for p in FIXTURE_DIR.glob("*.json") if not p.name.startswith("_")
)

#: App label of the example plugin. Matches the key the golden fixture
#: plugin_and_meta_fields.json expects.
PLUGIN_APP_LABEL = "pretix_demo"

#: Permission the CRUD views require for saving. Deliberately *not* imported
#: from views/crud.py: if that string ever changes, the read-only user built
#: here must stop being read-only in this file too, visibly.
CHANGE_PERMISSION = "event.settings.general:write"


# ---------------------------------------------------------------------------
# URL wiring
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def editor_routes():
    """Add the editor, CRUD and portability routes to the plugin's urlpatterns.

    Exactly what ``urls.py`` will do; done here because that file has another
    owner. Since wave 2 the CRUD routes are in here too (the editor's form posts
    to them) and so are portability-dev's event routes (the editor links to
    them). The organizer-level template routes joined them when the editor
    learned to edit organizer templates: ``TemplateEditorView`` posts to
    ``organizer.templates.add``/``.edit`` and links to ``.export``, and its own
    two routes come from ``template_editor_urlpatterns``, which the integrator
    still has to wire (handoff/requests/frontend-dev-an-integrator-template-editor-urls.md).

    Reverting is important: other test modules must see the unmodified URLconf.
    """
    from pretix.multidomain import maindomain_urlconf

    import pretix_custom_reports.urls as plugin_urls

    original = list(plugin_urls.urlpatterns)
    plugin_urls.urlpatterns = (
        list(editor_urlpatterns)
        + list(template_editor_urlpatterns)
        + list(api_urlpatterns)
        + list(event_urlpatterns)
        + list(portability_event_urlpatterns)
        + list(templates_event_urlpatterns)
        + list(templates_organizer_urlpatterns)
        + original
    )
    importlib.reload(maindomain_urlconf)
    clear_url_caches()
    yield
    plugin_urls.urlpatterns = original
    importlib.reload(maindomain_urlconf)
    clear_url_caches()


def url_for(name, event, **kwargs):
    return reverse(
        f"{URL_NAMESPACE}:{name}",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, **kwargs},
    )


def load_fixture(slug):
    with (FIXTURE_DIR / f"{slug}.json").open("r", encoding="utf-8") as fp:
        return json.load(fp)


def load_invalid(name):
    """One of the deliberately broken fixtures from ``invalid/``."""
    with (FIXTURE_DIR / "invalid" / f"{name}.json").open("r", encoding="utf-8") as fp:
        return json.load(fp)


def post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


# ---------------------------------------------------------------------------
# The event the golden fixtures describe
# ---------------------------------------------------------------------------
#
# _index.json promises three questions, one event meta property and one plugin
# field. Without them a golden fixture does not resolve against the real
# registry, and half of these tests would be asserting on an event that no
# fixture was written for.


def _mocked_plugin_app(app_label=PLUGIN_APP_LABEL):
    class App:
        name = app_label

        class PretixPluginMeta:
            name = "Demo plugin"
            version = "1.0.0"

    return App


def demo_receiver(sender, base, **kwargs):
    """The receiver a third-party plugin would register."""
    from django.db.models import Value
    from django.db.models.functions import Concat

    from pretix_custom_reports.contracts import (
        DataType,
        Operator as Op,
        ReportField,
        plugin_field_key,
    )

    alias = "pcr_pretix_demo_demo_value"
    coerced = Base.coerce(base)
    return [
        ReportField(
            key=plugin_field_key(PLUGIN_APP_LABEL, "demo_value"),
            label="Value from another plugin",
            group="Demo plugin",
            datatype=DataType.STRING,
            bases=(coerced,),
            orm_path=alias,
            annotation=lambda ctx: {
                alias: Concat(
                    Value("demo-"),
                    "code" if ctx.base is Base.ORDER else "order__code",
                )
            },
            filter_operators=(Op.EXACT, Op.CONTAINS, Op.IS_NOT_EMPTY),
            sortable=True,
            provider=PLUGIN_APP_LABEL,
        )
    ]


@pytest.fixture
def demo_plugin(event):
    """Connect the example plugin and enable it for the event."""
    from pretix_custom_reports.registry import cache as registry_cache
    from pretix_custom_reports.registry.signals import register_report_fields

    demo_receiver.__mocked_app = _mocked_plugin_app()
    register_report_fields.connect(
        demo_receiver, dispatch_uid="test_editor_demo_plugin", weak=False
    )
    active = [name for name in (event.plugins or "").split(",") if name]
    if PLUGIN_APP_LABEL not in active:
        active.append(PLUGIN_APP_LABEL)
        event.plugins = ",".join(active)
        with scopes_disabled():
            event.save(update_fields=["plugins"])
    registry_cache.clear_local_cache()
    yield demo_receiver
    register_report_fields.disconnect(
        demo_receiver, dispatch_uid="test_editor_demo_plugin"
    )
    registry_cache.clear_local_cache()


@pytest.fixture
def registry_cache_isolated():
    """Empty the registry's process-local cache around a test.

    ``field_registry()`` is a process-wide singleton keyed by event primary key,
    and primary keys repeat across tests.
    """
    from pretix_custom_reports.registry import cache as registry_cache

    registry_cache.clear_local_cache()
    yield
    registry_cache.clear_local_cache()


@pytest.fixture
def event_data(event, demo_plugin, registry_cache_isolated):
    """Questions, a meta property and enough orders for a preview.

    The numbers matter for the preview tests: four orders, three of them paid
    and each with two positions, so a preview limited to two rows really is
    truncated and a limit of three really returns three rows. One position is
    canceled and one order is in test mode, which is what
    ``options_full.json`` selects for.
    """
    with scopes_disabled():
        prop = EventMetaProperty.objects.create(
            organizer=event.organizer, name="campaign", default=""
        )
        EventMetaValue.objects.create(event=event, property=prop, value="summer")

        tshirt = Question.objects.create(
            event=event,
            question="T-shirt size",
            identifier="tshirt-size",
            type=Question.TYPE_CHOICE,
            position=0,
        )
        for position, label in enumerate(("S", "M", "L", "XL")):
            QuestionOption.objects.create(
                question=tshirt, answer=label, position=position
            )
        arrival = Question.objects.create(
            event=event,
            question="Day of arrival",
            identifier="arrival-date",
            type=Question.TYPE_DATE,
            position=1,
        )
        newsletter = Question.objects.create(
            event=event,
            question="Newsletter opt-in",
            identifier="newsletter",
            type=Question.TYPE_BOOLEAN,
            position=2,
        )

        category = ItemCategory.objects.create(event=event, name="Tickets")
        item = Item.objects.create(
            event=event,
            category=category,
            name="Regular ticket",
            internal_name="regular",
            default_price=Decimal("19.00"),
            admission=True,
        )
        channel = event.organizer.sales_channels.get(identifier="web")

        orders = {}
        # The first order is pinned down to the cent and to the second: the
        # formatting test filters for its code and asserts on its cells.
        for index, (code, status, testmode) in enumerate(
            [
                ("FMT01", Order.STATUS_PAID, False),
                ("PAID2", Order.STATUS_PAID, False),
                ("PAID3", Order.STATUS_PAID, False),
                ("TEST4", Order.STATUS_PENDING, True),
            ]
        ):
            order = Order.objects.create(
                event=event,
                code=code,
                status=status,
                testmode=testmode,
                email=f"{code.lower()}@example.org",
                sales_channel=channel,
                datetime=datetime.datetime(
                    2026, 3, 1, 10, 0, tzinfo=datetime.timezone.utc
                )
                + datetime.timedelta(days=index),
                expires=now() + datetime.timedelta(days=10),
                total=Decimal("19.00") * 2,
            )
            InvoiceAddress.objects.create(
                order=order, company="ACME", city="Berlin", country="DE"
            )
            for positionid in (1, 2):
                position = OrderPosition.objects.create(
                    order=order,
                    item=item,
                    price=Decimal("19.00"),
                    tax_rate=Decimal("0.00"),
                    tax_value=Decimal("0.00"),
                    positionid=positionid,
                    attendee_name_parts={"_legacy": f"Attendee {code} {positionid}"},
                    attendee_email=f"attendee{positionid}@example.org",
                )
                QuestionAnswer.objects.create(
                    orderposition=position, question=tshirt, answer="L"
                )
                QuestionAnswer.objects.create(
                    orderposition=position, question=arrival, answer="2026-09-01"
                )
                QuestionAnswer.objects.create(
                    orderposition=position, question=newsletter, answer="True"
                )
            if status == Order.STATUS_PAID:
                OrderPayment.objects.create(
                    order=order,
                    provider="banktransfer",
                    state=OrderPayment.PAYMENT_STATE_CONFIRMED,
                    amount=order.total,
                    payment_date=now(),
                )
            orders[code] = order

        # One canceled position, so include_canceled_positions is testable.
        OrderPosition.all.create(
            order=orders["PAID3"],
            item=item,
            price=Decimal("19.00"),
            positionid=3,
            canceled=True,
        )

    from pretix_custom_reports.registry import cache as registry_cache

    registry_cache.clear_local_cache()
    return {
        "orders": orders,
        "item": item,
        "questions": {
            "tshirt-size": tshirt,
            "arrival-date": arrival,
            "newsletter": newsletter,
        },
        "meta_property": prop,
    }


@pytest.fixture
def stored_report(event):
    """Factory for a saved report, the thing the editor now opens."""

    def make(definition, name="Stored report", identifier=""):
        with scopes_disabled():
            return ReportDefinition.objects.create(
                event=event,
                name=name,
                identifier=identifier,
                definition=definition,
            )

    return make


@pytest.fixture
def user_read_only(organizer):
    """User who may view reports (and previews) but must not save them."""
    user = User.objects.create_user("read-only@example.org", PASSWORD)
    team = Team.objects.create(
        organizer=organizer,
        name="Order readers",
        all_events=True,
        all_event_permissions=False,
        limit_event_permissions={VIEW_PERMISSION: True},
    )
    team.members.add(user)
    return user


@pytest.fixture
def client_read_only(client, user_read_only):
    client.login(email=user_read_only.email, password=PASSWORD)
    return client


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_editor_page_loads(client_with_perms, event):
    resp = client_with_perms.get(url_for("editor.new", event))
    assert resp.status_code == 200
    content = resp.content.decode()
    # The shell, the config blob and the two static files -- no CDN anywhere.
    assert 'id="pcr-editor"' in content
    assert 'id="pcr-config"' in content
    assert "pretix_custom_reports/js/report-editor-model.js" in content
    assert "pretix_custom_reports/js/report-editor.js" in content
    assert "pretix_custom_reports/css/report-editor.css" in content
    assert "//cdn" not in content
    assert "csrfmiddlewaretoken" in content


@pytest.mark.django_db
def test_the_editor_page_renders_no_raw_django_comment(client_with_perms, event):
    """A user found template comments printed on the page as visible text.

    ``{#`` .. ``#}`` is lexed by ``django.template.base.tag_re``, whose comment
    alternative does not match across a newline. A comment spanning several
    lines is therefore not a comment at all: it is character data and lands in
    the response verbatim. ``{% comment %}`` has no such limit, because it is a
    block tag the parser skips past.

    Checked from the outside, on the rendered page, because that is where the
    defect was visible and because a grep over the template file would keep
    passing the day the shell starts including a partial from somewhere else.
    """
    response = client_with_perms.get(url_for("editor.new", event))
    assert response.status_code == 200  # not a login redirect: this must be the page
    content = response.content.decode()
    assert "{#" not in content
    assert "#}" not in content
    # And the three paragraphs by name, in case the braces ever become legal.
    assert "What the CRUD form of persistence-dev expects" not in content
    assert "The editor shell. Bootstrap 3 markup" not in content
    assert "File import/export and templates live in" not in content


@pytest.mark.django_db
def test_the_stored_editor_page_renders_no_raw_django_comment(
    client_with_perms, event, stored_report
):
    """Same template, the other route -- the one a user actually opens twice."""
    report = stored_report(load_fixture("minimal_order"), name="Comments")
    response = client_with_perms.get(
        url_for("editor.edit", event, identifier=report.identifier)
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "{#" not in content
    assert "#}" not in content


@pytest.mark.django_db
def test_the_preview_html_renders_no_raw_django_comment(
    client_with_perms, event, event_data
):
    """Same defect, same fix, in the fragment the preview returns as "html"."""
    payload = post_json(
        client_with_perms,
        url_for("api.preview", event),
        {"definition": load_fixture("minimal_order")},
    ).json()
    assert "{#" not in payload["html"]
    assert "#}" not in payload["html"]
    assert "The live preview table, rendered on the server" not in payload["html"]


@pytest.mark.django_db
def test_editor_page_denied_without_permission(client_without_perms, event):
    resp = client_without_perms.get(url_for("editor.new", event))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_editor_page_requires_login(client, event):
    resp = client.get(url_for("editor.new", event))
    assert resp.status_code == 302
    assert "/control/login" in resp["Location"]


def editor_config(content):
    """Pull the JSON config blob out of the rendered editor page."""
    marker = '<script type="application/json" id="pcr-config">'
    start = content.index(marker) + len(marker)
    end = content.index("</script>", start)
    return json.loads(content[start:end])


@pytest.mark.django_db
@pytest.mark.parametrize("slug", FIXTURE_SLUGS)
def test_editor_page_opens_every_stored_golden_fixture(
    client_with_perms, event, stored_report, slug
):
    """DoD: every golden fixture can be stored and opened in the editor.

    The model canonicalises on save, so what the editor gets handed is the
    canonical document -- which is exactly what the round trip has to emit
    again.
    """
    raw = load_fixture(slug)
    report = stored_report(raw, name=slug)
    resp = client_with_perms.get(
        url_for("editor.edit", event, identifier=report.identifier)
    )
    assert resp.status_code == 200
    config = editor_config(resp.content.decode())
    assert config["initial"] == validate_definition(raw).as_dict()
    assert config["urls"]["fields"]
    assert config["urls"]["preview"]
    assert config["i18n"]["issue_no_columns"]


@pytest.mark.django_db
def test_editor_page_unknown_identifier_is_404(client_with_perms, event):
    resp = client_with_perms.get(url_for("editor.edit", event, identifier="nope"))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_editor_page_does_not_open_a_report_of_another_event(
    client_with_perms, event, organizer
):
    """CLAUDE.md rule 4: the queryset is scoped to the event in the URL."""
    with scopes_disabled():
        from pretix.base.models import Event

        other = Event.objects.create(
            organizer=organizer,
            name="Other",
            slug="other",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
            live=True,
        )
        foreign = ReportDefinition.objects.create(
            event=other, name="Foreign", definition=load_fixture("minimal_order")
        )
    resp = client_with_perms.get(
        url_for("editor.edit", event, identifier=foreign.identifier)
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_editor_page_does_not_open_an_organizer_template(
    client_with_perms, event, organizer
):
    """Templates have no event and belong to portability-dev's views."""
    with scopes_disabled():
        template = ReportDefinition.objects.create(
            organizer=organizer,
            name="Template",
            definition=load_fixture("minimal_order"),
        )
    assert template.is_template
    resp = client_with_perms.get(
        url_for("editor.edit", event, identifier=template.identifier)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Saving: the editor form and persistence-dev's CRUD views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_new_report_posts_to_the_create_view(client_with_perms, event):
    content = client_with_perms.get(url_for("editor.new", event)).content.decode()
    assert f'action="{url_for("event.reports.add", event)}"' in content
    assert 'id="pcr-save"' in content
    assert "disabled" not in content.split('id="pcr-save"')[1].split(">")[0]


@pytest.mark.django_db
def test_stored_report_posts_to_its_update_view(
    client_with_perms, event, stored_report
):
    report = stored_report(load_fixture("minimal_order"))
    content = client_with_perms.get(
        url_for("editor.edit", event, identifier=report.identifier)
    ).content.decode()
    assert (
        f'action="{url_for("event.reports.edit", event, report=report.pk)}"' in content
    )


@pytest.mark.django_db
def test_editor_posts_the_stable_identifier_back(
    client_with_perms, event, stored_report
):
    """Otherwise the model would generate a new identifier on every save.

    ``ReportDefinitionForm`` lists ``identifier``; an absent field cleans to the
    empty string, and ``ReportDefinition.save()`` then mints a fresh one --
    breaking every scheduled export that refers to this report by identifier.
    """
    report = stored_report(load_fixture("minimal_order"))
    identifier = report.identifier
    assert identifier
    content = client_with_perms.get(
        url_for("editor.edit", event, identifier=identifier)
    ).content.decode()
    assert f'name="identifier" value="{identifier}"' in content

    # And the round trip really keeps it.
    resp = client_with_perms.post(
        url_for("event.reports.edit", event, report=report.pk),
        data={
            "name": "Renamed",
            "description": "",
            "identifier": identifier,
            "base": "order",
            "definition": json.dumps(load_fixture("minimal_order")),
        },
    )
    assert resp.status_code == 302
    with scopes_disabled():
        report.refresh_from_db()
    assert report.name == "Renamed"
    assert report.identifier == identifier

    # The counter-test, so the assertion above cannot pass by accident: without
    # the field the model really does mint a new identifier.
    resp = client_with_perms.post(
        url_for("event.reports.edit", event, report=report.pk),
        data={
            "name": "Renamed again",
            "description": "",
            "base": "order",
            "definition": json.dumps(load_fixture("minimal_order")),
        },
    )
    assert resp.status_code == 302
    with scopes_disabled():
        report.refresh_from_db()
    assert report.identifier != identifier


@pytest.mark.django_db
def test_read_only_user_gets_the_editor_without_a_save_target(
    client_read_only, user_read_only, event, stored_report
):
    """The preview is allowed, saving is not -- and the button says so."""
    assert not user_read_only.has_event_permission(
        event.organizer, event, CHANGE_PERMISSION
    )
    report = stored_report(load_fixture("minimal_order"))
    resp = client_read_only.get(
        url_for("editor.edit", event, identifier=report.identifier)
    )
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'action=""' in content
    assert "disabled" in content.split('id="pcr-save"')[1].split(">")[0]
    # ... and the CRUD view agrees.
    assert (
        client_read_only.post(
            url_for("event.reports.edit", event, report=report.pk), data={}
        ).status_code
        == 403
    )


# ---------------------------------------------------------------------------
# Import, export and templates (portability-dev's views)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_stored_report_offers_export_import_and_templates(
    client_with_perms, event, stored_report
):
    """The three ways in and out of the editor, all of them portability-dev's."""
    report = stored_report(load_fixture("minimal_order"))
    content = client_with_perms.get(
        url_for("editor.edit", event, identifier=report.identifier)
    ).content.decode()

    assert url_for("event.reports.export", event, report=report.pk) in content
    assert url_for("event.reports.import", event) in content
    assert url_for("event.reports.templates", event) in content
    # Marked for the "you have unsaved changes" guard, and the export link with
    # the sharper variant: its file comes from the database, not from the page.
    assert 'data-pcr-leave="export"' in content
    assert content.count('data-pcr-leave="page"') == 2


@pytest.mark.django_db
def test_a_new_report_cannot_be_exported_yet(client_with_perms, event):
    """There is nothing to download before the first save, and the page says so."""
    content = client_with_perms.get(url_for("editor.new", event)).content.decode()
    assert 'id="pcr-export"' not in content
    assert "Save this report to be able to export it as a file." in content
    # Import and templates do not need a stored report.
    assert url_for("event.reports.import", event) in content
    assert url_for("event.reports.templates", event) in content


@pytest.mark.django_db
def test_read_only_user_may_export_but_not_import(
    client_read_only, event, stored_report
):
    """Export reads, import and templates write -- and they are gated apart.

    ``ReportExportView`` requires ``event.orders:read`` like the editor itself,
    ``ReportImportView`` and ``TemplatePickView`` require
    ``event.settings.general:write``. Offering a link into a 403 would be worse
    than not offering it.
    """
    report = stored_report(load_fixture("minimal_order"))
    content = client_read_only.get(
        url_for("editor.edit", event, identifier=report.identifier)
    ).content.decode()

    assert url_for("event.reports.export", event, report=report.pk) in content
    assert url_for("event.reports.import", event) not in content
    assert url_for("event.reports.templates", event) not in content

    # ... and the views agree with the buttons, in both directions.
    assert (
        client_read_only.get(
            url_for("event.reports.export", event, report=report.pk)
        ).status_code
        == 200
    )
    assert (
        client_read_only.get(url_for("event.reports.import", event)).status_code == 403
    )


@pytest.mark.django_db
def test_editor_survives_missing_portability_routes(
    client_with_perms, event, stored_report, monkeypatch
):
    """A half-wired urls.py must cost a button, not the page.

    The routes come from three different handoff requests and will not all land
    in the same commit, so every link is reversed defensively.
    """
    from django.urls import NoReverseMatch

    from pretix_custom_reports.views import editor as editor_views

    real_url = editor_views.ReportEditorView.url

    def only_own_routes(self, name, **extra):
        if name.startswith("event.reports.") and name != "event.reports.edit":
            raise NoReverseMatch(name)
        return real_url(self, name, **extra)

    monkeypatch.setattr(editor_views.ReportEditorView, "url", only_own_routes)

    report = stored_report(load_fixture("minimal_order"))
    resp = client_with_perms.get(
        url_for("editor.edit", event, identifier=report.identifier)
    )
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'id="pcr-editor"' in content
    assert 'id="pcr-export"' not in content
    assert 'id="pcr-import"' not in content
    assert 'id="pcr-templates"' not in content


@pytest.mark.django_db
def test_export_link_serves_the_stored_definition(
    client_with_perms, event, stored_report
):
    """Following the link really produces the file, from the saved report."""
    raw = load_fixture("wide_order")
    report = stored_report(raw, name="Wide")
    resp = client_with_perms.get(
        url_for("event.reports.export", event, report=report.pk)
    )
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/json"
    assert "attachment;" in resp["Content-Disposition"]
    document = json.loads(resp.content.decode())
    # Validated with the contract's own envelope validator, not by poking at
    # keys: this is the file another installation has to be able to import.
    envelope = validate_portable_document(document)
    assert envelope.definition.as_dict() == validate_definition(raw).as_dict()
    assert envelope.name == "Wide"


@pytest.mark.django_db
@pytest.mark.parametrize("slug", FIXTURE_SLUGS)
def test_full_round_trip_editor_form_to_database_and_back(
    client_with_perms, event, slug
):
    """DoD, end to end: what the editor posts is what the editor gets back.

    This is the wave-2 version of the round trip -- no longer editor -> endpoint
    -> editor, but editor form -> persistence-dev's CreateView -> database ->
    editor page. The definition has to survive unchanged.
    """
    raw = load_fixture(slug)
    canonical = validate_definition(raw)
    resp = client_with_perms.post(
        url_for("event.reports.add", event),
        data={
            "name": slug,
            "description": "",
            "identifier": "",
            "base": canonical.base.value,
            # Exactly what the editor's hidden input carries: the canonical
            # document as a JSON string.
            "definition": canonical.as_json(),
        },
    )
    assert resp.status_code == 302, resp.content

    with scopes_disabled():
        report = event.custom_reports.get(name=slug)
    assert report.definition == canonical.as_dict()

    page = client_with_perms.get(
        url_for("editor.edit", event, identifier=report.identifier)
    )
    assert page.status_code == 200
    assert editor_config(page.content.decode())["initial"] == canonical.as_dict()


# ---------------------------------------------------------------------------
# Permissions, CSRF, methods -- the same for every endpoint
# ---------------------------------------------------------------------------


def api_endpoints(event):
    return {
        "fields": (url_for("api.fields", event), "get"),
        "validate": (url_for("api.validate", event), "post"),
        "preview": (url_for("api.preview", event), "post"),
    }


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["fields", "validate", "preview"])
def test_endpoints_deny_users_without_permission(client_without_perms, event, name):
    """A preview endpoint without a permission check is a data leak."""
    url, method = api_endpoints(event)[name]
    if method == "get":
        resp = client_without_perms.get(url)
    else:
        resp = post_json(
            client_without_perms, url, {"definition": load_fixture("minimal_order")}
        )
    assert resp.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["fields", "validate", "preview"])
def test_endpoints_require_login(client, event, name):
    url, method = api_endpoints(event)[name]
    resp = client.get(url) if method == "get" else post_json(client, url, {})
    assert resp.status_code == 302
    assert "/control/login" in resp["Location"]


@pytest.mark.django_db
def test_endpoints_deny_other_event(client_with_perms, event_without_plugin):
    """The plugin is not active for that event, so its routes must not resolve."""
    resp = client_with_perms.get(url_for("api.fields", event_without_plugin))
    assert resp.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["validate", "preview"])
def test_post_endpoints_are_csrf_protected(user_with_perms, event, name):
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.login(email=user_with_perms.email, password=PASSWORD)
    url = api_endpoints(event)[name][0]
    resp = post_json(csrf_client, url, {"definition": load_fixture("minimal_order")})
    assert resp.status_code == 403

    # ... and works with the token the editor page hands to the JavaScript.
    page = csrf_client.get(url_for("editor.new", event))
    token = page.cookies["pretix_csrftoken"].value
    resp = csrf_client.post(
        url,
        data=json.dumps({"definition": load_fixture("minimal_order")}),
        content_type="application/json",
        headers={"x-csrftoken": token},
    )
    assert resp.status_code == 200


@pytest.mark.django_db
def test_get_only_endpoint_rejects_post(client_with_perms, event):
    resp = post_json(client_with_perms, url_for("api.fields", event), {})
    assert resp.status_code == 405


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["validate", "preview"])
def test_post_only_endpoint_rejects_get(client_with_perms, event, name):
    resp = client_with_perms.get(api_endpoints(event)[name][0])
    assert resp.status_code == 405


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body,expected_code",
    [
        ("not json at all", "not_json"),
        ("[1, 2, 3]", "wrong_type"),
        ('{"nothing": true}', "missing"),
    ],
)
def test_broken_request_bodies_are_rejected(
    client_with_perms, event, body, expected_code
):
    resp = client_with_perms.post(
        url_for("api.preview", event), data=body, content_type="application/json"
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["ok"] is False
    assert payload["stage"] == "request"
    assert payload["errors"][0]["code"] == expected_code


# ---------------------------------------------------------------------------
# Field library
# ---------------------------------------------------------------------------


@pytest.fixture
def library(client_with_perms, event, event_data):
    resp = client_with_perms.get(url_for("api.fields", event))
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.django_db
def test_field_library_shape(library):
    assert library["ok"] is True
    assert library["schema_version"] == 1
    assert [b["value"] for b in library["bases"]] == [b.value for b in Base]
    assert set(library["operators"]) == {op.value for op in Operator}
    for name, spec in library["operators"].items():
        assert spec["value_kind"] == OPERATOR_SPECS[Operator(name)].value_kind.value
        assert spec["label"]
    assert library["limits"]["preview_rows"] == PREVIEW_ROW_LIMIT
    assert library["groups"]
    assert library["fields"]


@pytest.mark.django_db
def test_field_library_is_served_from_the_real_registry(library):
    """Wave 2: no stub anywhere in the response."""
    assert library["source"] == "EventFieldRegistry"
    assert len(library["fields"]) > 80


@pytest.mark.django_db
def test_field_library_covers_every_key_the_fixtures_use(library):
    """The library must offer every key ``_index.json`` declares as required.

    In wave 1 this checked the stub feed; in wave 2, with the same assertion, it
    checks the real registry. Either way a fixture that cannot be edited is a
    broken editor.
    """
    with (FIXTURE_DIR / "_index.json").open("r", encoding="utf-8") as fp:
        index = json.load(fp)
    required = list(index["required_field_keys"]["core"])
    required += [
        f"answer.{identifier}"
        for identifier in index["required_field_keys"]["questions"]["identifiers"]
    ]
    required += [
        f"meta.event.{name}"
        for name in index["required_field_keys"]["meta_properties"]["event"]
    ]
    required += list(index["required_field_keys"]["plugin"]["keys"])

    available = {field["key"] for field in library["fields"]}
    assert not set(required) - available


@pytest.mark.django_db
def test_field_library_marks_availability_per_base(library):
    fields = {field["key"]: field for field in library["fields"]}

    # An order field is directly usable on both bases.
    assert fields["order.code"]["bases"]["order"]["available"] is True
    assert fields["order.code"]["bases"]["orderposition"]["available"] is True
    assert fields["order.code"]["bases"]["order"]["requires_aggregate"] is False

    # A position field needs an aggregate on base "order" (SPEC.md F3) and is
    # not sortable there.
    price = fields["position.price"]
    assert price["bases"]["order"]["requires_aggregate"] is True
    assert price["bases"]["order"]["aggregates"]
    assert price["bases"]["order"]["sortable"] is False
    assert price["bases"]["orderposition"]["requires_aggregate"] is False
    assert price["bases"]["orderposition"]["sortable"] is True

    # Not everything is sortable even on the position base.
    assert fields["payment.providers"]["bases"]["orderposition"]["sortable"] is False


@pytest.mark.django_db
def test_choice_fields_offer_choices_not_free_text(library):
    """F6: a choice field must give the editor a value list, not a text box."""
    fields = {field["key"]: field for field in library["fields"]}
    status = fields["order.status"]
    assert status["datatype"] == "choice"
    assert [c["value"] for c in status["choices"]] == ["n", "p", "e", "c"]
    assert all(c["label"] for c in status["choices"])


@pytest.mark.django_db
def test_choice_question_offers_its_options(library):
    """The wave-2 version of the same rule, for a field only this event has.

    The registry builds two ReportField objects for one question: on base
    ``orderposition`` it carries the options and is event scoped, on base
    ``order`` it is an aggregate without them. The library has to describe the
    field from the richer variant, otherwise a choice question would get a free
    text box in the filter area.
    """
    fields = {field["key"]: field for field in library["fields"]}
    tshirt = fields["answer.tshirt-size"]
    assert tshirt["datatype"] == "choice"
    assert [c["value"] for c in tshirt["choices"]] == ["S", "M", "L", "XL"]
    assert tshirt["value_scope"] == "event"
    assert tshirt["group"] == "answers"
    assert tshirt["bases"]["orderposition"]["operators"]
    assert tshirt["bases"]["order"]["requires_aggregate"] is True


@pytest.mark.django_db
def test_date_fields_offer_relative_operators(library):
    """F6/F8: without these, scheduled reports cannot stay meaningful."""
    fields = {field["key"]: field for field in library["fields"]}
    operators = fields["order.datetime"]["bases"]["order"]["operators"]
    for relative in (
        "relative_today",
        "relative_last_days",
        "relative_next_days",
        "relative_current_month",
        "relative_current_year",
        "relative_since_event_start",
    ):
        assert relative in operators
    # ... and the absolute ones as well, because the editor shows both.
    assert "between" in operators
    assert "gte" in operators


@pytest.mark.django_db
def test_question_fields_are_event_specific(client_with_perms, event, event_data):
    """A question of another event must not appear in this event's library.

    The registry is built per event; this asserts the property the editor
    depends on, because the field library is the allow-list the browser offers.
    """
    with scopes_disabled():
        from pretix.base.models import Event

        other = Event.objects.create(
            organizer=event.organizer,
            name="Other",
            slug="other",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
            live=True,
        )
        Question.objects.create(
            event=other,
            question="Only over there",
            identifier="foreign-question",
            type=Question.TYPE_TEXT,
        )
    payload = client_with_perms.get(url_for("api.fields", event)).json()
    keys = {field["key"] for field in payload["fields"]}
    assert "answer.tshirt-size" in keys
    assert "answer.foreign-question" not in keys


@pytest.mark.django_db
def test_renaming_a_question_moves_its_key(
    client_with_perms, event, event_data, registry_cache_isolated
):
    """The realistic way a saved report stops resolving (ADR 0001 section 3.2)."""
    with scopes_disabled():
        question = event_data["questions"]["tshirt-size"]
        question.identifier = "shirt-size"
        question.save(update_fields=["identifier"])

    payload = client_with_perms.get(url_for("api.fields", event)).json()
    keys = {field["key"] for field in payload["fields"]}
    assert "answer.shirt-size" in keys
    assert "answer.tshirt-size" not in keys

    # A report that still refers to the old key opens, with a warning.
    warnings = post_json(
        client_with_perms,
        url_for("api.validate", event),
        {"definition": load_fixture("orderposition_questions")},
    ).json()["warnings"]
    assert ("columns[4]", "unknown_field") in {(w["path"], w["code"]) for w in warnings}


@pytest.mark.django_db
def test_meta_property_field_only_exists_when_the_organizer_defines_it(
    client_with_perms, event, registry_cache_isolated
):
    """``meta.event.campaign`` is an EventMetaProperty, not a constant."""
    payload = client_with_perms.get(url_for("api.fields", event)).json()
    assert "meta.event.campaign" not in {f["key"] for f in payload["fields"]}

    with scopes_disabled():
        EventMetaProperty.objects.create(
            organizer=event.organizer, name="campaign", default=""
        )
    from pretix_custom_reports.registry import cache as registry_cache

    registry_cache.clear_local_cache()

    payload = client_with_perms.get(url_for("api.fields", event)).json()
    assert "meta.event.campaign" in {f["key"] for f in payload["fields"]}


@pytest.mark.django_db
def test_a_report_using_fields_this_event_does_not_have_is_reported_not_hidden(
    client_with_perms, event, registry_cache_isolated
):
    """An event without the questions and the meta property: warn per path.

    This is the case wave 1 could not exercise -- the stub registry knew every
    key of every fixture. Opening such a report has to work, because otherwise
    it can never be repaired (ADR 0001 section 3.2).
    """
    payload = post_json(
        client_with_perms,
        url_for("api.validate", event),
        {"definition": load_fixture("orderposition_questions")},
    ).json()
    assert payload["ok"] is True
    missing = {w["path"]: w["code"] for w in payload["warnings"]}
    # three question columns, three question filters, one question sort stage
    assert missing["columns[4]"] == "unknown_field"
    assert missing["columns[5]"] == "unknown_field"
    assert missing["columns[6]"] == "unknown_field"
    assert missing["filters.children[0]"] == "unknown_field"
    assert missing["sorting[0]"] == "unknown_field"
    # ... and the definition comes back untouched, ready to be fixed.
    assert (
        payload["definition"]
        == validate_definition(load_fixture("orderposition_questions")).as_dict()
    )


@pytest.mark.django_db
def test_preview_of_a_report_with_missing_fields_names_all_of_them(
    client_with_perms, event, registry_cache_isolated
):
    """Running it, unlike opening it, fails -- with the full list."""
    resp = post_json(
        client_with_perms,
        url_for("api.preview", event),
        {"definition": load_fixture("orderposition_questions")},
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["stage"] == "fields"
    assert set(payload["missing"]) == {
        "answer.tshirt-size",
        "answer.arrival-date",
        "answer.newsletter",
    }


@pytest.mark.django_db
def test_deprecated_fields_are_hidden(client_with_perms, event, monkeypatch):
    """A deprecated field still resolves for old reports but leaves the library."""
    from pretix_custom_reports.registry.library import field_registry
    from pretix_custom_reports.views import api

    class WithDeprecated:
        """The real registry with one field marked deprecated."""

        def __init__(self):
            self.inner = field_registry()

        def get_fields(self, event, base):
            fields = dict(self.inner.get_fields(event, base))
            fields["order.code"] = replace(fields["order.code"], deprecated=True)
            return fields

        def resolve(self, key, event, base):
            return self.get_fields(event, base).get(key)

    monkeypatch.setattr(api, "get_registry", WithDeprecated)
    payload = client_with_perms.get(url_for("api.fields", event)).json()
    assert "order.code" not in {field["key"] for field in payload["fields"]}
    assert "order.status" in {field["key"] for field in payload["fields"]}


# ---------------------------------------------------------------------------
# Round trip: fixture -> endpoint -> canonical JSON
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("slug", FIXTURE_SLUGS)
def test_validate_round_trips_every_fixture(client_with_perms, event, event_data, slug):
    """DoD: load a fixture, hand it back, get identical canonical JSON."""
    raw = load_fixture(slug)
    resp = post_json(
        client_with_perms, url_for("api.validate", event), {"definition": raw}
    )
    assert resp.status_code == 200, resp.content
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["definition"] == validate_definition(raw).as_dict()
    assert payload["warnings"] == []


@pytest.mark.django_db
def test_validate_reports_all_structural_errors_at_once(client_with_perms, event):
    broken = {
        "schema_version": 1,
        "base": "order",
        "columns": [
            {"field": "order__code"},
            {"field": "order.status", "aggregate": "median"},
        ],
        "sorting": [{"field": "order.code", "direction": "sideways"}],
    }
    resp = post_json(
        client_with_perms, url_for("api.validate", event), {"definition": broken}
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["stage"] == "structure"
    codes = {issue["code"] for issue in payload["errors"]}
    assert "invalid_field_key" in codes
    assert "unknown_aggregate" in codes
    assert "unknown_direction" in codes
    assert all(issue["path"] for issue in payload["errors"])


@pytest.mark.django_db
def test_validate_warns_about_unresolvable_field_but_still_returns_it(
    client_with_perms, event
):
    """A renamed question is a regular state, not a reason to refuse the report."""
    definition = {
        "schema_version": 1,
        "base": "order",
        "columns": [{"field": "order.code"}, {"field": "answer.gone-away"}],
    }
    resp = post_json(
        client_with_perms, url_for("api.validate", event), {"definition": definition}
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["definition"]["columns"][1]["field"] == "answer.gone-away"
    assert [w["code"] for w in payload["warnings"]] == ["unknown_field"]
    assert payload["warnings"][0]["path"] == "columns[1]"


@pytest.mark.django_db
def test_validate_flags_registry_stage_problems_per_path(client_with_perms, event):
    definition = {
        "schema_version": 1,
        "base": "order",
        # position.price without an aggregate, and a sort on an aggregated field
        "columns": [{"field": "order.code"}, {"field": "position.price"}],
        "sorting": [{"field": "position.price", "direction": "asc"}],
    }
    payload = post_json(
        client_with_perms, url_for("api.validate", event), {"definition": definition}
    ).json()
    codes = {(w["path"], w["code"]) for w in payload["warnings"]}
    assert ("columns[1]", "aggregate_required") in codes
    assert ("sorting[0]", "not_sortable") in codes


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("slug", FIXTURE_SLUGS)
def test_preview_runs_for_every_fixture(client_with_perms, event, event_data, slug):
    raw = load_fixture(slug)
    definition = validate_definition(raw)
    resp = post_json(
        client_with_perms, url_for("api.preview", event), {"definition": raw}
    )
    assert resp.status_code == 200, resp.content
    payload = resp.json()

    visible = [c for c in definition.columns if not c.hidden]
    assert [c["key"] for c in payload["columns"]] == [c.field for c in visible]
    assert payload["row_count"] <= PREVIEW_ROW_LIMIT
    assert payload["limit"] == PREVIEW_ROW_LIMIT
    for row in payload["rows"]:
        assert len(row) == len(payload["columns"])
        assert all(isinstance(cell, str) for cell in row)
    assert "<table" in payload["html"]
    assert payload["warnings"] == []


@pytest.mark.django_db
def test_preview_never_exceeds_the_row_limit(client_with_perms, event, event_data):
    """The preview must never load the full data set (SPEC.md section 4)."""
    url = url_for("api.preview", event)
    raw = load_fixture("orderposition_basic")

    payload = post_json(
        client_with_perms, url, {"definition": raw, "limit": 10_000}
    ).json()
    assert payload["limit"] == PREVIEW_ROW_LIMIT
    assert payload["row_count"] <= PREVIEW_ROW_LIMIT

    payload = post_json(client_with_perms, url, {"definition": raw, "limit": 3}).json()
    assert payload["limit"] == 3
    assert payload["row_count"] == 3

    payload = post_json(
        client_with_perms, url, {"definition": raw, "limit": "all the rows please"}
    ).json()
    assert payload["limit"] == PREVIEW_ROW_LIMIT


@pytest.mark.django_db
def test_preview_slices_in_sql_not_in_python(client_with_perms, event, event_data):
    """``preview=True`` has to reach the compiler, not just the row loop.

    Without it the database would materialise the full result set and the row
    cap would be cosmetic. Asserted on the generated SQL of the queryset the
    view compiles, because the response cannot show the difference.
    """
    from pretix_custom_reports.views import api

    captured = {}
    real = api.get_compiler

    def capturing():
        compiler = real()
        inner = compiler.compile

        def compile(definition, event, **kwargs):
            compiled = inner(definition, event, **kwargs)
            captured["kwargs"] = kwargs
            captured["limit"] = compiled.effective_limit
            captured["sql"] = str(compiled.queryset.query)
            return compiled

        compiler.compile = compile
        return compiler

    api.get_compiler = capturing
    try:
        resp = post_json(
            client_with_perms,
            url_for("api.preview", event),
            {"definition": load_fixture("minimal_order")},
        )
    finally:
        api.get_compiler = real
    assert resp.status_code == 200
    assert captured["kwargs"] == {"preview": True}
    assert captured["limit"] == PREVIEW_ROW_LIMIT
    assert f"LIMIT {PREVIEW_ROW_LIMIT}" in captured["sql"]


@pytest.mark.django_db
def test_preview_reports_the_estimated_total(client_with_perms, event, event_data):
    url = url_for("api.preview", event)
    raw = load_fixture("minimal_order")
    payload = post_json(client_with_perms, url, {"definition": raw, "limit": 2}).json()
    assert payload["total"] >= payload["row_count"]
    assert payload["truncated"] is True
    assert "rows shown" in payload["html"]

    # The count is the expensive half and may be switched off.
    payload = post_json(
        client_with_perms, url, {"definition": raw, "total": False}
    ).json()
    assert payload["total"] is None


@pytest.mark.django_db
def test_preview_applies_the_filters(client_with_perms, event, event_data):
    """Wave 1 could not test this: the stub compiler ignored filters."""
    url = url_for("api.preview", event)
    definition = {
        "schema_version": 1,
        "base": "order",
        "columns": [{"field": "order.code"}],
        "filters": {
            "op": "and",
            "children": [
                {"field": "order.code", "operator": "exact", "value": "FMT01"}
            ],
        },
    }
    payload = post_json(client_with_perms, url, {"definition": definition}).json()
    assert payload["rows"] == [["FMT01"]]
    assert payload["total"] == 1


@pytest.mark.django_db
def test_preview_applies_the_sorting(client_with_perms, event, event_data):
    url = url_for("api.preview", event)
    definition = {
        "schema_version": 1,
        "base": "order",
        "columns": [{"field": "order.code"}],
        "sorting": [{"field": "order.code", "direction": "desc"}],
    }
    payload = post_json(client_with_perms, url, {"definition": definition}).json()
    codes = [row[0] for row in payload["rows"]]
    assert codes == sorted(codes, reverse=True)


@pytest.mark.django_db
def test_preview_drops_hidden_columns(client_with_perms, event, event_data):
    raw = load_fixture("wide_order")
    assert any(column.get("hidden") for column in raw["columns"])
    payload = post_json(
        client_with_perms, url_for("api.preview", event), {"definition": raw}
    ).json()
    assert "order.comment" not in [c["key"] for c in payload["columns"]]


@pytest.mark.django_db
def test_preview_applies_the_column_format(client_with_perms, event, event_data):
    """Formatting happens on the server, per column, from the definition."""
    definition = {
        "schema_version": 1,
        "base": "order",
        "columns": [
            {"field": "order.total"},
            {"field": "order.total", "format": {"number_style": "currency"}},
            {"field": "order.total", "format": {"number_style": "raw"}},
            {"field": "order.testmode", "format": {"boolean_style": "yes_no"}},
            {"field": "order.testmode", "format": {"boolean_style": "one_zero"}},
            {"field": "order.datetime", "format": {"date_style": "iso"}},
            {"field": "order.datetime", "format": {"date_style": "date_only"}},
        ],
        "filters": {
            "op": "and",
            "children": [
                {"field": "order.code", "operator": "exact", "value": "FMT01"}
            ],
        },
    }
    payload = post_json(
        client_with_perms, url_for("api.preview", event), {"definition": definition}
    ).json()
    assert payload["row_count"] == 1, payload
    row = payload["rows"][0]
    assert row[2] == "38.00"  # raw
    assert row[1] != row[2]  # currency formatting actually happened
    assert "38" in row[1]
    assert row[3] in ("Yes", "No")
    assert row[4] in ("1", "0")
    assert row[5].startswith("2026-03-01T")
    assert "T" not in row[6]
    assert row[6] != row[5]


@pytest.mark.django_db
def test_preview_rejects_a_field_that_does_not_exist_here(client_with_perms, event):
    definition = {
        "schema_version": 1,
        "base": "order",
        "columns": [{"field": "answer.gone-away"}],
    }
    resp = post_json(
        client_with_perms, url_for("api.preview", event), {"definition": definition}
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["stage"] == "fields"
    assert payload["missing"] == ["answer.gone-away"]
    assert payload["errors"][0]["path"] == "columns[0]"


@pytest.mark.django_db
def test_preview_rejects_a_position_field_without_aggregate(client_with_perms, event):
    definition = load_invalid("missing_aggregate_on_order")
    resp = post_json(
        client_with_perms, url_for("api.preview", event), {"definition": definition}
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["stage"] == "compile"
    assert any(issue["code"] == "aggregate_required" for issue in payload["errors"])


def compiler_with_renderer(render):
    """The real compiler, but every cell is rendered by *render*.

    Wraps rather than replaces: the definition is really compiled, the queryset
    is really executed against the event, only the last step -- turning a row
    object into a cell -- misbehaves.

    Note the import: this builds the compiler from ``query.compiler`` directly
    and deliberately *not* through ``views.api.get_compiler``. The two callers
    monkeypatch exactly that name, so going through the seam would make the
    wrapper wrap itself -- once per request, until Python runs out of stack.
    """
    from pretix_custom_reports.query.compiler import ReportQueryCompiler
    from pretix_custom_reports.registry.library import field_registry

    class Wrapper:
        def compile(self, definition, event, **kwargs):
            inner = ReportQueryCompiler(field_registry())
            compiled = inner.compile(definition, event, **kwargs)
            compiled.columns = tuple(
                replace(column, render=render) for column in compiled.columns
            )
            return compiled

    return Wrapper()


@pytest.mark.django_db
def test_preview_survives_a_broken_field(
    client_with_perms, event, event_data, monkeypatch
):
    """A field whose renderer explodes must not take the editor down with it."""
    from pretix_custom_reports.views import api

    def explode(row):
        return 1 / 0

    monkeypatch.setattr(api, "get_compiler", lambda: compiler_with_renderer(explode))
    resp = post_json(
        client_with_perms,
        url_for("api.preview", event),
        {"definition": load_fixture("minimal_order")},
    )
    assert resp.status_code == 400
    assert resp.json()["stage"] == "execute"


@pytest.mark.django_db
def test_preview_escapes_cell_contents(
    client_with_perms, event, event_data, monkeypatch
):
    """Order data ends up in HTML; it must never be able to become markup."""
    from pretix_custom_reports.views import api

    payload_string = "<script>alert('x')</script>"
    monkeypatch.setattr(
        api, "get_compiler", lambda: compiler_with_renderer(lambda row: payload_string)
    )
    result = post_json(
        client_with_perms,
        url_for("api.preview", event),
        {"definition": load_fixture("minimal_order")},
    ).json()
    assert result["rows"][0][0] == payload_string
    assert "<script>alert" not in result["html"]
    assert "&lt;script&gt;" in result["html"]


@pytest.mark.django_db
def test_preview_shows_only_this_events_orders(client_with_perms, event, event_data):
    """CLAUDE.md rule 4, from the outside: the preview is one event's data."""
    with scopes_disabled():
        from pretix.base.models import Event

        other = Event.objects.create(
            organizer=event.organizer,
            name="Other",
            slug="other",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
            live=True,
        )
        Order.objects.create(
            event=other,
            code="FOREIGN",
            status=Order.STATUS_PAID,
            email="foreign@example.org",
            sales_channel=other.organizer.sales_channels.get(identifier="web"),
            datetime=now(),
            expires=now() + datetime.timedelta(days=10),
            total=Decimal("5.00"),
        )
    payload = post_json(
        client_with_perms,
        url_for("api.preview", event),
        {"definition": load_fixture("minimal_order")},
    ).json()
    assert "FOREIGN" not in [row[0] for row in payload["rows"]]


# ---------------------------------------------------------------------------
# Encodability of the JSON responses (finding S-003)
# ---------------------------------------------------------------------------
#
# "\ud800" is a lone high surrogate. It is *syntactically valid JSON*, so
# json.loads accepts it and hands back a Python str that cannot be encoded to
# UTF-8 -- and it therefore reaches us through an import file, through the CRUD
# form's JSON textarea or through the editor's JSON panel, and sits in a
# Column.label or a filter value from then on.
#
# With ensure_ascii=False, Django builds the response as a str and encodes it in
# django/http/response.py, which raises UnicodeEncodeError: a 500 on every
# api/validate/ and api/preview/ call for that report, i.e. a report that can no
# longer be opened *or repaired* in the editor. _ApiView.json therefore
# serialises with ensure_ascii=True. That is invisible in the browser -- the
# editor reads every response with JSON.parse, for which "\ud800" and the raw
# character are the same string -- and it is asserted here from both sides: the
# response body is pure ASCII, and the value survives the round trip.
#
# The payload gate (portability/payload.py) rejects such a document on the way
# in since S-003 was fixed. These tests are the second half of that fix and stay
# meaningful regardless: they cover the values that entered before the gate
# existed, and any path that does not run through it.

LONE_SURROGATE = "\ud800"


def _surrogate_definition():
    return {
        "schema_version": 1,
        "base": "order",
        "columns": [{"field": "order.code", "label": "x" + LONE_SURROGATE}],
    }


@pytest.mark.django_db
def test_validate_survives_a_lone_surrogate_in_a_label(
    client_with_perms, event, event_data
):
    response = post_json(
        client_with_perms,
        url_for("api.validate", event),
        {"definition": _surrogate_definition()},
    )
    assert response.status_code == 200, response.content[:400]
    response.content.decode("ascii")  # no raw surrogate in the body
    payload = json.loads(response.content.decode("utf-8"))
    assert payload["definition"]["columns"][0]["label"] == "x" + LONE_SURROGATE


@pytest.mark.django_db
def test_preview_survives_a_lone_surrogate_in_a_label(
    client_with_perms, event, event_data
):
    """The preview echoes the column heading, and used to die doing it."""
    response = post_json(
        client_with_perms,
        url_for("api.preview", event),
        {"definition": _surrogate_definition()},
    )
    assert response.status_code == 200, response.content[:400]
    response.content.decode("ascii")
    payload = json.loads(response.content.decode("utf-8"))
    assert payload["columns"][0]["label"] == "x" + LONE_SURROGATE
    assert payload["rows"], "the preview should still have produced rows"


@pytest.mark.django_db
def test_every_editor_endpoint_answers_in_pure_ascii(
    client_with_perms, event, event_data
):
    """The rule, not the single symptom: no endpoint of ours emits raw non-ASCII.

    A label is only the shortest way in. Whichever endpoint grows a new echo of
    user-supplied text next, this test is what keeps it encodable.
    """
    responses = [
        client_with_perms.get(url_for("api.fields", event)),
        post_json(
            client_with_perms,
            url_for("api.validate", event),
            {"definition": _surrogate_definition()},
        ),
        post_json(
            client_with_perms,
            url_for("api.preview", event),
            {"definition": _surrogate_definition(), "limit": 3},
        ),
        # ... and the error envelopes, which quote the offending input back.
        post_json(
            client_with_perms,
            url_for("api.validate", event),
            {"definition": {"schema_version": 1, "base": "order" + LONE_SURROGATE}},
        ),
    ]
    for response in responses:
        assert response.status_code in (200, 400), response.status_code
        response.content.decode("ascii")


# ---------------------------------------------------------------------------
# One renderer for the preview and the export (finding T-001)
# ---------------------------------------------------------------------------


def test_the_preview_renders_cells_with_the_exporter_s_function():
    """No second implementation of the column formats. That is the whole fix.

    ColumnFormat.date_style/number_style/boolean_style used to be applied by a
    private format_cell() in views/api.py and by nothing else, so "date only"
    showed a date on screen and a full timestamp in the file (T-001). The
    function now lives in exporters.py and both callers import it; this test
    fails the moment views/api.py grows its own again.
    """
    from pretix_custom_reports import exporters
    from pretix_custom_reports.views import api

    assert api.get_cell_renderer() is exporters.format_cell_value
    assert not hasattr(api, "format_cell")
    assert not hasattr(api, "_format_temporal")
    assert not hasattr(api, "_format_number")


# ---------------------------------------------------------------------------
# The JavaScript model, executed under node
# ---------------------------------------------------------------------------

NODE = shutil.which("node")

HARNESS_JS = """
'use strict';
const fs = require('fs');
const Model = require(process.argv[2]);
const job = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = {};
job.cases.forEach(function (testcase) {
    const model = new Model(job.meta);
    let state = model.load(testcase.definition);
    const result = {};
    result.dump = model.dump(state);
    result.dump_json = JSON.stringify(model.dump(state));
    result.issues = model.localIssues(state);
    result.previewable = model.isPreviewable(state);
    result.column_count = state.columns.length;

    // load -> edit -> undo the edit -> dump must be unchanged
    if (testcase.edit_field) {
        model.addColumn(state, testcase.edit_field);
        result.after_add = model.dump(state).columns.length;
        model.moveInList(state.columns, state.columns.length - 1, 0);
        result.moved_first = state.columns[0].field;
        model.moveInList(state.columns, 0, state.columns.length - 1);
        state.columns.pop();
    }
    result.dump_after_edit = model.dump(state);

    // dump -> load -> dump must be a fixed point
    result.reloaded = model.dump(model.load(result.dump));

    if (testcase.switch_base) {
        const plan = model.baseImpact(state, testcase.switch_base);
        result.plan = plan;
        result.plan_empty = model.baseImpactIsEmpty(plan);
        model.applyBase(state, testcase.switch_base);
        result.dump_switched = model.dump(state);
    }
    out[testcase.name] = result;
});
process.stdout.write(JSON.stringify(out));
"""


def run_model_js(tmp_path, meta, cases):
    """Execute report-editor-model.js under node and return its results."""
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"meta": meta, "cases": cases}), encoding="utf-8")
    completed = subprocess.run(
        [NODE, str(harness), str(MODEL_JS), str(job)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture
def js_meta(library):
    """Exactly the metadata the browser gets from GET api/fields/.

    Since wave 2 that is the real registry's field table, so the JavaScript
    model is exercised against the fields it will actually see.
    """
    return {
        "operators": library["operators"],
        "fields": {field["key"]: field for field in library["fields"]},
        "limits": library["limits"],
        "groups": library["groups"],
    }


@pytest.mark.skipif(not NODE, reason="node is not installed")
@pytest.mark.django_db
def test_js_model_round_trips_every_golden_fixture(tmp_path, js_meta):
    """DoD: load, edit and emit again -- byte-identical canonical JSON.

    Runs the editor's own model file, so this is the round trip the browser
    performs, not a Python re-implementation of it.
    """
    cases = [
        {
            "name": slug,
            "definition": load_fixture(slug),
            "edit_field": "order.code",
        }
        for slug in FIXTURE_SLUGS
    ]
    results = run_model_js(tmp_path, js_meta, cases)

    for slug in FIXTURE_SLUGS:
        raw = load_fixture(slug)
        canonical = validate_definition(raw)
        result = results[slug]

        assert result["dump"] == canonical.as_dict(), slug
        # Same key order, not just the same content: what the editor sends is
        # what gets stored.
        assert json.dumps(json.loads(result["dump_json"]), ensure_ascii=False) == (
            canonical.as_json()
        ), slug
        # An edit that is undone leaves no trace.
        assert result["after_add"] == len(raw["columns"]) + 1, slug
        assert result["moved_first"] == "order.code", slug
        assert result["dump_after_edit"] == canonical.as_dict(), slug
        # Re-loading our own output is a fixed point.
        assert result["reloaded"] == canonical.as_dict(), slug
        # And the result still validates.
        assert validate_definition(result["dump"]) == canonical, slug


@pytest.mark.skipif(not NODE, reason="node is not installed")
@pytest.mark.django_db
def test_js_model_reports_local_issues(tmp_path, js_meta):
    cases = [
        {
            "name": "empty",
            "definition": {"schema_version": 1, "base": "order", "columns": []},
        },
        {
            "name": "aggregate_missing",
            "definition": {
                "schema_version": 1,
                "base": "order",
                "columns": [{"field": "position.price"}],
            },
        },
        {
            "name": "duplicate_sorting",
            "definition": {
                "schema_version": 1,
                "base": "order",
                "columns": [{"field": "order.code"}],
                "sorting": [
                    {"field": "order.code", "direction": "asc"},
                    {"field": "order.code", "direction": "desc"},
                ],
            },
        },
        {
            "name": "unknown_field",
            "definition": {
                "schema_version": 1,
                "base": "order",
                "columns": [{"field": "answer.gone-away"}],
            },
        },
        {
            "name": "good",
            "definition": load_fixture("minimal_order"),
        },
    ]
    results = run_model_js(tmp_path, js_meta, cases)

    def codes(name):
        return {issue["code"] for issue in results[name]["issues"]}

    assert "no_columns" in codes("empty")
    assert "aggregate_required" in codes("aggregate_missing")
    assert "duplicate_sorting" in codes("duplicate_sorting")
    assert "field_unavailable" in codes("unknown_field")
    assert codes("good") == set()
    assert results["good"]["previewable"] is True
    assert results["empty"]["previewable"] is False


@pytest.mark.skipif(not NODE, reason="node is not installed")
@pytest.mark.django_db
def test_js_model_base_switch_explains_and_applies_the_loss(
    tmp_path, js_meta, client_with_perms, event
):
    """F3: switching the base must say what falls away, then do exactly that."""
    cases = [
        {
            "name": "to_order",
            "definition": load_fixture("orderposition_basic"),
            "switch_base": "order",
        },
        {
            "name": "to_position",
            "definition": load_fixture("order_with_aggregates"),
            "switch_base": "orderposition",
        },
        {
            "name": "no_change",
            "definition": load_fixture("minimal_order"),
            "switch_base": "orderposition",
        },
    ]
    results = run_model_js(tmp_path, js_meta, cases)

    to_order = results["to_order"]
    assert to_order["plan_empty"] is False
    # Position level columns survive as aggregates, sorting by them does not.
    assert {entry["key"] for entry in to_order["plan"]["add_aggregate"]}
    assert "position.positionid" in {
        entry["key"] for entry in to_order["plan"]["drop_sorting"]
    }
    switched = to_order["dump_switched"]
    assert switched["base"] == "order"
    assert all(
        column.get("aggregate")
        for column in switched["columns"]
        if column["field"].startswith(
            ("position.", "item.", "variation.", "subevent.", "seat.", "voucher.")
        )
    )

    # The other direction: an aggregate the field no longer allows on the new
    # base is dropped. Question answers are per position there, so joining them
    # stops being available.
    to_position = results["to_position"]
    assert "answer.tshirt-size" in {
        entry["key"] for entry in to_position["plan"]["drop_aggregate"]
    }
    switched_back = to_position["dump_switched"]
    assert switched_back["base"] == "orderposition"
    assert all(
        column.get("aggregate") is None
        for column in switched_back["columns"]
        if column["field"].startswith("answer.")
    )

    # An order-only report switches to the position base without losing anything.
    assert results["no_change"]["plan_empty"] is True

    # Whatever the switch produced must be accepted by the server, with no
    # registry warnings left over.
    for name in ("to_order", "to_position", "no_change"):
        payload = post_json(
            client_with_perms,
            url_for("api.validate", event),
            {"definition": results[name]["dump_switched"]},
        ).json()
        assert payload["ok"] is True, name
        assert payload["warnings"] == [], (name, payload["warnings"])


@pytest.mark.skipif(not NODE, reason="node is not installed")
@pytest.mark.django_db
def test_js_model_output_is_accepted_by_the_preview(
    tmp_path, js_meta, client_with_perms, event
):
    """The editor's output goes straight into the preview without a detour."""
    cases = [{"name": slug, "definition": load_fixture(slug)} for slug in FIXTURE_SLUGS]
    results = run_model_js(tmp_path, js_meta, cases)
    for slug in FIXTURE_SLUGS:
        resp = post_json(
            client_with_perms,
            url_for("api.preview", event),
            {"definition": results[slug]["dump"]},
        )
        assert resp.status_code == 200, (slug, resp.content)


# ---------------------------------------------------------------------------
# The editor in a real browser
# ---------------------------------------------------------------------------
#
# Everything above tests the editor without ever executing its DOM half. The
# four things that only a browser can answer are drag & drop (Sortable.js), the
# select2 enhancement, the typed value widgets and the base switch dialogue --
# and those were the open point wave 1 left behind.
#
# No new dependency is installed for this: playwright is already in the venv,
# and the browser is the one on the machine (Edge or Chrome, driven through
# playwright's `channel`), so nothing is downloaded either. If neither is
# available the tests skip instead of pretending.
#
# The server is pytest-django's `live_server`, not the dev server: it serves the
# routes this module injects (urls.py belongs to the integrator and does not
# carry them yet), the test database and the static files, and it needs no
# process anyone has to remember to start.


def _launch_browser(playwright):
    """A chromium that is already on this machine, or ``None``.

    Order: the system browsers first, playwright's own download last -- the
    bundled build is usually not there, and this must never install one.
    """
    for channel in ("msedge", "chrome"):
        try:
            return playwright.chromium.launch(channel=channel)
        except Exception:
            continue
    try:
        return playwright.chromium.launch()
    except Exception:
        return None


@pytest.fixture(scope="session")
def browser():
    """One browser for all browser tests, plus Django's async escape hatch.

    ``DJANGO_ALLOW_ASYNC_UNSAFE``: playwright's synchronous API keeps an event
    loop running in this thread, and Django refuses ORM access from a thread
    with a running loop. The flag is Django's documented way out for exactly
    this situation (docs/topics/async, "asgiref.sync"); the ORM calls in
    question are the test's own fixtures, and the live server runs in its own
    thread either way. Restored afterwards so no other test silently inherits
    it.
    """
    playwright_module = pytest.importorskip(
        "playwright.sync_api", reason="playwright is not installed"
    )
    import os

    previous = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

    playwright = playwright_module.sync_playwright().start()
    instance = _launch_browser(playwright)
    if instance is None:
        playwright.stop()
        pytest.skip("no chromium, Edge or Chrome available to drive")
    yield instance
    instance.close()
    playwright.stop()
    if previous is None:
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
    else:
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = previous


@pytest.fixture
def browser_page(browser, live_server, event, user_with_perms):
    """A logged-in browser page factory, for tests that pick their own URL."""
    login_client = Client()
    assert login_client.login(email=user_with_perms.email, password=PASSWORD)
    session_cookie = login_client.cookies["pretix_session"].value

    context = browser.new_context(
        base_url=live_server.url, viewport={"width": 1400, "height": 2400}
    )
    context.add_cookies(
        [
            {
                "name": "pretix_session",
                "value": session_cookie,
                "url": live_server.url,
            }
        ]
    )

    def open_editor(path):
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(live_server.url + path)
        page.wait_for_selector("#pcr-library-list li[data-field-key]", timeout=20_000)
        return page, errors

    yield open_editor
    context.close()


@pytest.fixture
def editor_in_browser(browser, live_server, event, event_data, user_with_perms):
    """A logged-in browser page with the editor open and booted.

    The session is created with Django's test client and the cookie is handed to
    the browser; logging in through the form would test pretix' login page, not
    ours.
    """
    login_client = Client()
    assert login_client.login(email=user_with_perms.email, password=PASSWORD)
    session_cookie = login_client.cookies["pretix_session"].value

    # A tall viewport, and not for comfort: the test settings switch off
    # django-compressor's precompilers, so the control panel's SCSS is served
    # as SCSS and the browser ignores it. Without Bootstrap's grid the two
    # editor columns stack, which pushes the column list far below a normal
    # 720px viewport -- and a drop target outside the viewport cannot be
    # reached by a pointer.
    context = browser.new_context(
        base_url=live_server.url, viewport={"width": 1400, "height": 2400}
    )
    context.add_cookies(
        [
            {
                "name": "pretix_session",
                "value": session_cookie,
                "url": live_server.url,
            }
        ]
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(live_server.url + url_for("editor.new", event))
    # The editor has booted once the field library is on the page: that only
    # happens after GET api/fields/ came back.
    page.wait_for_selector("#pcr-library-list li[data-field-key]", timeout=20_000)
    yield page, errors
    context.close()


def json_state(page):
    """The editor's canonical JSON, as the JSON panel shows it."""
    return json.loads(page.input_value("#pcr-json"))


def library_action(page, key, title):
    """Click one of the buttons on a field in the library.

    The buttons are ``visibility: hidden`` until the entry is hovered or
    focused, so the hover is not test decoration -- it is what a user does, and
    leaving it out is why playwright waits forever.
    """
    entry = page.locator(f"#pcr-library-list li[data-field-key='{key}']")
    entry.scroll_into_view_if_needed()
    entry.hover()
    entry.locator(f"button[title='{title}']").click()


def drag(page, source_selector, target_selector):
    """Drag one element onto another the way a hand does it.

    Not ``page.drag_and_drop``: Sortable.js uses the browser's native HTML5
    drag, and that only gets going after a mousedown followed by a small move
    and then a *gradual* travel to the target. A single jump produces a
    ``dragstart`` and nothing else -- verified by listening for the drag events
    while trying it.

    The other thing this taught us is in the viewport comment on the context
    fixture: a drop target outside the viewport never receives ``dragover``,
    however patiently the pointer is moved towards it.
    """
    source = page.locator(source_selector)
    target = page.locator(target_selector)
    source.scroll_into_view_if_needed()
    source.hover()
    start = source.bounding_box()
    end = target.bounding_box()
    assert start and end, (source_selector, target_selector)

    page.mouse.down()
    # A short nudge first: this is what turns the press into a drag.
    page.mouse.move(start["x"] + start["width"] / 2 + 8, start["y"] + 4)
    page.wait_for_timeout(50)
    page.mouse.move(
        end["x"] + end["width"] / 2,
        end["y"] + end["height"] / 2,
        steps=20,
    )
    page.wait_for_timeout(100)
    # One more move inside the target, so Sortable sees a dragover on it.
    page.mouse.move(
        end["x"] + end["width"] / 2 + 2,
        end["y"] + end["height"] / 2 + 2,
    )
    page.mouse.up()


@pytest.mark.django_db(transaction=True)
def test_browser_drag_and_drop_adds_a_column(editor_in_browser):
    """Dragging a field from the library into the column list adds a column.

    The buttons next to each field do the same thing and are covered by the
    model tests; this is about Sortable.js actually being loaded and its clone
    ending up in the right list.
    """
    page, errors = editor_in_browser
    assert page.text_content("#pcr-columns-count") == "0"
    # The empty list still offers a drop area; an empty <tbody> would be zero
    # pixels tall and could not be hit at all.
    assert page.locator("#pcr-columns tr.pcr-drop-hint").is_visible()

    drag(
        page,
        "#pcr-library-list li[data-field-key='order.code']",
        "#pcr-columns tr.pcr-drop-hint",
    )

    page.wait_for_function(
        "() => document.getElementById('pcr-columns-count').textContent === '1'"
    )
    assert [column["field"] for column in json_state(page)["columns"]] == ["order.code"]
    assert page.locator("#pcr-columns tr.pcr-drop-hint").count() == 0

    # A second field lands next to the first one, not instead of it.
    drag(
        page,
        "#pcr-library-list li[data-field-key='order.email']",
        "#pcr-columns tr:last-child",
    )
    page.wait_for_function(
        "() => document.getElementById('pcr-columns-count').textContent === '2'"
    )
    assert {column["field"] for column in json_state(page)["columns"]} == {
        "order.code",
        "order.email",
    }
    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_browser_filter_widgets_match_the_datatype(editor_in_browser):
    """SPEC.md F6: a choice field gets a value list, a date field a date picker.

    Free text is the exception, so this asserts on the *type* of the widgets the
    editor builds, not just on their presence.
    """
    page, errors = editor_in_browser

    # A choice field: multi-select for "is one of", not a text box.
    library_action(page, "order.status", "Add as filter")
    page.wait_for_selector(".pcr-condition-field")
    page.select_option("select.pcr-condition-operator", "in")
    page.wait_for_selector("select.pcr-multiselect")
    options = page.eval_on_selector_all(
        "select.pcr-multiselect option", "els => els.map(e => e.value)"
    )
    assert options == ["n", "p", "e", "c"]
    page.select_option("select.pcr-multiselect", ["p", "n"])
    # The browser reports selected options in document order, not click order.
    assert json_state(page)["filters"]["children"][0]["value"] == ["n", "p"]

    # A date field: a real date input *and* the relative operators.
    library_action(page, "order.datetime", "Add as filter")
    rows = page.locator(".pcr-condition-operator")
    operators = rows.nth(1).locator("option")
    values = [operators.nth(i).get_attribute("value") for i in range(operators.count())]
    assert "relative_last_days" in values
    assert "between" in values

    rows.nth(1).select_option("gte")
    page.wait_for_selector("input[type='datetime-local']")
    page.fill("input[type='datetime-local']", "2026-03-01T10:00")
    condition = json_state(page)["filters"]["children"][1]
    assert condition["operator"] == "gte"
    assert condition["value"].startswith("2026-03-01")

    # ... and the relative operator needs a day count, not a date.
    rows.nth(1).select_option("relative_last_days")
    page.wait_for_selector("input[type='number']")
    assert json_state(page)["filters"]["children"][1]["operator"] == (
        "relative_last_days"
    )
    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_browser_select2_enhances_the_field_chooser(editor_in_browser):
    """The field chooser has ~90 options; without select2 it is unusable.

    select2 is loaded by the control panel itself (pretixcontrol/base.html), so
    this also checks that the editor's assumption about the surrounding stack
    holds.
    """
    page, errors = editor_in_browser
    page.click("#pcr-add-condition")
    page.wait_for_selector("select.pcr-condition-field")
    # enhanceSelect() is deferred by a tick, so wait for select2's own markup.
    page.wait_for_selector(".select2-container", timeout=10_000)
    # A plain <select> would leave the user scrolling through ~90 options.
    assert page.locator("select.pcr-condition-field").count() == 1
    assert page.locator(".select2-container").count() >= 1

    page.click(".select2-container")
    page.fill("input.select2-search__field", "ZIP")
    page.wait_for_selector(".select2-results__option")
    # Two fields match ("ZIP code" and "Attendee ZIP code"), so pick by exact
    # text -- and not a group heading, which is an option element as well.
    page.click(".select2-results__option[role='option']:text-is('ZIP code')")

    condition = json_state(page)["filters"]["children"][0]
    assert condition["field"] == "invoice_address.zipcode"
    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_browser_base_switch_explains_the_loss_before_applying_it(editor_in_browser):
    """F3: the user sees what a base switch costs before it happens."""
    page, errors = editor_in_browser

    page.fill("#pcr-json", json.dumps(load_fixture("orderposition_basic")))
    page.click("#pcr-json-apply")
    page.wait_for_function(
        "() => document.querySelectorAll('#pcr-columns tr').length === 20"
    )

    page.click("#pcr-base-order")
    impact = page.locator("#pcr-base-impact")
    impact.wait_for(state="visible")
    text = impact.text_content()
    # Several fields are affected, and the dialogue names them.
    assert "position.positionid" in text
    assert "seat.zone_name" in text
    # Nothing has changed yet.
    assert json_state(page)["base"] == "orderposition"

    page.click("#pcr-base-impact button.btn-warning")
    page.wait_for_function(
        "() => JSON.parse(document.getElementById('pcr-json').value).base === 'order'"
    )
    switched = json_state(page)
    assert switched["base"] == "order"
    assert all(
        column.get("aggregate")
        for column in switched["columns"]
        if column["field"].startswith(("position.", "item.", "seat.", "voucher."))
    )
    # The sorting stage that cannot survive on the new base is gone.
    assert "position.positionid" not in [
        entry["field"] for entry in switched.get("sorting", [])
    ]
    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_browser_asks_before_leaving_with_unsaved_changes(
    browser_page, event, stored_report
):
    """Import, templates and the export download all leave the editor.

    None of them can see what is unsaved in this page: import and "load a
    template" replace it, and the export file is built from the stored row. So
    the editor asks -- and only when there is really something to lose.
    """
    report = stored_report(load_fixture("minimal_order"))
    page, errors = browser_page(
        url_for("editor.edit", event, identifier=report.identifier)
    )

    # Nothing changed yet: the link just follows, no dialogue.
    asked = []
    page.on("dialog", lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
    page.click("#pcr-templates")
    page.wait_for_load_state()
    assert asked == []
    assert "/reports/templates/" in page.url

    page.go_back()
    page.wait_for_selector("#pcr-library-list li[data-field-key]")

    # Now change something and try again: the editor asks, and a "no" keeps us
    # on the page with the change intact.
    library_action(page, "order.email", "Add as column")
    page.wait_for_function(
        "() => document.getElementById('pcr-columns-count').textContent === '2'"
    )
    page.click("#pcr-templates")
    page.wait_for_timeout(300)
    assert len(asked) == 1
    assert "unsaved" in asked[0]
    assert "/editor/" in page.url
    assert len(json_state(page)["columns"]) == 2

    # The export link warns about something else: its file is the saved version.
    page.click("#pcr-export")
    page.wait_for_timeout(300)
    assert len(asked) == 2
    assert "saved version" in asked[1]
    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_browser_live_preview_shows_real_rows(editor_in_browser, event_data):
    """The whole chain in one assertion: click -> preview endpoint -> table."""
    page, errors = editor_in_browser

    library_action(page, "order.code", "Add as column")
    page.wait_for_selector("#pcr-preview table td")
    codes = page.eval_on_selector_all(
        "#pcr-preview table tbody td", "els => els.map(e => e.textContent.trim())"
    )
    assert "FMT01" in codes
    # The test mode order is not in there: excluded by default (options).
    assert "TEST4" not in codes
    assert page.text_content("#pcr-preview-status").startswith("Showing 3 of 3 rows")
    assert errors == []


# ---------------------------------------------------------------------------
# Contract guards
# ---------------------------------------------------------------------------


def test_no_orm_path_can_reach_the_server_through_a_definition():
    """A smuggled ORM path must not survive structural validation.

    The editor only ever sends field keys; this asserts the other half -- that
    even a hand-crafted body cannot turn into a lookup, because a key containing
    ``__`` is rejected outright (contracts/fields.py).
    """
    with (FIXTURE_DIR / "invalid" / "smuggled_orm_path.json").open(
        "r", encoding="utf-8"
    ) as fp:
        smuggled = json.load(fp)
    with pytest.raises(DefinitionValidationError):
        validate_definition(smuggled)


def test_model_js_has_no_hardcoded_operator_table():
    """report-editor-model.js must not carry a copy of the operator table."""
    source = MODEL_JS.read_text(encoding="utf-8")
    for operator in Operator:
        if operator.value in ("in", "and", "or"):
            continue  # substrings of ordinary English words
        assert f'"{operator.value}"' not in source, operator.value


def test_static_assets_are_self_hosted():
    """No CDN, no external asset (SPEC.md section 4).

    Images and fonts are skipped, not because they may point outwards -- they
    cannot -- but because reading them as text would only produce a confusing
    failure.
    """
    text_suffixes = {".js", ".css", ".json", ".svg", ".html", ".txt", ""}
    checked = 0
    for path in sorted((PLUGIN_ROOT / "static").rglob("*")):
        if path.is_dir() or path.suffix.lower() not in text_suffixes:
            continue
        source = path.read_text(encoding="utf-8")
        checked += 1
        assert "//cdn." not in source, path
        assert "http://" not in source, path
        assert "https://" not in source, path
    assert checked >= 3  # two scripts and a stylesheet, at least


def test_no_stub_is_left_in_the_editor_views():
    """Wave 2: the editor must not import ``contracts.stubs`` any more.

    ADR 0001 section 6 asks that a stub in the production path be visible in the
    diff. This is the automated half of that promise.

    Checked on the parsed module rather than on the text, because the module
    docstrings still *mention* the superseded stubs -- deliberately, that is the
    history of the two swap points -- and a substring check would either forbid
    writing that down or have to be weakened until it stops meaning anything.
    Function-local imports are covered: ``ast.walk`` does not care how deeply
    nested a node is.
    """
    import ast

    for name in ("api.py", "editor.py"):
        source = (PLUGIN_ROOT / "views" / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module or ''}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        assert not [module for module in imported if "stubs" in module], (
            name,
            imported,
        )
        # The two factory functions of the stub module, in case someone imports
        # them under a different path.
        assert "stub_registry(" not in source, name
        assert "stub_compiler(" not in source, name


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------
#
# A user's verdict on the first version: "everything you need is there, but it
# is cramped and confusing, and the order does not really make sense". The fix
# is four separated blocks in the order the work happens -- what the report is
# called, what goes into it, how the result is arranged, what comes out -- plus a
# second save button at the bottom of a page that is long enough to make
# scrolling back up annoying.
#
# These tests assert on *order and separation*, not on styling: pixels are not
# testable from here, but "the preview is below the columns" and "there is a rule
# between the blocks" are.


#: The four blocks and, inside each of them, the elements that belong to it, in
#: the order the page is meant to read. Every entry is an id, because that is
#: what report-editor.js addresses too -- rename one and this list breaks
#: together with the JavaScript, which is the point.
LAYOUT_ORDER = [
    'id="pcr-section-basics"',
    'id="pcr-name"',
    'id="pcr-description"',
    'id="pcr-base"',
    'id="pcr-section-content"',
    'id="pcr-library"',
    'id="pcr-columns"',
    'id="pcr-filters"',
    'id="pcr-section-arrangement"',
    'id="pcr-sorting"',
    'id="pcr-opt-rowlimit"',
    'id="pcr-section-result"',
    'id="pcr-preview-panel"',
    'id="pcr-json"',
]


@pytest.mark.django_db
def test_editor_page_orders_its_four_blocks(client_with_perms, event):
    """Name/base, then content, then arrangement, then result."""
    content = client_with_perms.get(url_for("editor.new", event)).content.decode()
    positions = []
    for marker in LAYOUT_ORDER:
        assert marker in content, marker
        positions.append((marker, content.index(marker)))
    assert positions == sorted(positions, key=lambda entry: entry[1]), positions


@pytest.mark.django_db
def test_editor_page_separates_the_four_blocks(client_with_perms, event):
    """Three rules for four blocks, and a heading on each block."""
    content = client_with_perms.get(url_for("editor.new", event)).content.decode()
    assert content.count('class="pcr-section-divider"') == 3
    assert content.count('class="pcr-section-title"') == 4
    # The stylesheet has to carry the rules, otherwise the markup is decoration.
    css = (
        PLUGIN_ROOT / "static" / "pretix_custom_reports" / "css" / "report-editor.css"
    ).read_text(encoding="utf-8")
    assert ".pcr-section-divider" in css
    assert ".pcr-section-title" in css


@pytest.mark.django_db
def test_the_report_base_sits_in_the_first_block(client_with_perms, event):
    """Choosing the base is a decision about the whole report, not about a field.

    It used to sit on top of the field library, which is why it read like part of
    it. Both ids stay -- report-editor.js renders into ``#pcr-base-choices`` and
    unhides ``#pcr-base-impact`` -- only their place in the document changed.
    """
    content = client_with_perms.get(url_for("editor.new", event)).content.decode()
    assert 'id="pcr-base-choices"' in content
    assert 'id="pcr-base-impact"' in content
    assert content.index('id="pcr-base"') < content.index('id="pcr-library"')
    assert content.index('id="pcr-base"') < content.index('id="pcr-section-content"')


@pytest.mark.django_db
def test_editor_repeats_the_save_button_at_the_end_of_the_page(
    client_with_perms, event
):
    """A second button, one form, no nesting.

    ``form="pcr-form"`` is the HTML5 form owner attribute: the button submits the
    form up in the first block without being inside it, so the page keeps exactly
    one ``<form>`` of ours and report-editor.js keeps its single submit handler --
    which is what writes the hidden ``definition`` input before the POST goes out.
    """
    content = client_with_perms.get(url_for("editor.new", event)).content.decode()
    assert content.count('id="pcr-form"') == 1
    assert content.count('id="pcr-save"') == 1
    assert content.count('id="pcr-save-bottom"') == 1
    # The bottom button is outside the form and points back into it.
    assert content.index("</form>") < content.index('id="pcr-save-bottom"')
    assert 'form="pcr-form"' in content
    # ... and it is below the JSON panel, i.e. at the end of the page.
    assert content.index('id="pcr-json"') < content.index('id="pcr-save-bottom"')
    # A shared class, so anything that wants "the save buttons" gets both
    # without having to know that there are two.
    assert content.count("btn btn-primary pcr-save") == 2


@pytest.mark.django_db
def test_both_save_buttons_are_disabled_together(
    client_read_only, event, stored_report
):
    """Whatever disables one must disable the other, or the page lies twice."""
    report = stored_report(load_fixture("minimal_order"))
    content = client_read_only.get(
        url_for("editor.edit", event, identifier=report.identifier)
    ).content.decode()
    for marker in ('id="pcr-save"', 'id="pcr-save-bottom"'):
        assert "disabled" in content.split(marker)[1].split(">")[0], marker


# ---------------------------------------------------------------------------
# The template editor (organizer level)
# ---------------------------------------------------------------------------
#
# Same shell, same JavaScript, same JSON endpoints -- but an organizer-level
# report template has no event, and a field library only exists *for* an event:
# which questions, products and meta properties there are is event data. So the
# user picks a reference event and the editor talks to that event's
# api/fields/, api/preview/ and api/validate/. views/api.py is untouched.
#
# The critical assumption is that portability-dev's TemplateCreateView and
# TemplateUpdateView accept the editor's POST unchanged.
# test_template_editor_post_round_trip is the proof, and it is parametrised over
# every golden fixture for the same reason its event-level twin is.

#: Organizer-level change permission. Deliberately spelled out rather than
#: imported, for the same reason as CHANGE_PERMISSION above: if that string ever
#: moves, the users built here must stop matching it *visibly*.
ORGANIZER_CHANGE_PERMISSION = "organizer.settings.general:write"


def organizer_url_for(name, organizer, **kwargs):
    return reverse(
        f"{URL_NAMESPACE}:{name}",
        kwargs={"organizer": organizer.slug, **kwargs},
    )


def template_editor_url(organizer, reference_event=None, template=None):
    """The template editor's URL, with or without the reference event."""
    if template is None:
        url = organizer_url_for("organizer.templates.editor.new", organizer)
    else:
        url = organizer_url_for(
            "organizer.templates.editor.edit", organizer, template=template
        )
    if reference_event is not None:
        url += f"?reference_event={reference_event.slug}"
    return url


@pytest.fixture
def second_event(organizer):
    """A second event with the plugin on, so the picker has something to pick."""
    from pretix.base.models import Event

    with scopes_disabled():
        return Event.objects.create(
            organizer=organizer,
            name="Second Event",
            slug="second",
            date_from=now() + datetime.timedelta(days=60),
            plugins="pretix_custom_reports",
            live=True,
        )


@pytest.fixture
def stored_template(organizer):
    """Factory for a saved organizer template (``event=None``)."""

    def make(definition, name="Stored template", identifier=""):
        with scopes_disabled():
            return ReportDefinition.objects.create(
                organizer=organizer,
                name=name,
                identifier=identifier,
                definition=definition,
            )

    return make


@pytest.fixture
def user_organizer_only(organizer):
    """May change organizer settings, may not read orders in any event.

    ``all_events=False`` and no ``limit_events``: ``_get_teams_for_event``
    therefore finds no team for any event of this organizer, so every
    ``has_event_permission`` is False while the organizer-level check passes
    (verified in pretix/base/models/auth.py).
    """
    user = User.objects.create_user("organizer-only@example.org", PASSWORD)
    team = Team.objects.create(
        organizer=organizer,
        name="Organizer settings only",
        all_events=False,
        all_event_permissions=False,
        all_organizer_permissions=True,
    )
    team.members.add(user)
    return user


@pytest.fixture
def client_organizer_only(user_organizer_only):
    """Its own ``Client``, not pytest-django's shared one.

    ``client`` is a single object per test: a fixture that logs in on it replaces
    whoever was logged in before, so a test that wants two users at once (the
    preview gate below) must not build the second one on top of the first.
    """
    own = Client()
    assert own.login(email=user_organizer_only.email, password=PASSWORD)
    return own


@pytest.mark.django_db
def test_template_editor_points_at_the_reference_events_endpoints(
    client_with_perms, organizer, event
):
    """(a) The page loads, and every JSON endpoint belongs to the reference event.

    That is the whole trick: no new API and no organizer-level field registry.
    The editor's config carries the *event* URLs, which are gated on
    ``event.orders:read`` and on the plugin being active there.
    """
    resp = client_with_perms.get(template_editor_url(organizer, reference_event=event))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'id="pcr-editor"' in content
    assert 'id="pcr-config"' in content

    config = editor_config(content)
    assert config["urls"]["fields"] == url_for("api.fields", event)
    assert config["urls"]["preview"] == url_for("api.preview", event)
    assert config["urls"]["validate"] == url_for("api.validate", event)
    assert config["schema_version"]

    # Template mode is visible: the title, the reference-event hint and the note
    # that the preview shows that event's real orders.
    assert "Template editor" in content
    assert "Report editor" not in content
    assert "Reference event:" in content
    assert event.name in content
    assert 'id="pcr-preview-note"' in content

    # A new template posts to portability-dev's create view.
    assert (
        f'action="{organizer_url_for("organizer.templates.add", organizer)}"' in content
    )
    assert "disabled" not in content.split('id="pcr-save"')[1].split(">")[0]


@pytest.mark.django_db
def test_template_editor_renders_no_raw_django_comment(
    client_with_perms, organizer, event
):
    """The same lexer trap as on the event page, on both new code paths."""
    for url in (
        template_editor_url(organizer, reference_event=event),
        template_editor_url(organizer),  # auto-selected, so also the editor
    ):
        content = client_with_perms.get(url).content.decode()
        assert "{#" not in content
        assert "#}" not in content
        assert "Pick the reference event for the template editor" not in content
        assert "The reference-event hint lives inside" not in content


@pytest.mark.django_db
def test_template_editor_picks_the_only_usable_event_by_itself(
    client_with_perms, organizer, event, event_without_plugin
):
    """One candidate is not a choice, so do not ask.

    ``event_without_plugin`` is not a candidate: the API routes answer 404 for an
    event that has the plugin switched off, so offering it would mean offering a
    broken editor.
    """
    resp = client_with_perms.get(template_editor_url(organizer))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'id="pcr-editor"' in content
    assert 'id="pcr-reference-event"' not in content
    assert editor_config(content)["urls"]["fields"] == url_for("api.fields", event)
    # With nothing to choose from, the "use a different event" link would lead
    # straight back to this page.
    assert 'id="pcr-choose-event"' not in content


@pytest.mark.django_db
def test_template_editor_asks_which_event_when_there_are_several(
    client_with_perms, organizer, event, second_event, event_without_plugin
):
    """(b) No reference event and more than one candidate: the in-between page."""
    resp = client_with_perms.get(template_editor_url(organizer))
    assert resp.status_code == 200
    content = resp.content.decode()

    assert 'id="pcr-editor"' not in content
    assert 'id="pcr-reference-event"' in content
    assert 'method="get"' in content
    assert 'name="reference_event"' in content
    assert f'value="{event.slug}"' in content
    assert f'value="{second_event.slug}"' in content
    # The event without the plugin is not on offer.
    assert f'value="{event_without_plugin.slug}"' not in content

    # And the answer really opens the editor for that event.
    resp = client_with_perms.get(
        template_editor_url(organizer, reference_event=second_event)
    )
    assert resp.status_code == 200
    assert editor_config(resp.content.decode())["urls"]["fields"] == url_for(
        "api.fields", second_event
    )


@pytest.mark.django_db
def test_template_editor_offers_a_way_back_to_the_event_picker(
    client_with_perms, organizer, event, second_event
):
    """With several candidates the choice must be revisable -- and guarded.

    The link leaves the page, so it carries ``data-pcr-leave="page"`` like the
    import and template links do; report-editor.js asks before throwing unsaved
    changes away. It has to sit *inside* ``#pcr-editor``, because that is where
    the guard looks (``#pcr-editor a[data-pcr-leave]``).
    """
    content = client_with_perms.get(
        template_editor_url(organizer, reference_event=event)
    ).content.decode()
    assert 'id="pcr-choose-event"' in content
    before, after = content.split('id="pcr-choose-event"', 1)
    assert 'data-pcr-leave="page"' in after[:200]
    # The link goes back to the picker, not to the page it is on.
    assert "reference_event" not in before.rsplit("<a ", 1)[1]
    assert content.index('id="pcr-editor"') < content.index('id="pcr-choose-event"')


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["does-not-exist", "plain", "dummy%20"])
def test_template_editor_handles_an_unusable_reference_event(
    client_with_perms, organizer, event, second_event, event_without_plugin, slug
):
    """(c) Not a 500: an unusable slug means "please choose".

    ``plain`` is ``event_without_plugin`` -- it exists and this user may read it,
    but the plugin is off there. The three cases (unknown, plugin off, no
    permission) are deliberately not told apart in the answer: they mean the same
    thing to the user, and distinguishing them would leak which slugs exist.
    """
    resp = client_with_perms.get(
        template_editor_url(organizer) + f"?reference_event={slug}"
    )
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'id="pcr-editor"' not in content
    assert 'id="pcr-reference-event"' in content
    assert "cannot be used as a reference" in content


@pytest.mark.django_db
def test_template_editor_ignores_an_event_of_another_organizer(
    client_with_perms, organizer, event
):
    """CLAUDE.md rule 4: the candidates come from this organizer, full stop."""
    from pretix.base.models import Event, Organizer

    other_org = Organizer.objects.create(name="Other", slug="other-org")
    with scopes_disabled():
        foreign = Event.objects.create(
            organizer=other_org,
            name="Foreign",
            slug="foreign",
            date_from=now() + datetime.timedelta(days=30),
            plugins="pretix_custom_reports",
            live=True,
        )
    resp = client_with_perms.get(
        template_editor_url(organizer) + f"?reference_event={foreign.slug}"
    )
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'id="pcr-editor"' not in content
    assert "cannot be used as a reference" in content


@pytest.mark.django_db
def test_template_editor_says_so_when_no_event_can_be_used(
    client_organizer_only, user_organizer_only, organizer, event
):
    """An organizer admin without order access gets a message, not a 403 storm.

    The plugin *is* active in the organizer, so ``OrganizerPluginActiveMixin``
    lets the request through; this user simply cannot read orders anywhere, and
    every request the editor would then make would answer 403.
    """
    assert user_organizer_only.has_organizer_permission(
        organizer, ORGANIZER_CHANGE_PERMISSION
    )
    assert not user_organizer_only.has_event_permission(
        organizer, event, VIEW_PERMISSION
    )
    resp = client_organizer_only.get(template_editor_url(organizer))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'id="pcr-editor"' not in content
    assert 'id="pcr-reference-event"' not in content
    assert "There is no event you could use as a reference" in content


@pytest.mark.django_db
def test_template_editor_needs_the_organizer_change_permission(
    client_without_perms, organizer, event
):
    """Reading orders in an event says nothing about organizer templates."""
    resp = client_without_perms.get(
        template_editor_url(organizer, reference_event=event)
    )
    assert resp.status_code == 403


@pytest.mark.django_db
def test_template_editor_requires_login(client, organizer, event):
    resp = client.get(template_editor_url(organizer, reference_event=event))
    assert resp.status_code == 302
    assert "/control/login" in resp["Location"]


@pytest.mark.django_db
def test_template_editor_opens_a_stored_template(
    client_with_perms, organizer, event, stored_template
):
    """The edit route, its save target and the identifier that must survive."""
    template = stored_template(load_fixture("wide_order"), name="Wide template")
    resp = client_with_perms.get(
        template_editor_url(organizer, reference_event=event, template=template.pk)
    )
    assert resp.status_code == 200
    content = resp.content.decode()

    assert (
        editor_config(content)["initial"]
        == validate_definition(load_fixture("wide_order")).as_dict()
    )
    assert 'value="Wide template"' in content
    assert (
        'action="'
        + organizer_url_for("organizer.templates.edit", organizer, template=template.pk)
        + '"'
    ) in content
    assert f'name="identifier" value="{template.identifier}"' in content


@pytest.mark.django_db
def test_template_editor_404s_for_a_report_that_is_not_a_template(
    client_with_perms, organizer, event, stored_report
):
    """An event-level report must not be reachable through the organizer route.

    ``templates_for_organizer`` is ``organizer=<this one>`` **and**
    ``event IS NULL``, so this is structural rather than a filter someone has to
    remember (CLAUDE.md rule 4).
    """
    report = stored_report(load_fixture("minimal_order"))
    resp = client_with_perms.get(
        template_editor_url(organizer, reference_event=event, template=report.pk)
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_template_editor_404s_for_an_unknown_template(
    client_with_perms, organizer, event
):
    """And the 404 comes before the event picker, not after it."""
    assert (
        client_with_perms.get(
            template_editor_url(organizer, template=999999)
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_template_editor_offers_export_only(
    client_with_perms, organizer, event, stored_template
):
    """(e) No import, and above all no "load a template".

    There is no organizer-level file import (``views/portability.py`` is event
    level), and loading a template into a template is meaningless -- a template
    is the thing that gets loaded. Export exists and needs a stored row.
    """
    template = stored_template(load_fixture("minimal_order"))
    content = client_with_perms.get(
        template_editor_url(organizer, reference_event=event, template=template.pk)
    ).content.decode()

    assert 'id="pcr-export"' in content
    assert (
        organizer_url_for("organizer.templates.export", organizer, template=template.pk)
        in content
    )
    assert 'data-pcr-leave="export"' in content

    assert 'id="pcr-import"' not in content
    assert 'id="pcr-templates"' not in content
    assert "Load a template" not in content
    assert "Import from a file" not in content
    assert url_for("event.reports.templates", event) not in content
    assert url_for("event.reports.import", event) not in content

    # And following the link really produces the file.
    resp = client_with_perms.get(
        organizer_url_for("organizer.templates.export", organizer, template=template.pk)
    )
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/json"


@pytest.mark.django_db
def test_an_unsaved_template_cannot_be_exported_yet(
    client_with_perms, organizer, event
):
    content = client_with_perms.get(
        template_editor_url(organizer, reference_event=event)
    ).content.decode()
    assert 'id="pcr-export"' not in content
    assert "Save this template to be able to export it as a file." in content


@pytest.mark.django_db
@pytest.mark.parametrize("slug", FIXTURE_SLUGS)
def test_template_editor_post_round_trip(client_with_perms, organizer, event, slug):
    """(d) The proof that ``ReportDefinitionForm`` takes the editor's POST as is.

    Editor form -> portability-dev's TemplateCreateView -> database -> editor
    page, with the definition unchanged. This is the one assumption the whole
    feature rests on -- that no change to ``views/templates.py`` or ``forms.py``
    is needed -- so it is checked against every golden fixture, exactly like its
    event-level twin.
    """
    raw = load_fixture(slug)
    canonical = validate_definition(raw)

    resp = client_with_perms.post(
        organizer_url_for("organizer.templates.add", organizer),
        data={
            "name": slug,
            "description": "from the editor",
            # A new template posts an empty identifier and lets the model mint
            # one, exactly as the editor's markup does.
            "identifier": "",
            "base": canonical.base.value,
            # Exactly what the hidden input carries: the canonical document as a
            # JSON string.
            "definition": canonical.as_json(),
        },
    )
    assert resp.status_code == 302, (
        resp.context["form"].errors if resp.context and "form" in resp.context else ""
    )

    with scopes_disabled():
        stored = ReportDefinition.objects.templates_for_organizer(organizer).get(
            name=slug
        )
    assert stored.event_id is None
    assert stored.organizer_id == organizer.pk
    assert stored.definition == canonical.as_dict()

    content = client_with_perms.get(
        template_editor_url(organizer, reference_event=event, template=stored.pk)
    ).content.decode()
    assert editor_config(content)["initial"] == canonical.as_dict()

    # ... and saving again keeps the identifier, which is what "load this
    # template" and every scheduled export of a copy refer to.
    identifier = stored.identifier
    assert f'name="identifier" value="{identifier}"' in content
    resp = client_with_perms.post(
        organizer_url_for("organizer.templates.edit", organizer, template=stored.pk),
        data={
            "name": f"{slug} renamed",
            "description": "",
            "identifier": identifier,
            "base": canonical.base.value,
            "definition": canonical.as_json(),
        },
    )
    assert resp.status_code == 302
    with scopes_disabled():
        stored.refresh_from_db()
    assert stored.name == f"{slug} renamed"
    assert stored.identifier == identifier
    assert stored.definition == canonical.as_dict()


@pytest.mark.django_db
def test_template_editor_preview_stays_gated_on_the_reference_event(
    client_with_perms, client_organizer_only, organizer, event, event_data
):
    """The preview shows real order data, so it stays gated on the event.

    Two halves of one statement: the endpoint the template editor points at
    really works for a user who may read that event's orders, and it really
    refuses a user who may not -- even though that second user may change this
    organizer's templates. An unguarded preview would be a data leak
    (SPEC.md section 4).
    """
    url = url_for("api.preview", event)
    payload = post_json(
        client_with_perms, url, {"definition": load_fixture("minimal_order")}
    ).json()
    assert payload["ok"] is True
    assert payload["limit"] <= PREVIEW_ROW_LIMIT

    # 404, not 403, and that is pretix' choice rather than ours: for an event URL
    # whose user holds no permission at all in that event, ControlMiddleware
    # raises Http404 before any view runs ("The selected event was not found or
    # you have no permission ...", pretix/control/middleware.py). Either answer
    # is a refusal; the assertion allows both so that a future pretix release
    # switching between them does not read as a hole in our gate.
    refused = post_json(
        client_organizer_only, url, {"definition": load_fixture("minimal_order")}
    )
    assert refused.status_code in (403, 404)
