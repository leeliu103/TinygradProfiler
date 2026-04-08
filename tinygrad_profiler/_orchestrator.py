from __future__ import annotations

import datetime, os, re, secrets, shlex, shutil, sqlite3, subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from .events import ProfileEvent
from .timeline import CodeRegion, decode_att_file


class TraceEntry(TypedDict):
  se: int
  cu: int
  simd: int
  events: list[ProfileEvent]


class TraceData(TypedDict):
  target: str
  traces: list[TraceEntry]


@dataclass(frozen=True)
class KernelSummary:
  raw_name: str
  display_name: str
  dispatch_count: int


@dataclass(frozen=True)
class CaptureCandidate:
  dispatch_id: int
  kernel_id: int
  kernel_name: str
  se: int
  target: str
  att_path: Path
  codeobj_path: Path
  code_region: CodeRegion


_ATT_RE = re.compile(r"(?P<pid>\d+)_(?P<agent>\d+)_shader_engine_(?P<se>\d+)_(?P<dispatch>\d+)\.att$")
_OUT_RE = re.compile(r"(?P<pid>\d+)_(?P<target>gfx\d+)_code_object_id_(?P<codeobj>\d+)\.out$")


class KernelSelectionError(RuntimeError):
  pass


def orchestrate_capture(argv: list[str], kernel_iteration: int, se: int, simd: int, cu: int = 0, kernel_filter: str | None = None,
                        runs_root: Path | None = None, cwd: str | Path | None = None) -> tuple[TraceData, Path, KernelSummary]:
  if not argv:
    raise ValueError("argv must not be empty")
  if kernel_iteration < 1:
    raise ValueError(f"kernel_iteration must be >= 1, got {kernel_iteration}")
  if se < 0 or cu < 0 or simd < 0:
    raise ValueError(f"expected non-negative se/cu/simd, got se={se} cu={cu} simd={simd}")
  kernel_filter = kernel_filter.strip() or None if kernel_filter is not None else None

  repo_root = Path(__file__).resolve().parent.parent
  root = runs_root if runs_root is not None else repo_root / "runs"
  run_dir = _create_run_dir(root)
  discovery_dir = run_dir / "discover"
  discovery_dir.mkdir(parents=True, exist_ok=False)
  subprocess.run(_rocprof_discovery_command(argv, discovery_dir), cwd=str(Path.cwd() if cwd is None else Path(cwd)), check=True)
  selected_kernel = _select_kernel(discovery_dir, kernel_filter)

  capture_dir = run_dir / "capture"
  capture_dir.mkdir(parents=True, exist_ok=False)

  aql_build_dir = _ensure_patched_aqlprofile(repo_root)
  att_decoder_dir = _find_att_decoder_dir()
  cmd = _rocprof_att_command(argv, capture_dir, kernel_name=selected_kernel.raw_name, kernel_iteration=kernel_iteration, se=se, cu=cu, simd=simd,
                             att_decoder_dir=att_decoder_dir)
  env = _rocprof_env(aql_build_dir, att_decoder_dir)
  subprocess.run(cmd, cwd=str(Path.cwd() if cwd is None else Path(cwd)), env=env, check=True)

  candidate = _discover_candidate(capture_dir, selected_kernel.raw_name, kernel_iteration)
  events = decode_att_file(candidate.att_path, candidate.codeobj_path, candidate.target, cu=cu, simd=simd, code_region=candidate.code_region)
  if not events:
    raise RuntimeError(f"kernel {selected_kernel.display_name!r} iteration {kernel_iteration} dispatch {candidate.dispatch_id} decoded to zero events")

  trace_data: TraceData = {"target": candidate.target, "traces": [{"se": candidate.se, "cu": cu, "simd": simd, "events": events}]}
  return trace_data, run_dir, selected_kernel


def _create_run_dir(runs_root: Path) -> Path:
  runs_root.mkdir(parents=True, exist_ok=True)
  while True:
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = runs_root / f"{stamp}_{secrets.token_hex(3)}"
    if not run_dir.exists():
      run_dir.mkdir()
      return run_dir


