"""Compiler: report definition -> queryset (columns, filters, sorting).

Owner from wave 1 on: query-dev (ORCHESTRIERUNG.md section 5).

This file stays free of imports, as ADR 0000 section 9 asks of every package
``__init__``. The public entry point is therefore one level down::

    from pretix_custom_reports.query.compiler import ReportQueryCompiler

    report = ReportQueryCompiler(registry).compile(definition, event)

Reading order
-------------

1. ``compiler.py``  -- what a consumer calls, and which errors come out
2. ``plan.py``      -- pass one: resolve every key through the registry,
                       validate against the resolved fields, plan the queryset
3. ``report.py``    -- pass two: apply the plan, stream rows, count cheaply
4. ``filters.py``   -- operator + datatype -> ``Q()``, one AND/OR level
5. ``dates.py``     -- relative date filters in the *event's* timezone
6. ``columns.py``   -- renderers, aggregates, the joins a column needs
7. ``relations.py`` -- the only module that names concrete pretix models
8. ``values.py``    -- per-datatype coercion of filter values

The one rule everything here follows: ``orm_path``, lookups and operators come
out of a ``ReportField`` supplied by the registry, never out of the definition
(CLAUDE.md rule 2). ``plan.py`` is where that is enforced, and it builds no
queryset at all -- so an unknown key provably cannot reach the ORM.
"""
