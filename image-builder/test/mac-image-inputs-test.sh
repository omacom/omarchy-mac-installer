#!/bin/bash

# The inputs record is canonical, names https servers only, and hands a build
# exactly the database bytes it pinned.

set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
inputs=$ROOT/bin/mac-image-inputs
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

pass() {
  echo "ok - $*"
}

sha() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

mkdir -p "$work/cache/db"
declare -A digest=()
for repository in omarchy asahi-alarm core extra alarm aur; do
  printf '%s database\n' "$repository" >"$work/$repository.db"
  digest[$repository]=$(sha "$work/$repository.db")
  cp "$work/$repository.db" "$work/cache/db/${digest[$repository]}.db"
done
hex() { printf "$1%.0s" $(seq 64); }

record() {
  cat <<EOF
format=2
resolved_at=2026-09-25T10:41:01Z
candidate_set=apple-test-fixture
candidate_source_commit=$(printf 'a%.0s' $(seq 40))
candidate_signer=$(printf 'E%.0s' $(seq 40))
candidate_receipt_sha256=$(hex 1)
candidate_manifest_sha256=$(hex 2)
omarchy_channel=edge
omarchy_server=https://pkgs.omarchy.org/edge/\$arch
omarchy_db_sha256=${digest[omarchy]}
asahi_alarm_server=https://github.com/asahi-alarm/asahi-alarm/releases/download/aarch64
asahi_alarm_db_sha256=${digest[asahi-alarm]}
alarm_server=https://ca.us.mirror.archlinuxarm.org/\$arch/\$repo
alarm_core_db_sha256=${digest[core]}
alarm_extra_db_sha256=${digest[extra]}
alarm_alarm_db_sha256=${digest[alarm]}
alarm_aur_db_sha256=${digest[aur]}
alarm_rootfs_sha256=$(hex 3)
EOF
}

record >"$work/inputs"
"$BASH" "$inputs" check "$work/inputs" | cmp -s - "$work/inputs" || fail "check prints a canonical record"
pass "a canonical record checks"

refuse() {
  local description=$1
  shift
  "$@" >"$work/record" || true
  if "$BASH" "$inputs" check "$work/record" >/dev/null 2>&1; then
    fail "check accepts $description"
  fi
  pass "check refuses $description"
}

refuse "reordered fields" awk 'NR == 2 { held = $0; next } NR == 3 { print; print held; next } 1' "$work/inputs"
refuse "an unknown field" bash -c 'cat "$1"; echo lane=edge' _ "$work/inputs"
refuse "a repeated field" bash -c 'cat "$1"; sed -n 2p "$1"' _ "$work/inputs"
refuse "a short digest" sed 's/^omarchy_db_sha256=.*/omarchy_db_sha256=abc/' "$work/inputs"
refuse "an http server" sed 's|^alarm_server=https|alarm_server=http|' "$work/inputs"
refuse "a server that climbs" sed 's|^omarchy_server=.*|omarchy_server=https://pkgs.omarchy.org/edge/../x|' "$work/inputs"
refuse "an unknown channel" sed 's/^omarchy_channel=.*/omarchy_channel=beta/' "$work/inputs"
refuse "a lowercase signer" sed 's/^candidate_signer=.*/candidate_signer=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee/' "$work/inputs"
refuse "the mx-mac record format" sed 's/^format=2$/format=1/' "$work/inputs"

mkdir "$work/dbs"
"$BASH" "$inputs" dbs "$work/inputs" "$work/dbs" --cache "$work/cache" >/dev/null || fail "dbs from the cache"
for repository in omarchy asahi-alarm core extra alarm aur; do
  cmp -s "$work/$repository.db" "$work/dbs/$repository.db" || fail "$repository.db is the pinned database"
done
pass "dbs puts every pinned database in place from the cache"

mkdir "$work/moved"
printf 'moved on\n' >"$work/cache/db/${digest[extra]}.db"
if "$BASH" "$inputs" dbs "$work/inputs" "$work/moved" --cache "$work/cache" >/dev/null 2>&1; then
  fail "dbs accepts a database that is not the pinned one"
fi
pass "dbs refuses a database that is not the pinned one"

