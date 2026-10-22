#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
PICKER_BIN="${PICKER_BIN:-picker}"
OUT_DIR="${E203_DUT_DIR:-${PROJECT_ROOT}/output/toffee_e203}"
BUILD_THREADS="${BUILD_THREADS:-0}"

if [[ -z "${E203_FILELIST:-}" ]]; then
  if [[ -z "${PICKER_ROOT:-}" ]]; then
    echo "set E203_FILELIST, or set PICKER_ROOT to a Picker checkout" >&2
    exit 2
  fi
  E203_FILELIST="${PICKER_ROOT}/example/e203_ifu_ift2icb/filelist.txt"
fi

if [[ -z "${PICKER_TEMPLATE_DIR:-}" ]]; then
  if [[ -z "${PICKER_ROOT:-}" ]]; then
    echo "set PICKER_TEMPLATE_DIR, or set PICKER_ROOT to a Picker checkout" >&2
    exit 2
  fi
  PICKER_TEMPLATE_DIR="${PICKER_ROOT}/template"
fi

"${PICKER_BIN}" export \
  --fs "${E203_FILELIST}" \
  --lang python \
  --sname e203_ifu_ift2icb \
  --tname e203_ifu_ift2icb \
  --tdir "${OUT_DIR}" \
  --sdir "${PICKER_TEMPLATE_DIR}" \
  --autobuild true \
  --rw mem_direct \
  --vpi \
  --coverage \
  -V "--no-timing" \
  -j "${BUILD_THREADS}"

printf 'Generated e203 DUT: %s\n' "${OUT_DIR}"
