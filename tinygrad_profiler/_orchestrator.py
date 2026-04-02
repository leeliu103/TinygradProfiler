from __future__ import annotations

import datetime, os, re, secrets, shutil, sqlite3, subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from .events import ProfileEvent
from .timeline import decode_att_file


class TraceEntry(TypedDict):
  se: int
  cu: int
  simd: int
  events: list[ProfileEvent]


class TraceData(TypedDict):
  target: str
  traces: list[TraceEntry]


@dataclass(frozen=True)
class CaptureCandidate:
  dispatch_id: int
  kernel_id: int
  kernel_name: str
  se: int
  target: str
  att_path: Path
  codeobj_path: Path


_ATT_RE = re.compile(r"(?P<pid>\d+)_(?P<agent>\d+)_shader_engine_(?P<se>\d+)_(?P<dispatch>\d+)\.att$")
_OUT_RE = re.compile(r"(?P<pid>\d+)_(?P<target>gfx\d+)_code_object_id_(?P<codeobj>\d+)\.out$")


def orchestrate_capture(argv: list[str], kernel_name: str, kernel_iteration: int, se: int, simd: int, cu: int = 0,
                        runs_root: Path | None = None, cwd: str | Path | None = None) -> tuple[TraceData, Path]:
  if not argv:
    raise ValueError("argv must not be empty")
  if not kernel_name:
    raise ValueError("kernel_name must not be empty")
  if kernel_iteration < 1:
    raise ValueError(f"kernel_iteration must be >= 1, got {kernel_iteration}")
  if se < 0 or cu < 0 or simd < 0:
    raise ValueError(f"expected non-negative se/cu/simd, got se={se} cu={cu} simd={simd}")

  repo_root = Path(__file__).resolve().parent.parent
  root = runs_root if runs_root is not None else repo_root / "runs"
  run_dir = _create_run_dir(root)
  capture_dir = run_dir / "capture"
  capture_dir.mkdir(parents=True, exist_ok=False)

  aql_build_dir = _ensure_patched_aqlprofile(repo_root)
  att_decoder_dir = _find_att_decoder_dir()
  cmd = _rocprof_command(argv, capture_dir, kernel_name=kernel_name, kernel_iteration=kernel_iteration, se=se, cu=cu, simd=simd,
                         att_decoder_dir=att_decoder_dir)
  env = _rocprof_env(aql_build_dir, att_decoder_dir)
  subprocess.run(cmd, cwd=str(Path.cwd() if cwd is None else Path(cwd)), env=env, check=True)

  candidate = _discover_candidate(capture_dir, kernel_name, kernel_iteration)
  events = decode_att_file(candidate.att_path, candidate.codeobj_path, candidate.target, cu=cu, simd=simd)
  if not events:
    raise RuntimeError(f"kernel {kernel_name!r} iteration {kernel_iteration} dispatch {candidate.dispatch_id} decoded to zero events")

  trace_data: TraceData = {"target": candidate.target, "traces": [{"se": candidate.se, "cu": cu, "simd": simd, "events": events}]}
  return trace_data, run_dir


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


def _rocprof_command(argv: list[str], capture_dir: Path, *, kernel_name: str, kernel_iteration: int, se: int, cu: int, simd: int,
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
  if len(iteration_matches) > 1:
    raise ValueError(f"kernel {kernel_name!r} iteration {kernel_iteration} matched multiple kernel ids / dispatches: "
                     f"{_format_dispatch_rows(iteration_matches)}")
  dispatch = iteration_matches[0]
  if (att_info := att_files.get(dispatch["dispatch_id"])) is None:
    captured = ", ".join(str(dispatch_id) for dispatch_id in sorted(att_files)) or "none"
    raise FileNotFoundError(f"missing ATT blob for kernel {kernel_name!r} iteration {kernel_iteration} dispatch {dispatch['dispatch_id']} "
                            f"under {capture_dir}; captured dispatches: {captured}")
  if (codeobj_info := out_files.get(dispatch["code_object_id"])) is None:
    raise FileNotFoundError(f"missing code object {dispatch['code_object_id']} for kernel {kernel_name!r} under {capture_dir}")
  return CaptureCandidate(dispatch_id=dispatch["dispatch_id"], kernel_id=dispatch["kernel_id"], kernel_name=dispatch["kernel_name"], se=att_info["se"],
                          target=codeobj_info["target"], att_path=att_info["path"], codeobj_path=codeobj_info["path"])


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


def _format_dispatch_rows(dispatch_rows: list[dict[str, int | str]]) -> str:
  return ", ".join(f"kernel_id={dispatch['kernel_id']} dispatch_id={dispatch['dispatch_id']}" for dispatch in dispatch_rows)


def _query_dispatch_rows(results_db: Path, kernel_name: str) -> list[dict[str, int | str]]:
  with sqlite3.connect(results_db) as connection:
    rows = connection.execute("SELECT dispatch_id, kernel_id, name, code_object_id FROM kernels WHERE name = ? ORDER BY dispatch_id",
                              (kernel_name,)).fetchall()
  return [{"dispatch_id": int(dispatch_id), "kernel_id": int(kernel_id), "kernel_name": str(kernel_name), "code_object_id": int(code_object_id)}
          for dispatch_id, kernel_id, kernel_name, code_object_id in rows]
