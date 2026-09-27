#!/bin/sh
set -eu
SKILL_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
BIN="${PANSOU_BIN:-$SKILL_DIR/vendor/pansou-darwin-arm64}"
VAR="$SKILL_DIR/var"
PORT="${PANSOU_PORT:-18888}"
mkdir -p "$VAR"
if [ ! -x "$BIN" ]; then
  echo "PanSou binary missing or incompatible: $BIN" >&2
  exit 1
fi
export PORT
export ENABLED_PLUGINS="${ENABLED_PLUGINS:-pansearch,thepiratebay}"
export CACHE_ENABLED=true
export CACHE_PATH="$VAR/pansou-cache.db"
export HTTP_PROXY="${XUNLEI_CINEMA_PROXY:-http://127.0.0.1:7897}"
export HTTPS_PROXY="$HTTP_PROXY"
cd "$VAR"
echo "Starting local PanSou on 127.0.0.1:$PORT. Press Ctrl-C to stop."
exec "$BIN"
