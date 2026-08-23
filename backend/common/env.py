import os


def env_bool(name: str, default: bool = False) -> bool:
    """Return a boolean parsed from an environment variable.

    Truthy values: 1, true, yes, on (case-insensitive).
    Falsy values: anything else when set, including 0/false/no/off.
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        return default
