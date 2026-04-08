from __future__ import annotations

from .dsl import EnumBitField, FixedBitField, Inst
from .rdna3.ins import (DS, EXP, FLAT, GLOBAL, LDSDIR, MIMG, MTBUF, MUBUF, SCRATCH, SMEM, SOP1, SOP1_LIT, SOP2, SOP2_LIT, SOPC, SOPK, SOPK_LIT,
                        SOPP, VINTERP, VOP1, VOP1_LIT, VOP1_SDST, VOP2, VOP2_LIT, VOP3, VOP3_SDST, VOP3P, VOP3SD, VOPC, VOPD)
from .rdna4.ins import (DS as R4_DS, SMEM as R4_SMEM, SOP1 as R4_SOP1, SOP1_LIT as R4_SOP1_LIT, SOP2 as R4_SOP2, SOP2_LIT as R4_SOP2_LIT,
                        SOPC as R4_SOPC, SOPC_LIT as R4_SOPC_LIT, SOPK as R4_SOPK, SOPK_LIT as R4_SOPK_LIT, SOPP as R4_SOPP, VFLAT as R4_FLAT,
                        VGLOBAL as R4_GLOBAL, VINTERP as R4_VINTERP, VOP1 as R4_VOP1, VOP1_LIT as R4_VOP1_LIT,
                        VOP1_SDST as R4_VOP1_SDST, VOP2 as R4_VOP2, VOP2_LIT as R4_VOP2_LIT, VOP3 as R4_VOP3,
                        VOP3_SDST as R4_VOP3_SDST, VOP3P as R4_VOP3P, VOP3SD as R4_VOP3SD, VOPC as R4_VOPC, VOPD as R4_VOPD,
                        VSCRATCH as R4_SCRATCH)

_VARIANT_SRC0 = {"_SDWA_SDST": 0xF9, "_SDWA": 0xF9, "_DPP16": 0xFA}

_FORMATS = {
  "rdna3": [VOPD, VOP3P, VINTERP, VOP3SD, VOP3_SDST, VOP3, DS, GLOBAL, SCRATCH, FLAT, MUBUF, MTBUF, MIMG, EXP, LDSDIR, SMEM,
            SOP1, SOP1_LIT, SOP2, SOP2_LIT, SOPC, SOPK, SOPK_LIT, SOPP, VOPC, VOP1_SDST, VOP1, VOP1_LIT, VOP2, VOP2_LIT],
  "rdna4": [R4_VOPD, R4_VOP3P, R4_VINTERP, R4_VOP3SD, R4_VOP3_SDST, R4_VOP3, R4_DS, R4_GLOBAL, R4_SCRATCH, R4_FLAT, R4_SMEM,
            R4_SOP1, R4_SOP1_LIT, R4_SOPC, R4_SOPC_LIT, R4_SOPP, R4_SOPK, R4_SOPK_LIT, R4_VOPC, R4_VOP1_SDST, R4_VOP1, R4_VOP1_LIT,
            R4_SOP2, R4_SOP2_LIT, R4_VOP2, R4_VOP2_LIT],
}


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


def detect_format(data: bytes, arch: str = "rdna3") -> type[Inst]:
  if len(data) < 4:
    raise ValueError(f"need at least 4 bytes, got {len(data)}")
  for cls in _FORMATS[arch]:
    if _matches(data, cls):
      return cls
  raise ValueError(f"unknown {arch} format word={int.from_bytes(data[:4], 'little'):#010x}")


def decode_inst(data: bytes, arch: str = "rdna3") -> Inst:
  return detect_format(data, arch).from_bytes(data)
