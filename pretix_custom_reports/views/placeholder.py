"""Walking skeleton view.

Created in wave 0a by bootstrap-dev to prove that entry point, app
config, navigation signal, URL routing and permission check are wired up
correctly.

frontend-dev replaces the target of
``plugins:pretix_custom_reports:event.index`` with the real report
list/editor in wave 1/2; this module can then be deleted. Until then it
must stay free of any business logic.
"""

from django.views.generic import TemplateView
from pretix.control.permissions import EventPermissionRequiredMixin

from ..signals import VIEW_PERMISSION


class EventIndexView(EventPermissionRequiredMixin, TemplateView):
    """Empty placeholder behind the event-level "Exports" menu entry."""

    permission = VIEW_PERMISSION
    template_name = "pretix_custom_reports/placeholder.html"
