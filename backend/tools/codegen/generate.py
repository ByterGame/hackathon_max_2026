"""CLI: проверка контрактов, генерация моделей и создание новых обработчиков."""

import argparse
import sys
from pathlib import Path

import yaml

from .contracts import load_contracts
from .publication import publish
from .render import HEADER, render_project

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Пропустить проверку OpenAPI; по умолчанию она выполняется до генерации",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Сравнить результат генерации с файлами без записи",
    )
    args = parser.parse_args(argv)
    try:
        if args.skip_validation:
            print("Проверка OpenAPI отключена (--skip-validation).", file=sys.stderr)
        contracts = load_contracts(
            ROOT, validate_specification=not args.skip_validation
        )
        print(
            f"Ручек: {len(contracts.endpoints)}; схем: {len(contracts.specification['components']['schemas'])}"
        )
        files, create_only = render_project(ROOT, contracts)
        files[Path("openapi.yaml")] = (
            HEADER
            + "\n"
            + yaml.safe_dump(
                contracts.specification, allow_unicode=True, sort_keys=False
            )
        )
        changed = publish(ROOT, files, create_only, args.check)
        return 1 if args.check and changed else 0
    except (ValueError, OSError, SyntaxError) as error:
        print(f"Ошибка кодогенерации: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
