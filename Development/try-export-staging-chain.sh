#!/bin/bash
# The whole native import across both systems, on one sparse disk image: the
# Mac lays it out with diskutil, Linux writes a Btrfs root.img and makes a real
# export, the helper's staging writes it through diskutil and the raw device,
# then Linux runs the first boot's encryption, claims the export and imports
# it. macOS with Docker; the pinned linux-arm64 age at AGE.
#
#   Development/try-export-staging-chain.sh AGE [IMAGE_BUILDER]
#
# IMAGE_BUILDER is an Arch Linux ARM image with btrfs-progs, cryptsetup,
# util-linux and Python (default try-omarchy-guest-builder).
set -euo pipefail

age=$(cd "$(dirname "${1:?usage: try-export-staging-chain.sh AGE [IMAGE_BUILDER]}")" && pwd)/$(basename "$1")
builder=${2:-try-omarchy-guest-builder}
age_sha256=41b072352f4561018949623c674d16ef704019b9108a9bbdbd21292efebfc94f
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/try-export-chain.XXXXXX")
disk=""
cleanup() {
  [[ -z $disk ]] || "$repo/Development/try-export-staging-disk.sh" detach "$disk" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT
fail() { echo "staging-chain: $*" >&2; exit 1; }

linux() { # script
  docker run --rm --privileged --platform linux/arm64 -v "$work:/work" -v "$repo/packages/omarchy-migration:/src:ro" \
    -v "$age:/age:ro" -e OMARCHY_TEST_AGE=/age -e OMARCHY_TEST_AGE_SHA256="$age_sha256" \
    --entrypoint /bin/bash "$builder" -c "set -euo pipefail
      $(declare -f)
      $1"
}

# The partitions diskutil made, as Linux sees them: ESP and Root numbers.
partitions() {
  for number in $(seq 0 63); do [[ -e /dev/loop$number ]] || mknod "/dev/loop$number" b 7 "$number"; done
  loop=$(losetup -f --show -P /work/disk.img)
  partx -u "$loop" 2>/dev/null || partx -a "$loop"
  for path in /sys/block/${loop#/dev/}/${loop#/dev/}p*; do
    name=${path##*/}
    [[ -b /dev/$name ]] || mknod "/dev/$name" b "$(cut -d: -f1 "$path/dev")" "$(cut -d: -f2 "$path/dev")"
  done
  esp=$(sfdisk -l -o Device,Type "$loop" | awk '/EFI System/ { last = $1 } END { print last }')
  root=$(sfdisk -l -o Device,Type "$loop" | awk '/Linux filesystem/ { last = $1 } END { print last }')
  [[ -b $esp && -b $root ]] || { echo "no ESP or Root on $loop" >&2; exit 1; }
}

"$repo/Development/try-export-staging-disk.sh" create "$work/disk.img"
echo "staging-chain: diskutil laid out the disk"

linux '
  partitions
  mkfs.btrfs -q -f --byte-count 512M "$root"
  mkdir -p /mnt/build && mount "$root" /mnt/build
  btrfs -q subvolume create /mnt/build/@ && btrfs -q subvolume create /mnt/build/@home
  install -d /mnt/build/@/var/lib /mnt/build/@/home
  echo installed >/mnt/build/@/etc-marker
  umount /mnt/build
  losetup -d "$loop"
  useradd -m -u 1500 chain
  /src/install /work/root
  runuser -u chain -- env OMARCHY_TEST_AGE=/age OMARCHY_TEST_AGE_SHA256=$OMARCHY_TEST_AGE_SHA256 \
    /work/root/usr/bin/omarchy-migration fixture export --request /src/test/fixtures/fixture-request.json \
    --output-directory /tmp/export >/dev/null
  cp /tmp/export/bundle.age /tmp/export/receipt.json /work/
'
echo "staging-chain: Linux wrote root.img and a real export"

disk=$("$repo/Development/try-export-staging-disk.sh" attach "$work/disk.img")
(cd "$repo" && OMARCHY_TRY_EXPORT_STAGING_DISK=$disk OMARCHY_TRY_EXPORT_STAGING_BUNDLE=$work/bundle.age \
  OMARCHY_TRY_EXPORT_STAGING_RECEIPT=$work/receipt.json swift test --filter TryExportStagingDiskImageTests 2>&1) |
  grep -E "passed after|failed|Expectation" | tail -3
"$repo/Development/try-export-staging-disk.sh" detach "$disk"
disk=""
echo "staging-chain: the helper's writer staged it through diskutil"

linux '
  partitions
  trap "umount -R /mnt/root 2>/dev/null; cryptsetup close chain 2>/dev/null; losetup -d \$loop 2>/dev/null" EXIT
  mkdir -p /mnt/esp && mount -t vfat "$esp" /mnt/esp
  cp /mnt/esp/omarchy/migration.json /work/migration.json
  umount /mnt/esp
  partuuid=$(blkid -s PARTUUID -o value "$root")
  mkdir -p /work/by-partuuid && ln -sfn "$root" "/work/by-partuuid/$partuuid"
  head -c 64 /dev/urandom >/tmp/key
  mount "$root" /mnt/build 2>/dev/null || { mkdir -p /mnt/build; mount "$root" /mnt/build; }
  fs=$(btrfs filesystem usage -b /mnt/build | awk "/Device size:/ { print \$3; exit }")
  btrfs filesystem resize -32M /mnt/build >/dev/null
  umount /mnt/build
  mkdir -p /run/cryptsetup
  cryptsetup reencrypt --encrypt --type luks2 --batch-mode --reduce-device-size 32M \
    --device-size $((fs - 33554432)) --resilience checksum --key-file /tmp/key --keyfile-size 64 "$root" >/dev/null
  cryptsetup open --batch-mode --key-file /tmp/key --keyfile-size 64 "$root" chain
  cryptsetup resize --batch-mode --key-file /tmp/key --keyfile-size 64 chain
  mkdir -p /mnt/root && mount -o subvol=@ /dev/mapper/chain /mnt/root && mount -o subvol=@home /dev/mapper/chain /mnt/root/home
  btrfs filesystem resize max /mnt/root >/dev/null
  dd if=/dev/urandom of=/mnt/root/var/first-boot-data bs=1M count=64 conv=fsync status=none
  /src/install /work/root
  /work/root/usr/bin/omarchy-migration claim --marker /work/migration.json --root /mnt/root --by-partuuid /work/by-partuuid
  id=$(python3 -c "import json; print(json.load(open(\"/work/receipt.json\"))[\"export_id\"])")
  test ! -e /mnt/root/var/lib/omarchy-migration/claim-failed.json || { cat /mnt/root/var/lib/omarchy-migration/claim-failed.json; exit 1; }
  cmp /work/bundle.age /mnt/root/var/lib/omarchy-migration/incoming/$id/bundle.age
  echo "staging-chain: the first boot claimed the export through LUKS2"
  useradd -M -u 1500 -d /mnt/root/home/chain chain
  install -d -m 0700 -o chain /mnt/root/home/chain /mnt/root/home/.job /mnt/root/home/.job/job
  install -m 0600 -o chain /mnt/root/var/lib/omarchy-migration/incoming/$id/bundle.age /mnt/root/var/lib/omarchy-migration/incoming/$id/receipt.json /mnt/root/home/.job/
  importer() {
    runuser -u chain -- env OMARCHY_TEST_AGE=/age OMARCHY_TEST_AGE_SHA256=$OMARCHY_TEST_AGE_SHA256 \
      /work/root/usr/bin/omarchy-migration "$1" --bundle /mnt/root/home/.job/bundle.age --receipt /mnt/root/home/.job/receipt.json \
      --target /mnt/root/home/chain --job /mnt/root/home/.job/job --scratch /mnt/root/home/.job --passphrase-fd 0 ${2:+--plan-id "$2"}
  }
  passphrase=synthetic-only-otter-maple-window-cobalt
  plan=$(importer plan <<<"$passphrase" 2>/tmp/plan.err) || { cat /tmp/plan.err; exit 1; }
  plan_id=$(python3 -c "import json, sys; print(json.load(sys.stdin)[\"plan_id\"])" <<<"$plan")
  importer apply "$plan_id" <<<"$passphrase" >/dev/null
  test -n "$(ls -A /mnt/root/home/chain)"
  echo "staging-chain: imported into a fresh home"
'
echo "staging-chain: passed"
