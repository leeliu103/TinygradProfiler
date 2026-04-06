from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .serialize import dump_events

if TYPE_CHECKING:
  from ._orchestrator import TraceData, TraceEntry


def _select_trace(trace_data: "TraceData", se: int, cu: int, simd: int) -> "TraceEntry":
  matches = [trace for trace in trace_data["traces"] if trace["se"] == se and trace["cu"] == cu and trace["simd"] == simd]
  if not matches:
    raise ValueError(f"no trace found for se={se} cu={cu} simd={simd}")
  if len(matches) > 1:
    raise ValueError(f"multiple traces found for se={se} cu={cu} simd={simd}")
  return matches[0]


def _write_metadata(path: Path, *, target: str, kernel_name: str, kernel_iteration: int, se: int, cu: int, simd: int, event_count: int) -> Path:
  metadata = {
    "target": target,
    "kernel_name": kernel_name,
    "kernel_iteration": kernel_iteration,
    "se": se,
    "cu": cu,
    "simd": simd,
    "event_count": event_count,
  }
  path.write_text(json.dumps(metadata, indent=2) + "\n")
  return path


def build_agent_bundle(trace_data: "TraceData", output_dir: str | Path, *, kernel_name: str, kernel_iteration: int, se: int, cu: int, simd: int) -> Path:
  trace = _select_trace(trace_data, se=se, cu=cu, simd=simd)
  output = Path(output_dir)
  output.mkdir(parents=True, exist_ok=True)
  dump_events(output / "events.json", trace["events"])
  _write_metadata(output / "metadata.json", target=trace_data["target"], kernel_name=kernel_name, kernel_iteration=kernel_iteration,
                  se=trace["se"], cu=trace["cu"], simd=trace["simd"], event_count=len(trace["events"]))
  return output
