# Patched `aqlprofile` helper

This directory contains a small helper for building a patched RDNA4 `aqlprofile`.

It does not vendor `rocm-systems` in git. Instead it:

1. fetches a pinned `rocm-systems` commit
2. sparse-checks out only `projects/aqlprofile`
3. applies one local patch
4. builds a local `libhsa-amd-aqlprofile64.so`

## Files

- `UPSTREAM_COMMIT`
  - pins the exact `rocm-systems` commit the patch is applied against
- `patches/0001-gfx12-enable-pkts-exec-tokens.patch`
  - local `gfx12` patch
  - changes the ATT token mask so `ALUEXEC` and `VMEMEXEC` are not excluded
- `scripts/build.sh`
  - does the full fetch + sparse checkout + patch apply + CMake build flow

Generated local state is not tracked:

- `worktree/`
  - sparse checkout of `rocm-systems`
- `build/`
  - local CMake build output

## Usage

Build the patched library:

```bash
cd /app/tinygrad/TinygradProfiler/tools/aqlprofile
./scripts/build.sh
```

Then run `rocprofv3` with the local build ahead of the default ROCm library path:

```bash
LD_LIBRARY_PATH="$PWD/build${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" rocprofv3 --att ...
```

That affects only that process launch. It does not overwrite the system ROCm install.

## Notes

- This helper is RDNA4-focused. The local patch only changes the `gfx12` ATT token mask.
- The goal is to keep the ATT exec-completion tokens needed by the PKTS timeline rows.
- This makes `.att` richer for `TinygradProfiler`, but ATT capture scope and buffer-loss behavior are otherwise unchanged.
- Validated on this RDNA4 machine with a Triton GEMM capture: stock ATT had no `ALUEXEC` / `VMEMEXEC`; the patched build restored both.
