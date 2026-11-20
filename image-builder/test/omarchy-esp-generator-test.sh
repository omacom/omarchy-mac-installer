#!/bin/bash
# Tests for builder/omarchy-esp-generator. Runs as a normal user on any Linux
# with systemd's tools: bash test/omarchy-esp-generator-test.sh
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
generator=$here/../builder/omarchy-esp-generator
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
failures=0
n=0
# As root the generator reads the live device tree and ignores the fixtures.
if [[ $(id -u) == 0 ]]; then
  echo "SKIP omarchy-esp-generator: run this test as a normal user" >&2
  exit 0
fi

fail() { echo "FAIL $1" >&2; failures=$((failures + 1)); }

# run NAME BYTES: run the generator with BYTES as the device-tree property
# (no property when BYTES is "-"); its early directory is $work/NAME/early.
# It reads $work/NAME/sys/block as /sys/class/block and $work/NAME/dev as /dev,
# empty unless `disk` built them first.
run() {
  local name=$1 bytes=$2 dir=$work/$1 node
  mkdir -p "$dir/normal" "$dir/early" "$dir/late"
  if [[ $bytes == - ]]; then
    node=$dir/missing
  else
    node=$dir/node
    printf '%b' "$bytes" >"$node"
  fi
  # A normal user can't write /dev/kmsg, so the log lines land in stderr.
  OMARCHY_ESP_PARTUUID_FILE=$node OMARCHY_SYSFS_BLOCK=$dir/sys/block OMARCHY_DEV_DIR=$dir/dev \
    sh "$generator" "$dir/normal" "$dir/early" "$dir/late" 2>"$dir/log" ||
    { rc=$?; fail "$name: generator exited $rc"; cat "$dir/log" >&2; }
}

ESP=272043e7-ec24-4890-93d6-545cfbb8329b
BOOT=3c464bd8-909c-42fb-ac75-d47f02ff8504
BOOT_FS=4f4d5801424f4f548000000000000001

# disk NAME KIND: a MacBook Pro's (J616c) disk as sysfs shows it, under
# $work/NAME, by start sector: nvme0n1p3 (stub, 1000), p4 (ESP $ESP, 2000), p5 (Boot $BOOT,
# 3000). The first attempt's leftovers follow: p8 (an ESP with the same
# serial, 9000) and p9 (10000). On this Mac p9 has no filesystem; here it
# carries the Boot UUID, standing for a leftover that does. A second disk,
# mmcblk0, listed first, has a partition 5 with the Boot UUID starting just
# after the ESP's sector. KIND sets what p5 is: boot (the image's Boot
# filesystem), other-fs (ext4 with another UUID), no-magic (the Boot UUID's
# bytes without ext4's magic), unreadable, or not-ext4. absent leaves out p5
# and the leftovers, so nothing follows the ESP on its disk. reused-slot puts
# a leftover Boot in slot 5 after the leftovers (9500) and the real Boot in
# slot 7 at 3000. dup-esp adds a disk, listed first, with a partition that has
# the ESP's PARTUUID.
disk() {
  local dir=$work/$1 kind=$2
  mkdir -p "$dir/sys/block" "$dir/sys/devices/nvme0n1" "$dir/sys/devices/mmcblk0" "$dir/dev"
  printf 'MAJOR=259\nMINOR=0\nDEVNAME=nvme0n1\nDEVTYPE=disk\n' >"$dir/sys/devices/nvme0n1/uevent"
  ln -s ../devices/nvme0n1 "$dir/sys/block/nvme0n1"
  part "$dir" mmcblk0 mmcblk0p5 5 9f1c2e3d-4b5a-4c6d-8e7f-0a1b2c3d4e5f 2500
  ext4 "$dir/dev/mmcblk0p5" "$BOOT_FS"
  part "$dir" nvme0n1 nvme0n1p3 3 d6001b4d-94e9-428a-adc6-0cabe562d82c 1000
  part "$dir" nvme0n1 nvme0n1p4 4 "$ESP" 2000
  if [[ $kind != absent ]]; then
    part "$dir" nvme0n1 nvme0n1p8 8 406a9e32-2509-44d1-a557-7433956bef5c 9000
    part "$dir" nvme0n1 nvme0n1p9 9 e4c1df08-c8b6-49a9-a6b1-1a92d86c8ce2 10000
    ext4 "$dir/dev/nvme0n1p9" "$BOOT_FS"
  fi
  case $kind in
    boot) part "$dir" nvme0n1 nvme0n1p5 5 "$BOOT" 3000; ext4 "$dir/dev/nvme0n1p5" "$BOOT_FS" ;;
    other-fs) part "$dir" nvme0n1 nvme0n1p5 5 "$BOOT" 3000; ext4 "$dir/dev/nvme0n1p5" 0123456789abcdef0123456789abcdef ;;
    not-ext4) part "$dir" nvme0n1 nvme0n1p5 5 "$BOOT" 3000; truncate -s 4096 "$dir/dev/nvme0n1p5" ;;
    no-magic)
      part "$dir" nvme0n1 nvme0n1p5 5 "$BOOT" 3000; ext4 "$dir/dev/nvme0n1p5" "$BOOT_FS"
      printf '\0\0' | dd of="$dir/dev/nvme0n1p5" bs=1 seek=1080 conv=notrunc status=none ;;
    unreadable) part "$dir" nvme0n1 nvme0n1p5 5 "$BOOT" 3000; ext4 "$dir/dev/nvme0n1p5" "$BOOT_FS"; chmod 000 "$dir/dev/nvme0n1p5" ;;
    absent) ;;
    reused-slot)
      part "$dir" nvme0n1 nvme0n1p5 5 c0ffee00-1111-4222-8333-444455556666 9500; ext4 "$dir/dev/nvme0n1p5" "$BOOT_FS"
      part "$dir" nvme0n1 nvme0n1p7 7 "$BOOT" 3000; ext4 "$dir/dev/nvme0n1p7" "$BOOT_FS" ;;
    dup-esp)
      part "$dir" nvme0n1 nvme0n1p5 5 "$BOOT" 3000; ext4 "$dir/dev/nvme0n1p5" "$BOOT_FS"
      mkdir -p "$dir/sys/devices/loop0"
      part "$dir" loop0 loop0p1 1 "$ESP" 2000 ;;
  esac
}

