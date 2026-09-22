#!/bin/bash
# Build a private app whose helper trust is tied to this exact binary.
set -euo pipefail
(( $# == 2 )) || { echo 'usage: build-adhoc-app.sh RELEASE_INPUTS NEW_OUTPUT_DIRECTORY' >&2; exit 64; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
source "$root/Packaging/private-test/code-identity.sh"
release=$(cd "$1" && pwd -P)
output=$2
[[ $output == /* && ! -e $output && ! -L $output ]] || exit 64
[[ ${OMARCHY_PRIVATE_PLAIN_TEST:-0} != "1" || ${OMARCHY_PRIVATE_LIMINE_TEST:-0} != "1" ]] || exit 64
mkdir "$output"
if [[ ${OMARCHY_PRIVATE_LIMINE_TEST:-0} == "1" ]]; then
  OMARCHY_PRIVATE_PLAIN_TEST=0 OMARCHY_APP_SIGNING_IDENTITY=- \
    bash "$root/Packaging/build-app.sh" "$release" "$output"
else
  OMARCHY_PRIVATE_PLAIN_TEST=1 OMARCHY_APP_SIGNING_IDENTITY=- \
    bash "$root/Packaging/build-app.sh" "$release" "$output"
fi
app="$output/Omarchy MX Mac Installer.app"
helper="$app/Contents/Resources/omarchy-apple-installer-helper"
[[ $(/usr/bin/lipo -archs "$helper") == "arm64" ]] || exit 1
[[ $(/usr/bin/lipo -archs "$app/Contents/MacOS/OmarchyAppleInstallerApp") == "arm64" ]] || exit 1
helper_requirement=$(private_code_requirement "$helper" com.omarchy.mx.installer.helper)
descriptor="$app/Contents/Resources/Release/release.json"
chmod u+w "$descriptor"
/usr/bin/plutil -replace helper_code_signing_requirement -string "$helper_requirement" "$descriptor"
chmod 444 "$descriptor"
/usr/bin/plutil -insert OmarchyPrivateExactBuild -bool true "$app/Contents/Info.plist"
# The external system daemon is generated only after the app is sealed. Never
# leave a usable identifier-only registration path inside the signed bundle.
/usr/bin/plutil -replace EnvironmentVariables.OMARCHY_CLIENT_CODE_SIGNING_REQUIREMENT \
  -string 'never' "$app/Contents/Library/LaunchDaemons/com.omarchy.mx.installer.helper.plist"
/usr/bin/codesign --force --sign - --timestamp=none --options runtime "$app"
/usr/bin/codesign --verify --deep --strict "$app"
/usr/bin/codesign --verify --strict -R="$helper_requirement" "$helper"
private_code_requirement "$app" com.omarchy.mx.installer
