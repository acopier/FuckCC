import json
import os
import unittest
from typing import override

from fuckcc.catalog import (
    Catalog,
    arch_platforms,
    condition_ok,
    languages_of,
    os_architecture,
    select_packages,
    version_key,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_catalog():
    with open(os.path.join(FIXTURES, "products.xml"), "rb") as fh:
        return Catalog.from_xml(fh.read())


def load_manifest():
    with open(os.path.join(FIXTURES, "application_phsp.json")) as fh:
        return json.load(fh)


class CatalogTest(unittest.TestCase):
    @override
    def setUp(self):
        self.catalog = load_catalog()

    def test_cdn(self):
        self.assertEqual(self.catalog.cdn, "https://ccmdls.adobe.com")

    def test_apps_only_visible_downloadable(self):
        apps = {a.sap_code for a in self.catalog.apps()}
        self.assertIn("PHSP", apps)
        # KCCC has no build guids in the fixture, so it must not be advertised.
        self.assertNotIn("KCCC", apps)

    def test_kccc_has_no_builds(self):
        self.assertEqual(self.catalog.get("KCCC").versions(), [])

    def test_versions_sorted_newest_first(self):
        versions = self.catalog.get("PHSP").versions()
        self.assertEqual(versions[0], "27.11")
        self.assertIn("27.0", versions)

    def test_resolve_default_is_newest_universal(self):
        build = self.catalog.resolve("PHSP")
        self.assertEqual(build.version, "27.11")
        self.assertEqual(build.ap_platform, "macuniversal")
        self.assertTrue(build.build_guid)
        self.assertIn(("COCM", "1.0"), build.dependencies)

    def test_resolve_specific_version(self):
        build = self.catalog.resolve("PHSP", "27.0")
        self.assertEqual(build.version, "27.0")

    def test_resolve_unknown_product(self):
        with self.assertRaises(Exception):
            self.catalog.resolve("NOPE")

    def test_resolve_dependency_prefers_requested_base_version(self):
        dep = self.catalog.resolve_dependency("COCM", "1.0")
        self.assertIsNotNone(dep)
        self.assertEqual(dep.sap_code, "COCM")


class VersionKeyTest(unittest.TestCase):
    def test_numeric_ordering(self):
        self.assertGreater(version_key("27.11"), version_key("27.9"))
        self.assertGreater(version_key("27.9.1"), version_key("27.9"))
        self.assertGreater(version_key("6.11.0"), version_key("6.2.0"))

    def test_sort(self):
        data = ["27.2", "27.11", "27.9", "6.2", "27.0"]
        self.assertEqual(max(data, key=version_key), "27.11")


class ConditionTest(unittest.TestCase):
    def test_empty(self):
        self.assertTrue(condition_ok(None, {}))
        self.assertTrue(condition_ok("", {}))
        self.assertTrue(condition_ok("   ", {}))

    def test_equality(self):
        env = {"OSArchitecture": "arm64", "installLanguage": "en_US"}
        self.assertTrue(condition_ok("[OSArchitecture]==arm64", env))
        self.assertFalse(condition_ok("[OSArchitecture]==x64", env))
        self.assertTrue(condition_ok("[installLanguage]==en_US", env))
        self.assertFalse(condition_ok("[installLanguage]==de_DE", env))

    def test_inequality(self):
        env = {"OSArchitecture": "arm64"}
        self.assertTrue(condition_ok("[OSArchitecture]!=x64", env))
        self.assertFalse(condition_ok("[OSArchitecture]!=arm64", env))

    def test_and_or(self):
        env = {"OSArchitecture": "arm64", "installLanguage": "en_US"}
        self.assertTrue(
            condition_ok("[OSArchitecture]==arm64 && [installLanguage]==en_US", env)
        )
        self.assertFalse(
            condition_ok("[OSArchitecture]==arm64 && [installLanguage]==de_DE", env)
        )
        self.assertTrue(
            condition_ok("[installLanguage]==de_DE || [installLanguage]==en_US", env)
        )

    def test_all_language_wildcard(self):
        env = {"installLanguage": "*"}
        self.assertTrue(condition_ok("[installLanguage]==ja_JP", env))

    def test_unknown_variable_is_permissive(self):
        self.assertTrue(condition_ok("[SomethingElse]==whatever", {}))


class SelectionTest(unittest.TestCase):
    def test_selects_arch_language_and_unconditional(self):
        manifest = load_manifest()
        env = {"OSArchitecture": "arm64", "installLanguage": "en_US"}
        names = sorted(p["Path"].split("/")[-1] for p in select_packages(manifest, env))
        self.assertIn("AdobePhotoshop27-Core.zip", names)  # arm64
        self.assertNotIn("AdobePhotoshop27-Core_stripped.zip", names)  # x64
        self.assertIn("AdobePhotoshop27-en_US.zip", names)
        self.assertNotIn("AdobePhotoshop27-de_DE.zip", names)
        self.assertIn("AdobePhotoshop27-SuperCafModels.zip", names)  # no condition

    def test_minimal_excludes_non_core(self):
        manifest = load_manifest()
        env = {"OSArchitecture": "arm64", "installLanguage": "en_US"}
        names = [
            p["Path"].split("/")[-1]
            for p in select_packages(manifest, env, include_non_core=False)
        ]
        self.assertNotIn("AdobePhotoshop27-SuperCafModels.zip", names)

    def test_x64_selection(self):
        manifest = load_manifest()
        env = {"OSArchitecture": "x64", "installLanguage": "en_US"}
        names = [p["Path"].split("/")[-1] for p in select_packages(manifest, env)]
        self.assertIn("AdobePhotoshop27-Core_stripped.zip", names)
        self.assertNotIn("AdobePhotoshop27-Core.zip", names)


class HelperTest(unittest.TestCase):
    def test_arch_maps(self):
        self.assertEqual(os_architecture("arm64"), "arm64")
        self.assertEqual(os_architecture("x64"), "x64")
        self.assertEqual(arch_platforms("arm64")[0], "macuniversal")
        self.assertIn("macarm64", arch_platforms("arm64"))
        self.assertIn("osx10-64", arch_platforms("x64"))

    def test_languages_of(self):
        langs = languages_of(load_manifest())
        self.assertIn("en_US", langs)
        self.assertIn("de_DE", langs)


if __name__ == "__main__":
    unittest.main()
