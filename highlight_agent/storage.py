"""Atomic local manifests; no media or credentials are committed to Git."""

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel


def write_json(path: Path, value: BaseModel | dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    descriptor, name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path):
    return json.loads(path.read_text())
