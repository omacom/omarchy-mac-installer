#!/bin/bash
# Run the Mac Studio (M3 Ultra) stage 0 collector against stubbed macOS tools:
# board and macOS gates, and removal of identifying lines.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
kit="$root/Packaging/private-test/m3-ultra/collect-stage0.sh"
work=$(mktemp -d)
trap '/bin/rm -rf "$work"' EXIT
bin="$work/bin"
mkdir -p "$bin" "$work/home/Desktop"

cat >"$bin/sysctl" <<'EOF'
#!/bin/bash
case "$2" in
  hw.targettype) printf '%s\n' "$TEST_TARGET" ;;
  hw.model) echo "Mac15,14" ;;
  machdep.cpu.brand_string) echo "Apple M3 Ultra" ;;
  hw.memsize) echo 103079215104 ;;
  hw.ncpu) echo 32 ;;
  hw.perflevel0.logicalcpu) echo 24 ;;
  hw.perflevel1.logicalcpu) echo 8 ;;
  *) exit 1 ;;
esac
EOF
cat >"$bin/sw_vers" <<'EOF'
#!/bin/bash
[[ $1 == -productVersion ]] && echo "$TEST_MACOS" || echo 26A434
EOF
cat >"$bin/ioreg" <<'EOF'
#!/bin/bash
cat <<'TREE'
+-o gfx-asc  <class IOService>
    "firmware-uuid" = "0E6C1493-224F-3E07-8402-67BE342429B4"
    "serial-number" = <4331323334353637>
    "IOPlatformSerialNumber" = "C12345678"
    "local-mac-address" = <aabbccddeeff>
    "mac-text" = "aa:bb:cc:dd:ee:ff"
    "mac-address-wifi0" = <112233445566>
    "host-mac-address" = <665544332211>
    "dram-ecid-ch04" = <0123456789abcdef>
    "entangle-nonce" = <00112233>
    "random-seed" = <deadbeef>
    "unique-device-id" = <0102030405060708>
    "die-id" = <0102030405060708>
    "IOCompatibilityProperties" = {"IOName"="memory","compat-dimm-serial-number"=<"0x01234567","0x76543210">,"ecid-copy"=<01>}
    "IOHarmless" = {"IOName"="memory","ApplePowerCounter"=Yes}
    "vuid" = "AAAAAAAA-0000-1111-2222-333333333333"
    "IOMACAddress" = <aabbccddeeff>
    "oslog-uuid" = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    "IOConsoleUsers" = ({"kCGSSessionUserNameKey"="testowner","kCGSSessionUserIDKey"=501,"CGSSessionUniqueSessionUUID"="1A2B3C4D-1111-2222-3333-444455556666"})
    "good-samaritan-message" = <"If found call +99 555 0100 or testowner@example.invalid">
    "controller-unique-id" = "TESTCTRLID0123456789"
    "Controller Characteristics" = {"default-bits-per-cell"=3,"firmware-version"="241.0.12","controller-unique-id"="TESTNESTEDCTRL99    ","capacity"=1}
    "BluetoothUHEDevices" = <0011223344556677>
    "wifi-calibration-msf" = <8899aabbccddeeff>
    "sfr-manifest-data" = <0123456789abcdef>
    "fmm-mobileme-token-FMM" = <5445535446414b45>
    "computer-name" = "TestOwnerMac"
    "host-name" = "testowner-studio"
    "uuid" = <"ABCDEF01-2222-3333-4444-555566667777">
    "IOUserClientCreator" = "pid 321, TestPrivateApp"
    "gpu-core-count" = 80
