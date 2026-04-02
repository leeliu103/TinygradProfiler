from __future__ import annotations

import io, json, os, re, tempfile, urllib.request, xml.etree.ElementTree as ET, zipfile, zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

SUPPORTED_ARCHES = ("rdna3", "rdna4")
ARCHS = {
  "rdna3": {"xml": "amdgpu_isa_rdna3_5.xml", "pdf": "https://docs.amd.com/api/khub/documents/UVVZM22UN7tMUeiW_4ShTQ/content"},
  "rdna4": {"xml": "amdgpu_isa_rdna4.xml", "pdf": "https://docs.amd.com/api/khub/documents/uQpkEvk3pv~kfAb2x~j4uw/content"},
}
XML_URL = "https://gpuopen.com/download/machine-readable-isa/latest/"
NAME_MAP = {"VOP3_SDST_ENC": "VOP3SD", "VOP3_SDST_ENC_LIT": "VOP3SD_LIT", "VOP3_SDST_ENC_DPP16": "VOP3SD_DPP16",
            "VOP3_SDST_ENC_DPP8": "VOP3SD_DPP8", "VOPDXY": "VOPD", "VOPDXY_LIT": "VOPD_LIT", "VDS": "DS"}
FIXES = {
  "rdna3": {"SOPK": {22: "S_SUBVECTOR_LOOP_BEGIN", 23: "S_SUBVECTOR_LOOP_END"}, "FLAT": {55: "FLAT_ATOMIC_CSUB_U32"}},
  "rdna4": {"SOP1": {80: "S_GET_BARRIER_STATE", 81: "S_BARRIER_INIT", 82: "S_BARRIER_JOIN"}, "SOPP": {9: "S_WAITCNT", 21: "S_BARRIER_LEAVE"}},
}
FIELD_FIXES: dict[str, dict[str, list[tuple[str, int, int]]]] = {}
_ENC_SUFFIXES = ("_NSA1",)
_ENC_SUFFIX_MAP = {"_INST_LITERAL": "_LIT", "_VOP_DPP16": "_DPP16", "_VOP_DPP": "_DPP16", "_VOP_DPP8": "_DPP8",
                   "_VOP_SDWA": "_SDWA", "_VOP_SDWA_SDST_ENC": "_SDWA_SDST", "_MFMA": "_MFMA"}
_FIELD_RENAMES = {"opsel_hi_2": "opsel_hi2", "op_sel_hi_2": "opsel_hi2", "op_sel": "opsel", "bound_ctrl": "bc",
                  "tgt": "target", "row_en": "row", "unorm": "unrm", "clamp": "clmp", "wait_exp": "waitexp",
                  "simm32": "literal", "dpp_ctrl": "dpp", "acc_cd": "acc_cd", "acc": "acc",
                  "dst_sel": "dst_sel", "dst_unused": "dst_unused", "src0_sel": "src0_sel", "src1_sel": "src1_sel"}
_SKIP_ENCODINGS = ("NSA",)
_BASE_SUFFIXES = ("_SDWA_SDST", "_DPP16", "_DPP8", "_SDWA", "_LIT", "_MFMA")

FieldSpec: TypeAlias = tuple[str, int, int]
OperandSpec: TypeAlias = tuple[str | None, int, str | None]
TextElement: TypeAlias = tuple[float, float, str, str]


@dataclass
class ParsedIsaXml:
  encodings: dict[str, tuple[list[FieldSpec], str | None]]
  enums: dict[str, dict[int, str]]
  operand_types: dict[tuple[str, str], dict[str, OperandSpec]]
  data_formats: dict[str, int]
  suffix_only_ops: dict[str, dict[str, set[int]]]
  instruction_variants: dict[tuple[str, str, int], dict[str, dict[str, OperandSpec]]]


def _cache_root() -> Path:
  base = Path(os.environ["XDG_CACHE_HOME"]) if "XDG_CACHE_HOME" in os.environ else Path.home() / ".cache"
  return base / "tinygrad-profiler" / "amd_isa"


def _download_cached(url: str, filename: str) -> bytes:
  cache_path = _cache_root() / filename
  if cache_path.is_file():
    return cache_path.read_bytes()
  cache_path.parent.mkdir(parents=True, exist_ok=True)
  request = urllib.request.Request(url, headers={"User-Agent": "TinygradProfiler/0.1"})
  with urllib.request.urlopen(request, timeout=180) as response:
    data = response.read()
  with tempfile.NamedTemporaryFile(dir=cache_path.parent, delete=False) as tmp:
    tmp.write(data)
  Path(tmp.name).replace(cache_path)
  return data


