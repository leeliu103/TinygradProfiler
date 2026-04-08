#!/usr/bin/env bash
set -euo pipefail

patch_version_from_filename() {
  local path="${1:-}"
  local name
  name="$(basename "$path")"
  if [[ "$name" =~ \.1\.0\.([0-9]+)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  else
    printf '0\n'
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

resolve_built_lib() {
  local built_lib
  built_lib="$(find "${BUILD_DIR}" -type f -name 'libhsa-amd-aqlprofile64.so.*' | sort | head -n 1)"
  if [[ -z "${built_lib}" ]]; then
    echo "built aqlprofile library not found under ${BUILD_DIR}" >&2
    exit 1
  fi
  printf '%s\n' "${built_lib}"
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKTREE_DIR="${ROOT_DIR}/worktree"
REPO_DIR="${WORKTREE_DIR}/rocm-systems"
PROJECT_DIR="${REPO_DIR}/projects/aqlprofile"
BUILD_DIR="${ROOT_DIR}/build"
PATCH_FILE="${ROOT_DIR}/patches/0001-enable-pkts-exec-tokens.patch"
UPSTREAM_URL="${UPSTREAM_URL:-https://github.com/ROCm/rocm-systems.git}"
UPSTREAM_COMMIT="$(tr -d '\n' < "${ROOT_DIR}/UPSTREAM_COMMIT")"
ROCM_ROOT="${ROCM_ROOT:-/opt/rocm}"
THREADS="${THREADS:-$(nproc)}"
BASE_AQL_LIB="${BASE_AQL_LIB:-${ROCM_ROOT}/lib/libhsa-amd-aqlprofile64.so.1}"
ROCM_PATCH_VERSION="${ROCM_PATCH_VERSION:-$(patch_version_from_filename "$(readlink -f "${BASE_AQL_LIB}" 2>/dev/null || printf '%s' "${BASE_AQL_LIB}")")}"

require_cmd git
require_cmd cmake
require_cmd c++

mkdir -p "${WORKTREE_DIR}"

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  git clone --filter=blob:none --no-checkout "${UPSTREAM_URL}" "${REPO_DIR}"
  git -C "${REPO_DIR}" sparse-checkout init --cone
fi

git -C "${REPO_DIR}" remote set-url origin "${UPSTREAM_URL}"
git -C "${REPO_DIR}" fetch --depth 1 origin "${UPSTREAM_COMMIT}"
git -C "${REPO_DIR}" sparse-checkout set projects/aqlprofile
git -C "${REPO_DIR}" checkout --force "${UPSTREAM_COMMIT}"
git -C "${REPO_DIR}" clean -fdx projects/aqlprofile
git -C "${REPO_DIR}" apply "${PATCH_FILE}"

rm -rf "${BUILD_DIR}"

cmake \
  -S "${PROJECT_DIR}" \
  -B "${BUILD_DIR}" \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PREFIX_PATH="${ROCM_ROOT}" \
  -DROCM_PATCH_VERSION="${ROCM_PATCH_VERSION}" \
  -DCPACK_GENERATOR=STGZ \
  -DAQLPROFILE_BUILD_TESTS=OFF \
  -DCPACK_OBJCOPY_EXECUTABLE="${ROCM_ROOT}/llvm/bin/llvm-objcopy" \
  -DCPACK_READELF_EXECUTABLE="${ROCM_ROOT}/llvm/bin/llvm-readelf" \
  -DCPACK_STRIP_EXECUTABLE="${ROCM_ROOT}/llvm/bin/llvm-strip" \
  -DCPACK_OBJDUMP_EXECUTABLE="${ROCM_ROOT}/llvm/bin/llvm-objdump"

cmake --build "${BUILD_DIR}" -j"${THREADS}"

built_lib="$(resolve_built_lib)"
echo "built ${built_lib}"
echo "use with:"
echo "  LD_LIBRARY_PATH=${BUILD_DIR}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH} rocprofv3 --att ..."
