#!/usr/bin/env bash
# Downloads a prebuilt PDFium (bblanchon/pdfium-binaries) into ./.pdfium,
# verifies its SHA-256, and prints the export line for PDFIUM_DYNAMIC_LIB_PATH.
#
#   scripts/fetch-pdfium.sh                         # build 7543, host platform
#   PDFIUM_DIR=/tmp/pdfium scripts/fetch-pdfium.sh  # somewhere else
#   eval "$(scripts/fetch-pdfium.sh)"               # fetch + export in one go
#
# Build 7543 is the PDFium ABI pdfium-render 0.8.37 targets by default
# (feature "pdfium_latest" = "pdfium_7543"); a newer build may load but is not
# what the bindings were generated against. Checksums are pinned for Linux
# x64/arm64 (what CI uses); elsewhere the script prints the hash it got.
set -euo pipefail

build="${PDFIUM_BUILD:-7543}"
dest="${PDFIUM_DIR:-$(cd "$(dirname "$0")/.." && pwd)/.pdfium}"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)  asset=pdfium-linux-x64.tgz;   lib=libpdfium.so ;;
  Linux-aarch64) asset=pdfium-linux-arm64.tgz; lib=libpdfium.so ;;
  Darwin-arm64)  asset=pdfium-mac-arm64.tgz;   lib=libpdfium.dylib ;;
  Darwin-x86_64) asset=pdfium-mac-x64.tgz;     lib=libpdfium.dylib ;;
  *) echo "unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

expected=""
if [ "$build" = "7543" ]; then
  case "$asset" in
    pdfium-linux-x64.tgz)   expected=9329a3c4b19b3c8d0a93af5440f44be84e4bd879a204e47b1a7a160e96809da4 ;;
    pdfium-linux-arm64.tgz) expected=4965a4c0b64c45b5edefa1072e2b483bf90b4d25d7deec44f104dcbdecf05c3e ;;
  esac
fi

if [ -f "$dest/lib/$lib" ] && grep -q "BUILD=$build" "$dest/VERSION" 2>/dev/null; then
  echo "PDFium $build already in $dest" >&2
else
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  url="https://github.com/bblanchon/pdfium-binaries/releases/download/chromium%2F${build}/${asset}"
  echo "downloading $url" >&2
  curl -fsSL --retry 3 -o "$tmp/pdfium.tgz" "$url"
  if command -v sha256sum >/dev/null; then
    actual="$(sha256sum "$tmp/pdfium.tgz" | cut -d' ' -f1)"
  else
    actual="$(shasum -a 256 "$tmp/pdfium.tgz" | cut -d' ' -f1)"
  fi
  if [ -n "$expected" ] && [ "$actual" != "$expected" ]; then
    echo "checksum mismatch for $asset: got $actual, expected $expected" >&2
    exit 1
  fi
  [ -z "$expected" ] && echo "sha256 $actual (not pinned for $asset)" >&2
  rm -rf "$dest"
  mkdir -p "$dest"
  tar -xzf "$tmp/pdfium.tgz" -C "$dest"
fi

echo "export PDFIUM_DYNAMIC_LIB_PATH=$dest/lib"
