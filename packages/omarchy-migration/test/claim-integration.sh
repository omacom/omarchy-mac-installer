#!/bin/bash
# Claim a staged export the way a native first boot does, with real tools: a
# sparse loop disk with a GPT Root partition, root.img at its start, a real
# age bundle raw at its region, the boot's in-place LUKS2 encryption and Btrfs
# growth, then claim, handoff, plan and apply into a fresh home. Runs the
# encrypted and the unencrypted root. Needs root, loop devices, util-linux,
# btrfs-progs, cryptsetup and OMARCHY_TEST_AGE with OMARCHY_TEST_AGE_SHA256.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
: "${OMARCHY_TEST_AGE:?set OMARCHY_TEST_AGE to the verified age 1.3.2}"
: "${OMARCHY_TEST_AGE_SHA256:?set OMARCHY_TEST_AGE_SHA256}"
[[ $(id -u) == 0 ]] || { echo "claim-integration: needs root" >&2; exit 1; }

DISK_BYTES=$((40 * 1024 * 1024 * 1024))
IMAGE_BYTES=$((512 * 1024 * 1024))
REDUCE=32M
REDUCE_BYTES=$((32 * 1024 * 1024))
KEY_BYTES=64
ROOT_TYPE=B921B045-1DF0-41C3-AF44-4C6F280D3FAE
USER_NAME=omarchy-claim-test
USER_ID=1500
SECRET=synthetic-only-otter-maple-window-cobalt
THEME=.config/example-theme/selected
CHANGED=.config/unfamiliar-example/settings

work=$(mktemp -d /var/tmp/omarchy-migration-claim.XXXXXX)
chmod 0755 "$work"
mnt=$work/mnt
mapper=omarchy-migration-claim-$$
loop=""
node=""

fail() {
  echo "claim-integration: $*" >&2
  exit 1
}

detach() {
  local target
  for target in "$mnt/home" "$mnt" "$work/build"; do
    mountpoint -q "$target" 2>/dev/null && umount "$target"
  done
  [[ -e /dev/mapper/$mapper ]] && cryptsetup close "$mapper"
  [[ -n $node ]] && rm -f "$node"
  if [[ -n $loop ]]; then
    partx -d "$loop" 2>/dev/null || true
    losetup -d "$loop"
  fi
  loop="" node=""
  rm -f "$work/disk.img"
}

