#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"
crate_dir="$repo_dir/wasm/mfsk-decoder"
out_dir="$repo_dir/web"

if ! command -v wasm-pack >/dev/null 2>&1; then
  echo "wasm-pack is required to build the browser MFSK decoder." >&2
  echo "Install Rust and wasm-pack, then rerun this script." >&2
  exit 1
fi

wasm-pack build "$crate_dir" --target web --release --out-dir "$crate_dir/pkg"
cp "$crate_dir/pkg/hamsdr_mfsk_decoder.js" "$out_dir/mfsk-decoder.js"
cp "$crate_dir/pkg/hamsdr_mfsk_decoder_bg.wasm" "$out_dir/mfsk-decoder_bg.wasm"
