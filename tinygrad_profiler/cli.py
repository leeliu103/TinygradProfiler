from __future__ import annotations

import argparse
from pathlib import Path

from ._amd_isa import SUPPORTED_ARCHES as ISA_ARCHES, extract_isa
from ._agent_bundle import build_agent_bundle
from ._deployer import build_web_bundle, start_web_server
from ._orchestrator import KernelSelectionError, orchestrate_capture
from .serialize import dump_events
from .timeline import decode_att_file

PROFILE_WEBUI_HOST = "0.0.0.0"
PROFILE_WEBUI_PORT = 8001
PROFILE_WEBUI_TITLE = "TinygradProfiler PKTS"


def cmd_extract_isa(args: argparse.Namespace) -> None:
  output = extract_isa(args.arch, args.out)
  print(f"wrote AMD ISA JSON to {output}")


def cmd_decode_att(args: argparse.Namespace) -> None:
  events = decode_att_file(args.att, args.codeobj, args.target)
  dump_events(args.output, events)
  print(f"wrote {len(events)} events to {args.output}")


def cmd_profile_webui(args: argparse.Namespace) -> None:
  command = args.command[1:] if args.command and args.command[0] == "--" else args.command
  if not command:
    raise SystemExit("profile-webui requires a command after '--'")
  run_dir = None
  server = None
  try:
    trace_data, run_dir, selected_kernel = orchestrate_capture(command, args.kernel_iteration, se=args.se, cu=args.cu, simd=args.simd,
                                                               kernel_filter=args.kernel_filter)
    bundle_dir = build_web_bundle(trace_data, run_dir / "web", kernel_name=selected_kernel.display_name, kernel_iteration=args.kernel_iteration,
                                  se=args.se, cu=args.cu, simd=args.simd, title=PROFILE_WEBUI_TITLE)
    server = start_web_server(bundle_dir, host=PROFILE_WEBUI_HOST, port=PROFILE_WEBUI_PORT)
    bound_port = server.server_address[1]
    print(f"bundle: {bundle_dir}")
    print(f"local: http://127.0.0.1:{bound_port}/")
    print(f"remote: http://<this-machine-ip>:{bound_port}/")
    server.serve_forever()
  except KeyboardInterrupt:
    print("interrupted")
    if run_dir is not None:
      print(f"run: {run_dir}")
  except KernelSelectionError as exc:
    raise SystemExit(str(exc)) from exc
  finally:
    if server is not None:
      server.server_close()


def cmd_profile_agent(args: argparse.Namespace) -> None:
  command = args.command[1:] if args.command and args.command[0] == "--" else args.command
  if not command:
    raise SystemExit("profile-agent requires a command after '--'")
  run_dir = None
  try:
    trace_data, run_dir, selected_kernel = orchestrate_capture(command, args.kernel_iteration, se=args.se, cu=args.cu, simd=args.simd,
                                                               kernel_filter=args.kernel_filter)
    bundle_dir = build_agent_bundle(trace_data, run_dir / "agent", kernel_name=selected_kernel.display_name, kernel_iteration=args.kernel_iteration,
                                    se=args.se, cu=args.cu, simd=args.simd)
    print(f"bundle: {bundle_dir}")
    print(f"metadata: {bundle_dir / 'metadata.json'}")
    print(f"events: {bundle_dir / 'events.json'}")
  except KeyboardInterrupt:
    print("interrupted")
    if run_dir is not None:
      print(f"run: {run_dir}")
  except KernelSelectionError as exc:
    raise SystemExit(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description="Standalone RDNA4 ATT packet timeline decoder and ISA extractor")
  sub = parser.add_subparsers(dest="cmd", required=True)

  extract = sub.add_parser("extract-isa", help="Extract RDNA3/RDNA4 ISA JSON from AMD XML and PDF sources")
  extract.add_argument("--arch", required=True, choices=ISA_ARCHES)
  extract.add_argument("--out", type=Path, required=True, help="Output JSON file path")
  extract.set_defaults(func=cmd_extract_isa)

  decode = sub.add_parser("decode-att", help="Decode ATT plus code object into PKTS timeline JSON")
  decode.add_argument("--att", type=Path, required=True)
  decode.add_argument("--codeobj", type=Path, required=True)
  decode.add_argument("--target", required=True, help="RDNA4 target, for example gfx1201")
  decode.add_argument("--output", type=Path, required=True)
  decode.set_defaults(func=cmd_decode_att)

  profile = sub.add_parser("profile-webui", help="Capture one ATT trace, build a static PKTS bundle, and serve it")
  profile.add_argument("--kernel-filter", help="Optional substring filter applied to discovered kernel names")
  profile.add_argument("--kernel-iteration", required=True, type=int, help="1-based iteration number for the selected kernel")
  profile.add_argument("--se", required=True, type=int, help="Shader engine to trace")
  profile.add_argument("--simd", required=True, type=int, help="SIMD to trace within the selected CU")
  profile.add_argument("--cu", required=True, type=int, help="CU to trace within the selected shader engine")
  profile.add_argument("command", nargs=argparse.REMAINDER, help="Command to profile. Separate with '--'")
  profile.set_defaults(func=cmd_profile_webui)

  agent = sub.add_parser("profile-agent", help="Capture one ATT trace and write a serialized event bundle for agent consumption")
  agent.add_argument("--kernel-filter", help="Optional substring filter applied to discovered kernel names")
  agent.add_argument("--kernel-iteration", required=True, type=int, help="1-based iteration number for the selected kernel")
  agent.add_argument("--se", required=True, type=int, help="Shader engine to trace")
  agent.add_argument("--simd", required=True, type=int, help="SIMD to trace within the selected CU")
  agent.add_argument("--cu", required=True, type=int, help="CU to trace within the selected shader engine")
  agent.add_argument("command", nargs=argparse.REMAINDER, help="Command to profile. Separate with '--'")
  agent.set_defaults(func=cmd_profile_agent)
  return parser


def main() -> None:
  parser = build_parser()
  args = parser.parse_args()
  args.func(args)
