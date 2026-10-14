import os
import tempfile
import unittest
from typing import override

from fuckcc import config


class ConfigTest(unittest.TestCase):
    @override
    def setUp(self):
        self._old = os.environ.get("FUCKCC_HOME")
        self._tmp = tempfile.mkdtemp(prefix="fuckcc-test-")

    @override
    def tearDown(self):
        if self._old is None:
            os.environ.pop("FUCKCC_HOME", None)
        else:
            os.environ["FUCKCC_HOME"] = self._old

    def test_env_override(self):
        os.environ["FUCKCC_HOME"] = self._tmp
        self.assertEqual(config.home(), self._tmp)

    def test_paths_are_under_home(self):
        os.environ["FUCKCC_HOME"] = self._tmp
        p = config.paths()
        self.assertEqual(p["home"], self._tmp)
        for key in ("cache", "stage", "engine", "log"):
            self.assertTrue(p[key].startswith(self._tmp))

    def test_ensure_dirs(self):
        os.environ["FUCKCC_HOME"] = self._tmp
        p = config.ensure_dirs()
        for key in ("cache", "stage", "engine", "log"):
            self.assertTrue(os.path.isdir(p[key]))

    def test_expanduser(self):
        os.environ["FUCKCC_HOME"] = "~/fuckcc-test-home"
        self.assertTrue(config.home().startswith(os.path.expanduser("~")))


if __name__ == "__main__":
    unittest.main()
