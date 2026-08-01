"""Import/export of report definitions and organizer template resolution.

Owner from wave 2 on: portability-dev (ORCHESTRIERUNG.md section 5).

Created empty in wave 0a. Keep this file free of imports (ADR 0000 section 9
bullet 3); the modules are imported directly::

    from pretix_custom_reports.portability.importer import plan_import

Reading order, which is also the order the data flows in:

1. :mod:`~pretix_custom_reports.portability.payload` -- untrusted bytes to a
   plain ``dict``, and every refusal that happens before anybody interprets
   anything
2. :mod:`~pretix_custom_reports.portability.envelope` -- the file format:
   metadata around a definition, in both directions
3. :mod:`~pretix_custom_reports.portability.references` -- the name hints that
   make matching a renamed question possible at all
4. :mod:`~pretix_custom_reports.portability.resolution` -- **the** resolution
   layer: one definition, one target event, one report. Used by the file
   import, by "load organizer template" and by the event copy
5. :mod:`~pretix_custom_reports.portability.importer` -- plan (writes nothing)
   and commit (writes one row)
6. :mod:`~pretix_custom_reports.portability.templating` -- organizer templates:
   permissions on both ends, and the copy into an event
7. :mod:`~pretix_custom_reports.portability.eventcopy` -- the ``event_copy_data``
   logic; the receiver itself belongs to the integrator
8. :mod:`~pretix_custom_reports.portability.errors` -- what any of it raises

The one rule this package exists to enforce: an imported file may contribute
*which* fields a report uses, never *how* they are queried. ORM paths, lookups,
operators and aggregates come from the ``ReportField`` objects of the **target**
event's registry (CLAUDE.md rule 2). A file that contains an ORM path is
rejected by the structural validator -- it is not cleaned up and used anyway.
"""
