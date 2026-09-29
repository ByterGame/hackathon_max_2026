import tempfile
import unittest
from pathlib import Path

from src.domain.files.storage import (
    MAX_FILE_BYTES,
    FileError,
    detect_mime,
    normalize_name,
    path_for_key,
    store_stream,
)


PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 40


async def chunks(*values: bytes):
    for value in values:
        yield value


class FileStorageTests(unittest.IsolatedAsyncioTestCase):
    def test_signatures_and_filename(self) -> None:
        self.assertEqual(detect_mime(PNG), "image/png")
        self.assertEqual(detect_mime(b"%PDF-1.7\n"), "application/pdf")
        self.assertEqual(normalize_name("../photo.png", "image/png"), "photo.png")
        with self.assertRaises(FileError):
            normalize_name("photo.html", "image/png")
        with self.assertRaises(FileError):
            path_for_key(Path("/private"), "../../etc/passwd")

    async def test_atomic_write_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stored, name = await store_stream(
                root, chunks(PNG[:8], PNG[8:]),
                filename="proof.png", content_type="image/png",
            )
            self.assertEqual(name, "proof.png")
            self.assertEqual(stored.size_bytes, len(PNG))
            self.assertEqual(path_for_key(root, stored.key).read_bytes(), PNG)
            self.assertEqual(len(list(root.rglob("*.part"))), 0)

    async def test_rejects_spoofed_or_oversized_file_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileError) as spoofed:
                await store_stream(
                    root, chunks(b"<script>bad</script>"),
                    filename="photo.png", content_type="image/png",
                )
            self.assertEqual(spoofed.exception.status_code, 415)
            with self.assertRaises(FileError) as oversized:
                await store_stream(
                    root, chunks(PNG, b"a" * MAX_FILE_BYTES),
                    filename="photo.png", content_type="image/png",
                )
            self.assertEqual(oversized.exception.status_code, 413)
            self.assertEqual(list(root.rglob("*.part")), [])
            self.assertEqual(list(root.rglob("[0-9a-f]" * 64)), [])


if __name__ == "__main__":
    unittest.main()
