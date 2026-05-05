"""Environment bootstrap helpers for local files and injected secret payloads."""

from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path

DOTENV_TEXT_ENV_VARS = ("DOTENV_CONTENT", "ENV_FILE_CONTENT", "APP_CONFIG_SECRET")

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


def _set_env_defaults(values: dict[str, object]) -> None:
    for key, value in values.items():
        if value is not None:
            os.environ.setdefault(str(key), str(value))


def _read_env_embedded_config() -> str:
    for var_name in DOTENV_TEXT_ENV_VARS:
        raw_value = os.getenv(var_name)
        if raw_value:
            return raw_value
    return ""


def _parse_embedded_json(payload: str) -> dict[str, object] | None:
    if not payload.startswith("{"):
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _load_dotenv_text_from_env() -> None:
    # Supports full dotenv text or JSON object payloads from environment-backed secrets.
    raw_config = _read_env_embedded_config()
    if not raw_config:
        return

    payload = raw_config.strip()
    if not payload:
        return

    json_payload = _parse_embedded_json(payload)
    if json_payload is not None:
        _set_env_defaults(json_payload)
        return

    _set_env_defaults(dotenv_values(stream=StringIO(payload)))


def bootstrap_environment() -> None:
    root = Path(__file__).resolve().parents[1]

    explicit_path = os.getenv("ENV_FILE") or os.getenv("DOTENV_PATH")
    if explicit_path:
        load_dotenv(dotenv_path=explicit_path, override=False)

    load_dotenv(dotenv_path=root / ".env", override=False)
    load_dotenv(dotenv_path=root / ".env.local", override=False)
    _load_dotenv_text_from_env()