# part DIR DISK NAME PARTN PARTUUID START: a partition's sysfs entry.
part() {
  local p=$1/sys/devices/$2/$3
  mkdir -p "$p"
  : >"$p/partition"
  echo "$6" >"$p/start"
  printf 'MAJOR=259\nMINOR=%s\nDEVNAME=%s\nDEVTYPE=partition\nDISKSEQ=1\nPARTN=%s\nPARTUUID=%s\n' \
    "$4" "$3" "$4" "$5" >"$p/uevent"
  ln -s "../devices/$2/$3" "$1/sys/block/$3"
}

# ext4 FILE UUID_HEX: a device whose superblock has ext4's magic and UUID_HEX.
ext4() {
  truncate -s 4096 "$1"
  printf '\x53\xef' | dd of="$1" bs=1 seek=1080 conv=notrunc status=none
  printf "$(sed 's/../\\x&/g' <<<"$2")" | dd of="$1" bs=1 seek=1128 conv=notrunc status=none
}

# As read on the J616c: lower case, NUL-terminated. Upper case is accepted too.
run lower '272043e7-ec24-4890-93d6-545cfbb8329b\0'
grep -qx 'What=/dev/disk/by-partuuid/272043e7-ec24-4890-93d6-545cfbb8329b' "$work/lower/early/boot-efi.mount" 2>/dev/null ||
  fail "lower: no unit for the PARTUUID as the J616c reports it"
run upper '272043E7-EC24-4890-93D6-545CFBB8329B\0'
unit=$work/upper/early/boot-efi.mount
if [[ -f $unit ]]; then
  grep -qx 'What=/dev/disk/by-partuuid/272043e7-ec24-4890-93d6-545cfbb8329b' "$unit" || fail "upper: What= is not the lower-case PARTUUID"
  grep -qx 'Where=/boot/efi' "$unit" || fail "upper: Where="
  grep -qx 'Type=vfat' "$unit" || fail "upper: Type="
  grep -qx 'Options=umask=0077' "$unit" || fail "upper: Options= differs from fstab's"
  escaped=$(systemd-escape --path /dev/disk/by-partuuid/272043e7-ec24-4890-93d6-545cfbb8329b)
  grep -qxF "Requires=systemd-fsck@$escaped.service" "$unit" || fail "upper: no fsck of the ESP"
  grep -qxF "After=systemd-fsck@$escaped.service blockdev@$escaped.target" "$unit" ||
    fail "upper: not ordered after the ESP's fsck and block device"
  grep -qxF "SourcePath=$work/upper/node" "$unit" || fail "upper: SourcePath= doesn't name the device-tree property"
  grep -q 'mounting /boot/efi from PARTUUID 272043e7-ec24-4890-93d6-545cfbb8329b' "$work/upper/log" ||
    fail "upper: didn't log the PARTUUID it mounts"
  grep -qx 'Before=local-fs.target' "$unit" || fail "upper: not ordered before local-fs.target"
  [[ $(readlink "$work/upper/early/local-fs.target.requires/boot-efi.mount") == ../boot-efi.mount ]] ||
    fail "upper: local-fs.target doesn't require it"
  [[ -z $(ls -A "$work/upper/normal") && -z $(ls -A "$work/upper/late") ]] || fail "upper: wrote outside the early directory"
  # systemd itself must accept the unit, under the name it has to have.
  if command -v systemd-analyze >/dev/null; then
    out=$(systemd-analyze verify --man=no "$unit" 2>&1 | grep -v -e 'is not a device' -e 'Failed to put bus name' -e 'SO_PASSCRED' || true)
    grep -qiE 'error|invalid|unknown' <<<"$out" && fail "upper: systemd-analyze verify: $out"
  fi
