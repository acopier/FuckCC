"""Command line interface for fuckcc."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from argparse import Namespace

from . import __version__
from . import engine as engine_mod
from .api import AdobeClient, AdobeError
from .catalog import Catalog
from .config import ensure_dirs, home, paths
from .plan import Plan, Planner
from .stage import build_tree, download_plan
from .ui import bold, confirm, cyan, dim, error, human_bytes, info, ok, step, warn


# ---------------------------------------------------------------------------
def detect_arch() -> str:
    return "arm64" if platform.machine() == "arm64" else "x64"


def load_catalog(
    client: AdobeClient, refresh: bool = False, ttl_hours: int = 6
) -> Catalog:
    cache = os.path.join(paths()["cache"], "products_v6.xml")
    fresh = (
        os.path.exists(cache)
        and (time.time() - os.path.getmtime(cache)) < ttl_hours * 3600
    )
    if refresh or not fresh:
        step("Fetching Adobe product listing")
        data = client.products_xml(6)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        tmp = cache + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, cache)
        ok(f"catalog: {human_bytes(len(data))}")
    with open(cache, "rb") as fh:
        return Catalog.from_xml(fh.read())


# ---------------------------------------------------------------------------
def cmd_list(args: Namespace, client: AdobeClient) -> int:
    catalog = load_catalog(client, args.refresh)
    apps = catalog.apps()
    if args.json:
        print(
            json.dumps(
                [{"sap": a.sap_code, "name": a.display_name} for a in apps], indent=2
            )
        )
        return 0
    print(bold(f"{'SAP':<8} {'APPLICATION'}"))
    for a in apps:
        latest = a.versions()[0] if a.versions() else "?"
        print(f"{cyan(a.sap_code):<8} {a.display_name:<38} {dim(f'latest {latest}')}")
    info(f"\n{len(apps)} products. Use 'fuckcc versions <SAP>' for versions.")
    return 0


def cmd_versions(args: Namespace, client: AdobeClient) -> int:
    catalog = load_catalog(client, args.refresh)
    entry = catalog.get(args.sap)
    if not entry:
        error(f"unknown SAP code: {args.sap}")
        return 1
    if args.json:
        print(json.dumps(entry.versions(), indent=2))
        return 0
    print(bold(f"{entry.sap_code} — {entry.display_name}"))
    for v in entry.versions():
        plats = entry.platforms_for(v)
        print(f"  {v:<12} {dim(', '.join(sorted(plats.keys())))}")
    return 0


def cmd_languages(args: Namespace, client: AdobeClient) -> int:
    catalog = load_catalog(client, args.refresh)
    planner = Planner(catalog, client, arch=args.arch, language="en_US")
    try:
        langs = planner.available_languages(args.sap, args.version)
    except AdobeError as exc:
        error(str(exc))
        return 1
    if args.json:
        print(json.dumps(langs, indent=2))
        return 0
    print(
        bold(
            f"Languages for {args.sap.upper()}{f' {args.version}' if args.version else ''}"
        )
    )
    print("  " + ", ".join(langs))
    return 0


# ---------------------------------------------------------------------------
def _make_plan(args: Namespace, client: AdobeClient) -> Plan:
    catalog = load_catalog(client, args.refresh)
    planner = Planner(
        catalog,
        client,
        arch=args.arch,
        language=args.lang,
        include_dependencies=not args.no_deps,
        include_non_core=not args.minimal,
        recursive_deps=getattr(args, "recursive_deps", False),
    )
    return planner.build(args.sap, args.version)


def _print_plan(plan: Plan) -> None:
    print(
        bold(
            f"{plan.display_name} {plan.version}  ({plan.ap_platform}, "
            f"{plan.language}, {plan.arch})"
        )
    )
    print(
        f"  units: {len(plan.units)}   "
        f"download: {human_bytes(plan.total_download_bytes())}   "
        f"on-disk: {human_bytes(plan.total_extract_bytes())}"
    )
    for u in plan.units:
        tag = "target" if u.is_target else "dependency"
        print(
            f"    {cyan(u.sap_code):<8} {u.version:<14} {u.ap_platform:<12} "
            f"{len(u.packages)} pkg(s)  {dim(tag)}"
        )


def cmd_fetch(args: Namespace, client: AdobeClient) -> int:
    plan = _make_plan(args, client)
    _print_plan(plan)
    downloaded = download_plan(plan, client, jobs=args.jobs, verify=args.verify)
    root = build_tree(plan, downloaded, install_dir=args.install_dir)
    ok(f"package ready: {root}")
    info(
        "Install with: fuckcc install %s  (or run the generated .command)"
        f"{plan.target_sap}"
    )
    return 0


def cmd_install(args: Namespace, client: AdobeClient) -> int:
    plan = _make_plan(args, client)
    _print_plan(plan)
    if args.dry_run:
        info("dry run: not downloading or installing")
        return 0

    eng = args.engine or engine_mod.find_engine()
    if not eng:
        error("Adobe Setup engine not found.")
        info("Seed it once with one of:")
        info("  fuckcc engine harvest <Admin Console package / dmg / pkg>")
        info("  fuckcc engine bootstrap      (temporary CC install, EXPERIMENTAL)")
        return 3

    downloaded = download_plan(plan, client, jobs=args.jobs, verify=args.verify)
    root = build_tree(plan, downloaded, install_dir=args.install_dir, engine=eng)

    driver = os.path.join(root, "products", "driver.xml")
    step(f"Installing {plan.display_name} via {eng}")
    cmd = [eng, "--install=1", f"--driverXML={driver}"]
    if args.dry_run:
        info("would run: sudo " + " ".join(cmd))
        return 0
    if not args.yes and not confirm(
        "Run Adobe Setup now (requires admin)?", default=True
    ):
        info(f"staged at {root}; run the generated .command when ready")
        return 0
    proc = subprocess.run(
        ["sudo"] + cmd, cwd=os.path.join(root, "products"), check=False
    )
    if proc.returncode != 0:
        error(f"Adobe Setup exited with code {proc.returncode}")
        return proc.returncode
    ok("install finished")
    return 0


# ---------------------------------------------------------------------------
def cmd_engine(args: Namespace, client: AdobeClient) -> int:
    if args.engine_action == "status":
        st = engine_mod.status()
        active = st["active"]
        print(bold("Adobe Setup engine"))
        print(
            f"  managed : {st['managed']}  "
            f"{'found' if st['managed_exists'] else dim('missing')}"
        )
        print(
            f"  system  : {st['system']}  "
            f"{'found' if st['system_exists'] else dim('missing')}"
        )
        if active:
            ok(f"active engine: {active}")
        else:
            warn("no engine available; see 'fuckcc engine harvest --help'")
        return 0 if active else 1
    if args.engine_action == "harvest":
        try:
            engine_mod.harvest(args.source)
        except AdobeError as exc:
            error(str(exc))
            return 1
        return 0
    if args.engine_action == "bootstrap":
        try:
            engine_mod.bootstrap(client, assume_yes=args.yes)
        except AdobeError as exc:
            error(str(exc))
            return 1
        warn(engine_mod.uninstall_hint())
        return 0
    error("unknown engine action")
    return 2


def cmd_clean(args: Namespace, client: AdobeClient) -> int:
    p = paths()
    targets = []
    if args.all:
        targets = [p["cache"], p["stage"]]
        if args.engine:
            targets.append(p["engine"])
    else:
        if args.cache:
            targets.append(p["cache"])
        if args.stage:
            targets.append(p["stage"])
        if args.engine:
            targets.append(p["engine"])
    if not targets:
        targets = [p["stage"]]
    for t in targets:
        if os.path.isdir(t):
            import shutil

            shutil.rmtree(t)
            ok(f"removed {t}")
    return 0


def cmd_paths(args: Namespace, client: AdobeClient) -> int:
    p = paths()
    print(bold(f"fuckcc home: {home()}"))
    for k in ("cache", "stage", "engine", "log"):
        print(f"  {k:<8} {p[k]}")
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fuckcc",
        description="Install Adobe desktop apps from Adobe's own CDN without the "
        "Creative Cloud desktop app.",
    )
    parser.add_argument("--version", action="version", version=f"fuckcc {__version__}")
    parser.add_argument("--verbose", action="store_true", help="verbose network output")
    parser.add_argument(
        "--refresh", action="store_true", help="ignore the cached product listing"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list installable products")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_ver = sub.add_parser("versions", help="list versions for a product")
    p_ver.add_argument("sap")
    p_ver.add_argument("--arch", choices=["arm64", "x64"], default=detect_arch())
    p_ver.add_argument("--json", action="store_true")
    p_ver.set_defaults(func=cmd_versions)

    p_lang = sub.add_parser("languages", help="list install languages for a product")
    p_lang.add_argument("sap")
    p_lang.add_argument("--version", default=None)
    p_lang.add_argument("--arch", choices=["arm64", "x64"], default=detect_arch())
    p_lang.add_argument("--json", action="store_true")
    p_lang.set_defaults(func=cmd_languages)

    p_fetch = sub.add_parser("fetch", help="download and stage an offline package")
    p_fetch.add_argument("sap")
    p_fetch.add_argument("--version", default=None)
    p_fetch.add_argument("--lang", default="en_US")
    p_fetch.add_argument("--arch", choices=["arm64", "x64"], default=detect_arch())
    p_fetch.add_argument(
        "--no-deps", action="store_true", help="skip dependency components"
    )
    p_fetch.add_argument(
        "--recursive-deps",
        action="store_true",
        help="also resolve dependencies of dependencies",
    )
    p_fetch.add_argument("--minimal", action="store_true", help="core packages only")
    p_fetch.add_argument("--jobs", type=int, default=2)
    p_fetch.add_argument("--install-dir", default="/Applications")
    p_fetch.add_argument(
        "--verify", action="store_true", help="CRC-verify zip packages"
    )
    p_fetch.set_defaults(func=cmd_fetch)

    p_inst = sub.add_parser("install", help="download and install via Adobe Setup")
    p_inst.add_argument("sap")
    p_inst.add_argument("--version", default=None)
    p_inst.add_argument("--lang", default="en_US")
    p_inst.add_argument("--arch", choices=["arm64", "x64"], default=detect_arch())
    p_inst.add_argument("--no-deps", action="store_true")
    p_inst.add_argument("--recursive-deps", action="store_true")
    p_inst.add_argument("--minimal", action="store_true")
    p_inst.add_argument("--jobs", type=int, default=2)
    p_inst.add_argument("--install-dir", default="/Applications")
    p_inst.add_argument("--verify", action="store_true")
    p_inst.add_argument("--engine", default=None, help="path to Adobe Setup binary")
    p_inst.add_argument("--dry-run", action="store_true")
    p_inst.add_argument(
        "--yes", action="store_true", help="do not confirm before running Setup"
    )
    p_inst.set_defaults(func=cmd_install)

    p_eng = sub.add_parser("engine", help="manage Adobe's Setup engine")
    eng_sub = p_eng.add_subparsers(dest="engine_action", required=True)
    eng_status = eng_sub.add_parser("status", help="show engine locations")
    eng_status.set_defaults(func=cmd_engine)
    eng_harvest = eng_sub.add_parser(
        "harvest", help="extract engine from a package/dmg/pkg"
    )
    eng_harvest.add_argument("source")
    eng_harvest.set_defaults(func=cmd_engine)
    eng_boot = eng_sub.add_parser(
        "bootstrap", help="seed engine via Adobe's CC installer (EXPERIMENTAL)"
    )
    eng_boot.add_argument("--yes", action="store_true")
    eng_boot.set_defaults(func=cmd_engine)

    p_clean = sub.add_parser("clean", help="remove cached/staged files")
    p_clean.add_argument("--cache", action="store_true")
    p_clean.add_argument("--stage", action="store_true")
    p_clean.add_argument("--engine", action="store_true")
    p_clean.add_argument("--all", action="store_true")
    p_clean.set_defaults(func=cmd_clean)

    p_paths = sub.add_parser("paths", help="show where fuckcc stores data")
    p_paths.set_defaults(func=cmd_paths)

    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)
    client = AdobeClient(verbose=args.verbose)
    try:
        return args.func(args, client)
    except KeyboardInterrupt:
        warn("interrupted")
        return 130
    except AdobeError as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
