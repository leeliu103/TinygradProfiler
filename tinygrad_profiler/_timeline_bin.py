from __future__ import annotations

import json, struct
from decimal import Decimal
from pathlib import Path
from typing import Any, Generator, Iterable

from .events import ProfileEvent, ProfilePointEvent, ProfileRangeEvent, TracingKey

DevEvent = ProfileRangeEvent | ProfilePointEvent


def enum_str(s: str, cache: dict[str, int]) -> int:
  if (cached := cache.get(s)) is not None:
    return cached
  cache[s] = ret = len(cache)
  return ret


def option(value: int | None) -> int:
  return 0 if value is None else value + 1


def rel_ts(ts: int | Decimal, start_ts: int, ctx: str = "") -> int:
  value = int(ts) - start_ts
  if value < 0 or value > 0xFFFFFFFF:
    raise ValueError(f"timestamp out of range: {ctx} diff={value} (ts={ts} start={start_ts})")
  return value


def row_tuple(row: str) -> tuple[tuple[int, int], ...]:
  if "Clock" in row:
    return ((0, 0),)
  return tuple((ord(parts[0][0]), int(parts[1])) if len(parts := chunk.split(":")) > 1 else (999, 999) for chunk in row.split())


def flatten_events(profile: Iterable[ProfileEvent]) -> Generator[tuple[Decimal, Decimal, DevEvent], None, None]:
  for event in profile:
    if isinstance(event, ProfileRangeEvent):
      yield event.st, (event.en if event.en is not None else event.st), event
    elif isinstance(event, ProfilePointEvent):
      yield event.ts, event.ts, event
    else:
      raise TypeError(f"unsupported event type for standalone PKTS packer: {type(event).__name__}")


def timeline_layout(dev_events: list[tuple[int, int, float, DevEvent]], start_ts: int, scache: dict[str, int]) -> bytes | None:
  events: list[bytes] = []
  for st, _, dur, event in dev_events:
    if dur == 0:
      continue
    name: str | TracingKey = event.name
    ref: int | None = None
    key: int | None = None
    fmt: list[str] = []
    if isinstance(name, TracingKey):
      if isinstance(name.ret, str):
        fmt.append(name.ret)
      elif isinstance(name.ret, int):
        membw = name.ret / (dur * 1e-6)
        fmt.append(f"{membw*1e-9:.0f} GB/s" if membw < 1e13 else f"{membw*1e-12:.0f} TB/s")
      elif name.tb:
        fmt.append("TB:" + json.dumps(name.tb))
      name = name.display_name
    events.append(struct.pack("<IIIIfI", enum_str(name, scache), option(ref), option(key), rel_ts(st, start_ts, f"'{name}' on {event.device}"),
                              dur, enum_str("\n".join(fmt), scache)))
  return struct.pack("<BI", 0, len(events)) + b"".join(events) if events else None


def graph_layout(row: str, dev_events: list[tuple[int, int, float, DevEvent]], start_ts: int, peaks: list[int]) -> tuple[str, bytes | None]:
  if not row.startswith("LINE:"):
    return f"{row} Memory", None
  xy = [(rel_ts(event.ts, start_ts, f"line '{row}' on {event.device}"), int(event.key))
        for _, _, _, event in dev_events if isinstance(event, ProfilePointEvent)]
  if not xy:
    return row.replace("LINE:", ""), None
  peaks.append(peak := max(y for _, y in xy))
  return row.replace("LINE:", ""), struct.pack("<BIBQ", 1, len(xy), 1, peak) + b"".join(struct.pack("<IQ", x, y) for x, y in xy)


def pack_profile(events: Iterable[ProfileEvent]) -> bytes | None:
  dev_events: dict[str, list[tuple[int, int, float, DevEvent]]] = {}
  markers: list[ProfilePointEvent] = []
  ext_data: dict[str, Any] = {}
  start_ts: int | None = None
  end_ts: int | None = None

  for ts, en, event in flatten_events(events):
    st_i, en_i = int(ts), int(en)
    dev_events.setdefault(event.device, []).append((st_i, en_i, float(en - ts), event))
    if start_ts is None or st_i < start_ts:
      start_ts = st_i
    if end_ts is None or en_i > end_ts:
      end_ts = en_i
    if isinstance(event, ProfilePointEvent) and event.name == "marker":
      markers.append(event)
    if isinstance(event, ProfilePointEvent) and event.name == "JSON":
      ext_data[str(event.key)] = event.arg

  if start_ts is None or end_ts is None:
    return None

  layout: dict[str, bytes | None] = {}
  scache: dict[str, int] = {}
  peaks: list[int] = []
  for row, row_events in dev_events.items():
    row_events.sort(key=lambda item: item[0])
    layout[row] = timeline_layout(row_events, start_ts, scache)
    extra_name, extra_layout = graph_layout(row, row_events, start_ts, peaks)
    layout[extra_name] = extra_layout

  rows = sorted([row for row, blob in layout.items() if blob is not None], key=row_tuple)
  payload = [b"".join([struct.pack("<B", len(row)), row.encode(), layout[row]]) for row in rows]
  index = json.dumps({
    "strings": list(scache),
    "dtypeSize": {},
    "markers": [{"ts": rel_ts(marker.ts, start_ts, f"marker '{marker.arg.get('name', '?')}'"), **marker.arg} for marker in markers],
    **ext_data,
  }).encode()
  return struct.pack("<IQII", rel_ts(end_ts, start_ts, "end_ts"), max(peaks, default=0), len(index), len(payload)) + index + b"".join(payload)


def write_profile_bin(path: str | Path, events: Iterable[ProfileEvent]) -> Path:
  output = Path(path)
  blob = pack_profile(events)
  if blob is None:
    raise ValueError("no events to encode")
  output.write_bytes(blob)
  return output
