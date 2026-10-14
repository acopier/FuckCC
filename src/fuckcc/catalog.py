"""Parse Adobe's product listing and application manifests.

The product listing (``products/all?...``) is a large XML document describing
every downloadable product, the macOS architecture of each build, and a
``buildGuid`` used to fetch that build's manifest. The manifest
(``core/v3/applications``) lists the individual ZIP packages that make up an
install and the conditions under which each applies.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from .api import AdobeError

_COND_RE = re.compile(r"^\[(?P<var>[A-Za-z0-9_]+)\]\s*(?P<op>==|!=)\s*(?P<val>.+)$")


def version_key(v: str) -> tuple[tuple[int, int, str], ...]:
    """Sortable key for dotted Adobe version strings (``27.11`` > ``27.9``)."""
    parts = re.split(r"[.\-_]", v)
    key: list[tuple[int, int, str]] = []
    for p in parts:
        if p.isdigit():
            key.append((1, int(p), ""))
        else:
            key.append((0, 0, p))
    return tuple(key)


@dataclass
class Build:
    sap_code: str
    version: str  # listing version attribute (e.g. "27.11")
    ap_platform: str  # macuniversal / macarm64 / osx10-64 / osx10
    base_version: str
    build_guid: str
    dependencies: list[tuple[str, str]] = field(default_factory=list)
    display_name: str = ""


@dataclass
class ProductEntry:
    sap_code: str
    display_name: str
    hidden: bool
    # version attr -> ap_platform -> Build
    builds: dict[str, dict[str, Build]] = field(default_factory=dict)

    def platforms_for(self, version: str) -> dict[str, Build]:
        return self.builds.get(version, {})

    def versions(self) -> list[str]:
        return sorted(self.builds.keys(), key=version_key, reverse=True)


class Catalog:
    def __init__(self, cdn: str, products: dict[str, ProductEntry]) -> None:
        self.cdn = cdn.rstrip("/")
        self.products = products

    # -- construction --------------------------------------------------------
    @classmethod
    def from_xml(cls, xml_bytes: bytes) -> Catalog:
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise AdobeError(f"could not parse product listing: {exc}") from exc

        products: dict[str, ProductEntry] = {}
        cdn: str | None = None
        for channel in root.findall("channels/channel"):
            cname = channel.get("name") or ""
            if cdn is None:
                node = channel.find("cdn/secure")
                if node is not None and node.text:
                    cdn = node.text.strip()
            hidden = cname != "ccm"
            for p in channel.findall("products/product"):
                sap = p.get("id")
                if not sap:
                    continue
                entry = products.get(sap)
                if entry is None:
                    entry = ProductEntry(
                        sap_code=sap,
                        display_name=(p.findtext("displayName") or sap).strip(),
                        hidden=hidden,
                    )
                    products[sap] = entry
                else:
                    # A product present in the ccm channel is not hidden.
                    entry.hidden = entry.hidden and hidden
                version = p.get("version")
                if not version:
                    continue
                for pf in p.findall("platforms/platform"):
                    ls = pf.find("languageSet")
                    if ls is None:
                        continue
                    guid = ls.get("buildGuid")
                    if not guid:
                        continue
                    ap_platform = pf.get("id") or ""
                    deps: list[tuple[str, str]] = []
                    for d in ls.findall("dependencies/dependency"):
                        dsap = d.findtext("sapCode")
                        dver = d.findtext("baseVersion")
                        if dsap and dver:
                            deps.append((dsap, dver))
                    build = Build(
                        sap_code=sap,
                        version=version,
                        ap_platform=ap_platform,
                        base_version=ls.get("baseVersion") or version,
                        build_guid=guid,
                        dependencies=deps,
                        display_name=entry.display_name,
                    )
                    entry.builds.setdefault(version, {})[ap_platform] = build
        if not cdn:
            raise AdobeError("product listing did not contain a CDN URL")
        return cls(cdn, products)

    # -- queries -------------------------------------------------------------
    def get(self, sap_code: str) -> ProductEntry | None:
        return self.products.get(sap_code.upper())

    def apps(self) -> list[ProductEntry]:
        """Non-hidden products that have at least one downloadable build."""
        out: list[ProductEntry] = []
        for p in self.products.values():
            if p.hidden:
                continue
            if any(b for plats in p.builds.values() for b in plats.values()):
                out.append(p)
        return sorted(out, key=lambda p: p.display_name.lower())

    def resolve(
        self,
        sap_code: str,
        version: str | None = None,
        platforms: list[str] | None = None,
    ) -> Build:
        entry = self.get(sap_code)
        if entry is None:
            raise AdobeError(f"unknown product SAP code: {sap_code}")
        platforms = platforms or ["macuniversal", "macarm64", "osx10-64", "osx10"]

        versions = entry.versions()
        if version:
            if version not in entry.builds:
                raise AdobeError(
                    f"version {version} not available for {sap_code} (try: {', '.join(versions[:8])})"
                )
            candidates = [version]
        else:
            candidates = versions

        for v in candidates:
            plats = entry.platforms_for(v)
            for want in platforms:
                if want in plats:
                    return plats[want]
        raise AdobeError(
            f"no usable build found for {sap_code}{f' version {version}' if version else ''}"
        )

    def resolve_dependency(
        self, sap_code: str, base_version: str, platforms: list[str] | None = None
    ) -> Build | None:
        """Find a build for a dependency SAP code at (or near) ``base_version``."""
        entry = self.get(sap_code)
        if entry is None:
            return None
        platforms = platforms or ["macuniversal", "macarm64", "osx10-64", "osx10"]

        # Prefer an exact baseVersion match, then an exact version match.
        exact: list[Build] = []
        for plats in entry.builds.values():
            for b in plats.values():
                if b.base_version == base_version or b.version == base_version:
                    exact.append(b)
        search = exact or [b for plats in entry.builds.values() for b in plats.values()]
        for want in platforms:
            for b in search:
                if b.ap_platform == want:
                    return b
        return search[0] if search else None


# -- manifest helpers --------------------------------------------------------
def dependencies_of(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    deps = (manifest.get("Dependencies") or {}).get("Dependency") or []
    out = []
    for d in deps:
        sap = d.get("SAPCode")
        ver = d.get("BaseVersion")
        if sap and ver:
            out.append((sap, ver))
    return out


def languages_of(manifest: dict[str, Any]) -> list[str]:
    langs = ((manifest.get("SupportedLanguages") or {}).get("Language")) or []
    return [l.get("locale") for l in langs if l.get("locale")]


def product_version(manifest: dict[str, Any]) -> str:
    return str(manifest.get("ProductVersion") or manifest.get("BaseVersion") or "")


def min_os_version(manifest: dict[str, Any]) -> str | None:
    os_req = (manifest.get("SystemRequirement") or {}).get("OsVersion") or {}
    return os_req.get("min")


def condition_ok(condition: str | None, env: dict[str, str]) -> bool:
    """Evaluate an Adobe package ``Condition`` against an environment.

    Conditions look like ``[OSArchitecture]==arm64`` or
    ``[installLanguage]==en_US`` and may be combined with ``&&`` / ``||``.
    Unrecognised variables are treated permissively (included) to avoid
    dropping content the app expects.
    """
    if not condition:
        return True
    cond = condition.strip()
    if not cond:
        return True

    for alternative in cond.split("||"):
        terms = [t.strip() for t in alternative.split("&&") if t.strip()]
        if all(_term_ok(t, env) for t in terms):
            return True
    return False


def _term_ok(term: str, env: dict[str, str]) -> bool:
    m = _COND_RE.match(term)
    if not m:
        return True  # unknown syntax: be permissive
    var = m.group("var")
    op = m.group("op")
    val = m.group("val").strip()
    if var not in env:
        return True  # unknown variable: be permissive
    current = env[var]
    if current == "*":  # "ALL" selection matches anything
        return True
    matches = current == val
    return matches if op == "==" else not matches


def select_packages(
    manifest: dict[str, Any], env: dict[str, str], include_non_core: bool = True
) -> list[dict[str, Any]]:
    """Return the manifest packages appropriate for ``env``."""
    packages = (manifest.get("Packages") or {}).get("Package") or []
    chosen: list[dict[str, Any]] = []
    for pkg in packages:
        ptype = (pkg.get("Type") or "").lower()
        if ptype != "core" and not include_non_core:
            continue
        if condition_ok(pkg.get("Condition"), env):
            chosen.append(pkg)
    return chosen


def arch_platforms(arch: str) -> list[str]:
    """Ordered platform preference list for the given host architecture."""
    if arch == "arm64":
        return ["macuniversal", "macarm64", "osx10-64", "osx10"]
    return ["macuniversal", "osx10-64", "osx10", "macarm64"]


def os_architecture(arch: str) -> str:
    """Adobe's ``[OSArchitecture]`` value for the given host architecture."""
    return "arm64" if arch == "arm64" else "x64"
