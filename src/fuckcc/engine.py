"""Locate and seed Adobe's ``Setup`` (HyperDrive) engine.

Installing an Adobe app requires Adobe's own ``Setup`` binary: it is the only
thing that understands the encrypted ``.pimx`` package descriptors and knows
where each payload file belongs. The engine normally ships with the Creative
Cloud desktop app, but we only need it *once* — ``fuckcc`` keeps a private copy
under its own root and never needs Creative Cloud installed or running again.

Three ways to obtain it:

* ``fuckcc engine harvest <path>`` — extract it from any Adobe installer or
  Admin Console offline package you already have (recommended, no CC install).
* ``fuckcc engine bootstrap``        — download Adobe's official Creative Cloud
  installer, let it drop the shared engine, harvest it, then remove CC.
* point ``FUCKCC_HOME`` at a machine that already has the engine.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import time
import zipfile

from .api import AdobeClient, AdobeError
from .config import paths
from .ui import confirm, human_bytes, ok, step, warn

SYSTEM_ENGINE = "/Library/Application Support/Adobe/Adobe Desktop Common/HDBox/Setup"
SYSTEM_HD_BOX_DIR = "/Library/Application Support/Adobe/Adobe Desktop Common/HDBox"

_PRODUCT_NAMES: dict[str, str] = {
    "AEFT": "After Effects",
    "AME": "Media Encoder",
    "AUDT": "Audition",
    "DRWV": "Dreamweaver",
    "IDSN": "InDesign",
    "ILST": "Illustrator",
    "KBRG": "Bridge",
    "LTRM": "Lightroom",
    "PHSP": "Photoshop",
    "PPRO": "Premiere Pro",
}


def managed_hd_box_dir() -> str:
    return os.path.join(paths()["engine"], "HDBox")


def managed_setup() -> str:
    return os.path.join(managed_hd_box_dir(), "Setup")


def find_engine() -> str | None:
    """Return a usable Setup engine path, or None."""
    for candidate in (managed_setup(), SYSTEM_ENGINE):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def status() -> dict[str, str | bool | None]:
    info: dict[str, str | bool | None] = {
        "managed": managed_setup(),
        "system": SYSTEM_ENGINE,
        "active": find_engine(),
    }
    info["managed_exists"] = os.path.isfile(managed_setup())
    info["system_exists"] = os.path.isfile(SYSTEM_ENGINE)
    return info


def product_cleanup_paths(
    sap_code: str, install_dir: str = "/Applications"
) -> list[str]:
    """Return product-specific app and user-support paths that actually exist."""
    product_name = _PRODUCT_NAMES.get(sap_code.upper(), sap_code.upper())
    patterns = [
        os.path.join(install_dir, f"Adobe {product_name}*"),
        os.path.expanduser(f"~/Library/Application Support/Adobe/{product_name}*"),
        os.path.expanduser(f"~/Library/Caches/Adobe/{product_name}*"),
        os.path.expanduser(f"~/Library/Logs/Adobe/{product_name}*"),
    ]
    return sorted({path for pattern in patterns for path in glob.glob(pattern)})


# -- harvesting --------------------------------------------------------------
def _candidate_hd_box_dirs(root: str) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        for d in list(dirnames):
            if d.lower() == "hdbox":
                full = os.path.join(dirpath, d)
                if os.path.exists(os.path.join(full, "Setup")):
                    found.append(full)
    return found


def _harvest_from_dir(root: str) -> str | None:
    candidates = _candidate_hd_box_dirs(root)
    if not candidates:
        return None
    src = candidates[0]
    dst = managed_hd_box_dir()
    if os.path.exists(dst):
        shutil.rmtree(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copytree(src, dst, symlinks=True)
    # Ensure the Setup binary is executable.
    try:
        os.chmod(managed_setup(), 0o755)
    except OSError:
        pass
    return managed_setup()


def _harvest_from_dmg(path: str) -> str | None:
    mountpoint = tempfile.mkdtemp(prefix="fuckcc-dmg-")
    try:
        subprocess.check_call(
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-noverify",
                "-mountpoint",
                mountpoint,
                path,
            ],
            stdout=subprocess.DEVNULL,
        )
        return _harvest_from_dir(mountpoint)
    finally:
        subprocess.call(
            ["hdiutil", "detach", mountpoint, "-quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(mountpoint, ignore_errors=True)


def _harvest_from_zip(path: str) -> str | None:
    tmp = tempfile.mkdtemp(prefix="fuckcc-zip-")
    try:
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp)
        return _harvest_from_dir(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _harvest_from_pkg(path: str) -> str | None:
    tmp = tempfile.mkdtemp(prefix="fuckcc-pkg-")
    try:
        subprocess.check_call(
            ["pkgutil", "--expand-full", path, tmp], stdout=subprocess.DEVNULL
        )
        result = _harvest_from_dir(tmp)
        if result:
            return result
        # Some packages are payload archives rather than an expanded bundle.
        return _harvest_from_dir(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def harvest(source: str) -> str:
    """Extract the Setup engine from ``source`` into fuckcc's private root."""
    source = os.path.abspath(os.path.expanduser(source))
    if not os.path.exists(source):
        raise AdobeError(f"path does not exist: {source}")
    step(f"Harvesting Adobe Setup engine from {source}")

    result = None
    if os.path.isdir(source):
        result = _harvest_from_dir(source)
    elif source.lower().endswith(".dmg"):
        result = _harvest_from_dmg(source)
    elif source.lower().endswith(".zip"):
        result = _harvest_from_zip(source)
    elif source.lower().endswith(".pkg"):
        result = _harvest_from_pkg(source)
    else:
        result = _harvest_from_dir(source)

    if not result:
        raise AdobeError(
            "no HDBox/Setup engine found inside %s.\n"
            "Pass an Adobe Admin Console offline package, an unzipped installer, "
            "or a .dmg/.pkg that contains 'Adobe Desktop Common/HDBox'"
        )
    ok(f"engine installed at {result}")
    return result


