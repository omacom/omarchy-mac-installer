#!/bin/bash
# Validate exact private app/package bytes, without registration or privilege.
set -euo pipefail
(( $# == 2 && EUID != 0 )) || exit 64
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$root/Packaging/private-test/code-identity.sh"
app=$1
package=$2
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
helper="$app/Contents/Resources/omarchy-apple-installer-helper"
app_requirement=$(private_code_requirement "$app" com.omarchy.mx.installer)
helper_requirement=$(private_code_requirement "$helper" com.omarchy.mx.installer.helper)
/usr/bin/codesign --verify --deep --strict -R="$app_requirement" "$app"
/usr/bin/codesign --verify --strict -R="$helper_requirement" "$helper"
/usr/sbin/pkgutil --expand-full "$package" "$work/expanded"
export PRIVATE_TEST_APP_REQUIREMENT="$app_requirement"
export PRIVATE_TEST_HELPER_REQUIREMENT="$helper_requirement"
python3 - "$work/expanded" "$app" <<'PY'
from pathlib import Path
import hashlib
import json
import os
import plistlib
import sys
import xml.etree.ElementTree as ET

expanded, original = map(Path, sys.argv[1:])
installed = expanded / 'Payload/Applications/Omarchy MX Mac Installer.app'
def files(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}
assert files(installed) == files(original)
plist = plistlib.loads((expanded / 'Payload/Library/LaunchDaemons/com.omarchy.mx.installer.helper.plist').read_bytes())
assert plist == {'Label': 'com.omarchy.mx.installer.helper', 'Program': '/Library/PrivilegedHelperTools/com.omarchy.mx.installer.helper', 'MachServices': {'com.omarchy.mx.installer.helper': True}, 'UserName': 'root', 'EnvironmentVariables': {'OMARCHY_CLIENT_CODE_SIGNING_REQUIREMENT': os.environ['PRIVATE_TEST_APP_REQUIREMENT']}}
helper = expanded / 'Payload/Library/PrivilegedHelperTools/com.omarchy.mx.installer.helper'
assert helper.read_bytes() == (original / 'Contents/Resources/omarchy-apple-installer-helper').read_bytes()
assert helper.stat().st_mode & 0o777 == 0o755
assert helper.parent.stat().st_mode & 0o777 == 0o755
info = ET.parse(expanded / 'PackageInfo').getroot()
assert info.attrib['relocatable'] == 'false'
assert info.attrib['install-location'] == '/'
descriptor = json.loads((installed / 'Contents/Resources/Release/release.json').read_text())
assert descriptor['helper_code_signing_requirement'] == os.environ['PRIVATE_TEST_HELPER_REQUIREMENT']
postinstall = (expanded / 'Scripts/postinstall').read_text()
assert '@APP_HASH@' not in postinstall and '@HELPER_HASH@' not in postinstall
assert hashlib.sha256((expanded / 'Payload/Library/LaunchDaemons/com.omarchy.mx.installer.helper.plist').read_bytes()).hexdigest() in postinstall
assert os.environ['PRIVATE_TEST_APP_REQUIREMENT'] in postinstall
assert os.environ['PRIVATE_TEST_HELPER_REQUIREMENT'] in postinstall
print('PASS: exact app/helper payload, daemon pins, postinstall pins and nonrelocatable layout')
PY
# A separately signed program with the same identifier must not be trusted.
printf 'int main(void) { return 0; }\n' > "$work/impostor.c"
xcrun clang "$work/impostor.c" -o "$work/impostor"
for kind in app helper; do
  if [[ $kind == "app" ]]; then
    identifier=com.omarchy.mx.installer
    requirement=$app_requirement
  else
    identifier=com.omarchy.mx.installer.helper
    requirement=$helper_requirement
  fi
  /usr/bin/codesign --force --sign - --identifier "$identifier" "$work/impostor"
  if /usr/bin/codesign --verify --strict -R="$requirement" "$work/impostor"; then
    echo 'ERROR: same-identifier impostor accepted' >&2; exit 1
  fi
done
/usr/bin/ditto "$app" "$work/tampered.app"
chmod u+w "$work/tampered.app/Contents/Resources/Release/catalog.json"
printf '\n' >> "$work/tampered.app/Contents/Resources/Release/catalog.json"
if /usr/bin/codesign --verify --deep --strict "$work/tampered.app"; then exit 1; fi
# Native XPC enforcement, no launchd service and no installer helper execution.
xcrun clang -fobjc-arc -mmacosx-version-min=15.0 -framework Foundation "$root/test/private-xpc-pin-probe.m" -o "$work/xpc-probe"
/usr/bin/codesign --sign - --identifier com.omarchy.private-pin-probe "$work/xpc-probe"
correct=$(private_code_requirement "$work/xpc-probe" com.omarchy.private-pin-probe)
wrong='identifier "com.omarchy.private-pin-probe" and cdhash H"0000000000000000000000000000000000000000"'
"$work/xpc-probe" "$correct" "$correct" allow
"$work/xpc-probe" "$wrong" "$correct" deny-client
"$work/xpc-probe" "$correct" "$wrong" deny-server
echo 'PASS: exact pins, same-identifier impostors, tampered resources and native XPC peer rejection'