TREE
EOF
cat >"$bin/system_profiler" <<'EOF'
#!/bin/bash
printf 'Hardware:\n      Model Name: Mac Studio\n      Serial Number (system): C12345678\n      Hardware UUID: 11111111-2222-3333-4444-555555555555\n'
EOF
cat >"$bin/ditto" <<'EOF'
#!/bin/bash
tar -cf "${@: -1}" -C "$(dirname "${@: -2:1}")" "$(basename "${@: -2:1}")"
EOF
cat >"$bin/shasum" <<'EOF'
#!/bin/bash
sha256sum "${@: -1}"
EOF
chmod +x "$bin"/*

# Point the kit at the stubs.
/usr/bin/sed \
  -e "s#^SYSCTL=.*#SYSCTL=$bin/sysctl#" -e "s#^SW_VERS=.*#SW_VERS=$bin/sw_vers#" \
  -e "s#^IOREG=.*#IOREG=$bin/ioreg#" -e "s#^SYSTEM_PROFILER=.*#SYSTEM_PROFILER=$bin/system_profiler#" \
  -e "s#^DITTO=.*#DITTO=$bin/ditto#" -e "s#^SHASUM=.*#SHASUM=$bin/shasum#" \
  -e 's#/usr/bin/uname -s#echo Darwin#' "$kit" >"$work/kit.sh"

fail() { echo "not ok - $*" >&2; exit 1; }
run() { # TARGET MACOS
  TEST_TARGET=$1 TEST_MACOS=$2 HOME="$work/home" bash "$work/kit.sh" >"$work/out" 2>"$work/err" && status=0 || status=$?
}
collected() { compgen -G "$work/home/Desktop/omarchy-m3-ultra-stage0-*" >/dev/null; }

run J516sAP 27.0.1
[[ $status == 1 ]] && ! collected && grep -q "only for the Mac Studio (M3 Ultra" "$work/err" || fail "another Mac is refused before any collection"
for old in 15.3.2 26.6.2 27.0; do
  run J575dAP $old
  [[ $status == 2 ]] && ! collected && grep -q "needs macOS 27.0.1 or later" "$work/err" || fail "macOS $old is told to update first"
done
for ok in 27.0.1 27.0.2 27.1 28.0; do
  /bin/rm -rf "$work/home/Desktop"; mkdir -p "$work/home/Desktop"
  run J575dAP $ok
  [[ $status == 0 ]] && collected || fail "macOS $ok collects (status $status: $(cat "$work/err"))"
done
echo "ok - the kit refuses other Macs and macOS older than 27.0.1"

dir=$(echo "$work"/home/Desktop/omarchy-m3-ultra-stage0-*/)
dir=${dir%% *}
[[ -s $dir/summary.txt && -s $dir/device-tree.txt && -s $dir/gpu.txt && -s $dir/hardware.txt ]] || fail "all four files are written"
grep -q "model: Mac15,14" "$dir/summary.txt" || fail "summary names the model"
for f in "$dir"/*.txt; do
  if grep -q -i -E 'C12345678|serial-number|local-mac-address|mac-address|IOMACAddress|ecid|nonce|random-seed|unique-device-id|die-id|vuid|compat-dimm|0x01234567|aa:bb:cc|11111111-2222|Hardware UUID' "$f"; then
    fail "identifying lines remain in $f: $(grep -i -E "C12345678|serial-number|local-mac-address|mac-address|IOMACAddress|ecid|nonce|random-seed|unique-device-id|die-id|vuid|compat-dimm|0x01234567|aa:bb:cc|11111111-2222|Hardware UUID" "$f")"
  fi
done
grep -q "xx:xx:xx:xx:xx:xx" "$dir/device-tree.txt" || fail "a network address inside another line is masked"
grep -q "0E6C1493-224F-3E07-8402-67BE342429B4" "$dir/device-tree.txt" || fail "the firmware image uuid is kept"
grep -q "ApplePowerCounter" "$dir/device-tree.txt" || fail "a nested name that is not an identifier is kept"
grep -q "oslog-uuid" "$dir/device-tree.txt" || fail "other uuid keys are kept"
if grep -q -E 'testowner|555 0100|example.invalid|TESTCTRLID|TESTNESTEDCTRL|1A2B3C4D-1111|ABCDEF01-2222|TestPrivateApp|AAAAAAAA-BBBB' "$dir/device-tree.txt" "$dir/gpu.txt"; then
  fail "owner name, contact text, controller id, volume uuid and app names are removed or masked"
fi
if grep -q -i -E 'bluetooth|calibration|manifest-data|fmm-|computer-name|host-name|TestOwnerMac|testowner-studio' "$dir/device-tree.txt" "$dir/gpu.txt"; then
  fail "pairing and factory calibration blobs are removed"
fi
grep -q '"uuid" = <"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx">' "$dir/device-tree.txt" || fail "a uuid value is masked and its key kept"
grep -q '"gpu-core-count" = 80' "$dir/gpu.txt" || fail "hardware facts are kept"
[[ -s "$dir.zip" ]] || { zipfile=$(echo "$work"/home/Desktop/*.zip); [[ -s ${zipfile%% *} ]] || fail "the zip is written"; }
grep -q "SHA-256:" "$work/out" || fail "the digest is printed"
echo "ok - identifying lines are removed, firmware facts stay"
bash -n "$kit" && echo "ok - the kit parses"
