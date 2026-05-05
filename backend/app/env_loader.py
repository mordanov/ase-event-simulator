from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path

try:
    from dotenv import dotenv_values, load_dotenv
except ImportError:  # pragma: no cover - fallback for minimal runtime images

    def load_dotenv(*args, **kwargs):
        return False

    def dotenv_values(stream=None, **kwargs):
        values: dict[str, str] = {}
        if stream is None:
            return values
        for raw_line in stream:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values


def _load_dotenv_text_from_env() -> None:
    # Supports injecting full dotenv text (or JSON object) from Secrets Manager.
    raw = (
        os.getenv("DOTENV_CONTENT")
        or os.getenv("ENV_FILE_CONTENT")
        or os.getenv("APP_CONFIG_SECRET")
    )
    if not raw:
        return

    payload = raw.strip()
    if not payload:
        return

    if payload.startswith("{"):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            for key, value in data.items():
                if value is not None:
                    os.environ.setdefault(str(key), str(value))
            return

    for key, value in dotenv_values(stream=StringIO(payload)).items():
        if value is not None:
            os.environ.setdefault(key, value)


def bootstrap_environment() -> None:
    root = Path(__file__).resolve().parents[1]

    explicit_path = os.getenv("ENV_FILE") or os.getenv("DOTENV_PATH")
    if explicit_path:
        load_dotenv(dotenv_path=explicit_path, override=False)

    load_dotenv(dotenv_path=root / ".env", override=False)
    load_dotenv(dotenv_path=root / ".env.local", override=False)
    _load_dotenv_text_from_env()
