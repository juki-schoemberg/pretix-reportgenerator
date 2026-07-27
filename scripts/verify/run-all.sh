#!/usr/bin/env bash
# Re-run the environment verification. The dev server must be running
# (bash scripts/start-dev.sh) and, for the scheduled export, a mail sink:
#   python -m aiosmtpd -n -l localhost:1025
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

activate_venv
check_pretix

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rc=0
for script in verify_http.py verify_export.py verify_scheduled.py; do
  info "$script"
  python "$HERE/$script" || rc=1
  echo
done
[ "$rc" -eq 0 ] && info "all verifications passed" || printf '\033[31msome verifications failed\033[0m\n'
exit "$rc"
