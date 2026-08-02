pretix Custom Reports
=====================

.. image:: https://img.shields.io/badge/status-pre--alpha-orange
   :alt: Status: pre-alpha

Plugin for `pretix`_ that lets organizers build their own reports on orders and
order positions through a graphical editor — no SQL, no free-text ORM paths.
Reports are saved per event, can be scheduled through pretix' own scheduled
exports, exchanged as JSON files and shared as organizer-level templates.

.. contents::
   :local:
   :depth: 1


Screenshots
-----------

*Placeholders — replace with real screenshots before the first release.*

.. figure:: docs/img/screenshot-editor.png
   :alt: The report editor with the field library, the column list and the live preview

   **The report editor.** Field library on the left, columns, filters and
   sorting in the middle, live preview underneath.

.. figure:: docs/img/screenshot-report-list.png
   :alt: The list of saved reports of one event

   **Saved reports of an event**, with import, export and "load a template".

.. figure:: docs/img/screenshot-scheduled-export.png
   :alt: pretix' scheduled export dialog with "Custom report" selected

   **Scheduling.** A saved report is a regular pretix export, so it appears in
   the scheduled export dialog of the event and of the organizer.


Features
--------

**Report editor**
  Click a report together: pick fields from a searchable, grouped field
  library, drag columns into order, override column titles, choose a display
  format per data type, and hide columns that are only needed for filtering.
  A server-rendered live preview shows real rows while you build.

**Two report bases**
  Every report is either ``order`` (one row per order) or ``orderposition``
  (one row per position). Position fields stay available on an order-based
  report as an aggregate (``count``, ``count distinct``, ``sum``, ``min``,
  ``max``, ``avg``, ``join``) rather than disappearing.

**Field coverage**
  Order, order position, invoice address, product, variation, date (subevent),
  seat, voucher, discount, aggregated payments/refunds, aggregated check-ins,
  event meta properties — plus every **question** of the event as a dynamic
  field, and a few calculated fields (outstanding amount, payment status as
  text, age at the event date).

**Filters**
  Per field, with operators that depend on the data type, combined with AND/OR
  and one level of grouping. Date fields additionally offer relative operators
  (today, last N days, next N days, current month, current year, since the
  event started) so a scheduled report keeps returning a current result.

**Multi-stage sorting**
  An ordered list of (field, direction), drag-and-drop reorderable, restricted
  to fields the registry marks as sortable.

**Scheduled exports**
  See below. No scheduler of our own.

**Import and export of definitions**
  A report is exportable as a JSON file with schema version and metadata, and
  importable by file upload or by pasting the JSON. On import every field key
  is resolved against the registry of the target event and the result is shown
  before anything is written — unknown references are never swallowed silently
  and never turn into an unchecked ORM path.

**Organizer-level templates**
  Report definitions kept on the organizer. "Load a template" in an event
  creates an editable copy; event-specific references (questions, products) go
  through the same resolution step as a file import.

**Event copy**
  Copying an event takes its reports along, including the resolution of renamed
  questions.

**Extensible by other plugins**
  Other plugins contribute their own columns through the
  ``register_report_fields`` signal, see `docs/extending.md`_.


Installation
------------

From PyPI (once released)::

    pip install pretix-custom-reports

From a checkout::

    pip install -e .

Then restart the pretix web and Celery processes — newly registered entry
points are not picked up by a running autoreloader — and activate the plugin
per event under *Settings → Plugins → Output and export formats*.

Permissions:

====================================  ==========================================
``event.orders:read``                 see the report list, open the editor, run
                                      the preview, export a report as a file
``event.settings.general:write``      create, change, duplicate, delete, import,
                                      load a template
``organizer.settings.general:write``  manage the organizer-level templates
====================================  ==========================================

The plugin has no settings of its own and adds one database table
(``ReportDefinition``).


Compatibility
-------------

============  ==================================================
pretix        ``2026.6.0`` (pinned exactly, see below)
Python        >= 3.11 (developed and tested on 3.12.6)
Django        whatever the pinned pretix ships (5.2 for 2026.6.0)
Database      SQLite (tested), PostgreSQL (**not yet verified**,
              see *Known limitations*)
Formats       XLSX, CSV (comma), CSV (Excel-style), CSV (semicolon)
Dependencies  none beyond pretix itself
============  ==================================================

``PretixPluginMeta.compatibility`` is pinned to ``pretix==2026.6.0``. pretix
enforces this at app-config time and calls ``sys.exit(1)`` on a mismatch, which
takes the whole server down — so the pin is deliberate and must be widened
consciously (see ``docs/adr/0000-setup.md``).

ODS is deliberately missing: pretix' ``ListExporter`` in 2026.6.0 knows XLSX
and three CSV dialects and nothing else, and hand-rolling a serialiser is
forbidden by the project rules.


Scheduled exports
-----------------

The plugin **does not ship a scheduler**. A saved report is registered as a
regular pretix exporter (``identifier: customreports``, category *Custom
reports*), and pretix' own scheduled exports take it from there — on event
level (*Orders → Export → Scheduled exports*) and on organizer level.

