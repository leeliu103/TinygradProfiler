from __future__ import annotations

import dataclasses, decimal, json
from pathlib import Path
from typing import Any, Iterable

from .events import ProfileEvent


def _normalize(obj: Any) -> Any:
  if isinstance(obj, decimal.Decimal):
    return int(obj) if obj == obj.to_integral_value() else str(obj)
  if dataclasses.is_dataclass(obj):
    return {field.name: _normalize(getattr(obj, field.name)) for field in dataclasses.fields(obj)}
  if isinstance(obj, dict):
    return {str(k): _normalize(v) for k, v in obj.items()}
  if isinstance(obj, (list, tuple)):
    return [_normalize(x) for x in obj]
  return obj


def serialize_event(event: ProfileEvent) -> dict[str, Any]:
  data = _normalize(event)
  data["type"] = type(event).__name__
  return data


def serialize_events(events: Iterable[ProfileEvent]) -> list[dict[str, Any]]:
  return [serialize_event(event) for event in events]


def dump_events(path: str | Path, events: Iterable[ProfileEvent]) -> None:
  Path(path).write_text(json.dumps(serialize_events(events), indent=2, sort_keys=True) + "\n")

