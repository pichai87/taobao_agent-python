"""Smoke checks for the project environment and installed package."""

import importlib
import importlib.metadata
import sys
import unittest


class ProjectSetupTest(unittest.TestCase):
    def test_project_python_version(self) -> None:
        self.assertEqual(sys.version_info[:2], (3, 13))

    def test_package_can_be_imported(self) -> None:
        package = importlib.import_module("taobao_agent")
        self.assertIsNotNone(package.__file__)
        self.assertEqual(importlib.metadata.version("taobao-agent"), "0.1.0")


if __name__ == "__main__":
    unittest.main()