def _fetch_xml_root(filename: str) -> ET.Element:
  bundle = _download_cached(XML_URL, "machine-readable-isa.zip")
  if not bundle.startswith(b"PK"):
    raise RuntimeError(f"machine-readable ISA download is not a zip file: {XML_URL}")
  return ET.fromstring(zipfile.ZipFile(io.BytesIO(bundle)).read(filename))


def _fetch_pdf(url: str, arch: str) -> bytes:
  data = _download_cached(url, f"{arch}_isa.pdf")
  if not data.startswith(b"%PDF-"):
    raise RuntimeError(f"ISA PDF download is not a PDF file: {url}")
  return data


def _strip_enc(name: str) -> str:
  name = name.removeprefix("ENC_")
  for suffix in _ENC_SUFFIXES:
    name = name.replace(suffix, "")
  for old, new in sorted(_ENC_SUFFIX_MAP.items(), key=lambda item: -len(item[0])):
    name = name.replace(old, new)
  return name


def _norm_field(name: str) -> str:
  for old, new in _FIELD_RENAMES.items():
    name = name.replace(old, new)
  return name


def _map_flat(enc_name: str, instr_name: str) -> str:
  if enc_name in ("FLAT_GLBL", "FLAT_GLOBAL"):
    return "GLOBAL"
  if enc_name == "FLAT_SCRATCH":
    return "SCRATCH"
  if enc_name in ("FLAT", "VFLAT", "VGLOBAL", "VSCRATCH"):
    prefix = "V" if enc_name.startswith("V") else ""
    if instr_name.startswith("GLOBAL_"):
      return f"{prefix}GLOBAL"
    if instr_name.startswith("SCRATCH_"):
      return f"{prefix}SCRATCH"
    return f"{prefix}FLAT"
  return enc_name


