#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TOOLS="${XUNLEI_TOOLS_DIR:-$HOME/.codex/tools/xunlei-cinema}"
UPSTREAM="$TOOLS/vendor/xunlei-cli-src"
PIN="f6af5bfd6b2b182ed2129421285fc57e8db13706"
mkdir -p "$TOOLS/vendor" "$ROOT/var"
chmod 700 "$ROOT/var"

if [ ! -d "$UPSTREAM/.git" ]; then
  git clone https://github.com/nesk-woe/xunlei-cli.git "$UPSTREAM"
fi
git -C "$UPSTREAM" fetch origin "$PIN"
git -C "$UPSTREAM" checkout --detach "$PIN"

PYTHON="${PYTHON:-$HOME/.codex/venv/bin/python3}"
[ -x "$PYTHON" ] || { echo "Codex shared Python missing; see references/dependencies.md" >&2; exit 1; }
uv pip install --python "$PYTHON" "$UPSTREAM/xunlei-cli"

echo "Setup complete. Check: $PYTHON $ROOT/scripts/cinema_cli.py --help"
