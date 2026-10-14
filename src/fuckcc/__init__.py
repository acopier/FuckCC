"""fuckcc — install Adobe desktop apps without the Creative Cloud desktop app.

This package talks to Adobe's *public* software-delivery endpoints (the same
ones the Creative Cloud desktop app uses) to download official installers, then
drives Adobe's own ``Setup`` engine to install them. Nothing here bypasses
Adobe's licensing: apps still activate through your normal Adobe account sign-in.
"""

from typing import Final

__version__: Final = "0.1.0"

__all__ = ["__version__"]