def parse_xml(filename: str) -> ParsedIsaXml:
  root = _fetch_xml_root(filename)
  encodings: dict[str, tuple[list[FieldSpec], str | None]] = {}
  enums: dict[str, dict[int, str]] = {}
  operand_types: dict[tuple[str, str], dict[str, OperandSpec]] = {}
  data_formats: dict[str, int] = {}
  instruction_variants: dict[tuple[str, str, int], dict[str, dict[str, OperandSpec]]] = {}

  op_enum_map = {("OPR_HWREG", "ID"): "HWREG", ("OPR_SENDMSG_RTN", "MSG"): "MSG"}
  for operand_type in root.findall(".//OperandTypes/OperandType"):
    operand_name = operand_type.findtext("OperandTypeName")
    for field in operand_type.findall(".//Field"):
      enum_name = op_enum_map.get((operand_name, field.findtext("FieldName")))
      if enum_name is None:
        continue
      entries: dict[int, str] = {}
      for predefined in field.findall(".//PredefinedValue"):
        value, name = predefined.findtext("Value"), predefined.findtext("Name")
        if value is None or name is None:
          continue
        entries[int(value)] = name.upper()
      enums[enum_name] = entries

  for data_format in root.findall("ISA/DataFormats/DataFormat"):
    name, bits = data_format.findtext("DataFormatName"), data_format.findtext("BitCount")
    if name and bits:
      data_formats[name] = int(bits)

  for encoding in root.findall("ISA/Encodings/Encoding"):
    encoding_name = encoding.findtext("EncodingName")
    if encoding_name is None:
      continue
    is_base = encoding_name.startswith("ENC_") or encoding_name in ("VOP3_SDST_ENC", "VOPDXY")
    is_variant = any(suffix in encoding_name for suffix in _ENC_SUFFIX_MAP)
    if not is_base and not is_variant:
      continue
    if any(token in encoding_name for token in _SKIP_ENCODINGS):
      continue
    fields: list[FieldSpec] = []
    for field in encoding.findall(".//MicrocodeFormat/BitMap/Field"):
      bit_range = field.find("BitLayout/Range")
      if bit_range is None:
        continue
      field_name = field.findtext("FieldName")
      if field_name is None:
        continue
      bit_offset, bit_count = int(bit_range.findtext("BitOffset") or 0), int(bit_range.findtext("BitCount") or 0)
      fields.append((_norm_field(field_name.lower()), bit_offset + bit_count - 1, bit_offset))
    identifiers = encoding.findall("EncodingIdentifiers/EncodingIdentifier")
    ident = identifiers[0] if identifiers else None
    encoding_field = next((field for field in fields if field[0] == "encoding"), None)
    encoding_bits: str | None = None
    if ident is not None and ident.text is not None and encoding_field is not None:
      encoding_bits = "".join(ident.text[len(ident.text)-1-bit] for bit in range(encoding_field[1] % 32, (encoding_field[2] % 32) - 1, -1))
    base_name = _strip_enc(encoding_name)
    encodings[NAME_MAP.get(base_name, base_name)] = (fields, encoding_bits)

  opcode_encs: dict[str, dict[int, set[str]]] = {}
  for instruction in root.findall("ISA/Instructions/Instruction"):
    instruction_name = instruction.findtext("InstructionName")
    if instruction_name is None:
      continue
    for encoding in instruction.findall("InstructionEncodings/InstructionEncoding"):
      if encoding.findtext("EncodingCondition") != "default":
        continue
      encoding_name = encoding.findtext("EncodingName")
      if encoding_name is None:
        continue
      mapped_name, opcode = _map_flat(_strip_enc(encoding_name), instruction_name), int(encoding.findtext("Opcode") or 0)
      encoding_variant = NAME_MAP.get(mapped_name, mapped_name)
      base_enum = encoding_variant
      for suffix in _BASE_SUFFIXES:
        base_enum = base_enum.replace(suffix, "")
      opcode_encs.setdefault(base_enum, {}).setdefault(opcode, set()).add(encoding_variant)
      enums.setdefault(base_enum, {})[opcode] = instruction_name
      operand_info: dict[str, OperandSpec] = {}
      for operand in encoding.findall("Operands/Operand"):
        field_name = operand.findtext("FieldName")
        if field_name is None:
          continue
        operand_info[field_name.lower()] = (operand.findtext("DataFormatName"), int(operand.findtext("OperandSize") or 0), operand.findtext("OperandType"))
      for data_format_name, _, _ in operand_info.values():
        if data_format_name and data_format_name not in data_formats:
          data_formats[data_format_name] = 0
      if operand_info:
        operand_types[(instruction_name, base_enum)] = operand_info
      instruction_variants.setdefault((instruction_name, base_enum, opcode), {})[encoding_variant] = operand_info
      if "ADDTID" in instruction_name:
        alias_fmt = "FLAT" if mapped_name == "GLOBAL" else "VFLAT" if mapped_name == "VGLOBAL" else None
        if alias_fmt is not None:
          enums.setdefault(alias_fmt, {})[opcode] = instruction_name
          instruction_variants.setdefault((instruction_name, alias_fmt, opcode), {})[encoding_variant] = operand_info

  suffix_only_ops: dict[str, dict[str, set[int]]] = {}
  for base_fmt, opcodes in opcode_encs.items():
    for opcode, enc_names in opcodes.items():
      suffix = next((candidate for candidate in _ENC_SUFFIX_MAP.values() if all(candidate in enc_name for enc_name in enc_names)), None)
      if suffix is not None:
        suffix_only_ops.setdefault(suffix, {}).setdefault(base_fmt, set()).add(opcode)
  return ParsedIsaXml(encodings=encodings, enums=enums, operand_types=operand_types, data_formats=data_formats,
                      suffix_only_ops=suffix_only_ops, instruction_variants=instruction_variants)


def _extract_pdf_stream(obj: bytes) -> bytes:
  stream_pos = obj.find(b"stream")
  if stream_pos == -1:
    return b""
  start = obj.find(b"\n", stream_pos)
  if start == -1:
    return b""
  raw = obj[start + 1:obj.find(b"endstream", start)]
  if raw.endswith(b"\r"):
    raw = raw[:-1]
  return zlib.decompress(raw) if b"/FlateDecode" in obj else raw


