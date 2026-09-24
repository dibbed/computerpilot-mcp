#!/usr/bin/env sh
set -u

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_ROOT" || exit 1

VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
    printf '%s\n' '[1/5] Creating Python virtual environment...'
    PYTHON_BIN=${PYTHON_BIN:-}
    if [ -z "$PYTHON_BIN" ]; then
        if command -v python3 >/dev/null 2>&1; then
            PYTHON_BIN=$(command -v python3)
        elif command -v python >/dev/null 2>&1; then
            PYTHON_BIN=$(command -v python)
        else
            printf '%s\n' 'ERROR: Python 3.10 or newer is required but was not found.' >&2
            exit 10
        fi
    fi
    "$PYTHON_BIN" -m venv "$PROJECT_ROOT/.venv" || exit 11
else
    printf '%s\n' '[1/5] Virtual environment found.'
fi

failures=0
while :; do
    "$VENV_PYTHON" -m scripts.bootstrap --start
    code=$?
    case "$code" in
        130|143) exit "$code" ;;
    esac

    failures=$((failures + 1))
    retry=5
    [ "$failures" -ge 2 ] && retry=10
    [ "$failures" -ge 3 ] && retry=30
    [ "$failures" -ge 4 ] && retry=60

    printf '\nMCP exited with code %s. Restarting in %s seconds...\n' "$code" "$retry"
    printf '%s\n' 'Press Ctrl+C to stop.'
    sleep "$retry" || exit 130
done
