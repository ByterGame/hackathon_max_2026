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

    def test_returns_next_image_in_order(self) -> None:
        images = ["first.jpg", "second.jpg", "third.jpg"]

        self.assertEqual(choose_next_image(images, "first.jpg"), "second.jpg")
        self.assertEqual(choose_next_image(images, "second.jpg"), "third.jpg")

    def test_wraps_to_first_image(self) -> None:
        images = ["first.jpg", "second.jpg", "third.jpg"]

        self.assertEqual(choose_next_image(images, "third.jpg"), "first.jpg")

    def test_starts_with_first_image(self) -> None:
        images = ["first.jpg", "second.jpg"]

        self.assertEqual(choose_next_image(images), "first.jpg")
        self.assertEqual(choose_next_image(images, "missing.jpg"), "first.jpg")

    def test_empty_pool_is_reported(self) -> None:
        self.assertIsNone(choose_next_image([]))

    def test_single_image_cycles_to_itself(self) -> None:
        self.assertEqual(choose_next_image(["only.png"], "only.png"), "only.png")


if __name__ == "__main__":
    unittest.main()
