from __future__ import annotations

import os, tempfile
from pathlib import Path
from typing import Any


def colored(st: str, _color: str) -> str:
  return st


def getenv(name: str, default: Any = 0) -> Any:
  raw = os.getenv(name)
  if raw is None:
    return default
  if isinstance(default, bool):
    return raw.lower() not in {"", "0", "false", "no"}
  if isinstance(default, int):
    try:
      return int(raw)
    except ValueError:
      return default
  if isinstance(default, float):
    try:
      return float(raw)
    except ValueError:
      return default
  return raw


def temp(name: str, append_user: bool = False) -> str:
  suffix = f".{os.getuid()}" if append_user else ""
  return str(Path(tempfile.gettempdir()) / f"{name}{suffix}")

