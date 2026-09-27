#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
UPSTREAM="$ROOT/vendor/xunlei-cli-src"
PIN="f6af5bfd6b2b182ed2129421285fc57e8db13706"
mkdir -p "$ROOT/vendor" "$ROOT/var"
chmod 700 "$ROOT/var"

if [ ! -d "$UPSTREAM/.git" ]; then
  git clone https://github.com/nesk-woe/xunlei-cli.git "$UPSTREAM"
fi
git -C "$UPSTREAM" fetch origin "$PIN"
git -C "$UPSTREAM" checkout --detach "$PIN"

PYTHON="${PYTHON:-python3}"
"$PYTHON" -m venv "$ROOT/var/xunlei-cli-venv"
"$ROOT/var/xunlei-cli-venv/bin/python" -m pip install "$UPSTREAM/xunlei-cli"

if [ "$(uname -s)-$(uname -m)" != "Darwin-arm64" ]; then
  echo "PanSou bundled binary is macOS arm64 only; see references/dependencies.md for rebuilding." >&2
fi
echo "Setup complete. Check: $ROOT/var/xunlei-cli-venv/bin/python $ROOT/scripts/cinema_cli.py --help"
