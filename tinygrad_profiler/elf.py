from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class ElfSection:
  name: str
  sh_addr: int
  sh_offset: int
  sh_size: int
  content: bytes


def _read_cstr(blob: bytes, offset: int) -> str:
  end = blob.find(b"\x00", offset)
  if end == -1:
    end = len(blob)
  return blob[offset:end].decode("utf-8")


def elf_sections(blob: bytes) -> list[ElfSection]:
  if blob[:4] != b"\x7fELF":
    raise ValueError("not an ELF file")
  ei_class = blob[4]
  ei_data = blob[5]
  if ei_class != 2:
    raise ValueError(f"expected ELF64, got class={ei_class}")
  if ei_data != 1:
    raise ValueError(f"expected little-endian ELF, got data={ei_data}")

  ehdr = struct.unpack_from("<16sHHIQQQIHHHHHH", blob, 0)
  e_shoff = ehdr[6]
  e_shentsize = ehdr[11]
  e_shnum = ehdr[12]
  e_shstrndx = ehdr[13]
  if e_shentsize != 64:
    raise ValueError(f"unexpected section header size: {e_shentsize}")

  headers = [struct.unpack_from("<IIQQQQIIQQ", blob, e_shoff + i * e_shentsize) for i in range(e_shnum)]
  shstr = headers[e_shstrndx]
  shstr_off, shstr_size = shstr[4], shstr[5]
  shstr_blob = blob[shstr_off:shstr_off + shstr_size]

  sections: list[ElfSection] = []
  for header in headers:
    sh_name, _sh_type, _sh_flags, sh_addr, sh_offset, sh_size, _sh_link, _sh_info, _sh_addralign, _sh_entsize = header
    name = _read_cstr(shstr_blob, sh_name)
    sections.append(ElfSection(name, sh_addr, sh_offset, sh_size, blob[sh_offset:sh_offset + sh_size]))
  return sections


def get_elf_section(blob: bytes, name: str) -> ElfSection:
  for section in elf_sections(blob):
    if section.name == name:
      return section
  raise KeyError(f"missing ELF section {name!r}")

