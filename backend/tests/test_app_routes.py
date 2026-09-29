import unittest

from src.main import create_app


class AppRoutesTests(unittest.TestCase):
    def test_legacy_demo_routes_are_not_exposed(self) -> None:
        paths = create_app().openapi()["paths"]
        self.assertIn("/service/health", paths)
        self.assertIn("/service/ready", paths)
        self.assertIn("/issues/create_card", paths)
        self.assertFalse(any(path.startswith("/images/") for path in paths))
        self.assertFalse(any(path.startswith("/example_codegen/") for path in paths))


if __name__ == "__main__":
    unittest.main()
