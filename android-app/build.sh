#!/usr/bin/env bash
# Builds the signed Pumpvision APK without Gradle.
#
# Gradle/AGP buy nothing here: this is one Activity with no dependencies, so the
# SDK build tools do the whole job and there is no AGP<->Gradle<->JDK version
# matrix to keep working.
#
# Toolchain is local to this machine (no sudo was available to apt-install):
#   JDK    ~/tools/jdk-17*        Android SDK  ~/Android/sdk
# The signing key lives OUTSIDE the repo (~/.android-keys) so it can never be
# committed. Losing it means no future update can install over this app --
# Android requires every update to carry the same signature.
set -euo pipefail

export JAVA_HOME="${JAVA_HOME:-$HOME/tools/jdk-17.0.20.1+1}"
export PATH="$JAVA_HOME/bin:$PATH"
ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/sdk}"
BT="$ANDROID_HOME/build-tools/34.0.0"
PLATFORM="$ANDROID_HOME/platforms/android-34/android.jar"

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$APP/build"
KS="$HOME/.android-keys/pumpvision-release.jks"
KSPW="$(cat "$HOME/.android-keys/pumpvision-release.password")"

rm -rf "$BUILD"; mkdir -p "$BUILD/res" "$BUILD/classes" "$BUILD/dex"

echo "[1/6] compiling resources"
"$BT/aapt2" compile --dir "$APP/res" -o "$BUILD/res/resources.zip"

echo "[2/6] linking resources + manifest"
"$BT/aapt2" link \
    -o "$BUILD/unsigned-noded.apk" \
    -I "$PLATFORM" \
    --manifest "$APP/AndroidManifest.xml" \
    -A "$APP/assets" \
    --java "$BUILD/gen" \
    --min-sdk-version 24 --target-sdk-version 34 \
    "$BUILD/res/resources.zip"

echo "[3/6] compiling java"
mkdir -p "$BUILD/gen"
javac -source 17 -target 17 -nowarn \
    -classpath "$PLATFORM" \
    -d "$BUILD/classes" \
    $(find "$APP/src" "$BUILD/gen" -name '*.java')

echo "[4/6] dexing"
"$BT/d8" --min-api 24 --output "$BUILD/dex" \
    $(find "$BUILD/classes" -name '*.class')

echo "[5/6] packaging"
cp "$BUILD/unsigned-noded.apk" "$BUILD/unsigned.apk"
( cd "$BUILD/dex" && zip -q "$BUILD/unsigned.apk" classes.dex ) 2>/dev/null || \
  python3 - "$BUILD/unsigned.apk" "$BUILD/dex/classes.dex" <<'PY'
import sys, zipfile
apk, dex = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(apk, 'a', zipfile.ZIP_DEFLATED) as z:
    z.write(dex, 'classes.dex')
print("    classes.dex added via python")
PY
"$BT/zipalign" -f -p 4 "$BUILD/unsigned.apk" "$BUILD/aligned.apk"

echo "[6/6] signing"
"$BT/apksigner" sign \
    --ks "$KS" --ks-key-alias pumpvision \
    --ks-pass "pass:$KSPW" --key-pass "pass:$KSPW" \
    --out "$BUILD/pumpvision.apk" \
    "$BUILD/aligned.apk"
"$BT/apksigner" verify --print-certs "$BUILD/pumpvision.apk" | head -4

echo
echo "APK: $BUILD/pumpvision.apk"
ls -lh "$BUILD/pumpvision.apk"
