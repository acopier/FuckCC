import json
import os
import tempfile
import unittest
from typing import override

from fuckcc import config
from fuckcc.catalog import Catalog
from fuckcc.plan import Planner
from fuckcc.stage import build_tree, stage_dir

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

DEP_MANIFEST = {
    "Name": "Dependency",
    "ProductVersion": "1.0.0",
    "Packages": {
        "Package": [
            {
                "Type": "core",
                "Path": "/AdobeProducts/DEP/1.0/x/dep-core.zip",
                "DownloadSize": 1000,
                "ExtractSize": 2000,
            },
        ]
    },
    "Dependencies": {"Dependency": []},
}


class FakeClient:
    def __init__(
        self,
        manifest: dict[
            str, str | dict[str, list[str]] | dict[str, list[dict[str, int | str]]]
        ],
    ):
        self._manifest = manifest

    def application_manifest(self, build_guid: str):
        # PHSP manifest for the real build guid, a stub for everything else.
        if build_guid == "705f50ee-b3c7-4b88-b2af-4294f15bc0f0":
            return self._manifest
        return DEP_MANIFEST


def make_planner(**kwargs):
    with open(os.path.join(FIXTURES, "products.xml"), "rb") as fh:
        catalog = Catalog.from_xml(fh.read())
    with open(os.path.join(FIXTURES, "application_phsp.json")) as fh:
        manifest = json.load(fh)
    return Planner(catalog, FakeClient(manifest), **kwargs)


class PlanTest(unittest.TestCase):
    @override
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="fuckcc-test-")
        os.environ["FUCKCC_HOME"] = self._tmp

    @override
    def tearDown(self):
        os.environ.pop("FUCKCC_HOME", None)

    def test_plan_units_and_packages(self):
        planner = make_planner(arch="arm64", language="en_US")
        plan = planner.build("PHSP")
        self.assertEqual(plan.target_sap, "PHSP")
        self.assertEqual(plan.ap_platform, "macuniversal")
        self.assertEqual(plan.version, "27.11.0.26")
        target = plan.units[0]
        self.assertTrue(target.is_target)
        files = sorted(p.filename for p in target.packages)
        self.assertEqual(
            files,
            [
                "AdobePhotoshop27-Core.zip",
                "AdobePhotoshop27-SuperCafModels.zip",
                "AdobePhotoshop27-en_US.zip",
            ],
        )
        # dependencies are present but flagged as such
        self.assertGreater(len(plan.units), 1)
        self.assertTrue(all(not u.is_target for u in plan.units[1:]))

    def test_no_deps(self):
        planner = make_planner(
            arch="arm64", language="en_US", include_dependencies=False
        )
        plan = planner.build("PHSP")
        self.assertEqual(len(plan.units), 1)

    def test_dependency_requested_version_recorded(self):
        planner = make_planner(arch="arm64", language="en_US")
        plan = planner.build("PHSP")
        by_sap = {u.sap_code: u for u in plan.units}
        self.assertIn("COCM", by_sap)
        # version asked for by Photoshop, not the resolved concrete version
        self.assertEqual(by_sap["COCM"].requested_version, "1.0")

    def test_minimal_excludes_modules(self):
        planner = make_planner(arch="arm64", language="en_US", include_non_core=False)
        plan = planner.build("PHSP")
        files = [p.filename for p in plan.units[0].packages]
        self.assertNotIn("AdobePhotoshop27-SuperCafModels.zip", files)

    def test_stage_tree_and_driver(self):
        planner = make_planner(
            arch="arm64", language="en_US", include_dependencies=False
        )
        plan = planner.build("PHSP")
        downloaded = {}
        for p in plan.units[0].packages:
            path = os.path.join(config.paths()["cache"], p.sap_code, p.filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"x")
            downloaded[p.url] = path

        root = build_tree(plan, downloaded)
        self.assertEqual(root, stage_dir(plan))

        driver = os.path.join(root, "products", "driver.xml")
        self.assertTrue(os.path.exists(driver))
        with open(driver) as fh:
            text = fh.read()
        self.assertIn("<SAPCode>PHSP</SAPCode>", text)
        self.assertIn("<CodexVersion>27.11.0.26</CodexVersion>", text)
        self.assertIn("<InstallLanguage>en_US</InstallLanguage>", text)
        self.assertIn("<Platform>macuniversal</Platform>", text)

        # manifest + packages staged
        self.assertTrue(
            os.path.exists(os.path.join(root, "products", "PHSP", "application.json"))
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(root, "products", "PHSP", "AdobePhotoshop27-Core.zip")
            )
        )

        # launcher generated and executable
        launcher = os.path.join(root, "Install PHSP.command")
        self.assertTrue(os.path.exists(launcher))
        self.assertTrue(os.access(launcher, os.X_OK))

    def test_driver_lists_dependency_base_versions(self):
        planner = make_planner(arch="arm64", language="en_US")
        plan = planner.build("PHSP")
        downloaded = {}
        for unit in plan.units:
            for p in unit.packages:
                path = os.path.join(config.paths()["cache"], p.sap_code, p.filename)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(b"x")
                downloaded[p.url] = path
        root = build_tree(plan, downloaded)
        with open(os.path.join(root, "products", "driver.xml")) as fh:
            text = fh.read()
        # driver.xml must reference the requested base version "1.0" for COCM
        self.assertIn("<SAPCode>COCM</SAPCode>", text)
        self.assertIn("<BaseVersion>1.0</BaseVersion>", text)


if __name__ == "__main__":
    unittest.main()
