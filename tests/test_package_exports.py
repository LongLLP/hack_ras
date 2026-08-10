# tests/test_package_exports.py
"""The top-level package re-exports the project-operations modules, so a
script can do `from hack_ras import RasProject, plans` instead of one import
line per subpackage. These pin that surface: a re-export that silently stops
being the same module object would give a caller two diverging views of it.
"""
import unittest

import hack_ras
from hack_ras.project import flows, geoms, health, plans, rasmap, sync


class TestPackageExports(unittest.TestCase):
    def test_reexported_modules_are_the_same_objects(self):
        for name, module in (("plans", plans), ("geoms", geoms),
                             ("flows", flows), ("sync", sync),
                             ("health", health), ("rasmap", rasmap)):
            self.assertIs(getattr(hack_ras, name), module, name)

    def test_all_matches_what_is_actually_exported(self):
        self.assertEqual(
            sorted(hack_ras.__all__),
            ["RasProject", "flows", "geoms", "health", "plans", "rasmap",
             "sync"],
        )
        for name in hack_ras.__all__:
            self.assertTrue(hasattr(hack_ras, name), name)


if __name__ == "__main__":
    unittest.main()
