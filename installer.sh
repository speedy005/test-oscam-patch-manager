#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/installer.py" "$@"
fi

if command -v python >/dev/null 2>&1; then
    exec python "$SCRIPT_DIR/installer.py" "$@"
fi

echo "Python 3.9 or newer is required." >&2
echo "Install Python using your Linux distribution's package manager." >&2
exit 1
