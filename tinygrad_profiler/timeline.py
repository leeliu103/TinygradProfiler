from __future__ import annotations

import itertools, re
from decimal import Decimal
from pathlib import Path
from typing import Generator

from .elf import get_elf_section
from .events import ProfileEvent, ProfilePointEvent, ProfileRangeEvent, TracingKey
from .vendor.amd.dsl import Inst
from .vendor.amd.formats import detect_format

wave_colors = {"WMMA": "#1F7857", **{x:"#ffffc0" for x in ["VALU", "VINTERP"]}, "SALU": "#cef263", "SMEM": "#ffc0c0", "STORE": "#4fa3cc",
               **{x:"#b2b7c9" for x in ["VMEM", "SGMEM"]}, "LDS": "#9fb4a6", "IMMEDIATE": "#f3b44a", "BARRIER": "#d00000",
               "JUMP_NO": "#fb8500", "JUMP": "#ffb703", "WAVERDY": "#1a2a2a"}


def amd_decode(lib: bytes, target: str) -> dict[int, Inst]:
  if not target.startswith("gfx12"):
    raise ValueError(f"RDNA4-only standalone decoder, got target={target}")
  text = get_elf_section(lib, ".text")
  off, buf = text.sh_addr, text.content
  addr_table: dict[int, Inst] = {}
  offset = 0
  while offset < len(buf):
    remaining = buf[offset:]
    fmt = detect_format(remaining)
    decoded = fmt.from_bytes(remaining)
    addr_table[off + offset] = decoded
    offset += decoded.size()
  return addr_table


def sqtt_timeline(data: bytes, lib: bytes, target: str) -> Generator[ProfileEvent, None, None]:
  from .vendor.amd.sqtt import (map_insts, InstructionInfo, PacketType, INST, InstOp, VALUINST, IMMEDIATE, IMMEDIATE_MASK, VMEMEXEC,
                                ALUEXEC, INST_RDNA4, InstOpRDNA4, TS_DELTA_OR_MARK, TS_DELTA_OR_MARK_RDNA4, WAVEEND, WAVERDY)

  pc_map = {addr: str(inst) for addr, inst in amd_decode(lib, target).items()}
  row_ends: dict[str, Decimal] = {}
  row_counts: dict[str, itertools.count] = {}
  curr_barrier: dict[int, ProfileRangeEvent] = {}
  exec_pending: dict[str, list[tuple[str, str]]] = {}
  dispatch_to_exec = {"WMMA":"VALU", "VALU":"VALU", "VALU1":"VALU", "VALUT":"VALU", "VALUB":"VALU", "VALUINST":"VALU", "VINTERP":"VALU",
                      "SGMEM":"VMEM", "FLAT":"VMEM", "LDS":"LDS", "SALU":"SALU", "SMEM":"SALU", "VMEM":"VMEM"}

  def add(name: str, p: PacketType, wave: int | None = None, info: InstructionInfo | None = None) -> Generator[ProfileEvent, None, None]:
    row = "OTHER" if name.startswith("OTHER_") else f"WAVE:{wave}" if (wave := getattr(p, "wave", wave)) is not None \
        else f"{p.__class__.__name__}:0 {name.replace('_ALT', '')}"
    start_time, end_time = p._time, p._time + 1
    link = f"PC:{info.pc}" if info is not None else None
    if isinstance(p, (ALUEXEC, VMEMEXEC)):
      dispatch_id, op_type = exec_pending[name].pop(0)
      duration = int(dur_match.group(1)) if (dur_match := re.match(r".*_(\d+)$", op_type)) else 1
      start_time, end_time = p._time - duration, p._time
      link = f"LINK:{dispatch_id}"
      if op_type.startswith("WMMA"):
        name += "_WMMA"
    idx = next(row_counts.setdefault(row, itertools.count(0)))
    if isinstance(p, (VALUINST, INST, INST_RDNA4)) and (exec_type := dispatch_to_exec.get(name.replace("OTHER_", "").split("_")[0])) is not None:
      if name.startswith("OTHER_"):
        exec_type = f"{exec_type}_ALT"
      if isinstance(p, VALUINST) and info is not None and getattr(info.inst, "op_name", "").startswith("V_WMMA"):
        name = f"WMMA_{16 if 'IU4' in info.inst.op_name else 32}"
      exec_pending.setdefault(exec_type, []).append((f"{row}-{idx}", name))
    if row not in row_ends:
      yield ProfilePointEvent(row, "JSON", "pcMap", pc_map, ts=Decimal(0))
    yield (e := ProfileRangeEvent(row, TracingKey(name, ret=link), Decimal(start_time), Decimal(end_time)))
    if (et := row_ends.get(row)) is not None and e.st < et and not isinstance(p, (ALUEXEC, VMEMEXEC)):
      RuntimeError(f"packet {row}-{idx} overlaps: {e.st} {et}.")
    row_ends[row] = e.en if e.en is not None else Decimal(0)
    if wave is not None:
      if (barrier := curr_barrier.pop(wave, None)) is not None:
        barrier.en = Decimal(p._time)
      if name == "BARRIER":
        curr_barrier[wave] = e

  ns_per_tick = 10
  prev_pair: tuple[int, int] | None = None
  yield ProfilePointEvent("", "JSON", "waveColors", list(wave_colors.items()), ts=Decimal(0))
  for p, info in map_insts(data, lib, target):
    if isinstance(p, (TS_DELTA_OR_MARK, TS_DELTA_OR_MARK_RDNA4)) and p.is_marker:
      pair = (p._time, p.delta)
      if prev_pair is None:
        prev_pair = pair
      else:
        (s0, r0), (s1, r1) = prev_pair, pair
        freq_hz = (s1 - s0) * 1_000_000_000 // ((r1 - r0) * ns_per_tick)
        yield ProfilePointEvent("LINE:Shader Clock", "freq_hz", freq_hz, ts=Decimal(p._time))
        prev_pair = pair
    if isinstance(p, (INST, INST_RDNA4)):
      name = p.op.name if isinstance(p.op, (InstOp, InstOpRDNA4)) else f"0x{p.op:02x}"
      yield from add(name, p, info=info)
    if isinstance(p, (VALUINST, IMMEDIATE, WAVEEND)):
      yield from add(p.__class__.__name__, p, info=info)
    if isinstance(p, IMMEDIATE_MASK):
      if info is None:
        raise RuntimeError("IMMEDIATE_MASK packet missing instruction info")
      yield from add("IMMEDIATE", p, wave=info.wave, info=info)
    if isinstance(p, WAVERDY):
      for wave in range(16):
        if p.mask & (1 << wave):
          if wave in curr_barrier:
            yield from add("WAVERDY", p, wave=wave)
    if isinstance(p, (VMEMEXEC, ALUEXEC)):
      name = str(p.src).split(".")[1]
      if name == "VALU_SALU":
        yield from add("VALU", p)
        yield from add("SALU", p)
      else:
        yield from add(name, p)


def decode_att_bytes(att_blob: bytes, codeobj_blob: bytes, target: str) -> list[ProfileEvent]:
  return list(sqtt_timeline(att_blob, codeobj_blob, target))


def decode_att_file(att_path: str | Path, codeobj_path: str | Path, target: str) -> list[ProfileEvent]:
  return decode_att_bytes(Path(att_path).read_bytes(), Path(codeobj_path).read_bytes(), target)

