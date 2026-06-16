#!/usr/bin/env bash
# Fetch EmulatorJS v4.x bundle from upstream distribution.
# Extracts to /opt/slothos-roms/emujs/. Records SHA256 for reproducibility.
#
# Upstream: https://emulatorjs.org/ — loader.js / emulator.min.js / cores/*.wasm
#
# This is the official EmulatorJS distribution bundle (not linuxserver/emulatorjs Docker).

set -euo pipefail

DEST_DIR="${1:-/opt/slothos-roms/emujs}"
# Pin a stable release. See https://github.com/EmulatorJS/EmulatorJS/releases
EMUJS_VERSION="${EMUJS_VERSION:-4.2.3}"
# EmulatorJS publishes a 7z bundle per release at GitHub
TARBALL_URL="https://github.com/EmulatorJS/EmulatorJS/releases/download/v${EMUJS_VERSION}/${EMUJS_VERSION}.7z"
SHA256_FILE="${DEST_DIR}.sha256"

mkdir -p "${DEST_DIR}"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

echo ">> Downloading EmulatorJS v${EMUJS_VERSION}..."
echo "   URL: ${TARBALL_URL}"
curl -fsSL -o "${TMPDIR}/bundle.7z" "${TARBALL_URL}"

SHA="$(sha256sum "${TMPDIR}/bundle.7z" | awk '{print $1}')"
echo "   SHA256: ${SHA}"
echo "${SHA}" > "${SHA256_FILE}"

echo ">> Extracting to ${DEST_DIR}..."
7z x -y -o"${TMPDIR}/unpacked" "${TMPDIR}/bundle.7z" >/dev/null

# 7z extracts a "data/" subdir; sync its contents into DEST_DIR/data/
if [ -d "${TMPDIR}/unpacked/data" ]; then
    mkdir -p "${DEST_DIR}/data"
    rsync -a --delete "${TMPDIR}/unpacked/data/" "${DEST_DIR}/data/"
else
    mkdir -p "${DEST_DIR}/data"
    rsync -a --delete "${TMPDIR}/unpacked/" "${DEST_DIR}/data/"
fi

echo ">> Verifying loader.js present..."
if [ ! -f "${DEST_DIR}/data/loader.js" ]; then
    echo "ERROR: ${DEST_DIR}/data/loader.js missing after extract" >&2
    exit 1
fi

echo ">> Done. EmulatorJS bundle at ${DEST_DIR}/data/"
echo "   loader.js: ${DEST_DIR}/data/loader.js"
ls -1 "${DEST_DIR}/data/" | head -20
