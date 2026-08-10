# Owner from wave 1 on: integrator (see ORCHESTRIERUNG.md section 5)
#
# Every agent needs routes in here, therefore this file is deliberately serial.
# Other agents put their copy-ready lines into handoff/requests/.
#
# Verified against pretix 2026.6.0: ``urlpatterns`` from a plugin is included at
# the URL *root*, not below /control/ (pretix/multidomain/maindomain_urlconf.py).
# Control panel routes therefore have to spell out the full prefix, exactly like
# pretix/plugins/webcheckin/urls.py does.
#
# The route *lists* live next to the views they point at, so that no route is
# maintained twice and every agent stays the single owner of their own URLs.
# This module only concatenates them. The prefixes do not overlap; the order is
# therefore irrelevant and follows the wave order for readability.
from django.urls import re_path

from .views.api import api_urlpatterns
from .views.crud import ReportListView, event_urlpatterns
from .views.editor import editor_urlpatterns, template_editor_urlpatterns
from .views.portability import portability_event_urlpatterns
from .views.templates import (
    templates_event_urlpatterns,
    templates_organizer_urlpatterns,
)

#: The event-level entry point behind the "Reports" navigation entry and behind
#: ``PretixPluginMeta.navigation_links``. Wave 4 decision (integrator): this
#: points at the real report list instead of the wave-0a placeholder, so the
#: menu entry leads somewhere useful. ``event.reports`` stays as the canonical
#: name -- ``views/crud.py`` reverses it in ``get_success_url()`` and the
#: templates link to it -- so both URLs render the same list. See
#: handoff/status/integrator.md.
urlpatterns = [
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports/$",
        ReportListView.as_view(),
        name="event.index",
    ),
] + (
    event_urlpatterns  # persistence-dev: CRUD (5)
    + editor_urlpatterns  # frontend-dev: editor shell (2)
    + api_urlpatterns  # frontend-dev: JSON endpoints (3)
    + portability_event_urlpatterns  # portability-dev: file import/export (2)
    + templates_event_urlpatterns  # portability-dev: use a template (2)
    + templates_organizer_urlpatterns  # portability-dev: manage templates (5)
    + template_editor_urlpatterns  # frontend-dev: template editor (2)
)
