# TinygradProfiler

Standalone RDNA4-only ATT packet timeline decoder plus AMD ISA extractor derived from tinygrad.

It does not import the parent repo's `tinygrad` package at runtime. The required AMD decode tables are vendored locally under `tinygrad_profiler/vendor`.

## What it does

- decodes a rocprof ATT `.att` blob plus its matching `.out` code object
- reconstructs the `PKTS SE:*` event stream that tinygrad renders
- serializes the event stream to deterministic JSON
- can capture one kernel, build a PKTS webpage bundle, and serve it with `profile-webui`
- can extract RDNA3/RDNA4 ISA metadata and pseudocode into a single JSON file

## Scope

- ATT timeline decode: RDNA4 / `gfx12*` only
- ISA extraction: `rdna3` and `rdna4`
- minimal PKTS webpage via `profile-webui`, not the full tinygrad viz app

## Install

```bash
cd /app/tinygrad/TinygradProfiler
pip install -e .
```

## Extract AMD ISA JSON

```bash
tinygrad-profiler extract-isa \
  --arch rdna4 \
  --out /path/to/rdna4_isa.json
```

`--arch rdna3` is also supported.

That downloads and caches:

- AMD's machine-readable ISA zip
- the matching ISA PDF for the selected arch

It merges the XML encoding and operand metadata with PDF pseudocode extraction, then writes one JSON file to `--out`.

The download cache lives under `~/.cache/tinygrad-profiler/amd_isa/` unless `XDG_CACHE_HOME` is set.

## Decode an ATT trace

```bash
tinygrad-profiler decode-att \
  --att /path/to/trace.att \
  --codeobj /path/to/code_object.out \
  --target gfx1201 \
  --output /path/to/events.json
```

## Capture and serve the PKTS UI

```bash
tinygrad-profiler profile-webui \
  --kernel-name matmul_kernel \
  --kernel-iteration 1 \
  --se 0 \
  --simd 1 \
  --cu 0 \
  -- python your_program.py
```

- `--kernel-name` must match the formatted kernel name seen by `rocprofv3`
- the page is served on `0.0.0.0:8001`

## Getting the input files

For `decode-att`, you provide:

- a raw ATT trace `.att`
- the matching code object `.out`
- the RDNA4 target, for example `gfx1201`

You must already know which `.att` file matches which `.out` file. This tool only decodes a pair you provide.

These inputs typically come from `rocprofv3 --att`.
