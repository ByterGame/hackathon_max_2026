import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.codegen.contracts import ContractError
from tools.codegen.publication import MANIFEST, publish
from tools.codegen.render import HEADER


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.model = Path("src/gen/example/models.py")
        self.code = HEADER + "\nclass Example: pass\n"

    def test_deleted_document_removes_owned_module_but_not_handler(self):
        handler = Path("src/views/example/get_item.py")
        publish(
            self.root,
            {self.model: self.code, handler: "# handwritten\n"},
            {handler},
            False,
        )
        self.assertTrue(publish(self.root, {}, set(), False))
        self.assertFalse((self.root / self.model).exists())
        self.assertEqual((self.root / handler).read_text(), "# handwritten\n")

    def test_check_reports_deletion_without_writing(self):
        publish(self.root, {self.model: self.code}, set(), False)
        before = (self.root / MANIFEST).read_bytes()
        self.assertTrue(publish(self.root, {}, set(), True))
        self.assertTrue((self.root / self.model).exists())
        self.assertEqual((self.root / MANIFEST).read_bytes(), before)

    def test_domain_first_layout_removes_old_modules(self):
        old_api = Path("src/gen/api/request/client/get_request.py")
        old_internal = Path("src/gen/request/request_data.py")
        new_api = Path("src/gen/request/api/client/get_request.py")
        new_internal = Path("src/gen/request/internal/request_data.py")
        publish(
            self.root,
            {old_api: self.code, old_internal: self.code},
            set(),
            False,
        )
        files = {new_api: self.code, new_internal: self.code}
        publish(self.root, files, set(), False)
        for path in (old_api, old_internal):
            self.assertFalse((self.root / path).exists())
        for path in files:
            self.assertEqual((self.root / path).read_text(), self.code)
        self.assertFalse(publish(self.root, files, set(), False))

    def test_openapi_moves_from_legacy_build_directory(self):
        legacy = Path("build/openapi.yaml")
        current = Path("openapi.yaml")
        publish(self.root, {legacy: self.code}, set(), False)

        publish(self.root, {current: self.code}, set(), False)

        self.assertFalse((self.root / legacy).exists())
        self.assertEqual((self.root / current).read_text(), self.code)
        self.assertFalse(publish(self.root, {current: self.code}, set(), False))

    def test_manual_edit_blocks_all_changes(self):
        publish(self.root, {self.model: self.code}, set(), False)
        changed = self.code + "# manual change\n"
        (self.root / self.model).write_text(changed)
        new = Path("src/gen/example/new.py")
        with self.assertRaisesRegex(ContractError, "изменён вручную"):
            publish(self.root, {new: self.code}, set(), False)
        self.assertFalse((self.root / new).exists())
        self.assertEqual((self.root / self.model).read_text(), changed)

    def test_unmanaged_file_is_not_deleted(self):
        self.model_path = self.root / self.model
        self.model_path.parent.mkdir(parents=True)
        self.model_path.write_text(self.code)
        publish(self.root, {}, set(), False)
        self.assertTrue(self.model_path.exists())

    def test_second_identical_generation_is_unchanged(self):
        publish(self.root, {self.model: self.code}, set(), False)
        self.assertFalse(publish(self.root, {self.model: self.code}, set(), False))

    def test_manifest_cannot_claim_a_handler(self):
        path = self.root / MANIFEST
        path.parent.mkdir(parents=True)
        path.write_text('{"version": 1, "files": {"src/views/example.py": "hash"}}')
        with self.assertRaisesRegex(ContractError, "Недопустимая запись"):
            publish(self.root, {}, set(), False)

    def test_removing_one_class_updates_module(self):
        original = self.code + "class Another: pass\n"
        publish(self.root, {self.model: original}, set(), False)
        publish(self.root, {self.model: self.code}, set(), False)
        self.assertEqual((self.root / self.model).read_text(), self.code)


if __name__ == "__main__":
    unittest.main()