else
  fail "upper: no boot-efi.mount written"
fi

# The override only works if it replaces the unit systemd-fstab-generator makes
# from the image's fstab line: same unit name, mount point, type and options.
fstab_generator=/usr/lib/systemd/system-generators/systemd-fstab-generator
if [[ -x $fstab_generator && -f $unit ]]; then
  mkdir -p "$work/fstab"/{normal,early,late}
  printf 'UUID=4F4D-5801  /boot/efi  vfat  umask=0077  0 2\n' >"$work/fstab/fstab"
  SYSTEMD_FSTAB=$work/fstab/fstab SYSTEMD_PROC_CMDLINE= "$fstab_generator" \
    "$work/fstab/normal" "$work/fstab/early" "$work/fstab/late" 2>/dev/null || true
  theirs=$work/fstab/normal/boot-efi.mount
  if [[ -f $theirs ]]; then
    for key in Where Type Options; do
      [[ $(grep "^$key=" "$theirs") == "$(grep "^$key=" "$unit")" ]] || fail "fstab: $key= differs from systemd-fstab-generator's"
    done
    grep -q '^Requires=systemd-fsck@' "$theirs" && ! grep -q '^Requires=systemd-fsck@' "$unit" &&
      fail "fstab: systemd-fstab-generator checks the ESP and this doesn't"
  else
    fail "fstab: systemd-fstab-generator made no boot-efi.mount, so nothing is being replaced"
  fi
elif [[ ! -x $fstab_generator ]]; then
  echo "note: systemd-fstab-generator not found; skipped the comparison with it" >&2
fi

# /boot: the partition after the ESP, when it holds this image's Boot filesystem.
disk boot boot
run boot "$ESP\0"
unit=$work/boot/early/boot.mount
if [[ -f $unit ]]; then
  grep -qx "What=/dev/disk/by-partuuid/$BOOT" "$unit" || fail "boot: What= isn't the partition after the ESP"
  grep -qx 'Where=/boot' "$unit" || fail "boot: Where="
  grep -qx 'Type=ext4' "$unit" || fail "boot: Type="
  grep -qx 'Options=defaults,noatime' "$unit" || fail "boot: Options= differs from fstab's"
  escaped=$(systemd-escape --path "/dev/disk/by-partuuid/$BOOT")
  grep -qxF "Requires=systemd-fsck@$escaped.service" "$unit" || fail "boot: no fsck of the Boot partition"
  grep -qxF "After=systemd-fsck@$escaped.service blockdev@$escaped.target" "$unit" ||
    fail "boot: not ordered after the Boot partition's fsck and block device"
  grep -qx 'Before=local-fs.target' "$unit" || fail "boot: not ordered before local-fs.target"
  [[ $(readlink "$work/boot/early/local-fs.target.requires/boot.mount") == ../boot.mount ]] ||
    fail "boot: local-fs.target doesn't require it"
  grep -q "mounting /boot from PARTUUID $BOOT (nvme0n1p5)" "$work/boot/log" || fail "boot: didn't log the partition it mounts"
  grep -qx "What=/dev/disk/by-partuuid/$ESP" "$work/boot/early/boot-efi.mount" 2>/dev/null ||
    fail "boot: /boot/efi isn't mounted from the ESP any more"
  if command -v systemd-analyze >/dev/null; then
    out=$(systemd-analyze verify --man=no "$unit" 2>&1 | grep -v -e 'is not a device' -e 'Failed to put bus name' -e 'SO_PASSCRED' || true)
    grep -qiE 'error|invalid|unknown' <<<"$out" && fail "boot: systemd-analyze verify: $out"
  fi
  # It has to replace the unit systemd-fstab-generator makes from the image's
  # /boot line: same name, mount point, type and options, and an fsck.
  if [[ -x $fstab_generator ]]; then
    mkdir -p "$work/fstab-boot"/{normal,early,late}
    printf 'UUID=4f4d5801-424f-4f54-8000-000000000001  /boot  ext4  defaults,noatime  0 2\n' >"$work/fstab-boot/fstab"
    SYSTEMD_FSTAB=$work/fstab-boot/fstab SYSTEMD_PROC_CMDLINE= "$fstab_generator" \
      "$work/fstab-boot/normal" "$work/fstab-boot/early" "$work/fstab-boot/late" 2>/dev/null || true
    theirs=$work/fstab-boot/normal/boot.mount
    if [[ -f $theirs ]]; then
      for key in Where Type Options; do
        [[ $(grep "^$key=" "$theirs") == "$(grep "^$key=" "$unit")" ]] || fail "fstab-boot: $key= differs from systemd-fstab-generator's"
      done
      grep -q '^Requires=systemd-fsck@' "$theirs" && ! grep -q '^Requires=systemd-fsck@' "$unit" &&
        fail "fstab-boot: systemd-fstab-generator checks /boot and this doesn't"
    else
      fail "fstab-boot: systemd-fstab-generator made no boot.mount, so nothing is being replaced"
    fi
  fi
