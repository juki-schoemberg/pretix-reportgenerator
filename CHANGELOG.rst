Changelog
=========

This project follows `Semantic Versioning <https://semver.org/>`_. The version
lives in ``pretix_custom_reports/__init__.py``; ``pyproject.toml`` reads it from
there and ``PretixPluginMeta.version`` reports it to pretix.

0.1.0 (unreleased)
------------------

First feature-complete build.

Added
~~~~~

* **Report editor** with a searchable, grouped field library, drag-and-drop
  columns, per-column title, display format and visibility, and a
  server-rendered live preview.
* **Two report bases**, ``order`` and ``orderposition``. Position fields remain
  available on an order-based report as an aggregate.
* **Field registry** covering order, order position, invoice address, product,
  variation, subevent, seat, voucher, discount, aggregated payments/refunds,
  aggregated check-ins and event meta properties, plus every question of the
  event as a dynamic field and three calculated fields.
* **Filters** per field with type-dependent operators, AND/OR and one level of
  grouping, including relative date operators for recurring exports.
* **Multi-stage sorting** over fields the registry marks as sortable.
* **Scheduled exports** through pretix' own mechanism: the plugin registers a
  ``ListExporter`` on event and organizer level (``identifier: customreports``)
  and ships no scheduler of its own. The stored reference is the report's
  stable identifier, not its primary key.
* **Import and export of report definitions** as JSON, by file upload or by
  pasting, with a resolution report shown before anything is written.
* **Organizer-level templates** with "load a template" as an editable copy per
  event. Templates are built in the same graphical editor as reports, against a
  **reference event** that supplies the field library and the live preview; the
  choice of reference event is not stored with the template.
* **Event copy support**: reports travel with a copied event, including
  resolution of renamed questions and across organizers.
* **Third-party field signal** ``register_report_fields``, documented with a
  runnable example in ``docs/extending.md``.
* **German translation** (formal address, matching pretix' own ``de``
  catalog).

Security
~~~~~~~~

* Field access goes exclusively through the registry. No ORM path, lookup or
  operator ever comes from stored or imported JSON.
* Every queryset is hard-scoped to one event or to the events the user may
  see; organizer templates are scoped to their organizer.
* Every control-panel view of this plugin answers 404 when the plugin is
  switched off for the event, and the organizer export skips events that have
  it switched off.
* Full adversarial review with proof tests: ``docs/security-review.md``.

Known limitations
~~~~~~~~~~~~~~~~~

See the *Known limitations* section of ``README.rst``.
