"""Turn a user request ("install Photoshop, en_US, arm64") into a concrete plan.

A plan is the set of manifests and packages that Adobe's Setup engine needs in
order to install the requested product, including its dependency components
(color sets, Camera Raw, CoreSync, the UXP/CCX runtime, ...).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .api import AdobeClient
from .catalog import (
    Build,
    Catalog,
    arch_platforms,
    dependencies_of,
    languages_of,
    os_architecture,
    product_version,
    select_packages,
)


@dataclass
class Package:
    sap_code: str
    build_version: str
    url: str
    filename: str
    download_size: int
    extract_size: int
    condition: str | None = None
    is_core: bool = True


@dataclass
class Unit:
    """One product or dependency to stage (manifest + packages)."""

    sap_code: str
    display_name: str
    version: str  # manifest ProductVersion (full)
    listing_version: str
    ap_platform: str
    build_guid: str
    manifest: dict[str, Any]
    packages: list[Package] = field(default_factory=list)
    is_target: bool = False
    # Version the parent asked for; written to driver.xml as <BaseVersion>.
    requested_version: str = ""


@dataclass
class Plan:
    target_sap: str
    display_name: str
    version: str
    ap_platform: str
    language: str
    arch: str
    units: list[Unit] = field(default_factory=list)

    def total_download_bytes(self) -> int:
        return sum(p.download_size for u in self.units for p in u.packages)

    def total_extract_bytes(self) -> int:
        return sum(p.extract_size for u in self.units for p in u.packages)


class Planner:
    def __init__(
        self,
        catalog: Catalog,
        client: AdobeClient,
        arch: str = "arm64",
        language: str = "en_US",
        include_dependencies: bool = True,
        include_non_core: bool = True,
        recursive_deps: bool = False,
    ):
        self.catalog = catalog
        self.client = client
        self.arch = arch
        self.language = language
        self.include_dependencies = include_dependencies
        self.include_non_core = include_non_core
        self.recursive_deps = recursive_deps
        self._manifest_cache: dict[str, dict[str, Any]] = {}

    def _env(self) -> dict[str, str]:
        return {
            "OSArchitecture": os_architecture(self.arch),
            "installLanguage": self.language,
        }

    def _manifest(self, build_guid: str) -> dict[str, Any]:
        if build_guid not in self._manifest_cache:
            self._manifest_cache[build_guid] = self.client.application_manifest(
                build_guid
            )
        return self._manifest_cache[build_guid]

    def available_languages(
        self, sap_code: str, version: str | None = None
    ) -> list[str]:
        build = self.catalog.resolve(sap_code, version, arch_platforms(self.arch))
        return languages_of(self._manifest(build.build_guid))

    def _unit_from_build(
        self,
        build: Build,
        is_target: bool,
        display_name: str,
        requested_version: str = "",
    ) -> Unit:
        manifest = self._manifest(build.build_guid)
        packages = []
        for pkg in select_packages(
            manifest, self._env(), include_non_core=self.include_non_core
        ):
            path = pkg.get("Path") or ""
            if not path:
                continue
            packages.append(
                Package(
                    sap_code=build.sap_code,
                    build_version=str(manifest.get("ProductVersion") or build.version),
                    url=self.catalog.cdn + path,
                    filename=os.path.basename(path.split("?")[0]),
                    download_size=int(pkg.get("DownloadSize") or 0),
                    extract_size=int(pkg.get("ExtractSize") or 0),
                    condition=pkg.get("Condition"),
                    is_core=(pkg.get("Type") or "").lower() == "core",
                )
            )
        return Unit(
            sap_code=build.sap_code,
            display_name=display_name,
            version=product_version(manifest) or build.version,
            listing_version=build.version,
            ap_platform=build.ap_platform,
            build_guid=build.build_guid,
            manifest=manifest,
            packages=packages,
            is_target=is_target,
            requested_version=requested_version or build.version,
        )

    def build(self, sap_code: str, version: str | None = None) -> Plan:
        sap_code = sap_code.upper()
        entry = self.catalog.get(sap_code)
        display = entry.display_name if entry else sap_code
        build = self.catalog.resolve(sap_code, version, arch_platforms(self.arch))
        target = self._unit_from_build(build, True, display)

        units: list[Unit] = [target]
        if self.include_dependencies:
            seen = {sap_code}
            # Direct dependencies only by default (matches Adobe's own offline
            # packages). `recursive_deps` walks the whole graph instead.
            queue = [(sap, ver) for sap, ver in dependencies_of(target.manifest)]
            while queue:
                dep_sap, dep_ver = queue.pop(0)
                if dep_sap in seen:
                    continue
                seen.add(dep_sap)
                dep_build = self.catalog.resolve_dependency(
                    dep_sap, dep_ver, arch_platforms(self.arch)
                )
                if dep_build is None:
                    continue
                dep_unit = self._unit_from_build(
                    dep_build,
                    False,
                    dep_build.display_name or dep_sap,
                    requested_version=dep_ver,
                )
                units.append(dep_unit)
                if self.recursive_deps:
                    for d in dependencies_of(dep_unit.manifest):
                        if d[0] not in seen:
                            queue.append((d[0], d[1]))

        return Plan(
            target_sap=sap_code,
            display_name=display,
            version=target.version,
            ap_platform=target.ap_platform,
            language=self.language,
            arch=self.arch,
            units=units,
        )
