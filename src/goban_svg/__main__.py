"""`python -m goban_svg` -- runs the same CLI as the installed `goban-svg` console script.

Kept as a one-line shim so the package is runnable without installation (e.g.
straight out of a checkout via `python -m goban_svg ...`), delegating all
argument parsing and behavior to `goban_svg.cli.main` (docs/interfaces.md).
"""

from __future__ import annotations

import sys

from goban_svg.cli import main

if __name__ == "__main__":
    sys.exit(main())
