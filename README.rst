pretix Custom Reports
=====================

.. image:: https://img.shields.io/badge/status-pre--alpha-orange
   :alt: Status: pre-alpha

Plugin for `pretix`_ that lets organizers build their own reports on orders and
order positions through a graphical editor — no SQL, no free-text ORM paths.

**Status: skeleton only.** This wave (0a) contains the package layout, the
tooling and a walking skeleton: the plugin installs, can be activated per event
and adds an "Exports" navigation entry that opens an empty placeholder page.
No reporting functionality exists yet. See ``SPEC.md`` for the target scope and
``ORCHESTRIERUNG.md`` for the build plan.

Compatibility
-------------

======================  ==================
pretix                  ``2026.6.0`` (pinned exactly, see below)
Python                  >= 3.11 (developed on 3.12.6)
======================  ==================

``PretixPluginMeta.compatibility`` is pinned to ``pretix==2026.6.0``. pretix
enforces this at app-config time and calls ``sys.exit(1)`` on a mismatch, which
takes the whole server down — so the pin is deliberate and must be widened
consciously (see ``docs/adr/0000-setup.md``).

Development setup
-----------------

The plugin lives next to a pretix checkout inside the same virtualenv. Full
description of the reference environment: ``ENVIRONMENT.md``.

1. Set up pretix as usual (``../pretix``), activate the virtualenv.
2. Register the plugin::

    pip install -e .

3. Restart the pretix development server. Newly registered entry points are not
   picked up by the autoreloader.
4. Activate the plugin per event under
   *Settings → Plugins → Output and export formats*.

Quality gates
-------------

::

    pytest
    pytest -m "not performance"
    flake8 . && isort -c . && black --check .

Translations require ``gettext``::

    make

License
-------

Apache License 2.0, see ``LICENSE``.

.. _pretix: https://pretix.eu/
