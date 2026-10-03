#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"
crate_dir="$repo_dir/wasm/cw-decoder"
wasm-pack build "$crate_dir" --target web --release --out-dir pkg -- --locked
cp "$crate_dir/pkg/hamsdr_cw_decoder.js" "$repo_dir/web/cw-decoder.js"
cp "$crate_dir/pkg/hamsdr_cw_decoder_bg.wasm" "$repo_dir/web/cw-decoder_bg.wasm"
