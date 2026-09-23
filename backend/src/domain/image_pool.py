from pathlib import Path


SUPPORTED_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
IMAGE_DIRECTORY = Path(__file__).resolve().parents[2] / "temp"


def list_images(directory: Path = IMAGE_DIRECTORY) -> list[str]:
    return sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    )


def choose_next_image(images: list[str], current: str | None = None) -> str | None:
    if not images:
        return None
    if current not in images:
        return images[0]
    current_index = images.index(current)
    return images[(current_index + 1) % len(images)]
