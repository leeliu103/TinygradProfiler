from __future__ import annotations

import importlib.resources as resources
import json, shutil
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from ._timeline_bin import write_profile_bin

if TYPE_CHECKING:
  from ._orchestrator import TraceData, TraceEntry


class _QuietHTTPRequestHandler(SimpleHTTPRequestHandler):
  def log_message(self, format: str, *args) -> None:  # noqa: A003
    pass


def _select_trace(trace_data: "TraceData", se: int, cu: int, simd: int) -> "TraceEntry":
  matches = [trace for trace in trace_data["traces"] if trace["se"] == se and trace["cu"] == cu and trace["simd"] == simd]
  if not matches:
    raise ValueError(f"no trace found for se={se} cu={cu} simd={simd}")
  if len(matches) > 1:
    raise ValueError(f"multiple traces found for se={se} cu={cu} simd={simd}")
  return matches[0]


def _copy_web_assets(output_dir: Path) -> None:
  output_dir.mkdir(parents=True, exist_ok=True)
  web_root = resources.files("tinygrad_profiler").joinpath("web")
  with resources.as_file(web_root) as src:
    shutil.copytree(src, output_dir, dirs_exist_ok=True)


def _write_metadata(path: Path, *, title: str | None, target: str, kernel_name: str, kernel_iteration: int, se: int, cu: int, simd: int) -> Path:
  metadata = {
    "title": title or "TinygradProfiler PKTS",
    "target": target,
    "kernel_name": kernel_name,
    "kernel_iteration": kernel_iteration,
    "se": se,
    "cu": cu,
    "simd": simd,
  }
  path.write_text(json.dumps(metadata, indent=2) + "\n")
  return path


def build_web_bundle(trace_data: "TraceData", output_dir: str | Path, *, kernel_name: str, kernel_iteration: int, se: int, cu: int, simd: int,
                     title: str | None = None) -> Path:
  trace = _select_trace(trace_data, se=se, cu=cu, simd=simd)
  output = Path(output_dir)
  _copy_web_assets(output)
  write_profile_bin(output / "timeline.bin", trace["events"])
  _write_metadata(output / "metadata.json", title=title, target=trace_data["target"], kernel_name=kernel_name,
                  kernel_iteration=kernel_iteration, se=trace["se"], cu=trace["cu"], simd=trace["simd"])
  return output


def start_web_server(bundle_dir: str | Path, *, host: str = "0.0.0.0", port: int = 8001) -> ThreadingHTTPServer:
  bundle_path = Path(bundle_dir).resolve()
  if not bundle_path.is_dir():
    raise FileNotFoundError(f"bundle directory does not exist: {bundle_path}")
  handler = partial(_QuietHTTPRequestHandler, directory=str(bundle_path))
  return ThreadingHTTPServer((host, port), handler)


def serve_web_bundle(bundle_dir: str | Path, *, host: str = "0.0.0.0", port: int = 8001) -> None:
  server = start_web_server(bundle_dir, host=host, port=port)
  try:
    server.serve_forever()
  finally:
    server.server_close()
