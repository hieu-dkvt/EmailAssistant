"""Ghi OpenAPI của app ra file (không cần DB) cho client web: npm run gen:api.

uv run --directory backend python -m app.openapi_dump ../web/openapi.json
"""

import json
import sys
from pathlib import Path

from app.main import create_app


def openapi_json() -> str:
    return json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Cách dùng: python -m app.openapi_dump <file.json>", file=sys.stderr)
        return 2
    Path(args[0]).write_text(openapi_json(), encoding="utf-8", newline="\n")
    print(f"Đã ghi OpenAPI vào {args[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
