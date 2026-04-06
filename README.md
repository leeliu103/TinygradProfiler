# TinygradProfiler

Standalone ATT packet timeline profiler and AMD ISA extractor derived from tinygrad.
Captures a single GPU kernel's ATT trace via `rocprofv3`, decodes the RDNA4 packet stream,
and either serves an interactive PKTS timeline UI or writes a serialized agent bundle.
Also extracts RDNA3/RDNA4 ISA instruction metadata and pseudocode to JSON.

## Prerequisites

- Python >= 3.11
- AMD RDNA4 GPU (`gfx12*`) — required for `profile-webui` and `profile-agent`
- [ROCm](https://rocm.docs.amd.com/) with `rocprofv3` on `PATH` — the profiling commands invoke it as a subprocess
- `extract-isa` has no GPU or ROCm requirement (it downloads and parses AMD's published ISA sources)

## Install

```bash
git clone <repo-url> && cd TinygradProfiler
pip install -e .
```

## Capture and serve the PKTS UI

```bash
tinygrad-profiler profile-webui \
  --kernel-filter matmul_kernel \
  --kernel-iteration 1 \
  --se 0 \
  --simd 1 \
  --cu 0 \
  -- python your_program.py
```

- runs two passes: discovery (finds kernels), then ATT capture on the selected kernel
- `--kernel-filter` is optional — if the program dispatches a single kernel it is auto-selected
- serves the timeline UI on `0.0.0.0:8001`, accessible from other machines via `<host-ip>:8001`

## Capture and write the agent bundle

```bash
tinygrad-profiler profile-agent \
  --kernel-filter matmul_kernel \
  --kernel-iteration 1 \
  --se 0 \
  --simd 1 \
  --cu 0 \
  -- python your_program.py
```

- `profile-agent` uses the same capture path and kernel selection flow as `profile-webui`
- it writes `metadata.json` plus serialized `events.json` under `runs/<id>/agent/`
- no server is started — the output is a static bundle for downstream tooling or LLM agents


## Extract AMD ISA JSON

```bash
tinygrad-profiler extract-isa \
  --arch rdna4 \
  --out /path/to/rdna4_isa.json
```

`--arch rdna3` is also supported.

Downloads AMD's ISA sources (XML + PDF), merges encoding metadata with pseudocode,
and writes a single JSON file. Results are cached under `~/.cache/tinygrad-profiler/amd_isa/`.

