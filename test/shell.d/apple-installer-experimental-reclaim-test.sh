#!/bin/bash
# The experimental btrfs migration must resume after an interruption and must
# refuse a plan that would rewrite iBoot or System Recovery.
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
script=$root/Engine/overlay/src/experimental_reclaim_root.sh

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
bin=$work/bin
mkdir -p "$bin" "$work/root/etc" "$work/root/boot/efi" "$work/root/etc/kernel"
printf 'rd.luks.name=OLD=root root=UUID=FS\n' >"$work/root/etc/kernel/cmdline"
printf 'UUID=OLD / btrfs defaults 0 0\n' >"$work/root/etc/fstab"
printf '/Omarchy\n' >"$work/root/boot/efi/limine.conf"
cat >"$work/bin/btrfs" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >>"${RECLAIM_LOG:?}"
exit 0
EOF
cat >"$work/bin/cryptsetup" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >>"${RECLAIM_LOG:?}"
exit 0
EOF
cat >"$work/bin/sgdisk" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >>"${RECLAIM_LOG:?}"
exit 0
EOF
chmod 755 "$work/bin/btrfs" "$work/bin/cryptsetup" "$work/bin/sgdisk"

layout=$work/layout.json
cat >"$layout" <<'EOF'
{"devices":[
  {"name":"disk0s1","uuid":"ISC","fstype":"Apple_APFS_ISC","start":0},
  {"name":"disk0s5","uuid":"OLD","fstype":"btrfs","start":40},
  {"name":"disk0s6","uuid":"BOOT","fstype":"vfat","start":80},
  {"name":"disk0s3","uuid":"REC","fstype":"Apple_APFS_Recovery","start":90}
]}
EOF
plan=$work/reclaim-plan.json
cat >"$plan" <<'EOF'
{
  "schema": 1,
  "experimental": true,
  "filesystemUUID": "FS",
  "oldPartitionUUID": "OLD",
  "bootPartitionUUID": "BOOT",
  "iscUUID": "ISC",
  "recoveryUUID": "REC",
  "encrypted": true,
  "macosDeleted": true
}
EOF
printf 'OLD / cryptroot none luks\n' >"$work/root/etc/crypttab"

export PATH="$bin:$PATH"
export RECLAIM_PLAN=$plan
export RECLAIM_ROOT=$work/root
export RECLAIM_STATE_DIR=$work/state
export RECLAIM_LAYOUT=$layout
export RECLAIM_LOG=$work/log
export RECLAIM_MOUNT=$work/mount
mkdir -p "$work/mount"
: >"$RECLAIM_LOG"

if RECLAIM_FAIL_AT=added bash "$script"; then
  printf 'the interruption did not stop the migration\n' >&2
  exit 1
fi
grep -q '^phase=added$' "$work/state/state"
grep -q 'device add' "$RECLAIM_LOG"
if grep -q 'device remove' "$RECLAIM_LOG"; then
  printf 'remove ran before the interruption\n' >&2
  exit 1
fi

bash "$script"
grep -q 'device remove' "$RECLAIM_LOG"
[[ $(<"$work/state/complete") == OLD ]]
grep -q 'OLD-reclaim' "$work/root/etc/crypttab"
grep -q 'OLD-reclaim' "$work/root/etc/fstab"
[[ $(<"$work/root/boot/efi/omarchy/experimental/boot-partition-uuid") == BOOT ]]
before=$(wc -l <"$RECLAIM_LOG")
bash "$script"
[[ $(wc -l <"$RECLAIM_LOG") == "$before" ]]

python3 - "$plan" <<'PY'
import json, sys
path = sys.argv[1]
plan = json.load(open(path, encoding="utf-8"))
plan["oldPartitionUUID"] = "ISC"
json.dump(plan, open(path, "w", encoding="utf-8"))
PY
rm -rf "$work/state"
if bash "$script"; then
  printf 'a plan aimed at iBoot was accepted\n' >&2
  exit 1
fi

printf 'ok - experimental reclaim resumes, updates boot identity, and refuses iBoot\n'