def extract_pdf_text(url: str, arch: str) -> list[list[TextElement]]:
  data = _fetch_pdf(url, arch)
  xref: dict[int, int] = {}
  xref_match = re.search(rb"startxref\s+(\d+)", data)
  if xref_match is None:
    raise RuntimeError(f"could not find xref table in {url}")
  pos = int(xref_match.group(1)) + 4
  while data[pos:pos+7] != b"trailer":
    while data[pos:pos+1] in b" \r\n":
      pos += 1
    line_end = data.find(b"\n", pos)
    start_obj, count = map(int, data[pos:line_end].split()[:2])
    pos = line_end + 1
    for index in range(count):
      if data[pos+17:pos+18] == b"n" and (offset := int(data[pos:pos+10])) > 0:
        xref[start_obj + index] = offset
      pos += 20

  pages: list[list[TextElement]] = []
  for obj_id in sorted(xref):
    if b"/Type /Page" not in data[xref[obj_id]:xref[obj_id]+500]:
      continue
    contents_match = re.search(rb"/Contents (\d+) 0 R", data[xref[obj_id]:xref[obj_id]+500])
    if contents_match is None:
      continue
    stream = _extract_pdf_stream(data[xref[int(contents_match.group(1))]:data.find(b"endobj", xref[int(contents_match.group(1))])]).decode("latin-1")
    elements, font = [], ""
    regex = (r"(/F[\d.]+) [\d.]+ Tf|([\d.+-]+) ([\d.+-]+) Td|[\d.+-]+ [\d.+-]+ [\d.+-]+ [\d.+-]+ ([\d.+-]+) ([\d.+-]+) Tm"
             r"|<([0-9A-Fa-f]+)>.*?Tj|\[([^\]]+)\] TJ")
    for bt in re.finditer(r"BT(.*?)ET", stream, re.S):
      x, y = 0.0, 0.0
      for match in re.finditer(regex, bt.group(1)):
        if match.group(1):
          font = match.group(1)
        elif match.group(2):
          x, y = x + float(match.group(2)), y + float(match.group(3))
        elif match.group(4):
          x, y = float(match.group(4)), float(match.group(5))
        elif match.group(6) and (text := bytes.fromhex(match.group(6)).decode("latin-1")).strip():
          elements.append((x, y, text, font))
        elif match.group(7):
          text = "".join(bytes.fromhex(hex_text).decode("latin-1") for hex_text in re.findall(r"<([0-9A-Fa-f]+)>", match.group(7)))
          if text.strip():
            elements.append((x, y, text, font))
    pages.append(sorted(elements, key=lambda element: (-element[1], element[0])))
  return pages


def extract_pcode(pages: list[list[TextElement]], name_to_op: dict[str, int]) -> dict[tuple[str, int], str]:
  all_instructions: list[tuple[int, float, str, int]] = []
  for page_idx, page in enumerate(pages):
    by_y: dict[int, list[tuple[float, str]]] = {}
    for x, y, text, _ in page:
      by_y.setdefault(round(y), []).append((x, text))
    for y, items in sorted(by_y.items(), reverse=True):
      left = [(x, text) for x, text in items if 55 < x < 65]
      right = [(x, text) for x, text in items if 535 < x < 550]
      if left and right and left[0][1] in name_to_op and right[0][1].isdigit():
        all_instructions.append((page_idx, y, left[0][1], int(right[0][1])))

  pcode: dict[tuple[str, int], str] = {}
  for index, (page_idx, y, name, opcode) in enumerate(all_instructions):
    if index + 1 < len(all_instructions):
      next_page, next_y = all_instructions[index + 1][0], all_instructions[index + 1][1]
    else:
      next_page, next_y = page_idx, 0
    lines: list[tuple[int, float, str]] = []
    for page in range(page_idx, next_page + 1):
      start_y = y if page == page_idx else 800
      end_y = next_y if page == next_page else 0
      lines.extend((page, text_y, text) for x, text_y, text, font in pages[page] if font in ("/F6.0", "/F7.0") and end_y < text_y < start_y and 60 < x < 80)
    if not lines:
      continue
    sorted_lines = sorted(lines, key=lambda item: (item[0], -item[1]))
    filtered = [sorted_lines[0]]
    for line_index in range(1, len(sorted_lines)):
      prev_page, prev_y, _ = sorted_lines[line_index - 1]
      curr_page, curr_y, _ = sorted_lines[line_index]
      if curr_page == prev_page and prev_y - curr_y > 30:
        break
      if curr_page != prev_page and prev_y > 60 and curr_y < 730:
        break
      filtered.append(sorted_lines[line_index])
    pcode_lines = [text.replace("Ê", "").strip() for _, _, text in filtered]
    if pcode_lines:
      pcode[(name, opcode)] = "\n".join(pcode_lines)
  return pcode