def _ensure_patched_aqlprofile(repo_root: Path) -> Path:
  build_dir = repo_root / "tools" / "aqlprofile" / "build"
  if any(build_dir.glob("libhsa-amd-aqlprofile64.so*")):
    return build_dir
  build_script = repo_root / "tools" / "aqlprofile" / "scripts" / "build.sh"
  if not build_script.is_file():
    raise FileNotFoundError(f"missing aqlprofile build script: {build_script}")
  subprocess.run([str(build_script)], cwd=str(build_script.parent), check=True)
  if not any(build_dir.glob("libhsa-amd-aqlprofile64.so*")):
    raise RuntimeError(f"patched aqlprofile build completed without libraries in {build_dir}")
  return build_dir


def _find_att_decoder_dir() -> Path:
  candidates: list[Path] = []
  if (env_path := os.environ.get("ROCPROF_ATT_LIBRARY_PATH")):
    candidates += [Path(path) for path in env_path.split(":") if path]
  if (ld_path := os.environ.get("LD_LIBRARY_PATH")):
    candidates += [Path(path) for path in ld_path.split(":") if path]
  if (rocprof := shutil.which("rocprofv3")) is not None:
    candidates.append(Path(rocprof).resolve().parent.parent / "lib")
  candidates += [Path("/opt/rocm/lib"), *sorted(Path("/opt").glob("rocm-*/lib"), reverse=True)]
  seen: set[Path] = set()
  for candidate in candidates:
    if candidate in seen or not candidate.is_dir():
      continue
    seen.add(candidate)
    if any(candidate.glob("librocprof-trace-decoder.so*")):
      return candidate
  raise FileNotFoundError("unable to locate librocprof-trace-decoder.so; install the rocprof trace decoder and/or pass it in the environment")


def _rocprof_env(aql_build_dir: Path, att_decoder_dir: Path) -> dict[str, str]:
  env = os.environ.copy()
  ld_paths = [str(aql_build_dir), str(att_decoder_dir), *[path for path in env.get("LD_LIBRARY_PATH", "").split(":") if path]]
  env["LD_LIBRARY_PATH"] = ":".join(dict.fromkeys(ld_paths))
  return env


def _rocprof_discovery_command(argv: list[str], capture_dir: Path) -> list[str]:
  return [
    "rocprofv3",
    "--kernel-trace",
    "-d",
    str(capture_dir),
    "--",
    *argv,
  ]


def _rocprof_att_command(argv: list[str], capture_dir: Path, *, kernel_name: str, kernel_iteration: int, se: int, cu: int, simd: int,
                         att_decoder_dir: Path) -> list[str]:
  return [
    "rocprofv3",
    "--att",
    "--kernel-trace",
    "--att-library-path",
    str(att_decoder_dir),
    "--kernel-include-regex",
    _exact_kernel_regex(kernel_name),
    "--kernel-iteration-range",
    f"[{kernel_iteration}]",
    "--att-shader-engine-mask",
    hex(1 << se),
    "--att-target-cu",
    str(cu),
    "--att-simd-select",
    str(simd),
    "-d",
    str(capture_dir),
    "--",
    *argv,
  ]


def _exact_kernel_regex(kernel_name: str) -> str:
  return f"^{re.escape(kernel_name)}$"


def _select_kernel(discovery_dir: Path, kernel_filter: str | None) -> KernelSummary:
  try:
    results_db = _find_results_db(discovery_dir)
  except FileNotFoundError as exc:
    raise KernelSelectionError(f"no kernels found in rocprof results under {discovery_dir}") from exc
  summaries = _query_kernel_summaries(results_db)
  if not summaries:
    raise KernelSelectionError(f"no kernels found in rocprof results under {discovery_dir}")
  matches = [summary for summary in summaries if kernel_filter is None or kernel_filter in summary.display_name]
  if not matches:
    raise KernelSelectionError(f"no kernels matched --kernel-filter {kernel_filter!r}\n\navailable kernels:\n{_format_kernel_summaries(summaries)}")
  if len(matches) == 1:
    return matches[0]
  if kernel_filter is None:
    raise KernelSelectionError("multiple kernels found; rerun with --kernel-filter to select one:\n\n"
                               f"{_format_kernel_summaries(matches)}")
  raise KernelSelectionError(f"--kernel-filter {kernel_filter!r} matched multiple kernels; refine the filter:\n\n"
                             f"{_format_kernel_summaries(matches)}")


