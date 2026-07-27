#!/usr/bin/env bash
# Throw away the development database, migrate from scratch and re-seed the demo data.
#
#   bash scripts/reset-dev.sh              # asks before deleting
#   bash scripts/reset-dev.sh -y           # no questions
#   bash scripts/reset-dev.sh -y --orders 50
#
# Everything lives in <work>/data, the pretix clone is not touched.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

ASSUME_YES=0
SEED_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1; shift ;;
    *) SEED_ARGS+=("$1"); shift ;;
  esac
done

activate_venv
check_pretix

if [ "$ASSUME_YES" -ne 1 ]; then
  printf '\nThis deletes %s and all uploaded media, then re-creates everything.\n' "$DATA_DIR/db.sqlite3"
  printf 'Continue? [y/N] '
  read -r answer
  case "$answer" in
    y|Y|yes|Yes) ;;
    *) echo "aborted"; exit 1 ;;
  esac
fi

info "removing database and media"
rm -f "$DATA_DIR/db.sqlite3"
rm -rf "$DATA_DIR/media" "$DATA_DIR/cache"
mkdir -p "$DATA_DIR/media" "$DATA_DIR/cache" "$DATA_DIR/logs"

info "running migrations (this also creates admin@localhost / admin)"
cd "$PRETIX_SRC"
python manage.py migrate --noinput

info "seeding demo data"
cd "$REPO_DIR"
python scripts/seed_demo.py --reset ${SEED_ARGS[@]+"${SEED_ARGS[@]}"}

cat <<BANNER

  Done. Start the server with:

      bash scripts/start-dev.sh

  Login: admin@localhost / admin

BANNER
