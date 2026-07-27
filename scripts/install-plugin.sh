#!/usr/bin/env bash
# Register the plugin in the venv and compile its translations.
#
#   bash scripts/install-plugin.sh
#
# Run this once after the plugin skeleton exists (wave 0a) and again whenever
# setup.py / pyproject.toml or the entry points change. Code changes alone do
# NOT need a reinstall -- the editable install picks them up, the dev server
# reloads by itself.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

activate_venv

cd "$REPO_DIR"

if [ ! -f setup.py ] && [ ! -f pyproject.toml ]; then
  die "Neither setup.py nor pyproject.toml in $REPO_DIR.
      The plugin skeleton does not exist yet -- that is wave 0a (bootstrap-dev)."
fi

info "pip install -e . (in $VIRTUAL_ENV)"
python -m pip install -e .

if [ -f Makefile ]; then
  if command -v msgfmt >/dev/null 2>&1; then
    info "make (compiling translations)"
    make
  else
    printf '\033[33m!\033[0m msgfmt (gettext) is missing, skipping "make".\n'
    printf '    German strings stay untranslated. On Windows install gettext, e.g.:\n'
    printf '      winget install --id mlocati.GettextIconv\n'
    printf '    See ENVIRONMENT.md, section "Bekannte Stolpersteine".\n'
  fi
else
  printf '\033[33m!\033[0m No Makefile yet, skipping translation build.\n'
fi

info "installed plugins with a pretix entry point:"
python - <<'PY'
import importlib_metadata as md
found = False
for ep in md.entry_points(group="pretix.plugin"):
    print("    {} -> {}".format(ep.name, ep.value))
    found = True
if not found:
    print("    (none)")
PY

cat <<BANNER

  Next steps:

    1. Restart the dev server (Ctrl+C, then: bash scripts/start-dev.sh)
       Newly registered plugins are only picked up on a fresh start.
    2. Enable the plugin per event:
       http://localhost:8000/control/event/demo/demo-event/settings/plugins
       and the same for demo-serie.

BANNER