def _discover_candidate(capture_dir: Path, kernel_name: str, kernel_iteration: int) -> CaptureCandidate:
  results_db = _find_results_db(capture_dir)
  att_files = _discover_att_files(capture_dir)
  out_files = _discover_code_objects(capture_dir)
  dispatch_rows = _query_dispatch_rows(results_db, kernel_name)
  if not dispatch_rows:
    raise ValueError(f"kernel {kernel_name!r} not found in rocprof ATT results")
  iteration_matches = _match_kernel_iteration(dispatch_rows, kernel_iteration)
  if not iteration_matches:
    raise ValueError(f"kernel {kernel_name!r} iteration {kernel_iteration} is out of range; matching dispatches: {_format_dispatch_rows(dispatch_rows)}")
  dispatch = _select_iteration_dispatch(iteration_matches, att_files)
  if (att_info := att_files.get(dispatch["dispatch_id"])) is None:
    captured = ", ".join(str(dispatch_id) for dispatch_id in sorted(att_files)) or "none"
    raise FileNotFoundError(f"missing ATT blob for kernel {kernel_name!r} iteration {kernel_iteration} dispatch {dispatch['dispatch_id']} "
                            f"under {capture_dir}; captured dispatches: {captured}")
  if (codeobj_info := out_files.get(dispatch["code_object_id"])) is None:
    raise FileNotFoundError(f"missing code object {dispatch['code_object_id']} for kernel {kernel_name!r} under {capture_dir}")
  code_region = _query_kernel_code_region(results_db, kernel_id=dispatch["kernel_id"], code_object_id=dispatch["code_object_id"])
  return CaptureCandidate(dispatch_id=dispatch["dispatch_id"], kernel_id=dispatch["kernel_id"], kernel_name=dispatch["kernel_name"], se=att_info["se"],
                          target=codeobj_info["target"], att_path=att_info["path"], codeobj_path=codeobj_info["path"], code_region=code_region)


def _find_results_db(capture_dir: Path) -> Path:
  results = sorted(capture_dir.rglob("*_results.db"))
  if not results:
    raise FileNotFoundError(f"no rocprof results.db found under {capture_dir}")
  if len(results) > 1:
    raise RuntimeError(f"expected one results.db under {capture_dir}, found {len(results)}")
  return results[0]


def _discover_att_files(capture_dir: Path) -> dict[int, dict[str, int | Path]]:
  dispatch_to_att: dict[int, dict[str, int | Path]] = {}
  for path in sorted(capture_dir.rglob("*.att")):
    match = _ATT_RE.match(path.name)
    if match is None:
      continue
    dispatch_id = int(match.group("dispatch"))
    dispatch_to_att[dispatch_id] = {"se": int(match.group("se")), "path": path}
  return dispatch_to_att


def _discover_code_objects(capture_dir: Path) -> dict[int, dict[str, str | Path]]:
  code_objects: dict[int, dict[str, str | Path]] = {}
  for path in sorted(capture_dir.rglob("*.out")):
    match = _OUT_RE.match(path.name)
    if match is None:
      continue
    code_objects[int(match.group("codeobj"))] = {"target": match.group("target"), "path": path}
  if code_objects:
    return code_objects
  raise FileNotFoundError(f"no rocprof code-object files found under {capture_dir}")


def _match_kernel_iteration(dispatch_rows: list[dict[str, int | str]], kernel_iteration: int) -> list[dict[str, int | str]]:
  by_kernel_id: dict[int, list[dict[str, int | str]]] = {}
  for row in dispatch_rows:
    by_kernel_id.setdefault(int(row["kernel_id"]), []).append(row)
  return [rows[kernel_iteration - 1] for rows in by_kernel_id.values() if len(rows) >= kernel_iteration]


def _select_iteration_dispatch(iteration_matches: list[dict[str, int | str]], att_files: dict[int, dict[str, int | Path]]) -> dict[str, int | str]:
  # rocprofv3 can emit one ATT capture per kernel_id for the same demangled kernel
  # name. Prefer the earliest captured dispatch so profile-webui can keep working on
  # workloads that JIT or reload identical kernels into multiple code objects.
  captured_matches = [row for row in iteration_matches if int(row["dispatch_id"]) in att_files]
  candidates = captured_matches or iteration_matches
  return min(candidates, key=lambda row: int(row["dispatch_id"]))


def _format_dispatch_rows(dispatch_rows: list[dict[str, int | str]]) -> str:
  return ", ".join(f"kernel_id={dispatch['kernel_id']} dispatch_id={dispatch['dispatch_id']}" for dispatch in dispatch_rows)