cleanup() {
  detach || true
  userdel "$USER_NAME" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

migration() {
  "$work/root/usr/bin/omarchy-migration" "$@"
}

as_user() {
  runuser -u "$USER_NAME" -- env HOME="$mnt/home/$USER_NAME" OMARCHY_TEST_AGE="$OMARCHY_TEST_AGE" \
    OMARCHY_TEST_AGE_SHA256="$OMARCHY_TEST_AGE_SHA256" "$@"
}

json() {
  python3 -c 'import json, sys; value = json.load(open(sys.argv[1])); print(eval(sys.argv[2], {"v": value}))' "$@"
}

sha256() {
  sha256sum "$1" | cut -d ' ' -f 1
}

prepare() {
  "$here/install" "$work/root"
  useradd -M -u "$USER_ID" -d "$mnt/home/$USER_NAME" -s /bin/bash "$USER_NAME"
  install -d -m 0700 -o "$USER_NAME" "$work/source"
  as_user "$work/root/usr/bin/omarchy-migration" fixture export --request "$here/test/fixtures/fixture-request.json" \
    --output-directory "$work/source/export" >"$work/export.log"
  bundle=$work/source/export/bundle.age
  receipt=$work/source/export/receipt.json
  [[ $(sha256 "$bundle") == $(json "$receipt" 'v["bundle"]["sha256"]') ]] || fail "the exported bundle doesn't match its receipt"
  printf '%s\n' "$SECRET" >"$work/passphrase"
  chown "$USER_NAME" "$work/passphrase"
  chmod 0600 "$work/passphrase"
  install -d -m 0755 "$work/skel/.config/example-theme" "$work/skel/.config/unfamiliar-example"
  printf 'tokyo-night\n' >"$work/skel/$THEME"
  printf 'shipped default\n' >"$work/skel/$CHANGED"
  head -c 64 /dev/urandom >"$work/key"
  chmod 0600 "$work/key"
}

# The disk the installer leaves: an ESP, then Root holding a 512 MiB root.img (the kernel refuses to shrink Btrfs below 256 MiB)
# at its start and the bundle at its region, committed by the ESP marker.
make_disk() {
  truncate -s "$DISK_BYTES" "$work/disk.img"
  loop=$(losetup -f --show "$work/disk.img")
  printf 'label: gpt\nsize=64MiB, type=U\ntype=%s\n' "$ROOT_TYPE" | sfdisk -q --no-reread --no-tell-kernel "$loop"
  partx -a "$loop"
  local name=${loop#/dev/}p2
  [[ -r /sys/block/${loop#/dev/}/$name/dev ]] || fail "the kernel doesn't list $name"
  node=/dev/$name
  if [[ ! -b $node ]]; then
    local number
    number=$(cat "/sys/block/${loop#/dev/}/$name/dev")
    mknod "$node" b "${number%%:*}" "${number##*:}"
  fi
  partuuid=$(sfdisk --part-uuid "$loop" 2 | tr 'A-F' 'a-f')
  rm -rf "$work/by-partuuid"
  install -d "$work/by-partuuid"
  ln -s "$node" "$work/by-partuuid/$partuuid"

  mkfs.btrfs -q --byte-count "$IMAGE_BYTES" "$node"
  install -d "$work/build"
  mount "$node" "$work/build"
  btrfs -q subvolume create "$work/build/@"
  btrfs -q subvolume create "$work/build/@home"
  install -d -m 0755 "$work/build/@/var/lib" "$work/build/@/home"
  printf 'installed system\n' >"$work/build/@/etc-marker"
  umount "$work/build"

  python3 - "$here/lib" "$node" "$bundle" "$receipt" "$partuuid" "$work/esp/omarchy" <<'PY'
import json
import os
import sys

sys.path.insert(0, sys.argv[1])
from omarchy_migration import staging

node, bundle, receipt, partuuid, esp = sys.argv[2:]
data = open(bundle, "rb").read()
receipt = json.load(open(receipt))
fd = os.open(node, os.O_RDWR)
size = os.lseek(fd, 0, os.SEEK_END)
offset = staging.staging_region(size, len(data))
assert offset is not None, size
padded = data + b"\0" * (-len(data) % 4096)
os.pwrite(fd, padded, offset)
os.fsync(fd)
os.close(fd)
os.makedirs(esp, exist_ok=True)
marker = {"schema": staging.MARKER_SCHEMA, "export_id": receipt["export_id"], "partition_uuid": partuuid,
          "offset": offset, "length": len(data), "sha256": receipt["bundle"]["sha256"], "receipt": receipt}
with open(os.path.join(esp, "migration.json"), "w") as output:
    json.dump(marker, output)
print(f"claim-integration: staged {len(data)} bytes at {offset} of a {size}-byte Root")
PY
}

extent() {
  btrfs filesystem usage -b "$1" | awk '/Device size:/ { print $3; exit }'
}

# As omarchy-mac-encrypt does in the initramfs: shrink Btrfs by 32 MiB, encrypt
# its pre-shrink extent in place, open and grow the mapping; then mount and
# grow Btrfs over the partition as x-systemd.growfs does.
first_boot() {
  local encrypted=$1 source=$node fs
  if ((encrypted)); then
    install -d "$work/build"
    mount "$node" "$work/build"
    fs=$(extent "$work/build")
    btrfs filesystem resize "-$REDUCE" "$work/build" >/dev/null
    umount "$work/build"
    install -d /run/cryptsetup
    cryptsetup reencrypt --encrypt --type luks2 --batch-mode --reduce-device-size "$REDUCE" \
      --device-size "$((fs - REDUCE_BYTES))" --resilience checksum --key-file "$work/key" --keyfile-size "$KEY_BYTES" \
      "$node" >/dev/null
    cryptsetup open --batch-mode --key-file "$work/key" --keyfile-size "$KEY_BYTES" "$node" "$mapper"
    cryptsetup resize --batch-mode --key-file "$work/key" --keyfile-size "$KEY_BYTES" "$mapper"
    source=/dev/mapper/$mapper
  fi
  install -d "$mnt"
  mount -o subvol=@ "$source" "$mnt"
  mount -o subvol=@home "$source" "$mnt/home"
  btrfs filesystem resize max "$mnt" >/dev/null
  [[ $(cat "$mnt/etc-marker") == "installed system" ]] || fail "the installed system didn't survive the first boot"
  dd if=/dev/urandom of="$mnt/var/first-boot-data" bs=1M count=64 conv=fsync status=none
  sync
}

check_claim() {
  local export_id state=$mnt/var/lib/omarchy-migration
  export_id=$(json "$receipt" 'v["export_id"]')
  migration claim --marker "$work/esp/omarchy/migration.json" --root "$mnt" --by-partuuid "$work/by-partuuid" \
    >"$work/claim.log"
  [[ ! -e $work/esp/omarchy/migration.json ]] || fail "the claim left the marker"
  [[ ! -e $state/claim-failed.json ]] || fail "the claim failed: $(cat "$state/claim-failed.json")"
  [[ $(sha256 "$state/incoming/$export_id/bundle.age") == $(sha256 "$bundle") ]] || fail "the claimed copy differs"
  python3 -c 'import json, sys; sys.exit(json.load(open(sys.argv[1])) != json.load(open(sys.argv[2])))' \
    "$state/incoming/$export_id/receipt.json" "$receipt" || fail "the claimed receipt differs"
  [[ -e $state/pending/$export_id ]] || fail "the claim didn't list the export as pending"
  [[ $(stat -c %a "$state/incoming/$export_id") == 700 && $(stat -c %a "$state/incoming/$export_id/bundle.age") == 600 ]] ||
    fail "the claimed export isn't private"

  cp "$work/esp/omarchy/migration.json.again" "$work/esp/omarchy/migration.json"
  migration claim --marker "$work/esp/omarchy/migration.json" --root "$mnt" --by-partuuid "$work/by-partuuid" >/dev/null
  [[ ! -e $work/esp/omarchy/migration.json ]] || fail "a second claim left the marker"
  [[ $(sha256 "$state/incoming/$export_id/bundle.age") == $(sha256 "$bundle") ]] || fail "a second claim changed the copy"
  echo "claim-integration: claimed $export_id"
}

check_import() {
  local export_id home=$mnt/home/$USER_NAME result incoming jobs plan plan_id
  export_id=$(json "$receipt" 'v["export_id"]')
  install -d -m 0700 -o "$USER_NAME" "$home"
  cp -a "$work/skel/." "$home/"
  printf 'changed on this Mac\n' >"$home/$CHANGED"
  chown -R "$USER_NAME" "$home"
  chmod -R go-w "$home"

  # An earlier handoff stopped between its mkdir and its chown.
  install -d -m 0711 "$mnt/home/.omarchy-migration"
  install -d -m 0700 "$mnt/home/.omarchy-migration/$USER_ID"
  result=$(env -i PATH=/usr/bin:/bin SUDO_UID="$USER_ID" SUDO_USER="$USER_NAME" \
    "$work/root/usr/lib/omarchy-migration/handoff" --state "$mnt/var/lib/omarchy-migration")
  [[ $(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["exports"])' "$result") == "['$export_id']" ]] ||
    fail "the handoff didn't move the export: $result"
  work_folder=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["work"])' "$result")
  [[ $work_folder == "$mnt/home/.omarchy-migration/$USER_ID" && $(stat -c %U "$work_folder") == "$USER_NAME" ]] ||
    fail "the work folder is wrong: $work_folder"
  incoming=$home/.local/share/omarchy-migration/incoming/$export_id
  [[ $(stat -c '%U %a' "$incoming/bundle.age") == "$USER_NAME 600" ]] || fail "the handed-off bundle isn't the owner's"
  [[ $(sha256 "$incoming/bundle.age") == $(sha256 "$bundle") ]] || fail "the handed-off bundle differs"
  [[ ! -e $mnt/var/lib/omarchy-migration/incoming/$export_id && ! -e $mnt/var/lib/omarchy-migration/pending/$export_id ]] ||
    fail "the handoff left the claimed export"

  jobs=$work_folder/jobs/$export_id
  as_user install -d -m 0700 "$jobs" "$work_folder/scratch"
  local common=(--bundle "$incoming/bundle.age" --receipt "$incoming/receipt.json" --target "$home" --job "$jobs"
    --scratch "$work_folder/scratch" --defaults "$work/skel")
  as_user "$work/root/usr/bin/omarchy-migration" plan "${common[@]}" --summary "$work_folder/summary.json" \
    --passphrase-fd 3 3<"$work/passphrase" >"$work/plan.json"
  plan_id=$(json "$work/plan.json" 'v["plan_id"]')
  [[ $(json "$work/plan.json" 'v["actions"]["replace"]') == 1 ]] || fail "the plan doesn't replace the untouched default"
  [[ $(json "$work_folder/summary.json" 'v["replaces_default"]') == "['$THEME']" ]] || fail "the summary misses the replacement"
  [[ $(json "$work_folder/summary.json" '[k["reason"] for k in v["kept"] if k["path"] == "'"$CHANGED"'"]') == "['native_file_changed']" ]] ||
    fail "the summary misses the changed default"
  as_user "$work/root/usr/bin/omarchy-migration" apply "${common[@]}" --plan-id "$plan_id" \
    --passphrase-fd 3 3<"$work/passphrase" >"$work/report.json"
  [[ $(cat "$home/$THEME") == catppuccin ]] || fail "the untouched default wasn't replaced"
  [[ $(cat "$home/$CHANGED") == "changed on this Mac" ]] || fail "the changed default was replaced"
  [[ $(cat "$home/Projects/demo/untracked.txt") == "SYNTHETIC untracked draft" ]] || fail "a project file didn't come across"
  grep -rqx tokyo-night "$jobs" || fail "the replaced default has no backup"
  echo "claim-integration: imported into a fresh home"
}

run_variant() {
  local encrypted=$1
  make_disk
  cp "$work/esp/omarchy/migration.json" "$work/esp/omarchy/migration.json.again"
  first_boot "$encrypted"
  check_claim
  check_import
  rm -rf "${mnt:?}/home/$USER_NAME" "$mnt/home/.omarchy-migration"
  detach
  echo "claim-integration: $( ((encrypted)) && echo encrypted || echo unencrypted ) root passed"
}

prepare
run_variant 1
run_variant 0
echo "claim-integration: passed"
