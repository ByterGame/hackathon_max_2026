import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

from tools.codegen.contracts import ContractError, load_contracts
from tools.codegen.generate import ROOT, main


class CodegenCliTests(unittest.TestCase):
    def setUp(self):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.enterContext(redirect_stdout(self.stdout))
        self.enterContext(redirect_stderr(self.stderr))
        self.validate = self.enterContext(patch("tools.codegen.contracts.validate"))
        self.render = self.enterContext(
            patch(
                "tools.codegen.generate.render_project",
                return_value=({}, set()),
            )
        )
        self.publish = self.enterContext(
            patch("tools.codegen.generate.publish", return_value=False)
        )
        self.sequence = Mock()
        self.sequence.attach_mock(self.validate, "validate")
        self.sequence.attach_mock(self.render, "render")
        self.sequence.attach_mock(self.publish, "publish")

    def test_default_validates_before_rendering_and_publishing(self):
        self.assertEqual(main([]), 0)
        self.assertEqual(
            [entry[0] for entry in self.sequence.mock_calls],
            ["validate", "render", "publish"],
        )
        self.assertFalse(self.publish.call_args.args[3])

    def test_validation_failure_prevents_generation_and_writes(self):
        self.validate.side_effect = OpenAPIValidationError("invalid contract")
        self.assertEqual(main([]), 2)
        self.render.assert_not_called()
        self.publish.assert_not_called()
        self.assertIn("Некорректный OpenAPI", self.stderr.getvalue())

    def test_skip_validation_still_generates(self):
        self.assertEqual(main(["--skip-validation"]), 0)
        self.validate.assert_not_called()
        self.render.assert_called_once()
        self.publish.assert_called_once()
        self.assertIn("Проверка OpenAPI отключена", self.stderr.getvalue())

    def test_check_validates_and_requests_no_writes(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                self.publish.return_value = changed
                self.assertEqual(main(["--check"]), int(changed))
                self.assertTrue(self.publish.call_args.args[3])
        self.assertEqual(self.validate.call_count, 2)

    def test_check_can_skip_validation(self):
        self.assertEqual(main(["--check", "--skip-validation"]), 0)
        self.validate.assert_not_called()
        self.assertTrue(self.publish.call_args.args[3])

    def test_loader_validates_by_default(self):
        contracts = load_contracts(ROOT)
        self.assertEqual(self.validate.call_args, call(contracts.specification))

    def test_skip_validation_does_not_disable_path_checks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "docs/example/api/get_item.yaml"
            source.parent.mkdir(parents=True)
            source.write_text("path: /../escape\nmethod: get\n", encoding="utf-8")
            with self.assertRaises(ContractError):
                load_contracts(root, validate_specification=False)


if __name__ == "__main__":
    unittest.main()
