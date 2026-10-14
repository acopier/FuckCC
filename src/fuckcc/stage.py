"""Stage a :class:`~fuckcc.plan.Plan` into an Adobe-compatible install tree.

The output mirrors what Adobe's own offline packages look like::

    products/
      driver.xml
      PHSP/application.json
      PHSP/AdobePhotoshop27-Core.zip
      ...
      ACR/application.json
      ACR/...

Adobe's ``Setup`` engine consumes ``driver.xml`` plus the per-product manifest
(``application.json``) and packages. We also drop a small ``Install <App>.command``
launcher so the tree can be installed by hand.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from concurrent.futures import ThreadPoolExecutor, as_completed

from .api import AdobeClient
from .config import paths
from .plan import Package, Plan, Unit
from .ui import human_bytes, ok, step, warn


def stage_root_name(plan: Plan) -> str:
    return (
        f"{plan.target_sap}_{plan.version or plan.units[0].listing_version}_"
        f"{plan.language}_{plan.ap_platform}"
    )


def stage_dir(plan: Plan) -> str:
    return os.path.join(paths()["stage"], stage_root_name(plan))


def _cache_path(sap_code: str, filename: str) -> str:
    return os.path.join(paths()["cache"], sap_code, filename)


def _link_or_copy(src: str, dst: str) -> None:
    if os.path.exists(dst):
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def download_plan(
    plan: Plan, client: AdobeClient, jobs: int = 2, verify: bool = False
) -> dict[str, str]:
    """Download every package in the plan into the cache.

    Returns a mapping of package URL to local cached path.
    """
    todo = [(u, p) for u in plan.units for p in u.packages]
    results: dict[str, str] = {}
    total = sum(p.download_size for _, p in todo)
    step(
        f"Downloading {len(todo)} packages ({human_bytes(total)}) for "
        f"{plan.display_name}"
    )
    for u, p in todo:
        dest = _cache_path(p.sap_code, p.filename)
        have = os.path.exists(dest) and (
            not p.download_size or os.path.getsize(dest) == p.download_size
        )
        if have:
            ok(f"{p.sap_code}/{p.filename} (cached)")
            results[p.url] = dest

    pending = [(u, p) for (u, p) in todo if p.url not in results]
    if not pending:
        return results

    def _job(item: tuple[Unit, Package]) -> tuple[tuple[Unit, Package], str]:
        dest = _cache_path(pkg.sap_code, pkg.filename)
        client.download(
            pkg.url,
            dest,
            expected_size=pkg.download_size or None,
            progress_label=f"{pkg.sap_code}/{pkg.filename}",
        )
        return item, dest

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(_job, item): item for item in pending}
        for fut in as_completed(futures):
            (_, pkg), dest = fut.result()
            results[pkg.url] = dest
            ok(f"{pkg.sap_code}/{pkg.filename}")
    return results


def build_tree(
    plan: Plan,
    downloaded: dict[str, str],
    install_dir: str = "/Applications",
    engine: str | None = None,
) -> str:
    """Materialise the install tree for ``plan`` and return its root."""
    root = stage_dir(plan)
    products_dir = os.path.join(root, "products")
    os.makedirs(products_dir, exist_ok=True)

    step(f"Staging install tree at {root}")
    for unit in plan.units:
        product_dir = os.path.join(products_dir, unit.sap_code)
        os.makedirs(product_dir, exist_ok=True)
        manifest_path = os.path.join(product_dir, "application.json")
        with open(manifest_path, "w") as fh:
            json.dump(unit.manifest, fh, separators=(",", ":"))
        for pkg in unit.packages:
            src = downloaded.get(pkg.url)
            if not src or not os.path.exists(src):
                warn(f"missing package {pkg.filename}")
                continue
            _link_or_copy(src, os.path.join(product_dir, pkg.filename))
    ok(f"{len(plan.units)} product units staged")

    write_driver_xml(plan, products_dir, install_dir=install_dir)
    write_launcher(plan, root, products_dir, engine)
    return root


def write_driver_xml(
    plan: Plan, products_dir: str, install_dir: str = "/Applications"
) -> str:
    target = plan.units[0]
    deps = "\n".join(
        f"""         <Dependency>
                <SAPCode>{u.sap_code}</SAPCode>
                <BaseVersion>{u.requested_version}</BaseVersion>
                <EsdDirectory>./{u.sap_code}</EsdDirectory>
            </Dependency>"""
        for u in plan.units[1:]
    )
    content = f"""<DriverInfo>
    <ProductInfo>
        <Name>Adobe {plan.display_name}</Name>
        <SAPCode>{target.sap_code}</SAPCode>
        <CodexVersion>{target.version}</CodexVersion>
        <Platform>{plan.ap_platform}</Platform>
        <EsdDirectory>./{target.sap_code}</EsdDirectory>
        <Dependencies>
{deps}
        </Dependencies>
    </ProductInfo>
    <RequestInfo>
        <InstallDir>{install_dir}</InstallDir>
        <InstallLanguage>{plan.language}</InstallLanguage>
    </RequestInfo>
</DriverInfo>
"""
    path = os.path.join(products_dir, "driver.xml")
    with open(path, "w") as fh:
        fh.write(content)
    return path


def write_launcher(
    plan: Plan, root: str, products_dir: str, engine: str | None = None
) -> str:
    engine = (
        engine or "/Library/Application Support/Adobe/Adobe Desktop Common/HDBox/Setup"
    )
    driver = os.path.join(products_dir, "driver.xml")
    script = f"""#!/bin/bash
# Generated by fuckcc. Installs {plan.display_name} using Adobe's Setup engine.
set -euo pipefail
ENGINE="{engine}"
DRIVER="{driver}"
if [ ! -x "$ENGINE" ]; then
  echo "Adobe Setup engine not found at: $ENGINE" >&2
  echo "Run 'fuckcc engine status' for help seeding it." >&2
  exit 1
fi
echo "Installing {plan.display_name} {plan.version} ({plan.language}) via Adobe Setup..."
exec sudo "$ENGINE" --install=1 --driverXML="$DRIVER"
"""
    path = os.path.join(root, f"Install {plan.target_sap}.command")
    with open(path, "w") as fh:
        fh.write(script)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path
