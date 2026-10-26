#!/bin/bash
# A sparse raw disk image laid out like an install: an APFS stub, the ESP
# (EFI - OMARC), Boot and Root, for staging tests through diskutil
# (Tests/OmarchyAppleInstallerTrustCoreTests/TryExportStagingDiskImageTests.swift).
#
#   Development/try-export-staging-disk.sh create IMAGE   make and partition it, then detach
#   Development/try-export-staging-disk.sh attach IMAGE   attach it, print diskN
#   Development/try-export-staging-disk.sh detach diskN
set -euo pipefail

linux=%0FC63DAF-8483-4772-8E79-3D69D8477DE4%
esp=%C12A7328-F81F-11D2-BA4B-00A0C93EC93B%

attach() {
  hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount "$1" 2>/dev/null |
    awk 'NR == 1 { sub("/dev/", "", $1); print $1 }'
}

case ${1:-} in
  create)
    image=${2:?usage: create IMAGE}
    [[ ! -e $image ]] || { echo "$image exists" >&2; exit 1; }
    mkfile -n 48g "$image"
    disk=$(attach "$image")
    diskutil partitionDisk "$disk" 4 GPT APFS Omarchy 3G "$esp" %noformat% 500M \
      "$linux" %noformat% 2G "$linux" %noformat% R >/dev/null
    newfs_msdos -F 32 -v "EFI - OMARC" "/dev/r${disk}s3" >/dev/null
    hdiutil detach "$disk" >/dev/null
    ;;
  attach) attach "${2:?usage: attach IMAGE}" ;;
  detach) hdiutil detach "${2:?usage: detach diskN}" >/dev/null ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
