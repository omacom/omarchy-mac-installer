#!/bin/bash

set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"

config=$ROOT/Packaging/identity.conf
test_tmp=$(mktemp -d)
trap 'rm -rf "$test_tmp"' EXIT

# The Swift generator parses this file while the shell sources it, so only
# lines whose meaning is identical in both are allowed.
entry_pattern='^(INSTALLER_[A-Z0-9_]+)="([^"\$`]+)"$'
declare -A seen=()
line_number=0
while IFS= read -r line || [[ -n $line ]]; do
  line_number=$((line_number + 1))
  [[ -z $line || $line == \#* ]] && continue
  [[ $line =~ $entry_pattern ]] ||
    fail "identity.conf line $line_number is NAME=\"value\" without quoting or expansion" "$line"
  [[ -z ${seen[${BASH_REMATCH[1]}]:-} ]] || fail "identity.conf defines ${BASH_REMATCH[1]} once"
  seen[${BASH_REMATCH[1]}]=1
done <"$config"
source "$config"
for name in INSTALLER_APP_NAME INSTALLER_FILE_STEM INSTALLER_APP_IDENTIFIER \
  INSTALLER_HELPER_IDENTIFIER INSTALLER_PKG_IDENTIFIER INSTALLER_TEAM_ID \
  INSTALLER_APP_SIGNING_IDENTITY INSTALLER_PKG_SIGNING_IDENTITY INSTALLER_TRUST_ROOT \
  INSTALLER_CATALOG_KEY_SERVICE INSTALLER_PUBLIC_BASE INSTALLER_R2_BUCKET \
  INSTALLER_R2_ENDPOINT INSTALLER_GITHUB_RELEASE_REPO INSTALLER_GITHUB_RELEASE_LOGIN; do
  [[ -n ${seen[$name]:-} ]] || fail "identity.conf defines $name"
done
[[ $INSTALLER_TEAM_ID =~ ^[A-Z0-9]{10}$ ]] || fail "the team identifier has ten characters"
[[ $INSTALLER_APP_SIGNING_IDENTITY == "Developer ID Application: "*" ($INSTALLER_TEAM_ID)" ]] ||
  fail "the app signing identity belongs to the configured team"
[[ $INSTALLER_PKG_SIGNING_IDENTITY == "Developer ID Installer: "*" ($INSTALLER_TEAM_ID)" ]] ||
  fail "the package signing identity belongs to the configured team"
[[ $INSTALLER_PUBLIC_BASE == https://?* && $INSTALLER_PUBLIC_BASE != */ ]] ||
  fail "the public base is https without a trailing slash"
trust_root=$ROOT/$INSTALLER_TRUST_ROOT
[[ -f $trust_root && ! -L $trust_root && $(wc -c <"$trust_root") -eq 32 ]] ||
  fail "the configured trust root is a 32-byte public key"
pass "identity.conf is well formed and internally consistent"

# Release inputs that embed identity or hosting must be derived from it.
descriptor=$ROOT/Release/release.json
default_channel=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["default_channel"])' "$descriptor")
bash "$ROOT/scripts/make-release-descriptor" \
  --public-key "$trust_root" \
  --base-url "$INSTALLER_PUBLIC_BASE" \
  --default-channel "$default_channel" \
  --output "$test_tmp/release.json" >/dev/null
cmp -s "$test_tmp/release.json" "$descriptor" ||
  fail "Release/release.json is what make-release-descriptor writes from identity.conf" \
    "$(diff "$test_tmp/release.json" "$descriptor" || true)"
for template in "$ROOT"/scripts/release-inputs*.template.json; do
  python3 - "$template" "$INSTALLER_PUBLIC_BASE" "$INSTALLER_FILE_STEM" <<'PY' ||
import json, sys
template, base, stem = sys.argv[1:4]
installer = json.load(open(template))["installer"]
version = installer["latest_version"]
expected = f"{base}/installer/{version}/{stem}-{version}.pkg"
if installer["download_url"] != expected:
    sys.exit(f"{template}: download_url {installer['download_url']} is not {expected}")
PY
    fail "release input templates point at the configured installer download"
done
pass "release inputs are derived from identity.conf"

# Values set here must not be repeated in code, scripts or tests. Docs,
# recorded evidence and the derived release inputs checked above may name them.
guarded=(
  "$INSTALLER_APP_NAME" "$INSTALLER_FILE_STEM" "$INSTALLER_APP_IDENTIFIER"
  "$INSTALLER_HELPER_IDENTIFIER" "$INSTALLER_PKG_IDENTIFIER" "$INSTALLER_TEAM_ID"
  "$INSTALLER_CATALOG_KEY_SERVICE" "${INSTALLER_PUBLIC_BASE#https://}"
  "$INSTALLER_R2_BUCKET" "${INSTALLER_R2_ENDPOINT#https://}"
  "$INSTALLER_GITHUB_RELEASE_REPO" "$INSTALLER_GITHUB_RELEASE_LOGIN"
)
patterns=()
for value in "${guarded[@]}"; do
  patterns+=(-e "$value")
done
hits=$(
  git -C "$ROOT" ls-files -z |
    grep -zv -E '^(docs|evidence|Design)/|\.md$|^Packaging/identity\.conf$|^Release/release\.json$|^scripts/release-inputs.*\.template\.json$' |
    (cd "$ROOT" && xargs -0 grep -n -I -F "${patterns[@]}" --) || true
)
[[ -z $hits ]] || fail "identity and hosting values live only in Packaging/identity.conf" "$hits"
pass "no code, script or test repeats an identity or hosting value"

# A different configuration changes what the packaging scripts produce.
cat >"$test_tmp/identity.conf" <<'CONF'
INSTALLER_APP_NAME="Identity Probe Installer"
INSTALLER_HELPER_IDENTIFIER="invalid.omarchy.identity-probe.helper"
CONF
cp "$ROOT/Packaging/pkg/scripts/postinstall" "$test_tmp/postinstall"
if bash "$test_tmp/postinstall" >"$test_tmp/postinstall.log" 2>&1; then
  fail "postinstall fails when the configured daemon plist is absent"
fi
grep -Fq "daemon plist is missing: /Library/LaunchDaemons/invalid.omarchy.identity-probe.helper.plist" \
  "$test_tmp/postinstall.log" ||
  fail "postinstall takes the helper label from the identity shipped beside it" "$(cat "$test_tmp/postinstall.log")"
grep -Fq '"$PKG_DIR/../identity.conf" "$scripts/identity.conf"' "$ROOT/Packaging/pkg/build-pkg.sh" ||
  fail "build-pkg.sh ships identity.conf beside postinstall"
grep -Fq -- '--scripts "$scripts"' "$ROOT/Packaging/pkg/build-pkg.sh" ||
  fail "build-pkg.sh packages the staged scripts"

mkdir -p "$test_tmp/tree/Packaging" "$test_tmp/tree/scripts"
cp "$ROOT/scripts/make-release-descriptor" "$test_tmp/tree/scripts/"
sed -e 's/^INSTALLER_HELPER_IDENTIFIER=.*/INSTALLER_HELPER_IDENTIFIER="invalid.omarchy.identity-probe.helper"/' \
  -e 's/^INSTALLER_TEAM_ID=.*/INSTALLER_TEAM_ID="PROBE12345"/' \
  "$config" >"$test_tmp/tree/Packaging/identity.conf"
bash "$test_tmp/tree/scripts/make-release-descriptor" \
  --public-key "$trust_root" --base-url https://probe.invalid \
  --output "$test_tmp/probe.json" >/dev/null
python3 - "$test_tmp/probe.json" <<'PY' || fail "make-release-descriptor takes the helper and team from identity.conf"
import json, sys
descriptor = json.load(open(sys.argv[1]))
assert descriptor["helper_mach_service_name"] == "invalid.omarchy.identity-probe.helper"
assert descriptor["helper_code_signing_requirement"].endswith(
    'identifier "invalid.omarchy.identity-probe.helper" and certificate leaf[subject.OU] = "PROBE12345"'
)
PY
pass "packaging scripts follow a changed identity.conf"
