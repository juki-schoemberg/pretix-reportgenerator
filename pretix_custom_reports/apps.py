# Owner from wave 1 on: integrator (see ORCHESTRIERUNG.md section 5)
#
# Created in wave 0a by bootstrap-dev. Layout copied from
# pretix-plugin-cookiecutter (HEAD 9ef6054); the ``navigation_links`` format was
# verified against pretix/plugins/webcheckin/apps.py and
# pretix/control/views/event.py (``EventPlugins.prepare_links``).
from django.utils.translation import gettext_lazy as _

from . import __version__

try:
    from pretix.base.plugins import PluginConfig
except ImportError:
    raise RuntimeError("Please use pretix 2.7 or above to run this plugin!")


class PluginApp(PluginConfig):
    default = True
    name = "pretix_custom_reports"
    verbose_name = "Custom Reports"

    class PretixPluginMeta:
        name = _("Custom Reports")
        author = "Tobias Berndt"
        description = _(
            "Define your own reports on orders and order positions in a graphical "
            "editor, save them, reuse them, schedule them and share them as "
            "organizer-level templates."
        )
        visible = True
        version = __version__
        category = "FORMAT"
        # Pinned exactly on purpose: PluginConfig.__init__ calls sys.exit(1) on a
        # mismatch, see pretix/base/plugins.py. Widening this pin is a conscious
        # decision, see docs/adr/0000-setup.md.
        compatibility = "pretix==2026.6.0"
        settings_links = []
        # (label, urlname, extra kwargs) -- "organizer" and "event" are injected
        # by pretix, do not pass them here.
        navigation_links = [
            (_("Exports"), "plugins:pretix_custom_reports:event.index", {}),
        ]

    def ready(self):
        from . import signals  # NOQA