What that means in practice:

* The scheduled export stores the report's **stable identifier**, not its
  primary key. Renaming the report is fine; changing the identifier or deleting
  the report makes the scheduled export fail with a readable message instead of
  an "Internal Error" mail.
* Relative date filters ("last 30 days") are evaluated **when the export runs**,
  which is what makes a recurring report useful.
* On organizer level the same identifier is resolved **per event**, so one
  scheduled export can cover many events. Events that do not have the report
  can be skipped or can fail the whole export — that is a form field.
* Two run-time overrides (canceled positions, test mode orders) and a row limit
  can be set per export run without touching the saved report.
* pretix caps a scheduled export at 20 MB. A large report needs a row limit to
  arrive at all.
* The organizer-level exporter is offered for **every** organizer, because this
  is an event-level plugin using the legacy path of an organizer-level signal.
  Events that do not have the plugin enabled are filtered out, so an organizer
  who never enabled it simply sees an empty report list.


Extending
---------

Other plugins can contribute columns through an ``EventPluginSignal``. The full
documentation with a runnable example plugin is in `docs/extending.md`_.


Known limitations
-----------------

Open findings from the security review (``docs/security-review.md``) and the
integration/load tests (``handoff/blockers.md``). None of them blocks normal
use; they are listed here so nobody has to rediscover them.

**Behaviour**

* **Column formats only apply to the preview, not to the export** (T-001,
  medium). ``date_style``, ``number_style`` and ``boolean_style`` are honoured
  by the live preview but not by the exported file — set "date only" and the
  file still contains the full timestamp. Only ``separator`` (for ``join``
  columns) works on both paths. Needs an architecture decision about where
  rendering belongs, not just a patch.
* **Aggregated money columns can lose their decimal places on SQLite** (T-002,
  medium). ``23.50`` from a model column, ``20.5`` from an aggregate, in the
  same CSV row. PostgreSQL is expected to behave differently — which is the
  actual problem: the same report may produce two different files on two
  installations.
* **A ``join`` column costs one extra prefetch per chunk of 1000 rows** (T-003,
  low/medium), not one per report. That is the price of streaming with constant
  memory, but the docstring in ``query/columns.py`` promises otherwise. 49 000
  rows with two ``join`` columns: 151 queries instead of the promised 4.
* **Malformed JSON can produce a 500 instead of a form error** (S-003, medium).
  Unpaired UTF-16 surrogates in an imported definition reach three endpoints
  unguarded and can be persisted through import.
* **A duplicate identifier raises an IntegrityError instead of a form error**
  (S-004, low), when two users save the same identifier at the same time.
* **The preview runs one query per ``join`` column** (S-005, low).
* **``strategy=keep`` can be chosen by POST** on import (S-006, low), which
  skips one validation step. It cannot produce an unchecked ORM path.

**Environment**

* **PostgreSQL is not verified.** Everything was developed and tested on
  SQLite. Four areas are at risk: output types of ``Coalesce``/``Subquery`` on
  money and count aggregates, ``nulls_last`` in both sort directions,
  ``Cast(answer AS date)`` in the calculated age fields (on PostgreSQL a single
  broken row fails the *whole* query, on SQLite it does not), and ``Case``
  expressions over annotation aliases.
* **The German catalog needs ``gettext``.** ``.mo`` files are built at package
  build time (``pretix-plugin-build``). Without ``msgfmt`` on the build machine
  the UI stays English.
* **One string collides in translation:** ``"Date"`` is both the label of the
  ``DATE`` data type and the label of the subevent group/field. German needs
  "Datum" for the first and "Termin" for the second. Until one of the two gets
  a ``pgettext_lazy`` context, both read "Datum".


Development setup
-----------------

The plugin lives next to a pretix checkout inside the same virtualenv. Full
description of the reference environment: ``ENVIRONMENT.md``, first-time setup:
``SETUP.md``.

1. Set up pretix as usual (``../pretix``), activate the virtualenv.
2. Register the plugin::

    pip install -e .

3. Restart the pretix development server.
4. Activate the plugin per event under
   *Settings → Plugins → Output and export formats*.

Quality gates::

    pytest -m "not performance"
    pytest -m performance
    flake8 . && isort -c . && black --check . && docformatter --check .
    python -m pretix makemigrations pretix_custom_reports --check --dry-run

Translations require ``gettext``::

    make


Documentation
-------------

===============================  =============================================
``SPEC.md``                      what the plugin is supposed to do
``docs/extending.md``            the field signal for third-party plugins
``docs/adr/``                    architecture decisions
``docs/pretix-api-notes.md``     the verified pretix API reference this was
                                 built against
``docs/security-review.md``      adversarial review, findings and proofs
``docs/performance.md``          load test results and memory profile
``ENVIRONMENT.md``               the reference development environment
``SETUP.md``                     first-time setup of that environment
``ORCHESTRIERUNG.md``            how this repository was built
===============================  =============================================


License
-------

Apache License 2.0, see ``LICENSE``.

.. _pretix: https://pretix.eu/
.. _docs/extending.md: docs/extending.md
