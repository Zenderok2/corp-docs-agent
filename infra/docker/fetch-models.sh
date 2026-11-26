#!/usr/bin/env bash
set -euo pipefail

destination=${1:-runtime/models}
mkdir -p "$destination"

fetch() {
  local filename=$1 checksum=$2 url=$3
  if printf '%s  %s\n' "$checksum" "$destination/$filename" | sha256sum --check --status; then
    printf '%s уже проверен.\n' "$filename"
    return
  fi
  curl --fail --location --show-error --silent --retry 3 --continue-at - \
    --output "$destination/$filename.part" "$url"
  printf '%s  %s\n' "$checksum" "$destination/$filename.part" | sha256sum --check
  mv -- "$destination/$filename.part" "$destination/$filename"
}

fetch chat.gguf 2fde00ce69dd4899c70d020845e2638353015bba0fdf161b3eb965f2bca4464e \
  https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/resolve/ae44f08e1392f39c0e474af10c3ff8355c8b6688/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf

fetch embedding.gguf 06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439 \
  https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/370f27d7550e0def9b39c1f16d3fbaa13aa67728/Qwen3-Embedding-0.6B-Q8_0.gguf