def _query_kernel_code_region(results_db: Path, kernel_id: int, code_object_id: int) -> CodeRegion:
  with sqlite3.connect(results_db) as connection:
    kernel_row = connection.execute("SELECT kernel_address FROM kernel_symbols WHERE kernel_id = ? AND code_object_id = ? "
                                    "ORDER BY kernel_address ASC LIMIT 1", (kernel_id, code_object_id)).fetchone()
    if kernel_row is None or kernel_row[0] is None:
      raise ValueError(f"kernel_id={kernel_id} code_object_id={code_object_id} is missing kernel_symbols metadata")
    code_object_row = connection.execute("SELECT load_delta, load_base FROM code_objects WHERE id = ?", (code_object_id,)).fetchone()
    if code_object_row is None or (code_object_row[0] is None and code_object_row[1] is None):
      raise ValueError(f"code_object_id={code_object_id} is missing load_delta metadata")
    start_runtime = int(kernel_row[0])
    next_row = connection.execute("SELECT kernel_address FROM kernel_symbols WHERE code_object_id = ? AND kernel_address > ? "
                                  "ORDER BY kernel_address ASC LIMIT 1", (code_object_id, start_runtime)).fetchone()
  translation_delta = int(code_object_row[0] if code_object_row[0] is not None else code_object_row[1])
  start_addr = start_runtime - translation_delta
  if start_addr < 0:
    raise ValueError(f"kernel_id={kernel_id} code_object_id={code_object_id} has invalid start address 0x{start_addr:x}")
  end_addr = None
  if next_row is not None and next_row[0] is not None:
    end_addr = int(next_row[0]) - translation_delta
    if end_addr <= start_addr:
      raise ValueError(f"kernel_id={kernel_id} code_object_id={code_object_id} has non-increasing range 0x{start_addr:x}-0x{end_addr:x}")
  return CodeRegion(start_addr=start_addr, end_addr=end_addr)


def _query_dispatch_rows(results_db: Path, kernel_name: str) -> list[dict[str, int | str]]:
  with sqlite3.connect(results_db) as connection:
    rows = connection.execute("SELECT dispatch_id, kernel_id, name, code_object_id FROM kernels WHERE name = ? ORDER BY dispatch_id",
                              (kernel_name,)).fetchall()
  return [{"dispatch_id": int(dispatch_id), "kernel_id": int(kernel_id), "kernel_name": str(kernel_name), "code_object_id": int(code_object_id)}
          for dispatch_id, kernel_id, kernel_name, code_object_id in rows]


def _query_kernel_summaries(results_db: Path) -> list[KernelSummary]:
  with sqlite3.connect(results_db) as connection:
    rows = connection.execute("SELECT name, COUNT(*) AS dispatch_count FROM kernels WHERE name IS NOT NULL GROUP BY name "
                              "ORDER BY dispatch_count DESC, name ASC").fetchall()
  return [KernelSummary(raw_name=str(raw_name), display_name=_display_kernel_name(str(raw_name)), dispatch_count=int(dispatch_count))
          for raw_name, dispatch_count in rows]


def _display_kernel_name(raw_name: str) -> str:
  prefix = raw_name[:param_start] if (param_start := _top_level_param_start(raw_name)) is not None else raw_name
  last_space = -1
  depth = 0
  for idx, ch in enumerate(prefix):
    if ch == "<":
      depth += 1
    elif ch == ">":
      depth = max(0, depth - 1)
    elif ch.isspace() and depth == 0:
      last_space = idx
  return (prefix[last_space + 1:] if last_space >= 0 else prefix).strip() or raw_name


def _top_level_param_start(signature: str) -> int | None:
  depth = 0
  for idx, ch in enumerate(signature):
    if ch == "<":
      depth += 1
    elif ch == ">":
      depth = max(0, depth - 1)
    elif ch == "(" and depth == 0:
      return idx
  return None


def _format_kernel_summaries(summaries: list[KernelSummary]) -> str:
  display_counts: dict[str, int] = {}
  for summary in summaries:
    display_counts[summary.display_name] = display_counts.get(summary.display_name, 0) + 1
  lines: list[str] = []
  for idx, summary in enumerate(summaries):
    lines.append(f"[{idx}] {summary.display_name} (dispatches={summary.dispatch_count})")
    lines.append(f"    --kernel-filter {shlex.quote(summary.display_name)}")
    if display_counts[summary.display_name] > 1:
      lines.append(f"    raw: {summary.raw_name}")
  return "\n".join(lines)