else
  fail "boot: no boot.mount written"
  cat "$work/boot/log" >&2
fi

# Slot numbers needn't follow position: the partition that starts next wins.
disk reused-slot reused-slot
run reused-slot "$ESP\0"
grep -qx "What=/dev/disk/by-partuuid/$BOOT" "$work/reused-slot/early/boot.mount" 2>/dev/null ||
  fail "reused-slot: didn't mount the partition that starts right after the ESP"

# Anything else after the ESP leaves /boot to fstab, and still mounts /boot/efi.
for kind in other-fs no-magic unreadable not-ext4 absent dup-esp; do
  disk "$kind" "$kind"
  run "$kind" "$ESP\0"
  [[ ! -e $work/$kind/early/boot.mount ]] || fail "$kind: wrote boot.mount"
  [[ -f $work/$kind/early/boot-efi.mount ]] || fail "$kind: no boot-efi.mount"
  grep -q '/boot is mounted as fstab says' "$work/$kind/log" || fail "$kind: didn't log why fstab applies to /boot"
done
grep -q 'no partition after the ESP on its disk' "$work/absent/log" ||
  fail "absent: took a partition from another disk, or didn't say none follows the ESP"
grep -q "can't read nvme0n1p5" "$work/unreadable/log" || fail "unreadable: didn't say it couldn't read the partition"
grep -q '2 partitions have the ESP' "$work/dup-esp/log" || fail "dup-esp: didn't refuse an ambiguous ESP"
# No sysfs at all (the lower and upper runs): the same.
[[ ! -e $work/upper/early/boot.mount ]] || fail "upper: wrote boot.mount without sysfs"
grep -q '0 partitions have the ESP' "$work/upper/log" || fail "upper: didn't log why fstab applies to /boot"

# No property (not booted through m1n1 or aura): nothing is written, fstab applies.
disk missing boot
run missing -
[[ -z $(ls -A "$work/missing/early") ]] || fail "missing: wrote a unit without the property"
grep -q 'no ESP named in the device tree' "$work/missing/log" || fail "missing: didn't log why fstab applies"

# Anything but one canonical UUID is ignored the same way.
for bad in 'not-a-uuid\0' '272043e7-ec24-4890-93d6\0' '272043e7-ec24-4890-93d6-545cfbb8329b-x\0' \
  '../../etc/passwd\0' '272043e7-ec24-4890-93d6-545cfbb8329\n\0' '\0'; do
  name=bad$((++n))
  run "$name" "$bad"
  [[ -z $(ls -A "$work/$name/early") ]] || fail "$name: accepted ${bad}"
  grep -q 'is not one PARTUUID' "$work/$name/log" || fail "$name: didn't log the refusal"
done

if ((failures)); then
  echo "$failures failure(s)" >&2
  exit 1
fi
echo "PASS omarchy-esp-generator: device-tree ESP at /boot/efi and the partition after it at /boot, with fstab's options and fsck; fstab applies otherwise"
