# TinygradProfiler

Standalone RDNA4-only ATT packet timeline decoder derived from tinygrad's SQTT parser.

It does not import the parent repo's `tinygrad` package at runtime. The required AMD decode tables are vendored locally under `tinygrad_profiler/vendor`.

## What it does

- decodes a rocprof ATT `.att` blob plus its matching `.out` code object
- reconstructs the `PKTS SE:*` event stream that tinygrad renders
- serializes the event stream to deterministic JSON

## Scope

- RDNA4 / `gfx12*` only
- packet timeline only
- no frontend/UI

## Install

```bash
cd /app/tinygrad/TinygradProfiler
pip install -e .
```

## Decode an ATT trace

```bash
tinygrad-profiler decode-att \
  --att /path/to/trace.att \
  --codeobj /path/to/code_object.out \
  --target gfx1201 \
  --output /path/to/events.json
```

## Getting the input files

This tool does not collect ATT itself. You provide:

- a raw ATT trace `.att`
- the matching code object `.out`
- the RDNA4 target, for example `gfx1201`

You must already know which `.att` file matches which `.out` file. This tool only decodes a pair you provide.

These inputs typically come from `rocprofv3 --att`.
