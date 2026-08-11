"""Entry point for the frozen build.

PyInstaller needs a script to point at, and `airhand/__main__.py` is not it: freezing that file
would import it as `__main__` and its relative imports would resolve against nothing.

This is a build artifact, not a second way to run the engine. The documented development command
stays `python -m airhand.main` — the engine has to keep working without the desktop app, and a
launcher that only exists inside a bundle cannot carry that promise.
"""

import sys

from airhand.main import main

if __name__ == "__main__":
    sys.exit(main())
