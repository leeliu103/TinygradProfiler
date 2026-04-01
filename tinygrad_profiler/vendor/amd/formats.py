from __future__ import annotations

from .dsl import EnumBitField, FixedBitField, Inst
from .rdna4.ins import (DS, SMEM, SOP1, SOP1_LIT, SOP2, SOP2_LIT, SOPC, SOPC_LIT, SOPK, SOPK_LIT, SOPP, VFLAT, VGLOBAL, VINTERP, VOP1,
                        VOP1_LIT, VOP1_SDST, VOP2, VOP2_LIT, VOP3, VOP3_SDST, VOP3P, VOP3SD, VOPC, VOPD, VSCRATCH)

_VARIANT_SRC0 = {"_SDWA_SDST": 0xF9, "_SDWA": 0xF9, "_DPP16": 0xFA}

_FORMATS = [VOPD, VOP3P, VINTERP, VOP3SD, VOP3_SDST, VOP3, DS, VGLOBAL, VSCRATCH, VFLAT, SMEM,
            SOP1, SOP1_LIT, SOPC, SOPC_LIT, SOPP, SOPK, SOPK_LIT, VOPC, VOP1_SDST, VOP1, VOP1_LIT,
            SOP2, SOP2_LIT, VOP2, VOP2_LIT]


def _matches(data: bytes, cls: type[Inst]) -> bool:
  for _, field in cls._fields:
    dword_idx = field.lo // 32
    if len(data) < (dword_idx + 1) * 4:
      return False
    word = int.from_bytes(data[dword_idx * 4:(dword_idx + 1) * 4], "little")
    field_lo = field.lo % 32
    if isinstance(field, FixedBitField):
      if ((word >> field_lo) & field.mask) != field.default:
        return False
    if isinstance(field, EnumBitField) and field.allowed is not None:
      try:
        opcode = field.decode((word >> field_lo) & field.mask)
      except ValueError:
        return False
      if opcode not in field.allowed:
        return False
  name = cls.__name__
  word = int.from_bytes(data[:4], "little")
  for suffix, expected_src0 in _VARIANT_SRC0.items():
    if name.endswith(suffix):
      return (word & 0x1FF) == expected_src0
  return True


def detect_format(data: bytes) -> type[Inst]:
  if len(data) < 4:
    raise ValueError(f"need at least 4 bytes, got {len(data)}")
  for cls in _FORMATS:
    if _matches(data, cls):
      return cls
  raise ValueError(f"unknown rdna4 format word={int.from_bytes(data[:4], 'little'):#010x}")


def decode_inst(data: bytes) -> Inst:
  return detect_format(data).from_bytes(data)

