"""Export the OpenAPI contract of the actual FastAPI app for submission."""

import argparse
import json
from pathlib import Path

from src.main import create_app


OUTPUT = Path(__file__).resolve().parents[2] / "openapi.json"
DEFAULT_SERVER_URL = "https://168.113.209.86"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if openapi.json is outdated")
    parser.add_argument(
        "--server-url",
        default=DEFAULT_SERVER_URL,
        help="Public HTTPS API address to include in the exported schema",
    )
    args = parser.parse_args()

    schema = create_app().openapi()
    schema["servers"] = [{"url": args.server_url.rstrip("/")}]
    content = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != content:
            parser.exit(1, "openapi.json устарел; запустите python -m tools.export_runtime_openapi\n")
        print(f"openapi.json актуален: {len(schema['paths'])} маршрутов")
        return
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Создан {OUTPUT.name}: OpenAPI {schema['openapi']}, {len(schema['paths'])} маршрутов")


if __name__ == "__main__":
    main()
