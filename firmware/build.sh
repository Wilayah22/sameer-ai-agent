#!/usr/bin/env bash
# Build the StackChan firmware with the Sameer app. Needs ESP-IDF v5.5.4 (idf.py on PATH).
# Output: build-sameer/sameer-stackchan.bin (single image, flash at 0x0).
set -euo pipefail

STACKCHAN_COMMIT=1b5765599fba8aaad1811d9a79358ccc7051f5f3
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$HERE/build-sameer"

mkdir -p "$WORK"
if [ ! -d "$WORK/StackChan/.git" ]; then
    git clone https://github.com/m5stack/StackChan.git "$WORK/StackChan"
fi
git -C "$WORK/StackChan" fetch --depth 1 origin "$STACKCHAN_COMMIT"
git -C "$WORK/StackChan" checkout --force "$STACKCHAN_COMMIT"

cd "$WORK/StackChan/firmware"
python3 fetch_repos.py
python3 "$HERE/add_sameer_app.py" . "${SAMEER_SERVER_URL:-}" "${SAMEER_DEVICE_TOKEN:-}"

idf.py set-target esp32s3
idf.py build
idf.py merge-bin -o sameer-stackchan.bin
cp build/sameer-stackchan.bin "$WORK/sameer-stackchan.bin"
echo "Firmware: $WORK/sameer-stackchan.bin"
