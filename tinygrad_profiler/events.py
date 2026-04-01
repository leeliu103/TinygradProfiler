from __future__ import annotations

import decimal
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TracingKey:
  display_name: str
  keys: tuple[Any, ...] = ()
  ret: Any = None
  tb: tuple[tuple, ...] | None = None


class ProfileEvent: pass


@dataclass
class ProfileRangeEvent(ProfileEvent):
  device: str
  name: str | TracingKey
  st: decimal.Decimal
  en: decimal.Decimal | None = None


@dataclass(frozen=True)
class ProfilePointEvent(ProfileEvent):
  device: str
  name: str
  key: Any
  arg: Any = field(default_factory=dict)
  ts: decimal.Decimal = field(default_factory=lambda: decimal.Decimal(0))

