import tempfile
import unittest
from pathlib import Path

from src.domain.image_pool import choose_next_image, list_images


class ImagePoolTestCase(unittest.TestCase):
    def test_lists_only_supported_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for filename in ("second.webp", "first.JPG", "notes.txt"):
                (directory / filename).touch()

            self.assertEqual(list_images(directory), ["first.JPG", "second.webp"])

    def test_cycles_through_images(self) -> None:
        images = ["first.jpg", "second.jpg", "third.jpg"]

        self.assertEqual(choose_next_image(images), "first.jpg")
        self.assertEqual(choose_next_image(images, "first.jpg"), "second.jpg")
        self.assertEqual(choose_next_image(images, "second.jpg"), "third.jpg")
        self.assertEqual(choose_next_image(images, "third.jpg"), "first.jpg")
        self.assertEqual(choose_next_image(images, "missing.jpg"), "first.jpg")
        self.assertIsNone(choose_next_image([]))


if __name__ == "__main__":
    unittest.main()
