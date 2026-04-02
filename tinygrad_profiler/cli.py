from __future__ import annotations

import argparse
from pathlib import Path

from ._amd_isa import SUPPORTED_ARCHES as ISA_ARCHES, extract_isa
from .serialize import dump_events
from .timeline import decode_att_file


def cmd_extract_isa(args: argparse.Namespace) -> None:
  output = extract_isa(args.arch, args.out)
  print(f"wrote AMD ISA JSON to {output}")


def cmd_decode_att(args: argparse.Namespace) -> None:
  events = decode_att_file(args.att, args.codeobj, args.target)
  dump_events(args.output, events)
  print(f"wrote {len(events)} events to {args.output}")


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
  return parser


def main() -> None:
  parser = build_parser()
  args = parser.parse_args()
  args.func(args)
