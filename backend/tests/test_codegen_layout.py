import unittest
from pathlib import Path

from tools.codegen.contracts import Contracts
from tools.codegen.generate import ROOT
from tools.codegen.render import render_project, templates


class CodegenLayoutTests(unittest.TestCase):
    def test_root_is_backend(self):
        self.assertEqual(ROOT, Path(__file__).resolve().parents[1])
        self.assertTrue((ROOT / "docs/example_codegen/api").is_dir())
        self.assertTrue((ROOT / "docs/example_codegen/internal").is_dir())

    def test_outputs_are_relative_to_backend(self):
        contracts = Contracts({"components": {"schemas": {}}}, [])
        files, _ = render_project(ROOT, contracts)
        self.assertIn(Path("src/views/_generated_router.py"), files)
        self.assertTrue(all(path.is_relative_to("src") for path in files))

    def test_templates_are_available_after_move(self):
        environment = templates()
        self.assertIsNotNone(environment.get_template("handler.py.jinja"))
        self.assertIsNotNone(environment.get_template("router.py.jinja"))


if __name__ == "__main__":
    unittest.main()
