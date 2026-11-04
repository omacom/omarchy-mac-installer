#!/bin/bash
# Experimental. Run this from Omarchy after single-boot deletes the macOS
# container. The macOS installer never runs it. omarchy-mac first boot should
# execute this script when reclaim-plan.json is beside it. Running it again
# continues the same migration.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
plan=${RECLAIM_PLAN:-$script_dir/reclaim-plan.json}
root=${RECLAIM_ROOT:-}
state_dir=${RECLAIM_STATE_DIR:-$root/var/lib/omarchy/experimental-reclaim}
mount=${RECLAIM_MOUNT:-/}

die() { printf 'experimental reclaim: %s\n' "$1" >&2; exit 1; }

[[ -f $plan ]] || die "no reclaim plan"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

plan_lines=$state_dir/plan-lines
mkdir -p -- "$state_dir"
python3 - "$plan" >"$plan_lines" <<'PY' || die "the reclaim plan is not the experimental single-boot plan"
import json, sys
plan = json.load(open(sys.argv[1], encoding="utf-8"))
required = {
    "schema", "experimental", "filesystemUUID", "oldPartitionUUID",
    "bootPartitionUUID", "iscUUID", "recoveryUUID", "encrypted", "macosDeleted",
}
if plan.get("schema") != 1 or plan.get("experimental") is not True or set(plan) != required:
    raise SystemExit(2)
text_keys = ("filesystemUUID", "oldPartitionUUID", "bootPartitionUUID", "iscUUID", "recoveryUUID")
if not all(isinstance(plan[key], str) and plan[key] for key in text_keys):
    raise SystemExit(2)
if not isinstance(plan["encrypted"], bool) or not isinstance(plan["macosDeleted"], bool):
    raise SystemExit(2)
if len({plan[key] for key in text_keys}) != len(text_keys):
    raise SystemExit(2)
print("\n".join([
    plan["filesystemUUID"], plan["oldPartitionUUID"], plan["bootPartitionUUID"],
    plan["iscUUID"], plan["recoveryUUID"],
    "true" if plan["encrypted"] else "false",
    "true" if plan["macosDeleted"] else "false",
]))
PY
mapfile -t plan_row <"$plan_lines"
filesystem_uuid=${plan_row[0]}
old_uuid=${plan_row[1]}
boot_uuid=${plan_row[2]}
isc_uuid=${plan_row[3]}
recovery_uuid=${plan_row[4]}
encrypted=${plan_row[5]}
macos_deleted=${plan_row[6]}
if [[ $macos_deleted != true ]]; then
  exit 0
fi

mkdir -p -- "$state_dir"
complete=$state_dir/complete
state=$state_dir/state
if [[ -f $complete && $(<"$complete") == "$old_uuid" ]]; then
  exit 0
fi

if [[ -n ${RECLAIM_LAYOUT:-} ]]; then
  layout=$(cat -- "$RECLAIM_LAYOUT")
else
  layout=$(lsblk -b -J -o NAME,UUID,FSTYPE,TYPE,START,SIZE)
fi
python3 - "$isc_uuid" "$recovery_uuid" "$boot_uuid" "$old_uuid" "$layout" <<'PY' || die "the disk is not the recorded single-boot shape"
import json, sys
isc, recovery, boot, old, raw = sys.argv[1:]
tree = json.loads(raw)
flat = []
def walk(items):
    for item in items:
        flat.append(item)
        walk(item.get("children") or [])
walk(tree.get("blockdevices") or tree.get("devices") or [])
found = {item.get("uuid"): item for item in flat if item.get("uuid")}
for uuid, kind in ((isc, "Apple_APFS_ISC"), (recovery, "Apple_APFS_Recovery")):
    item = found.get(uuid)
    if item is None or item.get("fstype", item.get("type")) not in (kind, item.get("type")):
        if item is None or kind not in (item.get("fstype") or "", item.get("type") or ""):
            raise SystemExit(2)
if boot not in found or old not in found:
    raise SystemExit(2)
if found[old].get("fstype") in ("Apple_APFS_ISC", "Apple_APFS_Recovery"):
    raise SystemExit(2)
PY

phase=start
new_uuid=${old_uuid}-reclaim
mapper=omarchy-reclaim
if [[ -f $state ]]; then
  # shellcheck disable=SC1090
  source "$state"
fi

stop_if_requested() {
  if [[ ${RECLAIM_FAIL_AT:-} == "$phase" ]]; then
    die "interrupted during $phase"
  fi
}

if [[ $phase == start ]]; then
  sgdisk --new=0:0:0 --typecode=0:8300 /dev/disk/by-uuid/"$old_uuid"
  phase=partitioned
  printf 'phase=partitioned\nnew_uuid=%s\n' "$new_uuid" >"$state"
  stop_if_requested
fi

device=/dev/disk/by-uuid/$new_uuid
if [[ $encrypted == true ]]; then
  if [[ $phase == partitioned ]]; then
    cryptsetup luksFormat --type luks2 --batch-mode "$device"
    cryptsetup open "$device" "$mapper"
    phase=opened
    printf 'phase=opened\nnew_uuid=%s\n' "$new_uuid" >"$state"
    stop_if_requested
  fi
  block=/dev/mapper/$mapper
else
  block=$device
fi

if [[ $phase == opened || $phase == partitioned ]]; then
  btrfs device add -f "$block" "$mount"
  phase=added
  printf 'phase=added\nnew_uuid=%s\n' "$new_uuid" >"$state"
  stop_if_requested
fi

if [[ $phase == added ]]; then
  btrfs device remove "/dev/disk/by-uuid/$old_uuid" "$mount"
  btrfs filesystem resize max "$mount"
  printf 'phase=removed\nnew_uuid=%s\n' "$new_uuid" >"$state"
  phase=removed
fi

if [[ $phase == removed ]]; then
  python3 - "$root" "$old_uuid" "$new_uuid" "$boot_uuid" "$encrypted" <<'PY'
import pathlib, sys
root, old, new, boot, encrypted = sys.argv[1:]
root = pathlib.Path(root)
for relative in ("etc/crypttab", "etc/fstab", "boot/efi/limine.conf", "etc/kernel/cmdline"):
    file = root / relative
    if not file.is_file():
        continue
    text = file.read_text(encoding="utf-8")
    updated = text.replace(old, new)
    if updated != text:
        file.write_text(updated, encoding="utf-8")
record = root / "boot/efi/omarchy/experimental/boot-partition-uuid"
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(boot + "\n", encoding="utf-8")
if encrypted == "true":
    crypttab = (root / "etc/crypttab").read_text(encoding="utf-8")
    if new not in crypttab:
        raise SystemExit("crypttab still names the old root device")
PY
  printf '%s\n' "$old_uuid" >"$complete"
fi
