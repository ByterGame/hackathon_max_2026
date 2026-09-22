"""Запись результата и удаление устаревших файлов, принадлежащих генератору."""

import hashlib
import json
from pathlib import Path

from .contracts import ContractError
from .render import HEADER

MANIFEST = Path("src/gen/manifest.json")


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def managed_path(path: Path) -> bool:
    return path in {
        Path("src/views/_generated_router.py"),
        Path("openapi.yaml"),
        # Старый путь нужен для удаления файла при первой генерации после переноса.
        Path("build/openapi.yaml"),
    } or (
        path.is_relative_to("src/gen")
        and path.suffix == ".py"
        and path.name != "__init__.py"
    )


def target_path(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise ContractError(f"Недопустимый выходной путь: {relative}")
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ContractError(f"Выходной путь покидает проект: {relative}")
    if any(
        path.is_symlink()
        for path in [target, *target.parents]
        if path != root and path.is_relative_to(root)
    ):
        raise ContractError(f"Символическая ссылка в выходном пути: {relative}")
    return target


def read_manifest(root: Path) -> dict[str, str]:
    path = target_path(root, MANIFEST)
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        raise ContractError(f"Не удалось прочитать {MANIFEST}: {error}") from error
    if (
        not isinstance(document, dict)
        or document.get("version") != 1
        or not isinstance(document.get("files"), dict)
    ):
        raise ContractError(f"Неизвестный формат {MANIFEST}")
    files = document["files"]
    for name, checksum in files.items():
        if not managed_path(Path(name)) or not isinstance(checksum, str):
            raise ContractError(f"Недопустимая запись в {MANIFEST}: {name}")
        target_path(root, Path(name))
    return files


def publish(
    root: Path, files: dict[Path, str], create_only: set[Path], check: bool
) -> bool:
    previous_files = read_manifest(root)
    managed = {
        str(path): digest(content)
        for path, content in files.items()
        if path not in create_only
    }
    for name in managed:
        if not managed_path(Path(name)):
            raise ContractError(f"Генератор не должен владеть файлом {name}")

    deletions = []
    # Проверяем изменения человека до любой записи или удаления.
    for name, checksum in previous_files.items():
        target = target_path(root, Path(name))
        if not target.exists():
            continue
        content = target.read_text(encoding="utf-8")
        if not content.startswith(HEADER) or digest(content) != checksum:
            raise ContractError(
                f"Сгенерированный файл изменён вручную: {name}. Автоматическая перезапись/удаление отменены."
            )
        if name not in managed:
            deletions.append(target)

    changes = []
    for relative, content in sorted(files.items()):
        target = target_path(root, relative)
        if target.exists():
            if relative in create_only:
                continue
            previous = target.read_text(encoding="utf-8")
            if previous == content:
                continue
            if not previous.startswith(HEADER):
                raise ContractError(
                    f"Отказ перезаписывать файл без метки генератора: {relative}"
                )
        changes.append((target, content))

    manifest_content = (
        json.dumps({"version": 1, "files": managed}, indent=2, sort_keys=True) + "\n"
    )
    manifest_path = target_path(root, MANIFEST)
    manifest_changed = (
        not manifest_path.exists()
        or manifest_path.read_text(encoding="utf-8") != manifest_content
    )
    for target, content in changes:
        print(f"{'would write' if check else 'write'} {target.relative_to(root)}")
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    for target in deletions:
        print(f"{'would delete' if check else 'delete'} {target.relative_to(root)}")
        if not check:
            target.unlink()
    if manifest_changed:
        print(f"{'would write' if check else 'write'} {MANIFEST}")
        if not check:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(manifest_content, encoding="utf-8")
    return bool(changes or deletions or manifest_changed)
