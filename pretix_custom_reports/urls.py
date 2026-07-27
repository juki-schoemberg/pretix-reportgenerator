# Owner from wave 1 on: integrator (see ORCHESTRIERUNG.md section 5)
#
# Every agent needs routes in here, therefore this file is deliberately serial.
# Other agents put their copy-ready lines into handoff/requests/.
#
# Verified against pretix 2026.6.0: ``urlpatterns`` from a plugin is included at
# the URL *root*, not below /control/ (pretix/multidomain/maindomain_urlconf.py).
# Control panel routes therefore have to spell out the full prefix, exactly like
# pretix/plugins/webcheckin/urls.py does.
from django.urls import re_path

from .views.placeholder import EventIndexView

urlpatterns = [
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports/$",
        EventIndexView.as_view(),
        name="event.index",
    ),
]
