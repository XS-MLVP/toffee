#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
export E203_DUT_DIR="${E203_DUT_DIR:-${PROJECT_ROOT}/output/toffee_e203}"
export E203_ARTIFACT_DIR="${E203_ARTIFACT_DIR:-${PROJECT_ROOT}/output/e203-verification}"

EXTRA_PYTHONPATH=""
if [[ -n "${XCOMM_PYTHON:-}" ]]; then
  EXTRA_PYTHONPATH=":${XCOMM_PYTHON}"
fi
export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}/examples/integration/e203${EXTRA_PYTHONPATH}${PYTHONPATH:+:${PYTHONPATH}}"

python3 -m pytest -v "${PROJECT_ROOT}/examples/integration/e203/test_e203_toffee.py" "$@"
