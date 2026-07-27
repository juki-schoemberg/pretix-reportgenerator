# Owner from wave 2 on: exporter-dev (see ORCHESTRIERUNG.md section 5)
#
# Deliberately empty. exporter-dev subclasses pretix.base.exporter.ListExporter
# here and registers it via register_data_exporters /
# register_multievent_data_exporters (the receivers go into signals.py, which is
# owned by the integrator -- request them via handoff/requests/).
#
# Hard rule from CLAUDE.md: no hand-rolled CSV/XLSX generation and no own
# scheduler; scheduling runs through pretix Scheduled Exports.
