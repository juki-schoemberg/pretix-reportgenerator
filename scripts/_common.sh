#!/usr/bin/env bash
# Shared bootstrap for the dev scripts. Source this, do not execute it.
#
# Layout it expects (one level above the plugin repo):
#   <work>/venv  <work>/pretix  <work>/data  <work>/pretix-custom-reports
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(cd "$REPO_DIR/.." && pwd)"
VENV_DIR="$WORK_DIR/venv"
PRETIX_DIR="$WORK_DIR/pretix"
PRETIX_SRC="$PRETIX_DIR/src"
DATA_DIR="$WORK_DIR/data"
export PRETIX_CONFIG_FILE="$PRETIX_SRC/pretix.cfg"

die() { printf '\n\033[31mERROR\033[0m %s\n\n' "$1" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$1"; }

activate_venv() {
  if [ -n "${VIRTUAL_ENV:-}" ]; then
    info "venv already active: $VIRTUAL_ENV"
    return 0
  fi
  # Windows (Git Bash) uses Scripts/, Linux and macOS use bin/
  for candidate in "$VENV_DIR/Scripts/activate" "$VENV_DIR/bin/activate"; do
    if [ -f "$candidate" ]; then
      # shellcheck disable=SC1090
      . "$candidate"
      break
    fi
  done
  if [ -z "${VIRTUAL_ENV:-}" ]; then
    die "No active virtualenv and none found at $VENV_DIR.
      The environment has not been set up yet, or you moved the directories.
      Expected layout: $WORK_DIR/{venv,pretix,data,$(basename "$REPO_DIR")}
      See ENVIRONMENT.md for how to rebuild it."
  fi
  info "venv: $VIRTUAL_ENV"
}

check_pretix() {
  [ -d "$PRETIX_SRC" ] || die "pretix source not found at $PRETIX_SRC (see ENVIRONMENT.md)."
  [ -f "$PRETIX_CONFIG_FILE" ] || die "pretix.cfg not found at $PRETIX_CONFIG_FILE (see ENVIRONMENT.md)."
  python -c "import pretix" 2>/dev/null || die "pretix is not importable in this venv. Run: pip install -e \"$PRETIX_DIR[dev]\""
}
