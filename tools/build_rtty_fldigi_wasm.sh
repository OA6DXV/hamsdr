#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"
source_dir="$repo_dir/wasm/rtty-fldigi"
"${EMXX:-em++}" "$source_dir/decoder.cpp" "$source_dir/upstream/fftfilt.cxx" \
  -I"$source_dir" -I"$source_dir/upstream" -std=c++17 -O3 \
  -s MODULARIZE=1 -s EXPORT_ES6=1 -s ENVIRONMENT=web,worker,node -s DYNAMIC_EXECUTION=0 \
  -s ALLOW_MEMORY_GROWTH=1 -s INITIAL_MEMORY=16777216 \
  -s EXPORTED_FUNCTIONS='["_rtty_create","_rtty_destroy","_rtty_configure","_rtty_process","_rtty_metric","_malloc","_free"]' \
  -s EXPORTED_RUNTIME_METHODS='["UTF8ToString"]' \
  -o "$repo_dir/web/rtty-fldigi.mjs"
