"""Writes the API's OpenAPI schema to a file.

Feeds `npm run generate:api-types` in the frontend, so the TypeScript
types are derived from the actual route definitions instead of being
hand-maintained alongside them. Run from backend/:

    python -m scripts.export_openapi ../frontend/lib/openapi.json
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

# Importing the app requires settings to be present, but nothing here ever
# connects to anything - placeholders keep the export runnable in CI.
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "-Fh2wq9s0V1z7QpM3Yv8Jk6XoN4Rr5Td2Ac1Bw0EeGs=")
os.environ.setdefault("ANTHROPIC_API_KEY", "export-only")
os.environ.setdefault("VOYAGE_API_KEY", "export-only")


def main() -> None:
    from app.main import app

    destination = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n")
    print(f"OpenAPI-Schema geschrieben: {destination}")


if __name__ == "__main__":
    main()
