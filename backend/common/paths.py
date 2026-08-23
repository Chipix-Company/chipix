import os
from pathlib import Path


def outputs_dir() -> Path:
    root = os.getenv("CHIPVERIFY_OUTPUTS_DIR")
    if root:
        return Path(root)
    return Path(__file__).resolve().parents[2] / "outputs"
