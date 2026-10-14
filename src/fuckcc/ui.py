"""Tiny terminal UI helpers (standard library only)."""

import os
import sys
import time
from typing import Final

_USE_COLOR: Final = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text: str) -> str:
    return _c("1", text)


def dim(text: str) -> str:
    return _c("2", text)


def green(text: str) -> str:
    return _c("32", text)


def yellow(text: str) -> str:
    return _c("33", text)


def red(text: str) -> str:
    return _c("31", text)


def cyan(text: str) -> str:
    return _c("36", text)


def info(msg: str) -> None:
    print(msg)


def step(msg: str) -> None:
    print(cyan("==>") + " " + bold(msg))


def ok(msg: str) -> None:
    print(green("  ok") + "  " + msg)


def warn(msg: str) -> None:
    print(yellow("  !!") + "  " + msg, file=sys.stderr)


def error(msg: str) -> None:
    print(red("  xx") + "  " + msg, file=sys.stderr)


def human_bytes(n: float | None) -> str:
    if n is None:
        return "?"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} TiB"


class Progress:
    """A single-line progress bar that degrades gracefully when not a TTY."""

    def __init__(self, label: str, total: int = 0) -> None:
        self.label = label
        self.total: int = total
        self.done: int = 0
        self.start: float = time.time()
        self._last_render: float = 0.0
        self._enabled: bool = sys.stderr.isatty()
        self._last_line: str = ""

    def update(self, n: int) -> None:
        self.done += n
        now = time.time()
        if not self._enabled:
            return
        if now - self._last_render < 0.1 and self.total and self.done < self.total:
            return
        self._last_render = now
        self._render()

    def _render(self) -> None:
        elapsed = max(time.time() - self.start, 1e-6)
        speed = self.done / elapsed
        if self.total:
            frac = min(self.done / self.total, 1.0)
            width = 28
            filled = int(width * frac)
            bar = "#" * filled + "-" * (width - filled)
            line = (
                f"  {self.label} [{bar}] {frac:.0%}  "
                f"{human_bytes(self.done)}/{human_bytes(self.total)}  "
                f"{human_bytes(speed)}/s"
            )
        else:
            line = f"  {self.label}  {human_bytes(self.done)}  {human_bytes(speed)}/s"
        pad = max(len(self._last_line) - len(line), 0)
        sys.stderr.write("\r" + line + " " * pad)
        sys.stderr.flush()
        self._last_line = line

    def close(self) -> None:
        if not self._enabled:
            return
        self._render()
        sys.stderr.write("\n")
        sys.stderr.flush()


def confirm(prompt: str, default: bool = False) -> bool:
    if not sys.stdin.isatty():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        reply = input(bold(prompt) + suffix).strip().lower()
    except EOFError:
        return default
    if not reply:
        return default
    return reply in {"y", "yes"}
