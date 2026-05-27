"""Entry point for python -m xunlei."""

import sys

# Change process name shown in ps/top/htop
sys.argv[0] = "xunlei-cli"

# Support both `python3 -m xunlei` (relative import) and
# `python3 xunlei/` / `python3 xunlei/__main__.py` (absolute import)
try:
    from .cli import main
except ImportError:
    from xunlei.cli import main

if __name__ == "__main__":
    main()
