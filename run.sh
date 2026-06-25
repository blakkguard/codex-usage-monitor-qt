#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python -m codex_usage_widget
fi

exec python3 -m codex_usage_widget
