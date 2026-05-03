from __future__ import annotations

import os


_TRUTHY = {"1", "true", "yes", "y", "on"}
_FALSY = {"0", "false", "no", "n", "off"}


def _normalized(name: str) -> str:
    return os.getenv(name, "").strip().lower()


def _bool_from_env(name: str) -> bool | None:
    value = _normalized(name)
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return None


def is_cloud_mode() -> bool:
    """
    Determine whether the simulator is running in a cloud deployment.

    Precedence:
      1. Explicit override via SIMULATOR_CLOUD_MODE=true/false
      2. Common environment labels (APP_ENV / ENVIRONMENT / SIMULATOR_ENV)
      3. ECS/AWS runtime markers
    """
    explicit = _bool_from_env("SIMULATOR_CLOUD_MODE")
    if explicit is not None:
        return explicit

    for name in ("APP_ENV", "ENVIRONMENT", "SIMULATOR_ENV"):
        value = _normalized(name)
        if value in {"prod", "production", "cloud", "aws"}:
            return True
        if value in {"local", "dev", "development", "test"}:
            return False

    # ECS tasks typically expose one of these metadata env vars.
    if os.getenv("ECS_CONTAINER_METADATA_URI") or os.getenv("ECS_CONTAINER_METADATA_URI_V4"):
        return True

    # AWS-managed runtimes frequently expose this marker.
    if os.getenv("AWS_EXECUTION_ENV"):
        return True

    return False

