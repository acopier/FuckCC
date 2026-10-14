"""Filesystem locations.

Everything fuckcc writes lives under a single root (``FUCKCC_HOME``). When
Homebrew is available that root defaults to ``$(brew --prefix)/var/fuckcc`` so
the Homebrew cask can prune it with a ``zap`` stanza. Override with the
``FUCKCC_HOME`` environment variable for a fully self-contained install.
"""

import os
import subprocess
from typing import TypedDict

APP_NAME = "fuckcc"


class Paths(TypedDict):
    """The directory layout under :func:`home`."""

    home: str
    cache: str
    stage: str
    engine: str
    log: str


def brew_prefix() -> str | None:
    """Return the Homebrew prefix, or ``None`` when Homebrew is unavailable."""
    env = os.environ.get("HOMEBREW_PREFIX")
    if env:
        return env
    try:
        out = subprocess.check_output(
            ["brew", "--prefix"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except OSError, subprocess.CalledProcessError:
        return None
    return out or None


def home() -> str:
    """Root directory for all fuckcc state."""
    env = os.environ.get("FUCKCC_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    prefix = brew_prefix()
    if prefix:
        return os.path.join(prefix, "var", APP_NAME)
    return os.path.join(os.path.expanduser("~"), f".{APP_NAME}")


def paths() -> Paths:
    root = home()
    return {
        "home": root,
        "cache": os.path.join(root, "cache"),
        "stage": os.path.join(root, "stage"),
        "engine": os.path.join(root, "engine"),
        "log": os.path.join(root, "log"),
    }


def ensure_dirs() -> Paths:
    p = paths()
    for key in ("home", "cache", "stage", "engine", "log"):
        os.makedirs(p[key], exist_ok=True)
    return p