# -- bootstrap ---------------------------------------------------------------
def bootstrap(
    client: AdobeClient, assume_yes: bool = False, timeout_minutes: int = 20
) -> str | None:
    """Seed the engine via Adobe's official Creative Cloud installer.

    EXPERIMENTAL and invasive: this downloads Adobe's real CC installer, runs it
    so Adobe lays down the shared ``Adobe Desktop Common/HDBox`` engine, copies
    that engine into fuckcc's private root, and then asks you to remove Creative
    Cloud. It never bypasses licensing and it touches system locations.
    """
    warn("'engine bootstrap' installs the Creative Cloud app temporarily to obtain")
    warn(
        "Adobe's Setup engine, then removes it. This touches /Library and needs admin."
    )
    if not assume_yes and not confirm(
        "Continue with the CC installer bootstrap?", default=False
    ):
        raise AdobeError("aborted")

    url = client.wam_installer("KCCC", "Creative Cloud")
    dmg = os.path.join(paths()["cache"], "Creative_Cloud_Installer.dmg")
    step("Downloading Adobe's official Creative Cloud installer")
    client.download(url, dmg, progress_label="Creative_Cloud_Installer.dmg")
    ok(f"downloaded {dmg} ({human_bytes(os.path.getsize(dmg))})")

    mountpoint = tempfile.mkdtemp(prefix="fuckcc-cc-")
    try:
        subprocess.check_call(
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-noverify",
                "-mountpoint",
                mountpoint,
                dmg,
            ],
            stdout=subprocess.DEVNULL,
        )
        app = None
        for name in os.listdir(mountpoint):
            if name.endswith(".app"):
                app = os.path.join(mountpoint, name)
                break
        if not app:
            raise AdobeError("Creative Cloud installer app not found in the DMG")
        step("Launching Adobe's installer; complete the on-screen prompts")
        subprocess.call(["open", app])
        warn(
            f"Waiting up to {timeout_minutes} minutes for Adobe to lay down the Setup engine..."
        )
        deadline = time.time() + timeout_minutes * 60
        while time.time() < deadline:
            if os.path.isfile(SYSTEM_ENGINE):
                break
            time.sleep(5)
        if not os.path.isfile(SYSTEM_ENGINE):
            raise AdobeError("Setup engine did not appear; aborting bootstrap")
    finally:
        subprocess.call(
            ["hdiutil", "detach", mountpoint, "-quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(mountpoint, ignore_errors=True)

    engine = harvest(SYSTEM_HD_BOX_DIR)
    warn("Creative Cloud is now installed. Remove it from the Creative Cloud app's")
    warn("preferences or with Adobe's uninstaller; fuckcc no longer needs it.")
    return engine


def uninstall_hint() -> str:
    return (
        "Remove Creative Cloud with:\n"
        "  '/Applications/Utilities/Adobe Creative Cloud/ACC/Uninstaller.app/Contents/MacOS/Uninstaller' "
        "or the CC app's Help > Uninstall menu."
    )
