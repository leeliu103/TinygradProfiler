from __future__ import annotations

import argparse
from pathlib import Path

from .serialize import dump_events
from .timeline import decode_att_file


def cmd_decode_att(args: argparse.Namespace) -> None:
  events = decode_att_file(args.att, args.codeobj, args.target)
  dump_events(args.output, events)
  print(f"wrote {len(events)} events to {args.output}")


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description="Standalone RDNA4 ATT packet timeline decoder")
  sub = parser.add_subparsers(dest="cmd", required=True)

  decode = sub.add_parser("decode-att", help="Decode ATT plus code object into PKTS timeline JSON")
  decode.add_argument("--att", type=Path, required=True)
  decode.add_argument("--codeobj", type=Path, required=True)
  decode.add_argument("--target", required=True, help="RDNA4 target, for example gfx1201")
  decode.add_argument("--output", type=Path, required=True)
  decode.set_defaults(func=cmd_decode_att)
  return parser


def main() -> None:
  parser = build_parser()
  args = parser.parse_args()
  args.func(args)
