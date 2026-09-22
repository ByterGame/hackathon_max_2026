import ast
import unittest
from pathlib import Path

from tools.codegen.contracts import load_contracts
from tools.codegen.generate import ROOT
from tools.codegen.render import absolute_model_imports, render_project


class ModelImportTests(unittest.TestCase):
    def test_relative_import_becomes_absolute(self):
        source = (
            "# comment\nfrom ...internal.request_data import RequestStatus as Status\n"
        )
        result = absolute_model_imports(
            Path("gen/request/api/client/get_request.py"), source
        )
        self.assertEqual(
            result,
            "# comment\nfrom src.gen.request.internal.request_data import RequestStatus as Status\n",
        )

    def test_same_package_import_in_initializer(self):
        result = absolute_model_imports(
            Path("gen/example/__init__.py"), "from .models import Example\n"
        )
        self.assertEqual(result, "from src.gen.example.models import Example\n")

    def test_models_are_in_gen_and_handlers_use_absolute_imports(self):
        contracts = load_contracts(ROOT)
        files, _ = render_project(ROOT, contracts)
        for endpoint in contracts.endpoints:
            with self.subTest(path=endpoint.path):
                model_path = Path(
                    "src", *endpoint.models_module.split(".")
                ).with_suffix(".py")
                domain, *remaining = endpoint.path.strip("/").split("/")
                self.assertEqual(
                    model_path,
                    Path("src/gen", domain, "api", *remaining).with_suffix(".py"),
                )
                self.assertIn(model_path, files)
                nodes = ast.walk(ast.parse(files[model_path]))
                self.assertTrue(
                    all(
                        node.level == 0
                        for node in nodes
                        if isinstance(node, ast.ImportFrom)
                    )
                )
                handler_path = Path("src", *endpoint.module.split(".")).with_suffix(
                    ".py"
                )
                self.assertIn(f"from src.gen.{domain}.api", files[handler_path])
        self.assertFalse(
            any(
                path.name.endswith("_models.py") and path.is_relative_to("src/views")
                for path in files
            )
        )


if __name__ == "__main__":
    unittest.main()
