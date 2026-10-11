#!/usr/bin/env bash
# Collect immutable upstream sources outside the product source tree.
set -euo pipefail
out=${1:?Usage: bash collect_third_party_sources.sh NEW_OUTPUT_DIRECTORY}
if [[ -e "$out" ]]; then
    echo "Refusing to overwrite existing output: $out" >&2
    exit 1
fi
mkdir -p "$out"
out=$(realpath "$out")
git clone --depth 1 --branch 1.0.20260102 https://github.com/WireGuard/wireguard-android.git "$out/wireguard-android"
root="$out/wireguard-android"
test "$(git -C "$root" rev-parse HEAD)" = 09b75c2bd37f749e2a8c85876394854113c74be7
test "$(git -C "$root" rev-parse HEAD:tunnel/tools/wireguard-tools)" = e2ecaaa739144997ccff89d6ad6ec81698ea6ced
test "$(git -C "$root" rev-parse HEAD:tunnel/tools/elf-cleaner)" = 7efc05090675ec6161b7def862728086a26c3b1f
mkdir "$out/archives"
git -C "$root" archive --format=tar --prefix=wireguard-android/ HEAD | gzip -n > "$out/archives/wireguard-android.tar.gz"
fetch() { curl --fail --location --retry 3 --connect-timeout 15 --max-time 300 "$1" -o "$2"; }
fetch https://codeload.github.com/WireGuard/wireguard-tools/tar.gz/e2ecaaa739144997ccff89d6ad6ec81698ea6ced "$out/archives/wireguard-tools.tar.gz"
fetch https://codeload.github.com/termux/termux-elf-cleaner/tar.gz/7efc05090675ec6161b7def862728086a26c3b1f "$out/archives/elf-cleaner.tar.gz"
while read -r module version name; do
    fetch "https://proxy.golang.org/$module/@v/$version.zip" "$out/archives/$name.zip"
    fetch "https://proxy.golang.org/$module/@v/$version.mod" "$out/archives/$name.mod"
done <<'MODULES'
golang.zx2c4.com/wireguard v0.0.0-20250521234502-f333402bd9cb wireguard-go
golang.org/x/sys v0.33.0 golang-x-sys
golang.org/x/crypto v0.38.0 golang-x-crypto
golang.org/x/net v0.40.0 golang-x-net
golang.org/x/term v0.32.0 golang-x-term
golang.org/x/text v0.25.0 golang-x-text
golang.zx2c4.com/wintun v0.0.0-20230126152724-0fa3db229ce2 wintun
github.com/google/btree v1.1.2 google-btree
golang.org/x/time v0.7.0 golang-x-time
gvisor.dev/gvisor v0.0.0-20250503011706-39ed1f5ac29c gvisor
MODULES
fetch https://go.dev/dl/go1.24.3.src.tar.gz "$out/archives/go1.24.3.src.tar.gz"
fetch https://codeload.github.com/google/desugar_jdk_libs/tar.gz/73170c345e6a762fc6a1f0301bb15218850023ef "$out/archives/desugar_jdk_libs-2.1.5.tar.gz"
cp "$0" "$out/archives/collect_third_party_sources.sh"
(cd "$out/archives" && sha256sum ./* > SHA256SUMS)
echo "WireGuard source collection complete: $out/archives"
echo "Also complete docs/THIRD_PARTY_RELEASE.md before publishing an APK."
