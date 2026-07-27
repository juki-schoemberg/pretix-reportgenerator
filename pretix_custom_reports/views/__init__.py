"""View package for pretix-custom-reports.

Created in wave 0a by bootstrap-dev so that the three agents who add
modules here in waves 1 and 2 do not each create this file in parallel.

**Keep this file free of imports.** Modules are imported directly by
``urls.py`` (for example ``from .views.crud import ...``). Re-exporting
here would turn this file into a shared write target again, which is
exactly the collision it exists to prevent.

Module ownership (ORCHESTRIERUNG.md section 5):

- ``crud.py`` -- persistence-dev
- ``editor.py`` -- frontend-dev
- ``api.py`` -- frontend-dev
- ``portability.py`` -- portability-dev
- ``templates.py`` -- portability-dev
- ``placeholder.py`` -- bootstrap-dev (walking skeleton, frontend-dev may delete it)
"""
