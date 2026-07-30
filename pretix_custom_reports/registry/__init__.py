"""Field library: core fields, event questions, fields from third-party plugins.

Owner from wave 1 on: registry-dev (ORCHESTRIERUNG.md section 5).

This ``__init__`` stays **import-free** on purpose (ADR 0000 section 9 bullet 3).
Import the concrete module you need:

==========================================  ==================================
``registry.library``                        :class:`EventFieldRegistry`,
                                            :func:`field_registry` -- the entry
                                            point for everyone else
``registry.signals``                        ``register_report_fields``, the
                                            ``EventPluginSignal`` third-party
                                            plugins connect to (SPEC.md F5)
``registry.hints``                          the ``ReportField.extra`` keys and
                                            the aggregate helpers the query
                                            compiler needs
``registry.groups``                         UI group identifiers and labels
``registry.core``                           the hand-curated core field table
``registry.questions``                      dynamic ``answer.<identifier>``
                                            fields
``registry.meta``                           ``meta.event.<name>`` fields
``registry.computed``                       ``computed.*`` fields
``registry.annotations``                    the database expressions behind the
                                            annotated fields
``registry.cache``                          per-event cache and its
                                            invalidation receivers
==========================================  ==================================

Typical use::

    from pretix_custom_reports.contracts import Base
    from pretix_custom_reports.registry.library import field_registry

    fields = field_registry().get_fields(event, Base.ORDER)
    field = field_registry().resolve("order.code", event, Base.ORDER)

Three rules this package enforces and that callers may rely on:

1. **No registry without an event.** Every entry point requires a concrete
   ``Event``; there is no global field list and no cross-event lookup. Every
   queryset the registry builds is restricted to that one event.
2. **Hand-curated fields only.** Nothing is derived from ``Model._meta``. A
   field exists because somebody wrote it down in :mod:`registry.core`.
   Automatic introspection would publish internal columns (``secret``,
   ``internal_secret``) and arbitrary relation paths into a surface that later
   processes imported JSON.
3. **Core wins.** Keys in the reserved namespaces cannot be provided by a
   plugin; a plugin field whose key already exists is dropped, not merged.

See docs/adr/0002-registry.md for the reasoning, including the cache
invalidation strategy.
"""
