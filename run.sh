#!/usr/bin/env bash
#
# Prism launcher (macOS / Linux)
# -------------------------------
# One command to bootstrap and run Prism:
#   1. Find a Python 3.11+ interpreter
#   2. Create (or reuse) a local virtualenv at ./.venv
#   3. Install/sync dependencies from requirements.txt
#   4. Launch the Streamlit app at http://localhost:8501
#
# Usage:
#   ./run.sh                         # launch on the default port (8501)
#   ./run.sh -- --server.port 8600   # pass extra args through to streamlit
#
# Optional API keys (FRED live Treasury curve, Anthropic) are NOT required for
# demo mode. If you have them, put them in a .env file at the repo root, e.g.:
#   FRED_API_KEY=...
#   ANTHROPIC_API_KEY=...
# This script never echoes or requires any key.

set -euo pipefail

# --- Run from the repo root (where this script lives), regardless of caller CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"
REQ_FILE="requirements.txt"
APP_FILE="app.py"
MIN_MAJOR=3
MIN_MINOR=11
APP_URL="http://localhost:8501"

err() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; }
info() { printf '%s\n' "$*"; }

# --- 1. Locate a suitable Python interpreter (3.11+). ------------------------
# Verify the version by actually executing the interpreter, not by its name.
is_py_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= ('"$MIN_MAJOR"', '"$MIN_MINOR"') else 1)' \
    >/dev/null 2>&1
}

PY=""
for cand in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && is_py_ok "$cand"; then
    PY="$(command -v "$cand")"
    break
  fi
done

if [ -z "$PY" ]; then
  err "No suitable Python interpreter found."
  err "Prism requires Python ${MIN_MAJOR}.${MIN_MINOR}+ (e.g. python3.11 / python3.12)."
  err "Install it from: https://www.python.org/downloads/"
  err "After installing, re-run: ./run.sh"
  exit 1
fi

PY_VER="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
info "Using Python ${PY_VER} (${PY})"

# --- 2. Create the venv if missing, reuse if present. ------------------------
VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ]; then
  info "Creating virtualenv at ${VENV_DIR} ..."
  if ! "$PY" -m venv "$VENV_DIR"; then
    err "Failed to create virtualenv at ${VENV_DIR}."
    err "Ensure the 'venv' module is available for ${PY}."
    exit 1
  fi
else
  info "Reusing existing virtualenv at ${VENV_DIR}"
fi

if [ ! -x "$VENV_PY" ]; then
  err "Virtualenv python not found at ${VENV_PY} after creation."
  exit 1
fi

# --- 3. Install/sync dependencies. ------------------------------------------
# Lightweight optimization: skip the pip install step when requirements.txt is
# unchanged since the last successful install (tracked by a hash sentinel).
# A fresh checkout has no sentinel, so it always installs everything first.
REQ_HASH=""
if command -v shasum >/dev/null 2>&1; then
  REQ_HASH="$(shasum -a 256 "$REQ_FILE" | awk '{print $1}')"
elif command -v sha256sum >/dev/null 2>&1; then
  REQ_HASH="$(sha256sum "$REQ_FILE" | awk '{print $1}')"
fi
SENTINEL="$VENV_DIR/.deps-installed"

if [ -n "$REQ_HASH" ] && [ -f "$SENTINEL" ] && [ "$(cat "$SENTINEL")" = "$REQ_HASH" ]; then
  info "Dependencies already up to date (requirements.txt unchanged)."
else
  info "Installing dependencies ..."
  if ! "$VENV_PY" -m pip install --upgrade pip >/dev/null; then
    err "Failed to upgrade pip."
    exit 1
  fi
  if ! "$VENV_PY" -m pip install -r "$REQ_FILE"; then
    err "Failed to install dependencies from ${REQ_FILE}."
    exit 1
  fi
  if [ -n "$REQ_HASH" ]; then
    printf '%s' "$REQ_HASH" > "$SENTINEL"
  fi
fi

# --- 4. Launch the app. ------------------------------------------------------
# Strip a leading "--" separator so users can do: ./run.sh -- --server.port 8600
if [ "${1:-}" = "--" ]; then
  shift
fi

info ""
info "Starting Prism at ${APP_URL} ..."
info "(Press Ctrl-C to stop.)"

# Run headless so the launcher never blocks on Streamlit's first-run email
# prompt and so this works over SSH / CI. We open the browser ourselves below
# (best effort) to preserve the desktop UX. Disable anonymous usage stats.
open_browser() {
  # Wait until the server is reachable, then open the default browser once.
  local i
  for i in $(seq 1 30); do
    if curl -fsS -o /dev/null "${APP_URL}/_stcore/health" 2>/dev/null; then
      if command -v open >/dev/null 2>&1; then
        open "$APP_URL" >/dev/null 2>&1 || true   # macOS
      elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$APP_URL" >/dev/null 2>&1 || true  # Linux
      fi
      return 0
    fi
    sleep 1
  done
}
open_browser &

exec "$VENV_PY" -m streamlit run "$APP_FILE" \
  --server.headless true \
  --browser.gatherUsageStats false \
  "$@"
