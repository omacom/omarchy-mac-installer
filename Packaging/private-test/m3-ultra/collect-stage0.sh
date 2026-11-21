#!/bin/bash
# Stage 0 of the Mac Studio (M3 Ultra, 2025) test. Read-only: it collects the
# model, system firmware version and hardware-tree facts into one zip on the
# Desktop. It installs nothing and changes nothing outside that folder.
# Runs with the bash that macOS ships (3.2).
set -euo pipefail

SYSCTL=/usr/sbin/sysctl
SW_VERS=/usr/bin/sw_vers
IOREG=/usr/sbin/ioreg
SYSTEM_PROFILER=/usr/sbin/system_profiler
DITTO=/usr/bin/ditto
SHASUM=/usr/bin/shasum
KIT_VERSION="stage0-2"
MINIMUM_MACOS="27.0.1"

# Property names that identify one Mac or one owner, matched anywhere in the
# name (mac-address-wifi0, dram-ecid-ch04, ...). Their lines are removed. This
# also covers the console user (IOConsoleUsers: account name, user id, session),
# the lock-screen contact text (good-samaritan-message), the storage controller's
# unique id, the running programs' names (IOUserClientCreator), and per-Mac factory or
# pairing data (Bluetooth device records, calibration blobs, installer and boot manifest data).
DROP_KEYS='^[[:space:]|+-]*"[^"]*(mac-?addr|ecid|serial|unique-chip-id|unique-device-id|die-id|vuid|udid|IOPlatformUUID|boot-uuid|device-uuid|hw-uuid|random-seed|nonce|fm-account-identifier|boot-manifest-hash|boot-objects-path|IOConsoleUsers|good-samaritan|controller-unique-id|IOUserClientCreator|bluetooth|IDInstallerData|calibration|manifest-data|manifest-hash|sidp-rom|rom-panic|Tunnel Endpoint GUID|fmm-|computer-name|host-?name)[^"]*" *=|^[[:space:]|+-]*"CEPO" *='
# The same names inside a dictionary value, which ioreg prints on one line as "name"=<value>.
DROP_NESTED='"[^"]*(mac-?addr|ecid|serial-number|unique-chip-id|unique-device-id|udid|vuid|die-id|UserName|UniqueSessionUUID|controller-unique-id|IOUserClientCreator|good-samaritan)[^"]*"='
DROP_TEXT='Serial Number|Hardware UUID|Provisioning UDID|Activation Lock|Serial number'
# A UUID value names one volume, disk or session of this Mac. It is masked everywhere
# except on a line whose property name starts with "firmware" (the firmware image uuid is
# a fact about the firmware, not the Mac).
UUID_RE='[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}'

scrub() {
  LC_ALL=C /usr/bin/grep -a -v -i -E "$DROP_KEYS|$DROP_NESTED|$DROP_TEXT" |
    LC_ALL=C /usr/bin/sed -E -e 's/[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}/xx:xx:xx:xx:xx:xx/g' \
      -e "/^[ |+-]*\"firmware[^\"]*\" *=/!s/$UUID_RE/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/g"
}

# True when dotted version $1 is at least $2 (numeric parts; missing parts are 0).
at_least() {
  local IFS=. have want i
  read -r -a have <<<"$1"
  read -r -a want <<<"$2"
  for i in 0 1 2; do
    if (( ${have[$i]:-0} > ${want[$i]:-0} )); then return 0; fi
    if (( ${have[$i]:-0} < ${want[$i]:-0} )); then return 1; fi
  done
  return 0
}

[[ $(/usr/bin/uname -s) == "Darwin" ]] || { echo "Run this on the Mac Studio you want to test." >&2; exit 64; }

target=$("$SYSCTL" -n hw.targettype | /usr/bin/tr '[:upper:]' '[:lower:]')
target=${target%ap}
model=$("$SYSCTL" -n hw.model)
if [[ $target != "j575d" ]]; then
  echo "This kit is only for the Mac Studio (M3 Ultra, 2025). This Mac is $model ($target)." >&2
  echo "Nothing was collected. Stop here." >&2
  exit 1
fi

version=$("$SW_VERS" -productVersion)
build=$("$SW_VERS" -buildVersion)
if ! at_least "$version" "$MINIMUM_MACOS"; then
  echo "This Mac runs macOS $version. The test needs macOS $MINIMUM_MACOS or later." >&2
  echo "Update first: System Settings > General > Software Update. Then run this again." >&2
  echo "Nothing was collected." >&2
  exit 2
fi

stamp=$(/bin/date +%Y%m%d-%H%M%S)
name="omarchy-m3-ultra-stage0-$stamp"
out="$HOME/Desktop/$name"
[[ ! -e $out && ! -e $out.zip ]] || { echo "$out already exists. Move it away and run again." >&2; exit 1; }
/bin/mkdir -p "$out"

{
  echo "kit: $KIT_VERSION"
  echo "model: $model"
  echo "target: $target"
  echo "macOS: $version ($build)"
  echo "chip: $("$SYSCTL" -n machdep.cpu.brand_string)"
  echo "memory bytes: $("$SYSCTL" -n hw.memsize)"
  echo "cpus: $("$SYSCTL" -n hw.ncpu) (performance $("$SYSCTL" -n hw.perflevel0.logicalcpu 2>/dev/null || echo ?), efficiency $("$SYSCTL" -n hw.perflevel1.logicalcpu 2>/dev/null || echo ?))"
  echo "date: $(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$out/summary.txt"

"$SYSTEM_PROFILER" SPHardwareDataType SPDisplaysDataType 2>&1 | scrub >"$out/hardware.txt" || true
"$IOREG" -r -c AGXAccelerator -l -w0 2>&1 | scrub >"$out/gpu.txt" || true
"$IOREG" -p IODeviceTree -l -w0 2>&1 | scrub >"$out/device-tree.txt" || true

"$DITTO" -c -k --keepParent "$out" "$out.zip"
digest=$("$SHASUM" -a 256 "$out.zip")
digest=${digest%% *}

cat <<EOF

Done. Nothing was installed or changed. The file lists hardware and firmware
facts. It leaves out the serial number, hardware UUID, unique chip ID, memory chip IDs, network
addresses, your account name, your lock-screen contact text, the names of running programs and the
ID numbers of your disks. It holds none of your files.

  File:    $out.zip
  SHA-256: $digest

Send the zip and the SHA-256 line to the person who gave you this kit.
To remove everything this kit made: delete "$name" and "$name.zip" from the Desktop.
EOF