if "$BASH" "$inputs" dbs "$work/inputs" "$work/dbs" --cache "$work/cache" >/dev/null 2>&1; then
  fail "dbs writes into a directory that is not empty"
fi
pass "dbs needs an empty directory"

if "$BASH" "$inputs" resolve "$work/new" --candidates "$work" --alarm-server http://mirror.invalid >"$work/out" 2>&1; then
  fail "resolve accepts an http server"
fi
grep -Fq "not an https server URL: http://mirror.invalid" "$work/out" || fail "resolve refuses an http server by name"
pass "resolve refuses an http server"

# ── channel records ────────────────────────────────────────────────────────
# A fixture edge snapshot: a real repository database naming the channel set,
# its archives and stand-in signatures in the cache (nothing here verifies them).
mkdir -p "$work/channel" "$work/cache/pkg"
channel_db=$(cd "$ROOT" && python3 -c '
import sys
sys.path.insert(0, "test")
import fixtures
from pathlib import Path
names = [fixtures.CHANNEL_NAMES.get(role, role) for role in fixtures.VERSIONS]
database, sha256 = fixtures.make_channel(Path(sys.argv[1]), None, unsigned=names)
print(sha256)
' "$work/channel/edge")
cp "$work/channel/edge/omarchy.db" "$work/cache/db/$channel_db.db"
for archive in "$work"/channel/edge/packages/*.pkg.tar.*; do
  cp "$archive" "$work/cache/pkg/"
  printf 'signature\n' >"$work/cache/pkg/${archive##*/}.sig"
done
channel_record() {
  record | awk -v set="omarchy-edge-${channel_db:0:12}" -v db="$channel_db" -v manifest="$(hex 8)" '
    /^candidate_set=/ { print "channel_set=" set; print "channel_signer=40DFB630FF42BCFFB047046CF0134EE680CAC571";
      print "channel_manifest_sha256=" manifest; next }
    /^candidate_/ { next }
    /^omarchy_db_sha256=/ { print "omarchy_db_sha256=" db; next }
    { print }'
}
channel_record >"$work/channel-inputs"
"$BASH" "$inputs" check "$work/channel-inputs" | cmp -s - "$work/channel-inputs" || fail "check prints a canonical channel record"
pass "a canonical channel record checks"

refuse "a channel record that also names a candidate set" \
  awk '{ print } /^resolved_at=/ { print "candidate_set=apple-test-fixture" }' "$work/channel-inputs"
refuse "a channel set named for another database" \
  sed "s/^channel_set=.*/channel_set=omarchy-edge-$(hex 0 | cut -c1-12)/" "$work/channel-inputs"
refuse "a channel set named for another channel" sed 's/^channel_set=omarchy-edge-/channel_set=omarchy-rc-/' "$work/channel-inputs"
refuse "a lowercase channel signer" \
  sed 's/^channel_signer=.*/channel_signer=40dfb630ff42bcffb047046cf0134ee680cac571/' "$work/channel-inputs"
refuse "a channel record without its manifest digest" sed '/^channel_manifest_sha256=/d' "$work/channel-inputs"

mkdir "$work/channel-packages"
"$BASH" "$inputs" packages "$work/channel-inputs" "$work/channel-packages" --cache "$work/cache" >/dev/null ||
  fail "packages from the cache"
expected=$(cd "$work/channel/edge/packages" && ls | sort)
[[ $(cd "$work/channel-packages" && ls | grep -v '\.sig$' | sort) == "$expected" &&
  $(ls "$work/channel-packages" | grep -c '\.sig$') -eq $(wc -l <<<"$expected") ]] ||
  fail "packages puts each archive the pinned database names, with its signature, in place"
pass "packages puts the channel set's archives and signatures in place from the cache"

mkdir "$work/candidate-packages"
if "$BASH" "$inputs" packages "$work/inputs" "$work/candidate-packages" --cache "$work/cache" >/dev/null 2>&1; then
  fail "packages accepts a candidate record"
fi
cp "$work/cache/db/$channel_db.db" "$work/channel-db.keep"
printf 'moved on\n' >"$work/cache/db/$channel_db.db"
mkdir "$work/moved-packages"
if "$BASH" "$inputs" packages "$work/channel-inputs" "$work/moved-packages" --cache "$work/cache" >/dev/null 2>&1; then
  fail "packages accepts a channel database that is not the pinned one"
