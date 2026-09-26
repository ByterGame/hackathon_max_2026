import unittest
from pathlib import Path

from tools.codegen.contracts import Contracts
from tools.codegen.generate import ROOT
from tools.codegen.render import render_project


class CodegenLayoutTests(unittest.TestCase):
    def test_outputs_are_relative_to_backend(self):
        contracts = Contracts({"components": {"schemas": {}}}, [])
        files, _ = render_project(ROOT, contracts)
        self.assertIn(Path("src/views/_generated_router.py"), files)
        self.assertTrue(all(path.is_relative_to("src") for path in files))


if __name__ == "__main__":
    unittest.main()
