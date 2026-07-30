# Owner: frontend-dev (ORCHESTRIERUNG.md section 5)
#
# Tests for the report editor: the page, its JSON endpoints, the permission and
# CSRF gates, the preview limit and -- the point of the whole exercise -- a full
# round trip of every golden fixture through the editor's own JavaScript model.
#
# Two things in here are unusual and deliberate:
#
# 1. The URLconf. urls.py belongs to the integrator (ORCHESTRIERUNG.md section
#    5), so the editor's routes are not wired up yet. They live next to their
#    views (api.api_urlpatterns, editor.editor_urlpatterns) and this module
#    injects them the same way pretix would: it appends them to the plugin's own
#    urlpatterns and reloads pretix.multidomain.maindomain_urlconf, which builds
#    the "plugins:<app_label>" namespace at import time. That means these tests
#    exercise the real namespace, the real prefix and the real middleware chain,
#    and they keep passing unchanged once the integrator adds the two lines from
#    handoff/requests/frontend-dev-an-integrator-urls.md.
# 2. The node subprocess. The editor's state <-> JSON mapping lives in
#    report-editor-model.js because that is where the browser needs it. Testing
#    it from Python would test a re-implementation, so the real file is executed
#    under node instead. Skipped when node is not installed.
"""Tests for the graphical report editor and its JSON endpoints."""

import importlib
import json
import pathlib
import pytest
import shutil
import subprocess
from django.test import Client
from django.urls import clear_url_caches, reverse

import pretix_custom_reports
from pretix_custom_reports.contracts import (
    OPERATOR_SPECS,
    PREVIEW_ROW_LIMIT,
    Base,
    DefinitionValidationError,
    Operator,
    validate_definition,
)
from pretix_custom_reports.signals import URL_NAMESPACE
from pretix_custom_reports.views.api import api_urlpatterns
from pretix_custom_reports.views.editor import editor_urlpatterns

from .conftest import PASSWORD

PLUGIN_ROOT = pathlib.Path(pretix_custom_reports.__file__).resolve().parent
MODEL_JS = (
    PLUGIN_ROOT / "static" / "pretix_custom_reports" / "js" / "report-editor-model.js"
)
FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "definitions"

#: Every valid golden fixture, by slug.
FIXTURE_SLUGS = sorted(
    p.stem for p in FIXTURE_DIR.glob("*.json") if not p.name.startswith("_")
)


