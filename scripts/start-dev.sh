#!/usr/bin/env bash
# Start the pretix development server with the demo data.
#
#   bash scripts/start-dev.sh          # port 8000
#   bash scripts/start-dev.sh 8080     # different port
#
# Stop it with Ctrl+C.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

PORT="${1:-8000}"

activate_venv
check_pretix

if [ ! -f "$DATA_DIR/db.sqlite3" ]; then
  die "No database at $DATA_DIR/db.sqlite3.
      Run this first:  bash scripts/reset-dev.sh"
fi

if ! python "$REPO_DIR/scripts/seed_demo.py" --list >/dev/null 2>&1; then
  printf '\033[33m!\033[0m No demo data found. Create it with: bash scripts/reset-dev.sh\n'
fi

cat <<BANNER

  pretix dev server
  -----------------------------------------------------------
  Backend      http://localhost:$PORT/control/
  Shop (demo)  http://localhost:$PORT/demo/demo-event/
  Login        admin@localhost  /  admin
  Data dir     $DATA_DIR
  Log file     $DATA_DIR/logs/pretix.log
  -----------------------------------------------------------
  Background tasks run SYNCHRONOUSLY (no celery broker configured),
  so exports finish inside the request. See ENVIRONMENT.md.

  Stop with Ctrl+C.

BANNER

cd "$PRETIX_SRC"
exec python manage.py runserver "$PORT"