fi
mv "$work/channel-db.keep" "$work/cache/db/$channel_db.db"
pass "packages refuses a candidate record and a channel database that is not the pinned one"

if "$BASH" "$inputs" resolve "$work/new" --channel-set --candidates "$work" >"$work/out" 2>&1; then
  fail "resolve accepts --channel-set with --candidates"
fi
grep -Fq "exclude each other" "$work/out" || fail "resolve says --channel-set and --candidates exclude each other"
pass "resolve takes a candidate set or the channel, not both"

# A public image always carries the native Recovery app: refused before any download.
if "$BASH" "$inputs" resolve "$work/new" --channel-set --omarchy-server https://pkgs.invalid/edge >"$work/out" 2>&1; then
  fail "resolve accepts --channel-set without --recovery-app"
fi
grep -Fq -- "--channel-set requires --recovery-app" "$work/out" || fail "resolve says a channel set needs the Recovery app"
[[ ! -e $work/new ]] || fail "a refused channel resolve writes no record"
pass "resolve refuses a channel set without the native Recovery app"

# Hardware profiles are retired: every channel record builds the one Aurora image.
refuse "a retired hardware profile" awk '{ print } /^channel_manifest_sha256=/ { print "hardware_profile=neo" }' "$work/channel-inputs"
refuse "a retired legacy profile" awk '{ print } /^channel_manifest_sha256=/ { print "hardware_profile=legacy" }' "$work/channel-inputs"
if "$BASH" "$inputs" resolve "$work/new" --channel-set --hardware-profile neo >"$work/out" 2>&1; then
  fail "resolve accepts the retired --hardware-profile"
fi
grep -Fq -- "unknown option: --hardware-profile" "$work/out" || fail "retired profile failure reason"
pass "hardware profiles are retired from inputs records and resolve"

awk -v digest="$(hex 9)" '{ print } /^channel_manifest_sha256=/ { print "recovery_app_manifest_sha256=" digest }' "$work/channel-inputs" >"$work/recovery-inputs"
"$BASH" "$inputs" check "$work/recovery-inputs" | cmp -s - "$work/recovery-inputs" || fail "Recovery input digest roundtrips"
refuse "a malformed Recovery digest" sed 's/recovery_app_manifest_sha256=.*/recovery_app_manifest_sha256=short/' "$work/recovery-inputs"
refuse "a repeated Recovery digest" awk '{ print } /^recovery_app_manifest_sha256=/ { print }' "$work/recovery-inputs"
pass "Recovery inventory digest is canonical and pinned"

# Package fetch takes the whole Aurora set, its support packages included.
aurora_db=$(cd "$ROOT" && python3 -c '
import sys
sys.path.insert(0, "test")
import fixtures
from pathlib import Path
names = [fixtures.CHANNEL_NAMES.get(role, role) for role in fixtures.VERSIONS]
database, sha256 = fixtures.make_channel(Path(sys.argv[1]), None, unsigned=names)
print(sha256)
' "$work/aurora/edge")
cp "$work/aurora/edge/omarchy.db" "$work/cache/db/$aurora_db.db"
for archive in "$work"/aurora/edge/packages/*.pkg.tar.*; do
  cp "$archive" "$work/cache/pkg/"
  printf 'signature\n' >"$work/cache/pkg/${archive##*/}.sig"
done
sed -e "s/channel_set=.*/channel_set=omarchy-edge-${aurora_db:0:12}/" \
  -e "s/omarchy_db_sha256=.*/omarchy_db_sha256=$aurora_db/" "$work/channel-inputs" >"$work/aurora-fetch-inputs"
mkdir "$work/aurora-packages"
"$BASH" "$inputs" packages "$work/aurora-fetch-inputs" "$work/aurora-packages" --cache "$work/cache" || fail "Aurora packages fetch"
expected=$(cd "$work/aurora/edge/packages" && ls | sort)
[[ $(cd "$work/aurora-packages" && ls | grep -v '\.sig$' | sort) == "$expected" ]] || fail "the fetch selects exactly the Aurora set"
ls "$work/aurora-packages" | grep -q '^speakerguardd-' || fail "the fetch takes the Aurora support packages"
pass "package fetch takes the whole Aurora set"
