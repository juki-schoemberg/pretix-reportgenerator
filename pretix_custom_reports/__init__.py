# Owner from wave 1 on: integrator (see ORCHESTRIERUNG.md section 5)
#
# Keep this module import-free. setuptools reads ``__version__`` from here to
# resolve the dynamic project version, and the ``pretix.plugin`` entry point
# points at this module. pretix itself only evaluates the *module* part of the
# entry point (pretix/settings.py, ``entry_point.module``), the actual
# ``PretixPluginMeta`` lives in ``apps.py``.
__version__ = "0.1.0"