def _apply_arch_fixes(arch: str, parsed: ParsedIsaXml) -> ParsedIsaXml:
  for fmt, ops in FIXES.get(arch, {}).items():
    parsed.enums.setdefault(fmt, {}).update(ops)
  for fmt, fields in FIELD_FIXES.get(arch, {}).items():
    if fmt in parsed.encodings:
      base_fields, enc_bits = parsed.encodings[fmt]
      parsed.encodings[fmt] = (base_fields + fields, enc_bits)
  return parsed


def _serialize_fields(fields: list[FieldSpec]) -> list[dict[str, int | str]]:
  return [{"name": name, "hi": hi, "lo": lo} for name, hi, lo in fields]


def _serialize_operands(operand_info: dict[str, OperandSpec], fields: list[FieldSpec], data_formats: dict[str, int]) -> list[dict[str, Any]]:
  order = {name: index for index, (name, _, _) in enumerate(fields)}
  items = sorted(operand_info.items(), key=lambda item: (order.get(item[0], len(order)), item[0]))
  out: list[dict[str, Any]] = []
  for field_name, (data_format, operand_size, operand_type) in items:
    out.append({
      "field": field_name,
      "data_format": data_format,
      "data_format_bits": None if data_format is None else data_formats.get(data_format, 0),
      "operand_size": operand_size,
      "operand_type": operand_type,
    })
  return out


def _variant_sort_key(base: str, variant: str) -> tuple[int, str]:
  return (0 if variant == base else 1, variant)


def _build_instructions(parsed: ParsedIsaXml, arch: str, pcode: dict[tuple[str, int], str]) -> list[dict[str, Any]]:
  instructions: list[dict[str, Any]] = []
  for encoding_base, ops in sorted(parsed.enums.items()):
    if encoding_base in ("HWREG", "MSG"):
      continue
    for opcode, name in sorted(ops.items()):
      variant_map = parsed.instruction_variants.get((name, encoding_base, opcode))
      source = "xml" if variant_map is not None else "fixup"
      base_operands = parsed.operand_types.get((name, encoding_base), {})
      if variant_map is None:
        variant_map = {encoding_base: base_operands}
      variants = []
      for variant_name in sorted(variant_map, key=lambda variant: _variant_sort_key(encoding_base, variant)):
        fields, enc_bits = parsed.encodings.get(variant_name, ([], None))
        operands = variant_map[variant_name] or base_operands
        variants.append({
          "encoding": variant_name,
          "encoding_bits": enc_bits,
          "fields": _serialize_fields(fields),
          "operands": _serialize_operands(operands, fields, parsed.data_formats),
        })
      instructions.append({
        "name": name,
        "opcode": opcode,
        "encoding_base": encoding_base,
        "source": source,
        "pcode": pcode.get((name, opcode)),
        "variants": variants,
      })
  return instructions


def extract_isa_dataset(arch: str) -> dict[str, Any]:
  if arch not in ARCHS:
    raise ValueError(f"unsupported architecture: {arch}. supported: {', '.join(SUPPORTED_ARCHES)}")
  cfg = ARCHS[arch]
  parsed = _apply_arch_fixes(arch, parse_xml(cfg["xml"]))
  name_to_op = {name: opcode for fmt, ops in parsed.enums.items() if fmt not in ("HWREG", "MSG") for opcode, name in ops.items()}
  pcode = extract_pcode(extract_pdf_text(cfg["pdf"], arch), name_to_op)
  return {
    "schema_version": 1,
    "arch": arch,
    "sources": {"xml_bundle_url": XML_URL, "xml_file": cfg["xml"], "pdf_url": cfg["pdf"]},
    "operand_enums": {
      enum_name: [{"value": value, "name": name} for value, name in sorted(parsed.enums.get(enum_name, {}).items())]
      for enum_name in ("HWREG", "MSG")
      if enum_name in parsed.enums
    },
    "instructions": _build_instructions(parsed, arch, pcode),
  }


def extract_isa(arch: str, out: str | Path) -> Path:
  output = Path(out)
  if output.exists() and output.is_dir():
    raise ValueError(f"output path is a directory: {output}")
  output.parent.mkdir(parents=True, exist_ok=True)
  output.write_text(json.dumps(extract_isa_dataset(arch), indent=2) + "\n")
  return output
