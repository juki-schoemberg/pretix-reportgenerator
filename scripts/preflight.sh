#!/usr/bin/env bash
# Preflight für die pretix-Entwicklungsumgebung.
# Prüft nur und ändert nichts. Installiert bewusst nichts mit sudo —
# es sagt dir, was fehlt, und du entscheidest.
set -uo pipefail

OK=0
FAIL=0
WARN=0

green() { printf '  \033[32m✓\033[0m %s\n' "$1"; OK=$((OK+1)); }
red()   { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
yellow(){ printf '  \033[33m!\033[0m %s\n' "$1"; WARN=$((WARN+1)); }

echo
echo "pretix-Entwicklungsumgebung — Preflight"
echo "======================================="

# --- Betriebssystem -------------------------------------------------------
OS="$(uname -s)"
DISTRO=""
if [ -f /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  DISTRO="${ID:-}"
  echo
  echo "System: $OS / ${PRETTY_NAME:-unbekannt}"
else
  echo
  echo "System: $OS"
fi

# --- Python ---------------------------------------------------------------
echo
echo "Python"
if command -v python3 >/dev/null 2>&1; then
  PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  PYMAJ="$(python3 -c 'import sys; print(sys.version_info[0])')"
  PYMIN="$(python3 -c 'import sys; print(sys.version_info[1])')"
  if [ "$PYMAJ" -eq 3 ] && [ "$PYMIN" -ge 11 ]; then
    green "python3 $PYV"
  elif [ "$PYMAJ" -eq 3 ] && [ "$PYMIN" -ge 10 ]; then
    yellow "python3 $PYV — verbindlich ist die Angabe in pretix/pyproject.toml"
  else
    red "python3 $PYV ist zu alt (mindestens 3.10, eher 3.11+)"
  fi
  python3 -c 'import venv' 2>/dev/null \
    && green "venv-Modul vorhanden" \
    || red "venv-Modul fehlt (Paket python3-venv)"
  python3 -c 'import ensurepip' 2>/dev/null \
    && green "ensurepip vorhanden" \
    || yellow "ensurepip fehlt — venv-Erstellung kann scheitern"
else
  red "python3 nicht gefunden"
fi

# --- Werkzeuge ------------------------------------------------------------
echo
echo "Werkzeuge"
for tool in git curl make gcc; do
  command -v "$tool" >/dev/null 2>&1 && green "$tool" || red "$tool fehlt"
done
command -v msgfmt >/dev/null 2>&1 \
  && green "msgfmt (gettext)" \
  || red "msgfmt fehlt (Paket gettext) — Übersetzungen lassen sich nicht bauen"

# --- Node -----------------------------------------------------------------
echo
echo "Node (für den Asset-Build von pretix)"
if command -v node >/dev/null 2>&1; then
  NODEV="$(node --version)"
  NODEMAJ="$(echo "$NODEV" | sed 's/^v\([0-9]*\).*/\1/')"
  if [ "$NODEMAJ" -ge 18 ] 2>/dev/null; then
    green "node $NODEV"
  else
    yellow "node $NODEV — vermutlich zu alt, 18+ empfohlen"
  fi
else
  red "node fehlt"
fi
command -v npm >/dev/null 2>&1 && green "npm $(npm --version)" || red "npm fehlt"

# --- Platz ----------------------------------------------------------------
echo
echo "Ressourcen"
AVAIL_KB="$(df -Pk . | awk 'NR==2 {print $4}')"
AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
if [ "$AVAIL_GB" -ge 5 ]; then
  green "${AVAIL_GB} GB frei"
else
  yellow "nur ${AVAIL_GB} GB frei — Klon plus venv plus node_modules brauchen ca. 3–5 GB"
fi

# --- Paketvorschlag -------------------------------------------------------
echo
echo "Systempakete"
case "$DISTRO" in
  ubuntu|debian|raspbian)
    cat <<'HINT'
  Falls oben etwas fehlt, deckt das üblicherweise alles ab:

  sudo apt-get update && sudo apt-get install -y \
    build-essential python3-dev python3-venv python3-pip \
    libssl-dev libffi-dev zlib1g-dev \
    libxml2-dev libxslt1-dev \
    libjpeg-dev libopenjp2-7-dev \
    gettext git curl nodejs npm
HINT
    ;;
  fedora|rhel|centos|rocky|almalinux)
    cat <<'HINT'
  Falls oben etwas fehlt:

  sudo dnf install -y gcc gcc-c++ make python3-devel \
    openssl-devel libffi-devel zlib-devel \
    libxml2-devel libxslt-devel \
    libjpeg-turbo-devel openjpeg2-devel \
    gettext git curl nodejs npm
HINT
    ;;
  arch|manjaro)
    echo "  sudo pacman -S --needed base-devel python libxml2 libxslt libjpeg-turbo openjpeg2 gettext git curl nodejs npm"
    ;;
  *)
    if [ "$OS" = "Darwin" ]; then
      echo "  brew install python@3.12 gettext libxml2 libxslt jpeg openjpeg node git"
      echo "  Hinweis: gettext ist unter Homebrew keg-only, msgfmt ggf. per PATH nachziehen."
    else
      echo "  Distribution nicht erkannt — Äquivalente zu build-essential, python3-dev,"
      echo "  libxml2/libxslt, libjpeg, openjpeg, gettext, node und npm installieren."
    fi
    ;;
esac

# --- Fazit ----------------------------------------------------------------
echo
echo "======================================="
printf "OK: %d   Warnungen: %d   Fehlend: %d\n" "$OK" "$WARN" "$FAIL"
echo
if [ "$FAIL" -gt 0 ]; then
  echo "Es fehlt noch etwas. Installiere die Pakete oben und lass das Skript erneut laufen."
  echo "Die genauen Anforderungen deiner pretix-Zielversion stehen nach dem Klonen in"
  echo "pretix/doc/development/setup.rst — die gilt im Zweifel."
  exit 1
fi
echo "Bereit. Weiter mit dem Agent env-setup."