# ---------------------------------------------------------------------------
# URL wiring
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def editor_routes():
    """Add the editor routes to the plugin's urlpatterns for this module.

    Exactly what ``urls.py`` will do; done here because that file has another
    owner. Reverting is important: other test modules must see the unmodified
    URLconf.
    """
    from pretix.multidomain import maindomain_urlconf

    import pretix_custom_reports.urls as plugin_urls

    original = list(plugin_urls.urlpatterns)
    plugin_urls.urlpatterns = (
        list(editor_urlpatterns) + list(api_urlpatterns) + original
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
def test_editor_page_opens_every_golden_fixture(client_with_perms, event, slug):
    """DoD: every golden fixture can be opened in the editor."""
    resp = client_with_perms.get(url_for("editor.edit", event, identifier=slug))
    assert resp.status_code == 200
    config = editor_config(resp.content.decode())
    assert config["initial"] == load_fixture(slug)
    assert config["urls"]["fields"]
    assert config["urls"]["preview"]
    assert config["i18n"]["issue_no_columns"]


@pytest.mark.django_db
def test_editor_page_unknown_identifier_is_404(client_with_perms, event):
    resp = client_with_perms.get(url_for("editor.edit", event, identifier="nope"))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Permissions, CSRF, methods -- the same for every endpoint
# ---------------------------------------------------------------------------


def api_endpoints(event):
    return {
        "fields": (url_for("api.fields", event), "get"),
        "validate": (url_for("api.validate", event), "post"),
        "preview": (url_for("api.preview", event), "post"),
        "examples": (url_for("api.examples", event), "get"),
        "example": (url_for("api.example", event, slug="minimal_order"), "get"),
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name", ["fields", "validate", "preview", "examples", "example"]
)
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
@pytest.mark.parametrize(
    "name", ["fields", "validate", "preview", "examples", "example"]
)
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
def library(client_with_perms, event):
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
def test_field_library_covers_every_key_the_fixtures_use(library):
    """The library must offer every key ``_index.json`` declares as required.

    In wave 1 this checks the stub feed; in wave 2, with the same assertion, it
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
def test_deprecated_fields_are_hidden(client_with_perms, event, monkeypatch):
    """A deprecated field still resolves for old reports but leaves the library."""
    from dataclasses import replace

    from pretix_custom_reports.contracts.stubs import StubFieldRegistry
    from pretix_custom_reports.views import api

    class WithDeprecated(StubFieldRegistry):
        def get_fields(self, event, base):
            fields = dict(super().get_fields(event, base))
            fields["order.code"] = replace(fields["order.code"], deprecated=True)
            return fields

    monkeypatch.setattr(api, "get_registry", lambda: WithDeprecated())
    payload = client_with_perms.get(url_for("api.fields", event)).json()
    assert "order.code" not in {field["key"] for field in payload["fields"]}
    assert "order.status" in {field["key"] for field in payload["fields"]}


# ---------------------------------------------------------------------------
# Round trip: fixture -> endpoint -> canonical JSON
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("slug", FIXTURE_SLUGS)
def test_validate_round_trips_every_fixture(client_with_perms, event, slug):
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
def test_preview_runs_for_every_fixture(client_with_perms, event, slug):
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
def test_preview_never_exceeds_the_row_limit(client_with_perms, event):
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
def test_preview_reports_the_estimated_total(client_with_perms, event):
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
def test_preview_drops_hidden_columns(client_with_perms, event):
    raw = load_fixture("wide_order")
    assert any(column.get("hidden") for column in raw["columns"])
    payload = post_json(
        client_with_perms, url_for("api.preview", event), {"definition": raw}
    ).json()
    assert "order.comment" not in [c["key"] for c in payload["columns"]]


@pytest.mark.django_db
def test_preview_applies_the_column_format(client_with_perms, event):
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
    }
    payload = post_json(
        client_with_perms, url_for("api.preview", event), {"definition": definition}
    ).json()
    row = payload["rows"][0]
    assert row[2] == "19.00"  # raw
    assert row[1] != row[2]  # currency formatting actually happened
    assert "19" in row[1]
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


@pytest.mark.django_db
def test_preview_survives_a_broken_field(client_with_perms, event, monkeypatch):
    """A field whose renderer explodes must not take the editor down with it."""
    from pretix_custom_reports.contracts.stubs import StubQueryCompiler
    from pretix_custom_reports.views import api

    class Exploding(StubQueryCompiler):
        def compile(self, definition, event=None):
            compiled = super().compile(definition, event)
            broken = [
                type(column)(
                    key=column.key,
                    label=column.label,
                    datatype=column.datatype,
                    render=lambda row: 1 / 0,
                    aggregate=column.aggregate,
                    field=column.field,
                )
                for column in compiled.columns
            ]
            compiled.columns = tuple(broken)
            return compiled

    monkeypatch.setattr(api, "get_compiler", lambda: Exploding())
    resp = post_json(
        client_with_perms,
        url_for("api.preview", event),
        {"definition": load_fixture("minimal_order")},
    )
    assert resp.status_code == 400
    assert resp.json()["stage"] == "execute"


@pytest.mark.django_db
def test_preview_escapes_cell_contents(client_with_perms, event, monkeypatch):
    """Order data ends up in HTML; it must never be able to become markup."""
    from pretix_custom_reports.contracts.stubs import StubQueryCompiler
    from pretix_custom_reports.views import api

    payload_string = "<script>alert('x')</script>"

    class Injecting(StubQueryCompiler):
        def compile(self, definition, event=None):
            compiled = super().compile(definition, event)
            compiled.columns = tuple(
                type(column)(
                    key=column.key,
                    label=column.label,
                    datatype=column.datatype,
                    render=lambda row: payload_string,
                    aggregate=column.aggregate,
                    field=column.field,
                )
                for column in compiled.columns
            )
            return compiled

    monkeypatch.setattr(api, "get_compiler", lambda: Injecting())
    result = post_json(
        client_with_perms,
        url_for("api.preview", event),
        {"definition": load_fixture("minimal_order")},
    ).json()
    assert result["rows"][0][0] == payload_string
    assert "<script>alert" not in result["html"]
    assert "&lt;script&gt;" in result["html"]


# ---------------------------------------------------------------------------
# Wave 1 example endpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_examples_endpoint_lists_every_fixture(client_with_perms, event):
    payload = client_with_perms.get(url_for("api.examples", event)).json()
    assert sorted(entry["slug"] for entry in payload["definitions"]) == FIXTURE_SLUGS
    assert all(entry["purpose"] for entry in payload["definitions"])


@pytest.mark.django_db
@pytest.mark.parametrize("slug", FIXTURE_SLUGS)
def test_example_endpoint_serves_the_fixture_verbatim(client_with_perms, event, slug):
    payload = client_with_perms.get(url_for("api.example", event, slug=slug)).json()
    raw = load_fixture(slug)
    assert payload["definition"] == raw
    assert payload["canonical"] == validate_definition(raw).as_dict()


@pytest.mark.django_db
def test_example_endpoint_rejects_unknown_slugs(client_with_perms, event):
    assert (
        client_with_perms.get(url_for("api.example", event, slug="wat")).status_code
        == 404
    )


@pytest.mark.django_db
def test_example_endpoint_cannot_be_talked_out_of_its_directory(
    client_with_perms, event
):
    """The slug is matched against a directory listing, not pasted into a path."""
    base = url_for("api.examples", event)
    for evil in ("..%2f..%2fsecret", "....//etc/passwd", "-index"):
        resp = client_with_perms.get(f"{base}{evil}/")
        assert resp.status_code == 404


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
    """Exactly the metadata the browser gets from GET api/fields/."""
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
